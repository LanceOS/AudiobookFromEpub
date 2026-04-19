from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List


def _is_test_mode() -> bool:
    val = os.getenv("AUDIOBOOK_TEST_MODE", "")
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def extract_book_title(epub_path: Path) -> str:
    try:
        from ebooklib import epub as _epub  # type: ignore[reportMissingImports]
    except Exception:
        if _is_test_mode():
            return epub_path.stem
        raise

    book = _epub.read_epub(str(epub_path))
    metadata = book.get_metadata("DC", "title")
    if metadata and metadata[0] and metadata[0][0]:
        return metadata[0][0].strip()
    return epub_path.stem


def _normalize_filter_level(filter_level: str) -> str:
    level = (filter_level or "default").lower()
    if level not in {"off", "conservative", "default", "aggressive"}:
        return "default"
    return level


def _looks_like_nav_toc(soup, level: str) -> bool:
    nav = soup.find("nav")
    if not nav:
        return False

    nav_text = nav.get_text(separator=" ", strip=True).lower()
    li_count = len(nav.find_all("li"))

    if level == "conservative":
        return "table of contents" in nav_text or "contents" in nav_text or li_count > 8

    return "table of contents" in nav_text or "contents" in nav_text or li_count > 4


def _should_skip_chapter(
    chapter_title: str,
    text: str,
    level: str,
    *,
    allow_nav_only_fallback: bool = False,
    book_title: str = "",
    nav_toc: bool = False,
) -> bool:
    if level == "off":
        return False

    nm_title = (chapter_title or "").lower()
    nm_text = text.lower()
    word_count = len(text.split())
    sentence_count = nm_text.count(".") + nm_text.count("!") + nm_text.count("?")
    normalized_book_title = (book_title or "").strip().lower()

    conservative = level == "conservative"
    aggressive = level == "aggressive"

    if allow_nav_only_fallback:
        if nav_toc:
            if not normalized_book_title or nm_title != normalized_book_title or word_count < 200:
                return True
        if normalized_book_title and nm_title == normalized_book_title and word_count < 200:
            return True

        head_chunk = nm_text[:400]
        if head_chunk.strip().startswith("contents") and word_count < 200:
            return True
        if head_chunk.strip().startswith("notes") or head_chunk.strip().startswith("endnotes"):
            return True
        return False

    if conservative:
        front_re = re.compile(r"\b(table of contents|contents|toc|dedication|colophon|errata|index|appendix)\b", re.IGNORECASE)
        if front_re.search(nm_title) and word_count < 200:
            return True
        if nav_toc:
            return True
        return False

    front_re = re.compile(
        r"\b(preface|foreword|introduction|acknowledg|table of contents|contents|toc|dedication|colophon|errata|bibliograph|appendix|references|about the author|publisher|bibliography|note|notes|endnotes|index)\b",
        re.IGNORECASE,
    )

    if front_re.search(nm_title):
        if aggressive:
            if word_count < 600 and sentence_count < 6:
                return True
        else:
            if word_count < 300 and sentence_count < 4:
                return True

    if nav_toc:
        return True

    head_chunk = nm_text[:400]
    limit = 700 if aggressive else 400
    if ("table of contents" in head_chunk or head_chunk.strip().startswith("contents")) and word_count < limit:
        return True

    if head_chunk.strip().startswith("notes") or head_chunk.strip().startswith("endnotes"):
        return True

    return False


def extract_chapters_from_epub(epub_path: Path, filter_level: str = "default") -> List[Dict[str, str]]:
    try:
        import warnings as _warnings
        import ebooklib as _ebooklib  # type: ignore[reportMissingImports]
        from bs4 import BeautifulSoup as _BeautifulSoup  # type: ignore[reportMissingImports]
        from bs4 import XMLParsedAsHTMLWarning as _XMLParsedAsHTMLWarning  # type: ignore[reportMissingImports]
        from ebooklib import epub as _epub  # type: ignore[reportMissingImports]
    except Exception:
        if _is_test_mode():
            return [{"index": "1", "title": "Chapter 1", "text": "Test content."}]
        raise

    book = _epub.read_epub(str(epub_path))
    book_metadata = book.get_metadata("DC", "title")
    book_title = book_metadata[0][0].strip() if book_metadata and book_metadata[0] and book_metadata[0][0] else epub_path.stem
    chapters: List[Dict[str, str]] = []
    raw_chapters: List[Dict[str, str]] = []
    level = _normalize_filter_level(filter_level)

    try:
        items = list(book.get_items_of_type(_ebooklib.ITEM_DOCUMENT))
    except Exception:
        items = list(book.get_items())

    for item in items:
        try:
            content = item.get_content()
        except Exception:
            content = getattr(item, "content", b"")

        if not content:
            continue

        soup = _BeautifulSoup(content, "xml")
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 20:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore", _XMLParsedAsHTMLWarning)
                soup = _BeautifulSoup(content, "lxml")
            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 20:
            continue

        heading = soup.find(["h1", "h2", "h3", "title"])
        if heading and heading.get_text(strip=True):
            chapter_title = heading.get_text(strip=True)
        else:
            chapter_title = f"Chapter {len(chapters) + 1}"

        nav_toc = _looks_like_nav_toc(soup, level)
        raw_chapters.append({
            "title": chapter_title,
            "text": text,
            "nav_toc": nav_toc,
        })

        if _should_skip_chapter(chapter_title, text, level, book_title=book_title, nav_toc=nav_toc):
            continue

        chapters.append({
            "index": str(len(chapters) + 1),
            "title": chapter_title,
            "text": text,
        })

    if not chapters:
        if raw_chapters and level != "off":
            fallback_level = "conservative"
            fallback_chapters = []
            for raw_chapter in raw_chapters:
                if _should_skip_chapter(
                    raw_chapter["title"],
                    raw_chapter["text"],
                    fallback_level,
                    book_title=book_title,
                    nav_toc=bool(raw_chapter.get("nav_toc")),
                ):
                    continue
                fallback_chapters.append(
                    {
                        "index": str(len(fallback_chapters) + 1),
                        "title": raw_chapter["title"],
                        "text": raw_chapter["text"],
                    }
                )

            if fallback_chapters:
                chapters = fallback_chapters
            else:
                chapters = [
                    {
                        "index": str(i + 1),
                        "title": ch["title"],
                        "text": ch["text"],
                    }
                    for i, ch in enumerate(raw_chapters)
                    if not ch.get("nav_toc")
                ]
        else:
            raise ValueError("No readable chapter text found in EPUB.")

    return chapters


def looks_like_valid_epub(epub_path: Path) -> bool:
    try:
        if epub_path.stat().st_size < 4:
            return False

        with epub_path.open("rb") as fh:
            if fh.read(4) != b"PK\x03\x04":
                return False

        import zipfile

        with zipfile.ZipFile(epub_path, "r") as archive:
            names = set(archive.namelist())
            if "mimetype" not in names or "META-INF/container.xml" not in names:
                return False
            mimetype = archive.read("mimetype").decode("utf-8", errors="ignore").strip()
            if mimetype != "application/epub+zip":
                return False

        return True
    except Exception:
        return False
