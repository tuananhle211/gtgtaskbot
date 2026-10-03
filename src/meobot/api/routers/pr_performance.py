"""HTTP for scoring, the monthly review and the performance index. M6.

Thirteen routes over four nouns: **scoring rules**, **policies**, **reviews** and
**results**. Every one of them returns figures the server computed - a client
renders them and never reconstructs them.

**No route returns money.** M6 scores and reports performance; the head allocates
performance pay separately, outside MeoChat, using this report as evidence.

Nothing here is a generic ``PATCH``. Approving a rate, judging a month and
finalising a figure are three different decisions with three different
permissions, and a route that could do any of them depending on the body would
be a route whose authorisation depends on its payload.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep, SessionDep
from meobot.api.schemas.pr_performance import (
    CreatePerformancePolicyRequest,
    CreateWorkScoringRuleRequest,
    PerformanceActionRequest,
    PerformancePeriodRowResponse,
    PerformancePolicyResponse,
    PerformanceReviewResponse,
    PerformanceSnapshotResponse,
    PerformanceSummaryResponse,
    SetTargetOverrideRequest,
    SubmitPerformanceReviewRequest,
    TargetResponse,
    TimelinessEvidenceResponse,
    WorkScoringRuleResponse,
    WorkTypeBreakdownResponse,
)
from meobot.application.pr_performance_review_service import DimensionRating
from meobot.application.pr_performance_service import PerformanceSnapshot, PerformanceSummary
from meobot.db.models.pr_performance import PrPerformanceReview, PrWorkScoringRule
from meobot.db.models.pr_work import PrWorkType
from meobot.db.models.user import User
from meobot.domain.pr.performance import STANDARD_MINUTE_NOTE
from meobot.domain.pr.work_quota_labels import (
    quota_target_unit_label,
    work_quota_basis_label,
    workload_rule_label,
)

router = APIRouter(prefix="/api/pr/performance", tags=["pr-performance"])

_WRITE_RESPONSES: dict[int | str, dict[str, str]] = {
    403: {"description": "The actor may not do this"},
    404: {"description": "Not found"},
    409: {"description": "Refused by state"},
    422: {"description": "Refused by a business rule"},
}


def _rule(row: PrWorkScoringRule, work_type: PrWorkType | None = None) -> WorkScoringRuleResponse:
    basis = work_type.default_quota_basis if work_type else None
    unit = work_type.default_unit if work_type else None
    return WorkScoringRuleResponse(
        id=row.id,
        work_type_id=row.work_type_id,
        work_type_code=work_type.code if work_type else None,
        work_type_name=work_type.name if work_type else None,
        version_no=row.version_no,
        mode=row.mode,
        standard_minutes_per_unit=(
            str(row.standard_minutes_per_unit)
            if row.standard_minutes_per_unit is not None
            else None
        ),
        measurement_mode=basis.value if basis else None,
        measurement_mode_label=work_quota_basis_label(basis) if basis else None,
        unit_label=quota_target_unit_label(basis, unit) if basis else None,
        rule_label=(
            workload_rule_label(row.standard_minutes_per_unit, basis, unit)
            if basis and row.standard_minutes_per_unit is not None
            else None
        ),
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        status=row.status,
        note=row.note,
        approved_at=row.approved_at,
        created_at=row.created_at,
    )


def _review(row: PrPerformanceReview | None) -> PerformanceReviewResponse | None:
    if row is None:
        return None
    return PerformanceReviewResponse(
        id=row.id,
        user_id=row.user_id,
        reporting_period_id=row.reporting_period_id,
        quality_level=row.quality_level,
        quality_score=str(row.quality_score) if row.quality_score is not None else None,
        quality_note=row.quality_note,
        timeliness_level=row.timeliness_level,
        timeliness_score=str(row.timeliness_score) if row.timeliness_score is not None else None,
        timeliness_note=row.timeliness_note,
        business_contribution_level=row.business_contribution_level,
        business_contribution_score=(
            str(row.business_contribution_score)
            if row.business_contribution_score is not None
            else None
        ),
        business_contribution_note=row.business_contribution_note,
        overall_note=row.overall_note,
        reviewer_user_id=row.reviewer_user_id,
        reviewed_at=row.reviewed_at,
    )


def _snapshot(snapshot: PerformanceSnapshot) -> PerformanceSnapshotResponse:
    """Everything the screens need, already computed."""
    target = snapshot.target
    return PerformanceSnapshotResponse(
        user_id=snapshot.user_id,
        reporting_period_id=snapshot.period.id,
        period_code=snapshot.period.code,
        period_status=snapshot.period.status.value,
        policy_id=snapshot.policy.id if snapshot.policy else None,
        policy_version_no=snapshot.policy.version_no if snapshot.policy else None,
        target=TargetResponse(
            target_standard_minutes=(
                str(target.target_standard_minutes)
                if target.target_standard_minutes is not None
                else None
            ),
            calendar_workdays=str(target.calendar_workdays),
            approved_leave_days=str(target.approved_leave_days),
            eligible_workdays=str(target.eligible_workdays),
            daily_target_minutes=target.daily_target_minutes,
            override_reason=target.override_reason,
            unresolved_reason=target.unresolved_reason,
        ),
        eligible_standard_minutes=str(snapshot.eligible_standard_minutes),
        workload_score=str(snapshot.workload) if snapshot.workload is not None else None,
        quality_score=str(snapshot.quality) if snapshot.quality is not None else None,
        timeliness_score=str(snapshot.timeliness) if snapshot.timeliness is not None else None,
        business_contribution_score=(
            str(snapshot.business_contribution)
            if snapshot.business_contribution is not None
            else None
        ),
        review=_review(snapshot.review),
        raw_performance_index=(str(snapshot.raw_index) if snapshot.raw_index is not None else None),
        quality_gate_cap=str(snapshot.gate_cap) if snapshot.gate_cap is not None else None,
        final_performance_index=(
            str(snapshot.final_index) if snapshot.final_index is not None else None
        ),
        performance_band=snapshot.band,
        calculation_status=snapshot.status,
        diagnostics=snapshot.diagnostics,
        is_finalized=snapshot.is_finalized,
        finalized_at=snapshot.finalized_at,
        finalized_by_user_id=snapshot.finalized_by_user_id,
        planned_standard_minutes=(
            str(snapshot.planned_standard_minutes)
            if snapshot.planned_standard_minutes is not None
            else None
        ),
        counted_contributions=snapshot.counted_contributions,
        eligible_contributions=snapshot.eligible_contributions,
        over_quota_contributions=snapshot.over_quota_contributions,
        breakdown=[
            WorkTypeBreakdownResponse(
                work_type_id=row.work_type_id,
                work_type_code=row.work_type_code,
                work_type_name=row.work_type_name,
                contributions=row.contributions,
                eligible_amount=str(row.eligible_amount),
                counted_amount=str(row.counted_amount),
                standard_minutes=str(row.standard_minutes),
                status=row.status,
                target_value=str(row.target_value) if row.target_value is not None else None,
                completion_percent=(
                    str(row.completion_percent) if row.completion_percent is not None else None
                ),
                over_target_amount=str(row.over_target_amount),
            )
            for row in snapshot.breakdown
        ],
        evidence=TimelinessEvidenceResponse(
            work_items=snapshot.evidence.work_items,
            with_due_at=snapshot.evidence.with_due_at,
            on_time=snapshot.evidence.on_time,
            overdue=snapshot.evidence.overdue,
        ),
        standard_minute_note=STANDARD_MINUTE_NOTE,
    )


def _summary(summary: PerformanceSummary) -> PerformanceSummaryResponse:
    def number(value: object) -> str | None:
        return None if value is None else str(value)

    return PerformanceSummaryResponse(
        period_id=summary.period.id,
        period_code=summary.period.code,
        period_status=summary.period.status.value,
        employees=summary.employees,
        reviewed=summary.reviewed,
        pending_review=summary.pending_review,
        finalized=summary.finalized,
        average_final_index=number(summary.average_final_index),
        average_workload_score=number(summary.average_workload),
        average_quality_score=number(summary.average_quality),
        average_timeliness_score=number(summary.average_timeliness),
        average_business_contribution_score=number(summary.average_business_contribution),
        bands=summary.bands,
    )


# --- scoring rules ----------------------------------------------------------


@router.get(
    "/scoring-rules",
    response_model=list[WorkScoringRuleResponse],
    summary="Workload rules and their history",
)
async def list_scoring_rules(
    actor: CurrentActorDep,
    services: PrServicesDep,
    session: SessionDep,
    work_type_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[WorkScoringRuleResponse]:
    """``PR_WORK_CONFIGURE``. Every version, newest first, including superseded.

    History is part of the answer rather than clutter: *"what was this worth in
    September"* is the question a disputed figure turns into.
    """
    rows = await services.work_scoring_rules.list_rules(actor=actor, work_type_id=work_type_id)
    types = {row.id: row for row in (await session.execute(select(PrWorkType))).scalars().all()}
    return [_rule(row, types.get(row.work_type_id)) for row in rows]


@router.post(
    "/scoring-rules",
    response_model=WorkScoringRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a workload rule",
    responses=_WRITE_RESPONSES,
)
async def create_scoring_rule(
    body: CreateWorkScoringRuleRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkScoringRuleResponse:
    """``PR_WORK_CONFIGURE``. Drafted, not in force until approved."""
    row = await services.work_scoring_rules.create_draft(
        actor=actor,
        request_id=request_id,
        work_type_id=body.work_type_id,
        mode=body.mode,
        standard_minutes_per_unit=body.standard_minutes_per_unit,
        effective_from=body.effective_from,
        note=body.note,
    )
    return _rule(row, await services.session.get(PrWorkType, row.work_type_id))


@router.post(
    "/scoring-rules/{rule_id}/approve",
    response_model=WorkScoringRuleResponse,
    summary="Put a workload rule in force",
    responses=_WRITE_RESPONSES,
)
async def approve_scoring_rule(
    rule_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkScoringRuleResponse:
    """``PR_WORK_CONFIGURE``. Closes the version it replaces; refuses an overlap."""
    row = await services.work_scoring_rules.approve(
        actor=actor, request_id=request_id, rule_id=rule_id
    )
    return _rule(row, await services.session.get(PrWorkType, row.work_type_id))


# --- policy -----------------------------------------------------------------


@router.get(
    "/policies",
    response_model=list[PerformancePolicyResponse],
    summary="Performance policies and their history",
)
async def list_policies(
    actor: CurrentActorDep, services: PrServicesDep
) -> list[PerformancePolicyResponse]:
    """``PR_WORK_CONFIGURE``."""
    return [_policy(row) for row in await services.performance_policies.list_policies(actor=actor)]


@router.post(
    "/policies",
    response_model=PerformancePolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a performance policy",
    responses=_WRITE_RESPONSES,
)
async def create_policy(
    body: CreatePerformancePolicyRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PerformancePolicyResponse:
    """``PR_WORK_CONFIGURE``. **The four weights must total exactly 100.**"""
    return _policy(
        await services.performance_policies.create_draft(
            actor=actor,
            request_id=request_id,
            effective_from=body.effective_from,
            daily_target_minutes=body.daily_target_minutes,
            workload_weight=body.workload_weight,
            quality_weight=body.quality_weight,
            timeliness_weight=body.timeliness_weight,
            business_contribution_weight=body.business_contribution_weight,
            workload_score_cap=body.workload_score_cap,
            note=body.note,
        )
    )


@router.post(
    "/policies/{policy_id}/approve",
    response_model=PerformancePolicyResponse,
    summary="Put a performance policy in force",
    responses=_WRITE_RESPONSES,
)
async def approve_policy(
    policy_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PerformancePolicyResponse:
    """``PR_WORK_CONFIGURE``. Approved policies are immutable."""
    return _policy(
        await services.performance_policies.approve(
            actor=actor, request_id=request_id, policy_id=policy_id
        )
    )


def _policy(row: object) -> PerformancePolicyResponse:
    from meobot.db.models.pr_performance import PrPerformancePolicy

    assert isinstance(row, PrPerformancePolicy)
    return PerformancePolicyResponse(
        id=row.id,
        version_no=row.version_no,
        effective_from=row.effective_from,
        daily_target_minutes=row.daily_target_minutes,
        workload_weight=str(row.workload_weight),
        quality_weight=str(row.quality_weight),
        timeliness_weight=str(row.timeliness_weight),
        business_contribution_weight=str(row.business_contribution_weight),
        workload_score_cap=str(row.workload_score_cap),
        quality_scores=dict(row.quality_scores),
        timeliness_scores=dict(row.timeliness_scores),
        business_contribution_scores=dict(row.business_contribution_scores),
        quality_gate=[list(pair) for pair in row.quality_gate],
        performance_bands=[list(pair) for pair in row.performance_bands],
        status=row.status,
        approved_at=row.approved_at,
    )


# --- the monthly figure -----------------------------------------------------


@router.get("", response_model=PerformanceSnapshotResponse, summary="One person's month")
async def read_performance(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period_id: Annotated[uuid.UUID, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> PerformanceSnapshotResponse:
    """Your own month, or somebody else's with the reviewing or view-all capability.

    Computed live and **written nothing**: an employee opening their own page
    must not be a write, and a read that recalculated into the database would
    make every page view an audit event.
    """
    subject = user_id or actor.user_id
    if subject is None:
        from meobot.domain.pr.errors import PrValidationError

        raise PrValidationError(
            "Không xác định được nhân sự.",
            details={"field": "user_id", "reason": "user_id_required"},
        )
    return _snapshot(
        await services.performance.snapshot(actor=actor, user_id=subject, period_id=period_id)
    )


@router.get(
    "/period/{period_id}",
    response_model=list[PerformancePeriodRowResponse],
    summary="Everybody's month, for the review table",
)
async def list_period_performance(
    period_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    session: SessionDep,
) -> list[PerformancePeriodRowResponse]:
    """``PR_PERFORMANCE_REVIEW``. One row per active employee.

    Everybody rather than only those with work: a person who produced nothing is
    exactly the row a manager needs to see, and filtering them out would hide
    the case worth asking about.
    """
    from meobot.domain.pr.policy import PrCapability

    await services.capabilities.require(actor, PrCapability.PR_PERFORMANCE_REVIEW)
    people = (
        (await session.execute(select(User).where(User.active.is_(True)).order_by(User.full_name)))
        .scalars()
        .all()
    )
    rows: list[PerformancePeriodRowResponse] = []
    for person in people:
        snapshot = await services.performance.snapshot(
            actor=actor, user_id=person.id, period_id=period_id
        )
        rows.append(
            PerformancePeriodRowResponse(
                user_id=person.id, full_name=person.full_name, snapshot=_snapshot(snapshot)
            )
        )
    return rows


@router.put(
    "/review",
    response_model=PerformanceReviewResponse,
    summary="Record the monthly manager review",
    responses=_WRITE_RESPONSES,
)
async def submit_review(
    body: SubmitPerformanceReviewRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PerformanceReviewResponse:
    """``PR_PERFORMANCE_REVIEW``. **One review per person per month.**

    ``PUT`` rather than ``POST`` because it is idempotent on
    ``(user_id, period_id)``: submitting twice revises the one review rather than
    creating a second. Self-review is refused whoever the actor is.
    """
    review = await services.performance_reviews.submit(
        actor=actor,
        request_id=request_id,
        user_id=body.user_id,
        period_id=body.period_id,
        quality=(
            DimensionRating(level=body.quality.level, note=body.quality.note)
            if body.quality
            else None
        ),
        timeliness=(
            DimensionRating(level=body.timeliness.level, note=body.timeliness.note)
            if body.timeliness
            else None
        ),
        business_contribution=(
            DimensionRating(
                level=body.business_contribution.level, note=body.business_contribution.note
            )
            if body.business_contribution
            else None
        ),
        overall_note=body.overall_note,
    )
    rendered = _review(review)
    assert rendered is not None
    return rendered


@router.get("/review", response_model=PerformanceReviewResponse | None, summary="Read one review")
async def read_review(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period_id: Annotated[uuid.UUID, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> PerformanceReviewResponse | None:
    """Your own, or anybody's with ``PR_PERFORMANCE_REVIEW``."""
    subject = user_id or actor.user_id
    if subject is None:
        return None
    return _review(
        await services.performance_reviews.read(actor=actor, user_id=subject, period_id=period_id)
    )


@router.post(
    "/target-override",
    response_model=PerformanceSnapshotResponse,
    summary="Set a workload target by hand",
    responses=_WRITE_RESPONSES,
)
async def set_target_override(
    body: SetTargetOverrideRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PerformanceSnapshotResponse:
    """``PR_WORK_CONFIGURE``. **The reason is mandatory.**

    The one figure in M6 a person types rather than the system computing it, and
    the justification is what makes it defensible six months later.
    """
    await services.performance.set_target_override(
        actor=actor,
        request_id=request_id,
        user_id=body.user_id,
        period_id=body.period_id,
        monthly_target_override=body.monthly_target_override,
        override_reason=body.override_reason,
    )
    return _snapshot(
        await services.performance.snapshot(
            actor=actor, user_id=body.user_id, period_id=body.period_id
        )
    )


@router.post(
    "/recalculate",
    response_model=PerformanceSnapshotResponse,
    summary="Recompute and store one month",
    responses=_WRITE_RESPONSES,
)
async def recalculate(
    body: PerformanceActionRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PerformanceSnapshotResponse:
    """``PR_PERFORMANCE_REVIEW``. Open periods only."""
    return _snapshot(
        await services.performance.calculate(
            actor=actor, request_id=request_id, user_id=body.user_id, period_id=body.period_id
        )
    )


@router.post(
    "/finalize",
    response_model=PerformanceSnapshotResponse,
    summary="Agree one month's performance",
    responses=_WRITE_RESPONSES,
)
async def finalize(
    body: PerformanceActionRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PerformanceSnapshotResponse:
    """``PR_PERFORMANCE_REVIEW``. Refuses with diagnostics; **no force flag.**"""
    return _snapshot(
        await services.performance.finalize(
            actor=actor, request_id=request_id, user_id=body.user_id, period_id=body.period_id
        )
    )


@router.get(
    "/period/{period_id}/summary",
    response_model=PerformanceSummaryResponse,
    summary="The month in counts",
)
async def period_summary(
    period_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> PerformanceSummaryResponse:
    """``PR_PERFORMANCE_REVIEW``. Counts and averages - **not analytics.**

    How many are still waiting, how many are agreed, and how the bands fall is
    what running a month needs. Trends, rankings and forecasting are M7's.
    """
    return _summary(await services.performance.period_summary(actor=actor, period_id=period_id))
