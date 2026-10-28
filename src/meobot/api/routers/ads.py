"""Phòng Ads (0078): "Kết nối BM", "Tài khoản QC" and the staff dashboard.

Who: only the ADS department (a HEAD sees everybody and assigns accounts, a
member their own numbers), the OWNER / an ADMIN everything; anybody else 404
``ads_not_visible``. "Kết nối BM" (tokens, tests, syncs): the OWNER
(403 ``ads_connection_forbidden``).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, SettingsDep, get_session
from meobot.api.schemas.ads import (
    AdsAccountResponse,
    AdsAccountsResponse,
    AdsConnectionResponse,
    AdsConnectionsResponse,
    AdsConnectionTestResponse,
    AdsDashboardResponse,
    AdsSyncRequest,
    AdsSyncResponse,
    AssignAdsAccountRequest,
    CreateAdsConnectionRequest,
    CreateManualAccountRequest,
    EntryLineResponse,
    HistoryResponse,
    KpiMonthResponse,
    ManualChangeResponse,
    ManualChangesResponse,
    PersonOption,
    RevenueDayResponse,
    SaveKpiRequest,
    SaveManualRequest,
    SaveManualResponse,
    SaveRevenueRequest,
    TeamReportResponse,
    UpdateAdsConnectionRequest,
    UpdateManualAccountRequest,
    brand_options,
)
from meobot.application.ads.access import AdsAccess, ads_access
from meobot.application.ads.accounts_service import AdsAccountService, ads_people, month_of
from meobot.application.ads.connection_service import (
    AdsConnectionForbiddenError,
    AdsConnectionService,
)
from meobot.application.ads.dashboard_service import AdsDashboardService
from meobot.application.ads.manual_service import AdsManualService, ManualEntry
from meobot.application.ads.revenue_service import AdsRevenueService, EntryLine
from meobot.application.ads.sync_service import vn_today
from meobot.application.ads.team_service import AdsTeamService
from meobot.application.audit_service import AuditService
from meobot.application.cron.monitor_service import (
    HEADER_ATTEMPT,
    HEADER_TRIGGER,
    HEADER_USER,
    CronJobRunningError,
    Sender,
    celery_sender,
)
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.errors import ValidationError
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cron import CronJob
from meobot.domain.ads.models import MAX_SYNC_DAYS, AdsBrand, AdsGrouping, AdsPlatform, Period
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.cron.models import RUNNING_STALE_SECONDS, CronTrigger
from meobot.domain.identity.models import Actor
from meobot.integrations.meta_ads.client import GraphMetaAdsClient, MetaAdsClient

router = APIRouter(prefix="/api/ads", tags=["ads"])

#: The dashboard's default: the last 14 days up to yesterday.
DEFAULT_DAYS = 14
MAX_PERIOD_DAYS = 366


class AdsPeriodInvalidError(ValidationError):
    code = "ads_period_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message, details={"reason": "ads_period_invalid"})


def get_meta_ads_client() -> MetaAdsClient:
    return GraphMetaAdsClient()


def get_task_sender() -> Sender:
    return celery_sender


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[MetaAdsClient, Depends(get_meta_ads_client)]
SenderDep = Annotated[Sender, Depends(get_task_sender)]


async def _access(session: AsyncSession, actor: Actor) -> AdsAccess:
    membership = await UnitDirectoryService(session).membership_for(actor)
    return ads_access(membership)


def _connections(
    session: AsyncSession, settings: SettingsDep, client: MetaAdsClient
) -> AdsConnectionService:
    return AdsConnectionService(session, AuditService(session), settings, client=client)


# --- "Kết nối BM" ---------------------------------------------------------------


@router.get("/connections", response_model=AdsConnectionsResponse)
async def connections(
    actor: CurrentActorDep, session: SessionDep, settings: SettingsDep, client: ClientDep
) -> AdsConnectionsResponse:
    service = _connections(session, settings, client)
    rows = await service.connections(actor=actor)
    return AdsConnectionsResponse(
        connections=[AdsConnectionResponse.from_row(row) for row in rows],
        secret_ready=service.secret_ready(),
        sites=sorted({row.site for row in rows if row.site}),
    )


@router.post("/connections", response_model=AdsConnectionResponse, status_code=201)
async def create_connection(
    body: CreateAdsConnectionRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
    settings: SettingsDep,
    client: ClientDep,
) -> AdsConnectionResponse:
    row = await _connections(session, settings, client).create(
        actor=actor,
        request_id=request_id,
        name=body.name,
        business_id=body.business_id,
        site=body.site,
        token=body.token,
        note=body.note,
    )
    return AdsConnectionResponse.from_row(row)


@router.patch("/connections/{connection_id}", response_model=AdsConnectionResponse)
async def update_connection(
    connection_id: uuid.UUID,
    body: UpdateAdsConnectionRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
    settings: SettingsDep,
    client: ClientDep,
) -> AdsConnectionResponse:
    """A blank ``token`` keeps the stored one."""
    row = await _connections(session, settings, client).update(
        actor=actor,
        request_id=request_id,
        connection_id=connection_id,
        name=body.name,
        business_id=body.business_id,
        site=body.site,
        token=body.token,
        note=body.note,
        active=body.active,
    )
    return AdsConnectionResponse.from_row(row)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
    settings: SettingsDep,
    client: ClientDep,
) -> Response:
    """409 ``ads_connection_in_use`` once it has accounts: switch it off instead."""
    await _connections(session, settings, client).delete(
        actor=actor, request_id=request_id, connection_id=connection_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/connections/{connection_id}/test", response_model=AdsConnectionTestResponse)
async def test_connection(
    connection_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
    settings: SettingsDep,
    client: ClientDep,
) -> AdsConnectionTestResponse:
    """Asks Meta for the BM and its ad accounts. A failure is an answer
    (``ok: false`` + why in Vietnamese), not an error."""
    service = _connections(session, settings, client)
    outcome = await service.test(actor=actor, request_id=request_id, connection_id=connection_id)
    return AdsConnectionTestResponse(
        ok=outcome.ok,
        message=outcome.message,
        latency_ms=outcome.latency_ms,
        connection=AdsConnectionResponse.from_row(await service.connection(connection_id)),
    )


@router.post("/sync", response_model=AdsSyncResponse, status_code=202)
async def sync_now(
    body: AdsSyncRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
    sender: SenderDep,
) -> AdsSyncResponse:
    """ "Đồng bộ ngay" (no dates) / "Kéo lại khoảng ngày" (≤ 93 days). Queued
    like the schedule's run and recorded by the job monitor as a manual run;
    409 ``cron_job_running`` while one is going."""
    access = await _access(session, actor)
    if not access.manage_connections:
        raise AdsConnectionForbiddenError()
    if (body.since is None) != (body.until is None):
        raise AdsPeriodInvalidError("Chọn cả ngày bắt đầu và ngày kết thúc.")
    if body.since and body.until:
        if body.until < body.since:
            raise AdsPeriodInvalidError("Ngày kết thúc phải sau ngày bắt đầu.")
        if (body.until - body.since).days + 1 > MAX_SYNC_DAYS:
            raise AdsPeriodInvalidError(f"Mỗi lần kéo tối đa {MAX_SYNC_DAYS} ngày.")
    key = "ads.sync_spend" if body.kind == "spend" else "ads.sync_charges"
    job = await session.get(CronJob, key)
    running = ensure_utc(job.running_since) if job and job.running_since else None
    if running and utcnow() - running < timedelta(seconds=RUNNING_STALE_SECONDS):
        raise CronJobRunningError()
    kwargs: dict[str, str] = {}
    if body.since and body.until:
        kwargs = {"since": body.since.isoformat(), "until": body.until.isoformat()}
    if body.connection_id:
        kwargs["connection_id"] = str(body.connection_id)
    task_id = sender(
        key,
        (),
        kwargs,
        {
            HEADER_TRIGGER: CronTrigger.MANUAL.value,
            HEADER_ATTEMPT: 1,
            HEADER_USER: str(actor.user_id) if actor.user_id else "",
        },
        {"queue": "q_integrations"},
    )
    await AuditService(session).record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.ADS_SYNC_REQUESTED.value,
        result=AuditResult.SUCCESS,
        entity_type="ads_sync",
        entity_id=key,
        after_data={"task_id": task_id, **kwargs},
    )
    return AdsSyncResponse(task_id=task_id, job_key=key)


# --- "Tài khoản QC" ---------------------------------------------------------------


def _month(value: str | None) -> Period:
    if value:
        try:
            year, month = (int(part) for part in value.split("-"))
            return month_of(date(year, month, 1))
        except ValueError as error:
            raise AdsPeriodInvalidError("Tháng không hợp lệ (YYYY-MM).") from error
    return month_of(vn_today())


@router.get("/accounts", response_model=AdsAccountsResponse)
async def accounts(
    actor: CurrentActorDep,
    session: SessionDep,
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
    unassigned: Annotated[bool, Query()] = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
    month: Annotated[str | None, Query(max_length=7)] = None,
    platform: Annotated[AdsPlatform | None, Query()] = None,
) -> AdsAccountsResponse:
    access = await _access(session, actor)
    period = _month(month)
    views = await AdsAccountService(session, AuditService(session)).accounts(
        access,
        period=period,
        connection_id=connection_id,
        unassigned=unassigned,
        query=q,
        platform=platform,
    )
    people = await ads_people(session) if access.manage_accounts else []
    return AdsAccountsResponse(
        month=f"{period.start:%Y-%m}",
        accounts=[AdsAccountResponse.from_view(view) for view in views],
        people=[
            PersonOption(user_id=user.id, name=user.full_name, member_code=user.member_code)
            for user in people
        ],
        brands=brand_options(),
        can_manage=access.manage_accounts,
    )


@router.patch("/accounts/{account_id}", response_model=AdsAccountResponse)
async def assign_account(
    account_id: str,
    body: AssignAdsAccountRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> AdsAccountResponse:
    """``user_id: null`` = "Chưa gán"; only people seated in ADS (422
    ``ads_assignee_invalid``). 403 ``ads_account_forbidden`` for a member."""
    access = await _access(session, actor)
    service = AdsAccountService(session, AuditService(session))
    await service.assign(
        actor=actor,
        access=access,
        request_id=request_id,
        account_id=account_id,
        user_id=body.user_id,
        clear_user="user_id" in body.model_fields_set and body.user_id is None,
        brand=body.brand,
    )
    views = await service.accounts(access, period=month_of(vn_today()), query=account_id)
    view = next(item for item in views if item.account.account_id == account_id)
    return AdsAccountResponse.from_view(view)


# --- the dashboard ----------------------------------------------------------------


@router.get("/dashboard", response_model=AdsDashboardResponse)
async def dashboard(
    actor: CurrentActorDep,
    session: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    group: Annotated[AdsGrouping, Query()] = AdsGrouping.DAY,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    brand: Annotated[AdsBrand | None, Query()] = None,
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
    site: Annotated[str | None, Query(max_length=60)] = None,
    platform: Annotated[AdsPlatform, Query()] = AdsPlatform.FACEBOOK,
) -> AdsDashboardResponse:
    """One person's numbers (a Trưởng phòng / the OWNER: ``user_id`` or the
    whole department; a member: always their own)."""
    access = await _access(session, actor)
    yesterday = vn_today() - timedelta(days=1)
    period_end = end or yesterday
    period_start = start or period_end - timedelta(days=DEFAULT_DAYS - 1)
    if period_end < period_start:
        raise AdsPeriodInvalidError("Ngày kết thúc phải sau ngày bắt đầu.")
    if (period_end - period_start).days + 1 > MAX_PERIOD_DAYS:
        raise AdsPeriodInvalidError("Khoảng ngày tối đa 1 năm.")
    data = await AdsDashboardService(session).staff(
        access,
        period=Period(start=period_start, end=period_end),
        grouping=group,
        user_id=user_id,
        brand=brand,
        connection_id=connection_id,
        site=site,
        platform=platform,
    )
    return AdsDashboardResponse.from_domain(data)


# --- Google / TikTok Ads typed by hand (0081) -------------------------------------


def _manual(session: AsyncSession) -> AdsManualService:
    return AdsManualService(session, AuditService(session))


async def _account_response(
    session: AsyncSession, access: AdsAccess, account_id: str
) -> AdsAccountResponse:
    views = await AdsAccountService(session, AuditService(session)).accounts(
        access, period=month_of(vn_today()), query=account_id
    )
    view = next(item for item in views if item.account.account_id == account_id)
    return AdsAccountResponse.from_view(view)


@router.post("/manual/accounts", response_model=AdsAccountResponse, status_code=201)
async def create_manual_account(
    body: CreateManualAccountRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> AdsAccountResponse:
    """A marketer adds their Google / TikTok Ads account (a Trưởng phòng / the
    OWNER / an ADMIN: anybody's in ADS, ``user_id``). 422 ``ads_account_exists``,
    ``ads_manual_invalid``, ``ads_assignee_invalid``."""
    access = await _access(session, actor)
    account = await _manual(session).create_account(
        actor=actor,
        access=access,
        request_id=request_id,
        platform=body.platform,
        external_id=body.external_id,
        name=body.name,
        user_id=body.user_id,
        site=body.site,
        card_last4=body.card_last4,
    )
    return await _account_response(session, access, account.account_id)


@router.patch("/manual/accounts/{account_id}", response_model=AdsAccountResponse)
async def update_manual_account(
    account_id: str,
    body: UpdateManualAccountRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> AdsAccountResponse:
    access = await _access(session, actor)
    await _manual(session).update_account(
        actor=actor,
        access=access,
        request_id=request_id,
        account_id=account_id,
        name=body.name,
        site=body.site,
        card_last4=body.card_last4,
        fields=body.model_fields_set,
    )
    return await _account_response(session, access, account_id)


@router.delete("/manual/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_manual_account(
    account_id: str,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> Response:
    """Only one with no number typed, ever (422 ``ads_account_has_numbers``)."""
    access = await _access(session, actor)
    await _manual(session).delete_account(
        actor=actor, access=access, request_id=request_id, account_id=account_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/manual/days", response_model=SaveManualResponse)
async def save_manual_days(
    body: SaveManualRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> SaveManualResponse:
    """Each day's spend / "Số tiền lập hoá đơn" of typed-in accounts (a blank
    clears a cell); every change is kept. 403 ``ads_manual_forbidden`` on
    somebody else's account, 422 ``ads_manual_invalid``."""
    access = await _access(session, actor)
    changed = await _manual(session).save(
        actor=actor,
        access=access,
        request_id=request_id,
        entries=[
            ManualEntry(entry.account_id, entry.day, entry.field, entry.amount)
            for entry in body.entries
        ],
    )
    return SaveManualResponse(changed=changed)


@router.get("/manual/changes", response_model=ManualChangesResponse)
async def manual_changes(
    actor: CurrentActorDep,
    session: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    account_id: Annotated[str | None, Query(max_length=40)] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    platform: Annotated[AdsPlatform | None, Query()] = None,
) -> ManualChangesResponse:
    """ "Lịch sử sửa": the newest first (default the last 62 days), the
    accounts the viewer sees (a member: their own)."""
    access = await _access(session, actor)
    last = end or vn_today()
    first = start or last - timedelta(days=61)
    if last < first:
        raise AdsPeriodInvalidError("Ngày kết thúc phải sau ngày bắt đầu.")
    views = await _manual(session).changes(
        access, start=first, end=last, account_id=account_id, user_id=user_id, platform=platform
    )
    return ManualChangesResponse(
        start=first, end=last, changes=[ManualChangeResponse.from_view(view) for view in views]
    )


# --- revenue, KPI, Quản lý A (0079) --------------------------------------------------


def _revenue(session: AsyncSession) -> AdsRevenueService:
    return AdsRevenueService(session, AuditService(session))


def _total(lines: list[EntryLine]) -> float | None:
    amounts = [line.amount for line in lines if line.amount is not None]
    return sum(amounts) if amounts else None


@router.get("/revenue", response_model=RevenueDayResponse)
async def revenue_day(
    actor: CurrentActorDep,
    session: SessionDep,
    day: Annotated[date | None, Query()] = None,
) -> RevenueDayResponse:
    """One day's lines (default yesterday): every ADS person + the ◆ sources.
    A Trưởng phòng ADS / the OWNER / an ADMIN; else 403 ``ads_revenue_forbidden``."""
    access = await _access(session, actor)
    chosen = day or vn_today() - timedelta(days=1)
    lines = await _revenue(session).day(access, chosen)
    return RevenueDayResponse(
        day=chosen, lines=[EntryLineResponse.from_line(line) for line in lines], total=_total(lines)
    )


@router.put("/revenue", response_model=RevenueDayResponse)
async def save_revenue(
    body: SaveRevenueRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> RevenueDayResponse:
    """Saves the lines sent (a blank clears one); every change is kept."""
    access = await _access(session, actor)
    lines = await _revenue(session).save(
        actor=actor, access=access, request_id=request_id, day=body.day, entries=body.entries
    )
    return RevenueDayResponse(
        day=body.day,
        lines=[EntryLineResponse.from_line(line) for line in lines],
        total=_total(lines),
    )


@router.get("/kpi", response_model=KpiMonthResponse)
async def kpi_month(
    actor: CurrentActorDep,
    session: SessionDep,
    month: Annotated[str | None, Query(max_length=7)] = None,
) -> KpiMonthResponse:
    access = await _access(session, actor)
    period = _month(month)
    lines = await _revenue(session).kpis(access, period.start)
    return KpiMonthResponse(
        month=f"{period.start:%Y-%m}",
        lines=[EntryLineResponse.from_line(line) for line in lines],
        can_edit=access.edit_revenue,
    )


@router.put("/kpi", response_model=KpiMonthResponse)
async def save_kpi(
    body: SaveKpiRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    session: SessionDep,
) -> KpiMonthResponse:
    access = await _access(session, actor)
    period = _month(body.month)
    lines = await _revenue(session).save_kpis(
        actor=actor, access=access, request_id=request_id, month=period.start, entries=body.entries
    )
    return KpiMonthResponse(
        month=f"{period.start:%Y-%m}",
        lines=[EntryLineResponse.from_line(line) for line in lines],
        can_edit=access.edit_revenue,
    )


@router.get("/team", response_model=TeamReportResponse)
async def team_report(
    actor: CurrentActorDep,
    session: SessionDep,
    month: Annotated[str | None, Query(max_length=7)] = None,
    site: Annotated[str, Query(pattern="^(all|hcm|hn)$")] = "all",
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    platform: Annotated[AdsPlatform, Query()] = AdsPlatform.FACEBOOK,
) -> TeamReportResponse:
    """ "Tổng team" for one month, or ``start``-``end`` (both; compared with
    the same number of days before) - a Trưởng phòng ADS, the OWNER / an
    ADMIN; a member 404 ``ads_not_visible``. ``site`` narrows the revenue:
    ``hcm`` without "HN chuyển HCM", ``hn`` that alone; ``connection_id`` one
    BM's accounts (then no revenue)."""
    access = await _access(session, actor)
    if (start is None) != (end is None):
        raise AdsPeriodInvalidError("Chọn cả ngày bắt đầu và ngày kết thúc.")
    if start is not None and end is not None:
        if end < start:
            raise AdsPeriodInvalidError("Ngày kết thúc phải sau ngày bắt đầu.")
        if (end - start).days + 1 > MAX_PERIOD_DAYS:
            raise AdsPeriodInvalidError("Khoảng ngày tối đa 1 năm.")
    report = await AdsTeamService(session).report(
        access,
        month=_month(month).start,
        site=site,
        connection_id=connection_id,
        start=start,
        end=end,
        platform=platform,
    )
    return TeamReportResponse.from_report(report)


@router.get("/history", response_model=HistoryResponse)
async def history(
    actor: CurrentActorDep,
    session: SessionDep,
    month: Annotated[str | None, Query(max_length=7)] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
    site: Annotated[str, Query(pattern="^(all|hcm|hn)$")] = "all",
    platform: Annotated[AdsPlatform, Query()] = AdsPlatform.FACEBOOK,
) -> HistoryResponse:
    """ "Báo cáo tháng" (April to ``month``) and "Báo cáo ngày" (its days): the
    team or one marketer (a Trưởng phòng / the OWNER / an ADMIN); a member
    always gets their own."""
    access = await _access(session, actor)
    data = await AdsTeamService(session).history(
        access,
        month=_month(month).start,
        user_id=user_id,
        connection_id=connection_id,
        site=site,
        platform=platform,
    )
    return HistoryResponse.from_history(data)


__all__ = ["router"]
