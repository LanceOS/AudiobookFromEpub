#!/usr/bin/env python3
"""Create a minimal EPUB fixture for E2E tests.

Usage:
  python create_epub_fixture.py /tmp/test_book.epub
"""
import sys
from ebooklib import epub


def create_epub(path: str) -> None:
    book = epub.EpubBook()
    book.set_identifier("e2e-id")
    book.set_title("E2E Test Book")
    book.set_language("en")

    chapter = epub.EpubHtml(title="Chapter One", file_name="chapter1.xhtml", lang="en")
    chapter.content = "<h1>Chapter One</h1><p>End-to-end test content.</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(path, book)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: create_epub_fixture.py <output.epub>")
        sys.exit(2)
    create_epub(sys.argv[1])
