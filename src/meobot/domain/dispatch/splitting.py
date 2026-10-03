"""Turning a long announcement into ordered parts Telegram will accept.

Telegram refuses a ``sendMessage`` body over 4096 characters. Until this
release that limit was met by capping announcement content at 3000 characters
and dropping the rest - a silent truncation, which is the worst of the three
available behaviours: the sender believed the whole thing went out, and the
group read half a sentence.

The rules here, in order:

1. **Nothing is dropped.** Every non-whitespace character of the original
   appears in exactly one part, in order. That is asserted by a test, not
   assumed.
2. **Paragraphs first.** A part boundary that lands between two paragraphs is
   invisible to the reader. One that lands mid-word is not.
3. **Then sentences**, for a paragraph too long to fit whole.
4. **Then characters**, at a space where one exists - for the case that is
   always a pasted wall of text.

**HTML validity is guaranteed by construction, not by repair.** What is split
here is the plain text the person typed; the escaping and the markup happen
afterwards, per part, in the template. So no part can open a tag another part
closes: no part contains a tag at all until it is rendered, and rendering a
whole part always balances.

**Budgeting is done on the escaped length.** A body of ampersands is five times
longer once escaped, and a splitter that measured the raw string would produce
parts Telegram rejects. Measuring what will actually be sent costs one escape
per segment and removes the whole class of problem.

The part marker - "(2/3)" - is added by the template, never here, so a stored
part holds exactly the words that were confirmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as html_escape

#: Telegram's own limit on one message body.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

#: How much of that one part's *content* may use. The remainder covers the
#: template's heading, its signature line and the "(2/3)" marker, all of which
#: are added after splitting.
DEFAULT_PART_LIMIT = 3500

#: Room reserved inside the limit for the marker itself, so adding "(10/12)" to
#: a part that was exactly at the limit cannot push it over.
MARKER_RESERVE = 16

#: End of a sentence: terminal punctuation followed by whitespace. Kept with
#: the sentence it ends, so a part never begins with a stray full stop.
_SENTENCE_END = re.compile(r"(?<=[.!?…。])\s+")

#: A blank line, which is what a person means by "new paragraph".
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True, slots=True)
class MessagePart:
    """One ordered piece of one announcement.

    Args:
        number: 1-based position. Parts are delivered in this order, and a
            later part is never attempted before an earlier one succeeds.
        total: How many parts there are, so the template can render "(2/3)".
        content: The words themselves, unescaped and unmarked.
    """

    number: int
    total: int
    content: str

    @property
    def is_only_part(self) -> bool:
        """True for an announcement that fits in one message.

        Worth its own name because a single-part announcement carries no
        "(1/1)" marker: numbering one message is noise.
        """
        return self.total == 1


def rendered_length(text: str) -> int:
    """How long ``text`` will be once escaped for Telegram's HTML mode.

    This, and not ``len``, is what the limit applies to.
    """
    return len(html_escape(text, quote=False))


def split_announcement(content: str, *, limit: int = DEFAULT_PART_LIMIT) -> tuple[MessagePart, ...]:
    """Split one announcement into ordered, sendable parts.

    Args:
        content: Exactly what the person wrote.
        limit: Escaped-length budget for one part's content.

    Returns:
        One part when the announcement fits, several otherwise. Never empty for
        non-empty input, and never lossy: concatenating the parts reproduces
        every non-whitespace character of ``content`` in order.
    """
    body = (content or "").strip()
    if not body:
        return ()

    if rendered_length(body) <= limit:
        return (MessagePart(number=1, total=1, content=body),)

    budget = max(limit - MARKER_RESERVE, 64)
    chunks = _pack(_paragraphs(body), budget=budget, joiner="\n\n")
    total = len(chunks)
    return tuple(
        MessagePart(number=index, total=total, content=chunk)
        for index, chunk in enumerate(chunks, start=1)
    )


def _paragraphs(text: str) -> list[str]:
    """The text as paragraphs, blank lines removed and edges trimmed."""
    return [block.strip() for block in _PARAGRAPH_BREAK.split(text) if block.strip()]


def _sentences(text: str) -> list[str]:
    """One paragraph as sentences, falling back to lines when it has none."""
    parts = [piece.strip() for piece in _SENTENCE_END.split(text) if piece.strip()]
    if len(parts) > 1:
        return parts
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines if len(lines) > 1 else [text]


def _pack(segments: list[str], *, budget: int, joiner: str) -> list[str]:
    """Greedily fill parts with whole ``segments``, splitting one when it must.

    Greedy rather than balanced on purpose: a reader sees the parts in order,
    so filling each one before starting the next is what makes a two-part
    announcement look like a long message rather than two short ones.
    """
    chunks: list[str] = []
    current = ""

    for segment in segments:
        if rendered_length(segment) > budget:
            # This one segment does not fit even alone. Flush what we have and
            # break the segment down a level: paragraph -> sentence -> chars.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_oversized(segment, budget=budget))
            continue

        candidate = f"{current}{joiner}{segment}" if current else segment
        if rendered_length(candidate) <= budget:
            current = candidate
        else:
            chunks.append(current)
            current = segment

    if current:
        chunks.append(current)
    return chunks


def _split_oversized(segment: str, *, budget: int) -> list[str]:
    """Break one over-long segment, at the best boundary available."""
    sentences = _sentences(segment)
    if len(sentences) > 1:
        return _pack(sentences, budget=budget, joiner=" ")
    return _hard_split(segment, budget=budget)


def _hard_split(text: str, *, budget: int) -> list[str]:
    """The last resort: cut at a space near the limit, or at the limit.

    Reached only by a single sentence longer than a whole Telegram message,
    which in practice means somebody pasted something without punctuation. The
    cut still prefers a word boundary, and still loses nothing.
    """
    pieces: list[str] = []
    remaining = text
    while remaining:
        if rendered_length(remaining) <= budget:
            pieces.append(remaining)
            break
        cut = _largest_prefix(remaining, budget=budget)
        space = remaining.rfind(" ", max(1, cut // 2), cut)
        if space > 0:
            cut = space
        pieces.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return [piece for piece in pieces if piece]


def _largest_prefix(text: str, *, budget: int) -> int:
    """How many characters of ``text`` fit in ``budget`` once escaped.

    A binary search rather than a ratio: the expansion factor depends on which
    characters are where, and guessing it wrong produces a part Telegram
    refuses - which is exactly the failure this module exists to remove.
    """
    low, high = 1, len(text)
    best = 1
    while low <= high:
        middle = (low + high) // 2
        if rendered_length(text[:middle]) <= budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def marker_for(number: int, total: int) -> str:
    """The "(2/3)" a multi-part announcement carries, or "" for a single one."""
    return "" if total <= 1 else f"({number}/{total})"


def part_marker(part: MessagePart) -> str:
    """:func:`marker_for`, for a part that has not been stored yet."""
    return marker_for(part.number, part.total)
