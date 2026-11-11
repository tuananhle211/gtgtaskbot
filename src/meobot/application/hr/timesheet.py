"""Chấm công (0100): shifts, holidays, the time clock's file, the month's
timesheet, the lock and the Excel file.

Who: HCNS (``HR`` / ``TIMESHEET``) does everything and sees everybody; a
Trưởng phòng (``VIEW OWN``) sees their departments' people; everybody sees
their own. A locked month changes no more (its punches, requests).

The timesheet of one day is :func:`meobot.domain.hr.attendance.evaluate_day`
over the person's shift, the holidays, the punches and the approved requests.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.hr.absences import HrAbsenceService
from meobot.application.hr.access import HrAccess, HrForbiddenError, HrNotFoundError
from meobot.application.hr.attendance_import import ParsedFile, read_workbook
from meobot.application.hr.profiles import HrValidationError
from meobot.application.hr.shifts import (
    LOCKS_KEY,
    MonthLockedError,
    holidays_between,
    load_shift_book,
    locked_months,
    month_key,
    require_open,
)
from meobot.application.org.seats import load_seats
from meobot.core.config import Settings
from meobot.db.models.department import Department
from meobot.db.models.hr import OrganizationHoliday
from meobot.db.models.hr_attendance import HrAttendanceDay, HrLeaveType, HrShift
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.hr.attendance import (
    SESSIONS,
    SYMBOL_LABELS,
    WEEKDAY_SHORT,
    AbsenceKind,
    DayPart,
    DayResult,
    LeaveCover,
    MonthTotals,
    clock_text,
    evaluate_day,
    month_days,
    parts_of,
    shift_from_json,
    week_errors,
    week_to_json,
)
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import BRANCHES
from meobot.domain.units.member_code import normalise_member_code

MAX_IMPORT_BYTES = 20 * 1024 * 1024
MANUAL_SOURCE = "Sửa tay"


@dataclass(frozen=True, slots=True)
class PersonSheet:
    user: User
    departments: list[str]
    shift_name: str
    days: list[DayResult]
    totals: MonthTotals


def _day_view(result: DayResult, *, full: bool) -> dict[str, Any]:
    view: dict[str, Any] = {
        "day": result.day.isoformat(),
        "symbol": result.symbol,
        "in": clock_text(result.check_in),
        "out": clock_text(result.check_out),
        "late": result.late_counted,
        "early": result.early_counted,
        "missing": result.missing,
        "edited": result.edited,
        "explained": result.explained,
    }
    if full:
        view |= {
            "weekday": WEEKDAY_SHORT[result.day.weekday()],
            "expected": float(result.expected),
            "worked": float(result.worked),
            "paid_leave": float(result.paid_leave),
            "unpaid_leave": float(result.unpaid_leave),
            "holiday": float(result.holiday),
            "absent": float(result.absent),
            "late_raw": result.late,
            "early_raw": result.early,
            "late_excused": result.late_excused,
            "early_excused": result.early_excused,
            "leave_names": list(result.leave_names),
            "note": result.note,
        }
    return view


class HrTimesheetService:
    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit

    # --- shifts --------------------------------------------------------------------------

    async def shifts(self) -> list[dict[str, Any]]:
        rows = (
            await self._session.scalars(
                select(HrShift).order_by(HrShift.sort_order, HrShift.created_at)
            )
        ).all()
        return [self._shift_view(row) for row in rows]

    @staticmethod
    def _shift_view(row: HrShift) -> dict[str, Any]:
        shift = shift_from_json(row.name, row.week, row.grace_minutes)
        return {
            "id": str(row.id),
            "name": row.name,
            "week": week_to_json(shift),
            "grace_minutes": row.grace_minutes,
            "branches": list(row.branches or []),
            "department_ids": list(row.department_ids or []),
            "is_default": row.is_default,
            "active": row.active,
        }

    async def save_shift(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        shift_id: uuid.UUID | None,
        fields: dict[str, Any],
    ) -> HrShift:
        access.require_timesheet()
        if shift_id is None:
            row = HrShift(name="", week={}, branches=[], department_ids=[])
            self._session.add(row)
        else:
            found = await self._session.get(HrShift, shift_id)
            if found is None:
                raise HrNotFoundError("Không tìm thấy ca.")
            row = found
        if "name" in fields or shift_id is None:
            name = str(fields.get("name") or "").strip()
            if not name:
                raise HrValidationError("hr_shift_invalid", "Nhập tên ca.", field="name")
            row.name = name[:120]
        if "week" in fields or shift_id is None:
            week = fields.get("week") or {}
            problems = week_errors(week)
            if problems:
                raise HrValidationError("hr_shift_invalid", " ".join(problems), field="week")
            shift = shift_from_json(row.name, week, 0)
            if not shift.week:
                raise HrValidationError(
                    "hr_shift_invalid", "Ca phải có ít nhất một buổi làm.", field="week"
                )
            row.week = week_to_json(shift)
        if fields.get("grace_minutes") is not None:
            grace = int(fields["grace_minutes"])
            if not 0 <= grace <= 120:
                raise HrValidationError(
                    "hr_shift_invalid", "Phút cho phép trễ từ 0 đến 120.", field="grace_minutes"
                )
            row.grace_minutes = grace
        if fields.get("branches") is not None:
            branches = [str(item) for item in fields["branches"]]
            if any(item not in BRANCHES for item in branches):
                raise HrValidationError(
                    "hr_shift_invalid", "Chi nhánh không hợp lệ.", field="branches"
                )
            row.branches = branches
        if fields.get("department_ids") is not None:
            ids = [str(uuid.UUID(str(item))) for item in fields["department_ids"]]
            if ids:
                known = {
                    str(item)
                    for item in (
                        await self._session.scalars(
                            select(Department.id).where(
                                Department.id.in_([uuid.UUID(item) for item in ids])
                            )
                        )
                    ).all()
                }
                if set(ids) - known:
                    raise HrValidationError(
                        "hr_shift_invalid", "Phòng ban không hợp lệ.", field="department_ids"
                    )
            row.department_ids = ids
        if fields.get("active") is not None:
            row.active = bool(fields["active"])
        if fields.get("is_default"):
            for other in (await self._session.scalars(select(HrShift))).all():
                other.is_default = other is row
            row.is_default = True
            row.active = True
        if row.is_default and not row.active:
            raise HrValidationError(
                "hr_shift_invalid", "Không tắt được ca mặc định; chọn ca mặc định khác trước."
            )
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.HR_SHIFT_SAVED, {"shift": row.name})
        return row

    # --- holidays ----------------------------------------------------------------------

    async def holidays(self, year: int) -> list[dict[str, Any]]:
        found = await holidays_between(self._session, date(year, 1, 1), date(year, 12, 31))
        rows = (
            (
                await self._session.scalars(
                    select(OrganizationHoliday)
                    .where(OrganizationHoliday.holiday_date.in_(list(found)))
                    .order_by(OrganizationHoliday.holiday_date)
                )
            ).all()
            if found
            else []
        )
        return [
            {"id": str(row.id), "day": row.holiday_date.isoformat(), "name": row.name}
            for row in rows
        ]

    async def save_holidays(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        start: date,
        end: date | None,
        name: str,
    ) -> int:
        """One holiday or a run of days ("Tết": 5 days) under one name."""
        access.require_timesheet()
        text = (name or "").strip()
        if not text:
            raise HrValidationError("hr_holiday_invalid", "Nhập tên ngày lễ.", field="name")
        last = end or start
        if last < start or (last - start).days > 15:
            raise HrValidationError(
                "hr_holiday_invalid", "Khoảng ngày lễ không hợp lệ (tối đa 16 ngày).", field="end"
            )
        days = [start + timedelta(days=offset) for offset in range((last - start).days + 1)]
        await require_open(self._session, days)
        existing = {
            row.holiday_date: row
            for row in (
                await self._session.scalars(
                    select(OrganizationHoliday).where(OrganizationHoliday.holiday_date.in_(days))
                )
            ).all()
        }
        for day in days:
            if day in existing:
                existing[day].name = text[:200]
            else:
                self._session.add(
                    OrganizationHoliday(
                        holiday_date=day,
                        name=text[:200],
                        is_paid=True,
                        created_by_user_id=actor.user_id,
                    )
                )
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_HOLIDAY_SAVED,
            {"from": start.isoformat(), "to": last.isoformat(), "name": text},
        )
        return len(days)

    async def delete_holiday(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, holiday_id: uuid.UUID
    ) -> None:
        access.require_timesheet()
        row = await self._session.get(OrganizationHoliday, holiday_id)
        if row is None:
            raise HrNotFoundError("Không tìm thấy ngày lễ.")
        await require_open(self._session, [row.holiday_date])
        await self._session.delete(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_HOLIDAY_SAVED,
            {"removed": row.holiday_date.isoformat()},
        )

    # --- locks ---------------------------------------------------------------------------

    async def set_lock(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, month: date, locked: bool
    ) -> list[str]:
        access.require_timesheet()
        key = month_key(month)
        current = set(await locked_months(self._session))
        if locked:
            current.add(key)
        else:
            current.discard(key)
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == LOCKS_KEY)
        )
        value = {"locked": sorted(current)}
        if row is None:
            self._session.add(
                SystemSetting(
                    key=LOCKS_KEY,
                    value=value,
                    description="Chấm công: các tháng đã khoá bảng công.",
                    updated_by=actor.user_id,
                )
            )
        else:
            row.value = value
            row.updated_by = actor.user_id
        await self._session.flush()
        await self._record(
            actor, request_id, AuditAction.HR_TIMESHEET_LOCKED, {"month": key, "locked": locked}
        )
        return sorted(current)

    # --- the time clock's file -----------------------------------------------------------

    async def import_file(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, data: bytes, commit: bool
    ) -> dict[str, Any]:
        access.require_timesheet()
        if len(data) > MAX_IMPORT_BYTES:
            raise HrValidationError(
                "hr_attendance_invalid", f"File lớn quá {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
            )
        # openpyxl over a 20 MB file is seconds of CPU: off the event loop.
        parsed: ParsedFile = await asyncio.to_thread(read_workbook, data)
        codes = {normalise_member_code(row.code) for row in parsed.rows} - {None}
        users = (
            {
                user.member_code: user
                for user in (
                    await self._session.scalars(
                        select(User).where(User.member_code.in_(codes), User.deleted_at.is_(None))
                    )
                ).all()
            }
            if codes
            else {}
        )
        unmatched: dict[str, int] = {}
        merged: dict[tuple[uuid.UUID, date], dict[str, Any]] = {}
        for row in parsed.rows:
            code = normalise_member_code(row.code)
            user = users.get(code) if code else None
            if user is None:
                unmatched[row.code] = unmatched.get(row.code, 0) + 1
                continue
            key = (user.id, row.day)
            found = merged.get(key)
            if found is None:
                merged[key] = {
                    "in": row.check_in,
                    "out": row.check_out,
                    "machine": row.machine_code,
                    "sheet": row.sheet,
                }
                continue
            ins = [item for item in (found["in"], row.check_in) if item is not None]
            outs = [item for item in (found["out"], row.check_out) if item is not None]
            found["in"] = min(ins) if ins else None
            found["out"] = max(outs) if outs else None
        days = sorted({day for _, day in merged})
        months = sorted({month_key(day) for day in days})
        locked = sorted(set(months) & set(await locked_months(self._session)))
        existing: dict[tuple[uuid.UUID, date], HrAttendanceDay] = {}
        if merged:
            for item in (
                await self._session.scalars(
                    select(HrAttendanceDay).where(
                        HrAttendanceDay.user_id.in_({user for user, _ in merged}),
                        HrAttendanceDay.day >= days[0],
                        HrAttendanceDay.day <= days[-1],
                    )
                )
            ).all():
                existing[(item.user_id, item.day)] = item
        kept = sum(
            1 for key in merged if key in existing and existing[key].edited_by_user_id is not None
        )
        updated = sum(
            1 for key in merged if key in existing and existing[key].edited_by_user_id is None
        )
        report = {
            "sheets": [
                {
                    "name": item.name,
                    "read": item.read,
                    "reason": item.reason,
                    "rows": item.rows,
                    "bad_rows": item.bad_rows,
                }
                for item in parsed.sheets
            ],
            "problems": parsed.problems,
            "rows": len(parsed.rows),
            "people": len({user for user, _ in merged}),
            "days": len(merged),
            "from": days[0].isoformat() if days else None,
            "to": days[-1].isoformat() if days else None,
            "months": months,
            "locked": locked,
            "new_days": len(merged) - kept - updated,
            "updated_days": updated,
            "kept_manual": kept,
            "unmatched": [
                {"code": code, "rows": count}
                for code, count in sorted(unmatched.items(), key=lambda item: item[0])
            ],
            "committed": False,
        }
        if not commit:
            return report
        if parsed.problems or not merged:
            raise HrValidationError(
                "hr_attendance_invalid",
                parsed.problems[0]
                if parsed.problems
                else "File không có dòng chấm công nào khớp mã nhân viên.",
            )
        if locked:
            raise MonthLockedError(locked)
        for (user_id, day), values in merged.items():
            stored = existing.get((user_id, day))
            if stored is None:
                self._session.add(
                    HrAttendanceDay(
                        user_id=user_id,
                        day=day,
                        check_in=values["in"],
                        check_out=values["out"],
                        machine_code=values["machine"],
                        source=values["sheet"],
                    )
                )
            elif stored.edited_by_user_id is None:
                stored.check_in = values["in"]
                stored.check_out = values["out"]
                stored.machine_code = values["machine"]
                stored.source = values["sheet"]
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_ATTENDANCE_IMPORTED,
            {key: report[key] for key in ("rows", "people", "days", "from", "to", "kept_manual")}
            | {"unmatched": len(unmatched)},
        )
        report["committed"] = True
        return report

    async def edit_day(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        day: date,
        check_in: time | None,
        check_out: time | None,
        note: str | None,
    ) -> HrAttendanceDay:
        access.require_timesheet()
        await require_open(self._session, [day])
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise HrNotFoundError()
        if check_in is not None and check_out is not None and check_out <= check_in:
            raise HrValidationError(
                "hr_attendance_invalid", "Giờ ra phải sau giờ vào.", field="check_out"
            )
        text = (note or "").strip() or None
        if not text:
            raise HrValidationError(
                "hr_attendance_invalid", "Nhập lý do sửa giờ chấm công.", field="note"
            )
        row = await self._session.scalar(
            select(HrAttendanceDay).where(
                HrAttendanceDay.user_id == user_id, HrAttendanceDay.day == day
            )
        )
        before: dict[str, object] | None = None
        if row is None:
            row = HrAttendanceDay(user_id=user_id, day=day)
            self._session.add(row)
        else:
            before = {"in": clock_text(row.check_in), "out": clock_text(row.check_out)}
        row.check_in = check_in
        row.check_out = check_out
        row.note = text[:1000]
        row.source = MANUAL_SOURCE
        row.edited_by_user_id = actor.user_id
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.HR_ATTENDANCE_EDITED.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_profile",
            entity_id=str(user_id),
            before_data=before,
            after_data={
                "day": day.isoformat(),
                "in": clock_text(check_in),
                "out": clock_text(check_out),
            },
        )
        return row

    # --- the month -----------------------------------------------------------------------

    async def _people(
        self,
        access: HrAccess,
        first: date,
        last: date,
        department_id: uuid.UUID | None,
        q: str | None,
        user_id: uuid.UUID | None,
    ) -> list[User]:
        if user_id is not None:
            if not access.sees_attendance(user_id):
                raise HrNotFoundError()
            user = await self._session.get(User, user_id)
            if user is None or user.deleted_at is not None:
                raise HrNotFoundError()
            return [user]
        if not access.lists_attendance:
            raise HrForbiddenError()
        punched = select(HrAttendanceDay.user_id).where(
            HrAttendanceDay.day >= first, HrAttendanceDay.day <= last
        )
        statement = select(User).where(
            User.deleted_at.is_(None),
            or_(User.status == UserStatus.ACTIVE, User.id.in_(punched)),
        )
        if not access.timesheet and not access.view_all:
            statement = statement.where(User.id.in_(set(access.team) | {access.user_id}))
        if q:
            needle = f"%{q.strip()}%"
            statement = statement.where(
                or_(User.full_name.ilike(needle), User.member_code.ilike(needle))
            )
        users = list((await self._session.scalars(statement)).all())
        if department_id is not None:
            seats = await load_seats(self._session, [user.id for user in users])
            users = [
                user
                for user in users
                if any(seat.department_id == department_id for seat in seats.get(user.id, ()))
            ]
        users.sort(key=lambda user: (user.member_code or "~", user.full_name or ""))
        return users

    async def compute(self, users: Sequence[User], month: date, today: date) -> list[PersonSheet]:
        days = month_days(month)
        first, last = days[0], days[-1]
        ids = [user.id for user in users]
        if not ids:
            return []
        book = await load_shift_book(self._session, ids)
        holidays = await holidays_between(self._session, first, last)
        punches: dict[tuple[uuid.UUID, date], HrAttendanceDay] = {
            (row.user_id, row.day): row
            for row in (
                await self._session.scalars(
                    select(HrAttendanceDay).where(
                        HrAttendanceDay.user_id.in_(ids),
                        HrAttendanceDay.day >= first,
                        HrAttendanceDay.day <= last,
                    )
                )
            ).all()
        }
        requests = await HrAbsenceService(
            self._session, self._settings, self._audit
        ).approved_between(ids, first, last)
        types = {row.code: row for row in (await self._session.scalars(select(HrLeaveType))).all()}
        leaves: dict[tuple[uuid.UUID, date], list[LeaveCover]] = {}
        late: dict[tuple[uuid.UUID, date], int] = {}
        early: dict[tuple[uuid.UUID, date], int] = {}
        #: 0101: approved "Giải trình chấm công" = the declared times win.
        declared: dict[tuple[uuid.UUID, date], tuple[time | None, time | None]] = {}
        for request in requests:
            kind = AbsenceKind(request.kind)
            if kind is AbsenceKind.LEAVE:
                leave = types.get(request.leave_type_code or "")
                sessions = (
                    parts_of(DayPart(request.part))
                    if request.start_on == request.end_on
                    else SESSIONS
                )
                day = max(request.start_on, first)
                while day <= min(request.end_on, last):
                    leaves.setdefault((request.user_id, day), []).append(
                        LeaveCover(
                            sessions=sessions,
                            paid=True if leave is None else leave.paid,
                            symbol="P" if leave is None else leave.symbol,
                            type_name="Nghỉ" if leave is None else leave.name,
                        )
                    )
                    day += timedelta(days=1)
            elif kind is AbsenceKind.EXPLAIN:
                declared[(request.user_id, request.start_on)] = (
                    request.check_in,
                    request.check_out,
                )
            else:
                target = late if kind is AbsenceKind.LATE else early
                key = (request.user_id, request.start_on)
                target[key] = target.get(key, 0) + (request.minutes or 0)
        seats = await load_seats(self._session, ids)
        sheets: list[PersonSheet] = []
        for user in users:
            shift = book.for_user(user.id)
            row = book.row_for(user.id)
            totals = MonthTotals()
            results: list[DayResult] = []
            for day in days:
                punch = punches.get((user.id, day))
                check_in = None if punch is None else punch.check_in
                check_out = None if punch is None else punch.check_out
                explained = declared.get((user.id, day))
                if explained is not None:
                    check_in = explained[0] or check_in
                    check_out = explained[1] or check_out
                result = evaluate_day(
                    day=day,
                    shift=shift,
                    holiday=day in holidays,
                    check_in=check_in,
                    check_out=check_out,
                    explained=explained is not None,
                    leaves=leaves.get((user.id, day), ()),
                    late_allowance=late.get((user.id, day), 0),
                    early_allowance=early.get((user.id, day), 0),
                    future=day > today,
                    edited=punch is not None and punch.edited_by_user_id is not None,
                    note=None if punch is None else punch.note,
                )
                results.append(result)
                totals.add(result)
                for cover in leaves.get((user.id, day), ()):
                    share = result.paid_leave + result.unpaid_leave
                    if share:
                        totals.leave_by_type[cover.type_name] = (
                            totals.leave_by_type.get(cover.type_name, 0) + share
                        )
                        break
            sheets.append(
                PersonSheet(
                    user=user,
                    departments=[seat.name for seat in seats.get(user.id, ())],
                    shift_name=row.name if row is not None else shift.name,
                    days=results,
                    totals=totals,
                )
            )
        return sheets

    async def month(
        self,
        *,
        access: HrAccess,
        month: date,
        today: date,
        department_id: uuid.UUID | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        days = month_days(month)
        users = await self._people(access, days[0], days[-1], department_id, q, None)
        sheets = await self.compute(users, month, today)
        holidays = await holidays_between(self._session, days[0], days[-1])
        return {
            "month": month_key(month),
            "locked": month_key(month) in await locked_months(self._session),
            "days": [
                {
                    "day": day.isoformat(),
                    "weekday": WEEKDAY_SHORT[day.weekday()],
                    "holiday": holidays.get(day),
                }
                for day in days
            ],
            "people": [
                {
                    "user_id": str(sheet.user.id),
                    "name": sheet.user.full_name,
                    "member_code": sheet.user.member_code,
                    "departments": sheet.departments,
                    "shift": sheet.shift_name,
                    "totals": sheet.totals.as_dict(),
                    "days": [_day_view(result, full=False) for result in sheet.days],
                }
                for sheet in sheets
            ],
            "symbols": [{"symbol": key, "label": label} for key, label in SYMBOL_LABELS.items()],
            "can_manage": access.timesheet,
        }

    async def person(
        self, *, access: HrAccess, user_id: uuid.UUID, month: date, today: date
    ) -> dict[str, Any]:
        days = month_days(month)
        users = await self._people(access, days[0], days[-1], None, None, user_id)
        sheet = (await self.compute(users, month, today))[0]
        return {
            "month": month_key(month),
            "locked": month_key(month) in await locked_months(self._session),
            "user_id": str(sheet.user.id),
            "name": sheet.user.full_name,
            "member_code": sheet.user.member_code,
            "departments": sheet.departments,
            "shift": sheet.shift_name,
            "totals": sheet.totals.as_dict()
            | {"leave_by_type": {k: float(v) for k, v in sheet.totals.leave_by_type.items()}},
            "days": [_day_view(result, full=True) for result in sheet.days],
            "can_manage": access.timesheet,
        }

    async def export(
        self,
        *,
        access: HrAccess,
        month: date,
        today: date,
        department_id: uuid.UUID | None = None,
    ) -> bytes:
        days = month_days(month)
        users = await self._people(access, days[0], days[-1], department_id, None, None)
        sheets = await self.compute(users, month, today)

        def build() -> bytes:
            # Plain data in, bytes out: runs in a worker thread so a big
            # export does not hold up everybody else's requests.
            book = Workbook()
            grid = book.active
            assert grid is not None
            grid.title = "Bảng công"
            totals_head = [
                "Công chuẩn",
                "Đi làm",
                "Nghỉ có lương",
                "Nghỉ không lương",
                "Lễ",
                "Vắng",
                "Công tính lương",
                "Muộn (lần)",
                "Muộn (phút)",
                "Sớm (lần)",
                "Sớm (phút)",
                "Thiếu chấm",
            ]
            grid.append([f"BẢNG CHẤM CÔNG THÁNG {month.month:02d}/{month.year}"])
            grid.append(
                ["Mã NV", "Họ tên", "Phòng ban", "Ca"]
                + [f"{day.day}\n{WEEKDAY_SHORT[day.weekday()]}" for day in days]
                + totals_head
            )
            bold = Font(bold=True)
            for cell in grid[2]:
                cell.font = bold
                cell.alignment = Alignment(wrap_text=True, horizontal="center")
            grid["A1"].font = Font(bold=True, size=13)
            weekend = PatternFill("solid", fgColor="EEF2F8")
            for sheet in sheets:
                t = sheet.totals
                grid.append(
                    [
                        sheet.user.member_code,
                        sheet.user.full_name,
                        ", ".join(sheet.departments),
                        sheet.shift_name,
                    ]
                    + [result.symbol for result in sheet.days]
                    + [
                        float(t.standard),
                        float(t.worked),
                        float(t.paid_leave),
                        float(t.unpaid_leave),
                        float(t.holiday),
                        float(t.absent),
                        float(t.payable),
                        t.late_count,
                        t.late_minutes,
                        t.early_count,
                        t.early_minutes,
                        t.missing,
                    ]
                )
            for index, day in enumerate(days):
                if day.weekday() == 6:
                    for row in grid.iter_rows(min_row=2, min_col=5 + index, max_col=5 + index):
                        for cell in row:
                            cell.fill = weekend
            grid.freeze_panes = "E3"
            grid.column_dimensions["B"].width = 26
            grid.column_dimensions["C"].width = 22
            detail = book.create_sheet("Chi tiết")
            detail.append(
                [
                    "Mã NV",
                    "Họ tên",
                    "Ngày",
                    "Thứ",
                    "Vào",
                    "Ra",
                    "Ký hiệu",
                    "Công",
                    "Nghỉ",
                    "Muộn (phút)",
                    "Sớm (phút)",
                    "Thiếu chấm",
                    "Ghi chú",
                ]
            )
            for cell in detail[1]:
                cell.font = bold
            for sheet in sheets:
                for result in sheet.days:
                    detail.append(
                        [
                            sheet.user.member_code,
                            sheet.user.full_name,
                            result.day.strftime("%d/%m/%Y"),
                            WEEKDAY_SHORT[result.day.weekday()],
                            clock_text(result.check_in),
                            clock_text(result.check_out),
                            result.symbol,
                            float(result.worked),
                            ", ".join(result.leave_names),
                            result.late_counted or None,
                            result.early_counted or None,
                            "Có" if result.missing else None,
                            "; ".join(
                                item
                                for item in (
                                    "Giải trình đã duyệt" if result.explained else None,
                                    result.note,
                                )
                                if item
                            )
                            or None,
                        ]
                    )
            legend = book.create_sheet("Ký hiệu")
            legend.append(["Ký hiệu", "Ý nghĩa"])
            for key, label in SYMBOL_LABELS.items():
                legend.append([key, label])
            buffer = io.BytesIO()
            book.save(buffer)
            return buffer.getvalue()

        return await asyncio.to_thread(build)

    async def _record(
        self, actor: Actor, request_id: uuid.UUID, action: AuditAction, after: dict[str, Any]
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_timesheet",
            entity_id=None,
            after_data=after,
        )


__all__ = ["MANUAL_SOURCE", "HrTimesheetService", "PersonSheet"]
