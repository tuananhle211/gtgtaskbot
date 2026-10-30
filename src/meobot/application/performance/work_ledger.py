"""Writing ``work_points`` (0085): one row per ``source_key``, rewritten in
place, never doubled. The department a row counts for is the person's PR
department when they sit in one (PR and seeding work), else their first
seat - fixed when written, like ``order_node_points``."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.seats import load_seats
from meobot.db.models.work_points import WorkPoint
from meobot.domain.orders.design_codes import DesignItem, items_to_json, total_points
from meobot.domain.org.models import DepartmentFunction, Seat


def pr_department(seats: Iterable[Seat]) -> uuid.UUID | None:
    ordered = list(seats)
    for seat in ordered:
        if seat.function is DepartmentFunction.PR:
            return seat.department_id
    return ordered[0].department_id if ordered else None


async def put_points(
    session: AsyncSession,
    *,
    source: str,
    source_key: str,
    user_id: uuid.UUID,
    phase: str,
    items: Sequence[DesignItem],
    earned_at: datetime,
    ref_code: str | None = None,
    ref_title: str | None = None,
    ref_link: str | None = None,
) -> WorkPoint:
    """Write (or rewrite) the row ``source_key``."""
    row = await session.scalar(select(WorkPoint).where(WorkPoint.source_key == source_key))
    seats = await load_seats(session, [user_id])
    department_id = pr_department(seats.get(user_id, ()))
    values = {
        "source": source,
        "user_id": user_id,
        "phase": phase,
        "items": items_to_json(items),
        "points": total_points(items),
        "earned_at": earned_at,
        "ref_code": ref_code,
        "ref_title": ref_title,
        "ref_link": ref_link,
    }
    if row is None:
        row = WorkPoint(source_key=source_key, department_id=department_id, **values)
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    await session.flush()
    return row


async def drop_points(session: AsyncSession, source_key: str) -> None:
    await session.execute(delete(WorkPoint).where(WorkPoint.source_key == source_key))


__all__ = ["drop_points", "pr_department", "put_points"]
