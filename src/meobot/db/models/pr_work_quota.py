"""The quota engine's three tables: the plan, its quotas, and what they decided.

Milestone M2, migration ``0033``. Three new tables plus **one nullable column**
on ``pr_work_types``; nothing else changes, no M1 row is written by the
revision, and ``pr_work_items`` and ``pr_work_contributions`` come through it
byte for byte.

Why the decision is a row and not a column on the contribution
---------------------------------------------------------------

M1's handover recommended ``quota_status`` directly on
``pr_work_contributions``. M2 does **not** do that, and the two reasons are the
whole shape of this file:

* **partial eligibility does not fit in a status.** A ``QUANTITY`` contribution
  that straddles the cap is 40 comments eligible and 20 over, and three numbers
  cannot be one enum. Widening the contribution to carry them would put four
  quota columns on the table M1 built to mean *"is this valid completed
  work"* - the exact collapsing of two facts that the module exists to prevent;

* **an allocation is about a period, and a contribution is not.** The decision
  belongs to ``(contribution, reporting period, approved plan version)``, and
  it has to name the plan and quota version that produced it so that "why was
  this eligible yesterday and over quota today" is answerable from the row. A
  column on the contribution would have nowhere to keep that provenance and
  would have to be rewritten in place, losing it.

The one thing the separate table costs - a join to read a contribution's
status - is paid back immediately: a counted contribution with **no allocation
row at all** is a legible state, and it is the state every pre-M2 row is in.
It reads as ``NO_QUOTA``, which is the truth: no approved quota has claimed it.

What is deliberately absent
----------------------------

**Every score.** No ``base_score``, no ``awarded_score``, no ``points``, no
``quality_multiplier``, no ``bonus``. M2 decides *eligibility against an
approved quota* and awards nothing; M6 owns points, and a column for one here
would be an unapproved rate sitting in the schema.

Foreign keys
------------

``RESTRICT`` throughout, following M1 and the rest of the PR module. A plan
that decided something cannot be deleted, and neither can the period it was
written for.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.db.models.pr_work import WORK_CONTRIBUTIONS, WORK_TYPES
from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_quota import (
    PrWorkPlanStatus,
    PrWorkQuotaBasis,
    PrWorkQuotaStatus,
    PrWorkUnmeasurableReason,
)

WORK_PLANS = "pr_work_plans"
WORK_QUOTAS = "pr_work_quotas"
WORK_QUOTA_ALLOCATIONS = "pr_work_quota_allocations"
REPORTING_PERIODS = "pr_reporting_periods"

#: The partial index predicate that makes "one plan in force" a database fact.
#:
#: Partial rather than a unique on ``(user_id, period_id)``, because superseded
#: versions are kept for ever and drafts coexist with the approved plan they
#: will replace. Spelled in SQL both PostgreSQL and SQLite accept, because the
#: offline suite builds this schema from the models.
APPROVED_PLAN = text("status = 'APPROVED'")

#: The same, for the one draft revision in flight.
#:
#: One draft at a time is a product decision, not a technical one: two people
#: each writing a different next version of one employee's plan is a conflict
#: that ought to surface while it is still cheap, and "somebody is already
#: revising this" is a better refusal than two plans racing to be approved.
DRAFT_PLAN = text("status = 'DRAFT'")


class PrWorkPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One employee's KPI plan for one reporting period, at one version.

    **Versioned rather than edited**, following the rule Step 1B's weekly manual
    reporting input already documents - *"a correction is a new version, never
    an edit"* - and for the sharper reason that a quota decides how much of
    somebody's work counts.
    Whoever raised a cap in the middle of a month has to be visible afterwards,
    and an approved plan that could be edited in place would erase them.

    So the lifecycle is::

        APPROVED v1  ──►  DRAFT v2  ──edit──►  approve v2
                                                   │
                                        v1 → SUPERSEDED, v2 → APPROVED
                                        (one transaction, or neither)

    ``supersedes_plan_id`` links the chain backwards, so a reader holding an
    allocation can walk from the version that decided it to the version that
    replaced it and see what changed.

    **At most one ``APPROVED`` per employee per period**, enforced by
    ``uq_pr_work_plans_approved`` rather than by a service everybody has to
    remember to consult - which matters because two administrators approving
    two drafts at the same moment is exactly the race a partial unique index
    settles and an application check does not.
    """

    __tablename__ = WORK_PLANS
    __table_args__ = (
        CheckConstraint("version_no > 0", name="version_positive"),
        # A plan cannot supersede itself. The one cycle a single row can create;
        # longer ones are a service rule, because no CHECK can see another row.
        CheckConstraint(
            "supersedes_plan_id IS NULL OR supersedes_plan_id <> id", name="supersedes_is_not_self"
        ),
        # Approval writes the pair together, and a superseded plan keeps both -
        # it *was* approved, and forgetting when would lose the answer the
        # version chain exists to give.
        CheckConstraint(
            "(status IN ('APPROVED', 'SUPERSEDED')) = (approved_at IS NOT NULL)",
            name="approved_at_matches_status",
        ),
        CheckConstraint(
            "(approved_at IS NULL) = (approved_by_user_id IS NULL)",
            name="approved_by_matches_approved_at",
        ),
        CheckConstraint(
            "(status = 'SUPERSEDED') = (superseded_at IS NOT NULL)",
            name="superseded_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'DISCARDED') = (discarded_at IS NOT NULL)",
            name="discarded_at_matches_status",
        ),
        # KPI self-service (0038): who submitted or returned a draft is tied
        # to when, the way the approver is tied to the approval.
        CheckConstraint(
            "(submitted_at IS NULL) = (submitted_by_user_id IS NULL)",
            name="submitted_by_matches_submitted_at",
        ),
        CheckConstraint(
            "(returned_at IS NULL) = (returned_by_user_id IS NULL)",
            name="returned_by_matches_returned_at",
        ),
        # The version chain, and what makes "v2" mean one thing.
        Index(
            "uq_pr_work_plans_user_period_version",
            "user_id",
            "period_id",
            "version_no",
            unique=True,
        ),
        # **One plan in force.** See ``APPROVED_PLAN``.
        Index(
            "uq_pr_work_plans_approved",
            "user_id",
            "period_id",
            unique=True,
            postgresql_where=APPROVED_PLAN,
            sqlite_where=APPROVED_PLAN,
        ),
        # **One revision in flight.** See ``DRAFT_PLAN``.
        Index(
            "uq_pr_work_plans_draft",
            "user_id",
            "period_id",
            unique=True,
            postgresql_where=DRAFT_PLAN,
            sqlite_where=DRAFT_PLAN,
        ),
        # "This person's plans for this period, newest first" - the employee
        # view and the administrator's revision history, both from the index.
        Index("ix_pr_work_plans_user_period_status", "user_id", "period_id", "status"),
        # "Every plan for this period" - the administrator's list.
        Index("ix_pr_work_plans_period_status", "period_id", "status"),
    )

    #: Whose plan. One employee - M2 models no team and no shared plan, because
    #: MeoBot models no team; see ``docs/pr/WORK_CORE_M1.md`` §9.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: **The existing reporting period**, reused rather than restated as a pair
    #: of dates. A plan is for ``2026-09``, and the period row is what says
    #: which days that is and whether it is still open.
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{REPORTING_PERIODS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: 1, 2, 3 … within one ``(user, period)``. Never reused, never renumbered.
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[PrWorkPlanStatus] = mapped_column(
        value_enum(PrWorkPlanStatus, name="pr_work_plan_status", length=20),
        nullable=False,
        default=PrWorkPlanStatus.DRAFT,
        server_default=PrWorkPlanStatus.DRAFT.value,
        index=True,
    )
    #: The approved version this one was cloned from and will replace. Null on a
    #: first version.
    supersedes_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{WORK_PLANS}.id", ondelete=RESTRICT), nullable=True
    )
    #: Why this version exists. Prose for a person - *"tăng hạn mức seeding sau
    #: khi mở thêm kênh"* - and the thing that makes a revision explainable a
    #: quarter later without reading two rows of numbers side by side.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: When this version came into force. **The only timestamp eligibility
    #: reads**, and kept when the version is superseded.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: **KPI self-service, migration 0038.** When the employee handed this
    #: draft to their manager. Set only on a ``DRAFT``; while set, the employee
    #: may not edit and a manager still may. Cleared by a return. Never set on
    #: an approved row: approval is what a submission asks for, and the audit
    #: trail records that it was asked.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: When a manager sent the draft back for revision, and by whom. Cleared by
    #: the next submission. Mutually exclusive with ``submitted_at`` by the
    #: service: a draft is either waiting on the manager or on the employee.
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: The manager's reason, in their words, for the employee to read on the
    #: returned draft. Prose; nothing parses it.
    return_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_submitted(self) -> bool:
        """Awaiting review: a draft the employee has handed over and may not touch."""
        return self.status is PrWorkPlanStatus.DRAFT and self.submitted_at is not None

    @property
    def is_returned(self) -> bool:
        """Sent back: a draft the employee may edit again, with the manager's note."""
        return (
            self.status is PrWorkPlanStatus.DRAFT
            and self.submitted_at is None
            and self.returned_at is not None
        )


class PrWorkQuota(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One work type's target and cap inside one plan version.

    ``target_value`` and ``eligibility_cap`` are **two different numbers**, and
    keeping them apart is the point of the table:

    ==================  ==================================================
    ``target_value``    What the plan asks for. 20 scripts.
    ``eligibility_cap`` How much may be eligible at all. 25.
    ==================  ==================================================

    So 21-25 is real work that stays eligible without being asked for, and 26+
    is over quota. Collapsing the two would force a choice between "extra work
    is worth nothing" and "there is no target", and the department wants
    neither.

    **Neither may be null.** M2 never reads an absent cap as unlimited - see
    :mod:`meobot.domain.pr.work_quota` on why absence is not permission.

    Rows are mutable **only while their plan is a ``DRAFT``**. That is a service
    rule rather than a constraint, because a CHECK cannot see the parent row;
    what the database does guarantee is that a plan version's quota set is
    unique per work type, so an approved plan can never contain two answers for
    one kind of work.
    """

    __tablename__ = WORK_QUOTAS
    __table_args__ = (
        CheckConstraint("target_value > 0", name="target_positive"),
        CheckConstraint("eligibility_cap > 0", name="cap_positive"),
        # The cap is never below the target: a plan that asked for more work
        # than it would call eligible is not a plan anybody could satisfy.
        CheckConstraint("eligibility_cap >= target_value", name="cap_at_least_target"),
        # An ITEM_COUNT quota counts contributions and has no unit; a QUANTITY
        # quota has to say what it counts. The service additionally checks the
        # unit against the work type's - a CHECK cannot reach another table.
        CheckConstraint("(basis = 'QUANTITY') = (unit IS NOT NULL)", name="unit_matches_basis"),
        # **One answer per kind of work.** What makes an ambiguous quota match
        # unrepresentable rather than merely refused by a validator.
        Index("uq_pr_work_quotas_plan_type", "plan_id", "work_type_id", unique=True),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_PLANS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: **Exactly one work type.** M2 has no all-types quota and no channel or
    #: campaign scope: "2000 of everything" has no unit, and a second scope
    #: dimension would make "which quota does this contribution match" a
    #: question with more than one answer. Adding one later is a new column and
    #: a wider unique index, not a rewrite of the evaluator.
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_TYPES}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    basis: Mapped[PrWorkQuotaBasis] = mapped_column(
        value_enum(PrWorkQuotaBasis, name="pr_work_quota_basis", length=20), nullable=False
    )
    #: What the plan asks for. ``Numeric(12, 2)`` like ``PrWorkItem.quantity``,
    #: so a target expressed in half-days is representable.
    target_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: How much may be eligible. At least ``target_value``.
    eligibility_cap: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: Required for ``QUANTITY`` and forbidden for ``ITEM_COUNT``. Validated
    #: against the work type's ``default_unit`` by the plan service, because the
    #: work type is the semantic authority and a quota in units the work is
    #: never recorded in would silently never fill.
    unit: Mapped[PrWorkUnit | None] = mapped_column(
        value_enum(PrWorkUnit, name="pr_work_unit", length=20), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PrWorkQuotaAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What an approved quota decided about one counted contribution.

    One row per counted contribution, materialised by
    :class:`~meobot.application.pr_work_quota_service.PrWorkQuotaEligibilityService`
    and rewritten whenever that service recomputes an **open** period. The
    unique index on ``work_contribution_id`` is what makes "run reconciliation
    twice" harmless: there is one current decision per contribution and a
    second run overwrites it rather than adding to it.

    The three amounts
    ------------------

    ``basis_amount = eligible_amount + over_quota_amount`` **whenever an
    approved quota decided the row**. That is the invariant that makes a report
    add up: every unit of quota-decided work is on exactly one side of the cap,
    and a screen can show *counted*, *eligible* and *over* as three figures that
    reconcile.

    Two statuses are exempt, and neither exemption is a loophole:

    ``NO_QUOTA`` splits ``basis_amount, 0, 0``. The work is real and its amount
    is recorded, and neither side of a cap is where it belongs, because there is
    no cap. Forcing the sum would mean calling it eligible - *"missing quota
    means unlimited"* - or calling it over quota - *"your work was rejected"*.
    Both are false, so a summary counts it as a third figure, ``no_quota_amount``.

    ``UNMEASURABLE`` has **no ``basis_amount`` at all**. That is the point of the
    status: the quota exists and the contribution's share of it cannot be stated,
    so there is no number to put on either side. Writing ``0`` would say the
    employee produced nothing, and they produced something nobody has recorded a
    quantity for. A summary therefore **counts** these rows and does not sum
    them - see ``unmeasurable_contributions``.

    ``NO_QUOTA`` may also have a null ``basis_amount``, for the same reason one
    step earlier: a quantity-measured work type with no quantity has no amount to
    record whether or not anybody set a cap for it.

    Provenance
    -----------

    ``work_plan_id`` and ``work_quota_id`` name **the version that decided
    this**, not the plan that happens to be current. Reading them is how
    somebody answers *"this was eligible yesterday and is over quota today"*:
    the allocation names v1, the plan chain shows v2 replaced it, and the audit
    row says who approved v2 and when. That is why the values are referenced
    rather than copied - a copied cap with no version behind it explains
    nothing.

    Both are null for ``NO_QUOTA``: there is no quota to name. ``UNMEASURABLE``
    **does** name one - a quota exists, that is precisely why the row is not
    ``NO_QUOTA`` - and it additionally carries ``reason_code`` saying what is
    missing, so the screen can name the field somebody has to fix.
    """

    __tablename__ = WORK_QUOTA_ALLOCATIONS
    __table_args__ = (
        CheckConstraint("basis_amount >= 0", name="basis_amount_not_negative"),
        CheckConstraint("eligible_amount >= 0", name="eligible_amount_not_negative"),
        CheckConstraint("over_quota_amount >= 0", name="over_quota_amount_not_negative"),
        # **A row holds a decision, and "nothing has decided yet" is not one.**
        # ``PENDING_EVALUATION`` describes the *absence* of a row, so a row
        # holding it would contradict itself - and would be indistinguishable
        # from a real decision to every reader that trusts the column.
        CheckConstraint(
            "quota_status IN ('NO_QUOTA', 'UNMEASURABLE', 'ELIGIBLE', "
            "'PARTIALLY_ELIGIBLE', 'OVER_QUOTA')",
            name="status_is_materialisable",
        ),
        # The invariant, said by the database, **for every row an approved quota
        # measured**: a row where the parts do not add up to the whole is a row a
        # report would quietly get wrong.
        #
        # Two statuses are exempt and neither is a loophole. ``NO_QUOTA`` splits
        # ``basis_amount, 0, 0`` - the work is real and *neither* side of a cap
        # is where it belongs, because there is no cap; forcing the sum would
        # mean calling it eligible ("missing quota means unlimited") or over
        # quota ("your work was rejected"), and both are false. ``UNMEASURABLE``
        # has no ``basis_amount`` at all, which is the whole point of it.
        CheckConstraint(
            "quota_status IN ('NO_QUOTA', 'UNMEASURABLE') "
            "OR basis_amount = eligible_amount + over_quota_amount",
            name="amounts_reconcile",
        ),
        # An amount is required for everything a quota actually measured, and
        # optional only where measurement was impossible. Without this, a
        # decided row could silently carry no number.
        CheckConstraint(
            "basis_amount IS NOT NULL OR quota_status IN ('NO_QUOTA', 'UNMEASURABLE')",
            name="decided_rows_carry_an_amount",
        ),
        # And a measured amount is **positive**. A contribution worth 0.00 is not
        # "eligible for nothing" - it is a measurement that failed, and it
        # belongs in ``UNMEASURABLE`` with ``INVALID_QUANTITY``. This also keeps
        # ``ELIGIBLE`` and ``OVER_QUOTA`` disjoint: at ``basis_amount = 0`` the
        # two rules below would both hold.
        CheckConstraint(
            "quota_status IN ('NO_QUOTA', 'UNMEASURABLE') OR basis_amount > 0",
            name="measured_amount_is_positive",
        ),
        # Each decided status *means* one split, and the database says which.
        # These three are what stop an ``ELIGIBLE`` row quietly carrying an
        # over-quota amount - the kind of row no screen would question and every
        # total would be wrong by.
        CheckConstraint(
            "quota_status <> 'ELIGIBLE' "
            "OR (eligible_amount = basis_amount AND over_quota_amount = 0)",
            name="eligible_is_wholly_inside",
        ),
        CheckConstraint(
            "quota_status <> 'OVER_QUOTA' "
            "OR (eligible_amount = 0 AND over_quota_amount = basis_amount)",
            name="over_quota_is_wholly_outside",
        ),
        CheckConstraint(
            "quota_status <> 'PARTIALLY_ELIGIBLE' "
            "OR (eligible_amount > 0 AND over_quota_amount > 0)",
            name="partial_is_on_both_sides",
        ),
        # NO_QUOTA names no quota; every other status names one - including
        # ``UNMEASURABLE``, whose whole distinction from ``NO_QUOTA`` is that a
        # quota exists. The plan is implied by the quota and carried beside it so
        # a summary need not join.
        CheckConstraint(
            "(quota_status = 'NO_QUOTA') = (work_quota_id IS NULL)",
            name="quota_matches_status",
        ),
        CheckConstraint(
            "(work_quota_id IS NULL) = (work_plan_id IS NULL)", name="plan_matches_quota"
        ),
        # **A reason belongs to exactly one status.** Required for
        # ``UNMEASURABLE``, so no row can say "cannot measure" without saying
        # what is missing; forbidden elsewhere, so a stale reason cannot survive
        # a recompute that resolved the problem.
        CheckConstraint(
            "(quota_status = 'UNMEASURABLE') = (reason_code IS NOT NULL)",
            name="reason_matches_status",
        ),
        # Nothing is eligible without a quota, and nothing is eligible that could
        # not be measured. The first is the anti-gaming rule this milestone
        # exists for - "missing quota means unlimited" made impossible in the
        # schema - and the second is its twin: a contribution nobody could
        # measure must not be eligible for an amount nobody computed.
        CheckConstraint(
            "quota_status NOT IN ('NO_QUOTA', 'UNMEASURABLE') "
            "OR (eligible_amount = 0 AND over_quota_amount = 0)",
            name="no_quota_is_never_eligible",
        ),
        # **One current decision per contribution.** What makes a repeated
        # reconcile idempotent rather than additive.
        Index("uq_pr_work_quota_allocations_contribution", "work_contribution_id", unique=True),
        # **The KPI summary index.** "This person's eligibility in this period,
        # by work type" - the employee's plan screen and the administrator's
        # per-person view, both answered without touching the contributions
        # table.
        Index(
            "ix_pr_work_quota_allocations_user_period_type",
            "user_id",
            "reporting_period_id",
            "work_type_id",
        ),
        # "Everything this period decided, by status" - the reconciliation
        # report and the department-wide count.
        Index("ix_pr_work_quota_allocations_period_status", "reporting_period_id", "quota_status"),
        # "What did this plan version decide" - the provenance question, from
        # the index rather than from a scan.
        Index("ix_pr_work_quota_allocations_plan", "work_plan_id"),
    )

    work_contribution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_CONTRIBUTIONS}.id", ondelete=RESTRICT), nullable=False
    )
    #: The period this decision belongs to, taken from the contribution's
    #: ``counted_at``. **Not** the period it was assigned or completed in - see
    #: ``docs/pr/WORK_CORE_M1.md`` §10.
    reporting_period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{REPORTING_PERIODS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: Whose work. Denormalised from the contribution, and the reason is the
    #: summary index above: *"this person, this period, this type"* is the
    #: question every KPI screen asks, and answering it through a join to
    #: ``pr_work_contributions`` would make the cheapest read in the module the
    #: one with the widest lock footprint.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: Which kind of work. Denormalised from the work item for the same reason,
    #: and additionally because it is the grain a quota matches on.
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_TYPES}.id", ondelete=RESTRICT), nullable=False
    )
    work_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{WORK_PLANS}.id", ondelete=RESTRICT), nullable=True
    )
    work_quota_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{WORK_QUOTAS}.id", ondelete=RESTRICT), nullable=True
    )
    quota_status: Mapped[PrWorkQuotaStatus] = mapped_column(
        value_enum(PrWorkQuotaStatus, name="pr_work_quota_status", length=20),
        nullable=False,
        index=True,
    )
    #: How the amounts below are measured. Taken from the quota when there is
    #: one and from the **work type** when there is not, which is what lets a
    #: ``NO_QUOTA`` row still say "3.150 bình luận" rather than "30 rows".
    basis: Mapped[PrWorkQuotaBasis] = mapped_column(
        value_enum(PrWorkQuotaBasis, name="pr_work_quota_basis", length=20), nullable=False
    )
    #: The unit the amounts are in, for ``QUANTITY``. Null for ``ITEM_COUNT``,
    #: whose amounts are counts of contributions.
    unit: Mapped[PrWorkUnit | None] = mapped_column(
        value_enum(PrWorkUnit, name="pr_work_unit", length=20), nullable=True
    )
    #: What this contribution is worth: ``1`` for ``ITEM_COUNT``,
    #: ``quantity * credit_weight`` for ``QUANTITY``.
    #:
    #: **Null when the contribution could not be measured** - always for
    #: ``UNMEASURABLE``, and for a ``NO_QUOTA`` row whose quantity-measured work
    #: type has no quantity on the item. Null rather than zero, because zero is a
    #: measurement and this is the absence of one: a report that summed it would
    #: say the employee produced nothing.
    basis_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: How much of it fitted inside the cap. Zero for ``NO_QUOTA`` and
    #: ``OVER_QUOTA``.
    eligible_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    over_quota_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: **Why the contribution could not be measured.** Required for
    #: ``UNMEASURABLE`` and null for every other status.
    #:
    #: A stable machine code, never an exception message: an error string is an
    #: implementation detail that changes when somebody rewords a docstring, and
    #: storing one as business state would put a stack trace on an employee's KPI
    #: screen. The Vietnamese sentence is composed from the code at the API
    #: boundary - see
    #: :func:`~meobot.domain.pr.work_quota_labels.work_unmeasurable_reason_label`.
    reason_code: Mapped[PrWorkUnmeasurableReason | None] = mapped_column(
        value_enum(PrWorkUnmeasurableReason, name="pr_work_unmeasurable_reason", length=30),
        nullable=True,
    )
    #: When the projection last decided this. Distinct from ``updated_at``,
    #: which moves whenever the row is written even if nothing about the
    #: decision changed.
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__: list[str] = [
    "WORK_PLANS",
    "WORK_QUOTAS",
    "WORK_QUOTA_ALLOCATIONS",
    "PrWorkPlan",
    "PrWorkQuota",
    "PrWorkQuotaAllocation",
]
