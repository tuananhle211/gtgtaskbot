"""Tài chính: what the CRM sync keeps for the revenue reports (0105, 2026-11-13).

One run reads the CRM for Phòng Ads and Tài chính together
(:mod:`meobot.application.crm.sync`); the payments themselves stay in
``ads_crm_payments`` (Phòng Ads reads them already, 0083), now with the people
on the customer's file, the order's owner, the order code and the customer's
name. Here:

* ``crm_branches`` - every CRM branch, with the APM legal entity ("pháp nhân")
  and APM branch ("cơ sở") it belongs to, set on "Tài chính > Cấu hình";
* ``crm_staff`` - every CRM person a payment, a share or a công tua named, and
  the APM account they are (set by hand, user 12/11 R5);
* ``crm_orders`` - orders by the day they were created: doanh số, VAT, còn nợ;
* ``crm_shared_revenues`` - the revenue a payment credits to a doctor / TVV /
  KTV / nurse (no CRM id: replaced by payment day);
* ``crm_tours`` - công tua, one row per person per treatment (replaced by day).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base
from meobot.db.models.legal_entity import LEGAL_ENTITIES
from meobot.db.models.pr import USERS_TABLE

CRM_BRANCHES = "crm_branches"
CRM_STAFF = "crm_staff"
CRM_ORDERS = "crm_orders"
CRM_SHARED_REVENUES = "crm_shared_revenues"
CRM_TOURS = "crm_tours"
FINANCE_FEES = "finance_fees"
FINANCE_SERVICE_COSTS = "finance_service_costs"
FINANCE_KPIS = "finance_kpis"

_JSON = JSON().with_variant(postgresql.JSONB(), "postgresql")
_MONEY = Numeric(16, 2)
#: An id the CRM numbers; ``BIGINT`` on PostgreSQL, ``INTEGER`` on SQLite so
#: an autoincrement key still works there.
_ROW_ID = BigInteger().with_variant(Integer(), "sqlite")


class CrmBranchRecord(Base):
    """A CRM branch and where it belongs in APM (user 12/11: a config screen)."""

    __tablename__ = CRM_BRANCHES

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The pháp nhân its revenue belongs to; ``None`` = "Chưa gắn".
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{LEGAL_ENTITIES}.id", ondelete="SET NULL"), nullable=True
    )
    #: The APM cơ sở (``domain.org.models.BRANCHES``: VP_HN / CS_HN / CS_HN2 / CS_SG).
    apm_branch: Mapped[str | None] = mapped_column(String(10), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrmStaffRecord(Base):
    """A CRM person (telesale, MKT, CSKH, carepage, order owner, doctor, TVV,
    KTV, nurse) and the APM account they are, set by hand."""

    __tablename__ = CRM_STAFF
    __table_args__ = (Index("ix_crm_staff_user", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Where they were seen: ``marketing`` / ``telesales`` / ``cskh`` /
    #: ``carepage`` / ``owner`` / ``share`` / ``tour``.
    roles: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    #: The CRM department a share named them under (Bác sĩ, TVV, KTV, Y tá).
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrmOrderRecord(Base):
    """One CRM order (not cancelled), by the day it was created."""

    __tablename__ = CRM_ORDERS
    __table_args__ = (
        Index("ix_crm_orders_created_on", "created_on"),
        Index("ix_crm_orders_code", "code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The Vietnam day it was created (the CRM filters on it).
    created_on: Mapped[date] = mapped_column(Date, nullable=False)
    branch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Doanh số: what the customer has to pay, VAT included.
    all_total: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    amount_before_vat: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    vat_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    gross_revenue: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    #: Còn nợ; 0 = paid in full.
    the_rest: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    discount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    payment_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: "Người lên đơn" (a CRM person).
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``[{service_id, service_name, quantity, total_price}]``.
    items: Mapped[list[dict[str, Any]]] = mapped_column(_JSON, nullable=False, default=list)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class CrmShareRecord(Base):
    """Revenue a payment credits to one person of a CRM department."""

    __tablename__ = CRM_SHARED_REVENUES
    __table_args__ = (
        Index("ix_crm_shared_revenues_day", "payment_date"),
        Index("ix_crm_shared_revenues_employee", "employee_id"),
    )

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    department_id: Mapped[int] = mapped_column(Integer, nullable=False)
    order_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    services: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    payment_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 0 Không chọn, 1 Bác sĩ tiêm, 2 Bác sĩ phẫu, 3 Bác sĩ khác, 4 Bác sĩ khác 2.
    share_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shared_revenue: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class CrmTourRecord(Base):
    """One công tua: one person's part in one treatment."""

    __tablename__ = CRM_TOURS
    __table_args__ = (
        Index("ix_crm_tours_day", "performed_on"),
        Index("ix_crm_tours_employee", "employee_id"),
    )

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    performed_on: Mapped[date] = mapped_column(Date, nullable=False)
    order_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: 0 Trừ liệu trình, 1 Bảo hành, 2 Bảo lưu.
    treatment_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``primary`` (công chính) / ``support`` (hỗ trợ).
    work_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    procedure_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class FinanceFee(Base):
    """The four fees of one BCLT row (an order on one day: the day it was
    created, or the day a later payment came in), typed by Kế toán (đợt 2,
    0106). ``None`` = the automatic value (card fee = the day's payments x the
    method's %, cost = the order's lines x the services' cost on its creation
    day, referral and car = 0)."""

    __tablename__ = FINANCE_FEES

    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    card_fee: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    referral_fee: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    car_fee: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The marks of "BCLT new" picked by Kế toán (0109; lists in
    #: ``domain/finance/ledger_marks.py``); ``None`` = APM's suggestion.
    status2: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    upsale: Mapped[str | None] = mapped_column(String(40), nullable=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class FinanceServiceCost(Base):
    """The cost price ("viện phí / giá cost") of one CRM service, per unit."""

    __tablename__ = FINANCE_SERVICE_COSTS

    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class FinanceKpi(Base):
    """A month's revenue KPI (thực thu) for the company, an APM cơ sở or a
    Marketing team (R7: typed on APM)."""

    __tablename__ = FINANCE_KPIS
    __table_args__ = (UniqueConstraint("month", "dimension", "key", name="uq_finance_kpis_cell"),)

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    #: The 1st of the month.
    month: Mapped[date] = mapped_column(Date, nullable=False)
    #: ``total`` / ``site`` (APM cơ sở code) / ``team`` (HN / HCM).
    dimension: Mapped[str] = mapped_column(String(10), nullable=False)
    key: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


__all__ = [
    "CRM_BRANCHES",
    "CRM_ORDERS",
    "CRM_SHARED_REVENUES",
    "CRM_STAFF",
    "CRM_TOURS",
    "FINANCE_FEES",
    "FINANCE_KPIS",
    "FINANCE_SERVICE_COSTS",
    "CrmBranchRecord",
    "CrmOrderRecord",
    "CrmShareRecord",
    "CrmStaffRecord",
    "CrmTourRecord",
    "FinanceFee",
    "FinanceKpi",
    "FinanceServiceCost",
]
