"""PR content: the record of an AI review *execution*, as opposed to its answer.

Step 1F. ``pr_ai_reviews`` is append-only and always will be - a verdict about a
draft is history, and history that can be edited is not history. But an
execution has a lifecycle: it is queued, it runs, it succeeds or fails, it may
be retried, and it may finish only to discover the draft moved underneath it.

Those are two different things and this is the second table.

```
pr_ai_review_runs        pr_ai_reviews
────────────────────     ────────────────────
QUEUED  → RUNNING   ──▶  one appended row, on success
        → SUCCEEDED      (never updated, never deleted)
        → FAILED
        → SUPERSEDED
```

``review_id`` points from the run to the row it produced. Nothing points back:
a review must remain readable and meaningful with no run behind it, because
reviews recorded before Step 1F existed have none.

What the version pin is for
---------------------------

``content_version_id`` is the exact immutable draft this execution is about,
fixed when the run was queued. The worker loads *that* row, not "whatever is
current when I get round to it". When the run finishes, the version is compared
against the item's current draft: if somebody rewrote it in the meantime the
answer is about text nobody is looking at any more, the run becomes
``SUPERSEDED``, and **no workflow transition happens**. That is the whole
defence behind "an AI review of version N is never an approval of version N+1".

One active execution per draft
------------------------------

``uq_pr_ai_review_runs_active`` is a partial unique index over
``(content_id, content_version_id, review_type)`` restricted to ``QUEUED`` and
``RUNNING``. Two callers entering ``AI_REVIEW``, a double-clicked retry and a
duplicated Celery delivery all collide there rather than in application code.
Terminal rows are excluded, so a failed run can legitimately be followed by
another attempt at the same draft, and the history of both is kept.

Nothing secret lives here
-------------------------

``error_code`` is a stable machine string (``llm_error``, ``invalid_output``) and
never a provider message, a stack trace or a URL. No prompt text, no API key, no
response body. The table is read by the web panel, and everything in it is
something a person may see.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, _not_empty
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewRunStatus,
    PrAiReviewTrigger,
    PrAiReviewType,
)

#: The two statuses that hold the single active slot for one draft. A literal
#: here as well as in the domain enum, because it is written into a partial
#: index predicate and an index cannot import Python.
_ACTIVE_PREDICATE = "status IN ('QUEUED', 'RUNNING')"


class PrAiReviewRun(Base, UUIDPrimaryKeyMixin):
    """One attempt to have a model review one draft.

    Read the module docstring first: this row is mutable on purpose, and the
    row it produces is not.

    ``requested_by_user_id`` is **who asked**, never who reviewed. An automatic
    run has none; a manual retry names the person who pressed the button. It
    exists so "why did this run twice" has an answer, and it must never be read
    as an approver - ``pr_ai_reviews`` still has no user column at all, and
    ``pr_approval_events`` is still the only record of a human decision.

    ``model_name`` and ``model_version`` are null until the run actually calls a
    provider: at queue time nobody knows which model will answer, and writing
    the configured one would claim provenance for a call that had not happened.
    ``prompt_version`` is known at queue time because it is ours.

    ``outcome`` is the derived result, kept on the run as well as on the review
    so a ``SUPERSEDED`` execution - which by design writes no review row - still
    says what it concluded.
    """

    __tablename__ = "pr_ai_review_runs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_not_negative"),
        CheckConstraint(_not_empty("prompt_version"), name="prompt_version_not_empty"),
        CheckConstraint(
            "model_name IS NULL OR length(trim(model_name)) > 0", name="model_name_not_blank"
        ),
        # A finished run has an end; an unfinished one does not claim to.
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL", name="finished_implies_started"
        ),
        # The idempotency rule, in the database. See the module docstring.
        # Declared for both dialects this project runs on: PostgreSQL in
        # production, SQLite in the unit tests. Both support partial indexes,
        # so the constraint is tested rather than assumed.
        Index(
            "uq_pr_ai_review_runs_active",
            "content_id",
            "content_version_id",
            "review_type",
            unique=True,
            postgresql_where=text(_ACTIVE_PREDICATE),
            sqlite_where=text(_ACTIVE_PREDICATE),
        ),
        # "What is the latest run for this item" - what the detail page asks on
        # every load and on every poll.
        Index("ix_pr_ai_review_runs_content_created", "content_id", "created_at"),
        # The sweeper's query: oldest queued work first.
        Index("ix_pr_ai_review_runs_status_created", "status", "created_at"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: The exact immutable draft this run reviews. Fixed at queue time.
    content_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_versions.id", ondelete=RESTRICT), nullable=False
    )
    review_type: Mapped[PrAiReviewType] = mapped_column(
        value_enum(PrAiReviewType, name="pr_ai_review_type", length=30), nullable=False
    )
    trigger: Mapped[PrAiReviewTrigger] = mapped_column(
        value_enum(PrAiReviewTrigger, name="pr_ai_review_trigger", length=20), nullable=False
    )
    status: Mapped[PrAiReviewRunStatus] = mapped_column(
        value_enum(PrAiReviewRunStatus, name="pr_ai_review_run_status", length=20),
        nullable=False,
        index=True,
    )
    #: Who asked. Never who reviewed - see the class docstring.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete=RESTRICT), nullable=True
    )
    #: Null until a provider has actually answered.
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Ours, so known when the run is queued.
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: How many times execution has been started. Bounds the retry policy.
    #: ``server_default`` as well as ``default``: migration 0018 writes one, and
    #: without it here the ORM metadata and the schema disagree - which
    #: ``test_pr_core_migrations`` reports as permanent autogenerate drift.
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: The appended review this run produced, when it produced one.
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_ai_reviews.id", ondelete=RESTRICT), nullable=True
    )
    #: The derived result, kept here too so a superseded run still says what it
    #: concluded about the draft it read.
    outcome: Mapped[PrAiReviewResult | None] = mapped_column(
        value_enum(PrAiReviewResult, name="pr_ai_review_result", length=30), nullable=True
    )
    #: A stable machine string. Never a provider message or a traceback.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Present, unlike on ``pr_ai_reviews``: this row is a lifecycle and is
    #: meant to change.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__: list[str] = ["PrAiReviewRun"]
