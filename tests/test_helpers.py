#!/usr/bin/env python3
"""Unit tests for helper and utility functions."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from main import (  # type: ignore[reportMissingImports]
    app,
    calculate_elapsed_seconds,
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
    looks_like_valid_epub,
    rate_limit_bucket_for_request,
    should_enable_cleanup,
    slugify,
    split_text_into_chunks,
    validate_output_directory,
    voice_to_lang_code,
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


if __name__ == "__main__":
    unittest.main()
