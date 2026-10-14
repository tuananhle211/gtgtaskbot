"""The unified task page: ``GET /api/tasks/{ref}`` and ``POST /api/tasks/{ref}/actions``.

One route pair for both units, so it is mounted without a unit gate: the wall
is checked inside, per task - a person outside the task's unit gets the same
404 the unit's own routes would give, and the OWNER sees both. Every write
returns the full detail, like ``/api/orders``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    LLMProviderDep,
    RequestIdDep,
    get_app_settings,
    get_session,
)
from meobot.api.schemas.tasks import TaskActionRequest, TaskDetailResponse
from meobot.application.audit_service import AuditService
from meobot.application.orders.services import build_order_services
from meobot.application.pr_services import build_pr_services
from meobot.application.tasks.action_service import TaskActionCommand, TaskActionService
from meobot.application.tasks.delete_service import TaskDeleteService
from meobot.application.tasks.detail_service import TaskDetailService
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings
from meobot.domain.orders.design_codes import DesignPick

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such task, or not visible to you."}
}
_ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    403: {"description": "The action exists here, but not for you."},
    409: {"description": "The task moved on (stale version), or the stage forbids it."},
    422: {"description": "Unknown action key, or a required input is missing."},
}


class TaskServices:
    def __init__(
        self, detail: TaskDetailService, actions: TaskActionService, deletes: TaskDeleteService
    ) -> None:
        self.detail = detail
        self.actions = actions
        self.deletes = deletes


def get_task_services(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    llm: LLMProviderDep,
) -> TaskServices:
    pr = build_pr_services(session, settings)
    # The provider reads an order's script when "Gửi lại order" is pressed here.
    orders = build_order_services(session, settings, pr=pr, llm=llm)
    detail = TaskDetailService(
        session, settings, directory=UnitDirectoryService(session), pr=pr, orders=orders
    )
    return TaskServices(
        detail,
        TaskActionService(detail=detail, pr=pr, orders=orders),
        TaskDeleteService(session, detail=detail, pr=pr, audit=AuditService(session)),
    )


ServicesDep = Annotated[TaskServices, Depends(get_task_services)]


@router.get("/{ref}", response_model=TaskDetailResponse, responses=_NOT_FOUND)
async def task_detail(
    ref: str, actor: CurrentActorDep, services: ServicesDep
) -> TaskDetailResponse:
    """By task id or code, or by the PR content / order id behind it."""
    return TaskDetailResponse.from_domain(await services.detail.detail(actor, ref))


@router.delete(
    "/{ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_NOT_FOUND, 403: {"description": "task_delete_forbidden - the OWNER only."}},
)
async def delete_task(
    ref: str, actor: CurrentActorDep, request_id: RequestIdDep, services: ServicesDep
) -> Response:
    """*Xoá task.* The OWNER only, for good: the order (or, by PR's own rules,
    the content item) and everything hanging off it."""
    await services.deletes.delete(actor=actor, request_id=request_id, ref=ref)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{ref}/actions", response_model=TaskDetailResponse, responses=_ACTION_RESPONSES)
async def task_action(
    ref: str,
    body: TaskActionRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    services: ServicesDep,
) -> TaskDetailResponse:
    """Run one action the GET offered, by its key, against the version on screen."""
    task = await services.actions.perform(
        actor=actor,
        request_id=request_id,
        ref=ref,
        command=TaskActionCommand(
            key=body.key,
            version=body.version,
            note=body.note,
            link=body.link,
            text=body.text,
            assignee_user_id=body.assignee_user_id,
            tokens=body.tokens,
            deadline_at=body.deadline_at,
            design_picks=(
                None
                if body.design_codes is None
                else tuple(
                    DesignPick(code_id=item.code_id, quantity=item.quantity)
                    for item in body.design_codes
                )
            ),
        ),
    )
    return TaskDetailResponse.from_domain(await services.detail.detail(actor, str(task.id)))
