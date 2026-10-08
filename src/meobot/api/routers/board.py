"""The shared board: ``/api/board/tasks`` for the table, ``/api/board/dashboard``
for the tiles. Both take ``unit=PR|ADS|ALL`` and the same filters, and the
unit named must be one the caller is tagged into (``ALL``: the OWNER)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, get_app_settings, get_session
from meobot.api.schemas.board import DashboardResponse, TaskPageResponse
from meobot.application.board.dashboard_service import DashboardService
from meobot.application.board.task_board_service import ALL_UNITS, TaskBoardService
from meobot.application.pr_services import build_pr_services
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings
from meobot.domain.board.models import BoardQuery, Phase
from meobot.domain.units.errors import UnitNotFoundError
from meobot.domain.units.models import UnitCode

router = APIRouter(prefix="/api/board", tags=["board"])

_MAX_LIMIT = 200


def get_board(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TaskBoardService:
    return TaskBoardService(
        session,
        settings,
        directory=UnitDirectoryService(session),
        pr=build_pr_services(session, settings),
    )


def get_dashboard(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    board: Annotated[TaskBoardService, Depends(get_board)],
) -> DashboardService:
    return DashboardService(session, settings, board)


BoardDep = Annotated[TaskBoardService, Depends(get_board)]
DashboardDep = Annotated[DashboardService, Depends(get_dashboard)]


def _phase(value: str | None) -> Phase | None:
    if value is None or not value.strip():
        return None
    try:
        return Phase(value.strip().upper())
    except ValueError as error:
        raise UnitNotFoundError(
            "Không tìm thấy.", details={"reason": "unknown_phase", "value": value}
        ) from error


def _unit_label(unit: str | None, board_units: tuple[str, ...]) -> str:
    if unit and unit.strip().upper() == ALL_UNITS:
        return ALL_UNITS
    return board_units[0]


@router.get("/tasks", response_model=TaskPageResponse)
async def board_tasks(
    actor: CurrentActorDep,
    board: BoardDep,
    unit: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    phase: Annotated[str | None, Query()] = None,
    step: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    video_kind_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    owner: Annotated[uuid.UUID | None, Query()] = None,
    assignee: Annotated[uuid.UUID | None, Query()] = None,
    person: Annotated[uuid.UUID | None, Query()] = None,
    mine: Annotated[bool, Query()] = False,
    awaiting_me: Annotated[bool, Query()] = False,
    priority: Annotated[bool, Query()] = False,
    urgent: Annotated[bool, Query()] = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskPageResponse:
    codes = await board.resolve_units(actor, unit)
    query = BoardQuery(
        date_from=date_from,
        date_to=date_to,
        phase=_phase(phase),
        step=(step or "").strip().upper() or None,
        kind=kind or None,
        video_kind_id=video_kind_id,
        status=status or None,
        owner_user_id=owner,
        assignee_user_id=assignee,
        person_user_id=person,
        mine=mine,
        awaiting_me=awaiting_me,
        priority=priority,
        urgent=urgent,
        search=q or None,
        limit=limit,
        offset=offset,
    )
    page = await board.page(actor, unit=unit, query=query)
    return TaskPageResponse.from_domain(
        _unit_label(unit, tuple(code.value for code in codes)),
        page,
        ads_steps=codes == (UnitCode.ADS,),
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def board_dashboard(
    actor: CurrentActorDep,
    board: BoardDep,
    dashboard: DashboardDep,
    unit: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    owner: Annotated[uuid.UUID | None, Query()] = None,
    person: Annotated[uuid.UUID | None, Query()] = None,
) -> DashboardResponse:
    codes = await board.resolve_units(actor, unit)
    summary = await dashboard.summary(
        actor,
        unit=unit,
        date_from=date_from,
        date_to=date_to,
        owner_user_id=owner,
        person_user_id=person,
    )
    return DashboardResponse.from_domain(
        _unit_label(unit, tuple(code.value for code in codes)), summary
    )
