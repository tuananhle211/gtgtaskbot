"""Ngân sách chạy ads theo tuần (0082, 2026-10-29): the Trưởng phòng ADS sets
each marketer's budget for the month's four weeks (:mod:`meobot.domain.ads.budget`)
and the dashboards set it against the week's spend.

* the spend is every platform's (Facebook read from Meta, Google / TikTok
  typed by hand) on the ad accounts the person holds - a budget is the
  person's money, whatever they run it on; no BM, no site;
* the people are those seated in the ADS department, plus anybody who still
  has a budget that month;
* who: a Trưởng phòng ADS and the OWNER / an ADMIN write
  (``AdsAccess.edit_revenue``, 403 ``ads_revenue_forbidden``) and see
  everybody; a member sees their own line.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.access import AdsAccess, AdsNotVisibleError
from meobot.application.ads.accounts_service import ads_people
from meobot.application.ads.revenue_service import (
    AdsRevenueForbiddenError,
    AdsRevenueInvalidError,
    _amount,
    month_start,
)
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.core.errors import ValidationError
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsAccount, AdsBudget, AdsSpendDaily
from meobot.db.models.user import User
from meobot.domain.ads.budget import WEEKS, BudgetStatus, BudgetWeek, budget_status, budget_weeks
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor


class AdsBudgetInvalidError(ValidationError):
    code = "ads_budget_invalid"

    def __init__(self, message: str, key: str) -> None:
        super().__init__(message, details={"reason": "ads_budget_invalid", "key": key})


@dataclass(frozen=True, slots=True)
class BudgetCell:
    week: BudgetWeek
    budget: float | None
    spend: float
    status: BudgetStatus

    @property
    def rate(self) -> float | None:
        return self.spend / self.budget if self.budget else None


@dataclass(frozen=True, slots=True)
class BudgetLine:
    #: ``None`` for the team's total line.
    user_id: uuid.UUID | None
    name: str
    member_code: str | None
    cells: tuple[BudgetCell, ...]

    @property
    def budget(self) -> float | None:
        set_ = [cell.budget for cell in self.cells if cell.budget is not None]
        return sum(set_) if set_ else None

    @property
    def spend(self) -> float:
        return sum(cell.spend for cell in self.cells)


@dataclass(frozen=True, slots=True)
class BudgetMonth:
    month: date
    today: date
    weeks: tuple[BudgetWeek, ...]
    lines: list[BudgetLine]
    #: Everybody shown together; ``None`` for one person.
    total: BudgetLine | None


def entry_key(user_id: uuid.UUID, week: int) -> str:
    """The save payload names a cell ``<user id>:<week>``."""
    return f"{user_id}:{week}"


def _line(
    user_id: uuid.UUID | None,
    name: str,
    member_code: str | None,
    weeks: Sequence[BudgetWeek],
    budgets: Mapping[int, float],
    spend: Mapping[int, float],
    today: date,
) -> BudgetLine:
    cells = []
    for week in weeks:
        budget = budgets.get(week.index)
        spent = spend.get(week.index, 0.0)
        cells.append(BudgetCell(week, budget, spent, budget_status(spent, budget, week, today)))
    return BudgetLine(user_id, name, member_code, tuple(cells))


class AdsBudgetService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def _people(self, first: date) -> list[User]:
        people = {user.id: user for user in await ads_people(self._session)}
        kept = await self._session.scalars(
            select(User)
            .join(AdsBudget, AdsBudget.user_id == User.id)
            .where(AdsBudget.month == first)
            .distinct()
        )
        for user in kept:
            people.setdefault(user.id, user)
        return sorted(people.values(), key=lambda user: user.full_name.casefold())

    async def _stored(self, first: date) -> dict[tuple[uuid.UUID, int], AdsBudget]:
        rows = await self._session.scalars(select(AdsBudget).where(AdsBudget.month == first))
        return {(row.user_id, row.week): row for row in rows}

    async def _spend(
        self, weeks: Sequence[BudgetWeek], user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict[int, float]]:
        spend: dict[uuid.UUID, dict[int, float]] = defaultdict(dict)
        if not user_ids:
            return spend
        rows = await self._session.execute(
            select(AdsAccount.user_id, AdsSpendDaily.day, func.sum(AdsSpendDaily.spend))
            .join(AdsAccount, AdsAccount.account_id == AdsSpendDaily.account_id)
            .where(
                AdsAccount.user_id.in_(user_ids),
                AdsSpendDaily.day >= weeks[0].start,
                AdsSpendDaily.day <= weeks[-1].end,
            )
            .group_by(AdsAccount.user_id, AdsSpendDaily.day)
        )
        for user_id, day, amount in rows:
            week = next(week for week in weeks if week.start <= day <= week.end)
            spend[user_id][week.index] = spend[user_id].get(week.index, 0.0) + float(amount or 0)
        return spend

    async def month(
        self,
        access: AdsAccess,
        month: date,
        *,
        user_id: uuid.UUID | None = None,
        today: date | None = None,
    ) -> BudgetMonth:
        access.require_view()
        if not access.view_all:
            user_id = access.user_id
        if user_id is not None and not access.sees(user_id):
            raise AdsNotVisibleError()
        first = month_start(month)
        today = today or vn_today()
        weeks = budget_weeks(first)
        people = await self._people(first)
        if user_id is not None:
            people = [user for user in people if user.id == user_id]
            if not people:
                one = await self._session.get(User, user_id)
                if one is None or one.deleted_at is not None:
                    raise AdsNotVisibleError()
                people = [one]
        stored = await self._stored(first)
        spend = await self._spend(weeks, [user.id for user in people])
        lines = [
            _line(
                user.id,
                user.full_name,
                user.member_code,
                weeks,
                {
                    week: float(row.amount)
                    for (owner, week), row in stored.items()
                    if owner == user.id
                },
                spend.get(user.id, {}),
                today,
            )
            for user in people
        ]
        total = None
        if user_id is None:
            budgets: dict[int, float] = {}
            spent: dict[int, float] = defaultdict(float)
            for line in lines:
                for cell in line.cells:
                    spent[cell.week.index] += cell.spend
                    if cell.budget is not None:
                        budgets[cell.week.index] = budgets.get(cell.week.index, 0.0) + cell.budget
            total = _line(None, "Cả phòng", None, weeks, budgets, spent, today)
        return BudgetMonth(first, today, weeks, lines, total)

    async def save(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        month: date,
        entries: Mapping[str, object],
    ) -> BudgetMonth:
        access.require_view()
        if not access.edit_revenue:
            raise AdsRevenueForbiddenError()
        first = month_start(month)
        people = {user.id for user in await self._people(first)}
        stored = await self._stored(first)
        now = utcnow()
        for key, raw in entries.items():
            owner_text, _, week_text = key.partition(":")
            try:
                owner = uuid.UUID(owner_text)
                week = int(week_text)
            except ValueError as error:
                raise AdsBudgetInvalidError("Ô ngân sách không hợp lệ.", key) from error
            if owner not in people or not 1 <= week <= WEEKS:
                raise AdsBudgetInvalidError("Ô ngân sách không hợp lệ.", key)
            try:
                amount: Decimal | None = _amount(raw, key)
            except AdsRevenueInvalidError as error:
                raise AdsBudgetInvalidError(error.message, key) from error
            row = stored.get((owner, week))
            if amount is None:
                if row is not None:
                    await self._session.delete(row)
            elif row is None:
                self._session.add(
                    AdsBudget(
                        month=first,
                        user_id=owner,
                        week=week,
                        amount=amount,
                        updated_by_user_id=actor.user_id,
                        updated_at=now,
                    )
                )
            elif row.amount != amount:
                row.amount = amount
                row.updated_by_user_id = actor.user_id
                row.updated_at = now
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_BUDGET_SAVED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_budget",
            entity_id=first.isoformat(),
            after_data={key: str(value) for key, value in entries.items()},
        )
        return await self.month(access, first)


__all__ = [
    "AdsBudgetInvalidError",
    "AdsBudgetService",
    "BudgetCell",
    "BudgetLine",
    "BudgetMonth",
    "entry_key",
]
