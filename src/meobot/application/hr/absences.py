"""Đơn từ (0100): xin nghỉ, đi muộn, về sớm - their approval chain, and phép năm.

The chain (user 10/11/2026): a leave goes **Trưởng phòng → Giám đốc khối →
Giám đốc → HCNS**; a late / early one to the first of the three management
levels that has somebody, then HCNS. Who sits on each level is fixed when the
request is sent (:meth:`HrAbsenceService.chain_for`):

* **Trưởng phòng** - for a member: the head they report to
  (``manager_user_id``), else every head of the department; for a head: the
  heads of the parent department;
* **Giám đốc khối** - the heads of the block's office ("Văn phòng khối": the
  active department of the same ``block`` whose code starts with ``VP``);
* **Giám đốc** - the heads of the Ban Giám đốc (function ``BOARD``);
* **HCNS** - whoever holds ``HR`` / ``TIMESHEET`` ALL (the Phòng HCNS).

A level is skipped when nobody but the requester is on it, or when somebody
who already decided the request sits on it. Any one approver of a step
decides it; OWNER / ADMIN may stand in on any step, HCNS on its own step.
A refusal needs a reason and ends the request. The requester cancels while it
is open; HCNS (and OWNER / ADMIN) may also cancel an approved one, with a
reason. A month whose timesheet is locked takes no request.

Phép năm = :func:`meobot.domain.hr.attendance.annual_balance` with the days of
approved requests of an ``annual`` leave type and HCNS's adjustments.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from meobot.application.audit_service import AuditService
from meobot.application.hr.absence_notifications import AbsenceNotifier, Notice
from meobot.application.hr.access import HrAccess, HrForbiddenError, HrNotFoundError
from meobot.application.hr.files import sniff
from meobot.application.hr.profiles import HrValidationError
from meobot.application.hr.shifts import ShiftBook, holidays_between, load_shift_book, require_open
from meobot.application.org.access import load_access
from meobot.application.org.seats import load_seats
from meobot.core.config import Settings
from meobot.core.errors import ConflictError
from meobot.core.time import utcnow
from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.db.models.hr_attendance import (
    HrAbsenceFile,
    HrAbsenceRequest,
    HrAbsenceStep,
    HrLeaveAdjustment,
    HrLeaveType,
)
from meobot.db.models.hr_profile import HrProfile
from meobot.db.models.user import User
from meobot.db.models.user_permission import UserPermission
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.hr.attendance import (
    KIND_LABELS,
    LEVEL_LABELS,
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_FILES,
    PART_LABELS,
    STATUS_LABELS,
    STEP_STATUS_LABELS,
    AbsenceKind,
    AbsenceStatus,
    AnnualBalance,
    DayPart,
    StepLevel,
    StepStatus,
    annual_balance,
    leave_days,
    level_skipped,
    minutes_between,
    plan_levels,
)
from meobot.domain.hr.profiles import MODULE_HR, HrPermission
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.org.models import DepartmentFunction, Position

#: The longest leave one request may cover (calendar days).
MAX_SPAN_DAYS = 62
OPEN_STEPS = (StepStatus.WAITING.value, StepStatus.PENDING.value)


class AbsenceMovedError(ConflictError):
    code = "hr_absence_moved"

    def __init__(self) -> None:
        super().__init__(
            "Đơn này đã được người khác xử lý. Tải lại để xem.", details={"reason": self.code}
        )


@dataclass(frozen=True, slots=True)
class Person:
    id: uuid.UUID
    name: str
    member_code: str | None


def _fmt_day(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _days_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = f"{value.normalize():f}".replace(".", ",")
    return text


_VN = ZoneInfo("Asia/Ho_Chi_Minh")
#: How each step reads in the Telegram copy's list of steps.
STEP_MARKS: dict[StepStatus, str] = {
    StepStatus.APPROVED: "✅",
    StepStatus.REJECTED: "❌",
    StepStatus.PENDING: "⏳",
    StepStatus.WAITING: "▫️",
    StepStatus.SKIPPED: "↷",
    StepStatus.CLOSED: "⏹",
}


def _names(
    ids: Iterable[uuid.UUID | str], people: Mapping[uuid.UUID, Person], most: int = 3
) -> str:
    """ "A, B, C và 2 người khác" - who sits on a step."""
    names = [
        people[key].name if (key := uuid.UUID(str(item))) in people else "Không tên"
        for item in dict.fromkeys(ids)
    ]
    if not names:
        return "chưa có ai"
    shown = ", ".join(names[:most])
    return f"{shown} và {len(names) - most} người khác" if len(names) > most else shown


def _chain_text(
    row: HrAbsenceRequest, steps: Sequence[HrAbsenceStep], people: Mapping[uuid.UUID, Person]
) -> str:
    """Every step of the chain, one line each, with who did what and when."""
    lines = [f"Trạng thái: {STATUS_LABELS[AbsenceStatus(row.status)]}", "Các bước duyệt:"]
    for step in steps:
        status = StepStatus(step.status)
        label = LEVEL_LABELS[StepLevel(step.level)]
        if status in (StepStatus.APPROVED, StepStatus.REJECTED):
            who = people.get(step.decided_by_user_id) if step.decided_by_user_id else None
            at = step.decided_at.astimezone(_VN).strftime("%d/%m %H:%M") if step.decided_at else ""
            verb = "duyệt" if status is StepStatus.APPROVED else "không duyệt"
            line = f"{label}: {who.name if who else 'Không rõ'} {verb} {at}".rstrip()
            if step.note:
                line += f" - {step.note}"
        elif status is StepStatus.PENDING:
            line = f"{label}: đang chờ {_names(step.approver_ids, people)}"
        elif status is StepStatus.WAITING:
            line = f"{label}: {_names(step.approver_ids, people)} (chưa tới lượt)"
        else:
            line = f"{label}: {STEP_STATUS_LABELS[status].lower()}"
        lines.append(f"{STEP_MARKS[status]} {line}")
    return "\n".join(lines)


class HrAbsenceService:
    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit

    # --- who decides --------------------------------------------------------------------

    async def _active_heads(self) -> dict[uuid.UUID, list[uuid.UUID]]:
        rows = (
            await self._session.execute(
                select(DepartmentMember.department_id, DepartmentMember.user_id)
                .join(User, User.id == DepartmentMember.user_id)
                .where(
                    DepartmentMember.position == Position.HEAD,
                    DepartmentMember.left_at.is_(None),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
                .order_by(DepartmentMember.joined_at)
            )
        ).all()
        heads: dict[uuid.UUID, list[uuid.UUID]] = {}
        for department_id, user_id in rows:
            heads.setdefault(department_id, []).append(user_id)
        return heads

    async def timesheet_holders(self) -> list[uuid.UUID]:
        """Whoever holds ``HR`` / ``TIMESHEET`` ALL through a seat or a
        personal grant (never named for being OWNER / ADMIN only)."""
        cells = (
            await self._session.execute(
                select(DepartmentPermission.department_id, DepartmentPermission.position).where(
                    DepartmentPermission.module == MODULE_HR,
                    DepartmentPermission.permission == HrPermission.TIMESHEET.value,
                    DepartmentPermission.scope == "ALL",
                )
            )
        ).all()
        candidates: set[uuid.UUID] = set()
        for department_id, position in cells:
            candidates |= set(
                (
                    await self._session.scalars(
                        select(DepartmentMember.user_id)
                        .join(Department, Department.id == DepartmentMember.department_id)
                        .where(
                            DepartmentMember.department_id == department_id,
                            DepartmentMember.position == position,
                            DepartmentMember.left_at.is_(None),
                            Department.active.is_(True),
                        )
                    )
                ).all()
            )
        candidates |= set(
            (
                await self._session.scalars(
                    select(UserPermission.user_id).where(
                        UserPermission.module == MODULE_HR,
                        UserPermission.permission == HrPermission.TIMESHEET.value,
                        UserPermission.scope == "ALL",
                        UserPermission.revoked_at.is_(None),
                    )
                )
            ).all()
        )
        if not candidates:
            return []
        users = (
            await self._session.scalars(
                select(User).where(
                    User.id.in_(candidates),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        holders: list[uuid.UUID] = []
        for user in sorted(users, key=lambda item: (item.full_name or "", str(item.id))):
            access = await load_access(self._session, user_id=user.id, role=user.role)
            if access.scope(MODULE_HR, HrPermission.TIMESHEET.value) == "ALL":
                holders.append(user.id)
        return holders

    async def chain_for(self, user_id: uuid.UUID) -> dict[StepLevel, list[uuid.UUID]]:
        """Who sits on each level for ``user_id`` (the requester included,
        dropped later)."""
        departments = {
            row.id: row
            for row in (
                await self._session.scalars(select(Department).where(Department.active.is_(True)))
            ).all()
        }
        heads = await self._active_heads()
        seats = (await load_seats(self._session, [user_id])).get(user_id, ())
        manager: list[uuid.UUID] = []
        block: list[uuid.UUID] = []
        for seat in seats:
            department = departments.get(seat.department_id)
            if department is None:
                continue
            if seat.position is Position.HEAD:
                if department.parent_id is not None:
                    manager += heads.get(department.parent_id, [])
            else:
                own = heads.get(department.id, [])
                if seat.manager_user_id is not None and seat.manager_user_id in own:
                    manager.append(seat.manager_user_id)
                else:
                    manager += own
            name = department.block
            parent = departments.get(department.parent_id) if department.parent_id else None
            if not name and parent is not None:
                name = parent.block
            if name:
                for office in departments.values():
                    if office.block == name and office.code.upper().startswith("VP"):
                        block += heads.get(office.id, [])
        director = [
            user
            for department in departments.values()
            if department.function is DepartmentFunction.BOARD
            for user in heads.get(department.id, [])
        ]
        return {
            StepLevel.MANAGER: list(dict.fromkeys(manager)),
            StepLevel.BLOCK: list(dict.fromkeys(block)),
            StepLevel.DIRECTOR: list(dict.fromkeys(director)),
            StepLevel.HR: await self.timesheet_holders(),
        }

    # --- reads ---------------------------------------------------------------------------

    async def leave_types(self, *, include_inactive: bool = False) -> list[HrLeaveType]:
        statement = select(HrLeaveType).order_by(HrLeaveType.sort_order, HrLeaveType.name)
        if not include_inactive:
            statement = statement.where(HrLeaveType.active.is_(True))
        return list((await self._session.scalars(statement)).all())

    async def _people(self, ids: Iterable[uuid.UUID | str | None]) -> dict[uuid.UUID, Person]:
        wanted = {uuid.UUID(str(item)) for item in ids if item}
        if not wanted:
            return {}
        rows = (await self._session.scalars(select(User).where(User.id.in_(wanted)))).all()
        return {
            row.id: Person(row.id, row.full_name or "Không tên", row.member_code) for row in rows
        }

    async def form(self, *, actor: Actor, today: date) -> dict[str, Any]:
        """What the "Tạo đơn" form needs: types, the chain, the balance."""
        assert actor.user_id is not None
        chain = await self.chain_for(actor.user_id)
        people = await self._people(user for users in chain.values() for user in users)
        book = await load_shift_book(self._session, [actor.user_id])
        row = book.row_for(actor.user_id)
        shift = book.for_user(actor.user_id)

        def names(level: StepLevel) -> list[dict[str, Any]]:
            return [
                {"id": str(user), "name": people[user].name}
                for user in chain[level]
                if user != actor.user_id and user in people
            ]

        return {
            "leave_types": [_type_view(item) for item in await self.leave_types()],
            "chain": {
                kind.value: [
                    {"level": level.value, "label": LEVEL_LABELS[level], "people": names(level)}
                    for level in plan_levels(kind, chain, actor.user_id)
                ]
                for kind in AbsenceKind
            },
            "balance": (await self.balance(actor.user_id, today.year, today)).as_dict(),
            "shift": {
                "name": row.name if row is not None else shift.name,
                "grace_minutes": shift.grace_minutes,
                "week": {
                    str(weekday): {
                        key: [session.start.strftime("%H:%M"), session.end.strftime("%H:%M")]
                        for key, session in sessions.items()
                    }
                    for weekday, sessions in shift.week.items()
                },
            },
        }

    # --- submit ----------------------------------------------------------------------------

    async def submit(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        kind: AbsenceKind,
        leave_type: str | None,
        start_on: date,
        end_on: date | None,
        part: DayPart,
        at_time: time | None,
        reason: str,
        today: date,
        check_in: time | None = None,
        check_out: time | None = None,
        file_ids: Sequence[uuid.UUID] = (),
    ) -> HrAbsenceRequest:
        assert actor.user_id is not None
        user_id = actor.user_id
        text = (reason or "").strip()
        if not text:
            raise HrValidationError("hr_absence_invalid", "Nhập lý do.", field="reason")
        if len(text) > 1000:
            raise HrValidationError("hr_absence_invalid", "Lý do dài quá.", field="reason")
        book = await load_shift_book(self._session, [user_id])
        shift = book.for_user(user_id)
        type_row: HrLeaveType | None = None
        minutes: int | None = None
        days: Decimal | None = None
        files: list[HrAbsenceFile] = []
        if kind is AbsenceKind.LEAVE:
            type_row = await self._session.scalar(
                select(HrLeaveType).where(
                    HrLeaveType.code == (leave_type or ""), HrLeaveType.active.is_(True)
                )
            )
            if type_row is None:
                raise HrValidationError("hr_absence_invalid", "Chọn loại nghỉ.", field="leave_type")
            end = end_on or start_on
            if end < start_on:
                raise HrValidationError(
                    "hr_absence_invalid", "Ngày kết thúc trước ngày bắt đầu.", field="end_on"
                )
            if (end - start_on).days >= MAX_SPAN_DAYS:
                raise HrValidationError(
                    "hr_absence_invalid",
                    f"Một đơn nghỉ tối đa {MAX_SPAN_DAYS} ngày; tách thành nhiều đơn.",
                    field="end_on",
                )
            if end != start_on:
                part = DayPart.FULL
            holidays = await holidays_between(self._session, start_on, end)
            days = leave_days(start_on, end, part, lambda _day: shift, holidays)
            if days <= 0:
                raise HrValidationError(
                    "hr_absence_no_workday",
                    "Khoảng ngày này không có ngày làm việc nào theo ca của bạn.",
                    field="start_on",
                )
            end_on = end
            at_time = None
            await self._refuse_overlap(user_id, kind, start_on, end, part)
        elif kind is AbsenceKind.EXPLAIN:
            end_on = start_on
            part = DayPart.FULL
            at_time = None
            if start_on > today:
                raise HrValidationError(
                    "hr_absence_time_invalid",
                    "Chỉ giải trình được ngày đã qua hoặc hôm nay.",
                    field="start_on",
                )
            if not shift.sessions(start_on):
                raise HrValidationError(
                    "hr_absence_no_shift", "Ngày này bạn không có ca làm việc.", field="start_on"
                )
            if check_in is None and check_out is None:
                raise HrValidationError(
                    "hr_absence_invalid",
                    "Nhập giờ vào hoặc giờ ra thực tế bạn quên chấm.",
                    field="check_in",
                )
            if check_in is not None and check_out is not None and check_out <= check_in:
                raise HrValidationError(
                    "hr_absence_time_invalid", "Giờ ra phải sau giờ vào.", field="check_out"
                )
            files = await self._own_waiting_files(user_id, file_ids)
            await self._refuse_overlap(user_id, kind, start_on, start_on, part)
        else:
            end_on = start_on
            part = DayPart.FULL
            sessions = shift.sessions(start_on)
            if not sessions:
                raise HrValidationError(
                    "hr_absence_no_shift", "Ngày này bạn không có ca làm việc.", field="start_on"
                )
            if at_time is None:
                raise HrValidationError(
                    "hr_absence_invalid",
                    "Nhập giờ đến." if kind is AbsenceKind.LATE else "Nhập giờ về.",
                    field="at_time",
                )
            ordered = sorted(sessions.values(), key=lambda item: item.start)
            if kind is AbsenceKind.LATE:
                minutes = minutes_between(ordered[0].start, at_time)
                if minutes <= 0:
                    raise HrValidationError(
                        "hr_absence_time_invalid",
                        f"Giờ đến phải sau giờ vào ca ({ordered[0].start:%H:%M}).",
                        field="at_time",
                    )
            else:
                minutes = minutes_between(at_time, ordered[-1].end)
                if minutes <= 0:
                    raise HrValidationError(
                        "hr_absence_time_invalid",
                        f"Giờ về phải trước giờ hết ca ({ordered[-1].end:%H:%M}).",
                        field="at_time",
                    )
            await self._refuse_overlap(user_id, kind, start_on, start_on, part)
        await require_open(self._session, [start_on, end_on])

        row = HrAbsenceRequest(
            user_id=user_id,
            kind=kind.value,
            leave_type_code=None if type_row is None else type_row.code,
            start_on=start_on,
            end_on=end_on,
            part=part.value,
            at_time=at_time,
            minutes=minutes,
            days=days,
            reason=text,
            status=AbsenceStatus.PENDING.value,
            check_in=check_in if kind is AbsenceKind.EXPLAIN else None,
            check_out=check_out if kind is AbsenceKind.EXPLAIN else None,
        )
        self._session.add(row)
        await self._session.flush()
        if kind is AbsenceKind.EXPLAIN:
            for item in files:
                item.request_id = row.id
            await self._session.flush()
        chain = await self.chain_for(user_id)
        for position, level in enumerate(plan_levels(kind, chain, user_id)):
            self._session.add(
                HrAbsenceStep(
                    request_id=row.id,
                    position=position,
                    level=level.value,
                    approver_ids=[str(user) for user in chain[level] if user != user_id],
                    status=StepStatus.WAITING.value,
                )
            )
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_ABSENCE_SUBMITTED,
            row,
            {
                "kind": row.kind,
                "leave_type": row.leave_type_code,
                "days": _num(row.days),
                "files": len(file_ids) if kind is AbsenceKind.EXPLAIN else 0,
            },
        )
        await self._advance(row)
        await self._announce(row, change="submit", actor=actor)
        return row

    async def _refuse_overlap(
        self, user_id: uuid.UUID, kind: AbsenceKind, start: date, end: date, part: DayPart
    ) -> None:
        rows = (
            await self._session.scalars(
                select(HrAbsenceRequest).where(
                    HrAbsenceRequest.user_id == user_id,
                    HrAbsenceRequest.kind == kind.value,
                    HrAbsenceRequest.status.in_(
                        (AbsenceStatus.PENDING.value, AbsenceStatus.APPROVED.value)
                    ),
                    HrAbsenceRequest.start_on <= end,
                    HrAbsenceRequest.end_on >= start,
                )
            )
        ).all()
        for other in rows:
            if (
                kind is AbsenceKind.LEAVE
                and start == end
                and other.start_on == other.end_on
                and part is not DayPart.FULL
                and other.part != DayPart.FULL.value
                and other.part != part.value
            ):
                continue
            raise HrValidationError(
                "hr_absence_overlap",
                f"Bạn đã có đơn {KIND_LABELS[kind].lower()} trùng ngày "
                f"{_fmt_day(other.start_on)} "
                f"({STATUS_LABELS[AbsenceStatus(other.status)].lower()}).",
                field="start_on",
            )

    # --- the chain moves --------------------------------------------------------------------

    async def _steps(self, request_id: uuid.UUID) -> list[HrAbsenceStep]:
        return list(
            (
                await self._session.scalars(
                    select(HrAbsenceStep)
                    .where(HrAbsenceStep.request_id == request_id)
                    .order_by(HrAbsenceStep.position)
                )
            ).all()
        )

    async def _advance(self, row: HrAbsenceRequest) -> HrAbsenceStep | None:
        """Open the next step that is not skipped (returned), else approve the
        request (``None``). Telling people is the caller's :meth:`_announce`."""
        steps = await self._steps(row.id)
        deciders = [
            step.decided_by_user_id
            for step in steps
            if step.status == StepStatus.APPROVED.value and step.decided_by_user_id
        ]
        for step in steps:
            if step.status != StepStatus.WAITING.value:
                continue
            approvers = [uuid.UUID(item) for item in step.approver_ids]
            if level_skipped(approvers, row.user_id, deciders):
                step.status = StepStatus.SKIPPED.value
                continue
            step.status = StepStatus.PENDING.value
            await self._session.flush()
            return step
        row.status = AbsenceStatus.APPROVED.value
        row.decided_at = utcnow()
        await self._session.flush()
        return None

    def _may_decide(self, step: HrAbsenceStep, actor: Actor, access: HrAccess) -> bool:
        if actor.user_id is None:
            return False
        if str(actor.user_id) in step.approver_ids:
            return True
        if actor.role in (Role.OWNER, Role.ADMIN):
            return True
        return step.level == StepLevel.HR.value and access.timesheet

    async def decide(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        absence_id: uuid.UUID,
        step_id: uuid.UUID | None,
        approve: bool,
        note: str | None,
    ) -> HrAbsenceRequest:
        row = await self._visible(absence_id, actor, access)
        if row.user_id == actor.user_id:
            raise HrForbiddenError("Bạn không tự duyệt đơn của mình được.")
        if row.status != AbsenceStatus.PENDING.value:
            raise AbsenceMovedError()
        steps = await self._steps(row.id)
        step = next((item for item in steps if item.status == StepStatus.PENDING.value), None)
        if step is None or (step_id is not None and step.id != step_id):
            raise AbsenceMovedError()
        if not self._may_decide(step, actor, access):
            raise HrForbiddenError("Bước này không chờ bạn duyệt.")
        text = (note or "").strip() or None
        if not approve and not text:
            raise HrValidationError(
                "hr_absence_note_required", "Nhập lý do không duyệt.", field="note"
            )
        if text and len(text) > 1000:
            raise HrValidationError("hr_absence_invalid", "Ghi chú dài quá.", field="note")
        step.status = (StepStatus.APPROVED if approve else StepStatus.REJECTED).value
        step.decided_by_user_id = actor.user_id
        step.decided_at = utcnow()
        step.note = text
        await self._record(
            actor,
            request_id,
            AuditAction.HR_ABSENCE_DECIDED,
            row,
            {"level": step.level, "approve": approve},
        )
        if approve:
            await self._session.flush()
            opened = await self._advance(row)
            await self._announce(
                row, change="step" if opened else "approved", actor=actor, decided=step
            )
        else:
            for other in steps:
                if other.status in OPEN_STEPS:
                    other.status = StepStatus.CLOSED.value
            row.status = AbsenceStatus.REJECTED.value
            row.decided_at = utcnow()
            await self._session.flush()
            await self._announce(row, change="rejected", actor=actor, decided=step, note=text)
        return row

    async def cancel(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        absence_id: uuid.UUID,
        note: str | None,
    ) -> HrAbsenceRequest:
        row = await self._visible(absence_id, actor, access)
        text = (note or "").strip() or None
        own = row.user_id == actor.user_id
        manager = access.timesheet or actor.role in (Role.OWNER, Role.ADMIN)
        if row.status == AbsenceStatus.PENDING.value and (own or manager):
            pass
        elif row.status == AbsenceStatus.APPROVED.value and manager:
            if not text:
                raise HrValidationError(
                    "hr_absence_note_required", "Nhập lý do huỷ đơn đã duyệt.", field="note"
                )
        elif row.status in (AbsenceStatus.PENDING.value, AbsenceStatus.APPROVED.value):
            raise HrForbiddenError(
                "Chỉ người gửi huỷ được đơn đang duyệt; đơn đã duyệt do HCNS huỷ."
            )
        else:
            raise AbsenceMovedError()
        if not own and not text:
            raise HrValidationError("hr_absence_note_required", "Nhập lý do huỷ.", field="note")
        await require_open(self._session, [row.start_on, row.end_on])
        waiting: list[uuid.UUID] = []
        for step in await self._steps(row.id):
            if step.status == StepStatus.PENDING.value:
                waiting = [uuid.UUID(item) for item in step.approver_ids]
            if step.status in OPEN_STEPS:
                step.status = StepStatus.CLOSED.value
        row.status = AbsenceStatus.CANCELLED.value
        row.cancelled_by_user_id = actor.user_id
        row.cancel_note = text
        row.decided_at = utcnow()
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.HR_ABSENCE_CANCELLED, row, {"own": own})
        await self._announce(row, change="cancel", actor=actor, waiting=waiting, note=text)
        return row

    # --- notifications ------------------------------------------------------------------

    async def _describe(self, row: HrAbsenceRequest) -> tuple[str, str]:
        person = (await self._people([row.user_id])).get(row.user_id)
        name = person.name if person else "Một nhân viên"
        kind = AbsenceKind(row.kind)
        if kind is AbsenceKind.LEAVE:
            leave = await self._session.scalar(
                select(HrLeaveType.name).where(HrLeaveType.code == row.leave_type_code)
            )
            when = (
                _fmt_day(row.start_on)
                if row.start_on == row.end_on
                else f"{_fmt_day(row.start_on)} - {_fmt_day(row.end_on)}"
            )
            part = (
                ""
                if row.part == DayPart.FULL.value
                else f" ({PART_LABELS[DayPart(row.part)].lower()})"
            )
            what = f"{leave or 'Nghỉ'} {when}{part}, {_days_text(row.days)} ngày"
        elif kind is AbsenceKind.EXPLAIN:
            what = f"{KIND_LABELS[kind]} {_fmt_day(row.start_on)}: {_declared(row)}"
        else:
            verb = "đến lúc" if kind is AbsenceKind.LATE else "về lúc"
            at = row.at_time.strftime("%H:%M") if row.at_time else ""
            what = f"{KIND_LABELS[kind]} {_fmt_day(row.start_on)}, {verb} {at} ({row.minutes} phút)"
        return name, what

    async def _announce(
        self,
        row: HrAbsenceRequest,
        *,
        change: str,
        actor: Actor,
        decided: HrAbsenceStep | None = None,
        waiting: Sequence[uuid.UUID] = (),
        note: str | None = None,
    ) -> None:
        """Tell everybody a change concerns (user 12/11/2026), one message each.

        ``change``: ``submit`` (sent; the first open step, else approved at
        once), ``step`` (``decided`` approved, another step opened),
        ``approved`` (``decided`` was the last step), ``rejected``
        (``decided`` refused it), ``cancel`` (``waiting`` = who the open step
        waited on). Nobody hears about their own action, except the requester
        getting the receipt of what they sent. HCNS (the ``TIMESHEET``
        holders) hears when a request is sent and when it ends - not each step.
        """
        steps = await self._steps(row.id)
        pending = next((step for step in steps if step.status == StepStatus.PENDING.value), None)
        awaited = [uuid.UUID(item) for item in pending.approver_ids] if pending else []
        hr = (
            await self.timesheet_holders()
            if change in ("submit", "approved", "rejected", "cancel")
            else []
        )
        earlier = [
            step.decided_by_user_id
            for step in steps
            if step.status == StepStatus.APPROVED.value
            and step.decided_by_user_id
            and (decided is None or step.id != decided.id)
        ]
        peers = [uuid.UUID(item) for item in decided.approver_ids] if decided else []
        people = await self._people(
            [row.user_id, actor.user_id, *awaited, *earlier, *peers, *waiting, *hr]
            + [item for step in steps for item in step.approver_ids]
            + [step.decided_by_user_id for step in steps]
        )
        name, what = await self._describe(row)
        what = what[:1].lower() + what[1:]
        doer = people.get(actor.user_id) if actor.user_id else None
        by = doer.name if doer else "Hệ thống"
        level = f" ({LEVEL_LABELS[StepLevel(decided.level)]})" if decided else ""
        next_up = (
            f"Đang chờ {_names(awaited, people)} ({LEVEL_LABELS[StepLevel(pending.level)]}) duyệt."
            if pending
            else ""
        )
        reason = f" Lý do: {note}" if note else ""
        mine = f"Đơn {what}"
        theirs = f"Đơn {what} của {name}"
        notices: list[Notice] = []

        def tell(
            people_ids: Iterable[uuid.UUID], event: NotificationEvent, heading: str, body: str
        ) -> None:
            notices.extend(Notice(item, event, heading, body) for item in people_ids)

        requester = [row.user_id]
        event = NotificationEvent
        if change == "submit":
            tell(
                awaited,
                event.HR_ABSENCE_AWAITING,
                "Có đơn từ chờ bạn duyệt",
                f"{name} xin {what}. Đang chờ bạn duyệt "
                f"({LEVEL_LABELS[StepLevel(pending.level)] if pending else ''}).",
            )
            if pending:
                tell(
                    requester, event.HR_ABSENCE_SUBMITTED, "Đã gửi đơn", f"{mine} đã gửi. {next_up}"
                )
                tell(
                    hr,
                    event.HR_ABSENCE_SUBMITTED,
                    "Có đơn từ mới",
                    f"{name} gửi đơn {what}. {next_up}",
                )
            else:
                done = "không có cấp nào cần duyệt, đơn được duyệt ngay."
                tell(
                    requester,
                    event.HR_ABSENCE_APPROVED,
                    "Đơn của bạn đã được duyệt",
                    f"{mine}: {done}",
                )
                tell(hr, event.HR_ABSENCE_APPROVED, "Đơn từ đã duyệt xong", f"{theirs}: {done}")
        elif change == "step":
            tell(
                awaited,
                event.HR_ABSENCE_AWAITING,
                "Có đơn từ chờ bạn duyệt",
                f"{name} xin {what}. {by}{level} đã duyệt, giờ chờ bạn duyệt "
                f"({LEVEL_LABELS[StepLevel(pending.level)] if pending else ''}).",
            )
            tell(
                requester,
                event.HR_ABSENCE_STEP_APPROVED,
                "Đơn của bạn đã qua một bước duyệt",
                f"{by}{level} đã duyệt đơn {what}. {next_up}",
            )
            tell(
                peers,
                event.HR_ABSENCE_STEP_APPROVED,
                "Đơn từ đã có người duyệt",
                f"{by} đã duyệt bước{level} đơn {what} của {name}, "
                f"bạn không cần duyệt nữa. {next_up}",
            )
            tell(
                earlier,
                event.HR_ABSENCE_STEP_APPROVED,
                "Đơn từ bạn đã duyệt có cập nhật",
                f"{by}{level} đã duyệt đơn {what} của {name}. {next_up}",
            )
        elif change == "approved":
            tell(
                requester,
                event.HR_ABSENCE_APPROVED,
                "Đơn của bạn đã được duyệt",
                f"{mine} đã được duyệt xong ({by}{level} duyệt bước cuối).",
            )
            body = f"{theirs} đã được duyệt xong ({by}{level} duyệt bước cuối)."
            tell(
                peers,
                event.HR_ABSENCE_APPROVED,
                "Đơn từ đã duyệt xong",
                f"{body} Bạn không cần duyệt nữa.",
            )
            tell([*earlier, *hr], event.HR_ABSENCE_APPROVED, "Đơn từ đã duyệt xong", body)
        elif change == "rejected":
            tell(
                requester,
                event.HR_ABSENCE_REJECTED,
                "Đơn của bạn không được duyệt",
                f"{mine} không được duyệt: {by}{level} từ chối.{reason}",
            )
            tell(
                [*peers, *earlier, *hr],
                event.HR_ABSENCE_REJECTED,
                "Đơn từ không được duyệt",
                f"{by}{level} không duyệt đơn {what} của {name}.{reason}",
            )
        elif change == "cancel":
            own = actor.user_id == row.user_id
            tell(
                requester,
                event.HR_ABSENCE_CANCELLED,
                "Đơn của bạn đã bị huỷ",
                f"{by} đã huỷ đơn {what}.{reason}",
            )
            said = f"{name} đã rút đơn {what}" if own else f"{by} đã huỷ đơn {what} của {name}"
            tell(
                waiting,
                event.HR_ABSENCE_CANCELLED,
                "Đơn từ đã huỷ",
                f"{said}, bạn không cần duyệt nữa.{reason}",
            )
            tell([*earlier, *hr], event.HR_ABSENCE_CANCELLED, "Đơn từ đã huỷ", f"{said}.{reason}")
        receipt = change == "submit"
        notices = [
            notice
            for notice in notices
            if notice.recipient != actor.user_id or (receipt and notice.recipient == row.user_id)
        ]
        key = f"step{decided.position}" if change == "step" and decided else change
        await AbsenceNotifier(self._session, self._settings).send(
            notices,
            title=f"{name}: {what}",
            chain=_chain_text(row, steps, people),
            request_id=row.id,
            key=key,
            sender=actor.user_id,
        )

    # --- lists ----------------------------------------------------------------------------

    async def _visible(
        self, absence_id: uuid.UUID, actor: Actor, access: HrAccess
    ) -> HrAbsenceRequest:
        row = await self._session.get(HrAbsenceRequest, absence_id)
        if row is None or not await self._may_see(row, actor, access):
            raise HrNotFoundError("Không tìm thấy đơn.")
        return row

    async def _may_see(self, row: HrAbsenceRequest, actor: Actor, access: HrAccess) -> bool:
        if actor.role in (Role.OWNER, Role.ADMIN) or access.sees_attendance(row.user_id):
            return True
        if actor.user_id is None:
            return False
        steps = await self._steps(row.id)
        return any(str(actor.user_id) in step.approver_ids for step in steps)

    async def awaiting_count(self, actor: Actor, access: HrAccess) -> int:
        return len(await self._awaiting_ids(actor, access))

    async def _awaiting_ids(self, actor: Actor, access: HrAccess) -> list[uuid.UUID]:
        if actor.user_id is None:
            return []
        rows = (
            await self._session.execute(
                select(HrAbsenceStep.request_id, HrAbsenceStep.level, HrAbsenceStep.approver_ids)
                .join(HrAbsenceRequest, HrAbsenceRequest.id == HrAbsenceStep.request_id)
                .where(
                    HrAbsenceStep.status == StepStatus.PENDING.value,
                    HrAbsenceRequest.status == AbsenceStatus.PENDING.value,
                    HrAbsenceRequest.user_id != actor.user_id,
                )
            )
        ).all()
        me = str(actor.user_id)
        return [
            request
            for request, level, approvers in rows
            if me in (approvers or []) or (level == StepLevel.HR.value and access.timesheet)
        ]

    async def listing(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        tab: str,
        status: str | None = None,
        kind: str | None = None,
        month: date | None = None,
        user_id: uuid.UUID | None = None,
        q: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        statement = select(HrAbsenceRequest).order_by(HrAbsenceRequest.created_at.desc())
        if tab == "mine":
            statement = statement.where(HrAbsenceRequest.user_id == actor.user_id)
        elif tab == "awaiting":
            ids = await self._awaiting_ids(actor, access)
            if not ids:
                return []
            statement = statement.where(HrAbsenceRequest.id.in_(ids))
        elif not (access.timesheet or actor.role in (Role.OWNER, Role.ADMIN)):
            named = select(HrAbsenceStep.request_id).where(
                cast(HrAbsenceStep.approver_ids, Text).contains(str(actor.user_id))
            )
            people = set(access.team) | ({access.user_id} if access.user_id else set())
            statement = statement.where(
                or_(HrAbsenceRequest.user_id.in_(people), HrAbsenceRequest.id.in_(named))
            )
        if status:
            statement = statement.where(HrAbsenceRequest.status == status)
        if kind:
            statement = statement.where(HrAbsenceRequest.kind == kind)
        if month is not None:
            first = month.replace(day=1)
            following = (first + timedelta(days=32)).replace(day=1)
            statement = statement.where(
                HrAbsenceRequest.start_on < following, HrAbsenceRequest.end_on >= first
            )
        if user_id is not None:
            statement = statement.where(HrAbsenceRequest.user_id == user_id)
        if q:
            needle = f"%{q.strip()}%"
            statement = statement.join(User, User.id == HrAbsenceRequest.user_id).where(
                or_(User.full_name.ilike(needle), User.member_code.ilike(needle))
            )
        rows = list((await self._session.scalars(statement.limit(limit))).all())
        return await self.views(rows, actor=actor, access=access)

    async def views(
        self, rows: Sequence[HrAbsenceRequest], *, actor: Actor, access: HrAccess
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        steps_of: dict[uuid.UUID, list[HrAbsenceStep]] = {}
        for step in (
            await self._session.scalars(
                select(HrAbsenceStep)
                .where(HrAbsenceStep.request_id.in_([row.id for row in rows]))
                .order_by(HrAbsenceStep.position)
            )
        ).all():
            steps_of.setdefault(step.request_id, []).append(step)
        ids: set[Any] = {row.user_id for row in rows} | {row.cancelled_by_user_id for row in rows}
        for steps in steps_of.values():
            for step in steps:
                ids |= set(step.approver_ids) | {step.decided_by_user_id}
        people = await self._people(ids)
        types = {item.code: item for item in await self.leave_types(include_inactive=True)}
        seats = await load_seats(self._session, [row.user_id for row in rows])
        files_of: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for request, file_id, name, content_type, size in (
            await self._session.execute(
                select(
                    HrAbsenceFile.request_id,
                    HrAbsenceFile.id,
                    HrAbsenceFile.name,
                    HrAbsenceFile.content_type,
                    HrAbsenceFile.size_bytes,
                )
                .where(HrAbsenceFile.request_id.in_([row.id for row in rows]))
                .order_by(HrAbsenceFile.created_at)
            )
        ).all():
            files_of.setdefault(request, []).append(
                {"id": str(file_id), "name": name, "content_type": content_type, "size": size}
            )
        views = []
        for row in rows:
            view = self._view(
                row,
                steps_of.get(row.id, []),
                people,
                types,
                [seat.name for seat in seats.get(row.user_id, ())],
                actor,
                access,
            )
            view["files"] = files_of.get(row.id, [])
            views.append(view)
        return views

    def _view(
        self,
        row: HrAbsenceRequest,
        steps: list[HrAbsenceStep],
        people: dict[uuid.UUID, Person],
        types: dict[str, HrLeaveType],
        departments: list[str],
        actor: Actor,
        access: HrAccess,
    ) -> dict[str, Any]:
        def person(user_id: uuid.UUID | str | None) -> dict[str, Any] | None:
            if not user_id:
                return None
            found = people.get(uuid.UUID(str(user_id)))
            if found is None:
                return None
            return {"id": str(found.id), "name": found.name, "member_code": found.member_code}

        current = next((step for step in steps if step.status == StepStatus.PENDING.value), None)
        manager = access.timesheet or actor.role in (Role.OWNER, Role.ADMIN)
        own = row.user_id == actor.user_id
        leave = types.get(row.leave_type_code or "")
        status = AbsenceStatus(row.status)
        return {
            "id": str(row.id),
            "user": person(row.user_id),
            "departments": departments,
            "kind": row.kind,
            "kind_label": KIND_LABELS[AbsenceKind(row.kind)],
            "leave_type": None if leave is None else _type_view(leave),
            "start_on": row.start_on.isoformat(),
            "end_on": row.end_on.isoformat(),
            "part": row.part,
            "part_label": PART_LABELS[DayPart(row.part)],
            "at_time": None if row.at_time is None else row.at_time.strftime("%H:%M"),
            "minutes": row.minutes,
            "days": _num(row.days),
            "reason": row.reason,
            "status": row.status,
            "status_label": STATUS_LABELS[status],
            "created_at": row.created_at.isoformat(),
            "decided_at": None if row.decided_at is None else row.decided_at.isoformat(),
            "cancelled_by": person(row.cancelled_by_user_id),
            "cancel_note": row.cancel_note,
            "current_level": None if current is None else current.level,
            "current_label": None if current is None else LEVEL_LABELS[StepLevel(current.level)],
            "current_step_id": None if current is None else str(current.id),
            "waiting_on": []
            if current is None
            else [p for p in (person(item) for item in current.approver_ids) if p],
            "steps": [
                {
                    "id": str(step.id),
                    "position": step.position,
                    "level": step.level,
                    "level_label": LEVEL_LABELS[StepLevel(step.level)],
                    "status": step.status,
                    "status_label": STEP_STATUS_LABELS[StepStatus(step.status)],
                    "approvers": [p for p in (person(item) for item in step.approver_ids) if p],
                    "decided_by": person(step.decided_by_user_id),
                    "decided_at": None if step.decided_at is None else step.decided_at.isoformat(),
                    "note": step.note,
                }
                for step in steps
            ],
            "check_in": None if row.check_in is None else row.check_in.strftime("%H:%M"),
            "check_out": None if row.check_out is None else row.check_out.strftime("%H:%M"),
            "own": own,
            "can_decide": current is not None
            and not own
            and status is AbsenceStatus.PENDING
            and self._may_decide(current, actor, access),
            "can_cancel": (status is AbsenceStatus.PENDING and (own or manager))
            or (status is AbsenceStatus.APPROVED and manager),
        }

    async def detail(
        self, *, actor: Actor, access: HrAccess, absence_id: uuid.UUID, today: date
    ) -> dict[str, Any]:
        row = await self._visible(absence_id, actor, access)
        view = (await self.views([row], actor=actor, access=access))[0]
        leave = await self._session.scalar(
            select(HrLeaveType).where(HrLeaveType.code == row.leave_type_code)
        )
        if leave is not None and leave.annual:
            balance = await self.balance(row.user_id, row.start_on.year, today)
            view["balance"] = balance.as_dict()
        return view

    # --- evidence pictures ("Giải trình chấm công") ---------------------------------------

    async def upload_file(self, *, actor: Actor, name: str, data: bytes) -> HrAbsenceFile:
        """A picture waiting to be attached to the uploader's next request."""
        assert actor.user_id is not None
        if not data:
            raise HrValidationError("hr_absence_file_invalid", "Tệp rỗng.")
        if len(data) > MAX_EVIDENCE_BYTES:
            raise HrValidationError(
                "hr_absence_file_invalid",
                f"Ảnh lớn quá {MAX_EVIDENCE_BYTES // (1024 * 1024)} MB.",
            )
        content_type = sniff(data)
        if content_type is None or not content_type.startswith("image/"):
            raise HrValidationError("hr_absence_file_invalid", "Chỉ nhận ảnh JPEG, PNG hoặc WebP.")
        stale = utcnow() - timedelta(hours=24)
        for old in (
            await self._session.scalars(
                select(HrAbsenceFile).where(
                    HrAbsenceFile.uploaded_by_user_id == actor.user_id,
                    HrAbsenceFile.request_id.is_(None),
                    HrAbsenceFile.created_at < stale,
                )
            )
        ).all():
            await self._session.delete(old)
        waiting = await self._session.scalar(
            select(func.count())
            .select_from(HrAbsenceFile)
            .where(
                HrAbsenceFile.uploaded_by_user_id == actor.user_id,
                HrAbsenceFile.request_id.is_(None),
            )
        )
        if (waiting or 0) >= MAX_EVIDENCE_FILES * 3:
            raise HrValidationError(
                "hr_absence_file_invalid", "Bạn đã tải lên quá nhiều ảnh chưa gửi; gửi đơn trước."
            )
        clean = "".join(ch for ch in (name or "anh").strip() if ch.isprintable())[:255] or "anh"
        row = HrAbsenceFile(
            uploaded_by_user_id=actor.user_id,
            name=clean,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            data=data,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _own_waiting_files(
        self, user_id: uuid.UUID, file_ids: Sequence[uuid.UUID]
    ) -> list[HrAbsenceFile]:
        wanted = list(dict.fromkeys(file_ids))
        if not wanted:
            raise HrValidationError(
                "hr_absence_files_required",
                "Gửi kèm ít nhất một ảnh xác minh "
                "(ví dụ ảnh lịch sử duyệt web, tin nhắn công việc).",
                field="files",
            )
        if len(wanted) > MAX_EVIDENCE_FILES:
            raise HrValidationError(
                "hr_absence_file_invalid",
                f"Tối đa {MAX_EVIDENCE_FILES} ảnh mỗi đơn.",
                field="files",
            )
        rows = list(
            (
                await self._session.scalars(
                    select(HrAbsenceFile).where(
                        HrAbsenceFile.id.in_(wanted),
                        HrAbsenceFile.uploaded_by_user_id == user_id,
                        HrAbsenceFile.request_id.is_(None),
                    )
                )
            ).all()
        )
        if len(rows) != len(wanted):
            raise HrValidationError(
                "hr_absence_file_invalid", "Ảnh không còn, tải lên lại.", field="files"
            )
        return rows

    async def file(self, *, actor: Actor, access: HrAccess, file_id: uuid.UUID) -> HrAbsenceFile:
        row = await self._session.scalar(
            select(HrAbsenceFile)
            .options(undefer(HrAbsenceFile.data))
            .where(HrAbsenceFile.id == file_id)
        )
        if row is None:
            raise HrNotFoundError("Không tìm thấy ảnh.")
        if row.request_id is None:
            if row.uploaded_by_user_id != actor.user_id:
                raise HrNotFoundError("Không tìm thấy ảnh.")
            return row
        await self._visible(row.request_id, actor, access)
        return row

    async def drop_file(self, *, actor: Actor, file_id: uuid.UUID) -> None:
        row = await self._session.get(HrAbsenceFile, file_id)
        if row is None or row.uploaded_by_user_id != actor.user_id or row.request_id is not None:
            raise HrNotFoundError("Không tìm thấy ảnh.")
        await self._session.delete(row)
        await self._session.flush()

    # --- leave types -------------------------------------------------------------------

    async def save_leave_type(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        type_id: uuid.UUID | None,
        fields: dict[str, Any],
    ) -> HrLeaveType:
        access.require_timesheet()
        if type_id is None:
            code = "".join(
                ch for ch in str(fields.get("code") or "").upper() if ch.isalnum() or ch == "_"
            )
            name = str(fields.get("name") or "").strip()
            if not code or not name:
                raise HrValidationError("hr_leave_type_invalid", "Nhập mã và tên loại nghỉ.")
            if await self._session.scalar(select(HrLeaveType.id).where(HrLeaveType.code == code)):
                raise HrValidationError(
                    "hr_leave_type_invalid", "Mã loại nghỉ này đã có.", field="code"
                )
            last = await self._session.scalar(select(func.max(HrLeaveType.sort_order)))
            row = HrLeaveType(
                code=code[:30], name=name[:200], symbol=code[:2], sort_order=(last or 0) + 1
            )
            self._session.add(row)
        else:
            found = await self._session.get(HrLeaveType, type_id)
            if found is None:
                raise HrNotFoundError("Không tìm thấy loại nghỉ.")
            row = found
        if "name" in fields and fields["name"] is not None:
            name = str(fields["name"]).strip()
            if not name:
                raise HrValidationError(
                    "hr_leave_type_invalid", "Nhập tên loại nghỉ.", field="name"
                )
            row.name = name[:200]
        for key in ("paid", "annual", "active"):
            if key in fields and fields[key] is not None:
                setattr(row, key, bool(fields[key]))
        if "max_days" in fields:
            value = fields["max_days"]
            row.max_days = None if value in (None, "", 0) else int(value)
        if fields.get("symbol"):
            row.symbol = str(fields["symbol"]).strip()[:6]
        await self._session.flush()
        await self._record(
            actor, request_id, AuditAction.HR_LEAVE_TYPE_SAVED, None, {"code": row.code}
        )
        return row

    # --- annual leave -----------------------------------------------------------------------

    async def _annual_days(
        self, user_ids: Sequence[uuid.UUID], year: int
    ) -> dict[tuple[uuid.UUID, str], Decimal]:
        codes = select(HrLeaveType.code).where(HrLeaveType.annual.is_(True))
        rows = (
            await self._session.execute(
                select(
                    HrAbsenceRequest.user_id,
                    HrAbsenceRequest.status,
                    func.coalesce(func.sum(HrAbsenceRequest.days), 0),
                )
                .where(
                    HrAbsenceRequest.user_id.in_(user_ids),
                    HrAbsenceRequest.kind == AbsenceKind.LEAVE.value,
                    HrAbsenceRequest.leave_type_code.in_(codes),
                    HrAbsenceRequest.status.in_(
                        (AbsenceStatus.PENDING.value, AbsenceStatus.APPROVED.value)
                    ),
                    HrAbsenceRequest.start_on >= date(year, 1, 1),
                    HrAbsenceRequest.start_on <= date(year, 12, 31),
                )
                .group_by(HrAbsenceRequest.user_id, HrAbsenceRequest.status)
            )
        ).all()
        return {(user, status): Decimal(str(total)) for user, status, total in rows}

    async def _adjustments(
        self, user_ids: Sequence[uuid.UUID], year: int
    ) -> dict[uuid.UUID, Decimal]:
        rows = (
            await self._session.execute(
                select(HrLeaveAdjustment.user_id, func.sum(HrLeaveAdjustment.days))
                .where(HrLeaveAdjustment.user_id.in_(user_ids), HrLeaveAdjustment.year == year)
                .group_by(HrLeaveAdjustment.user_id)
            )
        ).all()
        return {user: Decimal(str(total)) for user, total in rows}

    async def balance(self, user_id: uuid.UUID, year: int, today: date) -> AnnualBalance:
        return (await self.balances_of([user_id], year, today))[user_id]

    async def balances_of(
        self, user_ids: Sequence[uuid.UUID], year: int, today: date
    ) -> dict[uuid.UUID, AnnualBalance]:
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        profiles = {
            row.user_id: row
            for row in (
                await self._session.scalars(select(HrProfile).where(HrProfile.user_id.in_(ids)))
            ).all()
        }
        days = await self._annual_days(ids, year)
        adjustments = await self._adjustments(ids, year)
        found: dict[uuid.UUID, AnnualBalance] = {}
        for user_id in ids:
            profile = profiles.get(user_id)
            found[user_id] = annual_balance(
                year=year,
                today=today,
                joined_on=None if profile is None else profile.joined_on,
                official_on=None if profile is None else profile.official_on,
                left_on=None if profile is None else profile.left_on,
                used=days.get((user_id, AbsenceStatus.APPROVED.value), Decimal(0)),
                pending=days.get((user_id, AbsenceStatus.PENDING.value), Decimal(0)),
                adjustment=adjustments.get(user_id, Decimal(0)),
            )
        return found

    async def balances(
        self,
        *,
        access: HrAccess,
        year: int,
        today: date,
        department_id: uuid.UUID | None,
        q: str | None,
    ) -> list[dict[str, Any]]:
        if not access.lists_attendance:
            raise HrForbiddenError()
        statement = select(User).where(User.deleted_at.is_(None), User.status == UserStatus.ACTIVE)
        if not access.timesheet and not access.view_all:
            statement = statement.where(User.id.in_(set(access.team) | {access.user_id}))
        if q:
            needle = f"%{q.strip()}%"
            statement = statement.where(
                or_(User.full_name.ilike(needle), User.member_code.ilike(needle))
            )
        users = list((await self._session.scalars(statement)).all())
        seats = await load_seats(self._session, [user.id for user in users])
        if department_id is not None:
            users = [
                user
                for user in users
                if any(seat.department_id == department_id for seat in seats.get(user.id, ()))
            ]
        users.sort(key=lambda user: (user.member_code or "~", user.full_name or ""))
        found = await self.balances_of([user.id for user in users], year, today)
        profiles = (
            {
                row.user_id: row
                for row in (
                    await self._session.scalars(
                        select(HrProfile).where(HrProfile.user_id.in_([user.id for user in users]))
                    )
                ).all()
            }
            if users
            else {}
        )
        return [
            {
                "user_id": str(user.id),
                "name": user.full_name,
                "member_code": user.member_code,
                "departments": [seat.name for seat in seats.get(user.id, ())],
                "joined_on": _iso(profiles[user.id].joined_on) if user.id in profiles else None,
                "official_on": _iso(profiles[user.id].official_on) if user.id in profiles else None,
                **found[user.id].as_dict(),
            }
            for user in users
        ]

    async def adjustments(
        self, *, access: HrAccess, user_id: uuid.UUID, year: int
    ) -> list[dict[str, Any]]:
        if not access.sees_attendance(user_id):
            raise HrNotFoundError()
        rows = (
            await self._session.scalars(
                select(HrLeaveAdjustment)
                .where(HrLeaveAdjustment.user_id == user_id, HrLeaveAdjustment.year == year)
                .order_by(HrLeaveAdjustment.created_at)
            )
        ).all()
        people = await self._people(row.created_by_user_id for row in rows)
        return [
            {
                "id": str(row.id),
                "days": _num(row.days),
                "note": row.note,
                "created_at": row.created_at.isoformat(),
                "created_by": people[row.created_by_user_id].name
                if row.created_by_user_id in people
                else None,
            }
            for row in rows
        ]

    async def adjust(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        year: int,
        days: Decimal,
        note: str,
    ) -> HrLeaveAdjustment:
        access.require_timesheet()
        text = (note or "").strip()
        if not text:
            raise HrValidationError(
                "hr_leave_adjust_invalid", "Nhập lý do điều chỉnh.", field="note"
            )
        if days == 0 or abs(days) > 60 or days * 2 != int(days * 2):
            raise HrValidationError(
                "hr_leave_adjust_invalid",
                "Số ngày phải khác 0, theo nửa ngày, tối đa 60.",
                field="days",
            )
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise HrNotFoundError()
        row = HrLeaveAdjustment(
            user_id=user_id,
            year=year,
            days=days,
            note=text[:1000],
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.HR_LEAVE_ADJUSTED.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_profile",
            entity_id=str(user_id),
            after_data={"year": year, "days": float(days)},
        )
        return row

    async def delete_adjustment(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, adjustment_id: uuid.UUID
    ) -> None:
        access.require_timesheet()
        row = await self._session.get(HrLeaveAdjustment, adjustment_id)
        if row is None:
            raise HrNotFoundError()
        await self._session.delete(row)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.HR_LEAVE_ADJUSTED.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_profile",
            entity_id=str(row.user_id),
            after_data={"year": row.year, "removed": float(row.days)},
        )

    # --- for the timesheet -------------------------------------------------------------------

    async def approved_between(
        self, user_ids: Sequence[uuid.UUID], start: date, end: date
    ) -> list[HrAbsenceRequest]:
        if not user_ids:
            return []
        return list(
            (
                await self._session.scalars(
                    select(HrAbsenceRequest).where(
                        HrAbsenceRequest.user_id.in_(user_ids),
                        HrAbsenceRequest.status == AbsenceStatus.APPROVED.value,
                        HrAbsenceRequest.start_on <= end,
                        HrAbsenceRequest.end_on >= start,
                    )
                )
            ).all()
        )

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        row: HrAbsenceRequest | None,
        after: dict[str, Any],
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_absence" if row is not None else "hr_leave_type",
            entity_id=None if row is None else str(row.id),
            after_data=after,
        )


def _declared(row: HrAbsenceRequest) -> str:
    """ "vào 08:00, ra 17:30" from a "Giải trình chấm công"."""
    parts = []
    if row.check_in is not None:
        parts.append(f"vào {row.check_in:%H:%M}")
    if row.check_out is not None:
        parts.append(f"ra {row.check_out:%H:%M}")
    return ", ".join(parts)


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _type_view(row: HrLeaveType) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "paid": row.paid,
        "annual": row.annual,
        "max_days": row.max_days,
        "symbol": row.symbol,
        "active": row.active,
    }


__all__ = ["MAX_SPAN_DAYS", "AbsenceMovedError", "HrAbsenceService", "ShiftBook"]
