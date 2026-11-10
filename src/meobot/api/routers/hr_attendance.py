"""``/api/hr`` đợt 3-4 (0100): đơn từ, phép năm, ca, ngày lễ, chấm công.

* ``/absences`` - leave / late / early requests (``/form``, list ``?tab=mine|
  awaiting|all``, POST, ``/{id}``, ``/{id}/decide``, ``/{id}/cancel``);
* ``/leave-types``, ``/leave/balances``, ``/leave/adjustments``;
* ``/shifts``, ``/holidays``;
* ``/timesheet`` (month), ``/timesheet/people/{id}``, ``/timesheet/export.xlsx``,
  ``/timesheet/lock``, ``/attendance/import`` (raw .xlsx body, ``?commit=``),
  ``/attendance/{user}/{day}`` (HCNS types a day).

Who: :mod:`meobot.application.hr.access` (``TIMESHEET`` = HCNS). 403
``hr_forbidden``, 404 ``hr_not_found``, 409 ``hr_absence_moved``, 422 ``hr_*``.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SessionDep,
    SettingsDep,
    UnitMembershipDep,
)
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.application.hr.absences import HrAbsenceService
from meobot.application.hr.access import HrAccess, hr_access
from meobot.application.hr.profiles import HrValidationError
from meobot.application.hr.shifts import locked_months
from meobot.application.hr.timesheet import MAX_IMPORT_BYTES, HrTimesheetService
from meobot.core.config import Settings
from meobot.domain.hr.attendance import (
    KIND_LABELS,
    LEVEL_LABELS,
    PART_LABELS,
    STATUS_LABELS,
    WEEKDAY_LABELS,
    AbsenceKind,
    DayPart,
)
from meobot.domain.org.models import BRANCHES

router = APIRouter(prefix="/api/hr", tags=["hr"])


async def _ctx(
    session: SessionDep, settings: Settings, membership: UnitMembershipDep
) -> tuple[HrAbsenceService, HrTimesheetService, HrAccess]:
    audit = AuditService(session)
    return (
        HrAbsenceService(session, settings, audit),
        HrTimesheetService(session, settings, audit),
        await hr_access(session, membership),
    )


def _month(value: str | None) -> date:
    if not value:
        return vn_today().replace(day=1)
    try:
        year, month = value.split("-")[:2]
        return date(int(year), int(month), 1)
    except ValueError as error:
        raise HrValidationError("hr_invalid", "Tháng không hợp lệ (YYYY-MM).") from error


def _sent(body: BaseModel) -> dict[str, Any]:
    return {key: getattr(body, key) for key in body.model_fields_set}


# --- bodies ---------------------------------------------------------------------------------


class AbsenceBody(BaseModel):
    kind: AbsenceKind
    leave_type: str | None = Field(default=None, max_length=30)
    start_on: date
    end_on: date | None = None
    part: DayPart = DayPart.FULL
    at_time: time | None = None
    reason: str = Field(min_length=1, max_length=1000)


class DecideBody(BaseModel):
    approve: bool
    step_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


class CancelBody(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class LeaveTypeBody(BaseModel):
    code: str | None = Field(default=None, max_length=30)
    name: str | None = Field(default=None, max_length=200)
    paid: bool | None = None
    annual: bool | None = None
    max_days: int | None = Field(default=None, ge=0, le=365)
    symbol: str | None = Field(default=None, max_length=6)
    active: bool | None = None


class AdjustmentBody(BaseModel):
    user_id: uuid.UUID
    year: int = Field(ge=2000, le=2100)
    days: Decimal = Field(ge=-60, le=60)
    note: str = Field(min_length=1, max_length=1000)


class ShiftBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    week: dict[str, dict[str, list[str] | None]] | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=120)
    branches: list[str] | None = Field(default=None, max_length=10)
    department_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    is_default: bool | None = None
    active: bool | None = None


class HolidayBody(BaseModel):
    start: date
    end: date | None = None
    name: str = Field(min_length=1, max_length=200)


class LockBody(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    locked: bool


class DayBody(BaseModel):
    check_in: time | None = None
    check_out: time | None = None
    note: str = Field(min_length=1, max_length=1000)


# --- access + choices ---------------------------------------------------------------------


@router.get("/attendance/access")
async def attendance_access(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    return {
        "timesheet": access.timesheet,
        "lists": access.lists_attendance,
        "awaiting": await absences.awaiting_count(actor, access),
        "locked": sorted(await locked_months(session)),
        "choices": {
            "kinds": [{"code": kind.value, "label": label} for kind, label in KIND_LABELS.items()],
            "parts": [{"code": part.value, "label": label} for part, label in PART_LABELS.items()],
            "statuses": [
                {"code": item.value, "label": label} for item, label in STATUS_LABELS.items()
            ],
            "levels": [
                {"code": item.value, "label": label} for item, label in LEVEL_LABELS.items()
            ],
            "branches": [{"code": code, "label": label} for code, label in BRANCHES.items()],
            "weekdays": list(WEEKDAY_LABELS),
        },
    }


# --- requests ------------------------------------------------------------------------------


@router.get("/absences/form")
async def absence_form(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
) -> dict[str, Any]:
    absences, _sheet, _access = await _ctx(session, settings, membership)
    return await absences.form(actor=actor, today=vn_today())


@router.get("/absences")
async def list_absences(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    tab: Literal["mine", "awaiting", "all"] = "mine",
    request_status: str | None = Query(default=None, alias="status", max_length=12),
    kind: str | None = Query(default=None, max_length=10),
    month: str | None = Query(default=None, max_length=7),
    user_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    items = await absences.listing(
        actor=actor,
        access=access,
        tab=tab,
        status=request_status,
        kind=kind,
        month=_month(month) if month else None,
        user_id=user_id,
        q=q,
    )
    return {"items": items, "awaiting": await absences.awaiting_count(actor, access)}


@router.post("/absences", status_code=status.HTTP_201_CREATED)
async def submit_absence(
    body: AbsenceBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    row = await absences.submit(
        actor=actor,
        request_id=request_id,
        kind=body.kind,
        leave_type=body.leave_type,
        start_on=body.start_on,
        end_on=body.end_on,
        part=body.part,
        at_time=body.at_time,
        reason=body.reason,
        today=vn_today(),
    )
    return (await absences.views([row], actor=actor, access=access))[0]


@router.get("/absences/{absence_id}")
async def absence_detail(
    absence_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    return await absences.detail(
        actor=actor, access=access, absence_id=absence_id, today=vn_today()
    )


@router.post("/absences/{absence_id}/decide")
async def decide_absence(
    absence_id: uuid.UUID,
    body: DecideBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    await absences.decide(
        actor=actor,
        access=access,
        request_id=request_id,
        absence_id=absence_id,
        step_id=body.step_id,
        approve=body.approve,
        note=body.note,
    )
    return await absences.detail(
        actor=actor, access=access, absence_id=absence_id, today=vn_today()
    )


@router.post("/absences/{absence_id}/cancel")
async def cancel_absence(
    absence_id: uuid.UUID,
    body: CancelBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    await absences.cancel(
        actor=actor, access=access, request_id=request_id, absence_id=absence_id, note=body.note
    )
    return await absences.detail(
        actor=actor, access=access, absence_id=absence_id, today=vn_today()
    )


# --- leave types + annual leave ---------------------------------------------------------------


@router.get("/leave-types")
async def leave_types(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    rows = await absences.leave_types(include_inactive=access.timesheet)
    return {
        "items": [
            {
                "id": str(row.id),
                "code": row.code,
                "name": row.name,
                "paid": row.paid,
                "annual": row.annual,
                "max_days": row.max_days,
                "symbol": row.symbol,
                "active": row.active,
            }
            for row in rows
        ],
        "can_edit": access.timesheet,
    }


@router.post("/leave-types", status_code=status.HTTP_201_CREATED)
async def create_leave_type(
    body: LeaveTypeBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    row = await absences.save_leave_type(
        actor=actor, access=access, request_id=request_id, type_id=None, fields=_sent(body)
    )
    return {"id": str(row.id), "code": row.code}


@router.patch("/leave-types/{type_id}")
async def edit_leave_type(
    type_id: uuid.UUID,
    body: LeaveTypeBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    row = await absences.save_leave_type(
        actor=actor, access=access, request_id=request_id, type_id=type_id, fields=_sent(body)
    )
    return {"id": str(row.id), "code": row.code}


@router.get("/leave/me")
async def my_leave(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    year: int | None = Query(default=None, ge=2000, le=2100),
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    assert actor.user_id is not None
    today = vn_today()
    wanted = year or today.year
    balance = await absences.balance(actor.user_id, wanted, today)
    return {
        "balance": balance.as_dict(),
        "adjustments": await absences.adjustments(
            access=access, user_id=actor.user_id, year=wanted
        ),
    }


@router.get("/leave/balances")
async def leave_balances(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    year: int | None = Query(default=None, ge=2000, le=2100),
    department_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    today = vn_today()
    wanted = year or today.year
    return {
        "year": wanted,
        "items": await absences.balances(
            access=access, year=wanted, today=today, department_id=department_id, q=q
        ),
        "can_adjust": access.timesheet,
    }


@router.get("/leave/adjustments")
async def leave_adjustments(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    user_id: uuid.UUID,
    year: int = Query(ge=2000, le=2100),
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    return {"items": await absences.adjustments(access=access, user_id=user_id, year=year)}


@router.post("/leave/adjustments", status_code=status.HTTP_201_CREATED)
async def add_adjustment(
    body: AdjustmentBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    absences, _sheet, access = await _ctx(session, settings, membership)
    row = await absences.adjust(
        actor=actor,
        access=access,
        request_id=request_id,
        user_id=body.user_id,
        year=body.year,
        days=body.days,
        note=body.note,
    )
    return {"id": str(row.id)}


@router.delete("/leave/adjustments/{adjustment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_adjustment(
    adjustment_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    absences, _sheet, access = await _ctx(session, settings, membership)
    await absences.delete_adjustment(
        actor=actor, access=access, request_id=request_id, adjustment_id=adjustment_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- shifts + holidays ----------------------------------------------------------------------


@router.get("/shifts")
async def list_shifts(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    return {"items": await sheet.shifts(), "can_edit": access.timesheet}


@router.post("/shifts", status_code=status.HTTP_201_CREATED)
async def create_shift(
    body: ShiftBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    row = await sheet.save_shift(
        actor=actor, access=access, request_id=request_id, shift_id=None, fields=_sent(body)
    )
    return sheet._shift_view(row)


@router.patch("/shifts/{shift_id}")
async def edit_shift(
    shift_id: uuid.UUID,
    body: ShiftBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    row = await sheet.save_shift(
        actor=actor, access=access, request_id=request_id, shift_id=shift_id, fields=_sent(body)
    )
    return sheet._shift_view(row)


@router.get("/holidays")
async def list_holidays(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    year: int | None = Query(default=None, ge=2000, le=2100),
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    wanted = year or vn_today().year
    return {"year": wanted, "items": await sheet.holidays(wanted), "can_edit": access.timesheet}


@router.post("/holidays", status_code=status.HTTP_201_CREATED)
async def add_holidays(
    body: HolidayBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    count = await sheet.save_holidays(
        actor=actor,
        access=access,
        request_id=request_id,
        start=body.start,
        end=body.end,
        name=body.name,
    )
    return {"days": count}


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    holiday_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    _absences, sheet, access = await _ctx(session, settings, membership)
    await sheet.delete_holiday(
        actor=actor, access=access, request_id=request_id, holiday_id=holiday_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- timesheet -----------------------------------------------------------------------------


@router.get("/timesheet")
async def timesheet(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    month: str | None = Query(default=None, max_length=7),
    department_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    return await sheet.month(
        access=access, month=_month(month), today=vn_today(), department_id=department_id, q=q
    )


@router.get("/timesheet/people/{user_id}")
async def timesheet_person(
    user_id: str,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    month: str | None = Query(default=None, max_length=7),
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    target = actor.user_id if user_id == "me" else uuid.UUID(user_id)
    assert target is not None
    return await sheet.person(access=access, user_id=target, month=_month(month), today=vn_today())


@router.get("/timesheet/export.xlsx")
async def export_timesheet(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    month: str | None = Query(default=None, max_length=7),
    department_id: uuid.UUID | None = None,
) -> Response:
    _absences, sheet, access = await _ctx(session, settings, membership)
    wanted = _month(month)
    data = await sheet.export(
        access=access, month=wanted, today=vn_today(), department_id=department_id
    )
    name = f"bang-cong-{wanted.year}-{wanted.month:02d}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.put("/timesheet/lock")
async def lock_timesheet(
    body: LockBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    locked = await sheet.set_lock(
        actor=actor,
        access=access,
        request_id=request_id,
        month=_month(body.month),
        locked=body.locked,
    )
    return {"locked": locked}


@router.post("/attendance/import")
async def import_attendance(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    commit: bool = False,
) -> dict[str, Any]:
    """The workbook's raw bytes are the body; ``commit=false`` = preview."""
    _absences, sheet, access = await _ctx(session, settings, membership)
    access.require_timesheet()
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_IMPORT_BYTES:
            raise HrValidationError(
                "hr_attendance_invalid", f"File lớn quá {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
            )
    return await sheet.import_file(
        actor=actor, access=access, request_id=request_id, data=bytes(data), commit=commit
    )


@router.put("/attendance/{user_id}/{day}")
async def edit_attendance(
    user_id: uuid.UUID,
    day: date,
    body: DayBody,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _absences, sheet, access = await _ctx(session, settings, membership)
    await sheet.edit_day(
        actor=actor,
        access=access,
        request_id=request_id,
        user_id=user_id,
        day=day,
        check_in=body.check_in,
        check_out=body.check_out,
        note=body.note,
    )
    return await sheet.person(
        access=access, user_id=user_id, month=day.replace(day=1), today=vn_today()
    )
