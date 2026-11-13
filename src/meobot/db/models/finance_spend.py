"""Tài chính đợt 3 (0107): mã chi + tuyến duyệt, vai trò tài chính, hồ sơ chi,
chứng từ, ngân sách, số dư MISA. See :mod:`meobot.domain.finance.spend`."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, deferred, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base
from meobot.db.models.department import DEPARTMENTS
from meobot.db.models.legal_entity import LEGAL_ENTITIES
from meobot.db.models.pr import USERS_TABLE

_JSON = JSON().with_variant(postgresql.JSONB(), "postgresql")
_MONEY = Numeric(18, 2)
_ROW_ID = BigInteger().with_variant(Integer(), "sqlite")
_USER = f"{USERS_TABLE}.id"


def _who() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey(_USER, ondelete="SET NULL"), nullable=True)


def _now() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class FinanceCode(Base):
    """A budget code ("mã ngân sách") and its route (``Route.as_value``)."""

    __tablename__ = "finance_codes"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    leaf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    route: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    updated_by_user_id: Mapped[uuid.UUID | None] = _who()
    updated_at: Mapped[datetime] = _now()


class FinanceCodeChange(Base):
    """Append-only history of a code's route / name (F1: "có lưu lịch sử")."""

    __tablename__ = "finance_code_changes"
    __table_args__ = (Index("ix_finance_code_changes_code", "code"),)

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    changed_by_user_id: Mapped[uuid.UUID | None] = _who()
    changed_at: Mapped[datetime] = _now()


class FinanceRoleAssignment(Base):
    """A person holding a finance role, optionally for one pháp nhân and / or
    one APM cơ sở, with a stand-in (F7)."""

    __tablename__ = "finance_role_assignments"
    __table_args__ = (Index("ix_finance_role_assignments_role", "role"),)

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_USER, ondelete="CASCADE"), nullable=False
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{LEGAL_ENTITIES}.id", ondelete="CASCADE"), nullable=True
    )
    apm_branch: Mapped[str | None] = mapped_column(String(10), nullable=True)
    standin_user_id: Mapped[uuid.UUID | None] = _who()
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _who()
    created_at: Mapped[datetime] = _now()


class FinanceRequest(Base):
    """A hồ sơ chi: one spending, from the request to the money handed out."""

    __tablename__ = "finance_requests"
    __table_args__ = (
        Index("ix_finance_requests_requester", "requester_id"),
        Index("ix_finance_requests_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    requester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_USER, ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{DEPARTMENTS}.id", ondelete="SET NULL"), nullable=True
    )
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{LEGAL_ENTITIES}.id", ondelete="RESTRICT"), nullable=False
    )
    apm_branch: Mapped[str | None] = mapped_column(String(10), nullable=True)
    budget_code: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    vendor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payee: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    #: The month the spending belongs to (budget) - the 1st.
    budget_month: Mapped[date] = mapped_column(Date, nullable=False)
    needed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The phase the open step is in; ``None`` once ended.
    phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The route as it was when sent (later edits to the code do not move it).
    route: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    #: The executor's record (quote, order, handover, acceptance).
    purchase_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _now()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: B5: purchases at acceptance, everything else when paid.
    realized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: F5: papers still missing when paid have 7 days.
    docs_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class FinanceRequestStep(Base):
    __tablename__ = "finance_request_steps"
    __table_args__ = (Index("ix_finance_request_steps_request", "request_id"),)

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_requests.id", ondelete="CASCADE"), nullable=False
    )
    #: Which round of the request (a return starts a new one).
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    approver_id: Mapped[uuid.UUID | None] = _who()
    standin_id: Mapped[uuid.UUID | None] = _who()
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = _who()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Why it was skipped / who it was escalated from (F6).
    remark: Mapped[str | None] = mapped_column(String(300), nullable=True)


class FinanceRequestEvent(Base):
    """Append-only history of a request."""

    __tablename__ = "finance_request_events"
    __table_args__ = (Index("ix_finance_request_events_request", "request_id"),)

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_requests.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = _who()
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = _now()


class FinanceDocument(Base):
    """A paper of a request: a file (kept on Google Drive when configured, M2,
    else in the database) or "đã nhận bản giấy" (``storage`` = ``paper``)."""

    __tablename__ = "finance_documents"
    __table_args__ = (Index("ix_finance_documents_request", "request_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_requests.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage: Mapped[str] = mapped_column(String(10), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    uploaded_by_user_id: Mapped[uuid.UUID | None] = _who()
    created_at: Mapped[datetime] = _now()


class FinanceBudget(Base):
    """B1: pháp nhân x phòng ban x mã chi x tháng (no cơ sở)."""

    __tablename__ = "finance_budgets"
    __table_args__ = (
        UniqueConstraint(
            "month", "legal_entity_id", "department_id", "code", name="uq_finance_budgets_cell"
        ),
    )

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{LEGAL_ENTITIES}.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{DEPARTMENTS}.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = _who()
    updated_at: Mapped[datetime] = _now()


class FinanceMisaBalance(Base):
    """One account line of a MISA F01-DNN "Bảng cân đối tài khoản" (M4)."""

    __tablename__ = "finance_misa_balances"
    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id", "month", "account", name="uq_finance_misa_balances_line"
        ),
    )

    id: Mapped[int] = mapped_column(_ROW_ID, primary_key=True, autoincrement=True)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{LEGAL_ENTITIES}.id", ondelete="CASCADE"), nullable=False
    )
    month: Mapped[date] = mapped_column(Date, nullable=False)
    account: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    opening_debit: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    opening_credit: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    debit: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    credit: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    closing_debit: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    closing_credit: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    imported_by_user_id: Mapped[uuid.UUID | None] = _who()
    imported_at: Mapped[datetime] = _now()


__all__ = [
    "FinanceBudget",
    "FinanceCode",
    "FinanceCodeChange",
    "FinanceDocument",
    "FinanceMisaBalance",
    "FinanceRequest",
    "FinanceRequestEvent",
    "FinanceRequestStep",
    "FinanceRoleAssignment",
]
