"""The staff dashboard ("Dashboard của tôi", plan §4.1) from the synced rows.

One person's ad accounts (a Trưởng phòng / the OWNER may pick anybody, or the
whole department), a period and the same period just before it:

* Khối 1: spend, messages, cost per message, phone numbers, the share of
  messages that gave a number, cost per number - each against the period
  before; "Số tiền lập hoá đơn" from Meta's charges;
* Khối 2: the series by day / week / month, the best day and the day to look at;
* Khối 3: per ad account - spend, charges, the difference, matched or not.

Revenue ("Doanh số", Ads / Doanh thu) is entered by the Trưởng phòng; until
then those figures are ``None`` and the screen says so.

``platform`` (0081) picks the ad accounts: Facebook (default, from Meta) or
Google / TikTok Ads (typed by hand - :mod:`meobot.application.ads.manual_service`).
Their revenue is entered on the ◆ Google / Tiktok lines, not per marketer: one
marketer's Google numbers have no revenue (``revenue_note`` ``platform``), the
whole department's take that ◆ line. A typed-in platform lists every account
in Khối 3, even one with nothing typed yet, so it can be filled in.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.access import AdsAccess
from meobot.application.ads.accounts_service import ads_people
from meobot.application.ads.manual_service import AdsManualService, may_edit
from meobot.application.ads.revenue_service import AdsRevenueService, RevenueRow
from meobot.application.audit_service import AuditService
from meobot.db.models.ads import AdsAccount, AdsChargeDaily, AdsConnection, AdsSpendDaily
from meobot.db.models.user import User
from meobot.domain.ads.models import (
    BRAND_LABELS,
    MANUAL_PLATFORMS,
    PLATFORM_SOURCES,
    AdsBrand,
    AdsGrouping,
    AdsPlatform,
    Period,
    bucket,
    change,
    commission_revenue,
    invoice_factor,
    matches,
    ratio,
)


@dataclass(frozen=True, slots=True)
class Totals:
    spend: float = 0.0
    messages: int = 0
    phones: int = 0
    charged: float = 0.0
    invoice: float = 0.0
    #: Entered by the Trưởng phòng (0079): "Doanh số xét KPI" = SG + HN of the
    #: marketers in view. ``None`` = nothing entered for the window, or a brand
    #: / BM filter (revenue is not split that way).
    revenue: float | None = None
    revenue_sg: float | None = None
    revenue_hn: float | None = None
    #: "Doanh thu hoa hồng": the revenue without the 8% VAT (from 07/2026).
    revenue_net: float | None = None

    @property
    def ads_ratio(self) -> float | None:
        """Ads / Doanh thu: spend over commission revenue."""
        return ratio(self.spend, self.revenue_net) if self.revenue_net else None

    @property
    def cost_per_message(self) -> float | None:
        return ratio(self.spend, self.messages)

    @property
    def cost_per_phone(self) -> float | None:
        return ratio(self.spend, self.phones)

    @property
    def phone_rate(self) -> float | None:
        return ratio(self.phones, self.messages)


@dataclass(frozen=True, slots=True)
class Kpi:
    key: str
    label: str
    value: float | None
    previous: float | None
    #: Relative change; for a cost, going down is good (``lower_is_better``).
    change: float | None
    lower_is_better: bool = False


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    start: date
    spend: float
    messages: int
    phones: int
    #: "Doanh số xét KPI" of the bucket; ``revenue_net`` without VAT.
    revenue: float | None = None
    revenue_net: float | None = None

    @property
    def cost_per_phone(self) -> float | None:
        return ratio(self.spend, self.phones)

    @property
    def ads_ratio(self) -> float | None:
        return ratio(self.spend, self.revenue_net) if self.revenue_net else None


@dataclass(frozen=True, slots=True)
class AccountLine:
    account_id: str
    name: str
    connection_name: str
    site: str
    brand: AdsBrand
    brand_label: str
    user_id: uuid.UUID | None
    user_name: str | None
    spend: float
    messages: int
    phones: int
    charged: float
    invoice: float
    #: Meta's charge minus the spend (both before VAT).
    difference: float
    matched: bool
    card_last4: str | None = None
    #: Per day of the period (only for periods up to 62 days): spend and
    #: "Số tiền lập hoá đơn", for the "theo ngày" tabs.
    spend_by_day: list[float] = field(default_factory=list)
    invoice_by_day: list[float] = field(default_factory=list)
    platform: AdsPlatform = AdsPlatform.FACEBOOK
    #: The id to show (a typed-in account's as typed; Meta's otherwise).
    external_id: str | None = None
    #: The viewer types this account's numbers (Google / TikTok Ads).
    can_edit: bool = False
    #: Per day: whether a number was typed / read (``None`` in the cell = not
    #: entered, unlike 0).
    spend_entered: list[bool] = field(default_factory=list)
    invoice_entered: list[bool] = field(default_factory=list)

    @property
    def invoice_ratio(self) -> float | None:
        """ "LHĐ / chi tiêu"."""
        return ratio(self.invoice, self.spend) if self.spend else None


@dataclass(frozen=True, slots=True)
class Highlights:
    """The cards beside the chart."""

    best_revenue_day: SeriesPoint | None
    #: Days with spend and no revenue entered.
    zero_revenue_days: list[SeriesPoint]
    #: The last five days of the period: total spend, and the average per day
    #: before them in the period (``None`` when there are no earlier days).
    last5_spend: float
    last5_days: int
    earlier_daily_spend: float | None


@dataclass(slots=True)
class Dashboard:
    period: Period
    previous: Period
    grouping: AdsGrouping
    user_id: uuid.UUID | None
    current: Totals
    before: Totals
    kpis: list[Kpi]
    series: list[SeriesPoint]
    best_day: SeriesPoint | None
    review_day: SeriesPoint | None
    accounts: list[AccountLine]
    #: People the viewer may pick (a Trưởng phòng / the OWNER), else empty.
    people: list[User] = field(default_factory=list)
    connections: list[AdsConnection] = field(default_factory=list)
    last_synced_at: datetime | None = None
    #: Why there is no revenue: ``filtered`` (brand / BM / cơ sở), ``platform``
    #: (one marketer's Google / TikTok numbers), else ``None``.
    revenue_note: str | None = None
    highlights: Highlights | None = None
    #: The days of the period, for the per-day tables (empty above 62 days).
    days: list[date] = field(default_factory=list)
    platform: AdsPlatform = AdsPlatform.FACEBOOK
    #: May add a Google / TikTok account here (for the person in view).
    can_add_account: bool = False
    #: The cơ sở in use (BMs and typed-in accounts), for the forms.
    sites: list[str] = field(default_factory=list)


class AdsDashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def staff(
        self,
        access: AdsAccess,
        *,
        period: Period,
        grouping: AdsGrouping = AdsGrouping.DAY,
        user_id: uuid.UUID | None = None,
        brand: AdsBrand | None = None,
        connection_id: uuid.UUID | None = None,
        site: str | None = None,
        platform: AdsPlatform = AdsPlatform.FACEBOOK,
    ) -> Dashboard:
        access.require_view()
        person = user_id if access.view_all else access.user_id
        manual = platform in MANUAL_PLATFORMS
        if manual:
            # A BM is Facebook's.
            connection_id = None
        statement = (
            select(AdsAccount, AdsConnection)
            .outerjoin(AdsConnection, AdsConnection.id == AdsAccount.connection_id)
            .where(AdsAccount.platform == platform)
        )
        if person is not None:
            statement = statement.where(AdsAccount.user_id == person)
        if brand is not None:
            statement = statement.where(AdsAccount.brand == brand)
        if connection_id is not None:
            statement = statement.where(AdsAccount.connection_id == connection_id)
        if site:
            statement = statement.where(func.coalesce(AdsConnection.site, AdsAccount.site) == site)
        accounts: dict[str, tuple[AdsAccount, AdsConnection | None]] = {
            account.account_id: (account, connection)
            for account, connection in await self._session.execute(statement)
        }
        previous = period.previous()
        ids = list(accounts)
        spend_rows = await self._rows(AdsSpendDaily, ids, previous.start, period.end)
        charge_rows = await self._rows(AdsChargeDaily, ids, previous.start, period.end)
        filtered = brand is not None or connection_id is not None or bool(site)
        revenue_rows: list[RevenueRow] = []
        if not filtered and not (manual and person is not None):
            source = PLATFORM_SOURCES.get(platform)
            revenue_rows = [
                row
                for row in await AdsRevenueService(self._session, AuditService(self._session)).rows(
                    previous.start, period.end
                )
                # Facebook: the ads marketers only (the ◆ sources are nobody's
                # ad spend); Google / TikTok: their ◆ line.
                if (
                    row.source is source
                    if manual
                    else row.user_id is not None and (person is None or row.user_id == person)
                )
            ]

        def revenue_in(window: Period) -> dict[str, float | None]:
            inside = [row for row in revenue_rows if window.start <= row.day <= window.end]
            if not inside:
                return {
                    "revenue": None,
                    "revenue_sg": None,
                    "revenue_hn": None,
                    "revenue_net": None,
                }
            return {
                "revenue": float(sum((row.gross for row in inside), Decimal(0))),
                "revenue_sg": float(sum((row.amount for row in inside), Decimal(0))),
                "revenue_hn": float(sum((row.amount_hn for row in inside), Decimal(0))),
                "revenue_net": float(
                    sum((commission_revenue(row.gross, row.day) for row in inside), Decimal(0))
                ),
            }

        def totals(window: Period) -> Totals:
            spend = sum(
                (row.spend for row in spend_rows if window.start <= row.day <= window.end),
                Decimal(0),
            )
            charged = Decimal(0)
            invoice = Decimal(0)
            for row in charge_rows:
                if window.start <= row.day <= window.end:
                    account, _ = accounts[row.account_id]
                    charged += row.amount
                    invoice += row.amount * invoice_factor(account.name, account.brand, row.day)
            return Totals(
                spend=float(spend),
                messages=sum(
                    row.messages for row in spend_rows if window.start <= row.day <= window.end
                ),
                phones=sum(
                    row.phones for row in spend_rows if window.start <= row.day <= window.end
                ),
                charged=float(charged),
                invoice=float(invoice),
                **revenue_in(window),
            )

        current = totals(period)
        before = totals(previous)
        series = self._series(spend_rows, revenue_rows, period, grouping)
        days = self._series(spend_rows, revenue_rows, period, AdsGrouping.DAY)
        with_phones = [point for point in days if point.phones]
        best = max(with_phones, key=lambda point: (point.phones, -point.spend), default=None)
        spent = [point for point in days if point.spend > 0]
        review = max(
            spent,
            key=lambda point: (point.spend / point.phones) if point.phones else float("inf"),
            default=None,
        )
        # The BMs to pick from: one person's accounts are often spread over
        # several BMs - list those, not every BM of the company.
        bms = select(AdsConnection).order_by(AdsConnection.name)
        if person is not None:
            bms = bms.where(
                AdsConnection.id.in_(
                    select(AdsAccount.connection_id).where(AdsAccount.user_id == person)
                )
            )
        connections = [] if manual else list(await self._session.scalars(bms))
        if manual:
            # Typed by hand: when somebody last typed a number.
            last_synced = await AdsManualService(
                self._session, AuditService(self._session)
            ).last_entered_at(ids)
        else:
            last_synced = await self._session.scalar(
                select(func.max(AdsConnection.last_spend_sync_at))
            )
        sites = sorted(
            {
                value
                for value in [
                    *await self._session.scalars(select(AdsConnection.site).distinct()),
                    *await self._session.scalars(select(AdsAccount.site).distinct()),
                ]
                if value
            }
        )
        if filtered:
            note: str | None = "filtered"
        elif manual and person is not None:
            note = "platform"
        else:
            note = None
        return Dashboard(
            period=period,
            previous=previous,
            grouping=grouping,
            user_id=person,
            current=current,
            before=before,
            kpis=_kpis(current, before),
            series=series,
            best_day=best,
            review_day=review if review is not best else None,
            accounts=await self._lines(
                accounts, spend_rows, charge_rows, period, access=access, every=manual
            ),
            people=await ads_people(self._session) if access.view_all else [],
            connections=connections,
            last_synced_at=last_synced,
            revenue_note=note,
            highlights=_highlights(days, period),
            days=_days_of(period),
            platform=platform,
            can_add_account=manual
            and (access.manage_accounts or person is None or person == access.user_id),
            sites=sites,
        )

    async def _rows(self, model: Any, ids: list[str], start: date, end: date) -> list[Any]:
        """Every row of ``model`` (spend or charges) for the accounts in the days."""
        if not ids:
            return []
        return list(
            await self._session.scalars(
                select(model).where(model.account_id.in_(ids), model.day >= start, model.day <= end)
            )
        )

    @staticmethod
    def _series(
        rows: list[AdsSpendDaily],
        revenue_rows: list[RevenueRow],
        period: Period,
        grouping: AdsGrouping,
    ) -> list[SeriesPoint]:
        revenue: dict[date, Decimal] = defaultdict(Decimal)
        net: dict[date, Decimal] = defaultdict(Decimal)
        for entry in revenue_rows:
            if period.start <= entry.day <= period.end:
                revenue[bucket(entry.day, grouping)] += entry.gross
                net[bucket(entry.day, grouping)] += commission_revenue(entry.gross, entry.day)
        sums: dict[date, list[float]] = defaultdict(lambda: [0.0, 0, 0])
        day = period.start
        while day <= period.end:
            sums[bucket(day, grouping)]
            day += timedelta(days=1)
        for row in rows:
            if period.start <= row.day <= period.end:
                point = sums[bucket(row.day, grouping)]
                point[0] += float(row.spend)
                point[1] += row.messages
                point[2] += row.phones
        return [
            SeriesPoint(
                start=start,
                spend=value[0],
                messages=int(value[1]),
                phones=int(value[2]),
                revenue=float(revenue[start]) if start in revenue else None,
                revenue_net=float(net[start]) if start in net else None,
            )
            for start, value in sorted(sums.items())
        ]

    async def _lines(
        self,
        accounts: dict[str, tuple[AdsAccount, AdsConnection | None]],
        spend_rows: list[AdsSpendDaily],
        charge_rows: list[AdsChargeDaily],
        period: Period,
        *,
        access: AdsAccess,
        every: bool = False,
    ) -> list[AccountLine]:
        """``every``: list the accounts with nothing in the period too (a
        typed-in platform's, to be filled in)."""
        days = _days_of(period)
        column = {day: index for index, day in enumerate(days)}
        spend: dict[str, list[float]] = defaultdict(lambda: [0.0, 0, 0])
        spend_daily: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(days))
        spend_seen: dict[str, list[bool]] = defaultdict(lambda: [False] * len(days))
        for row in spend_rows:
            if period.start <= row.day <= period.end:
                line = spend[row.account_id]
                line[0] += float(row.spend)
                line[1] += row.messages
                line[2] += row.phones
                if row.day in column:
                    spend_daily[row.account_id][column[row.day]] += float(row.spend)
                    spend_seen[row.account_id][column[row.day]] = True
        charged: dict[str, float] = defaultdict(float)
        invoice: dict[str, float] = defaultdict(float)
        invoice_daily: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(days))
        invoice_seen: dict[str, list[bool]] = defaultdict(lambda: [False] * len(days))
        for charge in charge_rows:
            if period.start <= charge.day <= period.end:
                account, _ = accounts[charge.account_id]
                amount = float(
                    charge.amount * invoice_factor(account.name, account.brand, charge.day)
                )
                charged[charge.account_id] += float(charge.amount)
                invoice[charge.account_id] += amount
                if charge.day in column:
                    invoice_daily[charge.account_id][column[charge.day]] += amount
                    invoice_seen[charge.account_id][column[charge.day]] = True
        active = set(accounts) if every else set(spend) | set(charged)
        user_ids = {accounts[key][0].user_id for key in active if accounts[key][0].user_id}
        names = (
            {
                user.id: user.full_name
                for user in await self._session.scalars(select(User).where(User.id.in_(user_ids)))
            }
            if user_ids
            else {}
        )
        lines: list[AccountLine] = []
        for key in active:
            account, connection = accounts[key]
            spent = spend[key][0]
            paid = charged.get(key, 0.0)
            lines.append(
                AccountLine(
                    account_id=key,
                    name=account.name,
                    connection_name=connection.name if connection else "Nhập tay",
                    site=connection.site if connection else account.site or "",
                    brand=account.brand,
                    brand_label=BRAND_LABELS[account.brand],
                    user_id=account.user_id,
                    user_name=names.get(account.user_id) if account.user_id else None,
                    spend=spent,
                    messages=int(spend[key][1]),
                    phones=int(spend[key][2]),
                    charged=paid,
                    invoice=invoice.get(key, 0.0),
                    difference=paid - spent,
                    matched=matches(Decimal(str(spent)), Decimal(str(paid))),
                    card_last4=account.card_last4,
                    spend_by_day=list(spend_daily[key]) if days else [],
                    invoice_by_day=list(invoice_daily[key]) if days else [],
                    platform=account.platform,
                    external_id=account.external_id or account.account_id,
                    can_edit=may_edit(access, account),
                    spend_entered=list(spend_seen[key]) if days else [],
                    invoice_entered=list(invoice_seen[key]) if days else [],
                )
            )
        lines.sort(key=lambda line: (-line.spend, line.name))
        return lines


#: Above this, the per-day tables would be too wide to read: left out.
MAX_DAILY_COLUMNS = 62


def _days_of(period: Period) -> list[date]:
    if period.days > MAX_DAILY_COLUMNS:
        return []
    return [period.start + timedelta(days=offset) for offset in range(period.days)]


def _highlights(days: list[SeriesPoint], period: Period) -> Highlights:
    with_revenue = [point for point in days if point.revenue]
    best = max(with_revenue, key=lambda point: point.revenue or 0.0, default=None)
    zero = [point for point in days if point.spend > 0 and not point.revenue]
    spent = [point for point in days if point.spend > 0]
    last5 = spent[-5:]
    earlier = [point for point in days if last5 and point.start < last5[0].start]
    earlier_spend = sum(point.spend for point in earlier)
    return Highlights(
        best_revenue_day=best,
        zero_revenue_days=zero,
        last5_spend=sum(point.spend for point in last5),
        last5_days=len(last5),
        earlier_daily_spend=earlier_spend / len(earlier) if earlier else None,
    )


def _kpis(current: Totals, before: Totals) -> list[Kpi]:
    def kpi(
        key: str, label: str, now: float | None, then: float | None, lower: bool = False
    ) -> Kpi:
        return Kpi(key, label, now, then, change(now, then), lower)

    return [
        kpi("revenue", "Doanh số (xét KPI)", current.revenue, before.revenue),
        kpi("revenue_net", "Doanh thu hoa hồng", current.revenue_net, before.revenue_net),
        kpi("revenue_sg", "Doanh số SG", current.revenue_sg, before.revenue_sg),
        kpi("revenue_hn", "Doanh số HN", current.revenue_hn, before.revenue_hn),
        kpi("ads_ratio", "Ads / Doanh thu", current.ads_ratio, before.ads_ratio, True),
        kpi("spend", "Tiền chi tiêu", current.spend, before.spend),
        kpi("messages", "Số mess", current.messages, before.messages),
        kpi(
            "cost_per_message", "Giá mess", current.cost_per_message, before.cost_per_message, True
        ),
        kpi("phones", "SĐT thu được", current.phones, before.phones),
        kpi("phone_rate", "% mess ra số", current.phone_rate, before.phone_rate),
        kpi("cost_per_phone", "Chi phí / SĐT", current.cost_per_phone, before.cost_per_phone, True),
        kpi("invoice", "Số tiền lập hoá đơn", current.invoice, before.invoice),
    ]


__all__ = [
    "AccountLine",
    "AdsDashboardService",
    "Dashboard",
    "Kpi",
    "SeriesPoint",
    "Totals",
]
