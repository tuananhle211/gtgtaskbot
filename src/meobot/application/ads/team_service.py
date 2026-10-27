"""Quản lý - "Dashboard tổng" (0079; design "Dashboard Marketing Ads", board
Quản lý), for one month.

* **Khối 1** - the month's totals against the month before: "Doanh thu xét
  KPI" (the HCM site's revenue: the marketers' SG part and every ◆ source; the
  marketers' HN part counts for their own KPI only), its split HCM / HN chuyển
  HCM, the commission revenue (without the 8% VAT), Ads / Doanh thu (charges ÷
  commission revenue), Ads / DT thực (charges ÷ revenue), the KPI and how far
  the month is, revenue by source, spend, charges, phone numbers.
* **Khối 2** - Facebook Ads: every marketer ranked by revenue, and who needs
  attention (Ads / Doanh thu above 60%, or spend with no revenue). Google Ads
  and TikTok Ads come later (chốt 27/10: left out for now).
* **Khối 3** - the detail table per marketer.
* **Báo cáo tháng** (April to the month) and **Báo cáo ngày** (each day).

``site`` narrows the revenue: ``hcm`` without the "HN chuyển HCM" source, ``hn``
that source alone. ``connection_id`` narrows everything to one BM's ad accounts:
spend, charges, phone numbers and the marketers holding an account there; the
revenue is entered per person, not per BM, so it is left out (``revenue_note``
``filtered``, as on the staff dashboard). Customers, bookings and bill figures
are not entered yet.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.access import AdsAccess, AdsNotVisibleError
from meobot.application.ads.accounts_service import ads_people
from meobot.application.ads.revenue_service import TEAM_KEY, AdsRevenueService, RevenueRow
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.db.models.ads import AdsAccount, AdsChargeDaily, AdsConnection, AdsSpendDaily
from meobot.db.models.user import User
from meobot.domain.ads.models import (
    ADS_RATIO_WATCH,
    REVENUE_SOURCE_LABELS,
    Period,
    RevenueSource,
    commission_revenue,
    invoice_factor,
    ratio,
)

#: "Báo cáo tháng" starts here (the Sheet's first month) or at January.
REPORT_FROM_MONTH = 4
SITES = ("all", "hcm", "hn")


@dataclass(slots=True)
class Figures:
    spend: float = 0.0
    invoice: float = 0.0
    phones: int = 0
    #: "Doanh thu xét KPI" (VAT included); ``revenue_net`` = commission revenue.
    revenue: float | None = None
    revenue_net: float | None = None

    @property
    def cost_per_phone(self) -> float | None:
        return ratio(self.spend, self.phones)

    @property
    def charged(self) -> float:
        """What Ads / Doanh thu divides: the charges, else (no charges yet,
        before July 2026) the spend."""
        return self.invoice or self.spend

    @property
    def ads_ratio(self) -> float | None:
        """Ads / Doanh thu (hoa hồng)."""
        return ratio(self.charged, self.revenue_net) if self.revenue_net else None

    @property
    def ads_ratio_real(self) -> float | None:
        """Ads / DT thực: over the revenue VAT included."""
        return ratio(self.invoice, self.revenue) if self.revenue and self.invoice else None

    def add_revenue(self, gross: Decimal, day: date) -> None:
        self.revenue = (self.revenue or 0.0) + float(gross)
        self.revenue_net = (self.revenue_net or 0.0) + float(commission_revenue(gross, day))


@dataclass(slots=True)
class MarketerLine:
    user_id: uuid.UUID
    name: str
    member_code: str | None
    #: Spend, charges, phones of their ad accounts; revenue = SG + HN.
    current: Figures
    kpi: float | None = None

    @property
    def kpi_rate(self) -> float | None:
        return ratio(self.current.revenue or 0, self.kpi) if self.kpi else None

    @property
    def ads_ratio(self) -> float | None:
        """A marketer's Ads / Doanh thu: spend ÷ commission revenue."""
        net = self.current.revenue_net
        return ratio(self.current.spend, net) if net else None


@dataclass(frozen=True, slots=True)
class SourceLine:
    key: str
    label: str
    revenue: float | None


@dataclass(frozen=True, slots=True)
class Alert:
    user_id: uuid.UUID
    name: str
    #: ``high_ratio`` / ``no_revenue``.
    kind: str
    spend: float
    revenue: float | None
    ads_ratio: float | None
    phones: int


@dataclass(frozen=True, slots=True)
class MonthLine:
    month: date
    figures: Figures
    kpi: float | None

    @property
    def kpi_rate(self) -> float | None:
        return ratio(self.figures.revenue or 0, self.kpi) if self.kpi else None


@dataclass(frozen=True, slots=True)
class DayLine:
    day: date
    figures: Figures


@dataclass(slots=True)
class TeamReport:
    month: Period
    previous: Period
    site: str
    current: Figures
    before: Figures
    #: The HCM site (everything but "HN chuyển HCM") and that source.
    revenue_hcm: float | None
    revenue_hn_transfer: float | None
    #: The marketers' HN part: their KPI, not the site's revenue.
    marketers_hn: float | None
    team_kpi: float | None
    #: Share of the month gone (days through yesterday ÷ days in the month).
    month_progress: float
    sources: list[SourceLine]
    marketers: list[MarketerLine]
    facebook: Figures
    attention: list[Alert] = field(default_factory=list)
    months: list[MonthLine] = field(default_factory=list)
    days: list[DayLine] = field(default_factory=list)
    #: The BM filter in force and every BM to pick from.
    connection_id: uuid.UUID | None = None
    connections: list[AdsConnection] = field(default_factory=list)
    #: ``filtered`` = a BM is picked: no revenue (not split by BM).
    revenue_note: str | None = None

    @property
    def team_kpi_rate(self) -> float | None:
        return ratio(self.current.revenue or 0, self.team_kpi) if self.team_kpi else None


def month_period(first: date) -> Period:
    start = first.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return Period(start=start, end=end)


def _in_site(row: RevenueRow, site: str) -> bool:
    """Whether a revenue line counts for the site's revenue (marketers: their
    SG part, handled by the caller)."""
    transfer = row.source is RevenueSource.HN
    if site == "hcm":
        return not transfer
    if site == "hn":
        return transfer
    return True


class AdsTeamService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def report(
        self,
        access: AdsAccess,
        *,
        month: date,
        site: str = "all",
        connection_id: uuid.UUID | None = None,
    ) -> TeamReport:
        access.require_view()
        if not access.view_all:
            raise AdsNotVisibleError()
        site = site if site in SITES else "all"
        period = month_period(month)
        previous = month_period(period.start - timedelta(days=1))
        first_month = period.start.replace(month=min(REPORT_FROM_MONTH, period.start.month))
        window = Period(start=first_month, end=period.end)

        filtered = connection_id is not None
        accounts = {
            account.account_id: account
            for account in await self._session.scalars(select(AdsAccount))
            if not filtered or account.connection_id == connection_id
        }
        spend_rows = [
            row
            for row in await self._session.execute(
                select(
                    AdsSpendDaily.day,
                    AdsSpendDaily.account_id,
                    AdsSpendDaily.spend,
                    AdsSpendDaily.phones,
                ).where(AdsSpendDaily.day >= window.start, AdsSpendDaily.day <= window.end)
            )
            if row.account_id in accounts
        ]
        charge_rows = [
            row
            for row in await self._session.execute(
                select(AdsChargeDaily.day, AdsChargeDaily.account_id, AdsChargeDaily.amount).where(
                    AdsChargeDaily.day >= window.start, AdsChargeDaily.day <= window.end
                )
            )
            if row.account_id in accounts
        ]
        revenue_service = AdsRevenueService(self._session, AuditService(self._session))
        revenue_rows = [] if filtered else await revenue_service.rows(window.start, window.end)

        def team(span: Period) -> Figures:
            figures = Figures()
            for day, _account_id, spend, phones in spend_rows:
                if span.start <= day <= span.end:
                    figures.spend += float(spend)
                    figures.phones += phones
            for day, account_id, amount in charge_rows:
                if span.start <= day <= span.end:
                    account = accounts[account_id]
                    figures.invoice += float(
                        amount * invoice_factor(account.name, account.brand, day)
                    )
            for row in revenue_rows:
                # The site's revenue: a marketer's SG part, a source's amount.
                if span.start <= row.day <= span.end and _in_site(row, site) and row.amount:
                    figures.add_revenue(row.amount, row.day)
            return figures

        current = team(period)
        before = team(previous)
        in_month = [row for row in revenue_rows if period.start <= row.day <= period.end]

        def total(values: Iterable[Decimal]) -> float | None:
            items = list(values)
            return float(sum(items, Decimal(0))) if items else None

        transfer = total(row.amount for row in in_month if row.source is RevenueSource.HN)
        sources = [
            SourceLine(
                "sg",
                "Doanh số SG",
                total(row.amount for row in in_month if row.user_id is not None and row.amount),
            ),
            SourceLine("s:HN", "Doanh số chuyển miền", transfer),
            *[
                SourceLine(
                    f"s:{source.value}",
                    REVENUE_SOURCE_LABELS[source],
                    total(row.amount for row in in_month if row.source is source),
                )
                for source in RevenueSource
                if source is not RevenueSource.HN
            ],
        ]

        # The marketers: their accounts' numbers and their own revenue (SG + HN).
        by_person: dict[uuid.UUID, Figures] = defaultdict(Figures)
        for day, account_id, spend, phones in spend_rows:
            owner = accounts[account_id].user_id
            if owner is not None and period.start <= day <= period.end:
                by_person[owner].spend += float(spend)
                by_person[owner].phones += phones
        for day, account_id, amount in charge_rows:
            account = accounts[account_id]
            if account.user_id is not None and period.start <= day <= period.end:
                by_person[account.user_id].invoice += float(
                    amount * invoice_factor(account.name, account.brand, day)
                )
        for row in in_month:
            if row.user_id is not None and row.gross:
                by_person[row.user_id].add_revenue(row.gross, row.day)
        kpis = {} if filtered else await revenue_service.kpi_amounts(period.start)
        staff = {user.id: user for user in await ads_people(self._session)}
        if filtered:
            # One BM: the people holding an account in it.
            holders = {account.user_id for account in accounts.values() if account.user_id}
            staff = {user_id: user for user_id, user in staff.items() if user_id in holders}
        missing = set(by_person) - set(staff)
        if missing:
            for user in await self._session.scalars(select(User).where(User.id.in_(missing))):
                staff[user.id] = user
        marketers = [
            MarketerLine(
                user_id=user_id,
                name=staff[user_id].full_name,
                member_code=staff[user_id].member_code,
                current=by_person.get(user_id, Figures()),
                kpi=float(kpis[f"u:{user_id}"]) if f"u:{user_id}" in kpis else None,
            )
            for user_id in set(staff) | set(by_person)
            if user_id in staff
        ]
        marketers.sort(
            key=lambda line: (-(line.current.revenue or 0.0), -line.current.spend, line.name)
        )

        facebook = Figures()
        for line in marketers:
            facebook.spend += line.current.spend
            facebook.invoice += line.current.invoice
            facebook.phones += line.current.phones
            if line.current.revenue is not None:
                facebook.revenue = (facebook.revenue or 0.0) + line.current.revenue
                facebook.revenue_net = (facebook.revenue_net or 0.0) + (
                    line.current.revenue_net or 0.0
                )

        attention: list[Alert] = []
        # Without revenue (a BM picked) every spender would look "no revenue".
        for line in [] if filtered else marketers:
            numbers = line.current
            if numbers.spend <= 0:
                continue
            if not numbers.revenue:
                kind = "no_revenue"
            elif (line.ads_ratio or 0.0) > ADS_RATIO_WATCH:
                kind = "high_ratio"
            else:
                continue
            attention.append(
                Alert(
                    line.user_id,
                    line.name,
                    kind,
                    numbers.spend,
                    numbers.revenue_net,
                    line.ads_ratio,
                    numbers.phones,
                )
            )
        attention.sort(
            key=lambda alert: (alert.kind != "no_revenue", -(alert.ads_ratio or 0.0), -alert.spend)
        )

        months: list[MonthLine] = []
        cursor = window.start
        while cursor <= period.start:
            span = month_period(cursor)
            amounts = {} if filtered else await revenue_service.kpi_amounts(span.start)
            months.append(
                MonthLine(
                    span.start,
                    team(span),
                    float(amounts[TEAM_KEY]) if TEAM_KEY in amounts else None,
                )
            )
            cursor = span.end + timedelta(days=1)

        days = [
            DayLine(day, team(Period(start=day, end=day)))
            for day in (period.start + timedelta(days=offset) for offset in range(period.days))
            if day <= max(vn_today(), period.start)
        ]

        today = vn_today()
        if today > period.end:
            progress = 1.0
        elif today <= period.start:
            progress = 0.0
        else:
            progress = (today - period.start).days / period.days

        return TeamReport(
            month=period,
            previous=previous,
            site=site,
            current=current,
            before=before,
            revenue_hcm=total(
                row.amount for row in in_month if row.amount and row.source is not RevenueSource.HN
            ),
            revenue_hn_transfer=transfer,
            marketers_hn=total(row.amount_hn for row in in_month if row.amount_hn),
            team_kpi=float(kpis[TEAM_KEY]) if TEAM_KEY in kpis else None,
            month_progress=progress,
            sources=sources,
            marketers=marketers,
            facebook=facebook,
            attention=attention[:5],
            months=months,
            days=days,
            connection_id=connection_id,
            connections=list(
                await self._session.scalars(select(AdsConnection).order_by(AdsConnection.name))
            ),
            revenue_note="filtered" if filtered else None,
        )


__all__ = [
    "AdsTeamService",
    "Alert",
    "DayLine",
    "Figures",
    "MarketerLine",
    "MonthLine",
    "SourceLine",
    "TeamReport",
]
