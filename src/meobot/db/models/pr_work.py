"""The Work Ledger: what work exists, whose it is, and whether it counts.

Milestone M1, migration ``0032``. Five tables, all new, and **nothing existing
is altered**. ``pr_tasks``, ``pr_content_items`` and the whole content workflow
are untouched by this milestone - the ledger observes and records, and no
column anywhere else changes meaning because it exists.

M2 (``0033``) added exactly one column here - ``PrWorkType.default_quota_basis``
- and its own three tables in :mod:`meobot.db.models.pr_work_quota`. No M1
column changed meaning, no M1 rule changed, and ``COUNTED`` still means what it
meant: *this person's share of this job is valid completed work*. Whether it is
inside an approved quota is a **different** fact, recorded in a different table.

Why this is not ``pr_tasks`` with more columns
-----------------------------------------------

``PrTask.task_type`` is documented as deliberately ungoverned free text - *"the
list is still being discovered"* - which is the opposite of what a measured
taxonomy needs; a task has no acceptance gate, so creating one and being
credited for it would be the same act; and ``pr_approval_events.task_id``
already couples tasks into the content approval trail, so widening what a task
*means* widens that coupling. A separate additive ledger changes none of those
and risks none of them.

The two-table split that carries the whole design
--------------------------------------------------

:class:`PrWorkItem` is **one real job**. :class:`PrWorkContribution` is **one
person's share of it**. A shoot with three people is one item and three
contributions, so the department's count and each employee's workload are
different numbers read from different tables rather than one number divided or
multiplied by a headcount.

That is also where the anti-gaming boundary sits: an item becomes ``APPROVED``,
and *that* is what lets each contribution become ``COUNTED``. The approval is
refused when the actor appears in ``pr_work_contributions`` for that item, which
is a join rather than a policy anybody can forget to apply.

Foreign keys
------------

``RESTRICT`` throughout, following the rest of the PR module: a work type with
work filed under it cannot be deleted, and neither can a user with
contributions. History is not something a delete should be able to remove.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.work import (
    PrWorkCategory,
    PrWorkContributionRole,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis

WORK_TYPES = "pr_work_types"
WORK_ITEMS = "pr_work_items"
WORK_CONTRIBUTIONS = "pr_work_contributions"
WORK_EVIDENCE = "pr_work_evidence"
WORK_HISTORY = "pr_work_history"

#: The partial unique index that makes source-derived work idempotent.
#:
#: Partial over ``source_key IS NOT NULL`` because manual work has no key and
#: never can: a non-partial index would collapse every manual row onto one
#: shared ``NULL`` in a way PostgreSQL happens to allow and MeoBot should not
#: depend on. Spelled in SQL both PostgreSQL and SQLite accept, because the
#: offline test suite builds this schema from the models.
KEYED_SOURCE = text("source_key IS NOT NULL")

#: The partial index predicate that makes a period container unique per
#: employee, work type and month. See ``uq_pr_work_items_period_container``.
PERIOD_CONTAINER = text("reporting_period_id IS NOT NULL")


class PrWorkType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One kind of work the department does. Configuration, not code.

    The **hybrid** half of the taxonomy: :attr:`category` is an enum that code
    groups by, and the row itself is the sub-type that the business owns and
    edits without a deploy. "Trend edit" is a row; ``PRODUCTION`` is a member of
    :class:`~meobot.domain.pr.work.PrWorkCategory`.

    :attr:`code` is the **stable machine identifier** and :attr:`name` is what a
    person reads. Nothing authorises, groups or branches on the name: a display
    label that acquired business meaning would make renaming "Kịch bản ngắn" a
    breaking change.

    **No score, no multiplier, no point value.** M1 measures how much valid work
    happened; what it is worth is M6's question, and putting a number here now
    would be inventing a rate nobody has approved.
    """

    __tablename__ = WORK_TYPES
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        CheckConstraint("display_order >= 0", name="display_order_not_negative"),
        Index("ix_pr_work_types_category_order", "category", "display_order"),
    )

    #: ``SHORT_SCRIPT``, ``HALF_DAY_SHOOT``. Upper snake, stable for ever.
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[PrWorkCategory] = mapped_column(
        value_enum(PrWorkCategory, name="pr_work_category", length=20), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What one unit of a work item's ``quantity`` counts.
    #:
    #: Chosen by the **type**, and copied onto each item at creation. That is
    #: what stops "100 comments" being filed as a hundred items by whoever
    #: preferred the shape: the unit is not the filer's decision.
    default_unit: Mapped[PrWorkUnit] = mapped_column(
        value_enum(PrWorkUnit, name="pr_work_unit", length=20),
        nullable=False,
        default=PrWorkUnit.ITEM,
        server_default=PrWorkUnit.ITEM.value,
    )
    #: **How this kind of work is measured against a KPI quota.** M2.
    #:
    #: On the *type* rather than only on each quota, because how a kind of work
    #: is measured is a fact about the work and not a per-employee negotiation:
    #: two people's plans measuring "seeding comments" differently - one by
    #: rows, one by comments - would make a department-wide figure mean nothing,
    #: and would hand whoever chose ``ITEM_COUNT`` a hundredfold advantage.
    #:
    #: It is what a quota's own ``basis`` is validated against, and it is what
    #: lets a counted contribution with **no approved quota** still be reported
    #: in the right units - see
    #: :class:`~meobot.db.models.pr_work_quota.PrWorkQuotaAllocation`.
    #:
    #: **Structural since M2.5, and locked once the type is in use** - see
    #: :data:`~meobot.domain.pr.work_types.WORK_TYPE_STRUCTURAL_FIELDS`. M1 and
    #: M2 left it editable at any time, on the reasoning that every quota and
    #: allocation stores the basis it was decided under so no history is
    #: rewritten. True, and insufficient: changing it silently re-measures how
    #: counted work with **no** approved quota is reported, and a type with an
    #: approved quota already written against its basis must not move underneath
    #: it. Editable freely while nothing references the type.
    default_quota_basis: Mapped[PrWorkQuotaBasis] = mapped_column(
        value_enum(PrWorkQuotaBasis, name="pr_work_quota_basis", length=20),
        nullable=False,
        default=PrWorkQuotaBasis.ITEM_COUNT,
        server_default=PrWorkQuotaBasis.ITEM_COUNT.value,
    )
    #: Whether finishing this kind of work requires a link or a file. Enforced
    #: at completion rather than at insert, the way ``required_for_review``
    #: already works on content resources.
    requires_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Inactive types keep their history and stop being offered. Never deleted -
    #: a type with work filed under it is referenced by ``RESTRICT`` anyway.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class PrWorkItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One real job. Not one person's workload - see :class:`PrWorkContribution`.

    The five timestamps down the middle of this table are the milestone's whole
    argument: ``created_at``, ``accepted_at``, ``completed_at``, ``approved_at``
    and - one table over - ``counted_at`` are five different facts, written by
    five different acts, and no trigger derives any of them from ``status``.
    That is the same reasoning ``PrTask.completed_at`` is left undeirved for,
    applied to a ladder that has an anti-gaming rule on it.

    ``quantity`` and ``unit``
    --------------------------

    One hundred comments is **one** work item with ``quantity = 100``, not a
    hundred items. The unit is copied from the work type at creation rather
    than joined at read time, so editing a type never rewrites what historical
    work claimed to be.
    """

    __tablename__ = WORK_ITEMS
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        # A one-off job claims a positive amount or none. A **period container**
        # (``reporting_period_id`` set) is the one row whose quantity is derived
        # - the sum of its counted results - and a stream nobody has validated
        # yet genuinely holds zero, which has to be representable so an empty
        # month reads "0 khách hàng" rather than nothing at all.
        CheckConstraint(
            "quantity IS NULL OR quantity > 0 OR reporting_period_id IS NOT NULL",
            name="quantity_positive",
        ),
        # A quantity needs a unit and a unit needs a quantity: "100" of nothing
        # and "comments" of no number are both unreadable on a report.
        CheckConstraint("(quantity IS NULL) = (unit IS NULL)", name="quantity_and_unit_together"),
        # A container belongs to exactly one person and one month, together.
        CheckConstraint(
            "(reporting_period_id IS NULL) = (subject_user_id IS NULL)",
            name="period_container_has_subject",
        ),
        # **One stream per employee, work type and month.** The rule that makes
        # "27 customers in September" one row with 27 units rather than twenty
        # rows somebody has to add up - and what every creation path is a
        # get-or-create against. Partial, so one-off work is unconstrained.
        Index(
            "uq_pr_work_items_period_container",
            "work_type_id",
            "reporting_period_id",
            "subject_user_id",
            unique=True,
            postgresql_where=PERIOD_CONTAINER,
            sqlite_where=PERIOD_CONTAINER,
        ),
        CheckConstraint(
            "source_type = 'MANUAL' OR source_key IS NOT NULL", name="derived_work_is_keyed"
        ),
        # The idempotency guarantee. Partial, so manual work is unconstrained
        # and every derived work event exists at most once - see ``KEYED_SOURCE``
        # and ``work_source_key``.
        Index(
            "uq_pr_work_items_source",
            "source_type",
            "source_key",
            unique=True,
            postgresql_where=KEYED_SOURCE,
            sqlite_where=KEYED_SOURCE,
        ),
        # "What is open and when is it due" - the operational list, and the
        # overdue sweep, answered from the index.
        Index("ix_pr_work_items_status_due", "status", "due_at"),
        Index("ix_pr_work_items_type_status", "work_type_id", "status"),
        # "What did I file, and what have I still to accept" - the manager's two
        # queues, both keyed on the person rather than on a team that does not
        # exist in this system.
        Index("ix_pr_work_items_created_by_status", "created_by_user_id", "status"),
        Index("ix_pr_work_items_assigned_by", "assigned_by_user_id"),
    )

    #: ``WRK-2026-000001``. Allocated by ``PrCodeService`` in the same
    #: transaction as the row, like every other PR entity's code.
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_TYPES}.id", ondelete=RESTRICT), nullable=False, index=True
    )

    # --- Where it came from ----------------------------------------------
    source_type: Mapped[PrWorkSourceType] = mapped_column(
        value_enum(PrWorkSourceType, name="pr_work_source_type", length=20),
        nullable=False,
        default=PrWorkSourceType.MANUAL,
        server_default=PrWorkSourceType.MANUAL.value,
    )
    #: ``content:{uuid}:SCRIPT_APPROVED``. Null for manual work, and required
    #: for everything else by ``derived_work_is_keyed``. Never parsed - it is
    #: compared for equality and nothing more. See
    #: :func:`~meobot.domain.pr.work.work_source_key` for the contract.
    source_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # --- State -------------------------------------------------------------
    status: Mapped[PrWorkStatus] = mapped_column(
        value_enum(PrWorkStatus, name="pr_work_status", length=20),
        nullable=False,
        default=PrWorkStatus.PROPOSED,
        server_default=PrWorkStatus.PROPOSED.value,
        index=True,
    )
    priority: Mapped[PrPriority] = mapped_column(
        value_enum(PrPriority, name="pr_priority", length=20),
        nullable=False,
        default=PrPriority.NORMAL,
        server_default=PrPriority.NORMAL.value,
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    unit: Mapped[PrWorkUnit | None] = mapped_column(
        value_enum(PrWorkUnit, name="pr_work_unit", length=20), nullable=True
    )

    # --- Who, and when ------------------------------------------------------
    #: Whoever filed the row. For a proposal this is the proposer, and it is
    #: what ``accept`` compares itself against.
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: The manager who put this into somebody's workload - by assigning it, or
    #: by accepting a proposal. Null while a proposal is still a proposal.
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: When somebody who did **not** do the work confirmed it. The only
    #: timestamp that can make a contribution countable.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: **When the work was actually performed, or is scheduled to be.** ``0037``.
    #:
    #: A different fact from every timestamp around it, which is why it is a
    #: column rather than a derivation: ``created_at`` is when the row was
    #: written, ``due_at`` is when it must be finished, ``accepted_at`` is when
    #: it entered a workload, ``completed_at`` is when somebody said it was
    #: done, and ``counted_at`` - one table over - is when it was validated.
    #: None of those is *"on what day did this happen"*.
    #:
    #: Written only by the paths that are **told** the answer by a source:
    #: content-derived work takes M3.1's canonical milestone instant, and
    #: recurring work takes the occurrence's ``scheduled_for``. **Manual work
    #: leaves it null** - a person filing a job states a deadline and never an
    #: execution date, and putting ``due_at`` here would label a deadline as a
    #: performance.
    #:
    #: Deliberately not derived from ``completed_at``: ``reopen`` sets that back
    #: to null, so a validator sending work back would erase the day the writer
    #: delivered it.
    execution_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: **Which firing of which recurring template produced this.** ``0037``.
    #:
    #: A plain foreign key rather than something decoded out of ``source_key``,
    #: and the distinction is the module's own rule: a source key is compared
    #: for equality and never parsed, because a key somebody starts taking
    #: substrings of is a key that will one day be taken apart wrongly.
    #:
    #: Many-to-one on purpose - one occurrence in ``SEPARATE_PER_ASSIGNEE`` mode
    #: produces one item per assignee. The occurrence ledger already links
    #: occurrence to template, so this is the whole of what a work card needs to
    #: offer *"đi tới công việc định kỳ"*.
    #:
    #: Null for everything a recurring template did not generate.
    recurring_occurrence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_work_recurring_occurrences.id", ondelete=RESTRICT),
        nullable=True,
        index=True,
    )
    #: **The period container columns.** ``0039``.
    #:
    #: Set together, or not at all. A row with both is *one work stream for one
    #: employee for one reporting month*: results accumulate inside it, its
    #: ``quantity`` is the sum of the results a validator counted, and a KPI
    #: target is read beside that sum for comparison only. A row with neither is
    #: a one-off job exactly as M1 shipped it.
    #:
    #: Not a status and not a flag, because the period *is* the fact: which
    #: month a stream belongs to is what makes September's 27 and October's 0
    #: two different rows rather than one counter somebody has to reset.
    reporting_period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: Whose stream this is. Always the container's one ``PRIMARY``
    #: contributor; carried on the row so the unique index can name it.
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: Why it was cancelled or rejected. Prose for a person, kept because
    #: "somebody cancelled this" without a reason is what makes people ask.
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Optional attributions ---------------------------------------------
    #: The channel this work was for, when it was for one. Written by M1.
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: Reserved for the M3 content projector and the M4 task bridge. **No M1
    #: code path writes either**, and they are here rather than added later
    #: because a foreign key added to a populated table is a migration with a
    #: backfill decision attached, while a nullable column on an empty one is
    #: not.
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=True, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_tasks.id", ondelete=RESTRICT), nullable=True, index=True
    )

    @property
    def is_period_container(self) -> bool:
        """True for a stream that accumulates results by month. See the columns."""
        return self.reporting_period_id is not None


class PrWorkContribution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person's share of one job. **The row a KPI reads.**

    Count status lives here and not on the item, because a quota belongs to a
    person: three people on one shoot have three quotas between them, and one
    status on the item could not answer three questions. It is also what lets
    the two figures reporting needs stay separate - the department did *one*
    shoot, and *three* people each did a day's work.

    ``credit_weight`` defaults to a full unit for everybody and is never divided
    automatically. See
    :data:`~meobot.domain.pr.work.DEFAULT_CREDIT_WEIGHT`.

    **No per-person acceptance or completion columns**, unlike
    ``pr_task_assignments``. M1 has no semantics for one contributor finishing
    before another: the item is completed once and validated once. Columns
    nothing writes are columns that lie, so they are absent until a milestone
    needs them.
    """

    __tablename__ = WORK_CONTRIBUTIONS
    __table_args__ = (
        CheckConstraint("credit_weight > 0 AND credit_weight <= 1", name="credit_weight_in_range"),
        # Counted work has a time, and uncounted work does not. The pair is
        # written together by ``approve`` and this is what refuses a half-write.
        CheckConstraint(
            "(count_status = 'COUNTED') = (counted_at IS NOT NULL)",
            name="counted_at_matches_status",
        ),
        # One person, one capacity, one job. What makes crediting somebody twice
        # for the same work in the same capacity unrepresentable rather than
        # merely unlikely - the same shape ``uq_pr_task_assignments_task_user_role``
        # already has.
        Index(
            "uq_pr_work_contributions_item_user_role",
            "work_item_id",
            "user_id",
            "contribution_role",
            unique=True,
        ),
        # **The KPI index.** "What has this person had counted, and when" - the
        # question every period figure asks, answered without touching the
        # items table.
        Index("ix_pr_work_contributions_user_count", "user_id", "count_status", "counted_at"),
        # "What is on this person's plate" - joined to the item for its status
        # and deadline.
        Index("ix_pr_work_contributions_user_item", "user_id", "work_item_id"),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_ITEMS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    contribution_role: Mapped[PrWorkContributionRole] = mapped_column(
        value_enum(PrWorkContributionRole, name="pr_work_contribution_role", length=20),
        nullable=False,
        default=PrWorkContributionRole.PRIMARY,
        server_default=PrWorkContributionRole.PRIMARY.value,
    )
    credit_weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("1.0")
    )
    #: When this person joined the job. Distinct from ``created_at``: a
    #: contributor added on day three was assigned on day three, and a report
    #: about day three needs that number rather than the row's birthday.
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    count_status: Mapped[PrWorkCountStatus] = mapped_column(
        value_enum(PrWorkCountStatus, name="pr_work_count_status", length=20),
        nullable=False,
        default=PrWorkCountStatus.PENDING,
        server_default=PrWorkCountStatus.PENDING.value,
        index=True,
    )
    #: **The period-attribution timestamp.** A contribution belongs to the
    #: reporting period containing this instant - not the period it was
    #: assigned in and not the one it was completed in. Work assigned on 31
    #: August and validated on 2 September is September's.
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    excluded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrWorkEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A link or a note proving a job was done.

    Shaped like ``PrContentResource`` on purpose so the two read alike, and
    **not** reusing it: that table's ``content_id`` is ``NOT NULL`` with
    ``ondelete=RESTRICT``, and most work has no content item to hang off.

    ``location`` is a URL or a path, not a file. M1 builds no binary storage: the
    department already keeps its output in Drive and on the NAS, and a second
    place for the same files would be a migration nobody asked for.
    """

    __tablename__ = WORK_EVIDENCE
    __table_args__ = (
        CheckConstraint(_not_empty("label"), name="label_not_empty"),
        CheckConstraint(_not_empty("location"), name="location_not_empty"),
        Index("ix_pr_work_evidence_item_created", "work_item_id", "created_at"),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_ITEMS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )


class PrWorkHistory(Base, UUIDPrimaryKeyMixin):
    """What happened to one piece of work, for a person to read. Append-only.

    The **domain** history, and deliberately not a copy of the audit trail. The
    two are written in the same transaction and carry different things:

    * ``audit_logs`` gets the security record - the request id, the actor, the
      structured ``before``/``after`` of what changed. It answers *"who changed
      what"* for somebody investigating;
    * this gets the story - the event, the status edge, the note somebody
      typed. It answers *"what happened to my work"* on a timeline, in SQL,
      without parsing a JSON payload nothing constrains.

    That distinction is the one Step 1F.2.3b already made for content
    transitions, for the same reason: a business question answered by parsing an
    audit payload starts returning the wrong answer the day somebody renames a
    key.

    No ``updated_at`` and nothing updates a row. A correction is a later event.
    """

    __tablename__ = WORK_HISTORY
    __table_args__ = (
        # "The timeline of this item, in order" - the only query this table has.
        Index("ix_pr_work_history_item_created", "work_item_id", "created_at"),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_ITEMS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: Set on the per-person events - ``COUNTED``, ``CONTRIBUTOR_ADDED`` - so a
    #: three-person approval leaves one ``APPROVED`` row and three ``COUNTED``
    #: rows that each name whose credit moved.
    contribution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{WORK_CONTRIBUTIONS}.id", ondelete=RESTRICT), nullable=True
    )
    event_type: Mapped[PrWorkEventType] = mapped_column(
        value_enum(PrWorkEventType, name="pr_work_event_type", length=30), nullable=False
    )
    from_status: Mapped[PrWorkStatus | None] = mapped_column(
        value_enum(PrWorkStatus, name="pr_work_status", length=20), nullable=True
    )
    to_status: Mapped[PrWorkStatus | None] = mapped_column(
        value_enum(PrWorkStatus, name="pr_work_status", length=20), nullable=True
    )
    #: Nullable because a future projector acts on nobody's behalf - not because
    #: the person might go away.
    #:
    #: ``RESTRICT`` like every other foreign key in the PR module, and
    #: deliberately **not** ``audit_logs``' ``SET NULL``. The two have different
    #: rules for a reason: a MeoBot user row is never hard-deleted - suspension
    #: and revocation are a status and a timestamp, precisely so that history
    #: stays attached to the person who made it. Forgetting who moved a piece of
    #: work would be losing the answer this table exists to give.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Small structured extras - the old and new deadline, the contributor's
    #: name. **Not** a second copy of the audit row's payload.
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONColumn, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__: list[str] = [
    "PERIOD_CONTAINER",
    "WORK_CONTRIBUTIONS",
    "WORK_EVIDENCE",
    "WORK_HISTORY",
    "WORK_ITEMS",
    "WORK_TYPES",
    "PrWorkContribution",
    "PrWorkEvidence",
    "PrWorkHistory",
    "PrWorkItem",
    "PrWorkType",
]
