"""Pictures an agent puts in its answer (2026-11-06).

A Hermes agent that draws a picture (``image_generate``) or saves one hands it
back as a ``MEDIA:/absolute/path.png`` tag in its text. Hermes turns those
tags into ``![image](data:image/png;base64,...)`` only on a non-streamed
answer; APM streams, so it gets the bare path - a file on the agents' VPS no
browser can open. APM therefore takes both forms out of the answer itself:
a path is fetched through the relay (``GET /agents/<slug>/media``), a data
URL is decoded, and each picture is kept as a file of the answer, shown
under its text like a question's pictures.

A whole web page the agent writes (a mockup, a redesign demo) in one
```` ```html ```` block is kept the same way, as an ``.html`` file of the
answer: the chat shows a short line instead of the code, and the file opens
as a preview in its own sandboxed tab or downloads.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

#: At most this many pictures are taken out of one answer (the rest stay as text).
MAX_ANSWER_IMAGES = 6
#: The biggest picture kept (the relay refuses bigger ones too).
MAX_ANSWER_IMAGE_BYTES = 10 * 1024 * 1024

_EXT = r"(?:png|jpe?g|webp|gif)"
#: ``MEDIA:/path/x.png``, maybe wrapped in quotes or backticks.
_MEDIA_TAG = re.compile(
    rf"[`'\"]?MEDIA:\s*(?P<path>/[^\s`'\"<>()\[\]]+?\.{_EXT})(?![A-Za-z0-9])[`'\"]?",
    re.IGNORECASE,
)
#: ``![alt](data:image/png;base64,...)``
_DATA_IMAGE = re.compile(
    r"!\[[^\]\n]{0,200}\]\((?P<url>data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\s]+)\)"
)
_BLANK_RUNS = re.compile(r"\n{3,}")
#: A fenced ```html block (closed).
_HTML_BLOCK = re.compile(r"```[ \t]*html[^\n]*\n(?P<html>.*?)\n?```", re.IGNORECASE | re.DOTALL)
#: Only a whole page becomes a file; a snippet stays as code in the text.
_WHOLE_PAGE = re.compile(r"<!doctype\s+html|<html[\s>]|<body[\s>]", re.IGNORECASE)
#: At most this many pages are taken out of one answer.
MAX_ANSWER_PAGES = 3
#: The biggest page kept.
MAX_ANSWER_PAGE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MediaRef:
    """One picture in an answer: the text it takes, and where its bytes are."""

    raw: str
    #: A file on the agents' VPS (fetched through the relay).
    path: str | None = None
    #: The picture itself (a ``data:`` URL in the text).
    data: bytes | None = None

    @property
    def name(self) -> str:
        if self.path:
            return self.path.rsplit("/", 1)[-1][:200] or "anh"
        return "anh-agent"


def has_media(text: str) -> bool:
    return bool(text) and (
        "MEDIA:" in text.upper() or "data:image/" in text or "```html" in text.lower()
    )


@dataclass(frozen=True, slots=True)
class HtmlPage:
    """A whole page in a ```html block: the block's text, and the page."""

    raw: str
    html: str


def find_html_pages(text: str) -> list[HtmlPage]:
    """The whole pages written in ```html blocks, in order, at most :data:`MAX_ANSWER_PAGES`."""
    if "```" not in text:
        return []
    pages: list[HtmlPage] = []
    for match in _HTML_BLOCK.finditer(text):
        html = match.group("html").strip()
        if not _WHOLE_PAGE.search(html) or len(html.encode()) > MAX_ANSWER_PAGE_BYTES:
            continue
        pages.append(HtmlPage(raw=match.group(0), html=html))
        if len(pages) >= MAX_ANSWER_PAGES:
            break
    return pages


def find_media(text: str) -> list[MediaRef]:
    """The pictures in ``text``, in order, each path once, at most :data:`MAX_ANSWER_IMAGES`."""
    if not has_media(text):
        return []
    found: list[tuple[int, MediaRef]] = []
    seen: set[str] = set()
    for match in _MEDIA_TAG.finditer(text):
        path = match.group("path")
        if path in seen:
            found.append((match.start(), MediaRef(raw=match.group(0))))  # a repeat: just drop it
            continue
        seen.add(path)
        found.append((match.start(), MediaRef(raw=match.group(0), path=path)))
    for match in _DATA_IMAGE.finditer(text):
        payload = match.group("url").split(",", 1)[1]
        try:
            data = base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
        except (binascii.Error, ValueError):
            continue
        found.append((match.start(), MediaRef(raw=match.group(0), data=data)))
    found.sort(key=lambda item: item[0])
    refs: list[MediaRef] = []
    pictures = 0
    for _, ref in found:
        if ref.path is None and ref.data is None:
            refs.append(ref)
            continue
        if pictures >= MAX_ANSWER_IMAGES:
            break
        pictures += 1
        refs.append(ref)
    return refs


def replace_media(text: str, replacements: dict[str, str]) -> str:
    """``text`` with each picture's raw text replaced (``""`` = taken out), blank runs folded."""
    for raw, new in replacements.items():
        text = text.replace(raw, new)
    return _BLANK_RUNS.sub("\n\n", text).strip()


__all__ = [
    "MAX_ANSWER_IMAGES",
    "MAX_ANSWER_IMAGE_BYTES",
    "MAX_ANSWER_PAGES",
    "MAX_ANSWER_PAGE_BYTES",
    "HtmlPage",
    "MediaRef",
    "find_html_pages",
    "find_media",
    "has_media",
    "replace_media",
]
