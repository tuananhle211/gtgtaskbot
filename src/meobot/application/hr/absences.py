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

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.hr.absence_notifications import AbsenceNotifier
from meobot.application.hr.access import HrAccess, HrForbiddenError, HrNotFoundError
from meobot.application.hr.profiles import HrValidationError
from meobot.application.hr.shifts import ShiftBook, holidays_between, load_shift_book, require_open
from meobot.application.org.access import load_access
from meobot.application.org.seats import load_seats
from meobot.core.config import Settings
from meobot.core.errors import ConflictError
from meobot.core.time import utcnow
from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.db.models.hr_attendance import (
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
        )
        self._session.add(row)
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
            {"kind": row.kind, "leave_type": row.leave_type_code, "days": _num(row.days)},
        )
        await self._advance(row, actor=actor)
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

    async def _advance(self, row: HrAbsenceRequest, *, actor: Actor) -> None:
        """Open the next step that is not skipped, else approve the request."""
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
            await self._notify_step(row, step, sender=actor.user_id)
            return
        row.status = AbsenceStatus.APPROVED.value
        row.decided_at = utcnow()
        await self._session.flush()
        await self._notify_requester(row, NotificationEvent.HR_ABSENCE_APPROVED, None, actor)

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
            await self._advance(row, actor=actor)
        else:
            for other in steps:
                if other.status in OPEN_STEPS:
                    other.status = StepStatus.CLOSED.value
            row.status = AbsenceStatus.REJECTED.value
            row.decided_at = utcnow()
            await self._session.flush()
            await self._notify_requester(row, NotificationEvent.HR_ABSENCE_REJECTED, text, actor)
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
        for step in await self._steps(row.id):
            if step.status in OPEN_STEPS:
                step.status = StepStatus.CLOSED.value
        row.status = AbsenceStatus.CANCELLED.value
        row.cancelled_by_user_id = actor.user_id
        row.cancel_note = text
        row.decided_at = utcnow()
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.HR_ABSENCE_CANCELLED, row, {"own": own})
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
        else:
            verb = "đến lúc" if kind is AbsenceKind.LATE else "về lúc"
            at = row.at_time.strftime("%H:%M") if row.at_time else ""
            what = f"{KIND_LABELS[kind]} {_fmt_day(row.start_on)}, {verb} {at} ({row.minutes} phút)"
        return name, what

    async def _notify_step(
        self, row: HrAbsenceRequest, step: HrAbsenceStep, *, sender: uuid.UUID | None
    ) -> None:
        name, what = await self._describe(row)
        recipients = [uuid.UUID(item) for item in step.approver_ids]
        await AbsenceNotifier(self._session, self._settings).tell(
            recipients=recipients,
            event=NotificationEvent.HR_ABSENCE_AWAITING,
            title=f"{name}: {what}",
            body=f"{name} xin {what.lower()[:1] + what[1:]}. Đang chờ bạn duyệt "
            f"({LEVEL_LABELS[StepLevel(step.level)]}).",
            request_id=row.id,
            key=f"step{step.position}",
            sender=sender,
        )

    async def _notify_requester(
        self,
        row: HrAbsenceRequest,
        event: NotificationEvent,
        note: str | None,
        actor: Actor,
    ) -> None:
        _name, what = await self._describe(row)
        body = (
            f"Đơn {what[:1].lower() + what[1:]} đã được duyệt."
            if event is NotificationEvent.HR_ABSENCE_APPROVED
            else f"Đơn {what[:1].lower() + what[1:]} không được duyệt. Lý do: {note}"
        )
        await AbsenceNotifier(self._session, self._settings).tell(
            recipients=[row.user_id],
            event=event,
            title=what,
            body=body,
            request_id=row.id,
            key="final",
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
        return [
            self._view(
                row,
                steps_of.get(row.id, []),
                people,
                types,
                [seat.name for seat in seats.get(row.user_id, ())],
                actor,
                access,
            )
            for row in rows
        ]

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
