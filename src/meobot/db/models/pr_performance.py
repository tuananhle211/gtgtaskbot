"""Scoring, monthly review and performance results. M6.

Six tables, and the split between them is the milestone's design rather than
normalisation for its own sake - see ``alembic/versions/0035``. The short
version: rates and policies are **versioned and approved** because they decide
pay; the monthly review is **one row per person per month** because that is a
form a manager can actually fill in; and every computed figure carries the
version of everything it was computed from, because a performance number nobody
can reproduce is a number nobody can defend.

**No money, anywhere.** M6 scores and reports performance; allocating
performance pay is a separate management decision the department head takes
outside MeoChat. There is no coefficient column and no amount column, and that is
deliberate: a performance index is an evaluation result, and storing it beside a
multiplier would make it read as a promise about somebody's pay.

**M6 reads M1, M2 and M3 and writes none of them.** No model here has a
writeable relationship into the work ledger, the quota allocations or the
content projections; the only foreign keys pointing that way are to
``pr_work_contributions``, ``pr_work_types`` and ``pr_reporting_periods``, and
they exist to *name* what was scored.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.pr.performance import (
    PrContributionScoreStatus,
    PrPerformanceCalculationStatus,
    PrPerformanceLevel,
    PrScoringRuleStatus,
    PrWorkScoringMode,
)

SCORING_RULES = "pr_work_scoring_rules"
POLICIES = "pr_performance_policies"
REVIEWS = "pr_performance_reviews"
TARGET_OVERRIDES = "pr_performance_target_overrides"
SCORE_ALLOCATIONS = "pr_work_score_allocations"
RESULTS = "pr_performance_results"

#: Minutes per unit needs four places: seeding is 0.9 minutes a comment.
MINUTES_PER_UNIT = Numeric(10, 4)
MINUTES_TOTAL = Numeric(14, 2)
SCORE = Numeric(7, 2)

#: **Every person column is ``RESTRICT``.** See ``0035`` - an accountability
#: column that goes ``NULL`` when an account is deleted leaves a rate nobody
#: approved and a month nobody reviewed.


class PrWorkScoringRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What one kind of work is worth, in standard minutes, from one date.

    **Versioned and effective-dated**, and both halves matter. Versioned because
    an approved rate must stop being editable the moment it can affect somebody's
    pay; effective-dated because raising the rate for video editing in January
    must not silently re-price last September, which has been reported and
    agreed.

    The version that applies is chosen by the contribution's ``counted_at`` - the
    instant the work became trustworthy - and never by the projector's clock.
    """

    __tablename__ = SCORING_RULES
    __table_args__ = (
        UniqueConstraint("work_type_id", "version_no", name="uq_pr_work_scoring_rules_version"),
        CheckConstraint(
            "(mode = 'EXCLUDED_FROM_PERFORMANCE' AND standard_minutes_per_unit IS NULL) "
            "OR (mode = 'STANDARD_MINUTES' AND standard_minutes_per_unit IS NOT NULL "
            "    AND standard_minutes_per_unit >= 0)",
            name="ck_pr_work_scoring_rules_minutes_match_mode",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_pr_work_scoring_rules_range_ordered",
        ),
        Index("ix_pr_work_scoring_rules_type_status", "work_type_id", "status"),
        Index("ix_pr_work_scoring_rules_effective", "work_type_id", "effective_from"),
    )

    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_work_types.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[PrWorkScoringMode] = mapped_column(
        value_enum(PrWorkScoringMode, name="pr_work_scoring_mode", length=30), nullable=False
    )
    #: Null exactly when the mode is ``EXCLUDED_FROM_PERFORMANCE``. *Excluded*
    #: and *worth nothing per unit* are different sentences, and the nullable
    #: column plus its CHECK is how they stay different.
    standard_minutes_per_unit: Mapped[Decimal | None] = mapped_column(
        MINUTES_PER_UNIT, nullable=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: Closed when a successor is approved. Open-ended until then.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[PrScoringRuleStatus] = mapped_column(
        value_enum(PrScoringRuleStatus, name="pr_scoring_rule_status", length=20), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCORING_RULES}.id", ondelete="RESTRICT"), nullable=True
    )


class PrPerformancePolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The weights, caps, barems, gate and bands one month was judged under.

    Versioned for the reason the scoring rules are: every number here moves
    money, so an approved policy is immutable and a change is a new version. A
    finalised result names the policy it used, which is what makes *"why was my
    September 103.15"* answerable in December.

    The barems travel as JSON because they are **tables of numbers the business
    edits**, not schema. A column per level would make adding a rung a migration,
    and the rungs are exactly what a department argues about.
    """

    __tablename__ = POLICIES
    __table_args__ = (
        UniqueConstraint("version_no", name="uq_pr_performance_policies_version"),
        # **The weights total one hundred, at the database.** A policy summing to
        # 90 would deflate every index computed under it, and the deflation would
        # look like people getting worse.
        CheckConstraint(
            "workload_weight + quality_weight + timeliness_weight "
            "+ business_contribution_weight = 100",
            name="ck_pr_performance_policies_weights_total_100",
        ),
        CheckConstraint(
            "daily_target_minutes > 0", name="ck_pr_performance_policies_daily_target_positive"
        ),
        Index("ix_pr_performance_policies_status", "status"),
        Index("ix_pr_performance_policies_effective", "effective_from"),
    )

    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: A KPI workday in standard minutes. 300 by default; the target is this
    #: times the month's eligible workdays, never a flat 7500.
    daily_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    workload_weight: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    quality_weight: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    timeliness_weight: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    business_contribution_weight: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    workload_score_cap: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    quality_scores: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    timeliness_scores: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    business_contribution_scores: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    #: ``[[quality_floor, cap_or_null], …]``, most generous first.
    quality_gate: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False)
    #: ``[[index_floor, name], …]``. Display only - bands create no payout cliff.
    performance_bands: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False)
    status: Mapped[PrScoringRuleStatus] = mapped_column(
        value_enum(PrScoringRuleStatus, name="pr_performance_policy_status", length=20),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrPerformanceReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """**One manager review, per person, per month.** Three judgements on it.

    The product decision the schema makes unrepresentable to get wrong: the
    unique constraint is on ``(user_id, reporting_period_id)``, so a hundred
    deliverables in a month still produce exactly one form. Per-item quality
    review is the design that looks rigorous and collapses on contact with twenty
    people and a hundred deliverables each.

    Each dimension is a nullable *pair* - a level and the score the policy gave
    it - written together or not at all. A half-filled review is a real state
    (the manager rated quality on Tuesday and has not got to the rest), and the
    calculation reports ``PERFORMANCE_REVIEW_PENDING`` rather than defaulting the
    missing dimension to 100.
    """

    __tablename__ = REVIEWS
    __table_args__ = (
        UniqueConstraint(
            "user_id", "reporting_period_id", name="uq_pr_performance_reviews_user_period"
        ),
        # Self-review is the one failure that invalidates the whole exercise, so
        # it is refused by the database as well as by the service.
        CheckConstraint(
            "reviewer_user_id IS NULL OR reviewer_user_id <> user_id",
            name="ck_pr_performance_reviews_no_self_review",
        ),
        *[
            CheckConstraint(
                f"({dimension}_level IS NULL) = ({dimension}_score IS NULL)",
                name=f"ck_pr_performance_reviews_{dimension}_level_and_score",
            )
            for dimension in ("quality", "timeliness", "business_contribution")
        ],
        # **Anything but Đạt needs a sentence.** The result follows a person.
        *[
            CheckConstraint(
                f"{dimension}_level IS NULL "
                f"OR {dimension}_level = 'MEETS_EXPECTATIONS' "
                f"OR ({dimension}_note IS NOT NULL AND length(trim({dimension}_note)) > 0)",
                name=f"ck_pr_performance_reviews_{dimension}_note_required",
            )
            for dimension in ("quality", "timeliness", "business_contribution")
        ],
        Index("ix_pr_performance_reviews_period", "reporting_period_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reporting_period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete="RESTRICT"), nullable=False
    )

    quality_level: Mapped[PrPerformanceLevel | None] = mapped_column(
        value_enum(PrPerformanceLevel, name="pr_performance_level_quality", length=30),
        nullable=True,
    )
    quality_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    quality_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    timeliness_level: Mapped[PrPerformanceLevel | None] = mapped_column(
        value_enum(PrPerformanceLevel, name="pr_performance_level_timeliness", length=30),
        nullable=True,
    )
    timeliness_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    timeliness_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    business_contribution_level: Mapped[PrPerformanceLevel | None] = mapped_column(
        value_enum(
            PrPerformanceLevel, name="pr_performance_level_business_contribution", length=30
        ),
        nullable=True,
    )
    business_contribution_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    business_contribution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    overall_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrPerformanceTargetOverride(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A workload target an owner set by hand, and the reason they had to.

    The one number a person may put into M6 that the calendar did not produce -
    somebody who started mid-month, a long absence the HR rows cannot express.
    Both columns are ``NOT NULL``: a row here **is** a deliberate override, so
    there is no state in which the number exists without its justification.

    Renamed from ``pr_performance_inputs`` while ``0035`` was still undeployed.
    It once also carried a bonus basis and a share weight; with compensation out
    of M6 the generic name described a table that holds exactly one thing.
    """

    __tablename__ = TARGET_OVERRIDES
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "reporting_period_id",
            name="uq_pr_performance_target_overrides_user_period",
        ),
        # **An override without a reason is not an override.** Replacing a
        # computed target is precisely the action somebody will be asked about.
        CheckConstraint(
            "length(trim(override_reason)) > 0",
            name="ck_pr_performance_target_overrides_reason_not_empty",
        ),
        CheckConstraint(
            "monthly_target_override > 0",
            name="ck_pr_performance_target_overrides_positive",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reporting_period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete="RESTRICT"), nullable=False
    )
    monthly_target_override: Mapped[Decimal] = mapped_column(MINUTES_TOTAL, nullable=False)
    override_reason: Mapped[str] = mapped_column(Text, nullable=False)
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class PrWorkScoreAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One contribution's workload, and the rule version that priced it.

    The provenance row. Without it a month's total is a number; with it, every
    minute in the total names the contribution it came from, the eligible amount
    M2 allowed, and the approved rate that converted one into the other.

    One row per contribution, so re-running converges rather than accumulating -
    the same guarantee M2's allocation table gives one layer down.
    """

    __tablename__ = SCORE_ALLOCATIONS
    __table_args__ = (
        UniqueConstraint("work_contribution_id", name="uq_pr_work_score_allocations_contribution"),
        # Minutes exist exactly when a rule priced them. ``NO_SCORING_RULE`` and
        # ``EXCLUDED_FROM_PERFORMANCE`` both carry zero, and the difference
        # between them lives in ``status`` rather than in the number.
        CheckConstraint(
            "(status = 'SCORED' AND scoring_rule_id IS NOT NULL) "
            "OR (status <> 'SCORED' AND eligible_standard_minutes = 0)",
            name="ck_pr_work_score_allocations_minutes_need_a_rule",
        ),
        Index("ix_pr_work_score_allocations_user_period", "user_id", "reporting_period_id"),
        Index("ix_pr_work_score_allocations_period_status", "reporting_period_id", "status"),
    )

    work_contribution_id: Mapped[uuid.UUID] = mapped_column(
        # **Not CASCADE.** A contribution that has been scored must not be
        # deletable: this row is what makes a month's total explainable, and
        # cascading it away would delete the explanation with the work.
        ForeignKey("pr_work_contributions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reporting_period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete="RESTRICT"), nullable=False
    )
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_work_types.id", ondelete="RESTRICT"), nullable=False
    )
    scoring_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCORING_RULES}.id", ondelete="RESTRICT"), nullable=True
    )
    #: **M2's eligible amount, copied** - kept as the informational
    #: decomposition of the counted amount against the approved cap.
    eligible_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: **The amount that was priced.** ``0039``: the whole counted amount of the
    #: contribution - inside the cap, over it, or with no quota at all - because
    #: a KPI target is a comparison and never a ceiling on what work is worth.
    #: Nullable only for rows written before ``0039``, which are backfilled
    #: from ``eligible_amount``, the one amount they ever priced.
    counted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    standard_minutes_per_unit: Mapped[Decimal | None] = mapped_column(
        MINUTES_PER_UNIT, nullable=True
    )
    eligible_standard_minutes: Mapped[Decimal] = mapped_column(MINUTES_TOTAL, nullable=False)
    status: Mapped[PrContributionScoreStatus] = mapped_column(
        value_enum(PrContributionScoreStatus, name="pr_contribution_score_status", length=30),
        nullable=False,
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrPerformanceResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person's month, with everything it was computed from.

    **Never one naked score.** The row carries the target, the eligible minutes,
    all four component scores, the raw index, the gate cap that applied, the
    final index and the coefficient - plus the policy and review it used. A
    figure whose inputs are not stored beside it cannot be explained after the
    policy changes, and explaining it is the entire job.
    """

    __tablename__ = RESULTS
    __table_args__ = (
        UniqueConstraint(
            "user_id", "reporting_period_id", name="uq_pr_performance_results_user_period"
        ),
        # A finalised month names the policy it was computed under. Without it
        # the figure cannot be reproduced, which is why it is stored at all.
        CheckConstraint(
            "finalized_at IS NULL "
            "OR (policy_id IS NOT NULL AND final_performance_index IS NOT NULL)",
            name="ck_pr_performance_results_finalized_has_provenance",
        ),
        Index("ix_pr_performance_results_period", "reporting_period_id"),
        Index(
            "ix_pr_performance_results_period_status",
            "reporting_period_id",
            "calculation_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reporting_period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete="RESTRICT"), nullable=False
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{POLICIES}.id", ondelete="RESTRICT"), nullable=True
    )
    performance_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{REVIEWS}.id", ondelete="RESTRICT"), nullable=True
    )
    #: ``None`` when the workday calendar could not answer - which is reported as
    #: ``TARGET_UNRESOLVED`` and never quietly filled in with 7500.
    target_standard_minutes: Mapped[Decimal | None] = mapped_column(MINUTES_TOTAL, nullable=True)
    eligible_standard_minutes: Mapped[Decimal] = mapped_column(MINUTES_TOTAL, nullable=False)
    workload_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    timeliness_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    business_contribution_score: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    raw_performance_index: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    #: The ceiling quality imposed, or ``None`` when it imposed none. Stored
    #: rather than recomputed so a capped month says *why* it was capped.
    quality_gate_cap: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    final_performance_index: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    calculation_status: Mapped[PrPerformanceCalculationStatus] = mapped_column(
        value_enum(
            PrPerformanceCalculationStatus, name="pr_performance_calculation_status", length=40
        ),
        nullable=False,
    )
    #: What is missing, per component, when the status is not ``READY``. The
    #: reason an owner's table is a worklist rather than a wall of "incomplete".
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


__all__: list[str] = [
    "PrPerformancePolicy",
    "PrPerformanceResult",
    "PrPerformanceReview",
    "PrPerformanceTargetOverride",
    "PrWorkScoreAllocation",
    "PrWorkScoringRule",
]
