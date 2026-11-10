"""Hồ sơ nhân sự (0096, ``docs/hr/HR_MODULE_PLAN.md`` §5).

One profile per account (``hr_profiles``): what the HR sheet keeps beyond the
account itself (name, member code, job title, branch, company, status stay on
``users``). The ID number, bank account and pay are sealed together in
``secret`` (:class:`~meobot.core.secrets.SecretBox`, AAD ``hr_profile:<user>``);
their last four digits are kept apart for a masked display. Contracts, files
and the staff's own change requests hang off the account.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, deferred, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from meobot.db.models.pr import RESTRICT, USERS_TABLE

PROFILES = "hr_profiles"
CONTRACT_TYPES = "hr_contract_types"
CONTRACTS = "hr_contracts"
DOCUMENTS = "hr_documents"
REQUESTS = "hr_change_requests"


class HrProfile(Base, TimestampMixin):
    __tablename__ = PROFILES

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), primary_key=True
    )
    # personal
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    birth_place: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ethnicity: Mapped[str | None] = mapped_column(String(60), nullable=True)
    religion: Mapped[str | None] = mapped_column(String(60), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    id_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    id_issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    id_issued_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    work_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    personal_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    drive_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    permanent_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    bank_holder: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # work
    grade: Mapped[str | None] = mapped_column(String(20), nullable=True)
    work_form: Mapped[str | None] = mapped_column(String(40), nullable=True)
    joined_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    official_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    left_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    leave_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # insurance & tax
    social_insurance_no: Mapped[str | None] = mapped_column(String(30), nullable=True)
    social_insurance_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    health_insurance_no: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dependants: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Sealed JSON: id_number, bank_account, base_salary, allowance,
    #: insurance_salary, salary_type, salary_note.
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )


class HrContractType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = CONTRACT_TYPES
    __table_args__ = (CheckConstraint("months >= 0", name="months_not_negative"),)

    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 0 = indefinite ("không xác định thời hạn").
    months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class HrContract(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = CONTRACTS

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    type_code: Mapped[str] = mapped_column(String(20), nullable=False)
    start_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    signed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    signed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Ended early ("chấm dứt").
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Sealed salary on the contract (AAD ``hr_contract:<id>``).
    salary_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )


class HrDocument(Base, UUIDPrimaryKeyMixin):
    __tablename__ = DOCUMENTS
    __table_args__ = (CheckConstraint("size_bytes > 0", name="size_positive"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HrChangeRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Q13: a person asks HCNS to change their own contact details."""

    __tablename__ = REQUESTS

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "CONTRACTS",
    "CONTRACT_TYPES",
    "DOCUMENTS",
    "PROFILES",
    "REQUESTS",
    "HrChangeRequest",
    "HrContract",
    "HrContractType",
    "HrDocument",
    "HrProfile",
]
