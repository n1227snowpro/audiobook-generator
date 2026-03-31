"""EPUB chapter detection using ebooklib + BeautifulSoup."""
from __future__ import annotations
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import re


def _html_to_text(html_content) -> str:
    soup = BeautifulSoup(html_content, "lxml")
    # Remove script/style tags
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n").strip()


def _extract_title(html_content, fallback: str) -> str:
    soup = BeautifulSoup(html_content, "lxml")
    for tag in ["h1", "h2", "h3"]:
        heading = soup.find(tag)
        if heading:
            text = heading.get_text().strip()
            if text:
                return text
    return fallback


def parse(filepath: str) -> list[dict]:
    book = epub.read_epub(filepath, options={"ignore_ncx": False})

    chapters = []
    spine_ids = {item_id for item_id, _ in book.spine}

    for item in book.get_items():
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if item.get_id() not in spine_ids:
            continue

        content = _html_to_text(item.get_content())
        if len(content.strip()) < 100:
            continue  # skip nav/cover pages

        title = _extract_title(item.get_content(), item.get_name())
        # Clean up title
        title = re.sub(r"\.(html?|xhtml?)$", "", title, flags=re.IGNORECASE)

        chapters.append({"title": title, "content": content})

    return chapters
