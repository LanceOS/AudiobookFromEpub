#!/usr/bin/env python3
"""Minimal smoke test for the localhost EPUB-to-audio app APIs."""

from io import BytesIO
import tempfile
import time
from pathlib import Path

from ebooklib import epub

from main import app


def build_epub_bytes() -> bytes:
    book = epub.EpubBook()
    book.set_identifier("smoke-id")
    book.set_title("Smoke Book")
    book.set_language("en")

    chapter = epub.EpubHtml(title="Chapter One", file_name="chapter1.xhtml", lang="en")
    chapter.content = "<h1>Chapter One</h1><p>Smoke test content for API route checks.</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
        epub.write_epub(tmp.name, book)
        return Path(tmp.name).read_bytes()


def main() -> None:
    payload = build_epub_bytes()
    client = app.test_client()

    with tempfile.TemporaryDirectory() as output_dir:
        upload_resp = client.post(
            "/api/upload",
            data={"epub": (BytesIO(payload), "smoke.epub")},
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
