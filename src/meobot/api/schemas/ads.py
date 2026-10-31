"""Wire shapes for Phòng Ads (``/api/ads``, 0078)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meobot.application.ads.accounts_service import AccountView
from meobot.application.ads.budget_service import BudgetLine, BudgetMonth
from meobot.application.ads.crm_service import AccountView as CrmAccountView
from meobot.application.ads.crm_service import CrmOverview, ImportResult
from meobot.application.ads.dashboard_service import Dashboard, SeriesPoint
from meobot.application.ads.manual_service import ChangeView
from meobot.application.ads.regions import RegionFigures, RegionSplit
from meobot.application.ads.revenue_service import EntryLine
from meobot.application.ads.team_service import Channel, DayDetail, Figures, History, TeamReport
from meobot.db.models.ads import AdsConnection
from meobot.domain.ads.budget import BUDGET_STATUS_LABELS, BudgetWeek
from meobot.domain.ads.crm import HOTLINE_KIND_LABELS, REGION_LABELS, CrmRegion, HotlineKind
from meobot.domain.ads.models import (
    BRAND_LABELS,
    MANUAL_PLATFORMS,
    PLATFORM_LABELS,
    REVENUE_SOURCE_LABELS,
    AdsBrand,
    AdsPlatform,
    RevenueSource,
    ads_ratio_tone,
)


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
    #: ``null`` / "Nhập tay" for a typed-in Google / TikTok account.
    connection_id: uuid.UUID | None
    connection_name: str
    site: str
    platform: str = AdsPlatform.FACEBOOK.value
    platform_label: str = PLATFORM_LABELS[AdsPlatform.FACEBOOK]
    #: The id to show (a typed-in account's as typed).
    external_id: str | None = None
    card_last4: str | None = None
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
        connection = view.connection
        return cls(
            account_id=account.account_id,
            name=account.name,
            connection_id=connection.id if connection else None,
            connection_name=connection.name if connection else "Nhập tay",
            site=connection.site if connection else account.site or "",
            platform=account.platform.value,
            platform_label=PLATFORM_LABELS[account.platform],
            external_id=account.external_id or account.account_id,
            card_last4=account.card_last4,
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
    platform: str = AdsPlatform.FACEBOOK.value
    external_id: str | None = None
    #: The viewer types this account's numbers (Google / TikTok Ads, 0081).
    can_edit: bool = False
    #: Per day: a number was typed / read (an empty cell is not 0).
    spend_entered: list[bool] = []
    invoice_entered: list[bool] = []


class PlatformOption(BaseModel):
    value: str
    label: str
    #: Typed by hand (no API).
    manual: bool


def platform_options() -> list[PlatformOption]:
    return [
        PlatformOption(
            value=item.value, label=PLATFORM_LABELS[item], manual=item in MANUAL_PLATFORMS
        )
        for item in AdsPlatform
    ]


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


class RegionFiguresResponse(BaseModel):
    #: Doanh thu Marketing (by the team) and Cơ sở (by the branch), VAT included.
    marketing: float
    site: float
    marketing_net: float
    #: Cost on the ad accounts of that cơ sở (BM site).
    spend: float
    invoice: float
    #: Hiệu suất: cost (charges, else spend) ÷ Marketing revenue without VAT.
    ads_ratio: float | None
    tone: str | None


class RegionSplitResponse(BaseModel):
    """HN = HN + HN chuyển SG (Marketing) / HN + SG chuyển HN (Cơ sở); SG alike."""

    hn: RegionFiguresResponse
    hcm: RegionFiguresResponse
    other_spend: float
    other_invoice: float
    #: hcm_hcm (SG), hn_hn (HN), hn_hcm (HN chuyển SG), hcm_hn (SG chuyển HN).
    flows: dict[str, float]

    @classmethod
    def from_split(cls, split: RegionSplit) -> RegionSplitResponse:
        def one(figures: RegionFigures) -> RegionFiguresResponse:
            return RegionFiguresResponse(
                marketing=figures.marketing,
                site=figures.site,
                marketing_net=figures.marketing_net,
                spend=figures.spend,
                invoice=figures.invoice,
                ads_ratio=figures.ads_ratio,
                tone=ads_ratio_tone(figures.ads_ratio),
            )

        return cls(
            hn=one(split.hn),
            hcm=one(split.hcm),
            other_spend=split.other_spend,
            other_invoice=split.other_invoice,
            flows=split.flows,
        )


class AdsDashboardResponse(BaseModel):
    start: date
    end: date
    previous_start: date
    previous_end: date
    grouping: str
    #: Revenue by customer flow: hcm_hcm, hn_hn, hn_hcm, hcm_hn (inside "Miền").
    flows: dict[str, float] = {}
    regions: RegionSplitResponse | None = None
    #: The picked BM's region (HN / HCM): the revenue is that region's Marketing.
    bm_region: str | None = None
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
    platform: str = AdsPlatform.FACEBOOK.value
    platforms: list[PlatformOption] = []
    #: May add a Google / TikTok account for the person in view.
    can_add_account: bool = False

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
                    platform=line.platform.value,
                    external_id=line.external_id,
                    can_edit=line.can_edit,
                    spend_entered=line.spend_entered,
                    invoice_entered=line.invoice_entered,
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
            sites=data.sites,
            brands=brand_options(),
            last_synced_at=data.last_synced_at,
            revenue=data.current.revenue,
            revenue_note=data.revenue_note,
            flows=data.flows,
            regions=RegionSplitResponse.from_split(data.regions),
            bm_region=data.bm_region.value if data.bm_region else None,
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
            platform=data.platform.value,
            platforms=platform_options(),
            can_add_account=data.can_add_account,
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
    #: Thực thu from the CRM (0083): read-only.
    from_crm: bool = False

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
            from_crm=line.from_crm,
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


class OtherLineResponse(BaseModel):
    """The ◆ Hotline / a former marketer, a line beside the marketers: no
    cost, no page."""

    key: str
    #: ``hotline`` / ``former``.
    kind: str
    name: str
    figures: FiguresResponse
    #: The hotline's revenue by kind: FB / TIKTOK / GOOGLE / OTHER.
    kinds: dict[str, float] = {}


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


class ChannelPersonResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    figures: FiguresResponse
    ads_ratio: float | None
    tone: str | None


class ChannelResponse(BaseModel):
    platform: str
    label: str
    manual: bool
    figures: FiguresResponse
    people: list[ChannelPersonResponse]
    accounts: int

    @classmethod
    def from_channel(cls, channel: Channel) -> ChannelResponse:
        return cls(
            platform=channel.platform.value,
            label=channel.label,
            manual=channel.platform in MANUAL_PLATFORMS,
            figures=FiguresResponse.from_figures(channel.figures),
            people=[
                ChannelPersonResponse(
                    user_id=person.user_id,
                    name=person.name,
                    figures=FiguresResponse.from_figures(person.figures),
                    ads_ratio=person.ads_ratio,
                    tone=ads_ratio_tone(person.ads_ratio),
                )
                for person in channel.people
            ],
            accounts=channel.accounts,
        )


class TeamReportResponse(BaseModel):
    #: The month the KPI belongs to (the one the range ends in).
    month: str
    start: date
    end: date
    #: The same number of days just before, for "so với kỳ trước".
    previous_start: date | None = None
    previous_end: date | None = None
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
    #: The four customer flows: hcm_hcm (SG), hn_hn (HN), hn_hcm (HN chuyển SG),
    #: hcm_hn (SG chuyển HN); ``revenue`` null outside "Miền".
    flows: list[SourceResponse] = []
    regions: RegionSplitResponse | None = None
    bm_region: str | None = None
    others: list[OtherLineResponse] = []
    marketers: list[MarketerResponse]
    facebook: FiguresResponse
    attention: list[AlertResponse]
    connection_id: uuid.UUID | None = None
    connections: list[ConnectionOption] = []
    #: ``filtered``: a BM is picked, revenue is not split that way;
    #: ``platform``: Google / TikTok, revenue = their ◆ line, no KPI.
    revenue_note: str | None = None
    platform: str = AdsPlatform.FACEBOOK.value
    platforms: list[PlatformOption] = []
    #: Khối 2: every platform's figures, whichever is picked.
    channels: list[ChannelResponse] = []

    @classmethod
    def from_report(cls, report: TeamReport) -> TeamReportResponse:
        return cls(
            month=f"{report.month.end:%Y-%m}",
            start=report.month.start,
            end=report.month.end,
            previous_start=report.previous.start,
            previous_end=report.previous.end,
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
            flows=[
                SourceResponse(
                    key=item.key,
                    label=item.label,
                    revenue=item.revenue,
                    revenue_net=item.revenue_net,
                )
                for item in report.flows
            ],
            regions=RegionSplitResponse.from_split(report.regions),
            bm_region=report.bm_region.value if report.bm_region else None,
            others=[
                OtherLineResponse(
                    key=line.key,
                    kind=line.kind,
                    name=line.name,
                    figures=FiguresResponse.from_figures(line.current),
                    kinds=line.kinds,
                )
                for line in report.others
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
            platform=report.platform.value,
            platforms=platform_options(),
            channels=[ChannelResponse.from_channel(channel) for channel in report.channels],
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
    #: ``filtered``: a BM is picked, revenue is not split that way;
    #: ``platform``: one marketer's Google / TikTok numbers, no revenue.
    revenue_note: str | None = None
    platform: str = AdsPlatform.FACEBOOK.value

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
            platform=history.platform.value,
        )


class DayCustomerResponse(BaseModel):
    #: The CRM's customer code (``KH…``); null on a payment synced before 0086.
    customer_code: str | None
    amount: float
    payments: int


class DayDetailResponse(BaseModel):
    """One day of "Báo cáo ngày": the customers behind its revenue (CRM
    thực thu) + what was typed by hand; together = ``revenue``."""

    day: date
    revenue: float | None
    customers: list[DayCustomerResponse]
    typed: float

    @classmethod
    def from_detail(cls, detail: DayDetail) -> DayDetailResponse:
        return cls(
            day=detail.day,
            revenue=detail.revenue,
            customers=[
                DayCustomerResponse(
                    customer_code=line.customer_code, amount=line.amount, payments=line.payments
                )
                for line in detail.customers
            ],
            typed=detail.typed,
        )


class CreateManualAccountRequest(BaseModel):
    #: ``GOOGLE`` or ``TIKTOK``.
    platform: AdsPlatform
    external_id: str = Field(max_length=60)
    name: str = Field(max_length=300)
    #: A Trưởng phòng / the OWNER / an ADMIN: who runs it (default: oneself).
    user_id: uuid.UUID | None = None
    site: str | None = Field(default=None, max_length=60)
    card_last4: str | None = Field(default=None, max_length=12)


class UpdateManualAccountRequest(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    site: str | None = Field(default=None, max_length=60)
    card_last4: str | None = Field(default=None, max_length=12)


class ManualEntryRequest(BaseModel):
    account_id: str = Field(max_length=40)
    day: date
    field: str = Field(pattern="^(spend|invoice)$")
    #: ``null`` / "" = clear the cell (not entered).
    amount: float | str | None = None


class SaveManualRequest(BaseModel):
    entries: list[ManualEntryRequest] = Field(max_length=2000)


class SaveManualResponse(BaseModel):
    changed: int


class ManualChangeResponse(BaseModel):
    id: uuid.UUID
    day: date
    account_id: str
    account_name: str
    external_id: str | None
    platform: str
    #: ``spend`` / ``invoice``.
    field: str
    old_amount: float | None
    new_amount: float | None
    changed_by: str | None
    changed_at: datetime

    @classmethod
    def from_view(cls, view: ChangeView) -> ManualChangeResponse:
        change = view.change
        return cls(
            id=change.id,
            day=change.day,
            account_id=change.account_id,
            account_name=view.account.name,
            external_id=view.account.external_id,
            platform=view.account.platform.value,
            field=change.field,
            old_amount=float(change.old_amount) if change.old_amount is not None else None,
            new_amount=float(change.new_amount) if change.new_amount is not None else None,
            changed_by=view.changed_by,
            changed_at=change.changed_at,
        )


class ManualChangesResponse(BaseModel):
    start: date
    end: date
    changes: list[ManualChangeResponse]


class BudgetWeekResponse(BaseModel):
    index: int
    start: date
    end: date
    label: str

    @classmethod
    def from_week(cls, week: BudgetWeek) -> BudgetWeekResponse:
        return cls(index=week.index, start=week.start, end=week.end, label=week.label)


class BudgetCellResponse(BaseModel):
    week: int
    budget: float | None
    spend: float
    #: spend ÷ budget; ``None`` without a budget.
    rate: float | None
    #: OVER / REACHED / UNDER / RUNNING / UPCOMING / NO_BUDGET.
    status: str
    status_label: str


class BudgetLineResponse(BaseModel):
    user_id: uuid.UUID | None
    name: str
    member_code: str | None
    cells: list[BudgetCellResponse]
    budget: float | None
    spend: float

    @classmethod
    def from_line(cls, line: BudgetLine) -> BudgetLineResponse:
        return cls(
            user_id=line.user_id,
            name=line.name,
            member_code=line.member_code,
            cells=[
                BudgetCellResponse(
                    week=cell.week.index,
                    budget=cell.budget,
                    spend=cell.spend,
                    rate=cell.rate,
                    status=cell.status.value,
                    status_label=BUDGET_STATUS_LABELS[cell.status],
                )
                for cell in line.cells
            ],
            budget=line.budget,
            spend=line.spend,
        )


class BudgetMonthResponse(BaseModel):
    month: str
    today: date
    weeks: list[BudgetWeekResponse]
    lines: list[BudgetLineResponse]
    total: BudgetLineResponse | None
    can_edit: bool

    @classmethod
    def from_month(cls, data: BudgetMonth, *, can_edit: bool) -> BudgetMonthResponse:
        return cls(
            month=f"{data.month:%Y-%m}",
            today=data.today,
            weeks=[BudgetWeekResponse.from_week(week) for week in data.weeks],
            lines=[BudgetLineResponse.from_line(line) for line in data.lines],
            total=BudgetLineResponse.from_line(data.total) if data.total else None,
            can_edit=can_edit,
        )


class SaveBudgetRequest(BaseModel):
    #: ``YYYY-MM``.
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    #: ``"<user id>:<week 1-4>" -> amount`` (``null`` or "" = clear the cell).
    entries: dict[str, float | str | None] = Field(max_length=2000)


class CrmPersonResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    member_code: str | None
    in_ads: bool


class CrmAccountResponse(BaseModel):
    crm_id: int
    name: str
    active: bool
    #: Listed by the CRM's marketing users (else only seen on a payment).
    in_directory: bool
    region: str | None
    region_label: str | None
    user_id: uuid.UUID | None
    user_name: str | None
    member_code: str | None
    source: str | None
    source_label: str | None
    #: A ◆ Hotline account's hotline (FB / TIKTOK / GOOGLE).
    hotline_kind: str | None = None
    hotline_kind_label: str | None = None
    #: A marketer who left (no app account): a line under the CRM name.
    former: bool = False
    note: str | None
    #: The month's counted payments and thực thu.
    payments: int
    total: float

    @classmethod
    def from_view(cls, view: CrmAccountView) -> CrmAccountResponse:
        account = view.account
        return cls(
            crm_id=account.crm_id,
            name=account.name,
            active=account.active,
            in_directory=account.in_directory,
            region=account.region.value if account.region else None,
            region_label=REGION_LABELS[account.region] if account.region else None,
            user_id=account.user_id,
            user_name=view.person.name if view.person else None,
            member_code=view.person.member_code if view.person else None,
            source=account.source.value if account.source else None,
            source_label=REVENUE_SOURCE_LABELS[account.source] if account.source else None,
            hotline_kind=account.hotline_kind.value if account.hotline_kind else None,
            hotline_kind_label=HOTLINE_KIND_LABELS[account.hotline_kind]
            if account.hotline_kind
            else None,
            former=account.former,
            note=account.note,
            payments=view.payments,
            total=float(view.total),
        )


class CrmOptionResponse(BaseModel):
    value: str
    label: str


class CrmOverviewResponse(BaseModel):
    month: str
    #: ``CRM_API_KEY`` is set on the server.
    configured: bool
    synced_from: date | None
    synced_to: date | None
    last_sync_at: datetime | None
    last_status: str | None
    last_message: str | None
    accounts: list[CrmAccountResponse]
    people: list[CrmPersonResponse]
    sources: list[CrmOptionResponse]
    regions: list[CrmOptionResponse]
    hotline_kinds: list[CrmOptionResponse] = []
    mapped_total: float
    unassigned_total: float
    unassigned_accounts: int

    @classmethod
    def from_overview(cls, data: CrmOverview) -> CrmOverviewResponse:
        coverage = data.coverage
        return cls(
            month=f"{data.month:%Y-%m}",
            configured=data.configured,
            synced_from=coverage.synced_from,
            synced_to=coverage.synced_to,
            last_sync_at=coverage.last_sync_at,
            last_status=coverage.last_status,
            last_message=coverage.last_message,
            accounts=[CrmAccountResponse.from_view(view) for view in data.accounts],
            people=[
                CrmPersonResponse(
                    user_id=p.user_id, name=p.name, member_code=p.member_code, in_ads=p.in_ads
                )
                for p in data.people
            ],
            sources=[
                CrmOptionResponse(value=source.value, label=label)
                for source, label in REVENUE_SOURCE_LABELS.items()
            ],
            regions=[
                CrmOptionResponse(value=region.value, label=label)
                for region, label in REGION_LABELS.items()
            ],
            hotline_kinds=[
                CrmOptionResponse(value=kind.value, label=label)
                for kind, label in HOTLINE_KIND_LABELS.items()
            ],
            mapped_total=float(data.mapped_total),
            unassigned_total=float(data.unassigned_total),
            unassigned_accounts=data.unassigned_accounts,
        )


class UpdateCrmAccountRequest(BaseModel):
    """Only the keys sent change; ``user_id`` and ``source`` exclude each other."""

    user_id: uuid.UUID | None = None
    source: RevenueSource | None = None
    region: CrmRegion | None = None
    hotline_kind: HotlineKind | None = None
    #: A marketer who left: their revenue is a line under the CRM name.
    former: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class CrmImportRequest(BaseModel):
    #: The "Gắn mã" workbook (.xlsx), base64.
    data: str = Field(min_length=1, max_length=8_000_000)


class CrmImportResponse(BaseModel):
    rows: int
    changed: int
    created: int

    @classmethod
    def from_result(cls, result: ImportResult) -> CrmImportResponse:
        return cls(rows=result.rows, changed=result.changed, created=result.created)


class CrmSyncRequest(BaseModel):
    #: Both or neither: neither = what the hourly run reads now.
    since: date | None = None
    until: date | None = None


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
    "DayCustomerResponse",
    "DayDetailResponse",
    "HistoryResponse",
    "UpdateAdsConnectionRequest",
    "brand_options",
]
