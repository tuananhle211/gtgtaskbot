"""Quản lý - "Dashboard tổng" (0079; design "Dashboard Marketing Ads", board
Quản lý), for one month.

* **Khối 1** - the month's totals against the month before: "Doanh thu xét
  KPI" (the HCM site's revenue: the marketers' SG part and every ◆ source; the
  marketers' HN part counts for their own KPI only), its split HCM / HN chuyển
  HCM, the commission revenue (without the 8% VAT), Ads / Doanh thu (charges ÷
  commission revenue), Ads / DT thực (charges ÷ revenue), the KPI and how far
  the month is, revenue by source, spend, charges, phone numbers.
* **Khối 2** - per platform (``channels``, whatever ``platform`` is picked):
  Facebook Ads with every marketer ranked by revenue and who needs attention
  (Ads / Doanh thu above 60%, or spend with no revenue); Google Ads and TikTok
  Ads (0081, typed by hand) with their spend, invoices, their ◆ revenue line
  and the marketers ranked by spend.
* **Khối 3** - the detail table per marketer.
* **Báo cáo tháng** (April to the month) and **Báo cáo ngày** (each day) -
  :meth:`AdsTeamService.history`, for the team or one marketer (their own
  revenue SG + HN, their KPI); a member gets their own.

``start`` / ``end`` read any range instead of a month (compared with the same
number of days just before); the KPI always belongs to the month the range
ends in, measured on that month's revenue from its 1st to the range's end.
``site`` narrows the revenue: ``hcm`` without the "HN chuyển HCM" source, ``hn``
that source alone. ``connection_id`` narrows everything to one BM's ad accounts:
spend, charges, phone numbers and the marketers holding an account there; the
revenue is entered per person, not per BM, so it is left out (``revenue_note``
``filtered``, as on the staff dashboard). Customers, bookings and bill figures
are not entered yet.

``platform`` (0081) switches Khối 1, Khối 3 and the reports to one platform's
ad accounts: Facebook (default) as above; Google / TikTok Ads take their ◆
revenue line (entered per source, not per marketer - ``revenue_note``
``platform``) and no KPI (set for the whole department), and list the
marketers holding an account there. A BM is Facebook's: ignored otherwise.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

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
    MANUAL_PLATFORMS,
    PLATFORM_LABELS,
    PLATFORM_SOURCES,
    REVENUE_SOURCE_LABELS,
    AdsPlatform,
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
    #: Their revenue (SG + HN) from the KPI month's 1st to the range's end.
    kpi_revenue: float | None = None

    @property
    def kpi_rate(self) -> float | None:
        return ratio(self.kpi_revenue or 0, self.kpi) if self.kpi else None

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
    #: The same without the 8% VAT ("Doanh thu hoa hồng"), for Khối 2.
    revenue_net: float | None = None


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


@dataclass(slots=True)
class ChannelPerson:
    user_id: uuid.UUID
    name: str
    #: Their accounts on the platform; revenue only on Facebook (SG + HN).
    figures: Figures

    @property
    def ads_ratio(self) -> float | None:
        net = self.figures.revenue_net
        return ratio(self.figures.spend, net) if net else None


@dataclass(slots=True)
class Channel:
    """One platform's line of Khối 2."""

    platform: AdsPlatform
    label: str
    figures: Figures
    #: Facebook: by revenue; Google / TikTok: by spend.
    people: list[ChannelPerson]
    accounts: int


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
    #: Share of the KPI month gone (days through yesterday ÷ days in it).
    month_progress: float
    #: The site's revenue from the KPI month's 1st to the range's end.
    kpi_revenue: float | None
    sources: list[SourceLine]
    marketers: list[MarketerLine]
    facebook: Figures
    attention: list[Alert] = field(default_factory=list)
    #: The BM filter in force and every BM to pick from.
    connection_id: uuid.UUID | None = None
    connections: list[AdsConnection] = field(default_factory=list)
    #: ``filtered`` = a BM is picked: no revenue (not split by BM);
    #: ``platform`` = Google / TikTok: revenue is their ◆ line, no KPI.
    revenue_note: str | None = None
    platform: AdsPlatform = AdsPlatform.FACEBOOK
    #: Khối 2: every platform, whichever is picked.
    channels: list[Channel] = field(default_factory=list)

    @property
    def team_kpi_rate(self) -> float | None:
        return ratio(self.kpi_revenue or 0, self.team_kpi) if self.team_kpi else None


@dataclass(frozen=True, slots=True)
class History:
    """ "Báo cáo tháng" and "Báo cáo ngày" of the team or one marketer."""

    month: Period
    #: ``None`` = the team.
    user_id: uuid.UUID | None
    months: list[MonthLine]
    days: list[DayLine]
    #: ``filtered`` = a BM is picked: no revenue (not split by BM);
    #: ``platform`` = one marketer's Google / TikTok numbers: no revenue.
    revenue_note: str | None = None
    platform: AdsPlatform = AdsPlatform.FACEBOOK


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


@dataclass(slots=True)
class _Raw:
    """Every account's rows in a window, loaded once and narrowed per platform."""

    accounts: list[AdsAccount]
    spend_rows: list[Any]
    charge_rows: list[Any]
    revenue_rows: list[RevenueRow]

    def narrow(
        self,
        *,
        platform: AdsPlatform = AdsPlatform.FACEBOOK,
        connection_id: uuid.UUID | None = None,
        person: uuid.UUID | None = None,
        site: str = "all",
    ) -> _Book:
        manual = platform in MANUAL_PLATFORMS
        if manual:
            connection_id = None
        accounts = {
            account.account_id: account
            for account in self.accounts
            if account.platform is platform
            and (connection_id is None or account.connection_id == connection_id)
            and (person is None or account.user_id == person)
        }
        if connection_id is not None:
            revenue: list[RevenueRow] = []
        elif manual:
            # Entered on the platform's ◆ line: the department's, nobody's own.
            source = PLATFORM_SOURCES[platform]
            revenue = (
                [] if person is not None else [r for r in self.revenue_rows if r.source is source]
            )
        else:
            revenue = [r for r in self.revenue_rows if person is None or r.user_id == person]
        return _Book(
            accounts,
            [row for row in self.spend_rows if row.account_id in accounts],
            [row for row in self.charge_rows if row.account_id in accounts],
            revenue,
            site=site,
            person=person,
        )


def _by_person(book: _Book, period: Period) -> dict[uuid.UUID, Figures]:
    """Each marketer's spend, charges and phones on the book's accounts in the
    period, and their own revenue (SG + HN) where the book carries it."""
    by_person: dict[uuid.UUID, Figures] = defaultdict(Figures)
    for day, account_id, spend, phones in book.spend_rows:
        owner = book.accounts[account_id].user_id
        if owner is not None and period.start <= day <= period.end:
            by_person[owner].spend += float(spend)
            by_person[owner].phones += phones
    for day, account_id, amount in book.charge_rows:
        account = book.accounts[account_id]
        if account.user_id is not None and period.start <= day <= period.end:
            by_person[account.user_id].invoice += float(
                amount * invoice_factor(account.name, account.brand, day)
            )
    for row in book.revenue_rows:
        if row.user_id is not None and row.gross and period.start <= row.day <= period.end:
            by_person[row.user_id].add_revenue(row.gross, row.day)
    return by_person


def _total_of(people: Iterable[Figures]) -> Figures:
    total = Figures()
    for figures in people:
        total.spend += figures.spend
        total.invoice += figures.invoice
        total.phones += figures.phones
        if figures.revenue is not None:
            total.revenue = (total.revenue or 0.0) + figures.revenue
            total.revenue_net = (total.revenue_net or 0.0) + (figures.revenue_net or 0.0)
    return total


@dataclass(slots=True)
class _Book:
    """A window's synced rows and revenue lines, already narrowed to a BM or a
    marketer."""

    accounts: dict[str, AdsAccount]
    spend_rows: list[Any]
    charge_rows: list[Any]
    revenue_rows: list[RevenueRow]
    site: str = "all"
    #: One marketer: their own revenue (SG + HN), not the site's.
    person: uuid.UUID | None = None

    def figures(self, span: Period) -> Figures:
        figures = Figures()
        for day, _account_id, spend, phones in self.spend_rows:
            if span.start <= day <= span.end:
                figures.spend += float(spend)
                figures.phones += phones
        for day, account_id, amount in self.charge_rows:
            if span.start <= day <= span.end:
                account = self.accounts[account_id]
                figures.invoice += float(amount * invoice_factor(account.name, account.brand, day))
        for row in self.revenue_rows:
            if not span.start <= row.day <= span.end:
                continue
            if self.person is not None:
                if row.gross:
                    figures.add_revenue(row.gross, row.day)
            # The site's revenue: a marketer's SG part, a source's amount.
            elif _in_site(row, self.site) and row.amount:
                figures.add_revenue(row.amount, row.day)
        return figures


def _month_days(period: Period) -> list[date]:
    """The month's days up to today (a month not begun yet: its first day)."""
    last = max(vn_today(), period.start)
    return [
        day
        for day in (period.start + timedelta(days=offset) for offset in range(period.days))
        if day <= last
    ]


class AdsTeamService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load(self, window: Period, *, revenue: bool = True) -> _Raw:
        accounts = list(await self._session.scalars(select(AdsAccount)))
        spend_rows = list(
            await self._session.execute(
                select(
                    AdsSpendDaily.day,
                    AdsSpendDaily.account_id,
                    AdsSpendDaily.spend,
                    AdsSpendDaily.phones,
                ).where(AdsSpendDaily.day >= window.start, AdsSpendDaily.day <= window.end)
            )
        )
        charge_rows = list(
            await self._session.execute(
                select(AdsChargeDaily.day, AdsChargeDaily.account_id, AdsChargeDaily.amount).where(
                    AdsChargeDaily.day >= window.start, AdsChargeDaily.day <= window.end
                )
            )
        )
        revenue_rows = (
            await AdsRevenueService(self._session, AuditService(self._session)).rows(
                window.start, window.end
            )
            if revenue
            else []
        )
        return _Raw(accounts, spend_rows, charge_rows, revenue_rows)

    async def history(
        self,
        access: AdsAccess,
        *,
        month: date,
        user_id: uuid.UUID | None = None,
        connection_id: uuid.UUID | None = None,
        site: str = "all",
        platform: AdsPlatform = AdsPlatform.FACEBOOK,
    ) -> History:
        """ "Báo cáo tháng" (April, or January, to the month) and "Báo cáo
        ngày" (the month's days): the team, or one marketer - a member always
        their own."""
        access.require_view()
        person = user_id if access.view_all else access.user_id
        manual = platform in MANUAL_PLATFORMS
        if manual:
            connection_id = None
        period = month_period(month)
        first_month = period.start.replace(month=min(REPORT_FROM_MONTH, period.start.month))
        raw = await self._load(Period(start=first_month, end=period.end))
        book = raw.narrow(
            platform=platform,
            connection_id=connection_id,
            person=person,
            site=site if site in SITES else "all",
        )
        no_kpi = connection_id is not None or manual
        revenue_service = AdsRevenueService(self._session, AuditService(self._session))
        kpi_key = f"u:{person}" if person is not None else TEAM_KEY
        months: list[MonthLine] = []
        cursor = first_month
        while cursor <= period.start:
            span = month_period(cursor)
            amounts = {} if no_kpi else await revenue_service.kpi_amounts(span.start)
            months.append(
                MonthLine(
                    span.start,
                    book.figures(span),
                    float(amounts[kpi_key]) if kpi_key in amounts else None,
                )
            )
            cursor = span.end + timedelta(days=1)
        return History(
            month=period,
            user_id=person,
            months=months,
            days=[
                DayLine(day, book.figures(Period(start=day, end=day)))
                for day in _month_days(period)
            ],
            revenue_note="filtered"
            if connection_id
            else "platform"
            if manual and person is not None
            else None,
            platform=platform,
        )

    async def report(
        self,
        access: AdsAccess,
        *,
        month: date,
        site: str = "all",
        connection_id: uuid.UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        platform: AdsPlatform = AdsPlatform.FACEBOOK,
    ) -> TeamReport:
        access.require_view()
        if not access.view_all:
            raise AdsNotVisibleError()
        site = site if site in SITES else "all"
        manual = platform in MANUAL_PLATFORMS
        if manual:
            connection_id = None
        if start is not None and end is not None:
            period = Period(start=start, end=end)
            previous = period.previous()
        else:
            period = month_period(month)
            previous = month_period(period.start - timedelta(days=1))
        kpi_month = month_period(period.end)
        kpi_span = Period(start=kpi_month.start, end=period.end)
        filtered = connection_id is not None
        raw = await self._load(Period(start=min(previous.start, kpi_month.start), end=period.end))
        books = {
            item: raw.narrow(
                platform=item,
                connection_id=connection_id if item is AdsPlatform.FACEBOOK else None,
                site=site,
            )
            for item in AdsPlatform
        }
        book = books[platform]
        accounts = book.accounts
        revenue_rows = book.revenue_rows
        revenue_service = AdsRevenueService(self._session, AuditService(self._session))
        team = book.figures

        current = team(period)
        before = team(previous)
        in_month = [row for row in revenue_rows if period.start <= row.day <= period.end]

        def total(values: Iterable[Decimal]) -> float | None:
            items = list(values)
            return float(sum(items, Decimal(0))) if items else None

        transfer = total(row.amount for row in in_month if row.source is RevenueSource.HN)

        def source_line(key: str, label: str, rows: list[RevenueRow]) -> SourceLine:
            return SourceLine(
                key,
                label,
                total(row.amount for row in rows),
                total(commission_revenue(row.amount, row.day) for row in rows),
            )

        sources = [
            source_line(
                "sg",
                "Doanh số SG",
                [row for row in in_month if row.user_id is not None and row.amount],
            ),
            source_line(
                "s:HN",
                "Doanh số chuyển miền",
                [row for row in in_month if row.source is RevenueSource.HN],
            ),
            *[
                source_line(
                    f"s:{source.value}",
                    REVENUE_SOURCE_LABELS[source],
                    [row for row in in_month if row.source is source],
                )
                for source in RevenueSource
                if source is not RevenueSource.HN
            ],
        ]

        # The marketers: their accounts' numbers and their own revenue (SG + HN).
        people = {item: _by_person(books[item], period) for item in AdsPlatform}
        by_person = people[platform]
        kpis = {} if filtered or manual else await revenue_service.kpi_amounts(kpi_month.start)
        kpi_revenue: dict[uuid.UUID, float] = defaultdict(float)
        for row in revenue_rows:
            if row.user_id is not None and kpi_span.start <= row.day <= kpi_span.end:
                kpi_revenue[row.user_id] += float(row.gross)
        seated = {user.id: user for user in await ads_people(self._session)}
        everybody = dict(seated)
        missing = {user_id for figures in people.values() for user_id in figures} - set(everybody)
        if missing:
            for user in await self._session.scalars(select(User).where(User.id.in_(missing))):
                everybody[user.id] = user
        # The seated people, and anybody else with numbers on the platform.
        staff = {
            user_id: user
            for user_id, user in everybody.items()
            if user_id in seated or user_id in by_person
        }
        if filtered or manual:
            # One BM, or Google / TikTok: the people holding an account there.
            holders = {account.user_id for account in accounts.values() if account.user_id}
            staff = {user_id: user for user_id, user in staff.items() if user_id in holders}
        marketers = [
            MarketerLine(
                user_id=user_id,
                name=staff[user_id].full_name,
                member_code=staff[user_id].member_code,
                current=by_person.get(user_id, Figures()),
                kpi=float(kpis[f"u:{user_id}"]) if f"u:{user_id}" in kpis else None,
                kpi_revenue=kpi_revenue.get(user_id),
            )
            for user_id in set(staff) | set(by_person)
            if user_id in staff
        ]
        marketers.sort(
            key=lambda line: (-(line.current.revenue or 0.0), -line.current.spend, line.name)
        )

        channels: list[Channel] = []
        for item in AdsPlatform:
            crew = [
                ChannelPerson(user_id, everybody[user_id].full_name, figures)
                for user_id, figures in people[item].items()
                if user_id in everybody
            ]
            if item in MANUAL_PLATFORMS:
                crew.sort(key=lambda line: (-line.figures.spend, line.name))
                # Spend of every account, revenue from the ◆ line.
                totals = books[item].figures(period)
            else:
                crew.sort(
                    key=lambda line: (
                        -(line.figures.revenue_net or 0.0),
                        -line.figures.spend,
                        line.name,
                    )
                )
                totals = _total_of(line.figures for line in crew)
            channels.append(
                Channel(item, PLATFORM_LABELS[item], totals, crew, len(books[item].accounts))
            )
        facebook = channels[0].figures

        attention: list[Alert] = []
        # Without revenue (a BM picked) every spender would look "no revenue".
        for person in [] if filtered else channels[0].people:
            numbers = person.figures
            if numbers.spend <= 0:
                continue
            if not numbers.revenue:
                kind = "no_revenue"
            elif (person.ads_ratio or 0.0) > ADS_RATIO_WATCH:
                kind = "high_ratio"
            else:
                continue
            attention.append(
                Alert(
                    person.user_id,
                    person.name,
                    kind,
                    numbers.spend,
                    numbers.revenue_net,
                    person.ads_ratio,
                    numbers.phones,
                )
            )
        attention.sort(
            key=lambda alert: (alert.kind != "no_revenue", -(alert.ads_ratio or 0.0), -alert.spend)
        )

        today = vn_today()
        if today > kpi_month.end:
            progress = 1.0
        elif today <= kpi_month.start:
            progress = 0.0
        else:
            progress = (today - kpi_month.start).days / kpi_month.days

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
            kpi_revenue=team(kpi_span).revenue,
            sources=sources,
            marketers=marketers,
            facebook=facebook,
            attention=attention[:5],
            connection_id=connection_id,
            connections=list(
                await self._session.scalars(select(AdsConnection).order_by(AdsConnection.name))
            ),
            revenue_note="filtered" if filtered else "platform" if manual else None,
            platform=platform,
            channels=channels,
        )


__all__ = [
    "AdsTeamService",
    "Alert",
    "Channel",
    "ChannelPerson",
    "DayLine",
    "Figures",
    "History",
    "MarketerLine",
    "MonthLine",
    "SourceLine",
    "TeamReport",
]
