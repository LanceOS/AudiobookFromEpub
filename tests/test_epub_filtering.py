#!/usr/bin/env python3
"""Unit tests for EPUB front/back-matter filtering."""

from __future__ import annotations

import os
import tempfile
import unittest
import time
from io import BytesIO
from pathlib import Path

os.environ.setdefault("AUDIOBOOK_TEST_MODE", "1")

from ebooklib import epub  # type: ignore[reportMissingImports]

from main import DEFAULT_OUTPUT_DIR, app, extract_chapters_from_epub


def build_epub_file(path: Path, docs: list[tuple[str, str]], book_title: str = "Test Book") -> None:
    book = epub.EpubBook()
    book.set_identifier("test-id")
    book.set_title(book_title)
    book.set_language("en")

    items = []
    for index, (title, html) in enumerate(docs, start=1):
        chapter = epub.EpubHtml(title=title, file_name=f"chap{index}.xhtml", lang="en")
        chapter.content = html
        book.add_item(chapter)
        items.append(chapter)

    book.toc = tuple(items)
    book.spine = ["nav"] + items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def make_epub_bytes(docs: list[tuple[str, str]], book_title: str = "Test Book") -> bytes:
    with tempfile.TemporaryDirectory() as temp_dir:
        epub_path = Path(temp_dir) / "fixture.epub"
        build_epub_file(epub_path, docs, book_title=book_title)
        return epub_path.read_bytes()


def titles_from_chapters(chapters: list[dict[str, str]]) -> list[str]:
    return [chapter["title"] for chapter in chapters]


def wait_for_job(client, job_id: str, timeout_seconds: float = 10.0) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status_response = client.get(f"/api/jobs/{job_id}/status")
        payload = status_response.get_json(silent=True) or {}
        if payload.get("status") in {"completed", "failed"}:
            return payload
        time.sleep(0.1)

    raise AssertionError(f"job {job_id} did not finish within {timeout_seconds} seconds")


class EpubFilteringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.csrf_token = session.get("csrf_token")
        self.assertTrue(self.csrf_token)

    def _extract_titles(self, docs: list[tuple[str, str]], level: str) -> list[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "fixture.epub"
            build_epub_file(epub_path, docs)
            chapters = extract_chapters_from_epub(epub_path, filter_level=level)
        return titles_from_chapters(chapters)

    def _upload_chapters_count(self, docs: list[tuple[str, str]], **form_fields: str) -> int:
        epub_bytes = make_epub_bytes(docs)
        data: dict[str, object] = {
            "epub": (BytesIO(epub_bytes), "fixture.epub"),
        }
        data.update(form_fields)

        response = self.client.post(
            "/api/upload",
            data=data,
            headers={"X-CSRF-Token": self.csrf_token},
            content_type="multipart/form-data",
        )

        payload = response.get_json(silent=True) or {}
        self.assertEqual(response.status_code, 200, payload)
        self.assertNotIn("error", payload)
        return int(payload["chapters_count"])

    def _upload_and_generate(
        self,
        docs: list[tuple[str, str]],
        *,
        filter_level: str = "default",
        mode: str = "single",
        output_name: str = "custom title",
        voice: str = "af_heart",
    ) -> tuple[dict[str, object], list[str]]:
        epub_bytes = make_epub_bytes(docs)

        upload_response = self.client.post(
            "/api/upload",
            data={
                "epub": (BytesIO(epub_bytes), "fixture.epub"),
                "filter_level": filter_level,
            },
            headers={"X-CSRF-Token": self.csrf_token},
            content_type="multipart/form-data",
        )
        upload_payload = upload_response.get_json(silent=True) or {}
        self.assertEqual(upload_response.status_code, 200, upload_payload)

        with tempfile.TemporaryDirectory(dir=str(DEFAULT_OUTPUT_DIR)) as output_dir:
            generate_response = self.client.post(
                "/api/generate",
                json={
                    "job_id": upload_payload["job_id"],
                    "output_dir": output_dir,
                    "output_name": output_name,
                    "mode": mode,
                    "voice": voice,
                },
                headers={"X-CSRF-Token": self.csrf_token},
            )
            generate_payload = generate_response.get_json(silent=True) or {}
            self.assertEqual(generate_response.status_code, 200, generate_payload)

            final_status = wait_for_job(self.client, str(upload_payload["job_id"]))
            self.assertEqual(final_status.get("status"), "completed", final_status)
            self.assertTrue(final_status.get("run_folder"), final_status)

            files_response = self.client.get(f"/api/jobs/{upload_payload['job_id']}/files")
            files_payload = files_response.get_json(silent=True) or {}
            self.assertEqual(files_response.status_code, 200, files_payload)
            file_names = [entry["name"] for entry in files_payload.get("files", [])]

        return final_status, file_names

    def test_default_skips_non_narrative_sections(self) -> None:
        docs = [
            (
                "Table of Contents",
                "<html><body><nav><ol><li>Chapter 1</li><li>Chapter 2</li></ol></nav><h1>Table of Contents</h1><p>Contents</p></body></html>",
            ),
            ("Chapter 1", "<html><body><h1>Chapter 1</h1><p>" + ("Story sentence. " * 80) + "</p></body></html>"),
            ("Endnotes", "<html><body><h1>Endnotes</h1><p>notes and references</p></body></html>"),
        ]

        titles = self._extract_titles(docs, "default")

        self.assertEqual(titles, ["Chapter 1"])

    def test_conservative_retains_more_than_default(self) -> None:
        docs = [
            (
                "Table of Contents",
                "<html><body><nav><ol><li>Chapter 1</li><li>Chapter 2</li></ol></nav><h1>Table of Contents</h1><p>Contents</p></body></html>",
            ),
            ("Chapter 1", "<html><body><h1>Chapter 1</h1><p>" + ("Story sentence. " * 80) + "</p></body></html>"),
            ("Endnotes", "<html><body><h1>Endnotes</h1><p>notes and references</p></body></html>"),
        ]

        default_titles = self._extract_titles(docs, "default")
        conservative_titles = self._extract_titles(docs, "conservative")

        self.assertEqual(default_titles, ["Chapter 1"])
        self.assertEqual(conservative_titles, ["Chapter 1", "Endnotes"])
        self.assertGreater(len(conservative_titles), len(default_titles))

    def test_default_falls_back_without_restoring_toc_pages(self) -> None:
        docs = [
            (
                "Table of Contents",
                "<html><body><nav><ol><li>Chapter 1</li><li>Chapter 2</li></ol></nav><h1>Table of Contents</h1><p>Contents</p></body></html>",
            ),
            ("Preface", "<html><body><h1>Preface</h1><p>Short intro.</p></body></html>"),
        ]

        titles = self._extract_titles(docs, "default")

        self.assertEqual(titles, ["Preface"])
        self.assertNotIn("Table of Contents", titles)

    def test_upload_respects_filter_level_and_legacy_flag(self) -> None:
        docs = [
            (
                "Table of Contents",
                "<html><body><nav><ol><li>Chapter 1</li><li>Chapter 2</li></ol></nav><h1>Table of Contents</h1><p>Contents</p></body></html>",
            ),
            ("Chapter 1", "<html><body><h1>Chapter 1</h1><p>" + ("Story sentence. " * 80) + "</p></body></html>"),
            ("Endnotes", "<html><body><h1>Endnotes</h1><p>notes and references</p></body></html>"),
        ]

        off_count = self._upload_chapters_count(docs, filter_level="off")
        default_count = self._upload_chapters_count(docs, filter_level="default")
        legacy_skip_count = self._upload_chapters_count(docs, skip_front_matter="1")
        legacy_off_count = self._upload_chapters_count(docs, skip_front_matter="0")

        self.assertGreater(off_count, default_count)
        self.assertEqual(default_count, legacy_skip_count)
        self.assertEqual(off_count, legacy_off_count)

    def test_chapter_mode_generates_filtered_chapter_files(self) -> None:
        docs = [
            (
                "Table of Contents",
                "<html><body><nav><ol><li>Chapter 1</li><li>Chapter 2</li></ol></nav><h1>Table of Contents</h1><p>Contents</p></body></html>",
            ),
            ("Chapter 1", "<html><body><h1>Chapter 1</h1><p>" + ("Story sentence. " * 50) + "</p></body></html>"),
            ("Chapter 2", "<html><body><h1>Chapter 2</h1><p>" + ("Another story sentence. " * 50) + "</p></body></html>"),
            ("Endnotes", "<html><body><h1>Endnotes</h1><p>notes and references</p></body></html>"),
        ]

        final_status, file_names = self._upload_and_generate(
            docs,
            filter_level="default",
            mode="chapter",
            output_name="chapter mode test",
        )

        self.assertEqual(final_status["chapters_count"], 2)
        self.assertEqual(file_names, ["001_chapter_1.wav", "002_chapter_2.wav"])

    def test_single_mode_uses_output_name_for_filename(self) -> None:
        docs = [
            (
                "Table of Contents",
                "<html><body><nav><ol><li>Chapter 1</li><li>Chapter 2</li></ol></nav><h1>Table of Contents</h1><p>Contents</p></body></html>",
            ),
            ("Chapter 1", "<html><body><h1>Chapter 1</h1><p>" + ("Story sentence. " * 50) + "</p></body></html>"),
            ("Chapter 2", "<html><body><h1>Chapter 2</h1><p>" + ("Another story sentence. " * 50) + "</p></body></html>"),
        ]

        final_status, file_names = self._upload_and_generate(
            docs,
            filter_level="default",
            mode="single",
            output_name="My Fancy Audiobook",
        )

        self.assertEqual(final_status["chapters_count"], 2)
        self.assertEqual(file_names, ["my_fancy_audiobook.wav"])


if __name__ == "__main__":
    unittest.main()
