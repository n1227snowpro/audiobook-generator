"""PDF chapter detection using PyMuPDF."""
from __future__ import annotations
import re
import fitz  # PyMuPDF


CHAPTER_PATTERNS = [
    re.compile(r"^(chapter\s+\d+[\.:—\-]?\s*.*)$", re.IGNORECASE),
    re.compile(r"^(chapter\s+[a-z]+[\.:—\-]?\s*.*)$", re.IGNORECASE),
    re.compile(r"^(\d+[\.:]\s+[A-Z].{3,80})$"),
    re.compile(r"^(part\s+\d+[\.:—\-]?\s*.*)$", re.IGNORECASE),
    re.compile(r"^(prologue|epilogue|introduction|preface|foreword|conclusion|afterword)$", re.IGNORECASE),
]


def _is_chapter_heading(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 120:
        return False
    for pat in CHAPTER_PATTERNS:
        if pat.match(text):
            return True
    return False


def parse(filepath: str) -> list[dict]:
    """Return list of {title, content} dicts."""
    doc = fitz.open(filepath)

    # Try bookmarks/TOC first
    toc = doc.get_toc()
    if toc:
        return _parse_by_toc(doc, toc)

    # Scan pages for chapter headings
    chapters = _parse_by_headings(doc)
    if chapters:
        return chapters

    # Fallback: split by every N pages
    return _parse_by_pages(doc, chunk_size=20)


def _parse_by_toc(doc, toc: list) -> list[dict]:
    """Use the document TOC to split chapters."""
    # Filter to level-1 entries only
    level1 = [(title, page) for level, title, page in toc if level == 1]
    if not level1:
        level1 = [(title, page) for level, title, page in toc if level <= 2]

    chapters = []
    for i, (title, start_page) in enumerate(level1):
        end_page = level1[i + 1][1] if i + 1 < len(level1) else len(doc)
        text_parts = []
        for p in range(start_page - 1, min(end_page - 1, len(doc))):
            text_parts.append(doc[p].get_text())
        content = "\n".join(text_parts).strip()
        if content:
            chapters.append({"title": title.strip(), "content": content})

    return chapters


def _parse_by_headings(doc) -> list[dict]:
    """Scan all pages for heading-like lines."""
    chapters = []
    current_title = None
    current_pages = []

    for page in doc:
        text = page.get_text()
        lines = text.split("\n")
        first_meaningful = next((l.strip() for l in lines if l.strip()), "")

        if _is_chapter_heading(first_meaningful):
            if current_title is not None and current_pages:
                chapters.append({
                    "title": current_title,
                    "content": "\n".join(current_pages).strip(),
                })
            current_title = first_meaningful
            current_pages = [text]
        else:
            if current_title is not None:
                current_pages.append(text)
            elif not chapters:
                # Pre-chapter content — treat as intro
                if current_pages:
                    current_pages.append(text)
                else:
                    current_title = "Introduction"
                    current_pages = [text]

    if current_title and current_pages:
        chapters.append({
            "title": current_title,
            "content": "\n".join(current_pages).strip(),
        })

    return chapters if len(chapters) >= 2 else []


def _parse_by_pages(doc, chunk_size: int = 20) -> list[dict]:
    """Fallback: chunk pages into equal-sized groups."""
    chapters = []
    total = len(doc)
    for i in range(0, total, chunk_size):
        pages = [doc[p].get_text() for p in range(i, min(i + chunk_size, total))]
        content = "\n".join(pages).strip()
        if content:
            chapters.append({
                "title": f"Part {len(chapters) + 1} (pages {i + 1}–{min(i + chunk_size, total)})",
                "content": content,
            })
    return chapters
