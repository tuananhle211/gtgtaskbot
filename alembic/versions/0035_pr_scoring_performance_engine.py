"""M6: scoring, monthly manager review and the performance index.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-03

Six tables, all new, and **nothing existing is touched**. M1's ledger, M2's
allocations and M3's projections are read and never written: M6 is a consumer of
their conclusions, and a migration that altered any of them would be M6 deciding
something those milestones own.

Why eight rather than one big "performance" table
--------------------------------------------------

Each of them is a different thing with a different lifetime, and a wide table
would force them to share one:

* ``pr_work_scoring_rules`` - *what a kind of work is worth in standard minutes*.
  Versioned and effective-dated, because a rate that decides pay must be
  approved, must stop being editable, and must keep scoring last September at
  last September's rate;
* ``pr_performance_policies`` - *the weights, caps, barems, gate and bands*.
  Versioned for the same reason;
* ``pr_performance_reviews`` - **one row per person per month**, carrying the
  three manager judgements. Not one per work item: twenty people and a hundred
  deliverables each is two thousand forms nobody fills in;
* ``pr_performance_target_overrides`` - the one per-person, per-month number an
  owner may set: a workload target the calendar could not produce, with the
  mandatory reason that makes it defensible;
* ``pr_work_score_allocations`` - one row per scored contribution, carrying the
  rule version that priced it. The provenance that makes a figure explainable
  three months later;
* ``pr_performance_results`` - the materialised monthly figure with every input
  it was computed from.

**No money, anywhere.** M6 scores and reports performance; the department head
allocates performance pay as a separate management decision outside MeoChat.
There is no coefficient column, no amount column and no allocation table, and
that is a product decision rather than an omission: a performance index is an
evaluation result, and a schema that stored it beside a multiplier would make it
read as a promise about somebody's pay.

No backfill, anywhere
----------------------

Not one historical score is computed here. Scoring needs a policy and a set of
rules that no deployment has yet, and inventing either in a migration would
write numbers nobody approved into months people have already been assessed on.

Downgrade
---------

Drops the six tables in dependency order and nothing else. It loses the
performance history, which exists nowhere else - and it cannot corrupt work,
quota or content, because nothing outside these six tables references them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCORING_RULES = "pr_work_scoring_rules"
POLICIES = "pr_performance_policies"
REVIEWS = "pr_performance_reviews"
TARGET_OVERRIDES = "pr_performance_target_overrides"
SCORE_ALLOCATIONS = "pr_work_score_allocations"
RESULTS = "pr_performance_results"

WORK_TYPES = "pr_work_types"
WORK_CONTRIBUTIONS = "pr_work_contributions"
PERIODS = "pr_reporting_periods"
USERS = "users"
RESTRICT = "RESTRICT"
CASCADE = "CASCADE"

#: **Every column naming a person is ``RESTRICT``.** The repository-wide PR rule,
#: asserted by ``test_pr_core_migrations.py``, and M6 is exactly the module it
#: exists for: ``approved_by_user_id`` on a rate and ``reviewer_user_id`` on a
#: month are accountability columns. ``SET NULL`` would let deleting an account
#: quietly erase who approved somebody's pay rate, leaving a figure nobody signed.
#: The only nullable FK here that is *not* a person is ``supersedes_rule_id``.

#: Enum columns are ``VARCHAR`` + CHECK, never a PostgreSQL ``ENUM`` - the
#: repository-wide rule, so adding a level is a code change rather than an
#: ``ALTER TYPE`` and the values stay greppable in the database.
SCORING_MODE_VALUES = ("STANDARD_MINUTES", "EXCLUDED_FROM_PERFORMANCE")
RULE_STATUS_VALUES = ("DRAFT", "APPROVED", "SUPERSEDED")
LEVEL_VALUES = (
    "EXCELLENT",
    "GOOD",
    "MEETS_EXPECTATIONS",
    "BELOW_EXPECTATIONS",
    "POOR",
)
SCORE_STATUS_VALUES = ("SCORED", "NO_SCORING_RULE", "EXCLUDED_FROM_PERFORMANCE")
CALCULATION_STATUS_VALUES = (
    "READY",
    "TARGET_UNRESOLVED",
    "NO_SCORING_RULE",
    "PERFORMANCE_REVIEW_PENDING",
    "FINALIZED",
)

#: Minutes per unit needs four places: seeding is 0.9 minutes a comment, and
#: rounding that to 0.90 is fine while rounding it to 1 would be a 10% raise.
MINUTES_PER_UNIT = sa.Numeric(10, 4)
#: Totals of the above over a month. Twelve digits is far beyond a person-month.
MINUTES_TOTAL = sa.Numeric(14, 2)
#: Scores and indices are percentages with two places.
SCORE = sa.Numeric(7, 2)
#: Mirrors ``meobot.db.base.JSONColumn`` exactly - ``JSONB`` on PostgreSQL, plain
#: ``JSON`` elsewhere. Spelled out here rather than imported because a migration
#: must not depend on application code that may have moved on, and written to
#: match because a plain ``sa.JSON()`` would leave the models permanently one
#: ``modify_type`` away from the schema they describe.
JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    # --- what a kind of work is worth ------------------------------------
    op.create_table(
        SCORING_RULES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                *SCORING_MODE_VALUES, name="pr_work_scoring_mode", native_enum=False, length=30
            ),
            nullable=False,
        ),
        # Null exactly when the mode is EXCLUDED_FROM_PERFORMANCE - the CHECK
        # below says so, because "excluded" and "worth nothing per unit" are
        # different sentences and a nullable number is how they stay different.
        sa.Column("standard_minutes_per_unit", MINUTES_PER_UNIT, nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *RULE_STATUS_VALUES, name="pr_scoring_rule_status", native_enum=False, length=20
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_rule_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_scoring_rules")),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_work_scoring_rules_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_scoring_rules_created_by"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_scoring_rules_approved_by"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_rule_id"],
            [f"{SCORING_RULES}.id"],
            name=op.f("fk_pr_work_scoring_rules_supersedes"),
            # ``RESTRICT`` like every other PR foreign key - the module admits no
            # ``SET NULL`` at all. A superseded rate is immutable history that
            # nothing deletes, and blanking the link would lose the chain a
            # figure's provenance is followed along.
            ondelete=RESTRICT,
        ),
        sa.UniqueConstraint(
            "work_type_id", "version_no", name=op.f("uq_pr_work_scoring_rules_version")
        ),
        # The pairing that keeps "excluded" honest.
        sa.CheckConstraint(
            "(mode = 'EXCLUDED_FROM_PERFORMANCE' AND standard_minutes_per_unit IS NULL) "
            "OR (mode = 'STANDARD_MINUTES' AND standard_minutes_per_unit IS NOT NULL "
            "    AND standard_minutes_per_unit >= 0)",
            name=op.f("ck_pr_work_scoring_rules_minutes_match_mode"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_pr_work_scoring_rules_range_ordered"),
        ),
    )
    op.create_index(
        "ix_pr_work_scoring_rules_type_status", SCORING_RULES, ["work_type_id", "status"]
    )
    op.create_index(
        "ix_pr_work_scoring_rules_effective", SCORING_RULES, ["work_type_id", "effective_from"]
    )

    # --- the weights, barems, gate and bands ------------------------------
    op.create_table(
        POLICIES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("daily_target_minutes", sa.Integer(), nullable=False),
        sa.Column("workload_weight", SCORE, nullable=False),
        sa.Column("quality_weight", SCORE, nullable=False),
        sa.Column("timeliness_weight", SCORE, nullable=False),
        sa.Column("business_contribution_weight", SCORE, nullable=False),
        sa.Column("workload_score_cap", SCORE, nullable=False),
        # The barems, gate and bands travel as JSON because they are *tables of
        # numbers the business edits*, not schema. A column per level would make
        # adding a rung a migration, and the rungs are exactly what a department
        # argues about.
        sa.Column("quality_scores", JSON_COLUMN, nullable=False),
        sa.Column("timeliness_scores", JSON_COLUMN, nullable=False),
        sa.Column("business_contribution_scores", JSON_COLUMN, nullable=False),
        sa.Column("quality_gate", JSON_COLUMN, nullable=False),
        sa.Column("performance_bands", JSON_COLUMN, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *RULE_STATUS_VALUES,
                name="pr_performance_policy_status",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_performance_policies")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_policies_created_by"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_policies_approved_by"),
            ondelete=RESTRICT,
        ),
        sa.UniqueConstraint("version_no", name=op.f("uq_pr_performance_policies_version")),
        # **The weights total one hundred, at the database.** A policy that sums
        # to 90 would silently deflate every index computed under it, and the
        # deflation would look like people getting worse.
        sa.CheckConstraint(
            "workload_weight + quality_weight + timeliness_weight "
            "+ business_contribution_weight = 100",
            name=op.f("ck_pr_performance_policies_weights_total_100"),
        ),
        sa.CheckConstraint(
            "daily_target_minutes > 0",
            name=op.f("ck_pr_performance_policies_daily_target_positive"),
        ),
    )
    op.create_index("ix_pr_performance_policies_status", POLICIES, ["status"])
    op.create_index("ix_pr_performance_policies_effective", POLICIES, ["effective_from"])

    # --- one review per person per month ----------------------------------
    op.create_table(
        REVIEWS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        *[
            column
            for dimension in ("quality", "timeliness", "business_contribution")
            for column in (
                sa.Column(
                    f"{dimension}_level",
                    sa.Enum(
                        *LEVEL_VALUES,
                        name=f"pr_performance_level_{dimension}",
                        native_enum=False,
                        length=30,
                    ),
                    nullable=True,
                ),
                sa.Column(f"{dimension}_score", SCORE, nullable=True),
                sa.Column(f"{dimension}_note", sa.Text(), nullable=True),
            )
        ],
        sa.Column("overall_note", sa.Text(), nullable=True),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_performance_reviews")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_reviews_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id"],
            [f"{PERIODS}.id"],
            name=op.f("fk_pr_performance_reviews_period"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_reviews_reviewer"),
            ondelete=RESTRICT,
        ),
        # **One per person per month.** The constraint that makes the product
        # decision unrepresentable to get wrong.
        sa.UniqueConstraint(
            "user_id", "reporting_period_id", name=op.f("uq_pr_performance_reviews_user_period")
        ),
        # A reviewer is never the person reviewed. Enforced here as well as in
        # the service, because self-review is the one failure that invalidates
        # the whole exercise.
        sa.CheckConstraint(
            "reviewer_user_id IS NULL OR reviewer_user_id <> user_id",
            name=op.f("ck_pr_performance_reviews_no_self_review"),
        ),
        # A level and its score are written together or not at all: a level with
        # no number is a rating that cannot be computed with, and a number with
        # no level is one nobody chose.
        *[
            sa.CheckConstraint(
                f"({dimension}_level IS NULL) = ({dimension}_score IS NULL)",
                name=op.f(f"ck_pr_performance_reviews_{dimension}_level_and_score"),
            )
            for dimension in ("quality", "timeliness", "business_contribution")
        ],
        # **Anything but Đạt needs a sentence.** The result moves money.
        *[
            sa.CheckConstraint(
                f"{dimension}_level IS NULL "
                f"OR {dimension}_level = 'MEETS_EXPECTATIONS' "
                f"OR ({dimension}_note IS NOT NULL AND length(trim({dimension}_note)) > 0)",
                name=op.f(f"ck_pr_performance_reviews_{dimension}_note_required"),
            )
            for dimension in ("quality", "timeliness", "business_contribution")
        ],
    )
    op.create_index("ix_pr_performance_reviews_period", REVIEWS, ["reporting_period_id"])

    # --- the one number an owner may set per person per month --------------
    op.create_table(
        TARGET_OVERRIDES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("monthly_target_override", MINUTES_TOTAL, nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=False),
        sa.Column("set_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_performance_target_overrides")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_target_overrides_user"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id"],
            [f"{PERIODS}.id"],
            name=op.f("fk_pr_performance_target_overrides_period"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_target_overrides_set_by"),
            ondelete=RESTRICT,
        ),
        sa.UniqueConstraint(
            "user_id", "reporting_period_id", name=op.f("uq_pr_performance_target_overrides_user_period")
        ),
        # **An override without a reason is not an override.** Both columns are
        # NOT NULL now that the row exists for nothing else: a row here *is* a
        # deliberate override, so there is no state in which one half is absent.
        sa.CheckConstraint(
            "length(trim(override_reason)) > 0",
            name=op.f("ck_pr_performance_target_overrides_reason_not_empty"),
        ),
        sa.CheckConstraint(
            "monthly_target_override > 0",
            name=op.f("ck_pr_performance_target_overrides_positive"),
        ),
    )

    # --- provenance, one row per scored contribution ----------------------
    op.create_table(
        SCORE_ALLOCATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_contribution_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("scoring_rule_id", sa.Uuid(), nullable=True),
        sa.Column("eligible_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("standard_minutes_per_unit", MINUTES_PER_UNIT, nullable=True),
        sa.Column("eligible_standard_minutes", MINUTES_TOTAL, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *SCORE_STATUS_VALUES,
                name="pr_contribution_score_status",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_score_allocations")),
        sa.ForeignKeyConstraint(
            ["work_contribution_id"],
            [f"{WORK_CONTRIBUTIONS}.id"],
            name=op.f("fk_pr_work_score_allocations_contribution"),
            # **Not CASCADE.** A contribution that has been scored must not be
            # deletable - the provenance row is what makes a month's total
            # explainable, and cascading it away would remove the explanation
            # along with the work.
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_score_allocations_user"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id"],
            [f"{PERIODS}.id"],
            name=op.f("fk_pr_work_score_allocations_period"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_work_score_allocations_work_type"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["scoring_rule_id"],
            [f"{SCORING_RULES}.id"],
            name=op.f("fk_pr_work_score_allocations_rule"),
            ondelete=RESTRICT,
        ),
        # One projection per contribution, so a re-run converges instead of
        # accumulating - the same guarantee M2's allocation table gives.
        sa.UniqueConstraint(
            "work_contribution_id", name=op.f("uq_pr_work_score_allocations_contribution")
        ),
        # Minutes exist exactly when a rule priced them.
        sa.CheckConstraint(
            "(status = 'SCORED' AND scoring_rule_id IS NOT NULL) "
            "OR (status <> 'SCORED' AND eligible_standard_minutes = 0)",
            name=op.f("ck_pr_work_score_allocations_minutes_need_a_rule"),
        ),
    )
    op.create_index(
        "ix_pr_work_score_allocations_user_period",
        SCORE_ALLOCATIONS,
        ["user_id", "reporting_period_id"],
    )
    op.create_index(
        "ix_pr_work_score_allocations_period_status",
        SCORE_ALLOCATIONS,
        ["reporting_period_id", "status"],
    )

    # --- the monthly figure, with everything it was computed from ---------
    op.create_table(
        RESULTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("performance_review_id", sa.Uuid(), nullable=True),
        sa.Column("target_standard_minutes", MINUTES_TOTAL, nullable=True),
        sa.Column("eligible_standard_minutes", MINUTES_TOTAL, nullable=False),
        sa.Column("workload_score", SCORE, nullable=True),
        sa.Column("quality_score", SCORE, nullable=True),
        sa.Column("timeliness_score", SCORE, nullable=True),
        sa.Column("business_contribution_score", SCORE, nullable=True),
        sa.Column("raw_performance_index", SCORE, nullable=True),
        sa.Column("quality_gate_cap", SCORE, nullable=True),
        sa.Column("final_performance_index", SCORE, nullable=True),
        sa.Column(
            "calculation_status",
            sa.Enum(
                *CALCULATION_STATUS_VALUES,
                name="pr_performance_calculation_status",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("diagnostics", JSON_COLUMN, nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_performance_results")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_results_user"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id"],
            [f"{PERIODS}.id"],
            name=op.f("fk_pr_performance_results_period"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            [f"{POLICIES}.id"],
            name=op.f("fk_pr_performance_results_policy"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["performance_review_id"],
            [f"{REVIEWS}.id"],
            name=op.f("fk_pr_performance_results_review"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_performance_results_finalized_by"),
            ondelete=RESTRICT,
        ),
        sa.UniqueConstraint(
            "user_id", "reporting_period_id", name=op.f("uq_pr_performance_results_user_period")
        ),
        # A finalised month names the policy it was computed under. Without it
        # the figure cannot be reproduced, which is the whole point of storing it.
        sa.CheckConstraint(
            "finalized_at IS NULL OR (policy_id IS NOT NULL AND final_performance_index IS NOT NULL)",
            name=op.f("ck_pr_performance_results_finalized_has_provenance"),
        ),
    )
    op.create_index("ix_pr_performance_results_period", RESULTS, ["reporting_period_id"])
    op.create_index(
        "ix_pr_performance_results_period_status",
        RESULTS,
        ["reporting_period_id", "calculation_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_pr_performance_results_period_status", table_name=RESULTS)
    op.drop_index("ix_pr_performance_results_period", table_name=RESULTS)
    op.drop_table(RESULTS)
    op.drop_index("ix_pr_work_score_allocations_period_status", table_name=SCORE_ALLOCATIONS)
    op.drop_index("ix_pr_work_score_allocations_user_period", table_name=SCORE_ALLOCATIONS)
    op.drop_table(SCORE_ALLOCATIONS)
    op.drop_table(TARGET_OVERRIDES)
    op.drop_index("ix_pr_performance_reviews_period", table_name=REVIEWS)
    op.drop_table(REVIEWS)
    op.drop_index("ix_pr_performance_policies_effective", table_name=POLICIES)
    op.drop_index("ix_pr_performance_policies_status", table_name=POLICIES)
    op.drop_table(POLICIES)
    op.drop_index("ix_pr_work_scoring_rules_effective", table_name=SCORING_RULES)
    op.drop_index("ix_pr_work_scoring_rules_type_status", table_name=SCORING_RULES)
    op.drop_table(SCORING_RULES)
