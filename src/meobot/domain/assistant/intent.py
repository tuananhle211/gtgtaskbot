"""Which internal records this question actually needs.

Step 1F.2.3h. The alternative to selecting is sending everything, and sending
everything is what makes an assistant expensive, slow and *worse*: a model given
six collections it did not need answers the one it did need less well, and a
hundred-comment thread pushes the row-level permission flags out of the part of
the prompt it attends to.

So the selection is a keyword match, and deliberately nothing cleverer:

* it is **deterministic**, so a test can pin it and a person can predict it;
* it costs no model call, so choosing what to send is not itself a round-trip;
* it degrades safely - an unrecognised question loads *less* context, never
  more, and the assistant then says it does not have the data rather than
  answering from something it should not have had.

This is not RAG and does not want to be. The corpus is a handful of bounded
collections on one known row, not a document space, so there is nothing here for
an embedding to find that a word cannot.

Matching is accent-folded through
:func:`~meobot.domain.member.normalization.strip_accents`, because people type
"phai sinh" as often as "phái sinh" and both mean the same thing.

That helper rather than
:func:`~meobot.domain.conversations.patterns.fold`, which does the identical
job: ``patterns`` imports from :mod:`meobot.integrations.llm`, and importing it
from here would put a provider package behind a pure string match - and, as it
happens, close an import cycle. This module depends on nothing but the standard
library and one accent table.
"""

from __future__ import annotations

from enum import StrEnum

from meobot.domain.member.normalization import strip_accents as fold


class ContextSection(StrEnum):
    """A block of internal records that a turn may need.

    One member per collection the context service knows how to load. Named for
    the record rather than for the question, because a question about *"ai thêm
    cái này"* needs the derivative rows and the history rows, and a section
    named after the question would have to be two things.
    """

    DERIVATIVES = "derivatives"
    PUBLICATIONS = "publications"
    PRODUCTION_SUBMISSIONS = "production_submissions"
    COMMENTS = "comments"
    RESOURCES = "resources"
    DESTINATIONS = "destinations"
    HISTORY = "history"


#: Trigger words per section, accent-folded. Vietnamese first because that is
#: what people type; the English and the internal codes are there because
#: somebody reading the panel will quote them back.
#:
#: Kept narrow on purpose. A word that appears in half of all questions - "nội
#: dung", "bài" - would select its section on every turn and defeat the point.
_TRIGGERS: dict[ContextSection, tuple[str, ...]] = {
    ContextSection.DERIVATIVES: (
        "phai sinh",
        "derivative",
        "cutdown",
        "cut down",
        "ban cat",
        "cat ngan",
        "remix",
        "recut",
        "reformat",
        "doi dinh dang",
        "caption variant",
    ),
    ContextSection.PUBLICATIONS: (
        "xuat ban",
        "publication",
        "publish",
        "link dang",
        "bai dang",
        "da dang",
        "dang bai",
        "thu hoi",
        "reverse",
        "kenh",
        "channel",
    ),
    ContextSection.PRODUCTION_SUBMISSIONS: (
        "san pham goc",
        "ban nop",
        "nop file",
        "file san xuat",
        "production submission",
        "master",
        "duyet noi bo",
        "internal review",
    ),
    ContextSection.COMMENTS: (
        "binh luan",
        "comment",
        "trao doi",
        "tra loi",
        "thao luan",
        "gop y",
    ),
    ContextSection.RESOURCES: (
        "tai nguyen",
        "tai lieu",
        "resource",
        "brief",
        "tham khao",
    ),
    ContextSection.DESTINATIONS: (
        "landing",
        "dich den",
        "destination",
        "trang dat lich",
        "link san pham",
    ),
    ContextSection.HISTORY: (
        "lich su",
        "history",
        "ai doi",
        "ai sua",
        "ai duyet",
        "ai chuyen",
        "khi nao",
        "hoan tac",
        "undo",
        "audit",
        "duyet luc nao",
    ),
}

#: Words that make a turn a *permission* question. These do not select a
#: collection of their own - they widen whatever else was selected, because
#: "tôi có sửa được không" is only answerable with the rows the flags sit on.
_PERMISSION_TRIGGERS: tuple[str, ...] = (
    "co the sua",
    "co sua duoc",
    "sua duoc khong",
    "xoa duoc khong",
    "co xoa duoc",
    "hoan tac duoc",
    "toi co quyen",
    "co quyen khong",
    "duoc phep",
    "can edit",
    "can delete",
    "quyen cua toi",
)

#: Sections a permission question falls back to when it names no record type -
#: *"tôi sửa được gì ở đây?"*. The three that carry per-row flags, and nothing
#: with a long tail: comments are excluded because a permission question is
#: almost never about them and the thread is the biggest block available.
_PERMISSION_DEFAULT: tuple[ContextSection, ...] = (
    ContextSection.DERIVATIVES,
    ContextSection.PUBLICATIONS,
    ContextSection.PRODUCTION_SUBMISSIONS,
)

#: What a turn gets when it is clearly about the item but names no collection -
#: *"nội dung này đang thế nào?"*, *"tiếp theo làm gì?"*. Empty on purpose: the
#: item itself and its available actions travel on every object turn anyway, and
#: those are what "what next" is answered from.
_DEFAULT_WITH_OBJECT: tuple[ContextSection, ...] = ()

#: Sections that pull others in with them, because one is unreadable alone.
#:
#: A publication says *which file went out* as a reference to a production
#: submission or a derivative - deliberately, since copying the file's location
#: onto the publication would be two representations of one file. That is right
#: for the panel, which loads both lists anyway, and it means a publication row
#: on its own answers "đã đăng lên kênh nào, link bài đâu" but not "đăng bản
#: nào" - the reader has an id and nothing to resolve it against.
#:
#: So asking about publications brings the two output lists. Both are small - a
#: handful of rows per item - and without them the assistant would have to say
#: it cannot tell which cut was posted, which is the question people actually
#: ask about a publication.
_COMPANIONS: dict[ContextSection, tuple[ContextSection, ...]] = {
    ContextSection.PUBLICATIONS: (
        ContextSection.PRODUCTION_SUBMISSIONS,
        ContextSection.DERIVATIVES,
    ),
}


def asks_about_permission(message: str) -> bool:
    """True when the turn is asking what *this account* may do."""
    folded = fold(message)
    return any(trigger in folded for trigger in _PERMISSION_TRIGGERS)


def sections_for(message: str, *, has_object: bool = True) -> tuple[ContextSection, ...]:
    """Which record blocks this message needs, in a stable order.

    Ordered by :class:`ContextSection`'s own declaration order rather than by
    match order, so two questions that select the same sections produce the same
    prompt - which is what makes a snapshot test meaningful and a diff readable.

    Args:
        message: What the person typed.
        has_object: Whether a content item was resolved for this turn. Without
            one there is nothing for these collections to hang off, so the
            answer is always empty - a question about derivatives with no
            content in context is a question the assistant has to ask back
            about, not one to load six collections for.

    Returns:
        Possibly empty, which is the ordinary case for a turn that is not about
        a specific piece of work.
    """
    if not has_object:
        return ()
    folded = fold(message)
    selected = {
        section
        for section, triggers in _TRIGGERS.items()
        if any(trigger in folded for trigger in triggers)
    }
    if not selected and asks_about_permission(message):
        selected.update(_PERMISSION_DEFAULT)
    if not selected:
        selected.update(_DEFAULT_WITH_OBJECT)
    for section in tuple(selected):
        selected.update(_COMPANIONS.get(section, ()))
    return tuple(section for section in ContextSection if section in selected)


__all__: list[str] = ["ContextSection", "asks_about_permission", "sections_for"]
