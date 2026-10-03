"""``/api/pr/work`` - KPI plans, quotas and eligibility. M2.

Its own router beside ``routers/pr_work.py`` rather than more of it: the Work
Ledger's HTTP surface is already seven hundred lines, and the two answer
different questions - one is *"what work exists and has it been validated"* and
this one is *"which of that work is inside an approved quota"*. Same prefix, so
they are one API family; different modules, so neither grows past reading.

Explicit lifecycle endpoints, never one PATCH
----------------------------------------------

``/approve``, ``/revise`` and ``/discard`` are three routes calling three
service methods. There is no ``PATCH /plans/{id}`` that takes a status, and the
one ``PATCH`` that exists edits a **draft** quota's two numbers and can reach
nothing else - not the work type, not the basis, not the unit, and not any
plan that is no longer a draft.

That is not a style preference. A general PATCH is a route through which a
client could write ``status: "APPROVED"`` and put a target into force without
the validation that makes the word mean something. Bodies additionally use
``extra="forbid"``, so a request carrying ``status`` or ``approved_at`` is
refused rather than ignored.

Authorization is the services', not this layer's
-------------------------------------------------

No route here reads a role, compares a user id, or decides who may act. Every
one of them calls a service method that requires its capability -
``PR_WORK_CONFIGURE`` for every write, ``PR_WORK_VIEW_ALL`` to ask about
somebody else - so a direct API call gets exactly the refusal a hidden button
would have prevented. In particular **``PR_WORK_MANAGE`` opens nothing here**:
a Trưởng nhóm who assigns work does not thereby configure anybody's KPI, and
that is checked in the service rather than by omitting a button.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep
from meobot.api.schemas.pr_work_quota import (
    AddQuotaRequest,
    ContributionEligibilityResponse,
    CreatePlanRequest,
    EligibilityListResponse,
    EligibilitySummaryResponse,
    EmployeePlanSummaryListResponse,
    EmployeePlanSummaryResponse,
    EnsurePeriodRequest,
    PlanHistoryEntryResponse,
    PlanHistoryResponse,
    PlanNoteRequest,
    ReconcileRequest,
    ReconcileResponse,
    ReportingPeriodResponse,
    SelfCreatePlanRequest,
    UpdateQuotaRequest,
    WorkPlanDetailResponse,
    WorkPlanPageResponse,
)
from meobot.application.pr_work_plan_service import active_draft_of, current_plan_of
from meobot.domain.pr.errors import PrNotFoundError
from meobot.domain.pr.work_quota import PrWorkPlanStatus
from meobot.domain.pr.work_quota_labels import work_plan_status_label

router = APIRouter(prefix="/api/pr/work", tags=["pr-work-quota"])

_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "Refused by the plan services - PR_WORK_CONFIGURE, or the read scope."},
    404: {"description": "No such plan, period or quota, or not visible to you."},
    409: {
        "description": (
            "Refused by a lifecycle rule: the plan is not a draft, a draft already "
            "exists, or the reporting period is closed or locked."
        )
    },
    422: {"description": "The request itself is not valid."},
}
_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "You may not read another person's KPI plan."},
    404: {"description": "No such plan or period."},
}


# --- Reporting periods ------------------------------------------------------


@router.get(
    "/periods",
    response_model=list[ReportingPeriodResponse],
    summary="The months a KPI plan can be written for",
    responses=_READ_RESPONSES,
)
async def list_periods(
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=120)] = 24,
) -> list[ReportingPeriodResponse]:
    """Month reporting periods, newest first.

    Months only, because a plan may only target a month - offering a client a
    period the approval would refuse is a picker that produces errors. Readable
    by anybody who may use the Work module: an employee has to be able to see
    which month their own plan is for.
    """
    rows = await services.work_periods.list_periods(actor=actor, limit=limit)
    return [ReportingPeriodResponse.from_row(row) for row in rows]


@router.post(
    "/periods",
    response_model=ReportingPeriodResponse,
    summary="Open a month for reporting",
    responses=_WRITE_RESPONSES,
)
async def ensure_period(
    body: EnsurePeriodRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ReportingPeriodResponse:
    """``PR_WORK_CONFIGURE``. **Idempotent.**

    Asking for a month that already exists returns it unchanged - including its
    status, so a retried request does not reopen a closed period. Returns 200
    rather than 201 for exactly that reason: the common case is "it is already
    there".
    """
    return ReportingPeriodResponse.from_row(
        await services.work_periods.ensure_month_period(
            actor=actor, request_id=request_id, year=body.year, month=body.month
        )
    )


# --- Plans ------------------------------------------------------------------


@router.get(
    "/plans",
    response_model=WorkPlanPageResponse,
    summary="KPI plans",
    responses=_READ_RESPONSES,
)
async def list_plans(
    actor: CurrentActorDep,
    services: PrServicesDep,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    period_id: Annotated[uuid.UUID | None, Query()] = None,
    plan_status: Annotated[PrWorkPlanStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkPlanPageResponse:
    """Plans, newest first.

    Without ``PR_WORK_VIEW_ALL`` this is the caller's own plans, and asking
    about somebody else is **refused** rather than quietly narrowed - a screen
    headed with a colleague's name showing your figures is worse than an error.
    """
    return WorkPlanPageResponse.from_page(
        await services.work_plans.list_plans(
            actor=actor,
            user_id=user_id,
            period_id=period_id,
            status=plan_status,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/plans/mine",
    response_model=WorkPlanDetailResponse,
    summary="My approved KPI plan for one month",
    responses=_READ_RESPONSES,
)
async def my_plan(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period_id: Annotated[uuid.UUID, Query()],
) -> WorkPlanDetailResponse:
    """The plan in force for the caller, in one month.

    Declared **before** ``/plans/{plan_id}`` so ``mine`` is not parsed as a
    UUID. A 404 when there is none, and the client says *"chưa có hạn mức
    KPI"* - which is the truth rather than an empty table that reads as "no
    work".
    """
    detail = await services.work_plans.plan_in_force(actor=actor, user_id=None, period_id=period_id)
    if detail is None:
        raise PrNotFoundError(
            "You have no approved KPI plan for that period",
            details={"entity": "pr_work_plan", "reason": "no_approved_plan"},
        )
    return WorkPlanDetailResponse.from_detail(detail)


@router.get(
    "/plans/summary",
    response_model=EmployeePlanSummaryListResponse,
    summary="Every employee's KPI standing for one month",
    responses=_READ_RESPONSES,
)
async def plan_period_summary(
    period_id: Annotated[uuid.UUID, Query()],
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> EmployeePlanSummaryListResponse:
    """**One row per employee**, not per plan version. ``PR_WORK_VIEW_ALL``.

    The KPI screen's main query. ``GET /plans`` lists plan *versions*, which is
    the right shape for an audit of what was written and the wrong shape for the
    question a manager opens the screen with: an employee on their third
    revision appeared three times, and an employee with no plan appeared not at
    all.

    Which version is current is decided by the server - approved, else draft,
    else none - so no browser reimplements M2's lifecycle. See
    :func:`~meobot.application.pr_work_plan_service._current_plan`.

    Registered **before** ``/plans/{plan_id}`` - FastAPI matches in declaration
    order, and a dynamic path declared first would take ``summary`` as a plan id
    and answer with a 422.
    """
    summaries = await services.work_plans.period_summary(actor=actor, period_id=period_id)
    return EmployeePlanSummaryListResponse.from_summaries(period_id, summaries)


@router.get(
    "/plans/mine/summary",
    response_model=EmployeePlanSummaryResponse,
    summary="My KPI standing for one month",
    responses=_READ_RESPONSES,
)
async def my_plan_summary(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period_id: Annotated[uuid.UUID, Query()],
) -> EmployeePlanSummaryResponse:
    """**The employee's own row.** KPI self-service.

    The same object a manager's summary lists, for the caller alone: the plan
    in force, the draft in flight with its review state, the counts and the
    projected workload. It is what the self-service screen draws its four
    states from - no plan, editing, submitted, approved with a revision - so
    those states are the server's reading of the rows, not a browser's guess.

    Declared before ``/plans/{plan_id}`` for the same reason ``/plans/mine`` is.
    """
    return EmployeePlanSummaryResponse.from_summary(
        await services.work_plans.employee_summary(actor=actor, period_id=period_id)
    )


@router.get(
    "/plans/history",
    response_model=PlanHistoryResponse,
    summary="One employee's KPI plan history for one month",
    responses=_READ_RESPONSES,
)
async def plan_history(
    user_id: Annotated[uuid.UUID, Query()],
    period_id: Annotated[uuid.UUID, Query()],
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> PlanHistoryResponse:
    """Every version, newest first. Own history, or ``PR_WORK_VIEW_ALL``.

    Secondary to the current plan by construction: this is a separate call the
    detail screen makes for a collapsed section, so history cannot be mistaken
    for a list of plans that are all in force.

    Ordered by ``version_no`` descending rather than by a timestamp, because
    numbers are per employee-month and never reused - two versions written in
    the same second would otherwise reorder between page loads.
    """
    rows = await services.work_plans.history(actor=actor, user_id=user_id, period_id=period_id)
    plans = [row for row, _ in rows]
    current = current_plan_of(plans)
    # **Two different "not history" answers.** With an approved v3 and a draft
    # v4, v3 is current and v4 is the revision being written; a screen that
    # filtered only on ``is_current`` put v4 in the history accordion, where it
    # carried no control to continue, approve or discard it. Decided here rather
    # than by comparing statuses in a browser - the index is what guarantees
    # there is exactly one.
    active_draft = active_draft_of(plans)
    return PlanHistoryResponse(
        user_id=user_id,
        period_id=period_id,
        items=[
            PlanHistoryEntryResponse(
                id=row.id,
                version_no=row.version_no,
                status=row.status.value,
                status_label=work_plan_status_label(row.status),
                quota_count=count,
                created_at=row.created_at,
                approved_at=row.approved_at,
                superseded_at=row.superseded_at,
                discarded_at=row.discarded_at,
                note=row.note,
                is_current=current is not None and current.id == row.id,
                is_active_draft=active_draft is not None and active_draft.id == row.id,
            )
            for row, count in rows
        ],
    )


@router.get(
    "/plans/{plan_id}",
    response_model=WorkPlanDetailResponse,
    summary="One KPI plan version",
    responses=_READ_RESPONSES,
)
async def plan_detail(
    plan_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> WorkPlanDetailResponse:
    """Own plan, or ``PR_WORK_VIEW_ALL``. A 404 otherwise.

    Not-found rather than forbidden, matching M1's work detail: whether a
    colleague has a KPI plan at all is itself information.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.detail(actor=actor, plan_id=plan_id)
    )


@router.post(
    "/plans/mine",
    response_model=WorkPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start my own draft KPI plan",
    responses=_WRITE_RESPONSES,
)
async def self_create_plan(
    body: SelfCreatePlanRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """**Tạo KPI của tôi.** KPI self-service; ``PR_WORK_EXECUTE``.

    The subject is the session, never the body. Lands at ``DRAFT`` under the
    same rules as the manager's ``POST /plans``: one draft at a time
    (``draft_already_exists``), and a plan already in force is changed by
    revising it (``approved_plan_requires_revision``), never by a second plan.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.self_create_plan(
            actor=actor, request_id=request_id, period_id=body.period_id, note=body.note
        )
    )


@router.post(
    "/plans",
    response_model=WorkPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a draft KPI plan",
    responses=_WRITE_RESPONSES,
)
async def create_plan(
    body: CreatePlanRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``. Lands at ``DRAFT`` and decides nothing yet.

    A draft is invisible to the evaluator on purpose: a quota that took effect
    while it was still being written would let a half-finished plan set
    somebody's KPI.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.create_plan(
            actor=actor,
            request_id=request_id,
            user_id=body.user_id,
            period_id=body.period_id,
            note=body.note,
        )
    )


@router.post(
    "/plans/{plan_id}/quotas",
    response_model=WorkPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a work type's target and cap",
    responses=_WRITE_RESPONSES,
)
async def add_quota(
    plan_id: uuid.UUID,
    body: AddQuotaRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``, **draft plans only**.

    A second quota for a work type the plan already covers is refused, so an
    approved plan can never contain two answers for one kind of work - and the
    unique index says the same thing independently.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.add_quota(
            actor=actor,
            request_id=request_id,
            plan_id=plan_id,
            work_type_id=body.work_type_id,
            target_value=body.target_value,
            eligibility_cap=body.eligibility_cap,
            basis=body.basis,
            unit=body.unit,
            note=body.note,
        )
    )


@router.patch(
    "/plans/{plan_id}/quotas/{quota_id}",
    response_model=WorkPlanDetailResponse,
    summary="Change a draft quota's numbers",
    responses=_WRITE_RESPONSES,
)
async def update_quota(
    plan_id: uuid.UUID,
    quota_id: uuid.UUID,
    body: UpdateQuotaRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``, **draft plans only**.

    The target and the cap, and nothing else. Changing what a quota is *about*
    is a different quota: remove this one and add the right one, so both acts
    are audited rather than one that reinterprets a number somebody looked at.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.update_quota(
            actor=actor,
            request_id=request_id,
            plan_id=plan_id,
            quota_id=quota_id,
            target_value=body.target_value,
            eligibility_cap=body.eligibility_cap,
            note=body.note,
            work_type_id=body.work_type_id,
        )
    )


@router.delete(
    "/plans/{plan_id}/quotas/{quota_id}",
    response_model=WorkPlanDetailResponse,
    summary="Remove a work type from a draft plan",
    responses=_WRITE_RESPONSES,
)
async def remove_quota(
    plan_id: uuid.UUID,
    quota_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``, **draft plans only**.

    A real delete, because a draft quota has decided nothing: no allocation can
    reference it, since the evaluator only ever reads approved plans.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.remove_quota(
            actor=actor, request_id=request_id, plan_id=plan_id, quota_id=quota_id
        )
    )


@router.post(
    "/plans/{plan_id}/approve",
    response_model=WorkPlanDetailResponse,
    summary="Put a KPI plan into force",
    responses=_WRITE_RESPONSES,
)
async def approve_plan(
    plan_id: uuid.UUID,
    body: PlanNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``. **The call that makes a target decide anything.**

    One transaction: the plan currently in force is superseded, this one becomes
    ``APPROVED``, and the period's eligibility is recomputed - so the figures on
    screen change when the decision does rather than at the next reconcile.

    A retried request is safe: the second attempt finds ``APPROVED`` under the
    row lock and is refused, so nothing is approved twice.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.approve(
            actor=actor, request_id=request_id, plan_id=plan_id, note=body.note
        )
    )


@router.post(
    "/plans/{plan_id}/submit",
    response_model=WorkPlanDetailResponse,
    summary="Submit my draft for the manager's review",
    responses=_WRITE_RESPONSES,
)
async def submit_plan(
    plan_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """**Gửi duyệt.** The plan's own subject, on a ready draft.

    Records who and when, locks the author out, and changes **nothing** else:
    the status stays ``DRAFT``, the plan in force stays in force, the evaluator
    reads nothing. A 409 ``draft_already_submitted`` on a repeat; a 422
    ``plan_not_ready`` with ``details.blockers`` when the draft would not pass
    approval either.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.submit(actor=actor, request_id=request_id, plan_id=plan_id)
    )


@router.post(
    "/plans/{plan_id}/return",
    response_model=WorkPlanDetailResponse,
    summary="Return a submitted draft to its author",
    responses=_WRITE_RESPONSES,
)
async def return_plan(
    plan_id: uuid.UUID,
    body: PlanNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """**Trả lại để chỉnh sửa.** ``PR_WORK_CONFIGURE``, on a submitted draft.

    The same version, reopened for its author, with the manager's note on it.
    The plan in force is untouched. A 409 ``draft_not_submitted`` for a draft
    nobody sent.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.return_for_revision(
            actor=actor, request_id=request_id, plan_id=plan_id, note=body.note
        )
    )


@router.post(
    "/plans/{plan_id}/revise",
    response_model=WorkPlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the next draft version of an approved plan",
    responses=_WRITE_RESPONSES,
)
async def revise_plan(
    plan_id: uuid.UUID,
    body: PlanNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``. **The only way to change an approved plan.**

    The old version stays in force while the new one is written, which is what
    somebody revising a plan actually wants: nothing changes for the employee
    until the revision is approved, and if it never is, nothing changed at all.
    Returns the **new draft**.
    """
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.revise(
            actor=actor, request_id=request_id, plan_id=plan_id, note=body.note
        )
    )


@router.post(
    "/plans/{plan_id}/discard",
    response_model=WorkPlanDetailResponse,
    summary="Abandon a draft KPI plan",
    responses=_WRITE_RESPONSES,
)
async def discard_plan(
    plan_id: uuid.UUID,
    body: PlanNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkPlanDetailResponse:
    """``PR_WORK_CONFIGURE``, draft only. Not a deletion - the row stays."""
    return WorkPlanDetailResponse.from_detail(
        await services.work_plans.discard(
            actor=actor, request_id=request_id, plan_id=plan_id, note=body.note
        )
    )


# --- Eligibility ------------------------------------------------------------


@router.get(
    "/eligibility",
    response_model=EligibilityListResponse,
    summary="Counted contributions, and what the quota says about each",
    responses=_READ_RESPONSES,
)
async def eligibility(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period_id: Annotated[uuid.UUID, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> EligibilityListResponse:
    """One person's counted work in one period, with its quota decision.

    A **read**: it materialises nothing, so a contribution nothing has evaluated
    yet is reported rather than written. Making a read write would mean
    refreshing a screen could change somebody's KPI, and would put a write on a
    path that has to work while a period is ``LOCKED``.

    **A missing allocation is not automatically ``NO_QUOTA``.** It is
    ``NO_QUOTA`` only when no approved quota covers the work type - a claim the
    read can establish with a lookup - and ``PENDING_EVALUATION`` when one does.
    Telling somebody nobody set them a target, when the real problem is that the
    period has not been reconciled, sends them to the wrong person.
    """
    period, subject, rows = await services.work_eligibility.eligibility(
        actor=actor, user_id=user_id, period_id=period_id
    )
    return EligibilityListResponse(
        period=ReportingPeriodResponse.from_row(period),
        user_id=subject,
        contributions=[ContributionEligibilityResponse.from_row(row) for row in rows],
    )


@router.get(
    "/eligibility/summary",
    response_model=EligibilitySummaryResponse,
    summary="KPI plan progress for one month",
    responses=_READ_RESPONSES,
)
async def eligibility_summary(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period_id: Annotated[uuid.UUID, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> EligibilitySummaryResponse:
    """Per work type: counted, eligible, over quota, and the target.

    Own figures always; somebody else's needs ``PR_WORK_VIEW_ALL`` - the same
    capability M1 uses for the department-wide work list, so there is one answer
    to *"may I see a colleague's record"* rather than two that can drift apart.
    """
    summary = await services.work_eligibility.summary(
        actor=actor, user_id=user_id, period_id=period_id
    )
    period = await services.work_periods.require_period(period_id)
    return EligibilitySummaryResponse.from_summary(summary, period=period)


@router.post(
    "/eligibility/reconcile",
    response_model=ReconcileResponse,
    summary="Recompute an open period's eligibility",
    responses=_WRITE_RESPONSES,
)
async def reconcile(
    body: ReconcileRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ReconcileResponse:
    """``PR_WORK_CONFIGURE``. **Idempotent**, and refused on a closed period.

    The explicit path that makes the module converge: it is how contributions
    counted before M2 shipped get their allocations without a migration-time
    backfill, and how a period recovers after a projection failed inside an
    approval.

    Running it twice produces identical allocations, because the evaluator is a
    projection rather than an increment. A ``CLOSED`` or ``LOCKED`` period is
    refused with ``pr_work_period_not_open`` and there is no flag that gets past
    it.
    """
    return ReconcileResponse.from_outcome(
        await services.work_eligibility.reconcile_period(
            actor=actor,
            request_id=request_id,
            period_id=body.period_id,
            user_ids=body.user_ids,
        )
    )
