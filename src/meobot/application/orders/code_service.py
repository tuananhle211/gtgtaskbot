"""``TUAN-D-261003-01``: the order code, allocated once and kept for life.

The number is the order's place in the marketer's day, so the counter is
keyed on (unit, member code, day). The day is the calendar day in the
application timezone, the same day the code prints.

Gap-free and race-safe on PostgreSQL by the same means as
:class:`~meobot.application.pr_code_service.PrCodeService`: one atomic
``INSERT … ON CONFLICT DO UPDATE … RETURNING``, whose row lock is held until
the transaction commits. The offline path (SQLite, the unit suite) is a
locked read then an update - correct on a single writer, and that is all the
offline suite needs. Neither path reads a ``MAX`` or counts rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_support import supports_row_locks
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.order import OrderCodeCounter
from meobot.domain.orders.errors import OrderValidationError
from meobot.domain.orders.models import OrderVideoType
from meobot.domain.orders.pipeline import format_order_code

logger = get_logger(__name__)


class OrderCodeService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def allocate(
        self,
        *,
        unit_id: uuid.UUID,
        member_code: str | None,
        video_type: OrderVideoType,
        at: datetime,
    ) -> str:
        """The next code for this marketer today. Must run on the order's session."""
        if not member_code:
            raise OrderValidationError(
                "Bạn chưa có mã thành viên trong luồng ORD nên chưa tạo được order. "
                "Nhờ Trưởng phòng cấp mã.",
                details={"reason": "member_code_missing", "field": "member_code"},
            )
        local = at.astimezone(self._settings.timezone)
        number = await self._next_number(unit_id, member_code, local)
        code = format_order_code(member_code, video_type, local, number)
        logger.info("order_code_allocated", extra={"order_code": code})
        return code

    async def _next_number(self, unit_id: uuid.UUID, member_code: str, local: datetime) -> int:
        day = local.date()
        if supports_row_locks(self._session):
            statement = (
                pg_insert(OrderCodeCounter)
                .values(
                    id=uuid.uuid4(), unit_id=unit_id, member_code=member_code, day=day, last_no=1
                )
                .on_conflict_do_update(
                    index_elements=[
                        OrderCodeCounter.unit_id,
                        OrderCodeCounter.member_code,
                        OrderCodeCounter.day,
                    ],
                    set_={"last_no": OrderCodeCounter.last_no + 1},
                )
                .returning(OrderCodeCounter.last_no)
            )
            return int((await self._session.execute(statement)).scalar_one())
        row = (
            await self._session.execute(
                select(OrderCodeCounter).where(
                    OrderCodeCounter.unit_id == unit_id,
                    OrderCodeCounter.member_code == member_code,
                    OrderCodeCounter.day == day,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            self._session.add(
                OrderCodeCounter(unit_id=unit_id, member_code=member_code, day=day, last_no=1)
            )
            await self._session.flush()
            return 1
        number = int(row.last_no) + 1
        await self._session.execute(
            update(OrderCodeCounter).where(OrderCodeCounter.id == row.id).values(last_no=number)
        )
        await self._session.flush()
        return number


__all__ = ["OrderCodeService"]
