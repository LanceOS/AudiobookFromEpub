#!/usr/bin/env python3
"""API route and generated-file endpoint tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from io import BytesIO

os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

from main import (  # type: ignore[reportMissingImports]
    DEFAULT_OUTPUT_DIR,
    JOBS,
    JOBS_LOCK,
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

    def test_health_route_returns_ok_json(self) -> None:
        response = self.client.get("/health")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertIn("time", payload)

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

    def test_generate_rejects_output_directory_outside_allowed_root(self) -> None:
        upload_payload = self._upload_placeholder_epub()

        response = self.client.post(
            "/api/generate",
            json={
                "job_id": upload_payload["job_id"],
                "output_dir": "/tmp",
                "output_name": "outside-root",
                "mode": "single",
                "voice": "af_heart",
            },
            headers=self._headers(),
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("inside allowed root", str(payload.get("error", "")))

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

    def test_generate_accepts_hf_model_id_and_stores_it(self) -> None:
        upload_payload = self._upload_placeholder_epub()
        output_dir = self._new_output_dir()
        model_id = "hexgrad/Kokoro-82M"
        job_id = str(upload_payload["job_id"])

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
