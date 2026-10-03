"""Results declared into a period container. Migration ``0039``.

One table, additive. Nothing in M1's ledger changes meaning because it exists:
a :class:`~meobot.db.models.pr_work.PrWorkItem` with ``reporting_period_id``
set is a *stream* - one employee, one work type, one month - and each row here
is one declaration into it: *"+3 khách hàng"*, *"+520 bình luận"*, or one
content item the workflow approved.

Why a result is a row and not a counter
----------------------------------------

A counter on the container would answer "how many" and nothing else. A row
answers *who declared it, when, with what label and link, whether somebody who
did not do the work confirmed it, and - for a result another module
contributed - which record it came from so contributing it twice is
impossible*. The last one is the idempotency guarantee the Content mapping
needs: ``uq_pr_work_results_source`` over ``(source_type, source_key)`` is what
makes a replayed projection converge on one row.

Why an excluded result is out
------------------------------

``EXCLUDED`` is not enough on its own, because the projector has to know
whether it may restore the row: an administrator's removal is re-evaluated
against the source, a validator's rejection is not. ``exclusion_kind``
(:class:`~meobot.domain.pr.work_results.PrWorkExclusionKind`, ``0041``) carries
that distinction; ``excluded_reason`` stays the free text a person typed.

Status is M1's own vocabulary
------------------------------

``PENDING / COUNTED / EXCLUDED`` - :class:`~meobot.domain.pr.work.PrWorkCountStatus`,
reused rather than a fourth status enum. The boundary is the one the whole
module is built around, moved one grain down: *declaring a result is not being
credited for it*. A manual result is ``PENDING`` until a validator who is not
the subject counts it; a source-recorded result is ``COUNTED`` when the source
supplied an independent validator, exactly as ``count_source_work`` decides for
a one-off job.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.db.models.pr_work import WORK_ITEMS
from meobot.domain.pr.work import PrWorkCountStatus
from meobot.domain.pr.work_results import PrWorkExclusionKind, PrWorkResultSource

WORK_RESULTS = "pr_work_results"

#: Partial predicate for the idempotency index. Manual results have no key.
KEYED_RESULT = text("source_key IS NOT NULL")


class PrWorkResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One declared or system-contributed result inside a period container."""

    __tablename__ = WORK_RESULTS
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        # Counted results have a counting instant and uncounted ones do not -
        # the same pairing M1 puts on the contribution.
        CheckConstraint(
            "(status = 'COUNTED') = (counted_at IS NOT NULL)", name="counted_at_matches_status"
        ),
        CheckConstraint(
            "source_type = 'MANUAL' OR source_key IS NOT NULL", name="derived_result_is_keyed"
        ),
        # Only an excluded result has a reason for being out. ``0041``. The
        # reverse is not enforced: a row excluded before ``0041`` has no kind.
        CheckConstraint(
            "status = 'EXCLUDED' OR exclusion_kind IS NULL", name="exclusion_kind_matches_status"
        ),
        # **The idempotency guarantee.** One row per contributed record, across
        # every container: a content item approved, undone, re-approved and
        # replayed by three workers is one result.
        Index(
            "uq_pr_work_results_source",
            "source_type",
            "source_key",
            unique=True,
            postgresql_where=KEYED_RESULT,
            sqlite_where=KEYED_RESULT,
        ),
        # "What was declared into this stream, in order" - the container's own
        # list, and the sum behind its quantity.
        Index("ix_pr_work_results_item_status", "work_item_id", "status", "reported_at"),
        Index("ix_pr_work_results_user_reported", "user_id", "reported_at"),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_ITEMS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: The container's subject. Copied so a per-person list needs no join.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: The generic reporting fields. Free text for a person; a content result
    #: carries the content code here so the row reads as "CNT-2026-000042".
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_type: Mapped[PrWorkResultSource] = mapped_column(
        value_enum(PrWorkResultSource, name="pr_work_result_source", length=20),
        nullable=False,
        default=PrWorkResultSource.MANUAL,
        server_default=PrWorkResultSource.MANUAL.value,
    )
    #: ``content:{uuid}:CONTENT_CREATION``. Compared for equality, never parsed.
    source_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[PrWorkCountStatus] = mapped_column(
        value_enum(PrWorkCountStatus, name="pr_work_result_status", length=20),
        nullable=False,
        default=PrWorkCountStatus.PENDING,
        server_default=PrWorkCountStatus.PENDING.value,
        index=True,
    )
    reported_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: The business instant - when the result was achieved or declared. Not
    #: ``created_at``, which is when the row was written.
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    excluded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: **Why** the result is out - and therefore whether the projector may put
    #: it back. ``0041``. ``None`` on a row excluded before that revision.
    exclusion_kind: Mapped[PrWorkExclusionKind | None] = mapped_column(
        value_enum(PrWorkExclusionKind, name="pr_work_result_exclusion_kind", length=20),
        nullable=True,
    )


__all__: list[str] = ["WORK_RESULTS", "PrWorkResult"]
