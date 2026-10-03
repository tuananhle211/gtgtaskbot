"""Reading a group name and a message body out of a Vietnamese sentence.

Two questions this module answers, both without a model:

* **"which group?"** - "đăng ký group này làm group Test" names *Test*, and
  "gửi Chào buổi sáng vào group Test" means the same destination. The group id
  itself never comes from here; it comes from ``message.chat.id`` on
  registration and from the registry on send. What comes from here is only the
  *name*, and a name is always checked against what is actually registered.
* **"what should be said?"** - "Gửi “Chào buổi sáng” vào group Test" carries a
  body and an address, and only the body may reach the group.

**Token-wise, not regex-over-the-folded-string.** Matching needs the accent-free
lower-case form; the answer has to come back in the words the person actually
typed, because "Test" is a name and "test" is not what they wrote. Folding each
token separately keeps both: the folded list is what is searched, the original
list is what is returned.

Nothing here decides anything on its own. A parsed name that matches no
registered destination produces a refusal naming that group - never a guess, and
never a fallback to conversation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from meobot.domain.member.normalization import strip_accents

#: Words that introduce a group in Vietnamese. ``gr`` is normalised to ``group``
#: upstream, so it does not need its own entry.
GROUP_WORDS: frozenset[str] = frozenset({"group", "nhom", "team"})

#: Prepositions that point at a destination: "gửi ... **vào** group Test".
DESTINATION_PREPOSITIONS: frozenset[str] = frozenset(
    {"vao", "cho", "toi", "len", "den", "qua", "sang"}
)

#: Determiners that are never part of a name. "đăng ký group **này**" names
#: nothing - which is the signal to fall back to the Telegram title.
DETERMINERS: frozenset[str] = frozenset({"nay", "do", "kia", "ay", "nao", "chung", "minh", "toi"})

#: Words that can only be instruction, never content, at the *start* of a send
#: request: "**Gửi nội dung này** vào group Test".
LEAD_WORDS: frozenset[str] = frozenset(
    {"gui", "nhan", "thong", "bao", "noi", "dung", "nay", "tin", "hay", "giup", "dum", "ho"}
)

#: A quoted body wins over every other reading: somebody who wrote quotation
#: marks has already said exactly where the message starts and ends.
_QUOTED = re.compile(r"[“\"'‘]\s*([^”\"'’]{2,3000}?)\s*[”\"'’]")

#: Characters that end a name. A trailing ':' is the usual one, because
#: "thông báo cho group Test: ..." puts the body straight after it.
_TRAILING = " \t.,;:!?…-–—\"'“”‘’()"  # noqa: RUF001

MAX_NAME_TOKENS = 4


def _fold(token: str) -> str:
    """One token, accent-free, lower case and free of punctuation."""
    folded = strip_accents(token).strip(_TRAILING)
    return "".join(
        char for char in folded if unicodedata.category(char)[0] in {"L", "N"} or char == " "
    )


@dataclass(frozen=True, slots=True)
class _Tokens:
    """One sentence as parallel original and folded token lists."""

    original: tuple[str, ...]
    folded: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.original)

    def ends_sentence(self, index: int) -> bool:
        """True when this token carries punctuation that closes a phrase."""
        return self.original[index].rstrip().endswith((".", ",", ";", ":", "!", "?"))


def _tokenize(text: str) -> _Tokens:
    words = (text or "").split()
    return _Tokens(original=tuple(words), folded=tuple(_fold(word) for word in words))


def _name_after(tokens: _Tokens, start: int) -> str:
    """Collect a name beginning at ``start``, or "" when there is none."""
    parts: list[str] = []
    for index in range(start, min(len(tokens), start + MAX_NAME_TOKENS)):
        folded = tokens.folded[index]
        if not folded or folded in DETERMINERS or folded in GROUP_WORDS:
            break
        parts.append(tokens.original[index].strip(_TRAILING))
        if tokens.ends_sentence(index):
            break
    return " ".join(part for part in parts if part)


def parse_group_name(text: str) -> str:
    """The name a sentence gives a group, in the words that were typed.

    "đăng ký group này làm group Test" is read right-to-left on purpose: the
    *last* "group" is the one being named, and the first is only "this one".
    Returns "" when the sentence names no group, which is not a failure - it is
    how "Đăng ký group này." asks for the Telegram title to be used.
    """
    tokens = _tokenize(text)
    for index in reversed(range(len(tokens))):
        if tokens.folded[index] in GROUP_WORDS:
            name = _name_after(tokens, index + 1)
            if name:
                return name
    return ""


def display_name_for(name: str, *, telegram_title: str | None) -> str:
    """The name MeoBot will call a destination by.

    "Test" becomes "Group Test" so the registry reads as a list of groups rather
    than a list of bare words. A name that already says what it is - "Team Nội
    dung", "nhóm Content" - is left alone.
    """
    chosen = (name or "").strip() or (telegram_title or "").strip()
    if not chosen:
        return ""
    first = _fold(chosen.split()[0])
    if first in GROUP_WORDS:
        # Capitalise the leading word so "group Test" and "Group Test" are one
        # display name rather than two.
        head, _, tail = chosen.partition(" ")
        return f"{head[:1].upper()}{head[1:]} {tail}".strip()
    return f"Group {chosen}"


def _destination_index(tokens: _Tokens) -> int | None:
    """Index of the token that begins a destination phrase, if any.

    Matches "vào group", "cho nhóm", "tới team" - a preposition immediately
    followed by a word for a group.
    """
    for index in range(len(tokens) - 1):
        if (
            tokens.folded[index] in DESTINATION_PREPOSITIONS
            and tokens.folded[index + 1] in GROUP_WORDS
        ):
            return index
    return None


def extract_destination_name(text: str) -> str:
    """The group a send request is aimed at, or "".

    Two shapes, in order: an explicit "vào group X", and the colon form
    "thông báo cho X: ..." where the name sits between the preposition and the
    body.
    """
    tokens = _tokenize(text)

    index = _destination_index(tokens)
    if index is not None:
        return _name_after(tokens, index + 2)

    # "thông báo cho Test: Chào buổi sáng" - no word for "group" at all, so the
    # colon is what marks the end of the name.
    for position in range(len(tokens)):
        if tokens.folded[position] not in DESTINATION_PREPOSITIONS:
            continue
        parts: list[str] = []
        for offset in range(position + 1, min(len(tokens), position + 1 + MAX_NAME_TOKENS)):
            folded = tokens.folded[offset]
            if not folded or folded in DETERMINERS:
                break
            parts.append(tokens.original[offset].strip(_TRAILING))
            if tokens.original[offset].rstrip().endswith(":"):
                return " ".join(parts)
        # Only a colon proves the name ended; without one this is not a name.
    return ""


def extract_send_content(text: str) -> str:
    """What the person wants said, with the addressing removed.

    Three readings, most explicit first:

    1. a quoted span - the person drew the boundary themselves;
    2. everything after the first colon - "thông báo cho group Test: ...";
    3. the words between the leading verb and the destination phrase.

    Returns "" when nothing is left, which the caller turns into a question
    rather than an empty announcement.
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    quoted = _QUOTED.search(raw)
    if quoted is not None:
        return quoted.group(1).strip()

    head, separator, tail = raw.partition(":")
    if separator and tail.strip() and _destination_or_address(head):
        return tail.strip()

    tokens = _tokenize(raw)
    index = _destination_index(tokens)
    if index is None:
        return ""

    start = 0
    while start < index and tokens.folded[start] in LEAD_WORDS:
        start += 1
    body = " ".join(tokens.original[start:index]).strip(_TRAILING)
    return body.strip()


def _destination_or_address(head: str) -> bool:
    """True when the text before a colon is addressing, not content.

    Guards the colon reading: "Nhắc tôi 15:30 gọi bác sĩ" must not be read as a
    body of "30 gọi bác sĩ".
    """
    tokens = _tokenize(head)
    if _destination_index(tokens) is not None:
        return True
    return any(token in DESTINATION_PREPOSITIONS for token in tokens.folded) and any(
        token in {"gui", "thong", "bao", "nhan"} for token in tokens.folded
    )
