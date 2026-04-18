#!/usr/bin/env python3
"""Security middleware tests for CSRF, origin checks, headers, and rate limiting."""

from __future__ import annotations

import os
import unittest
from io import BytesIO

os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

from main import (  # type: ignore[reportMissingImports]
    JOBS,
    JOBS_LOCK,
    RATE_LIMIT_LOCK,
    RATE_LIMIT_STATE,
    RATE_LIMITS,
    WORKERS,
    WORKERS_LOCK,
    app,
)


class SecurityMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        with JOBS_LOCK:
            JOBS.clear()
        with WORKERS_LOCK:
            WORKERS.clear()
        with RATE_LIMIT_LOCK:
            RATE_LIMIT_STATE.clear()

        self.client = app.test_client()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.csrf_token = session.get("csrf_token")
        self.assertTrue(self.csrf_token)

    def _csrf_headers(self, **extra: str) -> dict[str, str]:
        headers = {"X-CSRF-Token": str(self.csrf_token)}
        headers.update(extra)
        return headers

    def test_api_post_without_csrf_token_is_rejected(self) -> None:
        response = self.client.post(
            "/api/upload",
            data={"epub": (BytesIO(b"placeholder epub"), "fixture.epub")},
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid CSRF token", str(payload.get("error", "")))

    def test_api_post_with_invalid_csrf_token_is_rejected(self) -> None:
        response = self.client.post(
            "/api/upload",
            data={"epub": (BytesIO(b"placeholder epub"), "fixture.epub")},
            headers={"X-CSRF-Token": "invalid-token"},
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid CSRF token", str(payload.get("error", "")))

    def test_cross_origin_post_request_is_rejected(self) -> None:
        response = self.client.post(
            "/api/upload",
            data={"epub": (BytesIO(b"placeholder epub"), "fixture.epub")},
            headers=self._csrf_headers(Origin="https://evil.example.com"),
            content_type="multipart/form-data",
        )
        payload = response.get_json(silent=True) or {}

        self.assertEqual(response.status_code, 403)
        self.assertIn("Cross-origin requests are not allowed", str(payload.get("error", "")))

    def test_security_headers_present_on_index_response(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers.get("Content-Security-Policy"))
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertTrue(response.headers.get("Permissions-Policy"))

    def test_hsts_header_present_when_forwarded_proto_is_https(self) -> None:
        response = self.client.get("/", headers={"X-Forwarded-Proto": "https"})

        self.assertEqual(response.status_code, 200)
        strict_transport = response.headers.get("Strict-Transport-Security", "")
        self.assertIn("max-age=31536000", strict_transport)

    def test_upload_rate_limit_enforced(self) -> None:
        original_upload_limits = RATE_LIMITS["upload"]

        with RATE_LIMIT_LOCK:
            RATE_LIMITS["upload"] = (1, 60)
            RATE_LIMIT_STATE.clear()

        try:
            first_response = self.client.post(
                "/api/upload",
                data={"epub": (BytesIO(b"placeholder epub"), "fixture.epub")},
                headers=self._csrf_headers(),
                content_type="multipart/form-data",
            )
            first_payload = first_response.get_json(silent=True) or {}
            self.assertEqual(first_response.status_code, 200, first_payload)

            second_response = self.client.post(
                "/api/upload",
                data={"epub": (BytesIO(b"placeholder epub"), "fixture.epub")},
                headers=self._csrf_headers(),
                content_type="multipart/form-data",
            )
            second_payload = second_response.get_json(silent=True) or {}
            self.assertEqual(second_response.status_code, 429, second_payload)
            self.assertIn("Rate limit exceeded", str(second_payload.get("error", "")))
        finally:
            with RATE_LIMIT_LOCK:
                RATE_LIMITS["upload"] = original_upload_limits
                RATE_LIMIT_STATE.clear()


if __name__ == "__main__":
    unittest.main()
