"""Tài chính (0105): the revenue read from the CRM and its settings.

``/api/finance``: ``/access``; ``/revenue?month=`` (REVENUE); ``/branches`` +
``PATCH /branches/{id}``, ``/staff`` + ``PATCH /staff/{id}``, ``/people``,
``/vat`` (read: REVENUE or SETTINGS; write: SETTINGS); ``/sync`` (status) and
``POST /sync`` = "Đồng bộ ngay" - the same one CRM run as Phòng Ads' (job
``ads.sync_crm_revenue``), 409 ``cron_job_running`` while one is going.
Else 403 ``finance_forbidden``.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SettingsDep,
    UnitMembershipDep,
    get_session,
)
from meobot.api.schemas.finance import (
    BranchesResponse,
    BranchResponse,
    BranchUpdateRequest,
    DaySummaryResponse,
    DebtBucketResponse,
    DebtLineResponse,
    DebtReportResponse,
    DebtSettingsRequest,
    DebtSettingsResponse,
    FeeRulesRequest,
    FeeRulesResponse,
    FeesResponse,
    FeeUpdateRequest,
    FinanceAccessResponse,
    KpiRequest,
    KpiResponse,
    LedgerResponse,
    LedgerRowResponse,
    MonthRevenueResponse,
    PersonAmount,
    PersonChoiceResponse,
    ServiceCostRequest,
    ServiceCostResponse,
    StaffPageResponse,
    StaffResponse,
    StaffUpdateRequest,
    SyncQueuedResponse,
    SyncRequest,
    SyncStatusResponse,
    VatRequest,
    VatResponse,
)
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.application.crm.sync import SETTINGS_KEY as SYNC_KEY
from meobot.application.cron.monitor_service import (
    HEADER_ATTEMPT,
    HEADER_TRIGGER,
    HEADER_USER,
    CronJobRunningError,
    Sender,
    celery_sender,
)
from meobot.application.finance.access import FinanceAccess, load_finance_access
from meobot.application.finance.debts import FinanceDebtService
from meobot.application.finance.kpi import FinanceKpiService, kpi_keys
from meobot.application.finance.ledger import (
    METHOD_ORDER,
    DaySummary,
    Fees,
    FinanceLedgerService,
    LedgerRow,
    check_span,
    ledger_workbook,
)
from meobot.application.finance.revenue import TEAM_LABELS, FinanceRevenueService, month_span
from meobot.application.finance.settings import STAFF_PAGE, FinanceSettingsService
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cron import CronJob
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.ads.models import MAX_SYNC_DAYS
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.cron.models import RUNNING_STALE_SECONDS, CronTrigger
from meobot.domain.finance.crm import FEE_METHODS, PAYMENT_TYPE_LABELS, SHARE_TYPE_LABELS, whole
from meobot.domain.finance.errors import FinanceForbiddenError, FinanceInvalidError
from meobot.domain.org.models import BRANCHES

router = APIRouter(prefix="/api/finance", tags=["finance"])

#: The job both Phòng Ads and Tài chính read from (one run, user 13/11).
SYNC_JOB = "ads.sync_crm_revenue"


def get_task_sender() -> Sender:
    return celery_sender


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SenderDep = Annotated[Sender, Depends(get_task_sender)]


async def _access(session: SessionDep, membership: UnitMembershipDep) -> FinanceAccess:
    access = await load_finance_access(session, membership)
    if not access.sees:
        raise FinanceForbiddenError()
    return access


AccessDep = Annotated[FinanceAccess, Depends(_access)]


def _month(value: str | None) -> date:
    if not value:
        return vn_today().replace(day=1)
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as error:
        raise FinanceInvalidError("month_invalid", "Tháng dạng YYYY-MM.", field="month") from error


@router.get("/access", response_model=FinanceAccessResponse)
async def access_flags(session: SessionDep, membership: UnitMembershipDep) -> FinanceAccessResponse:
    """What the caller may do here (never 403: the nav reads it)."""
    return FinanceAccessResponse.from_access(await load_finance_access(session, membership))


@router.get("/revenue", response_model=MonthRevenueResponse)
async def revenue(
    session: SessionDep, access: AccessDep, month: str | None = Query(default=None)
) -> MonthRevenueResponse:
    access.require_revenue()
    view = await FinanceRevenueService(session).month(_month(month), today=vn_today())
    return MonthRevenueResponse.from_domain(view)


@router.get("/branches", response_model=BranchesResponse)
async def branches(session: SessionDep, access: AccessDep) -> BranchesResponse:
    service = FinanceSettingsService(session, AuditService(session))
    return BranchesResponse.build(
        await service.branches(), await service.entities(), access.settings
    )


@router.patch("/branches/{branch_id}", response_model=BranchResponse)
async def update_branch(
    branch_id: int,
    body: BranchUpdateRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> BranchResponse:
    row = await FinanceSettingsService(session, AuditService(session)).update_branch(
        actor=actor,
        access=access,
        request_id=request_id,
        branch_id=branch_id,
        changes={key: getattr(body, key) for key in body.model_fields_set},
    )
    return BranchResponse.from_row(row)


@router.get("/staff", response_model=StaffPageResponse)
async def staff(
    session: SessionDep,
    access: AccessDep,
    q: str = Query(default="", max_length=100),
    unmapped: bool = Query(default=False),
    page: int = Query(default=1, ge=1, le=1000),
) -> StaffPageResponse:
    found = await FinanceSettingsService(session, AuditService(session)).staff(
        q=q, unmapped=unmapped, page=page
    )
    return StaffPageResponse.build(found, STAFF_PAGE, access.settings)


@router.patch("/staff/{crm_id}", response_model=StaffResponse)
async def update_staff(
    crm_id: int,
    body: StaffUpdateRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> StaffResponse:
    service = FinanceSettingsService(session, AuditService(session))
    row = await service.update_staff(
        actor=actor, access=access, request_id=request_id, crm_id=crm_id, user_id=body.user_id
    )
    people = {person.id: person.full_name for person in await service.people()}
    return StaffResponse(
        id=row.id,
        name=row.name,
        roles=list(row.roles or []),
        department_name=row.department_name,
        user_id=row.user_id,
        user_name=people.get(row.user_id) if row.user_id else None,
    )


@router.get("/people", response_model=list[PersonChoiceResponse])
async def people(session: SessionDep, access: AccessDep) -> list[PersonChoiceResponse]:
    access.require_settings()
    service = FinanceSettingsService(session, AuditService(session))
    return [
        PersonChoiceResponse(id=user.id, name=user.full_name or "", member_code=user.member_code)
        for user in await service.people()
    ]


@router.get("/vat", response_model=VatResponse)
async def vat(session: SessionDep, access: AccessDep) -> VatResponse:
    default, by_entity = await FinanceSettingsService(session, AuditService(session)).vat()
    return VatResponse(
        vat_default=str(default),
        vat_by_entity={key: str(rate) for key, rate in by_entity.items()},
        can_edit=access.settings,
    )


@router.put("/vat", response_model=VatResponse)
async def set_vat(
    body: VatRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> VatResponse:
    default, by_entity = await FinanceSettingsService(session, AuditService(session)).set_vat(
        actor=actor,
        access=access,
        request_id=request_id,
        default=body.vat_default,
        by_entity=dict(body.vat_by_entity),
    )
    return VatResponse(
        vat_default=str(default),
        vat_by_entity={key: str(rate) for key, rate in by_entity.items()},
        can_edit=True,
    )


async def _running(session: AsyncSession) -> bool:
    job = await session.get(CronJob, SYNC_JOB)
    started = ensure_utc(job.running_since) if job and job.running_since else None
    return bool(started and utcnow() - started < timedelta(seconds=RUNNING_STALE_SECONDS))


@router.get("/sync", response_model=SyncStatusResponse)
async def sync_status(
    session: SessionDep, access: AccessDep, app_settings: SettingsDep
) -> SyncStatusResponse:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == SYNC_KEY))
    value = dict(row.value) if row is not None else {}
    key = app_settings.crm_api_key
    return SyncStatusResponse(
        configured=key is not None and bool(key.get_secret_value().strip()),
        at=value.get("at"),
        status=value.get("status"),
        message=value.get("message"),
        synced_from=value.get("synced_from"),
        synced_to=value.get("synced_to"),
        running=await _running(session),
        can_sync=access.settings,
    )


@router.post("/sync", response_model=SyncQueuedResponse, status_code=202)
async def sync_now(
    body: SyncRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    sender: SenderDep,
) -> SyncQueuedResponse:
    """ "Đồng bộ ngay" (no dates) or a span (≤ 93 days, not in the future)."""
    access.require_settings()
    if (body.since is None) != (body.until is None):
        raise FinanceInvalidError("period_invalid", "Chọn cả ngày bắt đầu và ngày kết thúc.")
    kwargs: dict[str, str] = {}
    if body.since and body.until:
        if body.until < body.since:
            raise FinanceInvalidError("period_invalid", "Ngày kết thúc phải sau ngày bắt đầu.")
        if (body.until - body.since).days + 1 > MAX_SYNC_DAYS:
            raise FinanceInvalidError("period_invalid", f"Mỗi lần kéo tối đa {MAX_SYNC_DAYS} ngày.")
        if body.until > vn_today():
            raise FinanceInvalidError("period_invalid", "Không kéo ngày trong tương lai.")
        kwargs = {"since": body.since.isoformat(), "until": body.until.isoformat()}
    if await _running(session):
        raise CronJobRunningError()
    task_id = sender(
        SYNC_JOB,
        (),
        kwargs,
        {
            HEADER_TRIGGER: CronTrigger.MANUAL.value,
            HEADER_ATTEMPT: 1,
            HEADER_USER: str(actor.user_id) if actor.user_id else "",
        },
        {"queue": "q_sync"},
    )
    await AuditService(session).record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.FINANCE_SYNC_REQUESTED.value,
        result=AuditResult.SUCCESS,
        entity_type="crm_sync",
        entity_id=SYNC_JOB,
        after_data={"task_id": task_id, **kwargs},
    )
    return SyncQueuedResponse(task_id=task_id, job_key=SYNC_JOB)


# --- đợt 2: bảng kê, phí, công nợ, KPI ----------------------------------------------------


def _fees(fees: Fees) -> FeesResponse:
    return FeesResponse(
        card_fee=int(whole(fees.card_fee)),
        referral_fee=int(whole(fees.referral_fee)),
        car_fee=int(whole(fees.car_fee)),
        cost=int(whole(fees.cost)),
        total=int(whole(fees.total)),
    )


def _row(row: LedgerRow, names: bool) -> LedgerRowResponse:
    return LedgerRowResponse(
        order_id=row.order_id,
        day=row.day,
        kind=row.kind,
        order_code=row.order_code,
        product=row.product,
        customer_code=row.customer_code,
        customer_name=row.customer_name if names else None,
        branch=row.branch,
        sales=int(whole(row.sales)),
        owed=int(whole(row.owed)),
        owed_days=row.owed_days,
        collected=int(whole(row.collected)),
        methods={str(key): int(whole(value)) for key, value in row.methods.items()},
        refunds=int(whole(row.refunds)),
        vat=int(whole(row.vat)),
        fees=_fees(row.fees),
        auto=_fees(row.auto),
        typed=sorted(row.typed),
        note=row.note,
        net=int(whole(row.net)),
        sellers=[PersonAmount(name=who, amount=int(whole(amount))) for who, amount in row.sellers],
        doctors={
            SHARE_TYPE_LABELS.get(kind, str(kind)): PersonAmount(
                name=who, amount=int(whole(amount))
            )
            for kind, (who, amount) in row.doctors.items()
        },
        sale=row.sale,
        marketing=row.marketing,
        carepage=row.carepage,
    )


def _day(day: DaySummary) -> DaySummaryResponse:
    return DaySummaryResponse(
        day=day.day,
        sales=int(whole(day.sales)),
        owed=int(whole(day.owed)),
        service_sales=int(whole(day.service_sales)),
        product_sales=int(whole(day.product_sales)),
        received=int(whole(day.received)),
        refunds=int(whole(day.refunds)),
        collected=int(whole(day.collected)),
        vat=int(whole(day.vat)),
        fees=int(whole(day.fees)),
        net=int(whole(day.net)),
        kpi=int(whole(day.kpi)) if day.kpi is not None else None,
    )


@router.get("/ledger", response_model=LedgerResponse)
async def ledger(
    session: SessionDep,
    access: AccessDep,
    since: date = Query(),
    until: date = Query(),
) -> LedgerResponse:
    """The BCLT rows and the Tổng hợp days of ``since``-``until`` (≤ 31 days)."""
    access.require_revenue()
    check_span(since, until)
    built = await FinanceLedgerService(session).build(since, until, today=vn_today())
    return LedgerResponse(
        since=since,
        until=until,
        rows=[_row(row, access.customers) for row in built.rows],
        days=[_day(day) for day in built.days],
        kpi_month=int(whole(built.kpi_month)) if built.kpi_month is not None else None,
        methods={str(method): PAYMENT_TYPE_LABELS[method] for method in METHOD_ORDER},
        can_edit_fees=access.fees,
    )


@router.get("/ledger.xlsx")
async def ledger_export(
    session: SessionDep, access: AccessDep, month: str | None = Query(default=None)
) -> Response:
    """The month's BCLT + Tổng hợp workbook (the accounting sheet's layout)."""
    access.require_revenue()
    since, until = month_span(_month(month))
    built = await FinanceLedgerService(session).build(since, until, today=vn_today())
    data = await asyncio.to_thread(ledger_workbook, built, names=access.customers)
    name = f"bang-ke-doanh-thu-{since:%Y-%m}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.patch("/fees", response_model=LedgerRowResponse | None)
async def update_fees(
    body: FeeUpdateRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> LedgerRowResponse | None:
    """Type the fees of one BCLT row (FEES); answers the row as it now reads."""
    service = FinanceLedgerService(session)
    await service.set_fees(
        actor=actor,
        access=access,
        request_id=request_id,
        order_id=body.order_id,
        day=body.day,
        changes={
            key: getattr(body, key)
            for key in body.model_fields_set
            if key not in ("order_id", "day")
        },
    )
    built = await service.build(body.day, body.day, today=vn_today())
    row = next((item for item in built.rows if item.order_id == body.order_id), None)
    return _row(row, access.customers) if row is not None else None


@router.get("/debts", response_model=DebtReportResponse)
async def debts(
    session: SessionDep,
    access: AccessDep,
    min_bucket: int | None = Query(default=None, ge=1, le=3650),
    q: str = Query(default="", max_length=100),
) -> DebtReportResponse:
    access.require_revenue()
    report = await FinanceDebtService(session).report(today=vn_today(), min_bucket=min_bucket, q=q)
    return DebtReportResponse(
        today=report.today,
        milestones=list(report.milestones),
        lines=[
            DebtLineResponse(
                order_id=line.order_id,
                code=line.code,
                created_on=line.created_on,
                age=line.age,
                bucket=line.bucket,
                customer_code=line.customer_code,
                customer_name=line.customer_name if access.customers else None,
                branch=line.branch,
                owner=line.owner,
                total=int(whole(line.total)),
                owed=int(whole(line.owed)),
            )
            for line in report.lines[:2000]
        ],
        buckets=[
            DebtBucketResponse(
                milestone=bucket.milestone, orders=bucket.orders, owed=int(whole(bucket.owed))
            )
            for bucket in report.buckets
        ],
        owed=int(whole(report.owed)),
    )


@router.get("/debts/settings", response_model=DebtSettingsResponse)
async def debt_settings(session: SessionDep, access: AccessDep) -> DebtSettingsResponse:
    marks, recipients = await FinanceSettingsService(session, AuditService(session)).debt_settings()
    return DebtSettingsResponse(
        milestones=list(marks), recipients=recipients, can_edit=access.settings
    )


@router.put("/debts/settings", response_model=DebtSettingsResponse)
async def set_debt_settings(
    body: DebtSettingsRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> DebtSettingsResponse:
    marks, recipients = await FinanceSettingsService(
        session, AuditService(session)
    ).set_debt_settings(
        actor=actor,
        access=access,
        request_id=request_id,
        milestones=body.milestones,
        recipients=body.recipients,
    )
    return DebtSettingsResponse(milestones=list(marks), recipients=recipients, can_edit=True)


def _rules(rates: dict[int, Decimal], can_edit: bool) -> FeeRulesResponse:
    return FeeRulesResponse(
        methods={str(method): PAYMENT_TYPE_LABELS[method] for method in FEE_METHODS},
        rates={str(method): str(rate) for method, rate in rates.items()},
        can_edit=can_edit,
    )


@router.get("/fee-rules", response_model=FeeRulesResponse)
async def fee_rules(session: SessionDep, access: AccessDep) -> FeeRulesResponse:
    rates = await FinanceSettingsService(session, AuditService(session)).fee_rules()
    return _rules(rates, access.settings)


@router.put("/fee-rules", response_model=FeeRulesResponse)
async def set_fee_rules(
    body: FeeRulesRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> FeeRulesResponse:
    rates = await FinanceSettingsService(session, AuditService(session)).set_fee_rules(
        actor=actor, access=access, request_id=request_id, rates=dict(body.rates)
    )
    return _rules(rates, True)


@router.get("/service-costs", response_model=list[ServiceCostResponse])
async def service_costs(
    session: SessionDep, access: AccessDep, q: str = Query(default="", max_length=100)
) -> list[ServiceCostResponse]:
    rows = await FinanceSettingsService(session, AuditService(session)).service_costs(q=q)
    return [
        ServiceCostResponse(
            service_id=key, name=name, cost=int(whole(cost)) if cost is not None else None
        )
        for key, name, cost in rows
    ]


@router.put("/service-costs/{service_id}", response_model=ServiceCostResponse)
async def set_service_cost(
    service_id: int,
    body: ServiceCostRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> ServiceCostResponse:
    cost = await FinanceSettingsService(session, AuditService(session)).set_service_cost(
        actor=actor,
        access=access,
        request_id=request_id,
        service_id=service_id,
        name=body.name,
        cost=body.cost,
    )
    return ServiceCostResponse(
        service_id=service_id,
        name=body.name,
        cost=int(whole(cost)) if cost is not None else None,
    )


def _kpi(month: date, values: dict[tuple[str, str], Decimal], can_edit: bool) -> KpiResponse:
    return KpiResponse(
        month=month.strftime("%Y-%m"),
        values={f"{d}:{k}": int(whole(v)) for (d, k), v in values.items()},
        cells=kpi_keys(),
        labels={
            "total:": "Toàn công ty",
            **{f"site:{code}": label for code, label in BRANCHES.items()},
            **{f"team:{key}": TEAM_LABELS[key] for key in TEAM_LABELS},
        },
        can_edit=can_edit,
    )


@router.get("/kpi", response_model=KpiResponse)
async def kpi(
    session: SessionDep, access: AccessDep, month: str | None = Query(default=None)
) -> KpiResponse:
    first = _month(month)
    values = await FinanceKpiService(session, AuditService(session)).month(first)
    return _kpi(first, values, access.settings)


@router.put("/kpi", response_model=KpiResponse)
async def set_kpi(
    body: KpiRequest,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> KpiResponse:
    first = _month(body.month)
    values = await FinanceKpiService(session, AuditService(session)).save(
        actor=actor,
        access=access,
        request_id=request_id,
        month=first,
        entries=dict(body.entries),
    )
    return _kpi(first, values, True)


__all__ = ["router"]
