"""Members' points coefficients ("hệ số điểm", 0076, 2026-10-26).

Every point a person earns in a month counts ``coefficient`` times (1 when
none is set). A coefficient applies from a month on, until a later one: the
coefficient of a month is the change with the latest ``effective_month`` not
after it (the latest written, when one month has several). The reports show
only the points after the coefficient.

Each line is weighed on its own and rounded half up, and totals add up the
weighed lines, so a person's total is always the sum of what their lines say.

Who sets them: the OWNER (``PERFORMANCE COEFFICIENT`` is OWNER-only - not an
ADMIN's by role, granted by the OWNER alone).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.org.access import Access
from meobot.application.org.seats import load_seats
from meobot.core.time import ensure_utc
from meobot.db.models.point_coefficient import UserPointCoefficient
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.performance.errors import (
    PerformanceCoefficientForbiddenError,
    PerformanceCoefficientInvalidError,
    PerformancePersonNotFoundError,
)
from meobot.domain.performance.models import (
    COEFFICIENT_DEFAULT,
    COEFFICIENT_MAX,
    COEFFICIENT_MIN,
    MODULE_PERFORMANCE,
    PerformancePermission,
)
from meobot.domain.units.models import UnitMembership

if TYPE_CHECKING:
    from meobot.application.account.stats_service import Month

ONE = Decimal(COEFFICIENT_DEFAULT)
NOTE_MAX = 500


def weigh(points: int, coefficient: Decimal) -> int:
    """``points`` x ``coefficient``, rounded half up to a whole point."""
    if coefficient == ONE:
        return points
    return int((Decimal(points) * coefficient).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def month_of(at: datetime, tz: ZoneInfo) -> date:
    """The first day of the (Vietnam-time) month ``at`` falls in."""
    local = ensure_utc(at).astimezone(tz)
    return date(local.year, local.month, 1)


def first_day(month: Month) -> date:
    return date(month.year, month.month, 1)


@dataclass(frozen=True, slots=True)
class CoefficientBook:
    """Everybody's changes, oldest first, ready to answer "x for that month"."""

    changes: dict[uuid.UUID, list[UserPointCoefficient]] = field(default_factory=dict)

    def change(self, user_id: uuid.UUID, month: date) -> UserPointCoefficient | None:
        found = None
        for row in self.changes.get(user_id, ()):
            if row.effective_month <= month:
                found = row
        return found

    def of(self, user_id: uuid.UUID, month: date) -> Decimal:
        row = self.change(user_id, month)
        return Decimal(row.coefficient) if row is not None else ONE

    def weigh(self, user_id: uuid.UUID, points: int, month: date) -> int:
        return weigh(points, self.of(user_id, month))


async def load_coefficients(
    session: AsyncSession, user_ids: Iterable[uuid.UUID] | None = None
) -> CoefficientBook:
    """The changes of ``user_ids`` (everybody when ``None``)."""
    statement = select(UserPointCoefficient).order_by(
        UserPointCoefficient.effective_month, UserPointCoefficient.created_at
    )
    if user_ids is not None:
        ids = list(set(user_ids))
        if not ids:
            return CoefficientBook()
        statement = statement.where(UserPointCoefficient.user_id.in_(ids))
    changes: dict[uuid.UUID, list[UserPointCoefficient]] = defaultdict(list)
    for row in (await session.scalars(statement)).all():
        changes[row.user_id].append(row)
    return CoefficientBook(dict(changes))


def may_set_coefficients(membership: UnitMembership) -> bool:
    """The OWNER, or whoever the OWNER granted ``PERFORMANCE COEFFICIENT``."""
    if membership.is_owner:
        return True
    access = membership.access
    return isinstance(access, Access) and access.allows(
        MODULE_PERFORMANCE, PerformancePermission.COEFFICIENT.value
    )


def parse_coefficient(value: str | float | Decimal) -> Decimal:
    """A coefficient from the form: 0.01-10, at most two decimals."""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as error:
        raise PerformanceCoefficientInvalidError("Hệ số phải là một số.") from error
    if not number.is_finite():
        raise PerformanceCoefficientInvalidError("Hệ số phải là một số.")
    if number != number.quantize(Decimal("0.01")):
        raise PerformanceCoefficientInvalidError("Hệ số có tối đa 2 chữ số thập phân.")
    if number < Decimal(COEFFICIENT_MIN) or number > Decimal(COEFFICIENT_MAX):
        raise PerformanceCoefficientInvalidError(
            f"Hệ số phải từ {COEFFICIENT_MIN.replace('.', ',')} đến {COEFFICIENT_MAX}."
        )
    return number.quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class CoefficientRow:
    """One account on the "Hệ số điểm" screen, for the month asked."""

    user_id: uuid.UUID
    name: str
    member_code: str | None
    departments: tuple[str, ...]
    coefficient: Decimal
    #: The month the coefficient in force started; ``None`` = the default 1.
    effective_month: date | None
    note: str | None
    #: A change already set for a later month, if any.
    next_coefficient: Decimal | None = None
    next_month: date | None = None


@dataclass(frozen=True, slots=True)
class CoefficientChange:
    id: uuid.UUID
    user_id: uuid.UUID
    coefficient: Decimal
    effective_month: date
    note: str | None
    created_by: str | None
    created_at: datetime


class CoefficientService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def overview(self, month: Month) -> list[CoefficientRow]:
        """Every active account, with the coefficient in force in ``month``."""
        users = (
            await self._session.scalars(
                select(User)
                .where(User.deleted_at.is_(None), User.active.is_(True))
                .order_by(User.full_name)
            )
        ).all()
        ids = [user.id for user in users]
        book = await load_coefficients(self._session, ids)
        seats = await load_seats(self._session, ids)
        start = first_day(month)
        rows: list[CoefficientRow] = []
        for user in users:
            change = book.change(user.id, start)
            later = [row for row in book.changes.get(user.id, ()) if row.effective_month > start]
            upcoming = None
            if later:
                # The nearest later month, its latest write.
                nearest = min(row.effective_month for row in later)
                upcoming = [row for row in later if row.effective_month == nearest][-1]
            rows.append(
                CoefficientRow(
                    user_id=user.id,
                    name=user.full_name or "—",
                    member_code=user.member_code,
                    departments=tuple(seat.name for seat in seats.get(user.id, ())),
                    coefficient=Decimal(change.coefficient) if change is not None else ONE,
                    effective_month=change.effective_month if change is not None else None,
                    note=change.note if change is not None else None,
                    next_coefficient=(
                        Decimal(upcoming.coefficient) if upcoming is not None else None
                    ),
                    next_month=upcoming.effective_month if upcoming is not None else None,
                )
            )
        return rows

    async def history(self, user_id: uuid.UUID) -> list[CoefficientChange]:
        """One person's changes, newest first."""
        await self._user(user_id)
        rows = (
            await self._session.scalars(
                select(UserPointCoefficient)
                .where(UserPointCoefficient.user_id == user_id)
                .order_by(
                    UserPointCoefficient.effective_month.desc(),
                    UserPointCoefficient.created_at.desc(),
                )
            )
        ).all()
        authors = {
            row[0]: row[1]
            for row in (
                await self._session.execute(
                    select(User.id, User.full_name).where(
                        User.id.in_(
                            {row.created_by_user_id for row in rows if row.created_by_user_id}
                        )
                    )
                )
            ).all()
        }
        return [self._change(row, authors.get(row.created_by_user_id)) for row in rows]

    async def set(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        coefficient: str | float | Decimal,
        month: Month,
        note: str | None = None,
    ) -> CoefficientChange:
        """From ``month`` on, ``user_id``'s points count ``coefficient`` times."""
        if not may_set_coefficients(membership):
            raise PerformanceCoefficientForbiddenError()
        user = await self._user(user_id)
        value = parse_coefficient(coefficient)
        text = (note or "").strip() or None
        if text is not None and len(text) > NOTE_MAX:
            raise PerformanceCoefficientInvalidError(
                f"Ghi chú tối đa {NOTE_MAX} ký tự.", field="note"
            )
        start = first_day(month)
        before = (await load_coefficients(self._session, [user.id])).of(user.id, start)
        row = UserPointCoefficient(
            user_id=user.id,
            effective_month=start,
            coefficient=value,
            note=text,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_POINT_COEFFICIENT_SET.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            before_data={"coefficient": str(before), "month": start.isoformat()},
            after_data={"coefficient": str(value), "month": start.isoformat(), "note": text},
        )
        return self._change(row, actor.full_name)

    async def _user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise PerformancePersonNotFoundError()
        return user

    @staticmethod
    def _change(row: UserPointCoefficient, author: str | None) -> CoefficientChange:
        return CoefficientChange(
            id=row.id,
            user_id=row.user_id,
            coefficient=Decimal(row.coefficient),
            effective_month=row.effective_month,
            note=row.note,
            created_by=author,
            created_at=row.created_at,
        )


__all__ = [
    "CoefficientBook",
    "CoefficientChange",
    "CoefficientRow",
    "CoefficientService",
    "first_day",
    "load_coefficients",
    "may_set_coefficients",
    "month_of",
    "parse_coefficient",
    "weigh",
]
