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
            data={"epub": (BytesIO(b"placeholder epub bytes"), filename)},
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
        self.assertIn(b"Download your selected model before generation", response.data)

    def test_index_renders_model_manager_controls(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"id=\"modelSelect\"", response.data)
        self.assertIn(b"id=\"modelTypeSelect\"", response.data)
        self.assertIn(b"id=\"downloadModelButton\"", response.data)
        self.assertIn(b"id=\"modelStatusMessage\"", response.data)

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
        self.assertGreaterEqual(len(models), 3)

        by_id = {str(item.get("id")): item for item in models}
        self.assertIn(LOCAL_DEFAULT_MODEL_ID, by_id)
        self.assertIn("hexgrad/Kokoro-82M", by_id)
        self.assertIn("openbmb/VoxCPM2", by_id)

        default_model = by_id[LOCAL_DEFAULT_MODEL_ID]
        self.assertEqual(default_model.get("status"), "ready")
        self.assertTrue(default_model.get("supports_generation"))

        vox_model = by_id["openbmb/VoxCPM2"]
        self.assertEqual(vox_model.get("model_type"), "vox")
        self.assertFalse(vox_model.get("supports_generation"))

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
        self.assertIn("af_heart", status.get("voices") or [])

    def test_model_voices_route_supports_vox_type(self) -> None:
        response = self.client.get("/api/models/voices?model_type=vox")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200, payload)
        status = payload.get("status") or {}
        self.assertEqual(status.get("model_type"), "vox")
        self.assertFalse(status.get("supports_generation"))
        self.assertEqual(status.get("voices"), ["vox_default"])

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

        with mock.patch.object(app_main, "is_hf_model_cached", return_value=True):
            response = self.client.post(
                "/api/generate",
                json={
                    "job_id": upload_payload["job_id"],
                    "output_dir": str(DEFAULT_OUTPUT_DIR),
                    "output_name": "vox-unsupported",
                    "mode": "single",
                    "voice": "vox_default",
                    "model_id": "openbmb/VoxCPM2",
                    "model_type": "vox",
                },
                headers=self._headers(),
            )

        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 400)
        self.assertIn("download/select only", str(payload.get("error", "")))

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
