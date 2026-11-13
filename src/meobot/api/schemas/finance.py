"""Tài chính (0105) - request / response bodies."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from meobot.application.finance.access import FinanceAccess
from meobot.application.finance.revenue import Figures, Line, MonthRevenue
from meobot.application.finance.settings import StaffPage
from meobot.db.models.finance import CrmBranchRecord
from meobot.db.models.legal_entity import LegalEntity
from meobot.domain.finance.crm import PAYMENT_ROLES
from meobot.domain.org.models import BRANCHES


class FinanceAccessResponse(BaseModel):
    revenue: bool
    customers: bool
    settings: bool
    fees: bool = False
    codes: bool = False

    @classmethod
    def from_access(cls, access: FinanceAccess) -> FinanceAccessResponse:
        return cls(
            revenue=access.revenue,
            customers=access.customers,
            settings=access.settings,
            fees=access.fees,
            codes=access.codes,
        )


class FiguresResponse(BaseModel):
    """Money in whole đồng."""

    collected: int
    collected_vat: int
    collected_net: int
    refunds: int
    payments: int
    sales: int
    revenue: int
    sales_vat: int
    owed: int
    orders: int

    @classmethod
    def from_domain(cls, figures: Figures) -> FiguresResponse:
        return cls(
            collected=int(figures.collected),
            collected_vat=int(figures.collected_vat),
            collected_net=int(figures.collected_net),
            refunds=int(figures.refunds),
            payments=figures.payments,
            sales=int(figures.sales),
            revenue=int(figures.revenue),
            sales_vat=int(figures.sales_vat),
            owed=int(figures.owed),
            orders=figures.orders,
        )


class LineResponse(BaseModel):
    key: str
    label: str
    figures: FiguresResponse
    #: The line's thực thu KPI this month (cơ sở / đội), when set.
    kpi: int | None = None

    @classmethod
    def from_domain(cls, line: Line, kpi: Decimal | None = None) -> LineResponse:
        return cls(
            key=line.key,
            label=line.label,
            figures=FiguresResponse.from_domain(line.figures),
            kpi=int(kpi) if kpi is not None else None,
        )


class DayResponse(BaseModel):
    day: date
    collected: int


class PersonLineResponse(BaseModel):
    crm_id: int
    name: str
    user_id: uuid.UUID | None
    user_name: str | None
    collected: int
    payments: int


class RoleResponse(BaseModel):
    role: str
    label: str
    people: list[PersonLineResponse]


class ShareLineResponse(BaseModel):
    crm_id: int | None
    name: str
    department: str | None
    user_name: str | None
    shared: int
    lines: int


class TourLineResponse(BaseModel):
    crm_id: int | None
    name: str
    user_name: str | None
    primary: int
    support: int


class MonthRevenueResponse(BaseModel):
    month: str
    figures: FiguresResponse
    previous: FiguresResponse
    #: The span ``previous`` covers (the same days while the month is running).
    previous_from: date
    previous_to: date
    by_entity: list[LineResponse]
    by_site: list[LineResponse]
    by_branch: list[LineResponse]
    by_team: list[LineResponse]
    days: list[DayResponse]
    roles: list[RoleResponse]
    shares: list[ShareLineResponse]
    tours: list[TourLineResponse]
    unmapped_branches: int
    #: The month's four fees, and thực thu after VAT and them.
    fees: int
    collected_after_fees: int
    kpi: int | None
    synced_at: str | None
    sync_status: str | None
    sync_message: str | None

    @classmethod
    def from_domain(cls, view: MonthRevenue) -> MonthRevenueResponse:
        coverage: dict[str, Any] = view.coverage
        return cls(
            month=view.month.strftime("%Y-%m"),
            figures=FiguresResponse.from_domain(view.figures),
            previous=FiguresResponse.from_domain(view.previous),
            previous_from=view.previous_from,
            previous_to=view.previous_to,
            by_entity=[LineResponse.from_domain(line) for line in view.by_entity],
            by_site=[
                LineResponse.from_domain(line, view.kpi.get(("site", line.key)))
                for line in view.by_site
            ],
            by_branch=[LineResponse.from_domain(line) for line in view.by_branch],
            by_team=[
                LineResponse.from_domain(line, view.kpi.get(("team", line.key)))
                for line in view.by_team
            ],
            days=[DayResponse(day=day, collected=int(amount)) for day, amount in view.days],
            roles=[
                RoleResponse(
                    role=role,
                    label=PAYMENT_ROLES[role],
                    people=[
                        PersonLineResponse(
                            crm_id=line.crm_id,
                            name=line.name,
                            user_id=line.user_id,
                            user_name=line.user_name,
                            collected=int(line.collected),
                            payments=line.payments,
                        )
                        for line in lines
                    ],
                )
                for role, lines in view.people.items()
            ],
            shares=[
                ShareLineResponse(
                    crm_id=line.crm_id,
                    name=line.name,
                    department=line.department,
                    user_name=line.user_name,
                    shared=int(line.shared),
                    lines=line.lines,
                )
                for line in view.shares
            ],
            tours=[
                TourLineResponse(
                    crm_id=line.crm_id,
                    name=line.name,
                    user_name=line.user_name,
                    primary=line.primary,
                    support=line.support,
                )
                for line in view.tours
            ],
            unmapped_branches=view.unmapped_branches,
            fees=int(view.fees),
            collected_after_fees=int(view.figures.collected_net - view.fees),
            kpi=int(view.kpi[("total", "")]) if ("total", "") in view.kpi else None,
            synced_at=coverage.get("at"),
            sync_status=coverage.get("status"),
            sync_message=coverage.get("message"),
        )


class EntityResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    active: bool


class SiteResponse(BaseModel):
    code: str
    label: str


class BranchResponse(BaseModel):
    id: int
    name: str
    location_id: int | None
    legal_entity_id: uuid.UUID | None
    apm_branch: str | None

    @classmethod
    def from_row(cls, row: CrmBranchRecord) -> BranchResponse:
        return cls(
            id=row.id,
            name=row.name,
            location_id=row.location_id,
            legal_entity_id=row.legal_entity_id,
            apm_branch=row.apm_branch,
        )


class BranchesResponse(BaseModel):
    branches: list[BranchResponse]
    entities: list[EntityResponse]
    sites: list[SiteResponse]
    can_edit: bool

    @classmethod
    def build(
        cls, rows: list[CrmBranchRecord], entities: list[LegalEntity], can_edit: bool
    ) -> BranchesResponse:
        return cls(
            branches=[BranchResponse.from_row(row) for row in rows],
            entities=[
                EntityResponse(id=item.id, code=item.code, name=item.name, active=item.active)
                for item in entities
            ],
            sites=[SiteResponse(code=code, label=label) for code, label in BRANCHES.items()],
            can_edit=can_edit,
        )


class BranchUpdateRequest(BaseModel):
    """Only the keys sent change; ``null`` clears."""

    legal_entity_id: uuid.UUID | None = None
    apm_branch: str | None = Field(default=None, max_length=10)


class StaffResponse(BaseModel):
    id: int
    name: str
    roles: list[str]
    department_name: str | None
    user_id: uuid.UUID | None
    user_name: str | None


class StaffPageResponse(BaseModel):
    rows: list[StaffResponse]
    total: int
    unmapped: int
    page_size: int
    can_edit: bool

    @classmethod
    def build(cls, page: StaffPage, size: int, can_edit: bool) -> StaffPageResponse:
        return cls(
            rows=[
                StaffResponse(
                    id=row.id,
                    name=row.name,
                    roles=list(row.roles or []),
                    department_name=row.department_name,
                    user_id=row.user_id,
                    user_name=user_name,
                )
                for row, user_name in page.rows
            ],
            total=page.total,
            unmapped=page.unmapped,
            page_size=size,
            can_edit=can_edit,
        )


class StaffUpdateRequest(BaseModel):
    user_id: uuid.UUID | None


class PersonChoiceResponse(BaseModel):
    id: uuid.UUID
    name: str
    member_code: str | None


class VatResponse(BaseModel):
    vat_default: str
    vat_by_entity: dict[str, str]
    can_edit: bool


class VatRequest(BaseModel):
    vat_default: str = Field(max_length=10)
    vat_by_entity: dict[str, str | None] = Field(default_factory=dict)


class SyncStatusResponse(BaseModel):
    configured: bool
    at: str | None
    status: str | None
    message: str | None
    synced_from: str | None
    synced_to: str | None
    running: bool
    can_sync: bool


class SyncRequest(BaseModel):
    since: date | None = None
    until: date | None = None


class SyncQueuedResponse(BaseModel):
    task_id: str
    job_key: str


# --- đợt 2 ---------------------------------------------------------------------------------


class FeesResponse(BaseModel):
    card_fee: int
    referral_fee: int
    car_fee: int
    cost: int
    total: int


class PersonAmount(BaseModel):
    name: str
    amount: int


class LedgerRowResponse(BaseModel):
    order_id: int | None
    day: date
    kind: str
    order_code: str | None
    product: bool
    customer_code: str | None
    #: Only with FINANCE CUSTOMERS, else null.
    customer_name: str | None
    branch: str | None
    sales: int
    owed: int
    owed_days: int | None
    collected: int
    methods: dict[str, int]
    refunds: int
    vat: int
    fees: FeesResponse
    auto: FeesResponse
    typed: list[str]
    note: str | None
    net: int
    sellers: list[PersonAmount]
    doctors: dict[str, PersonAmount]
    sale: str | None
    marketing: str | None
    carepage: str | None


class DaySummaryResponse(BaseModel):
    day: date
    sales: int
    owed: int
    service_sales: int
    product_sales: int
    received: int
    refunds: int
    collected: int
    vat: int
    fees: int
    net: int
    kpi: int | None


class LedgerResponse(BaseModel):
    since: date
    until: date
    rows: list[LedgerRowResponse]
    days: list[DaySummaryResponse]
    kpi_month: int | None
    methods: dict[str, str]
    can_edit_fees: bool


class FeeUpdateRequest(BaseModel):
    """Only the keys sent change; ``null`` / "" = back to the automatic value."""

    order_id: int
    day: date
    card_fee: str | None = Field(default=None, max_length=20)
    referral_fee: str | None = Field(default=None, max_length=20)
    car_fee: str | None = Field(default=None, max_length=20)
    cost: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=500)


class DebtLineResponse(BaseModel):
    order_id: int
    code: str | None
    created_on: date
    age: int
    bucket: int | None
    customer_code: str | None
    customer_name: str | None
    branch: str | None
    owner: str | None
    total: int
    owed: int


class DebtBucketResponse(BaseModel):
    milestone: int | None
    orders: int
    owed: int


class DebtReportResponse(BaseModel):
    today: date
    milestones: list[int]
    lines: list[DebtLineResponse]
    buckets: list[DebtBucketResponse]
    owed: int


class DebtSettingsResponse(BaseModel):
    milestones: list[int]
    recipients: list[uuid.UUID]
    can_edit: bool


class DebtSettingsRequest(BaseModel):
    milestones: list[int] = Field(max_length=6)
    recipients: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class FeeRulesResponse(BaseModel):
    """Payment method -> fee % (``FEE_METHODS``)."""

    methods: dict[str, str]
    rates: dict[str, str]
    can_edit: bool


class FeeRulesRequest(BaseModel):
    rates: dict[int, str | None]


class ServiceCostResponse(BaseModel):
    service_id: int
    name: str
    cost: int | None


class ServiceCostRequest(BaseModel):
    name: str = Field(max_length=300)
    cost: str | None = Field(default=None, max_length=20)


class KpiResponse(BaseModel):
    month: str
    #: ``"dimension:key"`` -> amount.
    values: dict[str, int]
    cells: dict[str, list[str]]
    labels: dict[str, str]
    can_edit: bool


class KpiRequest(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    entries: dict[str, str | None]
