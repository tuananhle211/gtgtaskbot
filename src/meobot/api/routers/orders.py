"""Ads orders: create, read, and every action of the pipeline.

Mounted behind the Ads unit gate (``main.py``): a caller not tagged Ads gets
a 404 before any of this runs. Inside, every action is a ``POST`` carrying
the ``version`` the caller last saw, and every one returns the full detail,
so a screen never needs a second request to redraw. Authority is decided in
:mod:`meobot.domain.orders.pipeline` through the command service, never here.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    LLMProviderDep,
    RequestIdDep,
    get_app_settings,
    get_session,
)
from meobot.api.schemas.orders import (
    ApproveFinalRequest,
    ApproverPerson,
    ApproversResponse,
    AssignRequest,
    BirthdayOptionsResponse,
    BirthdayOrderResponse,
    CreateBirthdayOrderRequest,
    CreateOrderRequest,
    NoteRequest,
    OrderActionResponse,
    OrderDetailResponse,
    PlanRequest,
    PriorityRequest,
    ResubmitOrderRequest,
    ReturnNodeRequest,
    ScriptCheckRequest,
    ScriptCheckResponse,
    SubmitWorkRequest,
    UpdateBirthdayOrderRequest,
    VersionedRequest,
)
from meobot.application.board.holders import ads_approvers
from meobot.application.orders.command_service import (
    BirthdayCommand,
    CreateOrderCommand,
    NodePlan,
    OrderEdit,
)
from meobot.application.orders.services import OrderServices, build_order_services
from meobot.core.config import Settings
from meobot.core.errors import ValidationError
from meobot.domain.orders.design_codes import DesignPick
from meobot.domain.orders.models import (
    OrderNodeType,
    OrderScriptSource,
    OrderVideoType,
    process_code,
)
from meobot.domain.orders.permissions import AdsPermission

router = APIRouter(prefix="/api/orders", tags=["orders"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such order, or not visible to you."}
}
_ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    403: {"description": "The stage allows this action, but not for you."},
    409: {"description": "The order moved on (stale version), or the stage forbids it."},
    422: {"description": "A required field is missing or invalid."},
}


def get_order_services(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    llm: LLMProviderDep,
) -> OrderServices:
    return build_order_services(session, settings, llm=llm)


ServicesDep = Annotated[OrderServices, Depends(get_order_services)]


def _video_type(value: str) -> OrderVideoType:
    try:
        return OrderVideoType(value.strip().upper())
    except ValueError as error:
        raise ValidationError(
            f"Loại video không hợp lệ: {value!r}.",
            details={"reason": "invalid_video_type", "field": "video_type", "value": value},
        ) from error


def _process(video_type: str | None, process: list[str] | None) -> OrderVideoType:
    """The process code from either form of the create body.

    ``process`` lists the ticked production nodes in any order; ``video_type``
    is the code. Nothing ticked is ``process_empty``; both sent and different
    is ``process_mismatch``.
    """
    from_list: OrderVideoType | None = None
    if process is not None:
        try:
            nodes = [OrderNodeType(item.strip().upper()) for item in process]
            from_list = process_code(nodes)
        except ValueError as error:
            raise ValidationError(
                "Quy trình chỉ gồm Biên kịch, Design và Dựng.",
                details={"reason": "invalid_process", "field": "process", "value": process},
            ) from error
        if from_list is None:
            raise ValidationError(
                "Cần chọn ít nhất một bước: Biên kịch, Design hoặc Dựng.",
                details={"reason": "process_empty", "field": "process"},
            )
    if video_type is None or not video_type.strip():
        if from_list is None:
            raise ValidationError(
                "Cần chọn ít nhất một bước: Biên kịch, Design hoặc Dựng.",
                details={"reason": "process_empty", "field": "process"},
            )
        return from_list
    code = _video_type(video_type)
    if from_list is not None and from_list is not code:
        raise ValidationError(
            "Quy trình và loại video gửi lên không khớp nhau.",
            details={
                "reason": "process_mismatch",
                "field": "process",
                "video_type": code.value,
                "process": from_list.value,
            },
        )
    return code


def _script_source(value: str | None) -> OrderScriptSource | None:
    if value is None or not value.strip():
        return None
    try:
        return OrderScriptSource(value.strip().upper())
    except ValueError as error:
        raise ValidationError(
            f"Source kịch bản không hợp lệ: {value!r}.",
            details={"reason": "invalid_script_source", "field": "script_source", "value": value},
        ) from error


def _preassigned(value: dict[str, uuid.UUID] | None) -> dict[OrderNodeType, uuid.UUID]:
    if not value:
        return {}
    parsed: dict[OrderNodeType, uuid.UUID] = {}
    for key, user_id in value.items():
        try:
            parsed[OrderNodeType(key.strip().upper())] = user_id
        except ValueError as error:
            raise ValidationError(
                f"Công đoạn không hợp lệ: {key!r}.",
                details={"reason": "invalid_node_type", "field": "preassigned", "value": key},
            ) from error
    return parsed


async def _detail(services: OrderServices, actor: Any, order_id: uuid.UUID) -> OrderDetailResponse:
    return OrderDetailResponse.from_domain(await services.queries.detail(actor, str(order_id)))


@router.post("/script-check", response_model=ScriptCheckResponse)
async def check_script(
    body: ScriptCheckRequest, actor: CurrentActorDep, services: ServicesDep
) -> ScriptCheckResponse:
    """ "AI duyệt kịch bản" before sending: the same reading the order gets
    when it is sent (a pass is remembered for the same title and script).
    403-like 422 ``not_an_orderer`` for somebody who may not order."""
    verdict = await services.commands.check_script(
        actor=actor, title=body.title, script=body.order_content
    )
    if verdict is None:
        return ScriptCheckResponse(
            enabled=False,
            reviewed=False,
            passed=True,
            message="AI duyệt kịch bản đang tắt: bạn gửi order được ngay.",
        )
    if not verdict.reviewed:
        return ScriptCheckResponse(
            enabled=True,
            reviewed=False,
            passed=False,
            problem=verdict.problem,
            message=(
                f"AI chưa duyệt được ({verdict.problem or 'lỗi kết nối'}). Bạn vẫn gửi được"
                " order, Trưởng phòng sẽ duyệt kỹ kịch bản."
            ),
        )
    if verdict.passed:
        return ScriptCheckResponse(
            enabled=True,
            reviewed=True,
            passed=True,
            message="AI duyệt kịch bản: đạt. Bạn có thể gửi order.",
        )
    return ScriptCheckResponse(
        enabled=True,
        reviewed=True,
        passed=False,
        violations=[violation.to_json() for violation in verdict.violations],
        revised_script=verdict.revised_script,
        note=verdict.note,
        message=(
            f"AI duyệt kịch bản: chưa đạt ({len(verdict.violations)} lỗi). Sửa các chỗ được"
            " chỉ ra rồi duyệt lại."
        ),
    )


@router.post(
    "",
    response_model=OrderDetailResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ACTION_RESPONSES,
)
async def create_order(
    body: CreateOrderRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    """Create and submit in one step. The code is allocated here."""
    order = await services.commands.create(
        actor=actor,
        request_id=request_id,
        command=CreateOrderCommand(
            title=body.title,
            video_type=_process(body.video_type, body.process),
            order_content=body.order_content,
            script_source=_script_source(body.script_source),
            design_link=body.design_link,
            reference_link=body.reference_link,
            source_link=body.source_link,
            preassigned=_preassigned(body.preassigned),
            video_kind_id=body.video_kind_id,
            platform_id=body.platform_id,
            duration_id=body.duration_id,
            desired_deadline_at=body.desired_deadline_at,
            approver_user_id=body.approver_user_id,
            note=body.note,
        ),
    )
    return await _detail(services, actor, order.id)


# --- birthday orders (0058): declared before "/{ref}" so the path is not a ref ---


@router.post(
    "/birthday",
    response_model=BirthdayOrderResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ACTION_RESPONSES,
)
async def create_birthday_order(
    body: CreateBirthdayOrderRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> BirthdayOrderResponse:
    """ "Order sinh nhật": straight to the Design step - no approval before
    it, no final review after. Whoever orders in ORD, or the Design team."""
    order = await services.commands.create_birthday(
        actor=actor,
        request_id=request_id,
        command=BirthdayCommand(
            full_name=body.full_name,
            branch=body.branch,
            gender=body.gender,
            birthday_on=body.birthday_on,
            reference_link=body.reference_link,
            reference_links=body.reference_links,
            note=body.note,
        ),
    )
    return BirthdayOrderResponse(id=order.id, code=order.code, version=order.version)


@router.get("/approvers", response_model=ApproversResponse)
async def approvers(actor: CurrentActorDep, services: ServicesDep) -> ApproversResponse:
    """Who approves the caller's next order, and whom they may choose."""
    scoped = await services.queries.scoped(actor)
    routes = await ads_approvers(services.session, scoped.scope.unit_id)
    own_gate = scoped.scope.context.permissions.holds(AdsPermission.ORDER_APPROVE)
    default = [] if own_gate else list(routes.default_approvers(actor.user_id))
    return ApproversResponse(
        own_gate=own_gate,
        required=not own_gate and not default,
        default=[ApproverPerson(user_id=person.user_id, name=person.name) for person in default],
        candidates=[
            ApproverPerson(user_id=person.user_id, name=person.name)
            for person in sorted(routes.head, key=lambda item: item.name)
            if person.user_id != actor.user_id
        ],
    )


@router.get("/birthday/options", response_model=BirthdayOptionsResponse)
async def birthday_options(
    actor: CurrentActorDep, services: ServicesDep
) -> BirthdayOptionsResponse:
    """The branches already on the birthday orders this person may see."""
    return BirthdayOptionsResponse(branches=await services.queries.birthday_branches(actor))


@router.patch(
    "/{order_id}/birthday", response_model=BirthdayOrderResponse, responses=_ACTION_RESPONSES
)
async def update_birthday_order(
    order_id: uuid.UUID,
    body: UpdateBirthdayOrderRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> BirthdayOrderResponse:
    """Correct a birthday order: its creator, the Design Leader, OWNER/ADMIN."""
    order = await services.commands.edit_birthday(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        command=BirthdayCommand(
            full_name=body.full_name,
            branch=body.branch,
            gender=body.gender,
            birthday_on=body.birthday_on,
            reference_link=body.reference_link,
            reference_links=body.reference_links,
            note=body.note,
        ),
    )
    return BirthdayOrderResponse(id=order.id, code=order.code, version=order.version)


@router.get("/{ref}", response_model=OrderDetailResponse, responses=_NOT_FOUND)
async def order_detail(
    ref: str, actor: CurrentActorDep, services: ServicesDep
) -> OrderDetailResponse:
    """By id or by code (``TUAN-D-261003-01``), within the caller's scope."""
    return OrderDetailResponse.from_domain(await services.queries.detail(actor, ref))


@router.get(
    "/{ref}/available-actions", response_model=list[OrderActionResponse], responses=_NOT_FOUND
)
async def available_actions(
    ref: str, actor: CurrentActorDep, services: ServicesDep
) -> list[OrderActionResponse]:
    detail = await services.queries.detail(actor, ref)
    return OrderDetailResponse.from_domain(detail).available_actions


@router.post(
    "/{order_id}/resubmit", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES
)
async def resubmit_order(
    order_id: uuid.UUID,
    body: ResubmitOrderRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    fields = body.model_fields_set
    await services.commands.resubmit(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        edit=OrderEdit(
            title=body.title,
            order_content=body.order_content,
            script_source=_script_source(body.script_source) if "script_source" in fields else None,
            design_link=body.design_link,
            reference_link=body.reference_link,
            source_link=body.source_link,
            preassigned=None if body.preassigned is None else _preassigned(body.preassigned),
            video_kind_id=body.video_kind_id,
            platform_id=body.platform_id,
            duration_id=body.duration_id,
            desired_deadline_at=body.desired_deadline_at,
            approver_user_id=body.approver_user_id,
            note=body.note,
        ),
    )
    return await _detail(services, actor, order_id)


@router.post("/{order_id}/approve", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES)
async def approve_order(
    order_id: uuid.UUID,
    body: VersionedRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.approve_order(
        actor=actor, request_id=request_id, order_id=order_id, expected_version=body.version
    )
    return await _detail(services, actor, order_id)


@router.post("/{order_id}/return", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES)
async def return_order(
    order_id: uuid.UUID,
    body: NoteRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.return_order(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        reason=body.note or "",
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/nodes/{node_id}/assign",
    response_model=OrderDetailResponse,
    responses=_ACTION_RESPONSES,
)
async def assign_node(
    order_id: uuid.UUID,
    node_id: uuid.UUID,
    body: AssignRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.assign(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        node_id=node_id,
        expected_version=body.version,
        assignee_user_id=body.assignee_user_id,
        plan=NodePlan(tokens=body.tokens, deadline_at=body.deadline_at),
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/nodes/{node_id}/accept",
    response_model=OrderDetailResponse,
    responses=_ACTION_RESPONSES,
)
async def accept_node(
    order_id: uuid.UUID,
    node_id: uuid.UUID,
    body: PlanRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.accept(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        node_id=node_id,
        expected_version=body.version,
        plan=NodePlan(tokens=body.tokens, deadline_at=body.deadline_at),
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/nodes/{node_id}/plan",
    response_model=OrderDetailResponse,
    responses=_ACTION_RESPONSES,
)
async def set_node_plan(
    order_id: uuid.UUID,
    node_id: uuid.UUID,
    body: PlanRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    """ "Sửa token/deadline" (0053)."""
    await services.commands.set_node_plan(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        node_id=node_id,
        expected_version=body.version,
        plan=NodePlan(tokens=body.tokens, deadline_at=body.deadline_at),
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/nodes/{node_id}/submit",
    response_model=OrderDetailResponse,
    responses=_ACTION_RESPONSES,
)
async def submit_work(
    order_id: uuid.UUID,
    node_id: uuid.UUID,
    body: SubmitWorkRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.submit_work(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        node_id=node_id,
        expected_version=body.version,
        link=body.link,
        script_text=body.script_text,
        note=body.note,
        design_picks=(
            None
            if body.design_codes is None
            else tuple(
                DesignPick(code_id=item.code_id, quantity=item.quantity)
                for item in body.design_codes
            )
        ),
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/nodes/{node_id}/approve",
    response_model=OrderDetailResponse,
    responses=_ACTION_RESPONSES,
)
async def approve_node(
    order_id: uuid.UUID,
    node_id: uuid.UUID,
    body: NoteRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.approve_node(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        node_id=node_id,
        expected_version=body.version,
        note=body.note,
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/nodes/{node_id}/return",
    response_model=OrderDetailResponse,
    responses=_ACTION_RESPONSES,
)
async def return_node(
    order_id: uuid.UUID,
    node_id: uuid.UUID,
    body: ReturnNodeRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.return_node(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        node_id=node_id,
        expected_version=body.version,
        note=body.note or "",
        plan=NodePlan(tokens=body.tokens, deadline_at=body.deadline_at),
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/video/approve", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES
)
async def approve_video(
    order_id: uuid.UUID,
    body: VersionedRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.approve_video(
        actor=actor, request_id=request_id, order_id=order_id, expected_version=body.version
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/video/return", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES
)
async def return_video(
    order_id: uuid.UUID,
    body: NoteRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.return_video(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        note=body.note or "",
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/final/approve", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES
)
async def approve_final(
    order_id: uuid.UUID,
    body: ApproveFinalRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.approve_final(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        product_link=body.product_link,
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/final/return", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES
)
async def return_final(
    order_id: uuid.UUID,
    body: NoteRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.return_final(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        note=body.note or "",
    )
    return await _detail(services, actor, order_id)


@router.post(
    "/{order_id}/priority", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES
)
async def set_priority(
    order_id: uuid.UUID,
    body: PriorityRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.set_priority(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        is_priority=body.is_priority,
    )
    return await _detail(services, actor, order_id)


@router.post("/{order_id}/cancel", response_model=OrderDetailResponse, responses=_ACTION_RESPONSES)
async def cancel_order(
    order_id: uuid.UUID,
    body: NoteRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> OrderDetailResponse:
    await services.commands.cancel(
        actor=actor,
        request_id=request_id,
        order_id=order_id,
        expected_version=body.version,
        reason=body.note or "",
    )
    return await _detail(services, actor, order_id)
