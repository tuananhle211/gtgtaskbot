"""Đơn từ, phép năm, ca làm việc, chấm công (0100, ``docs/hr/HR_MODULE_PLAN.md`` §6-7).

* ``hr_leave_types`` - the kinds of leave (phép năm, ốm, không lương, …),
  HCNS edits them;
* ``hr_absence_requests`` + ``hr_absence_steps`` - a leave / late / early
  request and its approval chain (Trưởng phòng → Giám đốc khối → Giám đốc →
  HCNS), each step's approvers fixed when it is sent;
* ``hr_shifts`` - working patterns per weekday, by department / branch;
* ``hr_attendance_days`` - one person's first punch-in and last punch-out of a
  day, from the time clock's file or typed by HCNS;
* ``hr_leave_adjustments`` - days HCNS adds to / takes from someone's annual
  leave of a year (what was taken before APM, corrections).

Public holidays stay in ``organization_holidays``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, deferred, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin
from meobot.db.models.pr import RESTRICT, USERS_TABLE

LEAVE_TYPES = "hr_leave_types"
ABSENCES = "hr_absence_requests"
STEPS = "hr_absence_steps"
ABSENCE_FILES = "hr_absence_files"
SHIFTS = "hr_shifts"
ATTENDANCE = "hr_attendance_days"
ADJUSTMENTS = "hr_leave_adjustments"


class HrLeaveType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = LEAVE_TYPES

    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Counted as paid days on the timesheet ("công tính lương").
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    #: Taken from the person's annual leave ("phép năm").
    annual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    #: The most days one request should take (a warning, never a refusal).
    max_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Its letter on the timesheet ("P", "Ô", "KL"…).
    symbol: Mapped[str] = mapped_column(String(6), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class HrAbsenceRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = ABSENCES
    __table_args__ = (
        Index("ix_hr_absence_requests_user_start", "user_id", "start_on"),
        Index("ix_hr_absence_requests_status", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: LEAVE / LATE / EARLY (``domain.hr.attendance.AbsenceKind``).
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    leave_type_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    start_on: Mapped[date] = mapped_column(Date, nullable=False)
    end_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: FULL / MORNING / AFTERNOON (a one-day leave only).
    part: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'FULL'"))
    #: Late: the time one arrives; early: the time one leaves.
    at_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    #: 0101 "Giải trình chấm công": the real times the person declares.
    check_in: Mapped[time | None] = mapped_column(Time, nullable=True)
    check_out: Mapped[time | None] = mapped_column(Time, nullable=True)
    #: Late / early minutes against the shift.
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Working days a leave takes, worked out when sent.
    days: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: PENDING / APPROVED / REJECTED / CANCELLED.
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    cancel_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrAbsenceFile(Base, UUIDPrimaryKeyMixin):
    """0101: a picture proving a "Giải trình chấm công". Uploaded first
    (``request_id`` NULL, its uploader's), attached when the request is sent."""

    __tablename__ = ABSENCE_FILES
    __table_args__ = (CheckConstraint("size_bytes > 0", name="size_positive"),)

    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{ABSENCES}.id", ondelete="CASCADE"), nullable=True, index=True
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HrAbsenceStep(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = STEPS
    __table_args__ = (UniqueConstraint("request_id", "position"),)

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ABSENCES}.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: MANAGER / BLOCK / DIRECTOR / HR.
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Who may decide it, fixed when the request is sent (user ids as text).
    approver_ids: Mapped[list[str]] = mapped_column(
        JSONColumn, nullable=False, server_default=text("'[]'")
    )
    #: WAITING / PENDING / APPROVED / REJECTED / SKIPPED / CLOSED.
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrShift(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = SHIFTS

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: ``{"0": {"morning": ["08:00", "12:00"], "afternoon": [...]}, ..., "6": ...}``
    week: Mapped[dict[str, dict[str, list[str]]]] = mapped_column(
        JSONColumn, nullable=False, server_default=text("'{}'")
    )
    grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    #: Branch codes (``domain.org.models.BRANCHES``) it is for.
    branches: Mapped[list[str]] = mapped_column(
        JSONColumn, nullable=False, server_default=text("'[]'")
    )
    #: Department ids (text) it is for - wins over a branch.
    department_ids: Mapped[list[str]] = mapped_column(
        JSONColumn, nullable=False, server_default=text("'[]'")
    )
    #: Everybody no other shift names.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class HrAttendanceDay(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = ATTENDANCE
    __table_args__ = (UniqueConstraint("user_id", "day"), Index("ix_hr_attendance_days_day", "day"))

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    check_in: Mapped[time | None] = mapped_column(Time, nullable=True)
    check_out: Mapped[time | None] = mapped_column(Time, nullable=True)
    #: The person's code on the time clock ("Mã chấm công").
    machine_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: The file's tab ("VP - T10") or "Sửa tay".
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Set when HCNS typed it: a later import leaves the day alone.
    edited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrLeaveAdjustment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = ADJUSTMENTS

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Added to the year's annual leave (negative = already taken / taken away).
    days: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"), nullable=True
    )


__all__ = [
    "ABSENCES",
    "ABSENCE_FILES",
    "ADJUSTMENTS",
    "ATTENDANCE",
    "LEAVE_TYPES",
    "SHIFTS",
    "STEPS",
    "HrAbsenceFile",
    "HrAbsenceRequest",
    "HrAbsenceStep",
    "HrAttendanceDay",
    "HrLeaveAdjustment",
    "HrLeaveType",
    "HrShift",
]
