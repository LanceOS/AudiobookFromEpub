#!/usr/bin/env python3
"""Unit tests for helper and utility functions."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np

import main as app_main

from main import (  # type: ignore[reportMissingImports]
    _normalize_filter_level,
    _should_skip_chapter,
    app,
    calculate_elapsed_seconds,
    choose_voice_reference,
    cleanup_interval_seconds,
    cleanup_max_age_seconds,
    create_run_folder,
    default_requested_device,
    detect_device,
    estimate_generation_seconds,
    get_allowed_output_root,
    is_test_mode,
    is_valid_job_id,
    iso_to_epoch,
    is_lfs_pointer,
    looks_like_valid_epub,
    maybe_cleanup_stale_data,
    rate_limit_bucket_for_request,
    synthesize_text_to_wav,
    should_enable_cleanup,
    slugify,
    split_text_into_chunks,
    validate_output_directory,
    voice_to_lang_code,
    extract_book_title,
)


class HelperFunctionTests(unittest.TestCase):
    def test_slugify_normalizes_text(self) -> None:
        self.assertEqual(slugify(" My Fancy Title! "), "my_fancy_title")

    def test_slugify_uses_fallback_for_empty_input(self) -> None:
        self.assertEqual(slugify("!!!", fallback="fallback_name"), "fallback_name")

    def test_is_valid_job_id_accepts_hex_uuid_style_ids(self) -> None:
        self.assertTrue(is_valid_job_id("a" * 32))
        self.assertFalse(is_valid_job_id("abc123"))

    def test_iso_to_epoch_and_elapsed_seconds(self) -> None:
        start = "2026-01-01T00:00:00Z"
        finish = "2026-01-01T00:00:45Z"

        self.assertIsNotNone(iso_to_epoch(start))
        self.assertEqual(calculate_elapsed_seconds(start, finish), 45.0)
        self.assertIsNone(iso_to_epoch("not-a-date"))

    def test_split_text_into_chunks_respects_max_chars(self) -> None:
        text = "Sentence one. Sentence two is a little longer! Sentence three?" * 8
        chunks = split_text_into_chunks(text, max_chars=80)

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 80 for chunk in chunks))

    def test_split_text_into_chunks_returns_empty_for_blank_text(self) -> None:
        self.assertEqual(split_text_into_chunks("   \n\t  "), [])

    def test_create_run_folder_avoids_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as output_root:
            output_dir = Path(output_root)
            first = create_run_folder(output_dir, "My Book")
            second = create_run_folder(output_dir, "My Book")

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertNotEqual(first, second)

    def test_get_allowed_output_root_uses_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.dict(os.environ, {"AUDIOBOOK_ALLOWED_OUTPUT_ROOT": root}, clear=False):
                allowed_root, error = get_allowed_output_root()

            self.assertIsNone(error)
            self.assertEqual(Path(root).resolve(), Path(str(allowed_root)).resolve())

    def test_validate_output_directory_accepts_child_inside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            candidate = str(Path(root) / "allowed" / "nested")
            with mock.patch.dict(os.environ, {"AUDIOBOOK_ALLOWED_OUTPUT_ROOT": root}, clear=False):
                output_dir, error = validate_output_directory(candidate)

            self.assertIsNone(error)
            self.assertEqual(Path(candidate).resolve(), Path(str(output_dir)).resolve())
            self.assertTrue(Path(candidate).exists())

    def test_validate_output_directory_rejects_path_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as allowed_root, tempfile.TemporaryDirectory() as outside_root:
            with mock.patch.dict(os.environ, {"AUDIOBOOK_ALLOWED_OUTPUT_ROOT": allowed_root}, clear=False):
                output_dir, error = validate_output_directory(outside_root)

            self.assertIsNone(output_dir)
            self.assertIn("inside allowed root", str(error))

    def test_validate_output_directory_requires_non_empty_value(self) -> None:
        output_dir, error = validate_output_directory("")
        self.assertIsNone(output_dir)
        self.assertIn("Output directory is required", str(error))

    def test_looks_like_valid_epub_checks_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            valid_epub = Path(temp_dir) / "valid.epub"
            invalid_epub = Path(temp_dir) / "invalid.epub"

            with zipfile.ZipFile(valid_epub, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", "<container/>")

            invalid_epub.write_bytes(b"not-a-valid-zip")

            self.assertTrue(looks_like_valid_epub(valid_epub))
            self.assertFalse(looks_like_valid_epub(invalid_epub))

    def test_extract_book_title_reads_metadata(self) -> None:
        from ebooklib import epub  # type: ignore[reportMissingImports]

        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "title-test.epub"

            book = epub.EpubBook()
            book.set_identifier("title-test")
            book.set_title("Metadata Title")
            book.set_language("en")
            chapter = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
            chapter.content = "<html><body><h1>Chapter 1</h1><p>Text.</p></body></html>"
            book.add_item(chapter)
            book.toc = (chapter,)
            book.spine = ["nav", chapter]
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            epub.write_epub(str(epub_path), book)

            self.assertEqual(extract_book_title(epub_path), "Metadata Title")

    def test_rate_limit_bucket_mapping(self) -> None:
        with app.test_request_context("/api/upload"):
            bucket, limit, window = rate_limit_bucket_for_request()
            self.assertEqual(bucket, "upload")
            self.assertGreater(limit, 0)
            self.assertGreater(window, 0)

        with app.test_request_context("/api/generate"):
            bucket, _, _ = rate_limit_bucket_for_request()
            self.assertEqual(bucket, "generate")

        with app.test_request_context("/api/jobs/abc/status"):
            bucket, _, _ = rate_limit_bucket_for_request()
            self.assertEqual(bucket, "jobs")

        with app.test_request_context("/not-an-api"):
            bucket, _, _ = rate_limit_bucket_for_request()
            self.assertIsNone(bucket)

    def test_normalize_filter_level_defaults_unknown_values(self) -> None:
        self.assertEqual(_normalize_filter_level("DEFAULT"), "default")
        self.assertEqual(_normalize_filter_level("unknown"), "default")

    def test_should_skip_chapter_respects_off_level(self) -> None:
        self.assertFalse(
            _should_skip_chapter(
                chapter_title="Table of Contents",
                text="contents contents contents",
                level="off",
            )
        )

    def test_choose_voice_reference_returns_voice_when_local_file_missing(self) -> None:
        self.assertEqual(choose_voice_reference("af_heart"), "af_heart")

    def test_is_lfs_pointer_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pointer_file = Path(temp_dir) / "pointer.txt"
            regular_file = Path(temp_dir) / "regular.txt"
            pointer_file.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n", encoding="utf-8")
            regular_file.write_text("regular file", encoding="utf-8")

            self.assertTrue(is_lfs_pointer(pointer_file))
            self.assertFalse(is_lfs_pointer(regular_file))

    def test_cleanup_removes_stale_entries_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            uploads_dir = Path(temp_root) / "uploads"
            jobs_dir = Path(temp_root) / "jobs"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            jobs_dir.mkdir(parents=True, exist_ok=True)

            stale_upload = uploads_dir / "stale"
            fresh_upload = uploads_dir / "fresh"
            stale_upload.mkdir()
            fresh_upload.mkdir()

            stale_meta = jobs_dir / "stale.json"
            fresh_meta = jobs_dir / "fresh.json"
            stale_meta.write_text("{}", encoding="utf-8")
            fresh_meta.write_text("{}", encoding="utf-8")

            stale_time = time.time() - (2 * 3600)
            now_time = time.time()

            os.utime(stale_upload, (stale_time, stale_time))
            os.utime(fresh_upload, (now_time, now_time))
            os.utime(stale_meta, (stale_time, stale_time))
            os.utime(fresh_meta, (now_time, now_time))

            with mock.patch.object(app_main, "UPLOADS_DIR", uploads_dir), mock.patch.object(app_main, "JOB_META_DIR", jobs_dir):
                app_main.CLEANUP_LAST_RUN = 0.0
                with mock.patch.dict(
                    os.environ,
                    {
                        "AUDIOBOOK_ENABLE_CLEANUP": "1",
                        "AUDIOBOOK_CLEANUP_AGE_HOURS": "1",
                        "AUDIOBOOK_CLEANUP_INTERVAL_SECONDS": "60",
                    },
                    clear=False,
                ):
                    maybe_cleanup_stale_data()

            self.assertFalse(stale_upload.exists())
            self.assertTrue(fresh_upload.exists())
            self.assertFalse(stale_meta.exists())
            self.assertTrue(fresh_meta.exists())

    def test_cleanup_configuration_parsing(self) -> None:
        with mock.patch.dict(os.environ, {"AUDIOBOOK_CLEANUP_AGE_HOURS": "2"}, clear=False):
            self.assertEqual(cleanup_max_age_seconds(), 7200)

        with mock.patch.dict(os.environ, {"AUDIOBOOK_CLEANUP_AGE_HOURS": "bad"}, clear=False):
            self.assertEqual(cleanup_max_age_seconds(), 168 * 3600)

        with mock.patch.dict(os.environ, {"AUDIOBOOK_CLEANUP_INTERVAL_SECONDS": "30"}, clear=False):
            self.assertEqual(cleanup_interval_seconds(), 60)

    def test_cleanup_enable_toggle(self) -> None:
        with mock.patch.dict(os.environ, {"AUDIOBOOK_ENABLE_CLEANUP": "0"}, clear=False):
            self.assertFalse(should_enable_cleanup())

        with mock.patch.dict(os.environ, {"AUDIOBOOK_ENABLE_CLEANUP": "yes"}, clear=False):
            self.assertTrue(should_enable_cleanup())

    def test_default_requested_device_uses_env_or_auto(self) -> None:
        with mock.patch.dict(os.environ, {"AUDIOBOOK_DEVICE": "cuda"}, clear=False):
            self.assertEqual(default_requested_device(), "cuda")

        with mock.patch.dict(os.environ, {"AUDIOBOOK_DEVICE": ""}, clear=False):
            self.assertEqual(default_requested_device(), "auto")

    def test_detect_device_invalid_preference_defaults_to_cpu(self) -> None:
        self.assertEqual(detect_device("invalid-device"), "cpu")

    def test_detect_device_auto_returns_known_value(self) -> None:
        self.assertIn(detect_device("auto"), {"cpu", "cuda"})

    def test_pipeline_bundle_caches_pipeline_by_voice_and_device(self) -> None:
        class FakePipeline:
            init_calls = 0

            def __init__(self, *args, **kwargs):
                FakePipeline.init_calls += 1

        with app_main.PIPELINE_LOCK:
            app_main.PIPELINE_CACHE.clear()

        with mock.patch.object(app_main, "HAS_KOKORO", True), mock.patch.object(app_main, "KPipeline", FakePipeline), mock.patch.object(app_main, "KModel", None):
            first = app_main.get_pipeline_bundle("af_heart", "cpu")
            second = app_main.get_pipeline_bundle("af_heart", "cpu")

        self.assertIs(first, second)
        self.assertEqual(FakePipeline.init_calls, 1)

    def test_synthesize_text_to_wav_writes_audio_using_pipeline_output(self) -> None:
        class FakePipeline:
            def __call__(self, chunk, voice=None):
                return iter([(None, None, np.array([0.1, -0.1], dtype=np.float32))])

        write_mock = mock.Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "out.wav"

            with mock.patch.object(app_main, "split_text_into_chunks", return_value=["chunk"]), mock.patch.object(
                app_main,
                "get_pipeline_bundle",
                return_value={"pipeline": FakePipeline(), "model": None},
            ), mock.patch.object(app_main, "choose_voice_reference", return_value="af_heart"), mock.patch.dict(
                sys.modules,
                {"soundfile": mock.Mock(write=write_mock)},
            ):
                synthesize_text_to_wav("text", voice="af_heart", output_path=output_path, device="cpu")

        self.assertTrue(write_mock.called)
        self.assertEqual(write_mock.call_args.args[0], str(output_path))
        self.assertEqual(write_mock.call_args.args[2], 24000)

    def test_voice_to_lang_code_parsing(self) -> None:
        self.assertEqual(voice_to_lang_code("af_heart"), "a")
        self.assertEqual(voice_to_lang_code("voicewithoutunderscore"), "a")

    def test_is_test_mode_env_parsing(self) -> None:
        with mock.patch.dict(os.environ, {"AUDIOBOOK_TEST_MODE": "1"}, clear=False):
            self.assertTrue(is_test_mode())

        with mock.patch.dict(os.environ, {"AUDIOBOOK_TEST_MODE": "no"}, clear=False):
            self.assertFalse(is_test_mode())

    def test_estimate_generation_seconds_varies_by_mode(self) -> None:
        job = {
            "chapters": [
                {"text": "a" * 400},
                {"text": "b" * 400},
            ]
        }
        single = estimate_generation_seconds(job, "single")
        chapter = estimate_generation_seconds(job, "chapter")

        self.assertGreaterEqual(single, 15)
        self.assertGreater(chapter, single)

    def test_parse_args_honors_cli_values(self) -> None:
        with mock.patch.object(sys, "argv", ["main.py", "--host", "0.0.0.0", "--port", "8123", "--debug"]):
            args = app_main.parse_args()

        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8123)
        self.assertTrue(args.debug)

    def test_main_refuses_debug_without_allow_override(self) -> None:
        args = argparse.Namespace(host="127.0.0.1", port=5000, debug=True)

        with mock.patch.object(app_main, "parse_args", return_value=args), mock.patch.object(
            app_main,
            "ensure_app_dirs",
        ), mock.patch.object(app_main.app, "run") as run_mock, mock.patch.dict(
            os.environ,
            {"AUDIOBOOK_ALLOW_DEBUG": "0"},
            clear=False,
        ):
            with self.assertRaises(SystemExit):
                app_main.main()

        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
