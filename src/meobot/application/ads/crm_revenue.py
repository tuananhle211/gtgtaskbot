"""The CRM's thực thu as revenue lines (0083; ``docs/ads-report/CRM_REVENUE_PLAN.md``).

:class:`CrmBook` turns the stored payments into one line per day per person
(``u:<id>``) or ◆ source (``s:<source>``), split by where each customer was
served - the payment's branch (HN1 / HN2 -> "Doanh số HN", SG -> "Doanh số
SG") - and by which team brought them - the account's region (HN accounts ->
``hn_team_*``; an account without a region counts as HCM; a payment whose
branch is unknown is served in its account's region). Points
(``payment_type`` 3) are not money; a refund is already negative. An account
that is nobody's and no source's is left out.

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
from meobot.domain.ads.crm import COUNTED_PAYMENT_TYPES, DEFAULT_BRANCH_SITES, CrmRegion
from meobot.domain.ads.models import RevenueSource, subject_key

#: The ``system_settings`` row holding what has been synced.
SETTINGS_KEY = "ads_crm"
#: A former marketer's line (0084): ``c:<CRM id>``, named after the account.
FORMER_PREFIX = "c:"


@dataclass(frozen=True, slots=True)
class Coverage:
    synced_from: date | None = None
    synced_to: date | None = None
    last_sync_at: datetime | None = None
    last_status: str | None = None
    last_message: str | None = None
    #: The CRM's branches -> region, as last read (else the defaults).
    branches: dict[int, CrmRegion] = field(default_factory=dict)

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
            "branches": {str(key): region.value for key, region in self.branches.items()},
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
            branches={
                int(key): CrmRegion(region)
                for key, region in (value.get("branches") or {}).items()
                if region in CrmRegion._value2member_map_
            },
        )

    def site_of(self, branch_id: int | None) -> CrmRegion | None:
        if branch_id is None:
            return None
        return self.branches.get(branch_id) or DEFAULT_BRANCH_SITES.get(branch_id)


async def load_coverage(session: AsyncSession) -> tuple[Coverage, SystemSetting | None]:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY))
    return Coverage.from_value(row.value if row else None), row


@dataclass(frozen=True, slots=True)
class CrmLine:
    day: date
    key: str
    user_id: uuid.UUID | None
    source: RevenueSource | None
    #: Served at the HCM branch.
    amount: Decimal
    #: Served at a Hà Nội branch.
    amount_hn: Decimal
    #: Of each: brought by the HN team (HN CRM accounts).
    hn_team_hcm: Decimal = Decimal(0)
    hn_team_hn: Decimal = Decimal(0)
    #: A ◆ Hotline line's hotline (FB / TIKTOK / GOOGLE), else ``None``.
    kind: str | None = None


def account_subject(account: AdsCrmAccount) -> str | None:
    if account.user_id is not None:
        return subject_key(user_id=account.user_id)
    if account.source is not None:
        return subject_key(source=account.source)
    if account.former:
        return f"{FORMER_PREFIX}{account.crm_id}"
    return None


@dataclass(slots=True)
class CrmBook:
    coverage: Coverage
    #: Every person / source key that holds a CRM account.
    subjects: set[str] = field(default_factory=set)
    lines: list[CrmLine] = field(default_factory=list)
    #: A former marketer's key -> their CRM account's name.
    labels: dict[str, str] = field(default_factory=dict)

    def covers(self, day: date, key: str) -> bool:
        return key in self.subjects and self.coverage.covers(day)

    def totals(self, day: date) -> dict[str, CrmLine]:
        """The day's lines summed per person / source (hotline kinds together)."""
        out: dict[str, CrmLine] = {}
        for line in self.lines:
            if line.day != day:
                continue
            seen = out.get(line.key)
            out[line.key] = (
                line
                if seen is None
                else CrmLine(
                    day,
                    line.key,
                    line.user_id,
                    line.source,
                    seen.amount + line.amount,
                    seen.amount_hn + line.amount_hn,
                    seen.hn_team_hcm + line.hn_team_hcm,
                    seen.hn_team_hn + line.hn_team_hn,
                )
            )
        return out


async def crm_book(session: AsyncSession, start: date, end: date) -> CrmBook:
    """The CRM's revenue lines for the days (only those already synced)."""
    coverage, _ = await load_coverage(session)
    accounts = list(await session.scalars(select(AdsCrmAccount)))
    owned = {account.crm_id: account for account in accounts if account_subject(account)}
    book = CrmBook(
        coverage,
        {key for a in owned.values() if (key := account_subject(a))},
        labels={f"{FORMER_PREFIX}{a.crm_id}": a.name for a in owned.values() if a.former},
    )
    if not owned or coverage.synced_from is None or coverage.synced_to is None:
        return book
    first = max(start, coverage.synced_from)
    last = min(end, coverage.synced_to)
    if first > last:
        return book
    # [served HCM, served HN, HN team served HCM, HN team served HN]
    sums: dict[tuple[date, str, str | None], list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 4)
    rows = await session.execute(
        select(
            AdsCrmPayment.payment_date,
            AdsCrmPayment.marketing_id,
            AdsCrmPayment.branch_id,
            func.sum(AdsCrmPayment.price),
        )
        .where(
            AdsCrmPayment.payment_date >= first,
            AdsCrmPayment.payment_date <= last,
            AdsCrmPayment.payment_type.in_(COUNTED_PAYMENT_TYPES),
            AdsCrmPayment.marketing_id.in_(list(owned)),
        )
        .group_by(AdsCrmPayment.payment_date, AdsCrmPayment.marketing_id, AdsCrmPayment.branch_id)
    )
    for day, marketing_id, branch_id, total in rows:
        account = owned[marketing_id]
        key = account_subject(account)
        assert key is not None
        site = coverage.site_of(branch_id) or account.region or CrmRegion.HCM
        # A hotline is nobody's team: its revenue goes where it was served.
        team = (
            site if account.source is RevenueSource.HOTLINE else (account.region or CrmRegion.HCM)
        )
        value = Decimal(total or 0)
        kind = account.hotline_kind.value if account.hotline_kind else None
        line = sums[(day, key, kind)]
        line[1 if site is CrmRegion.HN else 0] += value
        if team is CrmRegion.HN:
            line[3 if site is CrmRegion.HN else 2] += value
    for (day, key, kind), (amount, amount_hn, hn_hcm, hn_hn) in sorted(
        sums.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or "")
    ):
        sample = next(a for a in owned.values() if account_subject(a) == key)
        book.lines.append(
            CrmLine(day, key, sample.user_id, sample.source, amount, amount_hn, hn_hcm, hn_hn, kind)
        )
    return book


__all__ = [
    "FORMER_PREFIX",
    "SETTINGS_KEY",
    "Coverage",
    "CrmBook",
    "CrmLine",
    "account_subject",
    "crm_book",
    "load_coverage",
]
