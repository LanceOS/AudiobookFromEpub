#!/usr/bin/env python3
"""Tests for job-control endpoint edge cases."""

from __future__ import annotations

import os
import unittest
from io import BytesIO

os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

from main import JOBS, JOBS_LOCK, RATE_LIMIT_LOCK, RATE_LIMIT_STATE, WORKERS, WORKERS_LOCK, app  # type: ignore[reportMissingImports]


class JobControlRoutesTests(unittest.TestCase):
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

        with self.client.session_transaction() as session:
            self.csrf_token = session.get("csrf_token")
        self.assertTrue(self.csrf_token)

    def _headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": str(self.csrf_token)}

    def _upload_job(self) -> str:
        response = self.client.post(
            "/api/upload",
            data={"epub": (BytesIO(b"placeholder epub"), "fixture.epub")},
            headers=self._headers(),
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)
        return str(payload["job_id"])

    def test_stop_rejects_invalid_job_id_format(self) -> None:
        response = self.client.post("/api/jobs/not-a-job-id/stop", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_id format is invalid", str(payload.get("error", "")))

    def test_stop_rejects_unknown_job(self) -> None:
        response = self.client.post(f"/api/jobs/{'a' * 32}/stop", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 404)
        self.assertIn("Job not found", str(payload.get("error", "")))

    def test_stop_rejects_completed_job(self) -> None:
        job_id = self._upload_job()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"

        response = self.client.post(f"/api/jobs/{job_id}/stop", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 409)
        self.assertIn("Only active jobs can be stopped", str(payload.get("error", "")))

    def test_stop_returns_ok_for_already_stopped_job(self) -> None:
        job_id = self._upload_job()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "stopped"
            JOBS[job_id]["stop_requested"] = True

        response = self.client.post(f"/api/jobs/{job_id}/stop", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual((payload.get("job") or {}).get("status"), "stopped")

    def test_clear_files_rejects_invalid_job_id_format(self) -> None:
        response = self.client.post("/api/jobs/not-a-job-id/clear-files", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_id format is invalid", str(payload.get("error", "")))

    def test_clear_files_rejects_unknown_job(self) -> None:
        response = self.client.post(f"/api/jobs/{'b' * 32}/clear-files", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 404)
        self.assertIn("Job not found", str(payload.get("error", "")))

    def test_clear_files_rejects_non_stopped_job(self) -> None:
        job_id = self._upload_job()

        response = self.client.post(f"/api/jobs/{job_id}/clear-files", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 409)
        self.assertIn("Only stopped jobs can clear generated files", str(payload.get("error", "")))

    def test_clear_files_returns_ok_when_already_cleared(self) -> None:
        job_id = self._upload_job()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "stopped"
            JOBS[job_id]["stop_requested"] = True
            JOBS[job_id]["run_folder"] = None
            JOBS[job_id]["generated_files"] = []

        response = self.client.post(f"/api/jobs/{job_id}/clear-files", headers=self._headers(), json={})
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("files_cleared"), [])

    def test_status_route_rejects_invalid_job_id_format(self) -> None:
        response = self.client.get("/api/jobs/not-a-job-id/status")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_id format is invalid", str(payload.get("error", "")))

    def test_files_route_rejects_invalid_job_id_format(self) -> None:
        response = self.client.get("/api/jobs/not-a-job-id/files")
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 400)
        self.assertIn("job_id format is invalid", str(payload.get("error", "")))


if __name__ == "__main__":
    unittest.main()
