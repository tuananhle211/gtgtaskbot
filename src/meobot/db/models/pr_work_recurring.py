"""Recurring work templates, their people, and the scheduler's own ledger.

Milestone M4B, migration ``0036``. Three tables, all new, and **nothing
existing is altered**. M1's ledger, M2's allocations and M6's scores are read
and never written: a template generates :class:`~meobot.db.models.pr_work.PrWorkItem`
rows through M1's own service and then has no further opinion about them.

Why the occurrence table is not an optimisation
------------------------------------------------

It would be tempting to store nothing but "last generated at" on the template
and let the work items themselves be the record. That fails in exactly the ways
a scheduler fails: it cannot tell an occurrence that was declined because its
month was closed from one that has not been reached; it cannot tell a template
that generated nothing on Sunday because Sunday is not one of its weekdays from
one that crashed on Sunday; and it advances past a failure the moment the
failure is recorded anywhere other than the row it belongs to.

:class:`PrWorkRecurringOccurrence` is therefore the **durable scheduler
ledger**, not a log of it. Its unique constraint over ``(template_id,
occurrence_key)`` is what makes two beat workers sweeping the same second
produce one occurrence rather than two, and its ``state`` is what makes the
answer to "why is there no work for Tuesday" a row somebody can read.

Why ``revision_no`` is on both
-------------------------------

A template may legitimately be edited while it is running - a routine whose
quantity went from eighty comments to a hundred is normal operations. Recording
the revision that produced each occurrence is what stops a sweep that started
before an edit and finished after it from writing a work item with the old work
type, the new quantity and yesterday's assignee list. The generator locks the
template row for the duration, and the occurrence keeps the version number so
the coherence is *provable* afterwards rather than merely intended.

Foreign keys
------------

``RESTRICT`` throughout, like every other PR table: a template that has
generated work is history, and history is not something a delete removes. An
unused ``DRAFT`` has generated nothing and may be deleted outright - that is a
service rule, and the constraint is what makes it safe.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.db.models.pr_work import WORK_TYPES
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.recurring import (
    MAX_OCCURRENCE_KEY_LENGTH,
    MAX_TEMPLATE_NAME,
    PrRecurringFrequency,
    PrRecurringOccurrenceState,
    PrRecurringTemplateStatus,
)
from meobot.domain.pr.work import PrWorkAssignmentMode

RECURRING_TEMPLATES = "pr_work_recurring_templates"
RECURRING_TEMPLATE_CONTRIBUTORS = "pr_work_recurring_template_contributors"
RECURRING_OCCURRENCES = "pr_work_recurring_occurrences"


class PrWorkRecurringTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One routine the department runs, and management's standing authorization for it.

    **A template is not work.** It appears in no workload, no quota allocation
    and no performance figure; it is the instruction that causes work to exist,
    and everything measurable happens to the items it generates.

    That is also why :attr:`activated_by_user_id` matters more than it looks. An
    ``ACTIVE`` template is a manager saying *"this happens every day, and I am
    asking for it"* - so the work it generates is legitimately
    ``ACCEPTED`` rather than ``PROPOSED``, and the person who said so is
    recorded here rather than invented at generation time. What it is **not** is
    validation: nothing here can make work count, and the independent approval
    M1 requires is still required.
    """

    __tablename__ = RECURRING_TEMPLATES
    __table_args__ = (
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        CheckConstraint("revision_no >= 1", name="revision_positive"),
        CheckConstraint(
            "due_after_hours IS NULL OR (due_after_hours > 0 AND due_after_hours <= 8760)",
            name="due_after_hours_sane",
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="date_range_ordered"),
        # The shape of a schedule and its parameters have to agree, because a
        # WEEKLY template with no weekdays is a routine that can never fire and
        # a MONTHLY one with no day is a routine that fires whenever the reader
        # guesses. Both are refused in the domain too; this is the floor.
        CheckConstraint(
            "(frequency = 'MONTHLY' AND day_of_month IS NOT NULL "
            "  AND day_of_month BETWEEN 1 AND 31) "
            "OR (frequency <> 'MONTHLY' AND day_of_month IS NULL)",
            name="day_of_month_matches_frequency",
        ),
        # "Which templates does the sweeper have to look at" - answered from the
        # index, and it is the only query beat runs.
        Index("ix_pr_work_recurring_templates_sweep", "status", "last_evaluated_occurrence_at"),
        Index("ix_pr_work_recurring_templates_work_type", "work_type_id"),
    )

    #: Becomes the ``title`` of every work item generated. Bounded to the same
    #: width as that column - see ``MAX_TEMPLATE_NAME``.
    name: Mapped[str] = mapped_column(String(MAX_TEMPLATE_NAME), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_TYPES}.id", ondelete=RESTRICT), nullable=False
    )
    #: **Stored here and nowhere else.** M4A established that the mode is not a
    #: fact about a work item; on a template it genuinely is one, because it has
    #: to survive until the next occurrence is generated.
    assignment_mode: Mapped[PrWorkAssignmentMode] = mapped_column(
        value_enum(PrWorkAssignmentMode, name="pr_work_assignment_mode", length=30),
        nullable=False,
    )
    #: Required when the work type is measured by ``QUANTITY``, refused as zero
    #: or negative, and copied onto each generated item. One hundred comments a
    #: day is one item with ``quantity = 100`` - never a hundred items.
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: **Tích lũy kết quả theo kỳ.** ``0039``.
    #:
    #: ``False`` is M4B exactly as it shipped: every firing is a new job with
    #: this template's ``quantity``. ``True`` makes the routine a *stream*: a
    #: firing ensures the **one** period container for each assignee and the
    #: month the firing falls in, and results are reported into it - "+3, +5,
    #: +2" - rather than a job being completed and validated per firing. The
    #: quantity is unused for such a routine, because what a stream should
    #: reach is a KPI target and a KPI target lives in the plan, not on the work.
    accumulate_by_period: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    priority: Mapped[PrPriority] = mapped_column(
        value_enum(PrPriority, name="pr_priority", length=20),
        nullable=False,
        default=PrPriority.NORMAL,
        server_default=PrPriority.NORMAL.value,
    )

    # --- The calendar -------------------------------------------------------
    frequency: Mapped[PrRecurringFrequency] = mapped_column(
        value_enum(PrRecurringFrequency, name="pr_recurring_frequency", length=20), nullable=False
    )
    #: Monday=0. Empty for anything but ``WEEKLY``. A list rather than a bitmask
    #: because a bitmask is a number nobody can read in a database console.
    weekdays: Mapped[list[int]] = mapped_column(
        JSONColumn, nullable=False, default=list, server_default="[]"
    )
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: **Local wall clock.** "Giờ tạo" - the hour the occurrence is stamped at,
    #: evaluated in ``settings.app_timezone`` and stored as UTC on the item.
    #: A time column rather than an instant because 09:00 means 09:00 in Ho Chi
    #: Minh City in June and in December alike.
    run_time: Mapped[time] = mapped_column(Time, nullable=False)
    #: How long after the occurrence the work is due. Null means no deadline,
    #: which is a real answer for a routine nobody times.
    due_after_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Local dates, inclusive. ``start_date`` is a floor and **not** a promise:
    #: activation is the other floor, and generation starts at whichever is
    #: later - see the generator's activation boundary.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- State --------------------------------------------------------------
    status: Mapped[PrRecurringTemplateStatus] = mapped_column(
        value_enum(PrRecurringTemplateStatus, name="pr_recurring_template_status", length=20),
        nullable=False,
        default=PrRecurringTemplateStatus.DRAFT,
        server_default=PrRecurringTemplateStatus.DRAFT.value,
        index=True,
    )
    #: Bumped by every edit that could change what an occurrence produces, and
    #: copied onto each occurrence. See the module docstring.
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    #: **The cursor**, and its meaning is exact: *every occurrence at or before
    #: this instant has already been evaluated and has a durable row, or fell in
    #: an interval this template was deliberately not running.*
    #:
    #: Deliberately **not** ``last_generated_at``. An occurrence that was
    #: declined because its month was closed, or that failed and is waiting to
    #: be retried, has been *evaluated* without being *generated*, and a cursor
    #: that only moved on success would either re-walk settled ground for ever
    #: or need a second column to remember it had.
    #:
    #: Null on a template that has never been active. Set to the activation
    #: boundary when it becomes ``ACTIVE``, which is what stops a template whose
    #: ``start_date`` is six weeks ago from generating six weeks of work in its
    #: first sweep, and moved forward to the resume instant on resume, which is
    #: what stops a paused fortnight from being backfilled.
    last_evaluated_occurrence_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When this template last produced anything. Operational reporting only -
    #: nothing branches on it, because the cursor above is the scheduler's
    #: state and two fields that could disagree would be one field too many.
    last_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Who, and when ------------------------------------------------------
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: **The standing authorization.** Every work item this template generates
    #: is created by, and assigned by, this person - so a routine job's audit
    #: trail names the manager who asked for the routine rather than a worker
    #: process. Null while the template has never been activated.
    activated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrWorkRecurringTemplateContributor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person a template names. Not a contribution - nobody has done anything yet.

    Separate from :class:`~meobot.db.models.pr_work.PrWorkContribution` for the
    reason that runs through the whole module: a contribution is a *share of a
    real job* and carries a credit weight, a count status and a counted
    timestamp. A name on a template is an instruction about jobs that do not
    exist. Storing them in one table would mean a template's people appeared in
    workload queries.
    """

    __tablename__ = RECURRING_TEMPLATE_CONTRIBUTORS
    __table_args__ = (
        UniqueConstraint("template_id", "user_id", name="uq_template_contributor"),
        CheckConstraint("display_order >= 0", name="display_order_not_negative"),
        Index("ix_pr_recurring_contributors_user", "user_id"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RECURRING_TEMPLATES}.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: Preserves the order people were named in, because the first name becomes
    #: the ``PRIMARY`` contributor of shared work - M1's rule, applied to a list
    #: that is stored rather than posted.
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class PrWorkRecurringOccurrence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One scheduled firing of one template, and what became of it.

    The row is written **before** the work exists, in its own committed
    transaction, and moved to ``GENERATED`` in the **same** transaction as the
    work items. That ordering is the whole restart story: a worker that dies in
    between leaves a ``PENDING`` row saying an occurrence was owed, and the next
    sweep finds it, retries it, and is stopped from producing a second copy by
    the work item's own ``(source_type, source_key)`` unique index - which is
    keyed on :attr:`id`, so a retry composes the identical key.

    :attr:`work_item_count` is a number and not a foreign key on purpose:
    ``SEPARATE_PER_ASSIGNEE`` produces several items from one occurrence, and a
    single nullable ``work_item_id`` would have quietly recorded one of them.
    """

    __tablename__ = RECURRING_OCCURRENCES
    __table_args__ = (
        # **The idempotency guarantee.** Two beat workers sweeping the same
        # template in the same second insert the same key; one of them loses,
        # and losing is the mechanism working.
        UniqueConstraint("template_id", "occurrence_key", name="uq_template_occurrence"),
        CheckConstraint(_not_empty("occurrence_key"), name="occurrence_key_not_empty"),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        CheckConstraint("work_item_count >= 0", name="work_item_count_not_negative"),
        CheckConstraint("template_revision_no >= 1", name="revision_positive"),
        # Work exists exactly when the state says it does. The pairing is the
        # reason "GENERATED" can be trusted by anything reading this table.
        CheckConstraint(
            "(state = 'GENERATED') = (generated_at IS NOT NULL)",
            name="generated_state_has_timestamp",
        ),
        CheckConstraint(
            "state = 'GENERATED' OR work_item_count = 0", name="only_generated_has_work"
        ),
        # "What has this template still to settle" - the retry set, and the
        # sweeper's second query.
        Index("ix_pr_recurring_occurrences_state", "template_id", "state", "scheduled_for"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RECURRING_TEMPLATES}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: ``20260904T0900`` - the local wall clock of the firing. See
    #: :func:`~meobot.domain.pr.recurring.occurrence_key` for why it is local.
    occurrence_key: Mapped[str] = mapped_column(String(MAX_OCCURRENCE_KEY_LENGTH), nullable=False)
    #: The same instant as an actual instant. Stored beside the key rather than
    #: parsed back out of it, because keys in this module are compared and never
    #: read - the same rule ``source_key`` follows.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Which version of the template produced this. See the module docstring.
    template_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[PrRecurringOccurrenceState] = mapped_column(
        value_enum(PrRecurringOccurrenceState, name="pr_recurring_occurrence_state", length=30),
        nullable=False,
        default=PrRecurringOccurrenceState.PENDING,
        server_default=PrRecurringOccurrenceState.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Why the last attempt failed, or why it was declined. Prose for a person:
    #: "cannot generate into a closed period" is an operational fact somebody
    #: has to be able to read without opening a log aggregator.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: How many work items this occurrence produced. One for ``SHARED_WORK``,
    #: one per assignee otherwise.
    work_item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: The reporting period the occurrence fell in, when there was one. Kept for
    #: the provenance a ``SKIPPED_CLOSED_PERIOD`` row has to be able to explain.
    reporting_period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete=RESTRICT), nullable=True
    )


__all__: list[str] = [
    "RECURRING_OCCURRENCES",
    "RECURRING_TEMPLATES",
    "RECURRING_TEMPLATE_CONTRIBUTORS",
    "PrWorkRecurringOccurrence",
    "PrWorkRecurringTemplate",
    "PrWorkRecurringTemplateContributor",
]
