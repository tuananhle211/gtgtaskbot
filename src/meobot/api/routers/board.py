"""The shared board: ``/api/board/tasks`` for the table, ``/api/board/dashboard``
for the tiles. Both take ``unit=PR|ADS|ALL`` and the same filters, and the
unit named must be one the caller is tagged into (``ALL``: the OWNER and the
ADMIN)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, get_app_settings, get_session
from meobot.api.schemas.board import BoardCountsResponse, DashboardResponse, TaskPageResponse
from meobot.application.board.dashboard_service import DashboardService
from meobot.application.board.task_board_service import ALL_UNITS, TaskBoardService
from meobot.application.pr_services import build_pr_services
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings
from meobot.core.errors import ValidationError
from meobot.domain.board.models import DESIGN_STATES, BoardQuery, Phase
from meobot.domain.orders.models import OrderCategory
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


#: The orders ``/api/board/tasks`` accepts (``order=``).
ORDER_TODO_FIRST = "todo_first"
#: ORD (0058): the nearest coming birthday first - the birthday board.
ORDER_BIRTHDAY = "birthday"
_ORDERS = frozenset({"", "default", ORDER_TODO_FIRST, ORDER_BIRTHDAY})


def _order(value: str | None) -> str:
    """The order asked for (``""`` = the default). An unknown one is a 422."""
    cleaned = (value or "").strip().lower()
    if cleaned not in _ORDERS:
        raise ValidationError(
            "Thứ tự sắp xếp không hợp lệ.",
            details={"reason": "invalid_order", "field": "order", "value": value},
        )
    return cleaned


def _category(value: str | None) -> str:
    """The ORD order category a board lists: ``STANDARD`` ("Order tổng")
    unless the birthday board asks for ``BIRTHDAY``. Unknown → 422."""
    cleaned = (value or "").strip().upper() or OrderCategory.STANDARD.value
    try:
        return OrderCategory(cleaned).value
    except ValueError as error:
        raise ValidationError(
            "Loại order không hợp lệ.",
            details={"reason": "invalid_category", "field": "category", "value": value},
        ) from error


def _design_status(value: str | None) -> str | None:
    cleaned = (value or "").strip().upper()
    if not cleaned:
        return None
    if cleaned not in DESIGN_STATES:
        raise ValidationError(
            "Trạng thái thiết kế không hợp lệ.",
            details={"reason": "invalid_design_status", "field": "design_status", "value": value},
        )
    return cleaned


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
    overdue: Annotated[bool, Query()] = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
    order: Annotated[str | None, Query(max_length=40)] = None,
    category: Annotated[str | None, Query(max_length=20)] = None,
    design_status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskPageResponse:
    """``order=todo_first``: the rows waiting on the caller first ("Cần làm",
    the ``awaiting_me`` rule), then the rest of the filter; each part keeps the
    usual priority-then-newest order and paging continues across both.

    ``category`` (ORD): ``STANDARD`` by default - "Order tổng" does not list
    the birthday orders; ``BIRTHDAY`` is the birthday board, usually with
    ``order=birthday`` (the nearest coming birthday first).

    ``design_status`` (ORD): where the Design step stands, as the Design
    team's table shows it - one of ``design_states`` in the response."""
    codes = await board.resolve_units(actor, unit)
    ordering = _order(order)
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
        overdue=overdue,
        search=q or None,
        todo_first=ordering == ORDER_TODO_FIRST,
        birthday_first=ordering == ORDER_BIRTHDAY,
        category=_category(category),
        design_status=_design_status(design_status),
        limit=limit,
        offset=offset,
    )
    page = await board.page(actor, unit=unit, query=query)
    return TaskPageResponse.from_domain(
        _unit_label(unit, tuple(code.value for code in codes)),
        page,
        ads_steps=codes == (UnitCode.ADS,),
    )


@router.get("/counts", response_model=BoardCountsResponse)
async def board_counts(
    actor: CurrentActorDep,
    board: BoardDep,
    unit: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query(max_length=20)] = None,
) -> BoardCountsResponse:
    """The numbers behind the task table's quick filters - "Task của tôi (8)":
    each is the total that filter alone would list in this unit, on the board
    of this ``category`` (as on ``/tasks``)."""
    await board.resolve_units(actor, unit)
    chosen = _category(category)

    async def count(query: BoardQuery) -> int:
        return (await board.page(actor, unit=unit, query=query)).total

    return BoardCountsResponse(
        mine=await count(BoardQuery(mine=True, limit=1, category=chosen)),
        awaiting_me=await count(BoardQuery(awaiting_me=True, limit=1, category=chosen)),
        priority=await count(BoardQuery(priority=True, limit=1, category=chosen)),
        urgent=await count(BoardQuery(urgent=True, limit=1, category=chosen)),
        overdue=await count(BoardQuery(overdue=True, limit=1, category=chosen)),
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
