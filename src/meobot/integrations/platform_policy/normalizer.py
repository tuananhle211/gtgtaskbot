"""Turning an official policy page into deterministic, citable sections.

Step 1F.1. Deterministic - no model is involved in stripping HTML. An LLM asked
to remove navigation would be a per-refresh cost, a source of non-reproducible
snapshots, and a way for page text to reach a model with nothing between them.
Regular expressions and a hash are enough, and they give the same answer twice.

Two extractors, because the two platforms serve their policy differently
-------------------------------------------------------------------------

* :func:`extract_visible_text` - ordinary server-rendered HTML. Strips scripts,
  styles, SVG and navigation landmarks, keeps headings and prose.
* :func:`extract_embedded_router_state` - TikTok. Its pages are a single-page
  app whose policy text is delivered inside a URL-encoded JSON blob in the
  served HTML; tag-stripping alone returns about thirty characters of chrome.
  This decodes the blob and pulls the prose strings out of it. Still fully
  deterministic, still no browser, still only bytes the official host served.

Both are tried, best-effort, and the longer result wins. A page that yields
almost nothing either way produces a short normalized document, and
:class:`~meobot.application.pr_policy_source_service.PrPolicySourceService`
refuses to build a snapshot from it rather than storing an empty policy - which
is the failure mode that would silently produce a pack containing no rules.

What never reaches a snapshot
-----------------------------

Scripts, styles, analytics payloads, cookie banners, navigation, and anything
the fetcher was sent in a header. The normalized document is headings and policy
prose, which is what a reviewer's citation has to be able to point at.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

#: Bumped when extraction changes in a way that would alter a snapshot's hash.
#: Stored on every snapshot, so an old capture says which parser produced it and
#: a hash comparison across parser versions is never mistaken for a policy edit.
PARSER_VERSION = "policy-parser-v1"

#: Blocks removed entirely, contents and all.
_DROPPED_ELEMENTS = ("script", "style", "noscript", "svg", "template", "iframe", "form")

#: Landmarks whose contents are site chrome rather than policy.
_DROPPED_LANDMARKS = ("nav", "header", "footer", "aside")

_HEADING = re.compile(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>")
_TAG = re.compile(r"(?s)<[^>]+>")
#: Space, tab and NBSP - official pages use all three, and a snapshot that
#: hashed differently because of a non-breaking space would look like a
#: policy edit.
_WHITESPACE = re.compile("[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")

#: A sentence boundary: terminal punctuation followed by whitespace. Used only
#: when a whole paragraph is over the chunk limit.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

#: A prose string inside TikTok's embedded router state. Long enough and spaced
#: enough to be a sentence rather than a CSS rule or an identifier.
_PROSE = re.compile(r'"((?:[^"\\]|\\.){80,})"')

#: A document shorter than this is not policy. Roughly a paragraph: enough to
#: catch "the page rendered client-side and we got the cookie banner" without
#: rejecting a genuinely short official page.
MIN_USEFUL_CHARS = 400


@dataclass(frozen=True, slots=True)
class PolicySection:
    """One heading and the prose beneath it.

    ``path`` is the heading trail - ``Community Guidelines > Integrity`` - which
    is what a citation shows a person so they can find the rule on the real page.
    """

    path: str
    heading: str
    text: str


def _strip_markup(fragment: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    text = _TAG.sub(" ", fragment)
    text = html.unescape(text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def _drop_noise(markup: str) -> str:
    """Remove scripts, styles and site chrome, contents included."""
    cleaned = markup
    for element in _DROPPED_ELEMENTS + _DROPPED_LANDMARKS:
        cleaned = re.sub(rf"(?is)<{element}\b[^>]*>.*?</{element}>", "\n", cleaned)
        # Self-closing or unclosed variants, which real pages contain.
        cleaned = re.sub(rf"(?is)<{element}\b[^>]*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
    return cleaned


def extract_visible_text(markup: str) -> str:
    """Headings and prose from server-rendered HTML.

    Headings are kept as ``## heading`` lines so the section splitter can find
    them after the tags are gone. Everything else becomes plain text.
    """
    cleaned = _drop_noise(markup)
    cleaned = _HEADING.sub(lambda m: f"\n\n## {_strip_markup(m.group(2))}\n", cleaned)
    for block in ("p", "li", "div", "section", "article", "tr", "br"):
        cleaned = re.sub(rf"(?is)</?{block}\b[^>]*>", "\n", cleaned)
    text = _strip_markup(cleaned)
    # ``_strip_markup`` collapsed the newlines it was given; rebuild paragraphs
    # from the markers that survived.
    text = text.replace(" ## ", "\n\n## ")
    return _BLANK_LINES.sub("\n\n", text).strip()


def extract_embedded_router_state(markup: str) -> str:
    """Prose from a single-page app's embedded, URL-encoded JSON state.

    TikTok serves its guidelines this way: the visible DOM is a shell and the
    policy lives in a percent-encoded blob. This decodes it and keeps the
    sentence-shaped strings, which is deterministic and needs no browser.

    Returns an empty string when there is no such blob, so the caller can fall
    back to :func:`extract_visible_text` without branching on the platform.
    """
    blobs = re.findall(r"%7B%22[A-Za-z0-9%._~()'!*:@,;+$-]{500,}", markup)
    if not blobs:
        return ""
    decoded = unquote(max(blobs, key=len))

    seen: set[str] = set()
    kept: list[str] = []
    for raw in _PROSE.findall(decoded):
        candidate = _strip_markup(raw.replace("\\u003C", "<").replace("\\/", "/"))
        # Sentence-shaped: spaces, and not a stylesheet or a blob of ids.
        if candidate.count(" ") < 8 or "{" in candidate or "()" in candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        kept.append(candidate)
    return "\n\n".join(kept).strip()


def normalize_policy_html(markup: str) -> str:
    """The best deterministic reading of one official page.

    Both extractors run and the longer result wins, so no caller has to know
    which platform renders which way - and a page that changes how it renders
    keeps working without a code change.
    """
    visible = extract_visible_text(markup)
    embedded = extract_embedded_router_state(markup)
    best = embedded if len(embedded) > len(visible) else visible
    return normalize_policy_text(best)


def normalize_policy_text(text: str) -> str:
    """Canonical form, so an unchanged page hashes to an unchanged value.

    Unicode NFC, ``\\r\\n`` folded, trailing spaces removed, runs of blank lines
    collapsed. Without this, a platform reformatting its HTML - or a CDN
    serving different line endings - would look like a policy change and create
    a snapshot a day.
    """
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE.sub(" ", line).strip() for line in normalized.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def chunk_text(text: str, *, max_chars: int) -> list[str]:
    """Split one section into complete pieces, never dropping any of it.

    Boundary preference, best first:

    1. **paragraph** - a blank line, which is where the normalizer already put
       the document's own structure;
    2. **sentence** - a full stop followed by a space, for a paragraph that is
       itself over the limit;
    3. **hard cut** - only when a single sentence exceeds ``max_chars``, which
       happens in list-heavy policy pages with no terminal punctuation.

    Every character of ``text`` appears in exactly one returned chunk. This is
    the property that matters: the previous implementation bounded a rule by
    *truncating* it, so 685,226 characters of TikTok's guidelines became one
    8,000-character rule and the remaining 98.8% of the policy simply was not in
    the pack. A bound on how large one rule may be is not a licence to discard
    the rest of the document.

    A tail shorter than a sentence is merged into the previous chunk rather than
    emitted alone, so chunking cannot manufacture fragments that later fail a
    minimum-length filter and get dropped.
    """
    if len(text) <= max_chars:
        return [text] if text else []

    pieces: list[str] = []
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        # Paragraph too big on its own: fall to sentences, then to a hard cut.
        buffer = ""
        for sentence in _SENTENCE.split(paragraph):
            if not sentence:
                continue
            if len(sentence) > max_chars:
                if buffer:
                    pieces.append(buffer)
                    buffer = ""
                for start in range(0, len(sentence), max_chars):
                    pieces.append(sentence[start : start + max_chars])
                continue
            if len(buffer) + len(sentence) + 1 > max_chars:
                pieces.append(buffer)
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}".strip()
        if buffer:
            pieces.append(buffer)

    # Pack paragraphs back up to the limit, so a page of short paragraphs does
    # not become one rule per line.
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + len(piece) + 2 <= max_chars:
            current = f"{current}\n\n{piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def split_sections(document: str, *, root: str) -> list[PolicySection]:
    """Split a normalized document into citable sections.

    Splits on the ``## heading`` markers :func:`extract_visible_text` leaves.
    A document with no headings - which is the ordinary case for the embedded
    router-state path - becomes one section under ``root``, which is honest:
    the text is real policy and the page did not tell us where it divides.
    """
    sections: list[PolicySection] = []
    chunks = re.split(r"(?m)^## +(.+)$", document)
    preamble = chunks[0].strip()
    if preamble:
        sections.append(PolicySection(path=root, heading=root, text=preamble))
    for index in range(1, len(chunks) - 1, 2):
        heading = chunks[index].strip()
        body = chunks[index + 1].strip()
        if not body:
            continue
        sections.append(PolicySection(path=f"{root} > {heading}", heading=heading, text=body))
    return sections


__all__ = [
    "MIN_USEFUL_CHARS",
    "PARSER_VERSION",
    "PolicySection",
    "chunk_text",
    "extract_embedded_router_state",
    "extract_visible_text",
    "normalize_policy_html",
    "normalize_policy_text",
    "split_sections",
]
