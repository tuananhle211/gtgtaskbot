"""Request and response bodies for scoring and performance. M6.

**The backend is the calculation engine and these responses say so.** Every
figure a screen shows travels here already computed - workload score, raw index,
the gate cap that applied, the final index, the band - together with the inputs
it came from.

**No money.** M6 scores and reports performance; allocating performance pay is a
separate management decision the head takes outside MeoChat. No response here
carries an amount or a coefficient, and a performance index is not a salary
multiplier. A client that had to multiply weights by scores
would be a second engine, and two engines disagree eventually.

The rule the shapes follow: **numbers as strings**. ``Decimal`` through JSON as a
float is how 1.0315 becomes 1.0314999999, and a coefficient that renders
differently from the one the server computed is the exact failure the
canonical-component decision exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from meobot.domain.pr.performance import (
    PrContributionScoreStatus,
    PrPerformanceCalculationStatus,
    PrPerformanceLevel,
    PrScoringRuleStatus,
    PrWorkScoringMode,
)


class _Body(BaseModel):
    """Request bodies refuse unknown fields.

    ``extra="forbid"`` rather than ``ignore``: a body carrying
    ``final_performance_index`` is **refused** rather than silently dropped, so
    an attempt to write a computed figure is an error somebody sees.
    """

    model_config = ConfigDict(extra="forbid")


# --- scoring rules ----------------------------------------------------------


class WorkScoringRuleResponse(BaseModel):
    """One version of one work type's rate."""

    id: uuid.UUID
    work_type_id: uuid.UUID
    work_type_code: str | None = None
    work_type_name: str | None = None
    version_no: int
    mode: PrWorkScoringMode
    #: ``null`` exactly when the mode is ``EXCLUDED_FROM_PERFORMANCE``.
    standard_minutes_per_unit: str | None = None
    #: **What the rate is per.** The work type's measurement mode - ``ITEM_COUNT``
    #: or ``QUANTITY`` - and the unit one rate-multiplication covers: "đầu việc"
    #: for a count, the type's unit ("bình luận") for a quantity. Present when
    #: the work type was joined; a rule read on its own has neither.
    measurement_mode: str | None = None
    measurement_mode_label: str | None = None
    unit_label: str | None = None
    #: ``"30 phút / đầu việc"``, worded by the server so the config table and
    #: the KPI card print the same sentence. Null for an excluded rule.
    rule_label: str | None = None
    effective_from: date
    effective_to: date | None = None
    status: PrScoringRuleStatus
    note: str | None = None
    approved_at: datetime | None = None
    created_at: datetime


class CreateWorkScoringRuleRequest(_Body):
    """Draft a rate. ``PR_WORK_CONFIGURE``."""

    work_type_id: uuid.UUID
    mode: PrWorkScoringMode = PrWorkScoringMode.STANDARD_MINUTES
    #: Required for ``STANDARD_MINUTES``, refused for the excluded mode - see
    #: the service. Sent as a string so 0.9 stays 0.9.
    standard_minutes_per_unit: Decimal | None = Field(default=None, ge=0)
    effective_from: date
    note: str | None = Field(default=None, max_length=4000)


# --- policy -----------------------------------------------------------------


class PerformancePolicyResponse(BaseModel):
    """One version of the weights, caps, barems, gate and bands."""

    id: uuid.UUID
    version_no: int
    effective_from: date
    daily_target_minutes: int
    workload_weight: str
    quality_weight: str
    timeliness_weight: str
    business_contribution_weight: str
    workload_score_cap: str
    quality_scores: dict[str, str]
    timeliness_scores: dict[str, str]
    business_contribution_scores: dict[str, str]
    quality_gate: list[list[str | None]]
    performance_bands: list[list[str]]
    status: PrScoringRuleStatus
    approved_at: datetime | None = None


class CreatePerformancePolicyRequest(_Body):
    """Draft a policy. **Weights must total exactly 100.**"""

    effective_from: date
    daily_target_minutes: int = Field(default=300, gt=0)
    workload_weight: Decimal = Decimal("50")
    quality_weight: Decimal = Decimal("30")
    timeliness_weight: Decimal = Decimal("10")
    business_contribution_weight: Decimal = Decimal("10")
    workload_score_cap: Decimal = Decimal("120")
    note: str | None = Field(default=None, max_length=4000)


# --- the monthly review -----------------------------------------------------


class DimensionRatingBody(_Body):
    """One dimension's answer. A note is required for anything but *Đạt*."""

    level: PrPerformanceLevel
    note: str | None = Field(default=None, max_length=4000)


class SubmitPerformanceReviewRequest(_Body):
    """**One form, three dimensions**, each optional.

    Optional because a manager who rates quality on Tuesday and contribution on
    Friday is doing the ordinary thing. A dimension left out stays missing - it
    is never defaulted to *Đạt*, and the month reports
    ``PERFORMANCE_REVIEW_PENDING`` until it arrives.
    """

    user_id: uuid.UUID
    period_id: uuid.UUID
    quality: DimensionRatingBody | None = None
    timeliness: DimensionRatingBody | None = None
    business_contribution: DimensionRatingBody | None = None
    overall_note: str | None = Field(default=None, max_length=4000)


class PerformanceReviewResponse(BaseModel):
    """The month's three judgements, as recorded."""

    id: uuid.UUID
    user_id: uuid.UUID
    reporting_period_id: uuid.UUID
    quality_level: PrPerformanceLevel | None = None
    quality_score: str | None = None
    quality_note: str | None = None
    timeliness_level: PrPerformanceLevel | None = None
    timeliness_score: str | None = None
    timeliness_note: str | None = None
    business_contribution_level: PrPerformanceLevel | None = None
    business_contribution_score: str | None = None
    business_contribution_note: str | None = None
    overall_note: str | None = None
    reviewer_user_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None


# --- inputs -----------------------------------------------------------------


class SetTargetOverrideRequest(_Body):
    """A workload target set by hand. ``PR_WORK_CONFIGURE``.

    Both fields are required: a target without its justification is a number
    nobody can defend, and this is the one figure in M6 a person types rather
    than the system computing it.
    """

    user_id: uuid.UUID
    period_id: uuid.UUID
    monthly_target_override: Decimal = Field(gt=0)
    override_reason: str = Field(min_length=1, max_length=4000)


# --- the monthly figure -----------------------------------------------------


class WorkTypeBreakdownResponse(BaseModel):
    """One work type's share of the month, and whether it could be priced."""

    work_type_id: uuid.UUID
    work_type_code: str
    work_type_name: str
    contributions: int
    #: M2's decomposition of the counted amount against the approved cap.
    #: Informational since the period-container patch.
    eligible_amount: str
    #: **The amount priced** - the whole counted amount, whatever the cap said.
    counted_amount: str
    standard_minutes: str
    status: PrContributionScoreStatus
    #: The KPI target, read beside the actual for comparison only.
    target_value: str | None = None
    completion_percent: str | None = None
    over_target_amount: str = "0"


class TimelinessEvidenceResponse(BaseModel):
    """Deadline facts. **Evidence for the reviewer, never a score.**

    Deliberately not summarised into a percentage: the moment the system offers
    one, the manager is agreeing with an arithmetic answer instead of making
    their own - and the reason a cut was late is the thing the system cannot see.
    """

    work_items: int
    with_due_at: int
    on_time: int
    overdue: int


class TargetResponse(BaseModel):
    """The month's target, with the arithmetic that produced it.

    ``target_standard_minutes`` is ``null`` when the calendar could not answer.
    That is ``TARGET_UNRESOLVED``, and it is never quietly filled in with 7500.
    """

    target_standard_minutes: str | None = None
    calendar_workdays: str
    approved_leave_days: str
    eligible_workdays: str
    daily_target_minutes: int
    override_reason: str | None = None
    unresolved_reason: str | None = None


class PerformanceSnapshotResponse(BaseModel):
    """One person's month, fully computed. **The frontend renders; it does not calculate.**"""

    user_id: uuid.UUID
    reporting_period_id: uuid.UUID
    period_code: str
    period_status: str
    policy_id: uuid.UUID | None = None
    policy_version_no: int | None = None

    target: TargetResponse
    eligible_standard_minutes: str
    workload_score: str | None = None

    quality_score: str | None = None
    timeliness_score: str | None = None
    business_contribution_score: str | None = None
    review: PerformanceReviewResponse | None = None

    raw_performance_index: str | None = None
    #: The ceiling quality imposed, or ``null`` for none. Sent so a capped month
    #: can say *why* it was capped rather than just showing a lower number.
    quality_gate_cap: str | None = None
    final_performance_index: str | None = None
    performance_band: str | None = None

    calculation_status: PrPerformanceCalculationStatus
    diagnostics: dict[str, object] = Field(default_factory=dict)
    #: **M6B.** Whether the stored result has been agreed. Declared in M6A and
    #: never populated - the snapshot did not read the result row - so a screen
    #: would have offered edit controls on a finalised month that the server then
    #: refused. Populated now.
    is_finalized: bool = False
    finalized_at: datetime | None = None
    finalized_by_user_id: uuid.UUID | None = None
    #: **M6B, Part AV.** The approved KPI plan's caps priced at the same rates
    #: the workload uses, so a plan worth 69% of the month is visible in week
    #: one. ``null`` when no plan is approved - which is not zero, and is
    #: rendered as a different sentence. **Diagnostic only.**
    planned_standard_minutes: str | None = None

    counted_contributions: int = 0
    eligible_contributions: int = 0
    over_quota_contributions: int = 0
    breakdown: list[WorkTypeBreakdownResponse] = Field(default_factory=list)
    evidence: TimelinessEvidenceResponse
    #: **1 điểm workload = 1 phút chuẩn.** Sent so the phrase is the server's.
    standard_minute_note: str


class PerformancePeriodRowResponse(BaseModel):
    """One employee's row in the owner's monthly table."""

    user_id: uuid.UUID
    full_name: str
    snapshot: PerformanceSnapshotResponse


class PerformanceActionRequest(_Body):
    """Recalculate or finalise one person's month."""

    user_id: uuid.UUID
    period_id: uuid.UUID


# --- the month, summarised -------------------------------------------------


class PerformanceSummaryResponse(BaseModel):
    """One month's counts, for the head's report header.

    **Counts, not analytics.** Averages are ``null`` rather than zero when
    nothing qualifies: a department where nobody has been reviewed has no
    average, and 0,00 would report it as having performed badly.
    """

    period_id: uuid.UUID
    period_code: str
    period_status: str
    employees: int
    reviewed: int
    pending_review: int
    finalized: int
    average_final_index: str | None = None
    average_workload_score: str | None = None
    average_quality_score: str | None = None
    average_timeliness_score: str | None = None
    average_business_contribution_score: str | None = None
    #: Band name to headcount. Classification only - **never mapped to money.**
    bands: dict[str, int] = Field(default_factory=dict)


__all__: list[str] = [
    "CreatePerformancePolicyRequest",
    "CreateWorkScoringRuleRequest",
    "DimensionRatingBody",
    "PerformanceActionRequest",
    "PerformancePeriodRowResponse",
    "PerformancePolicyResponse",
    "PerformanceReviewResponse",
    "PerformanceSnapshotResponse",
    "PerformanceSummaryResponse",
    "SetTargetOverrideRequest",
    "SubmitPerformanceReviewRequest",
    "TargetResponse",
    "TimelinessEvidenceResponse",
    "WorkScoringRuleResponse",
    "WorkTypeBreakdownResponse",
]
