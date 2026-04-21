#!/usr/bin/env python3
"""Minimal smoke test for the localhost EPUB-to-audio app APIs."""

from io import BytesIO
import os
import tempfile
import time
from pathlib import Path

# Enable test mode so the app uses the bundled sample WAV instead of Kokoro
os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

# Ensure a dummy Kokoro voice exists before importing `main` so the app
# discovers at least one local voice for the built-in model during tests.
repo_root = Path(__file__).resolve().parent
kokoro_voices_dir = repo_root / "Kokoro-82M" / "voices"
try:
    kokoro_voices_dir.mkdir(parents=True, exist_ok=True)
    dummy_voice = kokoro_voices_dir / "af_heart.pt"
    if not dummy_voice.exists():
        dummy_voice.write_text("dummy-voice-file", encoding="utf-8")
except Exception:
    # If we can't create files (CI restrictions), continue and let the test
    # code attempt other fallbacks.
    pass

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

        # Query available models and voices so the smoke test works in environments
        # where local Kokoro voices may be absent. Pick the first model that
        # supports generation and exposes at least one voice.
        models_resp = client.get("/api/models")
        assert models_resp.status_code == 200, models_resp.json
        models_payload = models_resp.json or {}
        models = list(models_payload.get("models") or [])

        default_model_id = models_payload.get("default_model_id")
        chosen_model_id = None
        chosen_voice = None

        # Prefer the default model (typically the built-in Kokoro) so the smoke
        # test doesn't require external downloads. Try to get a voice for it.
        for m in models:
            if m.get("id") == default_model_id:
                v = list(m.get("voices") or [])
                if v and bool(m.get("supports_generation")):
                    chosen_model_id = default_model_id
                    chosen_voice = v[0]
                break

        # Query voices for the default model as a fallback
        if not chosen_voice and default_model_id:
            voices_resp = client.get("/api/models/voices", query_string={"model_id": default_model_id})
            if voices_resp.status_code == 200:
                status = voices_resp.json.get("status") or {}
                vlist = list(status.get("voices") or [])
                if vlist:
                    chosen_model_id = default_model_id
                    chosen_voice = vlist[0]

        # If default model didn't yield a usable voice, pick any model that
        # supports generation and is already downloaded locally.
        if not chosen_voice:
            for m in models:
                if bool(m.get("supports_generation")) and bool(m.get("downloaded")):
                    v = list(m.get("voices") or [])
                    if v:
                        chosen_model_id = m.get("id")
                        chosen_voice = v[0]
                        break

        assert chosen_voice, f"No available voices to test (models={models_payload})"

        gen_payload = {
            "job_id": job_id,
            "output_dir": output_dir,
            "output_name": "smoke_output",
            "mode": "single",
            "voice": chosen_voice,
        }
        # Only pass a non-default model_id if it's the default or already downloaded
        if chosen_model_id:
            gen_payload["model_id"] = chosen_model_id

        generate_resp = client.post("/api/generate", json=gen_payload, headers=headers)
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
