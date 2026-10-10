"""The unified task page: ``GET /api/tasks/{ref}`` and ``POST /api/tasks/{ref}/actions``.

One route pair for both units, so it is mounted without a unit gate: the wall
is checked inside, per task - a person outside the task's unit gets the same
404 the unit's own routes would give, and the OWNER sees both. Every write
returns the full detail, like ``/api/orders``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, get_app_settings, get_session
from meobot.api.schemas.tasks import TaskActionRequest, TaskDetailResponse
from meobot.application.orders.services import build_order_services
from meobot.application.pr_services import build_pr_services
from meobot.application.tasks.action_service import TaskActionCommand, TaskActionService
from meobot.application.tasks.detail_service import TaskDetailService
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings

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
    def __init__(self, detail: TaskDetailService, actions: TaskActionService) -> None:
        self.detail = detail
        self.actions = actions


def get_task_services(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TaskServices:
    pr = build_pr_services(session, settings)
    orders = build_order_services(session, settings, pr=pr)
    detail = TaskDetailService(
        session, settings, directory=UnitDirectoryService(session), pr=pr, orders=orders
    )
    return TaskServices(detail, TaskActionService(detail=detail, pr=pr, orders=orders))


ServicesDep = Annotated[TaskServices, Depends(get_task_services)]


@router.get("/{ref}", response_model=TaskDetailResponse, responses=_NOT_FOUND)
async def task_detail(
    ref: str, actor: CurrentActorDep, services: ServicesDep
) -> TaskDetailResponse:
    """By task id or code, or by the PR content / order id behind it."""
    return TaskDetailResponse.from_domain(await services.detail.detail(actor, ref))


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
        ),
    )
    return TaskDetailResponse.from_domain(await services.detail.detail(actor, str(task.id)))
