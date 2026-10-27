"""Wire shapes for Phòng Ads (``/api/ads``, 0078)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meobot.application.ads.accounts_service import AccountView
from meobot.application.ads.dashboard_service import Dashboard, SeriesPoint
from meobot.application.ads.revenue_service import EntryLine
from meobot.application.ads.team_service import Figures, History, TeamReport
from meobot.db.models.ads import AdsConnection
from meobot.domain.ads.models import BRAND_LABELS, AdsBrand, ads_ratio_tone


class AdsConnectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    business_id: str
    site: str
    note: str | None
    #: "••••abcd": never the token.
    token_hint: str
    active: bool
    meta_name: str | None
    account_count: int
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    last_spend_sync_at: datetime | None
    last_spend_sync_ok: bool | None
    last_spend_sync_message: str | None
    last_charge_sync_at: datetime | None
    last_charge_sync_ok: bool | None
    last_charge_sync_message: str | None

    @classmethod
    def from_row(cls, row: AdsConnection) -> AdsConnectionResponse:
        return cls(
            id=row.id,
            name=row.name,
            business_id=row.business_id,
            site=row.site,
            note=row.note,
            token_hint=row.token_hint,
            active=row.active,
            meta_name=row.meta_name,
            account_count=row.account_count,
            last_tested_at=row.last_tested_at,
            last_test_ok=row.last_test_ok,
            last_test_message=row.last_test_message,
            last_spend_sync_at=row.last_spend_sync_at,
            last_spend_sync_ok=row.last_spend_sync_ok,
            last_spend_sync_message=row.last_spend_sync_message,
            last_charge_sync_at=row.last_charge_sync_at,
            last_charge_sync_ok=row.last_charge_sync_ok,
            last_charge_sync_message=row.last_charge_sync_message,
        )


class AdsConnectionsResponse(BaseModel):
    connections: list[AdsConnectionResponse]
    #: False: the server has no encryption key, tokens cannot be stored.
    secret_ready: bool
    #: The cơ sở already in use, for the field's suggestions.
    sites: list[str]


class CreateAdsConnectionRequest(BaseModel):
    name: str = Field(max_length=80)
    business_id: str = Field(max_length=40)
    site: str = Field(default="", max_length=60)
    token: str = Field(max_length=1000)
    note: str | None = Field(default=None, max_length=300)


class UpdateAdsConnectionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    business_id: str | None = Field(default=None, max_length=40)
    site: str | None = Field(default=None, max_length=60)
    #: Blank = keep the stored token.
    token: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=300)
    active: bool | None = None


class AdsConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int
    connection: AdsConnectionResponse


class AdsSyncRequest(BaseModel):
    #: ``spend`` (chi phí) or ``charges`` (hoá đơn).
    kind: str = Field(pattern="^(spend|charges)$")
    #: Both or neither: neither = what the schedule would read now.
    since: date | None = None
    until: date | None = None
    connection_id: uuid.UUID | None = None


class AdsSyncResponse(BaseModel):
    task_id: str
    job_key: str


class PersonOption(BaseModel):
    user_id: uuid.UUID
    name: str
    member_code: str | None


class AdsAccountResponse(BaseModel):
    account_id: str
    name: str
    connection_id: uuid.UUID
    connection_name: str
    site: str
    status: int | None
    #: 1 = active on Meta.
    active: bool
    brand: str
    brand_label: str
    user_id: uuid.UUID | None
    user_name: str | None
    spend: float
    charged: float
    last_seen_at: datetime | None

    @classmethod
    def from_view(cls, view: AccountView) -> AdsAccountResponse:
        account = view.account
        return cls(
            account_id=account.account_id,
            name=account.name,
            connection_id=view.connection.id,
            connection_name=view.connection.name,
            site=view.connection.site,
            status=account.status,
            active=account.status == 1,
            brand=account.brand.value,
            brand_label=BRAND_LABELS[account.brand],
            user_id=account.user_id,
            user_name=view.user.full_name if view.user else None,
            spend=view.spend,
            charged=view.charged,
            last_seen_at=account.last_seen_at,
        )


class BrandOption(BaseModel):
    value: str
    label: str


class AdsAccountsResponse(BaseModel):
    month: str
    accounts: list[AdsAccountResponse]
    people: list[PersonOption]
    brands: list[BrandOption]
    can_manage: bool


class AssignAdsAccountRequest(BaseModel):
    #: ``null`` = "Chưa gán". Leave out to keep.
    user_id: uuid.UUID | None = None
    brand: AdsBrand | None = None


class KpiResponse(BaseModel):
    key: str
    label: str
    value: float | None
    previous: float | None
    change: float | None
    lower_is_better: bool


class SeriesPointResponse(BaseModel):
    start: date
    spend: float
    messages: int
    phones: int
    cost_per_phone: float | None
    revenue: float | None = None
    revenue_net: float | None = None
    ads_ratio: float | None = None

    @classmethod
    def from_point(cls, point: SeriesPoint) -> SeriesPointResponse:
        return cls(
            start=point.start,
            spend=point.spend,
            messages=point.messages,
            phones=point.phones,
            cost_per_phone=point.cost_per_phone,
            revenue=point.revenue,
            revenue_net=point.revenue_net,
            ads_ratio=point.ads_ratio,
        )


class AccountLineResponse(BaseModel):
    account_id: str
    name: str
    connection_name: str
    site: str
    brand: str
    brand_label: str
    user_id: uuid.UUID | None
    user_name: str | None
    spend: float
    messages: int
    phones: int
    charged: float
    invoice: float
    difference: float
    matched: bool
    card_last4: str | None = None
    #: "LHĐ / chi tiêu".
    invoice_ratio: float | None = None
    #: One value per entry of the dashboard's ``days``.
    spend_by_day: list[float] = []
    invoice_by_day: list[float] = []


class HighlightsResponse(BaseModel):
    best_revenue_day: SeriesPointResponse | None
    zero_revenue_days: list[SeriesPointResponse]
    last5_spend: float
    last5_days: int
    earlier_daily_spend: float | None


class ConnectionOption(BaseModel):
    id: uuid.UUID
    name: str
    site: str


class AdsDashboardResponse(BaseModel):
    start: date
    end: date
    previous_start: date
    previous_end: date
    grouping: str
    user_id: uuid.UUID | None
    kpis: list[KpiResponse]
    series: list[SeriesPointResponse]
    best_day: SeriesPointResponse | None
    review_day: SeriesPointResponse | None
    accounts: list[AccountLineResponse]
    people: list[PersonOption]
    connections: list[ConnectionOption]
    sites: list[str]
    brands: list[BrandOption]
    last_synced_at: datetime | None
    #: Entered by the Trưởng phòng (0079); ``null`` = none entered for the period.
    revenue: float | None = None
    #: ``filtered``: a brand / BM / cơ sở filter is on, revenue is not split that way.
    revenue_note: str | None = None
    #: "Doanh thu hoa hồng" of the period (revenue without the 8% VAT).
    revenue_net: float | None = None
    highlights: HighlightsResponse | None = None
    #: The days of the per-day tables (empty for periods over 62 days).
    days: list[date] = []

    @classmethod
    def from_domain(cls, data: Dashboard) -> AdsDashboardResponse:
        return cls(
            start=data.period.start,
            end=data.period.end,
            previous_start=data.previous.start,
            previous_end=data.previous.end,
            grouping=data.grouping.value,
            user_id=data.user_id,
            kpis=[
                KpiResponse(
                    key=kpi.key,
                    label=kpi.label,
                    value=kpi.value,
                    previous=kpi.previous,
                    change=kpi.change,
                    lower_is_better=kpi.lower_is_better,
                )
                for kpi in data.kpis
            ],
            series=[SeriesPointResponse.from_point(point) for point in data.series],
            best_day=SeriesPointResponse.from_point(data.best_day) if data.best_day else None,
            review_day=SeriesPointResponse.from_point(data.review_day) if data.review_day else None,
            accounts=[
                AccountLineResponse(
                    account_id=line.account_id,
                    name=line.name,
                    connection_name=line.connection_name,
                    site=line.site,
                    brand=line.brand.value,
                    brand_label=line.brand_label,
                    user_id=line.user_id,
                    user_name=line.user_name,
                    spend=line.spend,
                    messages=line.messages,
                    phones=line.phones,
                    charged=line.charged,
                    invoice=line.invoice,
                    difference=line.difference,
                    matched=line.matched,
                    card_last4=line.card_last4,
                    invoice_ratio=line.invoice_ratio,
                    spend_by_day=line.spend_by_day,
                    invoice_by_day=line.invoice_by_day,
                )
                for line in data.accounts
            ],
            people=[
                PersonOption(user_id=user.id, name=user.full_name, member_code=user.member_code)
                for user in data.people
            ],
            connections=[
                ConnectionOption(id=row.id, name=row.name, site=row.site)
                for row in data.connections
            ],
            sites=sorted({row.site for row in data.connections if row.site}),
            brands=brand_options(),
            last_synced_at=data.last_synced_at,
            revenue=data.current.revenue,
            revenue_note=data.revenue_note,
            revenue_net=data.current.revenue_net,
            highlights=HighlightsResponse(
                best_revenue_day=SeriesPointResponse.from_point(data.highlights.best_revenue_day)
                if data.highlights.best_revenue_day
                else None,
                zero_revenue_days=[
                    SeriesPointResponse.from_point(point)
                    for point in data.highlights.zero_revenue_days
                ],
                last5_spend=data.highlights.last5_spend,
                last5_days=data.highlights.last5_days,
                earlier_daily_spend=data.highlights.earlier_daily_spend,
            )
            if data.highlights
            else None,
            days=data.days,
        )


class EntryLineResponse(BaseModel):
    #: ``u:<user id>``, ``s:<source>`` or ``team``.
    key: str
    label: str
    user_id: uuid.UUID | None
    source: str | None
    member_code: str | None
    #: ``null`` = not entered (not the same as 0). A marketer's "Doanh số SG".
    amount: float | None
    #: A marketer's "Doanh số HN" (saved as ``<key>:hn``); ``null`` for others.
    amount_hn: float | None = None

    @classmethod
    def from_line(cls, line: EntryLine) -> EntryLineResponse:
        return cls(
            key=line.key,
            label=line.label,
            user_id=line.user_id,
            source=line.source.value if line.source else None,
            member_code=line.member_code,
            amount=line.amount,
            amount_hn=line.amount_hn,
        )


class RevenueDayResponse(BaseModel):
    day: date
    lines: list[EntryLineResponse]
    total: float | None


class SaveRevenueRequest(BaseModel):
    day: date
    #: ``key -> amount`` (``null`` or "" = clear the line).
    entries: dict[str, float | str | None]


class KpiMonthResponse(BaseModel):
    month: str
    lines: list[EntryLineResponse]
    can_edit: bool


class SaveKpiRequest(BaseModel):
    #: ``YYYY-MM``.
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    entries: dict[str, float | str | None]


class FiguresResponse(BaseModel):
    spend: float
    invoice: float
    phones: int
    revenue: float | None
    revenue_net: float | None
    cost_per_phone: float | None
    ads_ratio: float | None
    ads_ratio_real: float | None

    @classmethod
    def from_figures(cls, figures: Figures) -> FiguresResponse:
        return cls(
            spend=figures.spend,
            invoice=figures.invoice,
            phones=figures.phones,
            revenue=figures.revenue,
            revenue_net=figures.revenue_net,
            cost_per_phone=figures.cost_per_phone,
            ads_ratio=figures.ads_ratio,
            ads_ratio_real=figures.ads_ratio_real,
        )


class MarketerResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    member_code: str | None
    figures: FiguresResponse
    kpi: float | None
    kpi_rate: float | None
    #: Spend ÷ commission revenue, and its band (good / watch / bad).
    ads_ratio: float | None
    tone: str | None


class SourceResponse(BaseModel):
    key: str
    label: str
    revenue: float | None
    #: Without the 8% VAT ("Doanh thu hoa hồng").
    revenue_net: float | None = None


class AlertResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    kind: str
    spend: float
    revenue: float | None
    ads_ratio: float | None
    phones: int


class MonthLineResponse(BaseModel):
    month: date
    figures: FiguresResponse
    kpi: float | None
    kpi_rate: float | None


class DayLineResponse(BaseModel):
    day: date
    figures: FiguresResponse


class TeamReportResponse(BaseModel):
    month: str
    start: date
    end: date
    site: str
    current: FiguresResponse
    before: FiguresResponse
    revenue_hcm: float | None
    revenue_hn_transfer: float | None
    marketers_hn: float | None
    team_kpi: float | None
    team_kpi_rate: float | None
    month_progress: float
    sources: list[SourceResponse]
    marketers: list[MarketerResponse]
    facebook: FiguresResponse
    attention: list[AlertResponse]
    connection_id: uuid.UUID | None = None
    connections: list[ConnectionOption] = []
    #: ``filtered``: a BM is picked, revenue is not split that way.
    revenue_note: str | None = None

    @classmethod
    def from_report(cls, report: TeamReport) -> TeamReportResponse:
        return cls(
            month=f"{report.month.start:%Y-%m}",
            start=report.month.start,
            end=report.month.end,
            site=report.site,
            current=FiguresResponse.from_figures(report.current),
            before=FiguresResponse.from_figures(report.before),
            revenue_hcm=report.revenue_hcm,
            revenue_hn_transfer=report.revenue_hn_transfer,
            marketers_hn=report.marketers_hn,
            team_kpi=report.team_kpi,
            team_kpi_rate=report.team_kpi_rate,
            month_progress=report.month_progress,
            sources=[
                SourceResponse(
                    key=item.key,
                    label=item.label,
                    revenue=item.revenue,
                    revenue_net=item.revenue_net,
                )
                for item in report.sources
            ],
            marketers=[
                MarketerResponse(
                    user_id=line.user_id,
                    name=line.name,
                    member_code=line.member_code,
                    figures=FiguresResponse.from_figures(line.current),
                    kpi=line.kpi,
                    kpi_rate=line.kpi_rate,
                    ads_ratio=line.ads_ratio,
                    tone=ads_ratio_tone(line.ads_ratio),
                )
                for line in report.marketers
            ],
            facebook=FiguresResponse.from_figures(report.facebook),
            attention=[
                AlertResponse(
                    user_id=item.user_id,
                    name=item.name,
                    kind=item.kind,
                    spend=item.spend,
                    revenue=item.revenue,
                    ads_ratio=item.ads_ratio,
                    phones=item.phones,
                )
                for item in report.attention
            ],
            connection_id=report.connection_id,
            connections=[
                ConnectionOption(id=row.id, name=row.name, site=row.site)
                for row in report.connections
            ],
            revenue_note=report.revenue_note,
        )


def brand_options() -> list[BrandOption]:
    return [BrandOption(value=brand.value, label=BRAND_LABELS[brand]) for brand in AdsBrand]


class HistoryResponse(BaseModel):
    """ "Báo cáo tháng" + "Báo cáo ngày" of the team (``user_id`` null) or one
    marketer."""

    month: str
    start: date
    end: date
    user_id: uuid.UUID | None
    months: list[MonthLineResponse]
    days: list[DayLineResponse]
    #: ``filtered``: a BM is picked, revenue is not split that way.
    revenue_note: str | None = None

    @classmethod
    def from_history(cls, history: History) -> HistoryResponse:
        return cls(
            month=f"{history.month.start:%Y-%m}",
            start=history.month.start,
            end=history.month.end,
            user_id=history.user_id,
            months=[
                MonthLineResponse(
                    month=line.month,
                    figures=FiguresResponse.from_figures(line.figures),
                    kpi=line.kpi,
                    kpi_rate=line.kpi_rate,
                )
                for line in history.months
            ],
            days=[
                DayLineResponse(day=line.day, figures=FiguresResponse.from_figures(line.figures))
                for line in history.days
            ],
            revenue_note=history.revenue_note,
        )


__all__ = [
    "AdsAccountsResponse",
    "AdsConnectionResponse",
    "AdsConnectionTestResponse",
    "AdsConnectionsResponse",
    "AdsDashboardResponse",
    "AdsSyncRequest",
    "AdsSyncResponse",
    "AssignAdsAccountRequest",
    "CreateAdsConnectionRequest",
    "HistoryResponse",
    "UpdateAdsConnectionRequest",
    "brand_options",
]
