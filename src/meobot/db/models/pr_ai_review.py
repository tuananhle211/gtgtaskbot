"""PR content: the record of what an automated review said about a script.

Step 1A1. One table, ``pr_ai_reviews``, and nothing that calls a model: no
prompt, no client, no job, no transition. This step is the place the answer
goes, written before anything is able to produce one, so that the first review
ever run is auditable rather than retrofitted.

What the table is for
---------------------

An AI review is **advisory quality control**. It reads one specific version of
one content item and reports findings. It is not an approval, and this module
is built so that it cannot be mistaken for one:

* there is no ``reviewer_user_id`` here, and no foreign key to ``users`` at
  all. A row in this table names a *model*, not a person;
* nothing in this module writes ``pr_approval_events``. That table remains the
  record of what a human decided, and its shape is untouched by this step;
* ``AI_REVIEW`` is a :class:`~meobot.domain.pr.models.PrWorkflowStage` but
  deliberately **not** a :class:`~meobot.domain.pr.models.PrApprovalStage`, so
  there is no value an automated result could be filed under as an approval.

Append-only, like ``pr_approval_events``
----------------------------------------

No ``updated_at``, and nothing here updates a row. A second look at the same
version is a second row; a look at the next version is another. That is what
makes ``reviewed_version`` meaningful: "the script passed" is only ever an
answer about a specific draft, and a row that can be edited afterwards cannot
say which draft it was about. Nothing enforces uniqueness over
``(content_id, reviewed_version)`` - repeated attempts at one version are
normal, and a unique index would silently make the second attempt overwrite or
fail rather than accumulate.

JSON payloads
-------------

``issues``, ``suggestions`` and ``policy_flags`` are JSONB. Their intended
shapes are documented on the columns below and in
``docs/pr/STEP_1A1_AI_REVIEW_WORKFLOW.md``, and are **not** validated by the
database in this step. Findings are the part of a review most likely to grow a
field, and relational sub-tables would turn each of those into a migration
before anybody knows whether the field survives. Nothing parses these columns
yet; when something does, that is where the shape gets enforced.

Delete behaviour
----------------

Both foreign keys are ``ON DELETE RESTRICT``, matching every other PR table.
Deleting a content item or a task that has been reviewed fails rather than
quietly discarding the review history that explains why the script changed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, _not_empty
from meobot.domain.pr.models import PrAiReviewResult, PrAiReviewType


class PrAiReview(Base, UUIDPrimaryKeyMixin):
    """One automated review of one version of one content item.

    Read the module docstring first: this is history, not a decision, and the
    absence of a person on the row is the point.

    ``task_id`` is nullable for the same reason it is on
    :class:`~meobot.db.models.pr.PrApprovalEvent`: a review can be run against
    the content item itself, with no task standing behind it.

    ``score`` is optional. A review type that has no meaningful number - a
    checklist that either flagged something or did not - must be recordable
    without inventing one, so the column is nullable and only bounded when it
    is present.

    ``model_name``, ``model_version`` and ``prompt_version`` are what make an
    old row re-readable. Findings from a model that has since been replaced,
    or from a prompt that has since been rewritten, mean something different
    from today's; a review that cannot say which produced it is a paragraph of
    text with no provenance. ``model_version`` is nullable because some
    providers do not expose one; the other two are required and non-empty.

    ``reviewed_at`` is when the review ran, which is not when the row was
    written - a batch replayed from a queue is created now and was reviewed
    then, and an audit of "what did we know on Tuesday" needs the second
    number.
    """

    __tablename__ = "pr_ai_reviews"
    __table_args__ = (
        # There is no draft zero, exactly as on pr_approval_events.
        CheckConstraint("reviewed_version >= 1", name="reviewed_version_positive"),
        # Bounded only when present: a review without a score is valid.
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_in_range"),
        CheckConstraint(_not_empty("model_name"), name="model_name_not_empty"),
        CheckConstraint(_not_empty("prompt_version"), name="prompt_version_not_empty"),
        # The history of one item, in order - what a content card renders.
        Index("ix_pr_ai_reviews_content_reviewed_at", "content_id", "reviewed_at"),
        # "Has this version been reviewed, and how often" - the question rule
        # 11 asks (a new version needs a new review before it moves on) and the
        # reason no unique index sits on this pair.
        Index("ix_pr_ai_reviews_content_version", "content_id", "reviewed_version"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_tasks.id", ondelete=RESTRICT), nullable=True, index=True
    )
    review_type: Mapped[PrAiReviewType] = mapped_column(
        value_enum(PrAiReviewType, name="pr_ai_review_type", length=30),
        nullable=False,
        index=True,
    )
    #: Which draft was read. At least 1.
    reviewed_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[PrAiReviewResult] = mapped_column(
        value_enum(PrAiReviewResult, name="pr_ai_review_result", length=30),
        nullable=False,
        index=True,
    )
    #: 0-100 when the review type produces a number at all. Never required.
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    #: The review in a sentence or two, for a person. Never parsed.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``[{"code": str, "severity": "INFO"|"WARNING"|"ERROR", "message": str,
    #: "location": str | None}]``. Documented, not validated - see the module
    #: docstring.
    issues: Mapped[list[Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: ``[{"message": str, "location": str | None}]``. Documented, not
    #: validated.
    suggestions: Mapped[list[Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: ``[{"code": str, "severity": "WARNING"|"BLOCKING", "message": str}]``.
    #: Documented, not validated. A ``BLOCKING`` flag is a signal to the human
    #: reviewer; nothing in this step acts on it.
    policy_flags: Mapped[list[Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: Which model produced this. Required and non-empty - see the class
    #: docstring on why a review without provenance is not a review.
    model_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: The provider's version string, when there is one.
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which prompt was used. Required and non-empty.
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: When the review ran. Not when the row was written.
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: No ``updated_at`` beside it, by design. This table is append-only.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__: list[str] = ["PrAiReview"]
