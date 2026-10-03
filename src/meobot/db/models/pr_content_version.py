"""PR content: the immutable draft history behind ``reviewed_version``.

Step 1C. One table, ``pr_content_versions``.

Why it had to exist
-------------------

Steps 1A and 1A1 recorded *which* version a reviewer judged -
``pr_approval_events.version_reviewed``, ``pr_ai_reviews.reviewed_version`` -
but nothing stored what that version actually said. The integer pointed at
nothing. A review of version 4 and a review of version 5 were distinguishable
only by a number nobody could resolve back to text, which meant the one rule
those columns exist to enforce - *a PASS belongs to the draft it was given* -
could not be checked, only asserted.

This table is the thing they point at. ``(content_id, version_no)`` is unique,
so version 4 is one row forever, and the row is never updated.

Relationship to ``pr_content_items``
------------------------------------

The mutable columns on ``pr_content_items`` - ``title``, ``topic``, ``hook``,
``brief`` - stay exactly as they are and remain the **current projection**: the
values a list view reads without joining. They are not the record. The record
is the newest row here, and
:class:`~meobot.application.pr_content_service.PrContentService` writes both in
one transaction so the projection can never describe a draft that does not
exist.

``script_text`` has no counterpart on ``pr_content_items`` and deliberately
gets no column there. The script is the part a reviewer reads in full; putting
a mutable copy of it on the item would create a second answer to "what does
this draft say", and the whole point of this table is that there is one.

Append-only
-----------

No ``updated_at``, and nothing in the module updates a row. Correcting a typo
is version N+1, which is a fact somebody can review, rather than an edit that
silently changes what a past reviewer approved.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty


class PrContentVersion(Base, UUIDPrimaryKeyMixin):
    """One immutable draft of one content item.

    ``version_no`` starts at 1 and is allocated by
    :class:`~meobot.application.pr_content_service.PrContentService` under the
    content row's lock. The unique index is what makes that allocation safe:
    two concurrent revisions racing to write version 5 do not both succeed, and
    the loser fails the transaction rather than overwriting a draft.

    ``created_by_user_id`` is required. A draft nobody wrote is not a draft,
    and the review history hanging off it would have no author to ask.
    """

    __tablename__ = "pr_content_versions"
    __table_args__ = (
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        # Unique, and therefore also *the* index on ``(content_id,
        # version_no)``: a B-tree unique index answers "the versions of this
        # item, in order" and "does version 5 exist" from its leftmost prefix.
        # A second, non-unique index on the same pair would cost a write on
        # every append and buy nothing.
        Index(
            "uq_pr_content_versions_content_version",
            "content_id",
            "version_no",
            unique=True,
        ),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: Which draft this is. At least 1, contiguous in practice because the
    #: service allocates ``latest + 1``, but the database only enforces
    #: uniqueness - a gap left by a rolled-back transaction is not corruption.
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The script itself. Lives only here - see the module docstring.
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What changed and why, for the reviewer who saw the previous draft.
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: No ``updated_at`` beside it, by design. This table is append-only.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


__all__: list[str] = ["PrContentVersion"]
