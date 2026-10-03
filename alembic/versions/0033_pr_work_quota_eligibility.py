"""M2: quota eligibility - the plan, its quotas, and what they decided.

Three new tables and **one nullable column with a server default**. No existing
row is read or rewritten, no M1 column changes meaning, and there is **no
backfill of any kind** - not from the spreadsheet, not from ``pr_content_items``
and not from ``pr_tasks``. That is the whole risk profile of M2, and it is
deliberate: the quota engine is additive so that a mistake in it cannot reach
the ledger the department's KPI already depends on.

What the three tables are for
------------------------------

* ``pr_work_plans`` - **one employee's approved KPI plan for one reporting
  period, at one version.** Versioned rather than edited, because a quota
  decides how much of somebody's work counts and whoever raised a cap in the
  middle of a month has to stay visible afterwards;
* ``pr_work_quotas`` - **one work type's target and cap inside one plan
  version**. Two numbers, not one: ``target_value`` is what the plan asks for
  and ``eligibility_cap`` is how much may be eligible at all;
* ``pr_work_quota_allocations`` - **what an approved quota decided about one
  counted contribution**, with the amounts split into what fitted inside the
  cap and what did not - or ``UNMEASURABLE`` and a reason code when the
  contribution cannot be measured against the quota at all.

Why the decision is a table and not a column on ``pr_work_contributions``
-------------------------------------------------------------------------

M1's handover recommended ``quota_status`` on the contribution. This revision
does not do that, for two reasons that no column could satisfy:

* **partial eligibility is three numbers.** A ``QUANTITY`` contribution that
  straddles the cap is 40 comments eligible and 20 over, and an enum cannot say
  that. Four quota columns on ``pr_work_contributions`` would put the quota
  engine on the table M1 built to mean *"is this valid completed work"*, which
  is exactly the collapsing of two facts the module exists to prevent;
* **a decision has a version behind it.** The allocation names the plan version
  and the quota row that produced it, so *"why was this eligible yesterday and
  over quota today"* is answerable from the row. A column rewritten in place has
  nowhere to keep that.

The cost - a join - buys back something better: a counted contribution with **no
allocation row at all** is a legible state rather than a null, and it is the
state every contribution counted before this revision is in. It reads as
``NO_QUOTA``: no approved quota has claimed it. See ``docs/pr/WORK_QUOTA_M2.md``
§ "Pre-existing M1 data".

The one column on an M1 table
------------------------------

``pr_work_types.default_quota_basis``, ``NOT NULL`` with a server default of
``'ITEM_COUNT'``. On the *type* because how a kind of work is measured is a fact
about the work rather than a per-employee negotiation - two plans measuring
"seeding comments" differently, one by rows and one by comments, would make a
department-wide figure meaningless and hand whoever chose ``ITEM_COUNT`` a
hundredfold advantage. Existing types get ``ITEM_COUNT``, which is what M1's
figures already implied: nothing was measured by quantity, because nothing was
measured against a quota at all. A type that should be ``QUANTITY`` is
reconfigured afterwards through the existing work-type endpoint, which is a
decision somebody takes rather than a guess this revision makes from
``default_unit``.

The constraints that carry rules rather than tidiness
------------------------------------------------------

``uq_pr_work_plans_approved`` is **partial**, over ``status = 'APPROVED'``. At
most one plan is in force per employee per period, and it has to be the database
that says so: two administrators approving two drafts in the same instant is
precisely the race an application check loses and a partial unique index wins.
``uq_pr_work_plans_draft`` is the same shape for the one revision in flight.

``ck_pr_work_quota_allocations_amounts_reconcile`` refuses a row where
``basis_amount <> eligible_amount + over_quota_amount``. Every unit of
quota-decided work is on exactly one side of the cap, and a row where the parts
do not add up to the whole is a row a report would quietly get wrong.
``NO_QUOTA`` is exempt - its split is ``basis_amount, 0, 0``, because there is
no cap for the work to be inside or outside of.

``ck_pr_work_quota_allocations_no_quota_is_never_eligible`` is the
anti-gaming constraint. **Missing quota must never be read as unlimited
eligibility**, and this is the database saying so independently of the
evaluator: a ``NO_QUOTA`` row with a non-zero ``eligible_amount`` cannot exist.
The same constraint covers ``UNMEASURABLE``, which is its twin - a contribution
nobody could measure must not be eligible for an amount nobody computed.

``ck_pr_work_quota_allocations_status_is_materialisable`` refuses
``PENDING_EVALUATION``. That value exists in ``PrWorkQuotaStatus`` so a *read*
can say "an approved quota covers this and nothing has evaluated it yet", and it
describes the **absence** of a row - so a row holding it would contradict itself.

``ck_pr_work_quota_allocations_reason_matches_status`` ties ``reason_code`` to
``UNMEASURABLE`` in both directions: no row may say *"cannot measure"* without
saying what is missing, and a stale reason cannot survive a recompute that
resolved the problem.

Why ``UNMEASURABLE`` exists at all
-----------------------------------

Before it, a counted contribution with no allocation row was read back as
``NO_QUOTA``, and that conflated three different things: *no approved quota
covers this*, *a quota covers it and nothing has evaluated it yet*, and *a quota
covers it and the work item has no quantity to measure*. Only the first is
``NO_QUOTA`` - a business claim that **nobody set a target** - and telling an
employee that when their manager had set one, and the real problem was a blank
field on a work item, sends them to the wrong person.

So ``UNMEASURABLE`` is a materialised row with a quota, a reason and no amount;
``PENDING_EVALUATION`` is read-only and never stored; and ``NO_QUOTA`` keeps the
one meaning it always should have had.

``uq_pr_work_quota_allocations_contribution`` is what makes running
reconciliation twice harmless - one current decision per contribution, and a
second run overwrites rather than adds.

No scoring, anywhere
---------------------

There is no ``base_score``, no ``awarded_score``, no ``points``, no
``quality_multiplier`` and no ``bonus`` in this revision. M2 decides
*eligibility against an approved quota* and awards nothing. M6 owns points, and
a column for one here would be an unapproved rate sitting in the schema.

Downgrade
---------

Drops the three tables in dependency order and the one column. It loses the
quota engine, which exists nowhere else - and it cannot corrupt anything,
because nothing outside these three tables references them and the dropped
column has no reader in M1.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_TYPES = "pr_work_types"
WORK_CONTRIBUTIONS = "pr_work_contributions"
WORK_PLANS = "pr_work_plans"
WORK_QUOTAS = "pr_work_quotas"
WORK_QUOTA_ALLOCATIONS = "pr_work_quota_allocations"
REPORTING_PERIODS = "pr_reporting_periods"

USERS = "users"
RESTRICT = "RESTRICT"

#: Enum columns are ``VARCHAR`` + CHECK, never a PostgreSQL ``ENUM`` type - the
#: repository-wide rule, so adding a status is a code change rather than an
#: ``ALTER TYPE`` migration and the values stay greppable in the database.
BASIS_VALUES = ("ITEM_COUNT", "QUANTITY")
PLAN_STATUS_VALUES = ("DRAFT", "APPROVED", "SUPERSEDED", "DISCARDED")
#: The statuses an allocation row may hold.
#:
#: ``PENDING_EVALUATION`` is deliberately **absent**. It exists in
#: ``PrWorkQuotaStatus`` because a *read* has to be able to say "an approved
#: quota covers this and nothing has evaluated it yet", and it describes the
#: **absence** of a row - so a row holding it would contradict itself. The CHECK
#: below is the database refusing to store it.
QUOTA_STATUS_VALUES = (
    "NO_QUOTA",
    "UNMEASURABLE",
    "ELIGIBLE",
    "PARTIALLY_ELIGIBLE",
    "OVER_QUOTA",
)
UNMEASURABLE_REASON_VALUES = ("MISSING_QUANTITY", "INVALID_QUANTITY", "UNIT_MISMATCH")
UNIT_VALUES = (
    "ITEM",
    "VIDEO",
    "POST",
    "ARTICLE",
    "COMMENT",
    "MESSAGE",
    "SESSION",
    "HOUR",
    "DAY",
)


def _basis(name: str) -> sa.Enum:
    """A fresh ``PrWorkQuotaBasis`` enum object.

    One per column rather than a shared instance: two columns in one revision
    sharing an ``sa.Enum`` makes SQLAlchemy try to emit the same CHECK
    constraint name twice on some backends. The same reason 0032's ``_unit``
    exists.
    """
    return sa.Enum(*BASIS_VALUES, name=name, native_enum=False, length=20)


def _unit(name: str) -> sa.Enum:
    """A fresh ``PrWorkUnit`` enum object. See :func:`_basis`."""
    return sa.Enum(*UNIT_VALUES, name=name, native_enum=False, length=20)


def upgrade() -> None:
    # --- The one column on an M1 table ------------------------------------
    #
    # ``NOT NULL`` with a server default, so existing rows are filled by the
    # default rather than by an ``UPDATE`` this revision writes: PostgreSQL 11+
    # records the default in the catalog and rewrites no heap. Nothing in M1
    # reads the column, so no M1 figure moves because it appeared.
    op.add_column(
        WORK_TYPES,
        sa.Column(
            "default_quota_basis",
            _basis("pr_work_type_quota_basis"),
            nullable=False,
            server_default="ITEM_COUNT",
        ),
    )

    # --- One employee's plan for one period, at one version ---------------
    op.create_table(
        WORK_PLANS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*PLAN_STATUS_VALUES, name="pr_work_plan_status", native_enum=False, length=20),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("supersedes_plan_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no > 0", name=op.f("ck_pr_work_plans_version_positive")),
        sa.CheckConstraint(
            "supersedes_plan_id IS NULL OR supersedes_plan_id <> id",
            name=op.f("ck_pr_work_plans_supersedes_is_not_self"),
        ),
        # A superseded plan *was* approved and keeps saying when: forgetting it
        # would lose the answer the version chain exists to give.
        sa.CheckConstraint(
            "(status IN ('APPROVED', 'SUPERSEDED')) = (approved_at IS NOT NULL)",
            name=op.f("ck_pr_work_plans_approved_at_matches_status"),
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL) = (approved_by_user_id IS NULL)",
            name=op.f("ck_pr_work_plans_approved_by_matches_approved_at"),
        ),
        sa.CheckConstraint(
            "(status = 'SUPERSEDED') = (superseded_at IS NOT NULL)",
            name=op.f("ck_pr_work_plans_superseded_at_matches_status"),
        ),
        sa.CheckConstraint(
            "(status = 'DISCARDED') = (discarded_at IS NOT NULL)",
            name=op.f("ck_pr_work_plans_discarded_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_plans_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            [f"{REPORTING_PERIODS}.id"],
            name=op.f("fk_pr_work_plans_period_id_pr_reporting_periods"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_plan_id"],
            [f"{WORK_PLANS}.id"],
            name=op.f("fk_pr_work_plans_supersedes_plan_id_pr_work_plans"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_plans_created_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_plans_approved_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["discarded_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_plans_discarded_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_plans")),
    )
    op.create_index(op.f("ix_pr_work_plans_user_id"), WORK_PLANS, ["user_id"])
    op.create_index(op.f("ix_pr_work_plans_period_id"), WORK_PLANS, ["period_id"])
    op.create_index(op.f("ix_pr_work_plans_status"), WORK_PLANS, ["status"])
    op.create_index(
        "uq_pr_work_plans_user_period_version",
        WORK_PLANS,
        ["user_id", "period_id", "version_no"],
        unique=True,
    )
    # **One plan in force**, and **one revision in flight**. Partial, because
    # superseded versions are kept for ever and a draft coexists with the
    # approved plan it will replace.
    op.create_index(
        "uq_pr_work_plans_approved",
        WORK_PLANS,
        ["user_id", "period_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
        sqlite_where=sa.text("status = 'APPROVED'"),
    )
    op.create_index(
        "uq_pr_work_plans_draft",
        WORK_PLANS,
        ["user_id", "period_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
        sqlite_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        "ix_pr_work_plans_user_period_status", WORK_PLANS, ["user_id", "period_id", "status"]
    )
    op.create_index("ix_pr_work_plans_period_status", WORK_PLANS, ["period_id", "status"])

    # --- One work type's target and cap inside one plan version -----------
    op.create_table(
        WORK_QUOTAS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("basis", _basis("pr_work_quota_basis"), nullable=False),
        sa.Column("target_value", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("eligibility_cap", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("unit", _unit("pr_work_quota_unit"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("target_value > 0", name=op.f("ck_pr_work_quotas_target_positive")),
        sa.CheckConstraint("eligibility_cap > 0", name=op.f("ck_pr_work_quotas_cap_positive")),
        # A cap below the target would ask for more work than it would call
        # eligible - not a plan anybody could satisfy.
        sa.CheckConstraint(
            "eligibility_cap >= target_value", name=op.f("ck_pr_work_quotas_cap_at_least_target")
        ),
        # ITEM_COUNT counts contributions and has no unit; QUANTITY has to say
        # what it counts. Which unit is legal is the service's check, because a
        # CHECK cannot reach ``pr_work_types``.
        sa.CheckConstraint(
            "(basis = 'QUANTITY') = (unit IS NOT NULL)",
            name=op.f("ck_pr_work_quotas_unit_matches_basis"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            [f"{WORK_PLANS}.id"],
            name=op.f("fk_pr_work_quotas_plan_id_pr_work_plans"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_work_quotas_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_quotas")),
    )
    op.create_index(op.f("ix_pr_work_quotas_plan_id"), WORK_QUOTAS, ["plan_id"])
    op.create_index(op.f("ix_pr_work_quotas_work_type_id"), WORK_QUOTAS, ["work_type_id"])
    # **One answer per kind of work.** An ambiguous quota match is
    # unrepresentable rather than merely refused by a validator.
    op.create_index(
        "uq_pr_work_quotas_plan_type", WORK_QUOTAS, ["plan_id", "work_type_id"], unique=True
    )

    # --- What an approved quota decided about one counted contribution ----
    op.create_table(
        WORK_QUOTA_ALLOCATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_contribution_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("work_plan_id", sa.Uuid(), nullable=True),
        sa.Column("work_quota_id", sa.Uuid(), nullable=True),
        sa.Column(
            "quota_status",
            sa.Enum(
                *QUOTA_STATUS_VALUES, name="pr_work_quota_status", native_enum=False, length=20
            ),
            nullable=False,
        ),
        sa.Column("basis", _basis("pr_work_allocation_basis"), nullable=False),
        sa.Column("unit", _unit("pr_work_allocation_unit"), nullable=True),
        # **Nullable.** Null when the contribution could not be measured - always
        # for ``UNMEASURABLE``, and for a ``NO_QUOTA`` row whose
        # quantity-measured work type has no quantity on the item. Null rather
        # than zero, because zero is a measurement and this is the absence of
        # one: a report that summed it would say the employee produced nothing.
        sa.Column("basis_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("eligible_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("over_quota_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        # Why the contribution could not be measured. Required for
        # ``UNMEASURABLE`` and null everywhere else. A stable machine code, never
        # an exception message - see the model.
        sa.Column(
            "reason_code",
            sa.Enum(
                *UNMEASURABLE_REASON_VALUES,
                name="pr_work_unmeasurable_reason",
                native_enum=False,
                length=30,
            ),
            nullable=True,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "basis_amount >= 0", name=op.f("ck_pr_work_quota_allocations_basis_amount_not_negative")
        ),
        sa.CheckConstraint(
            "eligible_amount >= 0",
            name=op.f("ck_pr_work_quota_allocations_eligible_amount_not_negative"),
        ),
        sa.CheckConstraint(
            "over_quota_amount >= 0",
            name=op.f("ck_pr_work_quota_allocations_over_quota_amount_not_negative"),
        ),
        # **A row holds a decision, and "nothing has decided yet" is not one.**
        # ``PENDING_EVALUATION`` describes the absence of a row, so a row holding
        # it would contradict itself - and would be indistinguishable from a real
        # decision to every reader that trusts the column.
        sa.CheckConstraint(
            "quota_status IN ('NO_QUOTA', 'UNMEASURABLE', 'ELIGIBLE', "
            "'PARTIALLY_ELIGIBLE', 'OVER_QUOTA')",
            name=op.f("ck_pr_work_quota_allocations_status_is_materialisable"),
        ),
        # Every unit of quota-measured work is on exactly one side of the cap.
        # Two statuses are exempt and neither is a loophole: ``NO_QUOTA`` splits
        # ``basis_amount, 0, 0``, because the work is real and there is no cap
        # for it to be inside or outside of - forcing the sum would mean calling
        # it eligible ("missing quota means unlimited") or over quota ("your work
        # was rejected"), and both are false. ``UNMEASURABLE`` has no
        # ``basis_amount`` at all, which is the whole point of it.
        sa.CheckConstraint(
            "quota_status IN ('NO_QUOTA', 'UNMEASURABLE') "
            "OR basis_amount = eligible_amount + over_quota_amount",
            name=op.f("ck_pr_work_quota_allocations_amounts_reconcile"),
        ),
        # An amount is required for everything a quota measured, and optional
        # only where measurement was impossible.
        sa.CheckConstraint(
            "basis_amount IS NOT NULL OR quota_status IN ('NO_QUOTA', 'UNMEASURABLE')",
            name=op.f("ck_pr_work_quota_allocations_decided_rows_carry_an_amount"),
        ),
        # And a measured amount is positive. A contribution worth 0.00 is a
        # measurement that failed, not "eligible for nothing" - it belongs in
        # ``UNMEASURABLE`` with ``INVALID_QUANTITY``. This also keeps ``ELIGIBLE``
        # and ``OVER_QUOTA`` disjoint: at zero the two rules below both hold.
        sa.CheckConstraint(
            "quota_status IN ('NO_QUOTA', 'UNMEASURABLE') OR basis_amount > 0",
            name=op.f("ck_pr_work_quota_allocations_measured_amount_is_positive"),
        ),
        # Each decided status *means* one split, and the database says which.
        # What stops an ``ELIGIBLE`` row quietly carrying an over-quota amount -
        # a row no screen would question and every total would be wrong by.
        sa.CheckConstraint(
            "quota_status <> 'ELIGIBLE' "
            "OR (eligible_amount = basis_amount AND over_quota_amount = 0)",
            name=op.f("ck_pr_work_quota_allocations_eligible_is_wholly_inside"),
        ),
        sa.CheckConstraint(
            "quota_status <> 'OVER_QUOTA' "
            "OR (eligible_amount = 0 AND over_quota_amount = basis_amount)",
            name=op.f("ck_pr_work_quota_allocations_over_quota_is_wholly_outside"),
        ),
        sa.CheckConstraint(
            "quota_status <> 'PARTIALLY_ELIGIBLE' "
            "OR (eligible_amount > 0 AND over_quota_amount > 0)",
            name=op.f("ck_pr_work_quota_allocations_partial_is_on_both_sides"),
        ),
        # NO_QUOTA names no quota; every other status names one - including
        # ``UNMEASURABLE``, whose whole distinction from ``NO_QUOTA`` is that a
        # quota exists.
        sa.CheckConstraint(
            "(quota_status = 'NO_QUOTA') = (work_quota_id IS NULL)",
            name=op.f("ck_pr_work_quota_allocations_quota_matches_status"),
        ),
        sa.CheckConstraint(
            "(work_quota_id IS NULL) = (work_plan_id IS NULL)",
            name=op.f("ck_pr_work_quota_allocations_plan_matches_quota"),
        ),
        # **A reason belongs to exactly one status.** Required for
        # ``UNMEASURABLE``, so no row can say "cannot measure" without saying
        # what is missing; forbidden elsewhere, so a stale reason cannot survive
        # a recompute that resolved the problem.
        sa.CheckConstraint(
            "(quota_status = 'UNMEASURABLE') = (reason_code IS NOT NULL)",
            name=op.f("ck_pr_work_quota_allocations_reason_matches_status"),
        ),
        # **The anti-gaming constraint**, and its twin. Missing quota is never
        # unlimited eligibility, said by the database rather than only by the
        # evaluator - and a contribution nobody could measure is never eligible
        # for an amount nobody computed.
        sa.CheckConstraint(
            "quota_status NOT IN ('NO_QUOTA', 'UNMEASURABLE') "
            "OR (eligible_amount = 0 AND over_quota_amount = 0)",
            name=op.f("ck_pr_work_quota_allocations_no_quota_is_never_eligible"),
        ),
        sa.ForeignKeyConstraint(
            ["work_contribution_id"],
            [f"{WORK_CONTRIBUTIONS}.id"],
            name=op.f(
                "fk_pr_work_quota_allocations_work_contribution_id_pr_work_contributions"
            ),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id"],
            [f"{REPORTING_PERIODS}.id"],
            name=op.f("fk_pr_work_quota_allocations_reporting_period_id_pr_reporting_periods"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_quota_allocations_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_work_quota_allocations_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["work_plan_id"],
            [f"{WORK_PLANS}.id"],
            name=op.f("fk_pr_work_quota_allocations_work_plan_id_pr_work_plans"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["work_quota_id"],
            [f"{WORK_QUOTAS}.id"],
            name=op.f("fk_pr_work_quota_allocations_work_quota_id_pr_work_quotas"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_quota_allocations")),
    )
    op.create_index(
        op.f("ix_pr_work_quota_allocations_reporting_period_id"),
        WORK_QUOTA_ALLOCATIONS,
        ["reporting_period_id"],
    )
    op.create_index(
        op.f("ix_pr_work_quota_allocations_quota_status"),
        WORK_QUOTA_ALLOCATIONS,
        ["quota_status"],
    )
    # **One current decision per contribution.** What makes a repeated
    # reconcile idempotent rather than additive.
    op.create_index(
        "uq_pr_work_quota_allocations_contribution",
        WORK_QUOTA_ALLOCATIONS,
        ["work_contribution_id"],
        unique=True,
    )
    # **The KPI summary index.** "This person's eligibility in this period, by
    # work type" - every KPI screen's question, answered without touching
    # ``pr_work_contributions``.
    op.create_index(
        "ix_pr_work_quota_allocations_user_period_type",
        WORK_QUOTA_ALLOCATIONS,
        ["user_id", "reporting_period_id", "work_type_id"],
    )
    op.create_index(
        "ix_pr_work_quota_allocations_period_status",
        WORK_QUOTA_ALLOCATIONS,
        ["reporting_period_id", "quota_status"],
    )
    op.create_index(
        "ix_pr_work_quota_allocations_plan", WORK_QUOTA_ALLOCATIONS, ["work_plan_id"]
    )


def downgrade() -> None:
    """Drop the three tables, newest dependency first, then the one column.

    Nothing else is touched. ``pr_work_contributions``, ``pr_work_items`` and
    every reporting period come through unchanged - no ``counted_at`` moves and
    no ``count_status`` changes, because this revision never wrote one.
    """
    op.drop_table(WORK_QUOTA_ALLOCATIONS)
    op.drop_table(WORK_QUOTAS)
    op.drop_table(WORK_PLANS)
    op.drop_column(WORK_TYPES, "default_quota_basis")
