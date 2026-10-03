"""The conversation around a piece of content.

Step 1F.2.3g. Every other table in this module records a **decision**: an
approval names who signed, a transition names who moved it, a submission names
which file was judged. None of them holds the sentence somebody typed on the way
to that decision, and until this step there was nowhere to put it - so *"hook
đoạn đầu hơi dài, cắt còn 3 giây nhé"* was said in a Telegram group and lost, or
wedged into a ``change_note`` on a version it was not about.

``pr_content_comments`` is that place, and its whole design is about staying out
of the workflow's way.

Not any of the things it sits near
-----------------------------------

* not an **approval** - ``pr_approval_events`` is the signed record of a gate
  decision, it has no ``updated_at`` because nothing may edit it, and a comment
  is editable by its author. Reading "đồng ý nhé" in a comment must never be
  mistakable for a sign-off, and the two being different tables is what makes
  that structural rather than a convention;
* not an **audit event** - the audit trail is what the *system* recorded about
  what happened. A comment is what a person chose to say, it is displayed back to
  them, and they may take it down;
* not a **content version** - no draft is written, no ``version_no`` moves, and
  no review is invalidated. Commenting on a piece at ``HEAD_REVIEW`` leaves it at
  ``HEAD_REVIEW``;
* not a **review resource** - that is material somebody reads *in order to*
  work. A comment is the work being talked about.

One level of threading, enforced in the service
------------------------------------------------

``parent_comment_id`` is nullable and self-referencing: ``NULL`` is a root and
anything else is a reply to a root. **A reply may not be replied to**, and that
rule lives in
:class:`~meobot.application.pr_content_comment_service.PrContentCommentService`
rather than in a ``CHECK``, because expressing "my parent has no parent" in a
row-level constraint needs a subquery no supported database allows there. What
the table does enforce is the one thing it can: a comment is not its own parent.

The alternative - a ``depth`` column - was refused. It is derivable from the
link, it can disagree with the link, and a stored number that can be wrong about
the structure beside it is worse than a check in one service method.

Soft-deleted, and only here
----------------------------

``deleted_at`` and ``deleted_by_user_id``. This is the **one** PR table that
soft-deletes, and the reason is specific rather than a change of policy: a
deleted root comment still has replies hanging off it, and hard-deleting it would
either orphan them or take somebody else's answers down with it. So the row stays
as a tombstone - the panel renders *"Đã xoá bình luận."* and the replies below it
remain readable.

Step 1F.2.3a removed soft deletion from ``pr_content_items`` for a reason that
does not apply here: a hidden content item was invisible in every list while
still being the row every foreign key pointed at, and "deleted" had come to mean
"cancelled, but harder to find". A tombstoned comment is not hidden. It is a
visible gap in a conversation, which is what it actually is.

**The body is kept.** It is not shown after deletion - the read model withholds
it, along with the author's name - but the column is not blanked, because
``pr_content_comments`` is where a moderation question ("what was in the comment
the lead removed?") has its only possible answer.

Foreign keys
------------

``RESTRICT`` throughout, like every other reference in this module, including the
self-reference. The aggregate is deleted explicitly and in order by
:class:`~meobot.application.pr_lifecycle_service.PrContentLifecycleService`,
which deletes replies before roots for exactly that reason - ``RESTRICT`` is
checked per row and would refuse a single statement removing a parent and its
child together.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty

#: The self-reference's target, written once so the model and the migration
#: cannot disagree about which table a reply points at.
COMMENTS_TABLE = "pr_content_comments"


class PrContentComment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One thing somebody said about one content item."""

    __tablename__ = COMMENTS_TABLE
    __table_args__ = (
        # Every comment has something in it, deleted ones included - see the
        # module docstring on why the body is kept rather than blanked.
        CheckConstraint(_not_empty("body"), name="body_not_empty"),
        # The one structural rule a row-level check can express. "My parent has
        # no parent" cannot be, and lives in the service.
        CheckConstraint(
            "parent_comment_id IS NULL OR parent_comment_id <> id", name="parent_not_self"
        ),
        # "The root comments of this item, oldest first" - the list the detail
        # page draws on every open, with the sort key in the index so the
        # ordering falls out of it rather than out of a sort.
        Index("ix_pr_content_comments_content_created", "content_id", "created_at"),
        # "The replies to these roots", one ``IN`` for the whole page. This is
        # what makes the thread two queries instead of one per root.
        Index("ix_pr_content_comments_parent", "parent_comment_id"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: Who said it. Not nullable and never inferred from the audit trail: a
    #: comment is displayed with a name beside it, and a row that cannot say
    #: whose words those are should not exist.
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: ``NULL`` for a root, a root's id for a reply. Never a reply's id - see
    #: the module docstring.
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{COMMENTS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: Plain text, stored as typed with the ends trimmed. Nothing parses it and
    #: nothing renders it as markup.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: When the author last changed the wording, and ``NULL`` if they never did.
    #:
    #: Its own column rather than a comparison against ``updated_at``, which
    #: also moves when a comment is tombstoned: *"đã sửa"* is a claim about the
    #: author rewording something, and a deletion is not that.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when the comment is taken down. The row survives - see the module
    #: docstring - and the read model stops sending the body and the name.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who took it down: its author, or somebody moderating. Kept because "the
    #: lead removed this" and "they thought better of it" are different events
    #: and the tombstone looks identical.
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )

    @property
    def is_deleted(self) -> bool:
        """Whether this comment has been taken down."""
        return self.deleted_at is not None

    @property
    def is_root(self) -> bool:
        """Whether this comment starts a thread rather than answering one."""
        return self.parent_comment_id is None


__all__: list[str] = ["COMMENTS_TABLE", "PrContentComment"]
