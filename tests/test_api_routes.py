#!/usr/bin/env python3
"""API route and generated-file endpoint tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock
import zipfile

os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

import main as app_main

from main import (  # type: ignore[reportMissingImports]
    DEFAULT_OUTPUT_DIR,
    JOBS,
    JOBS_LOCK,
    LOCAL_DEFAULT_MODEL_ID,
    MODEL_DOWNLOADS,
    MODEL_DOWNLOADS_LOCK,
    MODEL_DOWNLOAD_WORKERS,
    MODEL_DOWNLOAD_WORKERS_LOCK,
    RATE_LIMIT_LOCK,
    RATE_LIMIT_STATE,
    WORKERS,
    WORKERS_LOCK,
    app,
)


def _build_minimal_epub_bytes() -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                archive.writestr(
                        "META-INF/container.xml",
                        """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
    </rootfiles>
</container>
""",
                )
                archive.writestr(
                        "OEBPS/content.opf",
                        """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0" xml:lang="en">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="BookId">urn:uuid:12345678-1234-5678-1234-567812345678</dc:identifier>
        <dc:title>Fixture Book</dc:title>
        <dc:language>en</dc:language>
    </metadata>
    <manifest>
        <item id="chapter1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine>
        <itemref idref="chapter1"/>
    </spine>
</package>
""",
                )
                archive.writestr(
                        "OEBPS/chap1.xhtml",
                        """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
    <head>
        <title>Chapter 1</title>
    </head>
    <body>
        <h1>Chapter 1</h1>
        <p>This is a minimal valid EPUB fixture used by the API tests.</p>
    </body>
</html>
""",
                )
        return buffer.getvalue()


class ApiRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        with JOBS_LOCK:
            JOBS.clear()
        with WORKERS_LOCK:
            WORKERS.clear()
        with MODEL_DOWNLOADS_LOCK:
            MODEL_DOWNLOADS.clear()
        with MODEL_DOWNLOAD_WORKERS_LOCK:
            MODEL_DOWNLOAD_WORKERS.clear()
        with RATE_LIMIT_LOCK:
            RATE_LIMIT_STATE.clear()

        self.client = app.test_client()
        index_response = self.client.get("/")
        self.assertEqual(index_response.status_code, 200)

        self._temp_output_dirs: list[tempfile.TemporaryDirectory[str]] = []

        with self.client.session_transaction() as session:
            self.csrf_token = session.get("csrf_token")
        self.assertTrue(self.csrf_token)

    def tearDown(self) -> None:
        for temp_output_dir in self._temp_output_dirs:
            temp_output_dir.cleanup()

    def _new_output_dir(self) -> str:
        temp_output_dir = tempfile.TemporaryDirectory(dir=str(DEFAULT_OUTPUT_DIR))
        self._temp_output_dirs.append(temp_output_dir)
        return temp_output_dir.name

    def _headers(self, **extra: str) -> dict[str, str]:
        headers = {"X-CSRF-Token": str(self.csrf_token)}
        headers.update(extra)
        return headers

    def _upload_placeholder_epub(self, filename: str = "fixture.epub") -> dict[str, object]:
        response = self.client.post(
            "/api/upload",
            data={"epub": (BytesIO(_build_minimal_epub_bytes()), filename)},
            headers=self._headers(),
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)
        return payload

    def _start_generation(
        self,
        job_id: str,
        output_dir: str,
        mode: str = "single",
        hf_model_id: str = "",
    ) -> dict[str, object]:
        response = self.client.post(
            "/api/generate",
            json={
                "job_id": job_id,
                "output_dir": output_dir,
                "output_name": "route test",
                "mode": mode,
                "voice": "af_heart",
                "hf_model_id": hf_model_id,
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)
        return payload

    def _wait_for_terminal_status(self, job_id: str, timeout_seconds: float = 8.0) -> dict[str, object]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status_response = self.client.get(f"/api/jobs/{job_id}/status")
            payload = status_response.get_json(silent=True) or {}
            if payload.get("status") in {"completed", "failed", "stopped"}:
                return payload
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not reach a terminal status")

    def _wait_for_model_download_status(
        self,
        model_id: str,
        expected_status: str,
        timeout_seconds: float = 4.0,
    ) -> dict[str, object]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            response = self.client.get(f"/api/models/download-status?model_id={model_id}")
            payload = response.get_json(silent=True) or {}
            status = payload.get("status") or {}
            if response.status_code == 200 and status.get("status") == expected_status:
                return status
            time.sleep(0.05)
        raise AssertionError(f"model {model_id} did not reach '{expected_status}' status")

    def _generate_single_file_job(self) -> tuple[str, str]:
        upload_payload = self._upload_placeholder_epub()
        job_id = str(upload_payload["job_id"])

        output_dir = self._new_output_dir()
        self._start_generation(job_id, output_dir=output_dir, mode="single")
        final_status = self._wait_for_terminal_status(job_id)
        self.assertEqual(final_status.get("status"), "completed", final_status)

        files_response = self.client.get(f"/api/jobs/{job_id}/files")
        files_payload = files_response.get_json(silent=True) or {}
        self.assertEqual(files_response.status_code, 200, files_payload)

        files = files_payload.get("files", [])
        self.assertGreaterEqual(len(files), 1, files_payload)
        filename = str(files[0]["name"])

        return job_id, filename

    def test_index_route_returns_html(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"EPUB to Audiobook", response.data)

    def test_index_shows_hf_cache_and_download_hint(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b".app_data/hf_models", response.data)
        self.assertIn(b"before generation", response.data)

    def test_index_renders_model_manager_controls(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"id=\"modelSelect\"", response.data)
        self.assertIn(b"id=\"downloadModelButton\"", response.data)
        self.assertIn(b"id=\"modelStatusMessage\"", response.data)

    def test_upload_route_does_not_store_chapter_text_in_job_record(self) -> None:
        payload = self._upload_placeholder_epub()
        job_id = str(payload["job_id"])

        with JOBS_LOCK:
            job = dict(JOBS[job_id])

        self.assertEqual(job.get("chapters_count"), payload.get("chapters_count"))
        self.assertNotIn("chapters", job)

    def test_job_files_route_is_read_only(self) -> None:
        job_id, _ = self._generate_single_file_job()

        with JOBS_LOCK:
            JOBS[job_id]["generated_files"] = []

        response = self.client.get(f"/api/jobs/{job_id}/files")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200, payload)
        self.assertGreaterEqual(len(payload.get("files") or []), 1)

        with JOBS_LOCK:
            self.assertEqual(JOBS[job_id].get("generated_files"), [])

    def test_health_route_returns_ok_json(self) -> None:
        response = self.client.get("/health")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertIn("time", payload)

    def test_models_route_returns_default_and_predefined_catalog(self) -> None:
        response = self.client.get("/api/models")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload.get("default_model_id"), LOCAL_DEFAULT_MODEL_ID)

        models = payload.get("models") or []
        self.assertGreaterEqual(len(models), 2)

        by_id = {str(item.get("id")): item for item in models}
        self.assertIn(LOCAL_DEFAULT_MODEL_ID, by_id)
        self.assertIn("hexgrad/Kokoro-82M", by_id)

        default_model = by_id[LOCAL_DEFAULT_MODEL_ID]
        self.assertEqual(default_model.get("status"), "ready")
        self.assertTrue(default_model.get("supports_generation"))

    def test_model_download_rejects_invalid_model_id(self) -> None:
        response = self.client.post(
            "/api/models/download",
            json={"model_id": "../../etc/passwd", "model_type": "kokoro"},
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("Hugging Face model ID", str(payload.get("error", "")))

    def test_model_download_starts_and_reports_downloaded_status(self) -> None:
        def fake_download(model_id: str, progress_callback=None):
            if progress_callback:
                progress_callback(20, f"Downloading model '{model_id}'...")
                progress_callback(100, "Model download complete.")
            return Path("/tmp/mock-model"), None

        with mock.patch.object(app_main, "is_hf_model_cached", return_value=False), mock.patch.object(
            app_main,
            "download_hf_model_snapshot",
            side_effect=fake_download,
        ):
            response = self.client.post(
                "/api/models/download",
                json={"model_id": "hexgrad/Kokoro-82M", "model_type": "kokoro"},
                headers=self._headers(),
            )
            payload = response.get_json(silent=True) or {}

            self.assertEqual(response.status_code, 202, payload)
            self.assertTrue(payload.get("started"))

            status = self._wait_for_model_download_status("hexgrad/Kokoro-82M", "downloaded")
            self.assertTrue(status.get("downloaded"))
            self.assertEqual(status.get("progress"), 100)
            self.assertEqual(status.get("model_type"), "kokoro")

            second = self.client.post(
                "/api/models/download",
                json={"model_id": "hexgrad/Kokoro-82M", "model_type": "kokoro"},
                headers=self._headers(),
            )
            second_payload = second.get_json(silent=True) or {}
            self.assertEqual(second.status_code, 200, second_payload)
            self.assertFalse(second_payload.get("started"))

    def test_model_voices_route_returns_local_kokoro_voices(self) -> None:
        response = self.client.get(f"/api/models/voices?model_id={LOCAL_DEFAULT_MODEL_ID}")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200, payload)
        status = payload.get("status") or {}
        self.assertEqual(status.get("model_type"), "kokoro")
        self.assertTrue(status.get("supports_generation"))
        voices = status.get("voices") or []
        self.assertIsInstance(voices, list)
        default_voice = status.get("default_voice")
        if voices:
            self.assertIn(default_voice, voices)
        else:
            self.assertIsNone(default_voice)

    def test_model_voices_route_returns_no_voices_for_other_type(self) -> None:
        response = self.client.get("/api/models/voices?model_type=other")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200, payload)
        status = payload.get("status") or {}
        self.assertEqual(status.get("model_type"), "other")
        self.assertFalse(status.get("supports_generation"))
        self.assertEqual(status.get("voices"), [])
        self.assertIsNone(status.get("default_voice"))

    def test_model_voices_route_infers_voxcpm2_for_model_id_alias(self) -> None:
        response = self.client.get("/api/models/voices?model_id=openbmb/VoxCPM2")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200, payload)
        status = payload.get("status") or {}
        self.assertEqual(status.get("model_type"), "voxcpm2")
        self.assertEqual(status.get("model_type_label"), "VoxCPM2")
        self.assertFalse(status.get("supports_generation"))

    def test_model_voices_route_uses_model_specific_voice_metadata(self) -> None:
        custom_entry = {
            "id": "openbmb/VoxCPM2",
            "display_name": "VoxCPM2",
            "model_type": "voxcpm2",
            "model_type_label": "VoxCPM2",
            "voices": ["speaker_a", "speaker_b"],
            "supports_generation": False,
        }

        with mock.patch.object(app_main, "get_model_catalog_entry", return_value=custom_entry):
            response = self.client.get("/api/models/voices?model_id=openbmb/VoxCPM2&model_type=voxcpm2")

        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)
        status = payload.get("status") or {}
        self.assertEqual(status.get("model_type"), "voxcpm2")
        self.assertEqual(status.get("model_type_label"), "VoxCPM2")
        self.assertEqual(status.get("voices"), ["speaker_a", "speaker_b"])
        self.assertEqual(status.get("default_voice"), "speaker_a")
        self.assertFalse(status.get("supports_generation"))

    def test_upload_rejects_missing_epub_field(self) -> None:
        response = self.client.post(
            "/api/upload",
            data={},
            headers=self._headers(),
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing file field", str(payload.get("error", "")))

    def test_upload_rejects_non_epub_extension(self) -> None:
        response = self.client.post(
            "/api/upload",
            data={"epub": (BytesIO(b"bad"), "fixture.txt")},
            headers=self._headers(),
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("Only .epub files", str(payload.get("error", "")))

    def test_generate_rejects_invalid_job_id_format(self) -> None:
        response = self.client.post(
            "/api/generate",
            json={
                "job_id": "not-a-job-id",
                "output_dir": str(DEFAULT_OUTPUT_DIR),
                "output_name": "invalid",
                "mode": "single",
                "voice": "af_heart",
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_id format is invalid", str(payload.get("error", "")))

    def test_generate_rejects_invalid_mode(self) -> None:
        upload_payload = self._upload_placeholder_epub()

        response = self.client.post(
            "/api/generate",
            json={
                "job_id": upload_payload["job_id"],
                "output_dir": str(DEFAULT_OUTPUT_DIR),
                "output_name": "invalid-mode",
                "mode": "batch",
                "voice": "af_heart",
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("mode must be 'single' or 'chapter'", str(payload.get("error", "")))

    def test_generate_rejects_unsupported_voice(self) -> None:
        upload_payload = self._upload_placeholder_epub()

        response = self.client.post(
            "/api/generate",
            json={
                "job_id": upload_payload["job_id"],
                "output_dir": str(DEFAULT_OUTPUT_DIR),
                "output_name": "invalid-voice",
                "mode": "single",
                "voice": "invalid_voice",
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported voice", str(payload.get("error", "")))

    def test_generate_accepts_output_directory_outside_allowed_root(self) -> None:
        upload_payload = self._upload_placeholder_epub()
        job_id = upload_payload["job_id"]

        response = self.client.post(
            "/api/generate",
            json={
                "job_id": job_id,
                "output_dir": "/tmp",
                "output_name": "outside-root",
                "mode": "single",
                "voice": "af_heart",
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)

        final_status = self._wait_for_terminal_status(job_id)
        self.assertEqual(final_status.get("status"), "completed", final_status)

    def test_generate_rejects_invalid_hf_model_id(self) -> None:
        upload_payload = self._upload_placeholder_epub()

        response = self.client.post(
            "/api/generate",
            json={
                "job_id": upload_payload["job_id"],
                "output_dir": str(DEFAULT_OUTPUT_DIR),
                "output_name": "invalid-model",
                "mode": "single",
                "voice": "af_heart",
                "hf_model_id": "../../etc/passwd",
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("Hugging Face model ID", str(payload.get("error", "")))

    def test_generate_rejects_model_that_is_not_downloaded(self) -> None:
        upload_payload = self._upload_placeholder_epub()

        with mock.patch.object(app_main, "is_hf_model_cached", return_value=False):
            response = self.client.post(
                "/api/generate",
                json={
                    "job_id": upload_payload["job_id"],
                    "output_dir": str(DEFAULT_OUTPUT_DIR),
                    "output_name": "missing-download",
                    "mode": "single",
                    "voice": "af_heart",
                    "model_id": "hexgrad/Kokoro-82M",
                    "model_type": "kokoro",
                },
                headers=self._headers(),
            )

        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 400)
        self.assertIn("not downloaded", str(payload.get("error", "")))

    def test_generate_rejects_unsupported_vox_model_type(self) -> None:
        upload_payload = self._upload_placeholder_epub()
        catalog_entry = {
            "model_id": "openbmb/VoxCPM2",
            "model_type": "voxcpm2",
            "model_type_label": "VoxCPM2",
            "voices": ["speaker_a"],
            "default_voice": "speaker_a",
            "supports_generation": False,
            "downloaded": True,
            "status": "downloaded",
            "progress": 100,
            "download_required": True,
            "predefined": False,
        }

        with mock.patch.object(app_main, "get_model_catalog_entry", return_value=catalog_entry), mock.patch.object(
            app_main,
            "is_hf_model_cached",
            return_value=True,
        ):
            response = self.client.post(
                "/api/generate",
                json={
                    "job_id": upload_payload["job_id"],
                    "output_dir": str(DEFAULT_OUTPUT_DIR),
                    "output_name": "other-unsupported",
                    "mode": "single",
                    "voice": "speaker_a",
                    "model_id": "openbmb/VoxCPM2",
                    "model_type": "voxcpm2",
                },
                headers=self._headers(),
            )

        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 400)
        self.assertIn("download/select only", str(payload.get("error", "")))

    def test_generate_rejects_qwen_backend_when_unavailable(self) -> None:
        upload_payload = self._upload_placeholder_epub()

        with mock.patch.object(app_main.ROUTE_DEPS, "is_test_mode", return_value=False), mock.patch.object(
            app_main.ROUTE_DEPS,
            "model_download_status",
            return_value={"downloaded": True, "status": "downloaded"},
        ), mock.patch.object(app_main, "HAS_QWEN_TTS", False), mock.patch.object(
            app_main,
            "QWEN_TTS_IMPORT_ERROR",
            "qwen-tts missing",
        ):
            response = self.client.post(
                "/api/generate",
                json={
                    "job_id": upload_payload["job_id"],
                    "output_dir": str(DEFAULT_OUTPUT_DIR),
                    "output_name": "qwen-backend-unavailable",
                    "mode": "single",
                    "voice": "Vivian",
                    "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                    "model_type": "qwen3_customvoice",
                },
                headers=self._headers(),
            )

        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 400)
        self.assertIn("Qwen3 CustomVoice backend is unavailable", str(payload.get("error", "")))

    def test_generate_accepts_hf_model_id_and_stores_it(self) -> None:
        upload_payload = self._upload_placeholder_epub()
        output_dir = self._new_output_dir()
        model_id = "hexgrad/Kokoro-82M"
        job_id = str(upload_payload["job_id"])

        with mock.patch.object(app_main, "is_hf_model_cached", return_value=True):
            response = self.client.post(
                "/api/generate",
                json={
                    "job_id": job_id,
                    "output_dir": output_dir,
                    "output_name": "model-id",
                    "mode": "single",
                    "voice": "af_heart",
                    "hf_model_id": model_id,
                },
                headers=self._headers(),
            )
        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)

        final_status = self._wait_for_terminal_status(job_id)
        self.assertEqual(final_status.get("status"), "completed", final_status)

        with JOBS_LOCK:
            self.assertEqual(JOBS[job_id]["config"].get("hf_model_id"), model_id)
            self.assertEqual(JOBS[job_id]["config"].get("model_id"), model_id)
            self.assertEqual(JOBS[job_id]["config"].get("model_type"), "kokoro")

    def test_file_route_streams_audio_and_download_route_sets_attachment(self) -> None:
        job_id, filename = self._generate_single_file_job()

        stream_response = self.client.get(f"/api/jobs/{job_id}/file/{filename}")
        self.assertEqual(stream_response.status_code, 200)
        stream_disposition = stream_response.headers.get("Content-Disposition", "").lower()
        self.assertNotIn("attachment", stream_disposition)

        download_response = self.client.get(f"/api/jobs/{job_id}/download/{filename}")
        self.assertEqual(download_response.status_code, 200)
        download_disposition = download_response.headers.get("Content-Disposition", "").lower()
        self.assertIn("attachment", download_disposition)

    def test_file_route_rejects_path_traversal_filename(self) -> None:
        job_id, _ = self._generate_single_file_job()

        response = self.client.get(f"/api/jobs/{job_id}/file/../escape.wav")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid file name", str(payload.get("error", "")))

    def test_download_route_returns_404_for_unknown_file(self) -> None:
        job_id, _ = self._generate_single_file_job()

        response = self.client.get(f"/api/jobs/{job_id}/download/missing.wav")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 404)
        self.assertIn("File not found", str(payload.get("error", "")))


if __name__ == "__main__":
    unittest.main()
