"""``/api/pr/work`` - the Work Ledger's HTTP surface. M1.

Its own router rather than more of ``routers/pr.py``, which is already close to
four thousand lines and is the content module's.

Explicit actions, never one big PATCH
--------------------------------------

Every lifecycle move is its own endpoint calling its own service method:
``/accept``, ``/start``, ``/complete``, ``/approve``, ``/reject``, ``/reopen``,
``/cancel``. There is no ``PATCH /work/{id}`` that takes a status, and the two
metadata endpoints that do exist - deadline and priority - can set nothing else.

That is not a style preference. A general PATCH is a route through which a
client can write ``status = 'APPROVED'`` and ``count_status = 'COUNTED'``
without passing the checks that make those words mean something, and the whole
milestone is those checks. The request bodies additionally use
``extra="forbid"``, so a body carrying ``status`` is refused rather than
ignored.

Authorization is the service's, not this layer's
-------------------------------------------------

No route here reads a role, compares a user id, or decides who may act. Every
one of them calls a service method that requires its capability and applies the
self-approval rules, so a direct API call gets exactly the refusal a hidden
button would have prevented - which is the only version of an anti-gaming rule
that is worth anything.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep
from meobot.api.schemas.pr_work import (
    AddContributorRequest,
    AddEvidenceRequest,
    AssignWorkBatchRequest,
    AssignWorkBatchResponse,
    AssignWorkRequest,
    BootstrapWorkTypesResponse,
    BulkValidateRequest,
    BulkValidationOutcomeResponse,
    BulkValidationPreflightResponse,
    ChangeDeadlineRequest,
    ChangePriorityRequest,
    CreateWorkTypeRequest,
    ExcludeResultRequest,
    ProposeWorkRequest,
    ReconsiderResultRequest,
    ReportResultRequest,
    UpdateWorkTypeRequest,
    ValidateResultsRequest,
    WorkEvidenceResponse,
    WorkHistoryResponse,
    WorkItemDetailResponse,
    WorkNoteRequest,
    WorkPageResponse,
    WorkReadinessResponse,
    WorkSummaryResponse,
    WorkTypeResponse,
)
from meobot.application.pr_work_bulk_validation_service import BulkValidateCommand
from meobot.application.pr_work_maintenance_service import TERMINAL_DELETABLE_STATUSES
from meobot.application.pr_work_query_service import (
    DEFAULT_WORK_PAGE,
    MAX_WORK_PAGE,
    PrWorkDateField,
    PrWorkPreset,
    PrWorkScope,
    WorkQuery,
)
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.time import utcnow
from meobot.domain.pr.content_work import is_legacy_content_work_item
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import PrWorkSourceType, PrWorkStatus

router = APIRouter(prefix="/api/pr/work", tags=["pr-work"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such work, or not visible to you."}
}
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": ("Refused by the work services - capability, or the self-approval rule.")},
    409: {"description": "Refused by a lifecycle or duplicate rule."},
    422: {"description": "The request itself is not valid."},
    **_NOT_FOUND,
}


# --- Work types -------------------------------------------------------------


@router.get("/types", response_model=list[WorkTypeResponse], summary="The work taxonomy")
async def list_work_types(
    actor: CurrentActorDep,
    services: PrServicesDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[WorkTypeResponse]:
    """Every kind of work, for a picker. Active only unless asked otherwise.

    Whether an inactive type is offered is the service's decision, not a filter
    a browser applies to a full list - a deactivated type must not be selectable
    by a client that forgot to filter.
    """
    rows = await services.work.list_work_types(actor=actor, include_inactive=include_inactive)
    return [WorkTypeResponse.from_row(row) for row in rows]


@router.post(
    "/types",
    response_model=WorkTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a kind of work",
    responses=_WRITE_RESPONSES,
)
async def create_work_type(
    body: CreateWorkTypeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkTypeResponse:
    """``PR_WORK_CONFIGURE``. The code is normalised and must be unique."""
    return WorkTypeResponse.from_row(
        await services.work.create_work_type(
            actor=actor,
            request_id=request_id,
            code=body.code,
            name=body.name,
            category=body.category,
            description=body.description,
            default_unit=body.default_unit,
            default_quota_basis=body.default_quota_basis,
            requires_evidence=body.requires_evidence,
            display_order=body.display_order,
        )
    )


@router.get(
    "/types/{work_type_id}",
    response_model=WorkTypeResponse,
    summary="One kind of work",
    responses=_WRITE_RESPONSES,
)
async def get_work_type(
    work_type_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> WorkTypeResponse:
    """``PR_WORK_EXECUTE``. Resolvable when inactive, unlike the list.

    Carries ``is_in_use`` and ``structure_locked`` so a configuration screen can
    disable the structural fields before the person types into them.
    """
    row = await services.work.get_work_type(actor=actor, work_type_id=work_type_id)
    return WorkTypeResponse.from_row(row, is_in_use=await services.work.is_work_type_in_use(row.id))


@router.patch(
    "/types/{work_type_id}",
    response_model=WorkTypeResponse,
    summary="Edit a kind of work",
    responses=_WRITE_RESPONSES,
)
async def update_work_type(
    work_type_id: uuid.UUID,
    body: UpdateWorkTypeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkTypeResponse:
    """``PR_WORK_CONFIGURE``.

    ``code``, ``default_unit`` and ``default_quota_basis`` are accepted while
    the type is unused and refused with ``409`` once it is - never ignored. See
    :class:`~meobot.api.schemas.pr_work.UpdateWorkTypeRequest`.

    This is not a general-purpose ``PATCH``: ``is_active`` is not in the body,
    so there is no route by which a rename turns a type off.
    """
    row = await services.work.update_work_type(
        actor=actor,
        request_id=request_id,
        work_type_id=work_type_id,
        code=body.code,
        name=body.name,
        category=body.category,
        description=body.description,
        default_unit=body.default_unit,
        default_quota_basis=body.default_quota_basis,
        requires_evidence=body.requires_evidence,
        display_order=body.display_order,
    )
    return WorkTypeResponse.from_row(row, is_in_use=await services.work.is_work_type_in_use(row.id))


@router.post(
    "/types/{work_type_id}/activate",
    response_model=WorkTypeResponse,
    summary="Offer this kind of work again",
    responses=_WRITE_RESPONSES,
)
async def activate_work_type(
    work_type_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkTypeResponse:
    """``PR_WORK_CONFIGURE``. Idempotent."""
    row = await services.work.set_work_type_active(
        actor=actor, request_id=request_id, work_type_id=work_type_id, is_active=True
    )
    return WorkTypeResponse.from_row(row, is_in_use=await services.work.is_work_type_in_use(row.id))


@router.post(
    "/types/{work_type_id}/deactivate",
    response_model=WorkTypeResponse,
    summary="Stop offering this kind of work",
    responses=_WRITE_RESPONSES,
)
async def deactivate_work_type(
    work_type_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkTypeResponse:
    """``PR_WORK_CONFIGURE``. **Not a delete**, and there is no delete.

    Historical work and approved plans keep naming this type and keep rendering
    it. What stops is being offered for new work and new quotas.
    """
    row = await services.work.set_work_type_active(
        actor=actor, request_id=request_id, work_type_id=work_type_id, is_active=False
    )
    return WorkTypeResponse.from_row(row, is_in_use=await services.work.is_work_type_in_use(row.id))


@router.post(
    "/types/bootstrap",
    response_model=BootstrapWorkTypesResponse,
    summary="Create the starting taxonomy",
    responses=_WRITE_RESPONSES,
)
async def bootstrap_work_types(
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> BootstrapWorkTypesResponse:
    """``PR_WORK_CONFIGURE``. Safe to run twice - see the service.

    Exists because ``pr_work_types`` ships empty and a department cannot file
    any work until somebody puts the first rows in it.
    """
    created = await services.work.bootstrap_work_types(actor=actor, request_id=request_id)
    rows = await services.work.list_work_types(actor=actor, include_inactive=True)
    return BootstrapWorkTypesResponse(
        created=[WorkTypeResponse.from_row(row) for row in created],
        work_types=[WorkTypeResponse.from_row(row) for row in rows],
    )


# --- Reading ----------------------------------------------------------------


def _query(
    *,
    scope: str,
    preset: str,
    date_field: str,
    date_from: date | None,
    date_to: date | None,
    user_id: uuid.UUID | None,
    work_type_id: uuid.UUID | None,
    source_type: PrWorkSourceType | None,
    content_id: uuid.UUID | None,
    period_id: uuid.UUID | None,
    work_status: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> WorkQuery:
    """The query parameters as the application layer's own type.

    Enum parsing happens here so an unknown value is a ``422`` naming the field
    rather than a stack trace from inside a service.
    """
    return WorkQuery(
        scope=PrWorkScope(scope),
        preset=PrWorkPreset(preset),
        date_field=PrWorkDateField(date_field),
        date_from=date_from,
        date_to=date_to,
        user_id=user_id,
        work_type_id=work_type_id,
        source_type=source_type,
        content_id=content_id,
        period_id=period_id,
        status=PrWorkStatus(work_status) if work_status else None,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=WorkPageResponse, summary="List work")
async def list_work(
    actor: CurrentActorDep,
    services: PrServicesDep,
    scope: Annotated[PrWorkScope | None, Query()] = None,
    preset: Annotated[PrWorkPreset, Query()] = PrWorkPreset.ALL,
    date_field: Annotated[PrWorkDateField, Query()] = PrWorkDateField.DUE_AT,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    work_type_id: Annotated[uuid.UUID | None, Query()] = None,
    source_type: Annotated[PrWorkSourceType | None, Query()] = None,
    content_id: Annotated[uuid.UUID | None, Query()] = None,
    period_id: Annotated[uuid.UUID | None, Query()] = None,
    work_status: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_WORK_PAGE)] = DEFAULT_WORK_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkPageResponse:
    """One bounded page, filtered server-side.

    ``preset`` is the tab. The four date presets narrow by ``date_field``; the
    three status presets - ``OVERDUE``, ``UPCOMING``, ``OPEN`` - **ignore the
    dates entirely**, which is what makes "selecting a month cannot hide
    carried-over work" a property of the query rather than a habit of the
    client.

    ``scope`` past ``MINE``, and ``user_id`` naming somebody else, both require
    ``PR_WORK_MANAGE`` and are **refused** rather than narrowed: silently
    reducing a department-wide request to one person's own work would put a
    figure on screen that means something other than its label.

    ``period_id`` is the **outer boundary**, post-M4, and it is applied before
    the preset. A management view of September narrowed to "Hôm nay" is
    September's work that happened today; the reverse - a "today" query somebody
    afterwards widened - is a different and wrong question, and it is what the
    screen used to do.
    """
    # **No scope means the widest one this actor may see** - *Toàn bộ* for
    # anybody who holds ``PR_WORK_VIEW_ALL``. The server decides, so a first
    # load and a call that omits the parameter agree.
    resolved = scope or await services.work_queries.default_scope(actor)
    page = await services.work_queries.page(
        actor=actor,
        query=_query(
            scope=resolved.value,
            preset=preset.value,
            date_field=date_field.value,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            work_type_id=work_type_id,
            source_type=source_type,
            content_id=content_id,
            period_id=period_id,
            work_status=work_status,
            search=search,
            limit=limit,
            offset=offset,
        ),
    )
    return WorkPageResponse.from_page(
        page,
        now=utcnow(),
        # The actual, the target and the priced minutes of every stream on the
        # page, in a fixed number of queries. A one-off job gets nothing here.
        containers=await services.work_results.summaries_for(page.items),
    )


@router.get("/summary", response_model=WorkSummaryResponse, summary="Work figures")
async def work_summary(
    actor: CurrentActorDep,
    services: PrServicesDep,
    scope: Annotated[PrWorkScope | None, Query()] = None,
    preset: Annotated[PrWorkPreset, Query()] = PrWorkPreset.MONTH,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    work_type_id: Annotated[uuid.UUID | None, Query()] = None,
    source_type: Annotated[PrWorkSourceType | None, Query()] = None,
    period_id: Annotated[uuid.UUID | None, Query()] = None,
) -> WorkSummaryResponse:
    """Created, accepted, completed, approved, counted - and what is open now.

    **Five different facts, never collapsed into one "task count".** Each of the
    first four counts work items whose own timestamp falls in the period;
    ``counted_contributions`` counts one row per person per counted job, which
    is the employee-workload figure a future quota will be measured against.

    ``overdue``, ``open``, ``awaiting_validation`` and ``proposed`` describe
    **now** and are computed without the period bounds, so no combination of
    parameters can hide outstanding work behind a month.
    """
    resolved = scope or await services.work_queries.default_scope(actor)
    summary = await services.work_queries.summary(
        actor=actor,
        query=_query(
            scope=resolved.value,
            preset=preset.value,
            date_field=PrWorkDateField.COUNTED_AT.value,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            work_type_id=work_type_id,
            # **M4A.** The tiles narrow with the list they sit above. A summary
            # that ignored the source filter would put "12 chờ xác nhận" over a
            # list showing three, which is worse than showing no figure at all.
            source_type=source_type,
            content_id=None,
            # Post-M4. The tiles describe the **month** the screen is scoped to
            # and deliberately not the day slice below it - see
            # ``PrWorkQueryService.summary``. The screen says which they are.
            period_id=period_id,
            work_status=None,
            search=None,
            limit=DEFAULT_WORK_PAGE,
            offset=0,
        ),
    )
    return WorkSummaryResponse.from_summary(summary)


# --- Period containers and results -------------------------------------------
#
# ``/results`` is registered before ``/{work_item_id}`` for the reason
# ``/readiness`` is: FastAPI matches in declaration order.


@router.post(
    "/results",
    response_model=WorkItemDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Report a result into the month's stream",
    responses=_WRITE_RESPONSES,
)
async def report_result(
    body: ReportResultRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """**The generic report form.** Quantity, label, link - and nothing about a KPI.

    Names a work type (and, for a manager, a person and a month) and reports
    into the one stream for those three keys, opening it on first use. The
    result lands ``PENDING`` and is counted by somebody who is not the subject.
    Whether a target exists, has been reached or would be exceeded is not a
    condition of this request - the target is compared afterwards, not
    consulted before.

    ``PR_WORK_EXECUTE`` for your own stream; ``PR_WORK_MANAGE`` for anybody's.
    """
    if body.work_type_id is None:
        raise PrValidationError(
            "Hãy chọn loại công việc.", details={"field": "work_type_id", "reason": "required"}
        )
    result = await services.work_results.report_result(
        actor=actor,
        request_id=request_id,
        quantity=body.quantity,
        label=body.label,
        link=body.link,
        note=body.note,
        work_type_id=body.work_type_id,
        subject_user_id=body.subject_user_id,
        period_id=body.period_id,
        occurred_at=body.occurred_at,
    )
    return await _detail(services, actor, result.work_item_id)


@router.post(
    "/results/{result_id}/exclude",
    response_model=WorkItemDetailResponse,
    summary="Take a result out of the actual",
    responses=_WRITE_RESPONSES,
)
async def exclude_result(
    result_id: uuid.UUID,
    body: ExcludeResultRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """*Từ chối / Không ghi nhận*. ``PR_WORK_VALIDATE``, and not the subject.

    The row stays - ``EXCLUDED / VALIDATOR_REJECTED`` with the reason - and no
    projection restores it; ``POST …/reconsider`` is the release.
    """
    result = await services.work_results.exclude_result(
        actor=actor, request_id=request_id, result_id=result_id, reason=body.reason
    )
    return await _detail(services, actor, result.work_item_id)


@router.post(
    "/results/{result_id}/reconsider",
    response_model=WorkItemDetailResponse,
    summary="Release a rejected result back to pending",
    responses=_WRITE_RESPONSES,
)
async def reconsider_result(
    result_id: uuid.UUID,
    body: ReconsiderResultRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """*Xem xét lại*. ``PR_WORK_VALIDATE``, and not the subject.

    Only a validator's rejection (or a legacy exclusion with no recorded
    author) is released; an administrator's removal and a source reversal are
    the projector's to re-evaluate and refuse with
    ``work_result_not_reconsiderable``. The rejection stays in the timeline.
    """
    result = await services.work_results.reconsider_result(
        actor=actor, request_id=request_id, result_id=result_id, note=body.note
    )
    return await _detail(services, actor, result.work_item_id)


@router.delete(
    "/results/{result_id}",
    response_model=WorkItemDetailResponse,
    summary="Withdraw your own pending result",
    responses=_WRITE_RESPONSES,
)
async def withdraw_result(
    result_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """The reporter takes back a manual result nobody has counted yet."""
    result = await services.work_results.require_result(result_id)
    work_item_id = result.work_item_id
    await services.work_results.withdraw_result(
        actor=actor, request_id=request_id, result_id=result_id
    )
    return await _detail(services, actor, work_item_id)


# Registered **before** ``GET /{work_item_id}``, and it has to be: FastAPI
# matches in declaration order, so a dynamic path declared first would take
# "readiness" as a work item id and answer 422 for a route that exists.
@router.get(
    "/readiness",
    response_model=WorkReadinessResponse,
    summary="What this work will be worth",
)
async def work_readiness(
    actor: CurrentActorDep,
    services: PrServicesDep,
    work_type_id: Annotated[uuid.UUID, Query()],
    user_id: Annotated[list[uuid.UUID] | None, Query()] = None,
) -> WorkReadinessResponse:
    """Whether counted work of this kind would reach a KPI quota and an M6 rate.

    ``PR_WORK_EXECUTE`` - anybody who may file work may be told what will become
    of it. **A diagnostic, not a gate**: every combination of these flags
    creates perfectly valid work, and no write anywhere consults this endpoint.

    Two independent configuration facts, because two different people fix them:

    * ``has_scoring_rule`` is about the **work type** and is the same for
      everybody. ``false`` means M6 will report ``NO_SCORING_RULE`` rather than
      invent a rate, and the person who can fix it holds ``PR_WORK_CONFIGURE``;
    * ``assignees[].has_quota`` is about **one person and this month**.
      ``false`` means M2 will allocate their counted contribution as
      ``NO_QUOTA`` - the work is real, counted and visible, and its eligible
      amount is zero.

    ``period_label`` is ``null`` when no reporting period covers today, which is
    a third and different situation from either flag.
    """
    readiness = await services.work_readiness.readiness(
        actor=actor,
        work_type_id=work_type_id,
        user_ids=tuple(user_id or ()),
    )
    return WorkReadinessResponse.of(readiness)


@router.get(
    "/{work_item_id}",
    response_model=WorkItemDetailResponse,
    summary="One work item",
    responses=_NOT_FOUND,
)
async def get_work(
    work_item_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> WorkItemDetailResponse:
    """One job, its people, its evidence, and what this actor may do to it.

    Visible to a contributor, its creator, whoever assigned it, or a holder of
    ``PR_WORK_MANAGE`` - explicit relationships, because MeoBot models no team
    and M1 declined to invent one.
    """
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/results",
    response_model=WorkItemDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Report a result into this stream",
    responses=_WRITE_RESPONSES,
)
async def report_result_into(
    work_item_id: uuid.UUID,
    body: ReportResultRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """The same report, into a stream named by id. See ``POST /results``."""
    await services.work_results.report_result(
        actor=actor,
        request_id=request_id,
        quantity=body.quantity,
        label=body.label,
        link=body.link,
        note=body.note,
        work_item_id=work_item_id,
        occurred_at=body.occurred_at,
    )
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/results/validate",
    response_model=WorkItemDetailResponse,
    summary="Count pending results",
    responses=_WRITE_RESPONSES,
)
async def validate_results(
    work_item_id: uuid.UUID,
    body: ValidateResultsRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_VALIDATE``, **and the actor must not be the subject.**

    Every pending result on the stream, or the ones named. Counting moves the
    actual; nothing about a target is consulted, so the twenty-first result
    is counted exactly like the first.
    """
    await services.work_results.validate_results(
        actor=actor,
        request_id=request_id,
        work_item_id=work_item_id,
        result_ids=body.result_ids,
        note=body.note,
    )
    return await _detail(services, actor, work_item_id)


@router.get(
    "/{work_item_id}/history",
    response_model=list[WorkHistoryResponse],
    summary="One work item's timeline",
    responses=_NOT_FOUND,
)
async def work_history(
    work_item_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[WorkHistoryResponse]:
    """The user-facing story, oldest first.

    Not the audit trail: this carries the status edges and the notes somebody
    typed, while ``audit_logs`` keeps the request id and the structured
    before/after for whoever is investigating rather than reading.
    """
    rows, names = await services.work.history(actor=actor, work_item_id=work_item_id, limit=limit)
    return [WorkHistoryResponse.from_row(row, names=names) for row in rows]


# --- Creating ---------------------------------------------------------------


@router.post(
    "/proposals",
    response_model=WorkItemDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose work",
    responses=_WRITE_RESPONSES,
)
async def propose_work(
    body: ProposeWorkRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """An employee suggests work. Lands at ``PROPOSED``. ``PR_WORK_EXECUTE``.

    **This does not put anything in anybody's KPI.** A proposal is in nobody's
    workload and its contributions are ``PENDING`` until a *different* manager
    accepts it - the proposer is refused by ``accept``, which is what stops
    "propose" and "assign" being the same button with two names.
    """
    item = await services.work.propose_work(
        actor=actor, request_id=request_id, command=_command(body)
    )
    return await _detail(services, actor, item.id)


@router.post(
    "",
    response_model=WorkItemDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign work",
    responses=_WRITE_RESPONSES,
)
async def assign_work(
    body: AssignWorkRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """A manager creates work and puts it on somebody. ``PR_WORK_MANAGE``.

    Lands at ``ACCEPTED`` directly, because the assignment **is** the
    authorization - and that asymmetry with the proposal route is the whole
    anti-gaming design: an employee cannot put work into their own workload, a
    manager can.
    """
    item = await services.work.assign_work(
        actor=actor, request_id=request_id, command=_command(body)
    )
    return await _detail(services, actor, item.id)


@router.post(
    "/batch",
    response_model=AssignWorkBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign work to several people",
    responses=_WRITE_RESPONSES,
)
async def assign_work_batch(
    body: AssignWorkBatchRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> AssignWorkBatchResponse:
    """A manager assigns one instruction to several people. ``PR_WORK_MANAGE``. **M4A.**

    ``assignment_mode`` decides what is actually created, and the two answers
    are different facts rather than different spellings:

    * ``SHARED_WORK`` - **one** work item with one contribution per person. A
      half-day shoot with a producer, a camera operator and an assistant;
    * ``SEPARATE_PER_ASSIGNEE`` - **one work item per person**, each with that
      person as its only contributor. "100 comments today" handed to three
      people is three obligations, completed, validated and missed separately.

    The field is **required whenever more than one person is named**. There is
    no default, because the wrong answer is not a cosmetic difference: three
    independent responsibilities recorded as one shared job means one person
    completes it for all three and one validation counts all three.

    All of it or none of it: every item is created in the request's single
    transaction, so a refusal on the last assignee leaves no work behind for
    the first.
    """
    items = await services.work.assign_work_batch(
        actor=actor,
        request_id=request_id,
        command=_command(body),
        mode=body.mode,
    )
    return AssignWorkBatchResponse(
        assignment_mode=body.mode.value,
        items=[await _detail(services, actor, item.id) for item in items],
    )


@router.post(
    "/validate/preflight",
    response_model=BulkValidationPreflightResponse,
    summary="What a bulk validation would do",
)
async def bulk_validation_preflight(
    body: BulkValidateRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> BulkValidationPreflightResponse:
    """Screen a selection without writing anything. ``PR_WORK_VALIDATE``. **M4A.**

    The panel calls this before opening its confirmation dialog, so a validator
    sees **which** rows are their own work or missing evidence and can drop
    them - rather than being told the batch was refused and left to find out
    why by bisection.

    Advice about a moment, not a promise about the next one: nothing is locked
    here, and :func:`bulk_validate` re-runs every screen under a row lock.
    """
    preflight = await services.work_bulk_validation.preflight(
        actor=actor, work_item_ids=body.work_item_ids
    )
    return BulkValidationPreflightResponse.of(preflight)


@router.post(
    "/validate",
    response_model=BulkValidationOutcomeResponse,
    summary="Validate several finished jobs",
    responses=_WRITE_RESPONSES,
)
async def bulk_validate(
    body: BulkValidateRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> BulkValidationOutcomeResponse:
    """Validate every job in the batch, or none of them. ``PR_WORK_VALIDATE``. **M4A.**

    Each item is validated by the **same** ``approve`` the single-item route
    calls, so the self-validation rule, the transition matrix, the history, the
    audit row, the notification and the M2 eligibility handoff all happen
    exactly once per job. Nothing here writes ``count_status``.

    * **409** ``pr_bulk_approval_stale`` - at least one job is missing, has
      moved off ``COMPLETED``, lacks required evidence, or is one the actor
      contributed to. **Nothing was validated**, and ``details.affected`` names
      every row that caused it.
    """
    outcome = await services.work_bulk_validation.validate(
        actor=actor,
        request_id=request_id,
        command=BulkValidateCommand(work_item_ids=body.work_item_ids, note=body.note),
    )
    return BulkValidationOutcomeResponse.of(outcome)


# --- Lifecycle actions ------------------------------------------------------


@router.post(
    "/{work_item_id}/accept",
    response_model=WorkItemDetailResponse,
    summary="Accept a proposal",
    responses=_WRITE_RESPONSES,
)
async def accept_work(
    work_item_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``, **and not by the person who proposed it.**

    A ``403`` here with ``reason: self_acceptance`` is the rule doing its job,
    not a bug: a manager may accept anybody's proposal except their own.
    """
    await services.work.accept(actor=actor, request_id=request_id, work_item_id=work_item_id)
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/reject",
    response_model=WorkItemDetailResponse,
    summary="Decline a proposal",
    responses=_WRITE_RESPONSES,
)
async def reject_work(
    work_item_id: uuid.UUID,
    body: WorkNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``, and not by its proposer. The row is kept, not deleted."""
    await services.work.reject(
        actor=actor, request_id=request_id, work_item_id=work_item_id, reason=body.note
    )
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/start",
    response_model=WorkItemDetailResponse,
    summary="Start work",
    responses=_WRITE_RESPONSES,
)
async def start_work(
    work_item_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """A contributor says they have begun. Counts nothing and changes no credit."""
    await services.work.start(actor=actor, request_id=request_id, work_item_id=work_item_id)
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/complete",
    response_model=WorkItemDetailResponse,
    summary="Report work finished",
    responses=_WRITE_RESPONSES,
)
async def complete_work(
    work_item_id: uuid.UUID,
    body: WorkNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """A contributor says the work is done. **This does not make it count.**

    The item moves to ``COMPLETED``, whose label is *"Chờ xác nhận"*, and every
    contribution stays ``PENDING`` until somebody who did not do the work
    approves it. Work types that require evidence refuse this until some exists.
    """
    await services.work.complete(
        actor=actor, request_id=request_id, work_item_id=work_item_id, note=body.note
    )
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/approve",
    response_model=WorkItemDetailResponse,
    summary="Validate finished work",
    responses=_WRITE_RESPONSES,
)
async def approve_work(
    work_item_id: uuid.UUID,
    body: WorkNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_VALIDATE``, **and the actor must not be a contributor.**

    The only endpoint in the module that makes anything ``COUNTED``. It locks
    the item, re-reads its status, refuses an actor with a contribution on it,
    then writes the item's ``approved_at`` and every eligible contribution's
    ``counted_at`` in one transaction - so there is no state in which the work
    is approved and somebody's credit is not.

    Two validators pressing at once: the second waits on the lock, re-reads
    ``APPROVED``, and is refused by the transition matrix. Nothing is counted
    twice.
    """
    await services.work.approve(
        actor=actor, request_id=request_id, work_item_id=work_item_id, note=body.note
    )
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/reopen",
    response_model=WorkItemDetailResponse,
    summary="Send finished work back",
    responses=_WRITE_RESPONSES,
)
async def reopen_work(
    work_item_id: uuid.UUID,
    body: WorkNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_VALIDATE``. The counterpart of approving.

    Nothing is un-counted: the item never reached ``APPROVED``, so no
    contribution ever had a ``counted_at`` to take back.
    """
    await services.work.reopen(
        actor=actor, request_id=request_id, work_item_id=work_item_id, reason=body.note
    )
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/cancel",
    response_model=WorkItemDetailResponse,
    summary="Cancel work",
    responses=_WRITE_RESPONSES,
)
async def cancel_work(
    work_item_id: uuid.UUID,
    body: WorkNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``. **Refused once the work has been validated.**

    Approved work has written ``counted_at`` onto its contributions, and taking
    that back is a correction against a reporting period that may since have
    been closed. Correction semantics belong to the milestone that has a quota
    engine to stay consistent with; a ``400`` here says so.

    Nothing is deleted. The row stays cancelled, with its history, and its
    pending contributions become ``EXCLUDED``.
    """
    await services.work.cancel(
        actor=actor, request_id=request_id, work_item_id=work_item_id, reason=body.note
    )
    return await _detail(services, actor, work_item_id)


# --- Contributors -----------------------------------------------------------


@router.post(
    "/{work_item_id}/contributors",
    response_model=WorkItemDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Put somebody on a job",
    responses=_WRITE_RESPONSES,
)
async def add_contributor(
    work_item_id: uuid.UUID,
    body: AddContributorRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``. Each contributor gets a full unit of credit by default.

    A shoot with three people is one work item and three contributions - never
    one contribution divided three ways, because the department did one shoot
    and each of them did a day's work.
    """
    await services.work.add_contributor(
        actor=actor,
        request_id=request_id,
        work_item_id=work_item_id,
        user_id=body.user_id,
        contribution_role=body.contribution_role,
        credit_weight=body.credit_weight,
    )
    return await _detail(services, actor, work_item_id)


@router.delete(
    "/{work_item_id}/contributors/{contribution_id}",
    response_model=WorkItemDetailResponse,
    summary="Take somebody off a job",
    responses=_WRITE_RESPONSES,
)
async def remove_contributor(
    work_item_id: uuid.UUID,
    contribution_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``. A **counted** contribution is refused.

    That one is a thing that happened, and removing it would delete a fact a
    reporting period may already have counted. The last contributor cannot be
    removed either - work belongs to somebody.
    """
    await services.work.remove_contributor(
        actor=actor,
        request_id=request_id,
        work_item_id=work_item_id,
        contribution_id=contribution_id,
    )
    return await _detail(services, actor, work_item_id)


# --- Metadata ---------------------------------------------------------------


@router.post(
    "/{work_item_id}/deadline",
    response_model=WorkItemDetailResponse,
    summary="Move a deadline",
    responses=_WRITE_RESPONSES,
)
async def change_deadline(
    work_item_id: uuid.UUID,
    body: ChangeDeadlineRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``. Both dates are recorded in the history.

    "Why is this not overdue any more" is the question a moved deadline creates,
    and the old value is the only thing that answers it.
    """
    await services.work.change_deadline(
        actor=actor,
        request_id=request_id,
        work_item_id=work_item_id,
        due_at=body.due_at,
        reason=body.reason,
    )
    return await _detail(services, actor, work_item_id)


@router.post(
    "/{work_item_id}/priority",
    response_model=WorkItemDetailResponse,
    summary="Change priority",
    responses=_WRITE_RESPONSES,
)
async def change_priority(
    work_item_id: uuid.UUID,
    body: ChangePriorityRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_MANAGE``."""
    await services.work.change_priority(
        actor=actor,
        request_id=request_id,
        work_item_id=work_item_id,
        priority=body.priority,
    )
    return await _detail(services, actor, work_item_id)


# --- Evidence ---------------------------------------------------------------


@router.get(
    "/{work_item_id}/evidence",
    response_model=list[WorkEvidenceResponse],
    summary="Evidence on one job",
    responses=_NOT_FOUND,
)
async def list_evidence(
    work_item_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> list[WorkEvidenceResponse]:
    """Same visibility as the item itself."""
    detail = await services.work.detail(actor=actor, work_item_id=work_item_id)
    return [WorkEvidenceResponse.from_row(row) for row in detail.evidence]


@router.post(
    "/{work_item_id}/evidence",
    response_model=WorkItemDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach evidence",
    responses=_WRITE_RESPONSES,
)
async def add_evidence(
    work_item_id: uuid.UUID,
    body: AddEvidenceRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """A contributor's own act. One text, or the legacy label and link.

    MeoBot stores no files either way. The schema has already refused a body
    that is neither shape or both, so the branch here is the only decision.
    """
    if body.text is not None:
        await services.work.add_evidence_text(
            actor=actor,
            request_id=request_id,
            work_item_id=work_item_id,
            text=body.text,
        )
    else:
        assert body.label is not None and body.location is not None
        await services.work.add_evidence(
            actor=actor,
            request_id=request_id,
            work_item_id=work_item_id,
            label=body.label,
            location=body.location,
            note=body.note,
        )
    return await _detail(services, actor, work_item_id)


@router.delete(
    "/{work_item_id}/evidence/{evidence_id}",
    response_model=WorkItemDetailResponse,
    summary="Detach evidence",
    responses=_WRITE_RESPONSES,
)
async def remove_evidence(
    work_item_id: uuid.UUID,
    evidence_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """Refused once the work has been validated - it was approved on this basis."""
    await services.work.remove_evidence(
        actor=actor,
        request_id=request_id,
        work_item_id=work_item_id,
        evidence_id=evidence_id,
    )
    return await _detail(services, actor, work_item_id)


# --- Shared -----------------------------------------------------------------


def _command(
    body: ProposeWorkRequest | AssignWorkRequest | AssignWorkBatchRequest,
) -> CreateWorkCommand:
    """A request body as the application layer's own command.

    Carries no status and no source: the endpoint decides both, so there is no
    body anywhere that can file work as already accepted or claim to have come
    from the content workflow.
    """
    return CreateWorkCommand(
        work_type_id=body.work_type_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        quantity=body.quantity,
        due_at=body.due_at,
        channel_id=body.channel_id,
        contributor_user_ids=tuple(body.contributor_user_ids),
    )


async def work_item_detail(
    services: PrServicesDep, actor: CurrentActorDep, work_item_id: uuid.UUID
) -> WorkItemDetailResponse:
    """Re-read one item after a write. Shared with the maintenance router."""
    return await _detail(services, actor, work_item_id)


async def _detail(
    services: PrServicesDep, actor: CurrentActorDep, work_item_id: uuid.UUID
) -> WorkItemDetailResponse:
    """Re-read one item after a write.

    Every action returns the whole refreshed item rather than the row it
    changed, because what the caller wants to know is *what state the work is in
    now* - and a client that had to fetch it separately would draw one frame of
    the old answer.
    """
    detail = await services.work.detail(actor=actor, work_item_id=work_item_id)
    container = await services.work_results.summary(detail.item)
    results = await services.work_results.results_of(detail.item.id) if container else []
    people: list[uuid.UUID] = []
    if detail.item.subject_user_id is not None:
        people.append(detail.item.subject_user_id)
    for one in results:
        people.append(one.reported_by_user_id)
        if one.counted_by_user_id is not None:
            people.append(one.counted_by_user_id)
        if one.excluded_by_user_id is not None:
            people.append(one.excluded_by_user_id)
    names = await services.work_results.names_for(people) if people else {}
    # Asked only for the two row kinds that have an administrative delete - a
    # legacy content row, and a terminal (cancelled or rejected) row - so an
    # ordinary detail read costs no capability lookup. The two are separate
    # rules and a row may satisfy either; neither reads the other.
    is_legacy = is_legacy_content_work_item(detail.item)
    is_terminal = detail.item.status in TERMINAL_DELETABLE_STATUSES
    may_configure = (is_legacy or is_terminal) and await services.capabilities.allows(
        actor, PrCapability.PR_WORK_CONFIGURE
    )
    # The server's answer to "may this terminal row go right now", with the
    # blocking counts when it may not. Computed only for somebody who could
    # act on it, and not for a row the legacy rule already covers - one
    # delete per panel, and the response says which rule it is.
    admin_delete = (
        await services.work_maintenance.terminal_delete_eligibility(detail.item)
        if may_configure and is_terminal and not is_legacy
        else None
    )
    # Asked only when this actor could act on a pending row: one source read
    # per pending source-derived result, none for a manual one, none for a
    # reader who sees no controls anyway.
    eligibility = (
        await services.work_results.source_eligibility(results)
        if results and detail.can_validate and not detail.is_subject
        else None
    )
    return WorkItemDetailResponse.from_detail(
        detail,
        now=utcnow(),
        container=container,
        results=results,
        result_names=names,
        actor_id=actor.user_id,
        may_configure=may_configure,
        source_eligibility=eligibility,
        admin_delete=admin_delete,
    )
