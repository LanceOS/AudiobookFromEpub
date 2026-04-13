#!/usr/bin/env python3
"""Minimal smoke test for the localhost EPUB-to-audio app APIs."""

from io import BytesIO
import os
import tempfile
import time
from pathlib import Path

# Enable test mode so the app uses the bundled sample WAV instead of Kokoro
os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

from main import DEFAULT_OUTPUT_DIR, app


def build_epub_bytes() -> bytes:
    # In test mode we don't need a real EPUB; return lightweight placeholder bytes
    return b"Test EPUB placeholder"


def main() -> None:
    payload = build_epub_bytes()
    client = app.test_client()

    index_resp = client.get("/")
    assert index_resp.status_code == 200
    with client.session_transaction() as sess:
        csrf_token = sess.get("csrf_token")
    assert csrf_token, "csrf token missing from session"
    headers = {"X-CSRF-Token": csrf_token}

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(DEFAULT_OUTPUT_DIR)) as output_dir:
        upload_resp = client.post(
            "/api/upload",
            data={"epub": (BytesIO(payload), "smoke.epub")},
            headers=headers,
            content_type="multipart/form-data",
        )
        assert upload_resp.status_code == 200, upload_resp.json
        job_id = upload_resp.json["job_id"]

        generate_resp = client.post(
            "/api/generate",
            json={
                "job_id": job_id,
                "output_dir": output_dir,
                "output_name": "smoke_output",
                "mode": "single",
                "voice": "af_heart",
            },
            headers=headers,
        )
        assert generate_resp.status_code == 200, generate_resp.json

        final_status = None
        run_folder = None
        for _ in range(50):
            status_resp = client.get(f"/api/jobs/{job_id}/status")
            assert status_resp.status_code == 200
            data = status_resp.json
            final_status = data["status"]
            run_folder = data.get("run_folder")
            if final_status in {"completed", "failed"}:
                break
            time.sleep(0.2)

        assert run_folder, "run folder should be created for each generation"
        assert Path(run_folder).exists(), f"run folder missing: {run_folder}"

        files_resp = client.get(f"/api/jobs/{job_id}/files")
        assert files_resp.status_code == 200

        print("upload_status=200")
        print("generate_status=200")
        print(f"final_status={final_status}")
        print(f"run_folder_exists={Path(run_folder).exists()}")
        print(f"files_count={len(files_resp.json.get('files', []))}")


if __name__ == "__main__":
    main()
