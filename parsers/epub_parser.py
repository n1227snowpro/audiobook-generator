"""EPUB chapter detection using ebooklib + BeautifulSoup."""
from __future__ import annotations
import re
import warnings

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Block-level tags whose text should each become a separate line
_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li",
               "blockquote", "pre", "td", "th", "dt", "dd"}

# Matches leading list markers: "1." "2." "1)" "2)" at the start of a line
_LIST_PREFIX_RE = re.compile(r"^\d+[\.\)]\s+")


def _html_to_text(html_content) -> str:
    """Convert HTML to plain text.

    Extracts each block-level element's text as a single line (joining any
    inline spans / dropcap decorations without inserting stray newlines), then
    joins blocks with newlines.  This avoids the fragmentation caused by
    decorative <span class="dropcap"> / <span class="lead_word"> patterns
    that break the first list item into individual characters.
    """
    soup = BeautifulSoup(html_content, "lxml")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()

    lines = []
    for elem in soup.find_all(_BLOCK_TAGS):
        # Skip container elements that have nested block children
        if elem.find(_BLOCK_TAGS):
            continue
        # get_text() with no separator naturally joins inline spans
        text = re.sub(r"\s+", " ", elem.get_text()).strip()
        if text:
            lines.append(text)

    if lines:
        return "\n".join(lines)
    # Fallback for unusual documents
    return re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n")).strip()


def _extract_title(html_content, fallback: str) -> str:
    soup = BeautifulSoup(html_content, "lxml")
    for tag in ["h1", "h2", "h3"]:
        heading = soup.find(tag)
        if heading:
            text = heading.get_text().strip()
            if text:
                return text
    return fallback


def _strip_numbered_list(content: str) -> str:
    """If the content looks like a numbered list (≥50 % of non-empty lines
    start with 'N.' or 'N)'), strip those prefixes so TTS reads text
    continuously without announcing counts."""
    lines = content.split("\n")
    non_empty = [l for l in lines if l.strip()]
    if len(non_empty) < 3:
        return content
    numbered = sum(1 for l in non_empty if _LIST_PREFIX_RE.match(l.strip()))
    if numbered / len(non_empty) < 0.5:
        return content
    return "\n".join(
        _LIST_PREFIX_RE.sub("", line) if _LIST_PREFIX_RE.match(line.strip()) else line
        for line in lines
    )


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

        content = _strip_numbered_list(content)
        chapters.append({"title": title, "content": content})

    return chapters
