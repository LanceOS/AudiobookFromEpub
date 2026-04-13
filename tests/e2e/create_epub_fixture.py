#!/usr/bin/env python3
"""Create a minimal EPUB fixture for E2E tests.

This script intentionally avoids requiring `ebooklib` so tests can run
in lightweight environments. The server runs in test mode and will
accept placeholder bytes as an upload.

Usage:
  python create_epub_fixture.py tests/e2e/fixture.epub
"""
import sys
from pathlib import Path


def create_placeholder(path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write a small placeholder file; server test-mode handles parsing.
    p.write_bytes(b"Test EPUB placeholder")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: create_epub_fixture.py <output.epub>")
        sys.exit(2)
    create_placeholder(sys.argv[1])
