"""The CRM's thực thu as revenue lines (0083; ``docs/ads-report/CRM_REVENUE_PLAN.md``).

:class:`CrmBook` turns the stored payments into one line per day per person
(``u:<id>``) or ◆ source (``s:<source>``): the person's HCM accounts fill
"Doanh số SG", their HN accounts "Doanh số HN" (an account without a region
counts as SG). Points (``payment_type`` 3) are not money; a refund is already
negative. An account that is nobody's and no source's is left out.

Inside the days the CRM has been synced (:class:`Coverage`), a person or a
source that holds at least one CRM account takes the CRM's numbers - what was
typed by hand for it on those days is ignored (``AdsRevenueService.rows``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.ads import AdsCrmAccount, AdsCrmPayment
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.ads.crm import COUNTED_PAYMENT_TYPES, CrmRegion
from meobot.domain.ads.models import RevenueSource, subject_key

#: The ``system_settings`` row holding what has been synced.
SETTINGS_KEY = "ads_crm"


@dataclass(frozen=True, slots=True)
class Coverage:
    synced_from: date | None = None
    synced_to: date | None = None
    last_sync_at: datetime | None = None
    last_status: str | None = None
    last_message: str | None = None

    def covers(self, day: date) -> bool:
        return (
            self.synced_from is not None
            and self.synced_to is not None
            and self.synced_from <= day <= self.synced_to
        )

    def as_value(self) -> dict[str, Any]:
        return {
            "synced_from": self.synced_from.isoformat() if self.synced_from else None,
            "synced_to": self.synced_to.isoformat() if self.synced_to else None,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_status": self.last_status,
            "last_message": self.last_message,
        }

    @classmethod
    def from_value(cls, value: dict[str, Any] | None) -> Coverage:
        value = value or {}

        def day(key: str) -> date | None:
            raw = value.get(key)
            return date.fromisoformat(raw) if raw else None

        raw_at = value.get("last_sync_at")
        return cls(
            synced_from=day("synced_from"),
            synced_to=day("synced_to"),
            last_sync_at=datetime.fromisoformat(raw_at) if raw_at else None,
            last_status=value.get("last_status"),
            last_message=value.get("last_message"),
        )


async def load_coverage(session: AsyncSession) -> tuple[Coverage, SystemSetting | None]:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY))
    return Coverage.from_value(row.value if row else None), row


@dataclass(frozen=True, slots=True)
class CrmLine:
    day: date
    key: str
    user_id: uuid.UUID | None
    source: RevenueSource | None
    #: HCM accounts (and those without a region).
    amount: Decimal
    #: HN accounts.
    amount_hn: Decimal


def account_subject(account: AdsCrmAccount) -> str | None:
    if account.user_id is not None:
        return subject_key(user_id=account.user_id)
    if account.source is not None:
        return subject_key(source=account.source)
    return None


@dataclass(slots=True)
class CrmBook:
    coverage: Coverage
    #: Every person / source key that holds a CRM account.
    subjects: set[str] = field(default_factory=set)
    lines: list[CrmLine] = field(default_factory=list)

    def covers(self, day: date, key: str) -> bool:
        return key in self.subjects and self.coverage.covers(day)

    def line(self, day: date, key: str) -> CrmLine | None:
        return next((line for line in self.lines if line.day == day and line.key == key), None)


async def crm_book(session: AsyncSession, start: date, end: date) -> CrmBook:
    """The CRM's revenue lines for the days (only those already synced)."""
    coverage, _ = await load_coverage(session)
    accounts = list(await session.scalars(select(AdsCrmAccount)))
    owned = {account.crm_id: account for account in accounts if account_subject(account)}
    book = CrmBook(coverage, {key for a in owned.values() if (key := account_subject(a))})
    if not owned or coverage.synced_from is None or coverage.synced_to is None:
        return book
    first = max(start, coverage.synced_from)
    last = min(end, coverage.synced_to)
    if first > last:
        return book
    sums: dict[tuple[date, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    rows = await session.execute(
        select(
            AdsCrmPayment.payment_date,
            AdsCrmPayment.marketing_id,
            func.sum(AdsCrmPayment.price),
        )
        .where(
            AdsCrmPayment.payment_date >= first,
            AdsCrmPayment.payment_date <= last,
            AdsCrmPayment.payment_type.in_(COUNTED_PAYMENT_TYPES),
            AdsCrmPayment.marketing_id.in_(list(owned)),
        )
        .group_by(AdsCrmPayment.payment_date, AdsCrmPayment.marketing_id)
    )
    for day, marketing_id, total in rows:
        account = owned[marketing_id]
        key = account_subject(account)
        assert key is not None
        part = 1 if account.region is CrmRegion.HN else 0
        sums[(day, key)][part] += Decimal(total or 0)
    for (day, key), (amount, amount_hn) in sorted(sums.items()):
        sample = next(a for a in owned.values() if account_subject(a) == key)
        book.lines.append(CrmLine(day, key, sample.user_id, sample.source, amount, amount_hn))
    return book


__all__ = [
    "SETTINGS_KEY",
    "Coverage",
    "CrmBook",
    "CrmLine",
    "account_subject",
    "crm_book",
    "load_coverage",
]
