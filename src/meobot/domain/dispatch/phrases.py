"""What somebody means while a delivery draft is open, decided without a model.

This module is the fix for the two reported conversational dead ends, and the
reason both of them are fixable at all is that neither sentence was ambiguous:

* the owner was shown three groups, said **"Tất cả"**, and was asked what they
  wanted to *do* with those groups - a question the draft already answered;
* the owner was shown a preview, said **"Xác nhận"**, and was asked what action
  and which recipients they meant - both of which the draft was holding.

Neither answer needed generation. What was missing was somewhere for the words
to land: while a draft is open, "Tất cả" is a selection and "Xác nhận" is a
confirmation, and both are decided here by pattern.

**Order is the whole design.** Cancelling wins over everything, an exclusion is
read before the selection it modifies, and a bare confirmation is tried only
after every phrase that names a destination has failed - so "Gửi đi" confirms
while "Gửi cho group Test" does not.

Nothing here resolves a destination. Names come back as the folded words the
person typed and are checked against :class:`~meobot.db.models.notifications.
TelegramChat` by the caller. A name that matches nothing is a question, never a
guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from meobot.domain.member.normalization import strip_accents

#: Words for a group. ``gr`` is here as well as in the normalizer because this
#: module folds its own input rather than going through abbreviation expansion.
GROUP_WORDS: frozenset[str] = frozenset({"group", "nhom", "team", "gr", "grp"})

#: Vietnamese number words, up to the largest count of groups anybody sensibly
#: reads off a phone screen in one card.
NUMBER_WORDS: dict[str, int] = {
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "tu": 4,
    "nam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
    "muoi": 10,
}

#: Politeness and address that carry no instruction. Stripped **only** before
#: the confirm / cancel / select-all comparisons - never before reading a name,
#: because a group really can be called "Anh em Content".
FILLERS: frozenset[str] = frozenset(
    {
        "tasksbot",
        "meobot",
        "bot",
        "nhe",
        "nha",
        "ah",
        "oi",
        "di",
        "luon",
        "giup",
        "gium",
        "ho",
        "minh",
        "em",
        "chi",
        "anh",
        "ban",
        "cho",
        "nhi",
        "vay",
        "the",
        "a",
        "ay",
        "cai",
    }
)

#: Every word a bare confirmation may consist of. The test is subset membership
#: of the *whole* message, so "Gửi đi" confirms and "Gửi vào group Test" does
#: not - the second contains words that are not in here.
CONFIRM_WORDS: frozenset[str] = frozenset(
    {
        "xac",
        "nhan",
        "dung",
        "roi",
        "gui",
        "ok",
        "oke",
        "okie",
        "okay",
        "chot",
        "thuc",
        "hien",
        "dong",
        "y",
        "vang",
        "duoc",
        "chuan",
        "uh",
        "um",
        "u",
        "thong",
        "bao",
        "tin",
        "va",
    }
)

#: Phrases that abandon the draft. Checked first: somebody backing out must
#: never have their words read as a selection.
CANCEL_PHRASES: tuple[str, ...] = (
    "huy",
    "thoi khong",
    "khong gui nua",
    "khong gui thong bao",
    "bo thong bao",
    "bo tin nay",
    "dung gui",
    "khong can gui",
    "khong lam nua",
)

#: Cancels only when it is the entire message. "Thôi" inside a sentence is a
#: particle, not an instruction.
CANCEL_EXACT: frozenset[str] = frozenset({"thoi", "khong", "khoi"})

EDIT_PHRASES: tuple[str, ...] = ("sua noi dung", "sua lai noi dung", "doi noi dung", "viet lai")

RESELECT_PHRASES: tuple[str, ...] = (
    "chon lai",
    "doi noi nhan",
    "doi group",
    "chon group khac",
    "doi nguoi nhan",
)

#: "Tất cả", in the shapes people actually type.
ALL_PHRASES: tuple[str, ...] = (
    "tat ca",
    "toan bo",
    "moi group",
    "moi nhom",
    "tat ca cac group",
    "het cac group",
    "tat ca group",
    "gui het",
    "ca hai group",
)

#: Words that introduce an exclusion.
EXCLUDE_MARKERS: tuple[str, ...] = ("tru ", "ngoai tru ", "bo ", "khong gui ", "loai ", "bo qua ")

ONLY_MARKERS: tuple[str, ...] = ("chi ", "duy nhat ", "chi con ")

ADD_MARKERS: tuple[str, ...] = ("them ", "them ca ", "co ca ")

#: Words that may begin a name phrase and are never part of the name itself.
NAME_LEAD_WORDS: frozenset[str] = frozenset(
    {
        "gui",
        "nhan",
        "dang",
        "thong",
        "bao",
        "tin",
        "noi",
        "dung",
        "nay",
        "vao",
        "cho",
        "toi",
        "len",
        "den",
        "cac",
        "nhung",
        "chi",
        "them",
        "bo",
        "tru",
        "khong",
        "ngoai",
        "loai",
        "ca",
        "cua",
        "o",
        "tai",
        "voi",
        "va",
        *GROUP_WORDS,
    }
)

#: What splits one name from the next.
_SEPARATORS = re.compile(r"\s+va\s+|\s+voi\s+|\s+cung\s+|\s*,\s*|\s*;\s*|\s*&\s*|\s+lan\s+")

#: Punctuation that never belongs to a name or a keyword.
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


class ContinuationKind(StrEnum):
    """What an answer to an open draft is asking for."""

    CONFIRM = "CONFIRM"
    SELECT_ALL = "SELECT_ALL"
    SELECT_INDICES = "SELECT_INDICES"
    SELECT_NAMES = "SELECT_NAMES"
    ADD_NAMES = "ADD_NAMES"
    EXCLUDE_NAMES = "EXCLUDE_NAMES"
    ONLY_NAMES = "ONLY_NAMES"
    CANCEL = "CANCEL"
    EDIT_CONTENT = "EDIT_CONTENT"
    RESELECT = "RESELECT"
    #: Understood as nothing in particular. The caller re-shows the card rather
    #: than handing the words to the conversation model - a draft owns its turn.
    UNRECOGNISED = "UNRECOGNISED"


@dataclass(frozen=True, slots=True)
class Continuation:
    """One reading of a reply to an open draft.

    Args:
        kind: What was asked for.
        indices: 1-based positions into the candidate list the person was
            shown, for "hai group đầu" and "group 1 và 3".
        names: Folded name phrases, to be matched against the registry by the
            caller. Never a destination on their own.
        excluded: Folded name phrases to remove, for "tất cả trừ group Test".
    """

    kind: ContinuationKind
    indices: tuple[int, ...] = ()
    names: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()

    @property
    def is_recognised(self) -> bool:
        return self.kind is not ContinuationKind.UNRECOGNISED


class AudienceScope(StrEnum):
    """A whole-registry phrase, rather than a list of names.

    "Gửi vào toàn bộ group đã đăng ký" names no group at all; it describes a
    set. Expanding it is the registry's job, and it always shows the resulting
    list before anything is sent.
    """

    #: "tất cả group", "toàn bộ group đã đăng ký".
    ALL_REGISTERED = "ALL_REGISTERED"
    #: "các group đang hoạt động" - healthy destinations only.
    ACTIVE_ONLY = "ACTIVE_ONLY"
    #: "những group tôi quản lý".
    MANAGED_BY_ME = "MANAGED_BY_ME"
    #: "group vừa đăng ký", "group vừa rồi" - the newest registration, and only
    #: that one. Still previewed by name before anything is sent, because
    #: "the one I just added" is a memory and memories disagree.
    MOST_RECENT = "MOST_RECENT"


def fold(text: str) -> str:
    """Accent-free, lower case, punctuation-free, single-spaced."""
    return " ".join(_PUNCTUATION.sub(" ", strip_accents(text or "")).split())


def _content_words(folded: str) -> list[str]:
    """The message with politeness removed, for the whole-message comparisons."""
    return [word for word in folded.split() if word not in FILLERS]


def _number_at(word: str) -> int | None:
    """A count written as a word or as digits, or ``None``."""
    if word.isdigit():
        return int(word)
    return NUMBER_WORDS.get(word)


def read_names(text: str) -> tuple[str, ...]:
    """Every name phrase a sentence offers, folded and de-duplicated.

    "gửi cho group Test và group Saykeng" yields ``("test", "saykeng")``. The
    leading verb, the preposition and the word "group" are all removed, because
    none of them is part of what anybody calls the group.
    """
    folded = fold(text)
    if not folded:
        return ()

    found: list[str] = []
    for chunk in _SEPARATORS.split(folded):
        words = chunk.split()
        start = 0
        while start < len(words):
            word = words[start]
            if word in GROUP_WORDS:
                # The word for "group" ends the addressing: whatever follows is
                # the name, whether or not it happens to also be a lead word.
                # Without this, "các group báo cáo" loses its "báo" and MeoBot
                # goes looking for a group called "cáo".
                start += 1
                break
            if word not in NAME_LEAD_WORDS and _number_at(word) is None:
                break
            start += 1
        name = " ".join(words[start:]).strip()
        if name and name not in found:
            found.append(name)
    return tuple(found)


def _names_after(folded: str, markers: tuple[str, ...]) -> tuple[str, ...]:
    """Names introduced by one of ``markers``, or ``()`` when none appears."""
    padded = f"{folded} "
    for marker in markers:
        index = padded.find(marker)
        if index < 0:
            continue
        # A marker at the start, or preceded by a word boundary. "bỏ" inside
        # "bỏ qua" is handled by ordering the markers longest-first at the call
        # site; "bo" inside "bao" is what this boundary check prevents.
        if index > 0 and padded[index - 1] not in " ":
            continue
        return read_names(padded[index + len(marker) :])
    return ()


def _read_indices(folded: str, *, candidate_count: int) -> tuple[int, ...]:
    """Positions counted off a list that was just shown, 1-based.

    Four shapes, all of which mean an offset into the card the person is
    looking at and none of which mean a database id:

    * "hai group đầu" - the first two;
    * "ba group trên" / "ba group vừa hiện" - the three that were listed;
    * "group cuối" - the last one;
    * "group 1 và 3" / "số 1 và 3" - explicit positions.
    """
    words = folded.split()

    # "group cuối", "group cuối cùng" - the most recently listed one.
    if "cuoi" in words and candidate_count:
        return (candidate_count,)

    # "<số> group đầu" and "<số> group trên/vừa hiện/vừa rồi".
    for index, word in enumerate(words):
        count = _number_at(word)
        if count is None or index + 1 >= len(words):
            continue
        if words[index + 1] not in GROUP_WORDS:
            continue
        tail = " ".join(words[index + 2 :])
        if tail.startswith(("dau", "tren", "vua hien", "vua roi", "do", "nay", "vua nhac")):
            bounded = min(count, candidate_count) if candidate_count else count
            return tuple(range(1, bounded + 1))

    # Explicit positions: "group 1 và 3", "số 1 với 3", "1 và 3".
    if any(word in {"group", "nhom", "so", "thu", "vi", "tri"} for word in words) or (
        " va " in folded or " voi " in folded
    ):
        digits = [
            int(word)
            for word in words
            if word.isdigit() and 1 <= int(word) <= max(candidate_count, 1)
        ]
        if digits:
            return tuple(dict.fromkeys(digits))
    return ()


def read_continuation(text: str, *, candidate_count: int = 0) -> Continuation:
    """Read a reply to an open draft.

    Args:
        text: Exactly what was typed, accents and all.
        candidate_count: How many groups the person was last shown. Used to
            turn "ba group trên" into positions and to decide whether "cả ba"
            means everything.

    Returns:
        A :class:`Continuation`. ``UNRECOGNISED`` is a real answer, not a
        failure: it tells the caller to re-show the card rather than let the
        words reach the conversation model.
    """
    folded = fold(text)
    if not folded:
        return Continuation(kind=ContinuationKind.UNRECOGNISED)

    core = _content_words(folded)
    core_text = " ".join(core)

    # 1. Backing out wins over everything.
    if core_text in CANCEL_EXACT or any(phrase in core_text for phrase in CANCEL_PHRASES):
        return Continuation(kind=ContinuationKind.CANCEL)

    # 2. Changing the message or the recipients, rather than answering about them.
    if any(phrase in folded for phrase in EDIT_PHRASES):
        return Continuation(kind=ContinuationKind.EDIT_CONTENT)
    if any(phrase in folded for phrase in RESELECT_PHRASES):
        return Continuation(kind=ContinuationKind.RESELECT)

    # 3. An exclusion is read before the selection it modifies, so "tất cả trừ
    #    group Test" keeps both halves instead of losing one to the other.
    excluded = _names_after(folded, ("ngoai tru ", "tru ", "bo qua ", "khong gui ", "loai ", "bo "))
    wants_all = any(phrase in folded for phrase in ALL_PHRASES) or _means_every_candidate(
        core, candidate_count=candidate_count
    )
    if excluded and wants_all:
        return Continuation(kind=ContinuationKind.SELECT_ALL, excluded=excluded)
    if excluded:
        return Continuation(kind=ContinuationKind.EXCLUDE_NAMES, excluded=excluded)

    only = _names_after(folded, ONLY_MARKERS)
    if only:
        return Continuation(kind=ContinuationKind.ONLY_NAMES, names=only)

    added = _names_after(folded, ADD_MARKERS)
    if added:
        return Continuation(kind=ContinuationKind.ADD_NAMES, names=added)

    # 4. Everything.
    if wants_all:
        return Continuation(kind=ContinuationKind.SELECT_ALL)

    # 5. Counted off the card that is on screen.
    indices = _read_indices(folded, candidate_count=candidate_count)
    if indices:
        return Continuation(kind=ContinuationKind.SELECT_INDICES, indices=indices)

    # 6. A bare confirmation - tried only once every phrase that could name a
    #    destination has failed, so "Gửi đi" confirms and "Gửi group Test" does
    #    not.
    if core and all(word in CONFIRM_WORDS for word in core):
        return Continuation(kind=ContinuationKind.CONFIRM)

    # 7. Named destinations.
    names = read_names(folded)
    if names:
        return Continuation(kind=ContinuationKind.SELECT_NAMES, names=names)

    return Continuation(kind=ContinuationKind.UNRECOGNISED)


def _means_every_candidate(core: list[str], *, candidate_count: int) -> bool:
    """True for "cả ba" when three groups are on the card.

    "Cả ba" is only "all" because there happen to be three. With four
    candidates it names a count that does not match, and answering it as "all"
    would send to one group the person did not mean.

    Deliberately keyed on "cả" alone. Reading a bare count as "everything" -
    "gửi ba group vừa hiện" - would give the same answer here and the wrong one
    the moment the card has more rows than the count, so a bare count goes
    through :func:`_read_indices` where the positions are explicit.
    """
    for index, word in enumerate(core):
        if word != "ca" or index + 1 >= len(core):
            continue
        count = _number_at(core[index + 1])
        if count is not None and candidate_count and count == candidate_count:
            return True
    return False


def read_audience_scope(text: str) -> AudienceScope | None:
    """Whether a sentence describes the whole registry rather than naming groups.

    Returns ``None`` for anything that names destinations, which is the common
    case and the one that goes through the alias matcher.
    """
    folded = fold(text)
    if not folded:
        return None
    if any(
        phrase in folded
        for phrase in ("vua dang ky", "vua tao", "vua them", "vua roi", "moi dang ky")
    ):
        return AudienceScope.MOST_RECENT
    if any(
        phrase in folded
        for phrase in ("toi quan ly", "minh quan ly", "toi phu trach", "minh phu trach")
    ):
        return AudienceScope.MANAGED_BY_ME
    if any(
        phrase in folded
        for phrase in ("dang hoat dong", "con hoat dong", "hoat dong binh thuong", "dang dung duoc")
    ):
        return AudienceScope.ACTIVE_ONLY
    if any(
        phrase in folded
        for phrase in (
            "tat ca group",
            "tat ca cac group",
            "toan bo group",
            "toan bo cac group",
            "moi group",
            "moi nhom",
            "tat ca nhom",
            "toan bo nhom",
            "tat ca cac nhom",
            "group da dang ky",
            "cac group da dang ky",
            "tat ca cac group da dang ky",
        )
    ):
        return AudienceScope.ALL_REGISTERED
    return None


def read_exclusions(text: str) -> tuple[str, ...]:
    """Names a request asks to leave out: "tất cả group trừ group Test"."""
    return _names_after(fold(text), ("ngoai tru ", "tru ", "bo qua ", "khong gui ", "loai ", "bo "))
