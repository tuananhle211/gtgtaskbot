"""The five tiles, the phase strip and the per-person counts.

Everything is computed from the same source and the same filters as the
table, so a tile and the rows behind it never disagree. The range defaults
to the current month in the application timezone (design §10.2); the tiles
count what was **placed** in the range, as the Apexmed brief specifies, and
"completed" means finished within that set.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.board.sources import display_names
from meobot.application.board.task_board_service import TaskBoardService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.domain.board.models import (
    BoardQuery,
    DashboardSummary,
    PersonStat,
    Phase,
    TaskCell,
    TaskRow,
)
from meobot.domain.identity.models import Actor
from meobot.domain.orders.deadlines import DeadlineStatus
from meobot.domain.units.models import UnitCode

#: How many rows the dashboard reads to compute its figures. The figures are
#: about a month of one unit's work; two hundred is far above what either
#: unit produces, and the limit is a guard, not a budget.
SAMPLE_LIMIT = 500


def current_month(settings: Settings) -> tuple[date, date]:
    today = utcnow().astimezone(settings.timezone).date()
    first = today.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return first, next_first - timedelta(days=1)


class DashboardService:
    def __init__(self, session: AsyncSession, settings: Settings, board: TaskBoardService) -> None:
        self._session = session
        self._settings = settings
        self._board = board

    async def summary(
        self,
        actor: Actor,
        *,
        unit: str | None,
        date_from: date | None,
        date_to: date | None,
        owner_user_id: uuid.UUID | None = None,
        person_user_id: uuid.UUID | None = None,
    ) -> DashboardSummary:
        if date_from is None or date_to is None:
            default_from, default_to = current_month(self._settings)
            date_from = date_from or default_from
            date_to = date_to or default_to
        codes = await self._board.resolve_units(actor, unit)
        query = BoardQuery(
            date_from=date_from,
            date_to=date_to,
            owner_user_id=owner_user_id,
            person_user_id=person_user_id,
            limit=SAMPLE_LIMIT,
            offset=0,
        )
        page = await self._board.page(actor, unit=unit, query=query)
        rows = page.rows
        total = page.total
        completed = sum(1 for row in rows if row.phase is Phase.DONE)
        pending = sum(1 for row in rows if row.phase in (Phase.REVIEW, Phase.FINAL_REVIEW))
        urgent = sum(1 for row in rows if row.urgent)
        overdue = sum(1 for row in rows if row.deadline_status == _OVERDUE)
        by_phase: dict[Phase, int] = dict.fromkeys(Phase, 0)
        for row in rows:
            by_phase[row.phase] += 1
        return DashboardSummary(
            unit=codes[0] if len(codes) == 1 else UnitCode.PR,
            date_from=date_from,
            date_to=date_to,
            total=total,
            completed=completed,
            pending_review=pending,
            urgent=urgent,
            progress_percent=None if total == 0 else round(completed * 100 / total),
            overdue=overdue,
            by_phase=by_phase,
            by_owner=await self._by_owner(rows),
            by_worker=await self._by_worker(rows),
        )

    async def _by_owner(self, rows: tuple[TaskRow, ...]) -> tuple[PersonStat, ...]:
        stats: dict[uuid.UUID, list[int]] = {}
        for row in rows:
            item = stats.setdefault(row.owner_user_id, [0, 0, 0])
            item[0] += 1
            if row.phase is Phase.DONE:
                item[1] += 1
            if _row_late(row):
                item[2] += 1
        names = await display_names(self._session, set(stats))
        return tuple(
            PersonStat(user_id=user_id, name=names.get(user_id, ""), opened=a, done=b, late=c)
            for user_id, (a, b, c) in sorted(stats.items(), key=lambda item: -item[1][0])
        )

    async def _by_worker(self, rows: tuple[TaskRow, ...]) -> tuple[PersonStat, ...]:
        """Per person holding a cell: cells held, cells finished, cells late -
        finished after their deadline or past it now (ORD, 0053). Names are
        what the cells carry, so no second lookup."""
        stats: dict[str, list[int]] = {}
        for row in rows:
            for cell in row.cells:
                if not cell.person_name:
                    continue
                item = stats.setdefault(cell.person_name, [0, 0, 0])
                item[0] += 1
                if cell.status in ("HOAN_THANH", "DONE"):
                    item[1] += 1
                if _cell_late(row, cell):
                    item[2] += 1
        return tuple(
            PersonStat(
                user_id=uuid.uuid5(uuid.NAMESPACE_URL, name), name=name, opened=a, done=b, late=c
            )
            for name, (a, b, c) in sorted(stats.items(), key=lambda item: -item[1][0])
        )


def _row_late(row: TaskRow) -> bool:
    """ORD (0053): past the orderer's wish. PR keeps its old meaning: urgent."""
    if row.unit is UnitCode.ADS:
        return row.deadline_status in _LATE
    return row.urgent


def _cell_late(row: TaskRow, cell: TaskCell) -> bool:
    """ORD: the step finished after its deadline or is past it now. PR: the
    held step of an urgent row, as before."""
    if row.unit is UnitCode.ADS:
        return cell.key != "FINAL" and cell.deadline_status in _LATE
    return row.urgent and cell.is_current


_OVERDUE = DeadlineStatus.OVERDUE.value
#: Past a deadline: still open after it, or finished after it.
_LATE = frozenset({DeadlineStatus.OVERDUE.value, DeadlineStatus.MISSED.value})


__all__ = ["SAMPLE_LIMIT", "DashboardService", "current_month"]
