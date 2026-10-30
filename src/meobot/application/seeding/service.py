"""The PR seeding group (0085).

* **Who**: a Trưởng phòng PR (or the OWNER / an ADMIN) puts people in the group
  ("Thành viên seeding"); only they and the group see the "Seeding" menu.
* **KPI**: one number of phones for the whole team per week (Monday-Sunday)
  and per month, set by the Trưởng phòng; the bar is the share reached by the
  approved numbers handed in during that period.
* **Nộp SĐT**: a member pastes numbers, one per line; each is normalised
  (:mod:`meobot.domain.seeding.phones`) and refused when invalid, twice in the
  batch, or already in the system (handed in by anybody and not refused). The
  rest wait for the Trưởng phòng; an approved number is worth
  ``PHONE_POINTS`` to whoever handed it in - one ``work_points`` row per person
  per day of hand-in, rewritten as numbers are decided.
* **Việc khác**: work + points, approved by the Trưởng phòng, then counted
  under "Việc khác" on the reports.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.org.seats import load_seats
from meobot.application.performance.pr_points import reviews_pr
from meobot.application.performance.work_ledger import drop_points, put_points
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.user import User
from meobot.db.models.work_points import (
    SeedingMember,
    SeedingOtherReport,
    SeedingPhone,
    SeedingTarget,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import DepartmentFunction
from meobot.domain.performance.work_points import (
    PHASE_OTHER,
    PHASE_SEEDING,
    PHONE_POINTS,
    WorkPointSource,
    other_item,
    phone_item,
)
from meobot.domain.seeding.errors import (
    SeedingForbiddenError,
    SeedingInvalidError,
    SeedingNotFoundError,
)
from meobot.domain.seeding.phones import (
    DUPLICATE,
    INVALID,
    MAX_LINES,
    REPEATED,
    VALID,
    read_lines,
    week_start,
)
from meobot.domain.units.models import UnitMembership

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
WEEK = "WEEK"
MONTH = "MONTH"
MAX_OTHER_POINTS = 100_000
DESCRIPTION_MAX = 2000


@dataclass(frozen=True, slots=True)
class SeedingAccess:
    user_id: uuid.UUID | None
    member: bool
    manage: bool

    @property
    def sees(self) -> bool:
        return self.member or self.manage


async def seeding_access(session: AsyncSession, membership: UnitMembership) -> SeedingAccess:
    manage = reviews_pr(membership)
    member = False
    if membership.user_id is not None:
        member = (
            await session.scalar(
                select(SeedingMember.id).where(
                    SeedingMember.user_id == membership.user_id,
                    SeedingMember.removed_at.is_(None),
                )
            )
        ) is not None
    return SeedingAccess(user_id=membership.user_id, member=member, manage=manage)


@dataclass(frozen=True, slots=True)
class Person:
    user_id: uuid.UUID
    name: str
    departments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Period:
    kind: str
    start: date
    end: date
    target: int | None
    approved: int
    pending: int

    @property
    def rate(self) -> float | None:
        if not self.target:
            return None
        return round(self.approved / self.target, 4)


@dataclass(frozen=True, slots=True)
class MemberFigures:
    person: Person
    approved_week: int
    approved_month: int
    pending: int
    phone_points_month: int
    other_points_month: int


@dataclass(frozen=True, slots=True)
class Overview:
    day: date
    week: Period
    month: Period
    members: tuple[MemberFigures, ...]


@dataclass(frozen=True, slots=True)
class LineResult:
    number: int
    raw: str
    phone: str | None
    status: str


@dataclass(frozen=True, slots=True)
class PhoneView:
    row: SeedingPhone
    name: str
    decided_by: str | None


@dataclass(frozen=True, slots=True)
class OtherView:
    row: SeedingOtherReport
    name: str
    decided_by: str | None


class SeedingService:
    def __init__(self, session: AsyncSession, audit: AuditService, *, tz: ZoneInfo) -> None:
        self._session = session
        self._audit = audit
        self._tz = tz

    # --- people -------------------------------------------------------------------

    async def members(self, access: SeedingAccess) -> list[Person]:
        _require(access.sees)
        ids = list(
            (
                await self._session.scalars(
                    select(SeedingMember.user_id)
                    .where(SeedingMember.removed_at.is_(None))
                    .order_by(SeedingMember.added_at)
                )
            ).all()
        )
        return await self._people(ids)

    async def candidates(self, access: SeedingAccess) -> list[Person]:
        """Active people seated in a PR department, not in the group yet."""
        _require(access.manage)
        seated = (
            await self._session.scalars(
                select(DepartmentMember.user_id)
                .join(Department, Department.id == DepartmentMember.department_id)
                .join(User, User.id == DepartmentMember.user_id)
                .where(
                    Department.function == DepartmentFunction.PR,
                    Department.active.is_(True),
                    DepartmentMember.left_at.is_(None),
                    User.active.is_(True),
                    User.deleted_at.is_(None),
                )
                .distinct()
            )
        ).all()
        current = {person.user_id for person in await self.members(access)}
        people = await self._people([user_id for user_id in seated if user_id not in current])
        return sorted(people, key=lambda person: person.name)

    async def add_member(
        self, *, actor: Actor, access: SeedingAccess, request_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        _require(access.manage)
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None or not user.active:
            raise SeedingNotFoundError()
        open_row = await self._open_member(user_id)
        if open_row is not None:
            return
        self._session.add(SeedingMember(user_id=user_id, added_by_user_id=actor.user_id))
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.SEEDING_MEMBER_CHANGED, user_id, "add")

    async def remove_member(
        self, *, actor: Actor, access: SeedingAccess, request_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        _require(access.manage)
        row = await self._open_member(user_id)
        if row is None:
            raise SeedingNotFoundError()
        row.removed_at = utcnow()
        row.removed_by_user_id = actor.user_id
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.SEEDING_MEMBER_CHANGED, user_id, "remove")

    # --- the KPI ------------------------------------------------------------------

    async def overview(self, access: SeedingAccess, day: date) -> Overview:
        _require(access.sees)
        week_first = week_start(day)
        week = await self._period(WEEK, week_first, week_first + timedelta(days=6))
        month_first = day.replace(day=1)
        next_month = (month_first + timedelta(days=32)).replace(day=1)
        month = await self._period(MONTH, month_first, next_month - timedelta(days=1))
        people = await self.members(access)
        week_counts = await self._approved_by_person(week.start, week.end)
        month_counts = await self._approved_by_person(month.start, month.end)
        pending = await self._pending_by_person()
        other = await self._other_points_by_person(month.start, month.end)
        seen = {person.user_id for person in people}
        extra = [
            user_id
            for user_id in {*week_counts, *month_counts, *pending, *other}
            if user_id not in seen
        ]
        people.extend(await self._people(extra))
        rows = tuple(
            sorted(
                (
                    MemberFigures(
                        person=person,
                        approved_week=week_counts.get(person.user_id, 0),
                        approved_month=month_counts.get(person.user_id, 0),
                        pending=pending.get(person.user_id, 0),
                        phone_points_month=month_counts.get(person.user_id, 0) * PHONE_POINTS,
                        other_points_month=other.get(person.user_id, 0),
                    )
                    for person in people
                ),
                key=lambda row: (-row.approved_month, row.person.name),
            )
        )
        return Overview(day=day, week=week, month=month, members=rows)

    async def set_target(
        self,
        *,
        actor: Actor,
        access: SeedingAccess,
        request_id: uuid.UUID,
        kind: str,
        day: date,
        target: int | None,
    ) -> None:
        _require(access.manage)
        if kind not in (WEEK, MONTH):
            raise SeedingInvalidError("Chọn KPI tuần hoặc tháng.", field="kind")
        if target is not None and not 0 <= target <= 1_000_000:
            raise SeedingInvalidError("KPI phải từ 0 đến 1.000.000 số.", field="target")
        start = week_start(day) if kind == WEEK else day.replace(day=1)
        row = await self._session.scalar(
            select(SeedingTarget).where(
                SeedingTarget.kind == kind, SeedingTarget.period_start == start
            )
        )
        if target is None:
            if row is not None:
                await self._session.delete(row)
        elif row is None:
            self._session.add(
                SeedingTarget(
                    kind=kind, period_start=start, target=target, set_by_user_id=actor.user_id
                )
            )
        else:
            row.target = target
            row.set_by_user_id = actor.user_id
            row.updated_at = utcnow()
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SEEDING_TARGET_SET.value,
            result=AuditResult.SUCCESS,
            entity_type="seeding_target",
            entity_id=f"{kind}:{start.isoformat()}",
            after_data={"target": target},
        )

    # --- phones -------------------------------------------------------------------

    async def submit_phones(
        self, *, actor: Actor, access: SeedingAccess, request_id: uuid.UUID, text: str
    ) -> list[LineResult]:
        _require(access.sees)
        assert actor.user_id is not None
        lines = read_lines(text)
        if not lines:
            raise SeedingInvalidError("Dán ít nhất một số điện thoại.", field="text")
        if len(lines) > MAX_LINES:
            raise SeedingInvalidError(f"Mỗi lần nộp tối đa {MAX_LINES} số.", field="text")
        wanted = {line.phone for line in lines if line.phone}
        taken = set()
        if wanted:
            taken = set(
                (
                    await self._session.scalars(
                        select(SeedingPhone.phone).where(
                            SeedingPhone.phone.in_(wanted), SeedingPhone.status != REJECTED
                        )
                    )
                ).all()
            )
        now = utcnow()
        seen: set[str] = set()
        results: list[LineResult] = []
        for line in lines:
            if line.phone is None:
                status = INVALID
            elif line.phone in taken:
                status = DUPLICATE
            elif line.phone in seen:
                status = REPEATED
            else:
                status = VALID
                seen.add(line.phone)
                self._session.add(
                    SeedingPhone(
                        phone=line.phone,
                        submitted_by_user_id=actor.user_id,
                        submitted_at=now,
                        status=PENDING,
                    )
                )
            results.append(LineResult(line.number, line.raw, line.phone, status))
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SEEDING_PHONES_SUBMITTED.value,
            result=AuditResult.SUCCESS,
            entity_type="seeding_phones",
            entity_id=str(actor.user_id),
            after_data={"accepted": len(seen), "lines": len(lines)},
        )
        return results

    async def phones(
        self,
        access: SeedingAccess,
        *,
        status: str | None = None,
        mine: bool = False,
        limit: int = 500,
    ) -> list[PhoneView]:
        _require(access.sees)
        statement = select(SeedingPhone).order_by(SeedingPhone.submitted_at.desc()).limit(limit)
        if mine or not access.manage:
            statement = statement.where(SeedingPhone.submitted_by_user_id == access.user_id)
        if status:
            statement = statement.where(SeedingPhone.status == status)
        rows = (await self._session.scalars(statement)).all()
        names = await self._names(
            {row.submitted_by_user_id for row in rows}
            | {row.decided_by_user_id for row in rows if row.decided_by_user_id}
        )
        return [
            PhoneView(
                row=row,
                name=names.get(row.submitted_by_user_id, "—"),
                decided_by=names.get(row.decided_by_user_id) if row.decided_by_user_id else None,
            )
            for row in rows
        ]

    async def decide_phones(
        self,
        *,
        actor: Actor,
        access: SeedingAccess,
        request_id: uuid.UUID,
        ids: Sequence[uuid.UUID],
        approve: bool,
        note: str | None = None,
    ) -> int:
        """Approve or refuse pending numbers; returns how many were decided."""
        _require(access.manage)
        if not ids:
            raise SeedingInvalidError("Chọn ít nhất một số.", field="ids")
        rows = (
            await self._session.scalars(
                select(SeedingPhone).where(
                    SeedingPhone.id.in_(list(ids)), SeedingPhone.status == PENDING
                )
            )
        ).all()
        now = utcnow()
        touched: set[tuple[uuid.UUID, date]] = set()
        for row in rows:
            row.status = APPROVED if approve else REJECTED
            row.decided_by_user_id = actor.user_id
            row.decided_at = now
            row.decision_note = (note or "").strip() or None
            touched.add((row.submitted_by_user_id, self._local_day(row.submitted_at)))
        await self._session.flush()
        for user_id, day in touched:
            await self._refresh_day(user_id, day)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SEEDING_DECIDED.value,
            result=AuditResult.SUCCESS,
            entity_type="seeding_phones",
            entity_id=str(len(rows)),
            after_data={"approve": approve, "count": len(rows)},
        )
        return len(rows)

    # --- other work ----------------------------------------------------------------

    async def submit_other(
        self,
        *,
        actor: Actor,
        access: SeedingAccess,
        request_id: uuid.UUID,
        description: str,
        points: int,
    ) -> OtherView:
        _require(access.sees)
        assert actor.user_id is not None
        text = description.strip()
        if not text:
            raise SeedingInvalidError("Ghi việc đã làm.", field="description")
        if len(text) > DESCRIPTION_MAX:
            raise SeedingInvalidError(f"Mô tả tối đa {DESCRIPTION_MAX} ký tự.", field="description")
        if not 1 <= points <= MAX_OTHER_POINTS:
            raise SeedingInvalidError(f"Số điểm phải từ 1 đến {MAX_OTHER_POINTS}.", field="points")
        row = SeedingOtherReport(
            user_id=actor.user_id,
            description=text,
            points=points,
            submitted_at=utcnow(),
            status=PENDING,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SEEDING_OTHER_SUBMITTED.value,
            result=AuditResult.SUCCESS,
            entity_type="seeding_other_report",
            entity_id=str(row.id),
            after_data={"points": points},
        )
        if access.manage:
            await self._approve_other(row, actor)
        return (await self._other_views([row]))[0]

    async def others(
        self,
        access: SeedingAccess,
        *,
        status: str | None = None,
        mine: bool = False,
        limit: int = 300,
    ) -> list[OtherView]:
        _require(access.sees)
        statement = (
            select(SeedingOtherReport).order_by(SeedingOtherReport.submitted_at.desc()).limit(limit)
        )
        if mine or not access.manage:
            statement = statement.where(SeedingOtherReport.user_id == access.user_id)
        if status:
            statement = statement.where(SeedingOtherReport.status == status)
        return await self._other_views((await self._session.scalars(statement)).all())

    async def decide_others(
        self,
        *,
        actor: Actor,
        access: SeedingAccess,
        request_id: uuid.UUID,
        ids: Sequence[uuid.UUID],
        approve: bool,
        note: str | None = None,
    ) -> int:
        _require(access.manage)
        if not ids:
            raise SeedingInvalidError("Chọn ít nhất một mục.", field="ids")
        rows = (
            await self._session.scalars(
                select(SeedingOtherReport).where(
                    SeedingOtherReport.id.in_(list(ids)), SeedingOtherReport.status == PENDING
                )
            )
        ).all()
        for row in rows:
            row.decision_note = (note or "").strip() or None
            if approve:
                await self._approve_other(row, actor)
            else:
                row.status = REJECTED
                row.decided_by_user_id = actor.user_id
                row.decided_at = utcnow()
                await drop_points(self._session, f"seeding_other:{row.id}")
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SEEDING_DECIDED.value,
            result=AuditResult.SUCCESS,
            entity_type="seeding_other_reports",
            entity_id=str(len(rows)),
            after_data={"approve": approve, "count": len(rows)},
        )
        return len(rows)

    # --- helpers ------------------------------------------------------------------

    async def _approve_other(self, row: SeedingOtherReport, actor: Actor) -> None:
        row.status = APPROVED
        row.decided_by_user_id = actor.user_id
        row.decided_at = utcnow()
        await put_points(
            self._session,
            source=WorkPointSource.SEEDING_OTHER.value,
            source_key=f"seeding_other:{row.id}",
            user_id=row.user_id,
            phase=PHASE_OTHER,
            items=[other_item(row.description, row.points)],
            earned_at=ensure_utc(row.submitted_at),
            ref_title=row.description[:120],
        )

    async def _refresh_day(self, user_id: uuid.UUID, day: date) -> None:
        """Rewrite one person's phone points for one day of hand-in."""
        start, end = self._bounds(day, day)
        count = int(
            await self._session.scalar(
                select(func.count(SeedingPhone.id)).where(
                    SeedingPhone.submitted_by_user_id == user_id,
                    SeedingPhone.status == APPROVED,
                    SeedingPhone.submitted_at >= start,
                    SeedingPhone.submitted_at < end,
                )
            )
            or 0
        )
        key = f"seeding_phones:{user_id}:{day.isoformat()}"
        if count == 0:
            await drop_points(self._session, key)
            return
        noon = datetime.combine(day, time(12), tzinfo=self._tz)
        await put_points(
            self._session,
            source=WorkPointSource.SEEDING_PHONES.value,
            source_key=key,
            user_id=user_id,
            phase=PHASE_SEEDING,
            items=[phone_item(count)],
            earned_at=noon,
            ref_title=f"Seeding {day.strftime('%d/%m/%Y')} · {count} SĐT",
        )

    async def _period(self, kind: str, first: date, last: date) -> Period:
        target = await self._session.scalar(
            select(SeedingTarget.target).where(
                SeedingTarget.kind == kind, SeedingTarget.period_start == first
            )
        )
        start, end = self._bounds(first, last)
        counts = {
            status: int(count)
            for status, count in (
                await self._session.execute(
                    select(SeedingPhone.status, func.count(SeedingPhone.id))
                    .where(SeedingPhone.submitted_at >= start, SeedingPhone.submitted_at < end)
                    .group_by(SeedingPhone.status)
                )
            ).all()
        }
        return Period(
            kind=kind,
            start=first,
            end=last,
            target=target,
            approved=counts.get(APPROVED, 0),
            pending=counts.get(PENDING, 0),
        )

    async def _approved_by_person(self, first: date, last: date) -> dict[uuid.UUID, int]:
        start, end = self._bounds(first, last)
        return {
            user_id: int(count)
            for user_id, count in (
                await self._session.execute(
                    select(SeedingPhone.submitted_by_user_id, func.count(SeedingPhone.id))
                    .where(
                        SeedingPhone.status == APPROVED,
                        SeedingPhone.submitted_at >= start,
                        SeedingPhone.submitted_at < end,
                    )
                    .group_by(SeedingPhone.submitted_by_user_id)
                )
            ).all()
        }

    async def _pending_by_person(self) -> dict[uuid.UUID, int]:
        return {
            user_id: int(count)
            for user_id, count in (
                await self._session.execute(
                    select(SeedingPhone.submitted_by_user_id, func.count(SeedingPhone.id))
                    .where(SeedingPhone.status == PENDING)
                    .group_by(SeedingPhone.submitted_by_user_id)
                )
            ).all()
        }

    async def _other_points_by_person(self, first: date, last: date) -> dict[uuid.UUID, int]:
        start, end = self._bounds(first, last)
        totals: dict[uuid.UUID, int] = defaultdict(int)
        for user_id, points in (
            await self._session.execute(
                select(SeedingOtherReport.user_id, SeedingOtherReport.points).where(
                    SeedingOtherReport.status == APPROVED,
                    SeedingOtherReport.submitted_at >= start,
                    SeedingOtherReport.submitted_at < end,
                )
            )
        ).all():
            totals[user_id] += int(points)
        return dict(totals)

    def _bounds(self, first: date, last: date) -> tuple[datetime, datetime]:
        """``[first 00:00, last+1 00:00)`` in Vietnam time, as instants."""
        start = datetime.combine(first, time(0), tzinfo=self._tz)
        end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=self._tz)
        return start, end

    def _local_day(self, at: datetime) -> date:
        return ensure_utc(at).astimezone(self._tz).date()

    async def _open_member(self, user_id: uuid.UUID) -> SeedingMember | None:
        row: SeedingMember | None = await self._session.scalar(
            select(SeedingMember).where(
                SeedingMember.user_id == user_id, SeedingMember.removed_at.is_(None)
            )
        )
        return row

    async def _people(self, ids: Sequence[uuid.UUID]) -> list[Person]:
        if not ids:
            return []
        names = await self._names(set(ids))
        seats = await load_seats(self._session, ids)
        return [
            Person(
                user_id=user_id,
                name=names.get(user_id, "—"),
                departments=tuple(seat.name for seat in seats.get(user_id, ())),
            )
            for user_id in ids
        ]

    async def _names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await self._session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
        return {user_id: name or "—" for user_id, name in rows.all()}

    async def _other_views(self, rows: Sequence[SeedingOtherReport]) -> list[OtherView]:
        names = await self._names(
            {row.user_id for row in rows}
            | {row.decided_by_user_id for row in rows if row.decided_by_user_id}
        )
        return [
            OtherView(
                row=row,
                name=names.get(row.user_id, "—"),
                decided_by=names.get(row.decided_by_user_id) if row.decided_by_user_id else None,
            )
            for row in rows
        ]

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        user_id: uuid.UUID,
        change: str,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="seeding_member",
            entity_id=str(user_id),
            after_data={"change": change},
        )


def _require(allowed: bool) -> None:
    if not allowed:
        raise SeedingForbiddenError()


__all__ = [
    "APPROVED",
    "MONTH",
    "PENDING",
    "REJECTED",
    "WEEK",
    "LineResult",
    "MemberFigures",
    "OtherView",
    "Overview",
    "Period",
    "Person",
    "PhoneView",
    "SeedingAccess",
    "SeedingService",
    "seeding_access",
]
