"""Folding the Vietnamese people actually type onto something matchable.

Real messages from a phone look like ``viec hnay cua toi``, ``mai toi di muon
30p``, ``da lam 3 cmt``. They have no accents, they abbreviate, and they are
lower case. Intent matching has to cope with all of that without a model.

**Conservative by construction.** Two rules keep this from corrupting meaning:

* **protected spans are never touched** - URLs, ``@mentions`` and email
  addresses are lifted out before any substitution and put back afterwards, so
  ``fb.com/abc`` does not become ``facebook.com/abc`` and a person named
  ``Task`` keeps their name;
* **substitutions are whole-word only.** ``rep`` becomes "trả lời"; ``report``
  does not become "trả lờiort".

**Ambiguous abbreviations need context.** ``tt`` is TikTok in "kênh tt" and is
nothing in particular in "tt nhé". ``ad`` is the group admin in "ad chưa duyệt"
and is not in "ad hoc". Those two only expand when a supporting word is present
in the same message; otherwise they are left alone, because a wrong guess about
which platform somebody meant is worse than no guess.

**The original is never discarded.** :class:`NormalizedText` carries both, so
intent matching uses the folded form while anything stored or audited keeps what
the person actually wrote.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

#: Spans that must survive untouched. Replaced by a placeholder before any
#: substitution runs and restored afterwards.
_PROTECTED = re.compile(
    r"""(
        https?://\S+                                  # URLs
        | \b[\w.+-]+@[\w-]+\.[\w.]{2,}\b              # email addresses
        | \b[\w-]+\.(?:com|vn|net|org|io|me|co|app)\b\S*  # bare domains: fb.com/abc
        | (?<!\w)@[A-Za-z0-9_]{4,32}\b                # @mentions
    )""",
    re.VERBOSE | re.IGNORECASE,
)

_PLACEHOLDER = "\x00{}\x00"

#: Unambiguous whole-word expansions. Written accent-free on the left because
#: matching happens after diacritics are folded.
_ABBREVIATIONS: Final[dict[str, str]] = {
    "hnay": "hom nay",
    "homnay": "hom nay",
    "hqua": "hom qua",
    "cmt": "binh luan",
    "cmts": "binh luan",
    "comment": "binh luan",
    "comments": "binh luan",
    "cmnt": "binh luan",
    "fb": "facebook",
    "yt": "youtube",
    "gr": "group",
    "rep": "tra loi",
    "reject": "bi tu choi",
    "rejected": "bi tu choi",
    "pending": "dang cho",
    "submit": "nop",
    "proof": "bang chung",
    "task": "viec",
    "tasks": "viec",
    "done": "hoan thanh",
    "xong": "hoan thanh",
    "bn": "bao nhieu",
    "ko": "khong",
    "k": "khong",
    "dc": "duoc",
    "dk": "duoc",
    "vs": "voi",
    "nghi": "nghi",
    "sg": "sang",
    "ch": "chieu",
    "t2": "thu hai",
    "t3": "thu ba",
    "t4": "thu tu",
    "t5": "thu nam",
    "t6": "thu sau",
    "t7": "thu bay",
    "cn": "chu nhat",
}

#: Multi-word phrases, applied before single words.
_PHRASES: Final[dict[str, str]] = {
    "link die": "lien ket khong truy cap duoc",
    "link loi": "lien ket khong truy cap duoc",
    "die link": "lien ket khong truy cap duoc",
}

#: Abbreviations that only expand when the message also mentions something that
#: makes the meaning unambiguous.
_CONTEXTUAL: Final[dict[str, tuple[str, frozenset[str]]]] = {
    "tt": ("tiktok", frozenset({"kenh", "page", "video", "dang", "clip", "channel"})),
    "ad": ("admin group", frozenset({"duyet", "group", "nhom", "gr", "bai"})),
}

#: ``30p`` / ``30ph`` / ``30 phut`` all mean thirty minutes.
_MINUTES = re.compile(r"\b(\d{1,3})\s*(?:p|ph|phut)\b")
#: ``9h`` / ``9h30`` / ``9 gio`` all name a time of day.
_HOURS = re.compile(r"\b(\d{1,2})\s*h(?:\s*(\d{1,2}))?\b")


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """One message in both forms.

    ``matchable`` is lower case, accent-free and expanded - for pattern
    matching only. ``original`` is what the person wrote, and is what gets
    stored, quoted back, or audited.
    """

    original: str
    matchable: str

    def contains(self, *needles: str) -> bool:
        """True when every needle appears in the matchable form."""
        return all(needle in self.matchable for needle in needles)

    def contains_any(self, *needles: str) -> bool:
        """True when at least one needle appears in the matchable form."""
        return any(needle in self.matchable for needle in needles)


def strip_accents(text: str) -> str:
    """Drop Vietnamese diacritics, keeping the letters underneath.

    ``đ`` needs handling by hand: it is a distinct letter, not a ``d`` with a
    combining mark, so NFD leaves it alone.
    """
    lowered = text.casefold().replace("đ", "d")
    return "".join(
        char for char in unicodedata.normalize("NFD", lowered) if not unicodedata.combining(char)
    )


def _protect(text: str) -> tuple[str, list[str]]:
    """Lift URLs, mentions and emails out so nothing rewrites them."""
    kept: list[str] = []

    def swap(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return _PLACEHOLDER.format(len(kept) - 1)

    return _PROTECTED.sub(swap, text), kept


def _restore(text: str, kept: list[str]) -> str:
    for index, value in enumerate(kept):
        text = text.replace(_PLACEHOLDER.format(index), value)
    return text


def normalize(text: str, *, enabled: bool = True) -> NormalizedText:
    """Fold a Member message into something intent patterns can match.

    Args:
        text: Exactly what the person sent.
        enabled: When false, only case and accents are folded and no
            abbreviation is expanded. Wired to a setting so a deployment can
            turn expansion off if it ever misreads a house style.
    """
    original = text or ""
    protected, kept = _protect(original)
    folded = strip_accents(protected)

    if not enabled:
        return NormalizedText(original=original, matchable=_collapse(_restore(folded, kept)))

    folded = _MINUTES.sub(lambda m: f"{m.group(1)} phut", folded)
    folded = _HOURS.sub(_expand_hour, folded)

    for phrase, replacement in _PHRASES.items():
        folded = folded.replace(phrase, replacement)

    words = re.split(r"(\W+)", folded)
    present = {word for word in words if word.isalpha()}
    expanded: list[str] = []
    for word in words:
        if not word.isalnum():
            expanded.append(word)
            continue
        if word in _ABBREVIATIONS:
            expanded.append(_ABBREVIATIONS[word])
            continue
        contextual = _CONTEXTUAL.get(word)
        if contextual is not None and present & contextual[1]:
            expanded.append(contextual[0])
            continue
        expanded.append(word)

    return NormalizedText(original=original, matchable=_collapse(_restore("".join(expanded), kept)))


def _expand_hour(match: re.Match[str]) -> str:
    """``9h`` -> ``9 gio``; ``9h30`` -> ``9 gio 30``."""
    hour, minute = match.group(1), match.group(2)
    return f"{hour} gio {minute}" if minute else f"{hour} gio"


def _collapse(text: str) -> str:
    """One space between words, nothing at the ends."""
    return " ".join(text.split())
