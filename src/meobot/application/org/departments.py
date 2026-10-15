"""Departments: the OWNER's to shape, and the source the streams are read from.

Departments, seats and grants are data (see :mod:`meobot.domain.org.models`).
The OWNER creates departments, seats people in them (several departments per
person, several heads per department) and decides what each grants.

The order flow and PR still read their **stream tags** (``org_unit_members``
in the ``ADS`` and ``PR`` units): who is in the order flow and as what, who is
in PR. Those tags are no longer edited by hand - :func:`sync_stream_tags`
derives them from the seats every time a seat or a grant changes:

* a seat in a production department (Biên Tập / Thiết Kế / Dựng) makes the
  person that function's staff in the order flow - its Leader when they head
  the department, reporting to the head they report to there;
* otherwise, any order permission their seats grant (or a seat in Ban Giám
  đốc) puts them in the order flow: as an approver (``HEAD``) when a seat
  grants approving orders, else as an orderer;
* a seat in PR Thương Hiệu (or Ban Giám đốc) puts them in PR.

Only role, Leader flag, own-Leader link and open/closed are derived; the rest
of a tag (member code, NAS link, daily tokens, birthday-board grant) stays as
it was.

The older "tag a person into a stream" calls (:func:`place`, :func:`unplace`,
:func:`set_unit_manager`) are kept as a door onto the same seats: an ORD head
or orderer is a seat in ADS, a Leader or staff of a function a seat in that
function's department, a PR member a seat in PR Thương Hiệu.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.org.seats import load_grants, load_seats
from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, TERMINAL_STAGES
from meobot.domain.orders.permissions import (
    AdsPermission,
    allowed_scopes,
    normalise_matrix,
    resolve_matrix,
)
from meobot.domain.org.models import (
    DEFAULT_DEPARTMENTS,
    DEFAULT_ORDER_GRANTS,
    MODULE_ORDER,
    PRODUCTION_FUNCTIONS,
    DepartmentFunction,
    Position,
    Seat,
    normalise_department_code,
)
from meobot.domain.units.models import UnitCode, UnitMemberRole, unit_seed_id

#: The production function behind each function role of the order flow.
ROLE_FUNCTIONS: Mapping[UnitMemberRole, DepartmentFunction] = {
    UnitMemberRole.BIEN_TAP: DepartmentFunction.BIEN_TAP,
    UnitMemberRole.THIET_KE: DepartmentFunction.THIET_KE,
    UnitMemberRole.DUNG: DepartmentFunction.DUNG,
}
FUNCTION_ROLES: Mapping[DepartmentFunction, UnitMemberRole] = {
    function: role for role, function in ROLE_FUNCTIONS.items()
}
#: The department an ORD head / orderer sits in when tagged the old way.
ADS_CODE = "ADS"


class DepartmentForbiddenError(AuthorizationError):
    code = "department_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu mới tạo phòng ban, gắn người và phân quyền.",
            details={"reason": "department_forbidden"},
        )


class DepartmentNotFoundError(NotFoundError):
    code = "department_not_found"


class DepartmentValidationError(ValidationError):
    code = "department_validation_error"


# --- seeding --------------------------------------------------------------------------


async def ensure_default_departments(session: AsyncSession) -> dict[str, Department]:
    """The starting departments (and their order grants), created when missing.
    Migration 0061 seeds the same; a database built from the models (the test
    suite) calls this."""
    found = {row.code: row for row in (await session.scalars(select(Department))).all()}
    for code, name, function, order in DEFAULT_DEPARTMENTS:
        if code in found:
            continue
        row = Department(code=code, name=name, function=function, sort_order=order)
        session.add(row)
        await session.flush()
        found[code] = row
        for position, cells in DEFAULT_ORDER_GRANTS.get(code, {}).items():
            for permission, scope in cells.items():
                session.add(
                    DepartmentPermission(
                        department_id=row.id,
                        position=position,
                        module=MODULE_ORDER,
                        permission=permission,
                        scope=scope,
                    )
                )
    await session.flush()
    return found


# --- the stream tags, derived ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Tag:
    role: UnitMemberRole
    is_lead: bool
    manager_user_id: uuid.UUID | None


def _ads_tag(seats: tuple[Seat, ...], grants: dict[tuple[uuid.UUID, str], Any]) -> _Tag | None:
    """What the order flow sees of a person, from their seats."""
    production = next((seat for seat in seats if seat.function in PRODUCTION_FUNCTIONS), None)
    if production is not None:
        return _Tag(
            role=FUNCTION_ROLES[production.function],
            is_lead=production.position is Position.HEAD,
            manager_user_id=production.manager_user_id,
        )
    held = [
        (seat, permission)
        for seat in seats
        for permission, _scope in grants.get((seat.department_id, seat.position.value), ())
    ]
    board = any(seat.function is DepartmentFunction.BOARD for seat in seats)
    if not held and not board:
        return None
    approves = any(permission == AdsPermission.ORDER_APPROVE.value for _seat, permission in held)
    if approves:
        return _Tag(role=UnitMemberRole.HEAD, is_lead=False, manager_user_id=None)
    ordering = next(
        (
            seat
            for seat, permission in held
            if permission == AdsPermission.ORDER_CREATE.value and seat.manager_user_id is not None
        ),
        None,
    )
    return _Tag(
        role=UnitMemberRole.ORDERER,
        is_lead=False,
        manager_user_id=None if ordering is None else ordering.manager_user_id,
    )


def _in_pr(seats: tuple[Seat, ...]) -> bool:
    return any(seat.function in (DepartmentFunction.PR, DepartmentFunction.BOARD) for seat in seats)


async def holds_open_order_work(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Owns an unfinished order, or holds (or was chosen for) an open step."""
    open_order = Order.stage.notin_(list(TERMINAL_STAGES))
    owns = select(Order.id).where(Order.owner_user_id == user_id, open_order)
    holds_node = (
        select(OrderNode.id)
        .join(Order, Order.id == OrderNode.order_id)
        .where(
            open_order,
            OrderNode.status.in_(list(ACTIVE_NODE_STATUSES)),
            or_(OrderNode.assignee_user_id == user_id, OrderNode.preassigned_user_id == user_id),
        )
    )
    return bool(await session.scalar(select(or_(exists(owns), exists(holds_node)))))


async def sync_stream_tags(
    session: AsyncSession, user_ids: Iterable[uuid.UUID], *, guard_open_work: bool = False
) -> None:
    """Bring each person's ORD and PR tags in line with their seats.

    ``guard_open_work``: refuse (``member_has_open_work``) to take somebody
    out of the order flow while they still own an open order or hold a step."""
    wanted = list(dict.fromkeys(user_ids))
    if not wanted:
        return
    await session.flush()
    seats = await load_seats(session, wanted)
    grants = await load_grants(
        session,
        MODULE_ORDER,
        [seat.department_id for held in seats.values() for seat in held],
    )
    units = {
        code: await session.get(OrgUnit, unit_seed_id(code)) for code in (UnitCode.ADS, UnitCode.PR)
    }
    # 0063: an open tag carries its person's member code.
    member_codes = {
        user_id: code
        for user_id, code in (
            await session.execute(select(User.id, User.member_code).where(User.id.in_(wanted)))
        ).all()
        if code
    }
    now = utcnow()
    for user_id in wanted:
        held = seats.get(user_id, ())
        targets: dict[UnitCode, _Tag | None] = {
            UnitCode.ADS: _ads_tag(held, grants),
            UnitCode.PR: _Tag(UnitMemberRole.MEMBER, False, None) if _in_pr(held) else None,
        }
        for code, target in targets.items():
            unit = units[code]
            if unit is None:
                continue
            row = await session.scalar(
                select(OrgUnitMember).where(
                    OrgUnitMember.unit_id == unit.id, OrgUnitMember.user_id == user_id
                )
            )
            if target is None:
                if row is not None and row.left_at is None:
                    if (
                        code is UnitCode.ADS
                        and guard_open_work
                        and await holds_open_order_work(session, user_id)
                    ):
                        raise DepartmentValidationError(
                            "Người này còn order hoặc công đoạn đang mở. Bàn giao xong rồi gỡ.",
                            details={"reason": "member_has_open_work"},
                        )
                    row.left_at = now
                    row.manager_user_id = None
                    row.birthday_orders = False
                continue
            if row is None:
                row = OrgUnitMember(
                    unit_id=unit.id, user_id=user_id, role=target.role, joined_at=now
                )
                session.add(row)
            elif row.left_at is not None:
                row.left_at = None
                row.joined_at = now
            row.role = target.role
            row.is_lead = target.is_lead
            row.manager_user_id = target.manager_user_id
            if user_id in member_codes:
                row.member_code = member_codes[user_id]
    await session.flush()


# --- seats: the shared writes ---------------------------------------------------------


async def open_seat(
    session: AsyncSession, department_id: uuid.UUID, user_id: uuid.UUID
) -> DepartmentMember | None:
    seat: DepartmentMember | None = await session.scalar(
        select(DepartmentMember).where(
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == user_id,
            DepartmentMember.left_at.is_(None),
        )
    )
    return seat


async def seat_person(
    session: AsyncSession,
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    position: Position,
    *,
    manager_user_id: uuid.UUID | None = None,
) -> DepartmentMember:
    """Give ``user_id`` an open seat (or change the one they have)."""
    seat = await open_seat(session, department_id, user_id)
    if seat is None:
        seat = DepartmentMember(
            department_id=department_id, user_id=user_id, position=position, joined_at=utcnow()
        )
        session.add(seat)
    seat.position = position
    if position is Position.HEAD:
        seat.manager_user_id = None
    elif manager_user_id is not None:
        await check_manager(session, department_id, user_id, manager_user_id)
        seat.manager_user_id = manager_user_id
    await session.flush()
    if position is not Position.HEAD:
        # No longer a head: nobody here reports to them any more.
        await _drop_reports(session, department_id, user_id)
    return seat


async def close_seat(session: AsyncSession, department_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    seat = await open_seat(session, department_id, user_id)
    if seat is None:
        return False
    seat.left_at = utcnow()
    seat.manager_user_id = None
    await _drop_reports(session, department_id, user_id)
    await session.flush()
    return True


async def check_manager(
    session: AsyncSession,
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    manager_user_id: uuid.UUID,
    *,
    reason: str = "department_manager_invalid",
) -> None:
    """A manager is an active head of the same department, not oneself."""
    boss = await open_seat(session, department_id, manager_user_id)
    boss_user = await session.get(User, manager_user_id)
    if (
        manager_user_id == user_id
        or boss is None
        or boss.position is not Position.HEAD
        or boss_user is None
        or not boss_user.active
    ):
        raise DepartmentValidationError(
            "Người quản lý phải là một trưởng phòng đang hoạt động của cùng phòng.",
            details={"reason": reason, "field": "manager_user_id"},
        )


async def _drop_reports(
    session: AsyncSession, department_id: uuid.UUID, user_id: uuid.UUID
) -> list[uuid.UUID]:
    reports = (
        await session.scalars(
            select(DepartmentMember).where(
                DepartmentMember.department_id == department_id,
                DepartmentMember.manager_user_id == user_id,
                DepartmentMember.left_at.is_(None),
            )
        )
    ).all()
    for report in reports:
        report.manager_user_id = None
    return [report.user_id for report in reports]


async def department_by_code(session: AsyncSession, code: str) -> Department | None:
    found: Department | None = await session.scalar(
        select(Department).where(Department.code == code)
    )
    return found


async def function_department(
    session: AsyncSession, function: DepartmentFunction
) -> Department | None:
    found: Department | None = await session.scalar(
        select(Department)
        .where(Department.function == function, Department.active.is_(True))
        .order_by(Department.sort_order, Department.code)
        .limit(1)
    )
    return found


# --- the older stream door ------------------------------------------------------------


async def _stream_departments(session: AsyncSession, code: UnitCode) -> list[Department]:
    """The departments a stream's old roles live in."""
    if code is UnitCode.PR:
        found = await function_department(session, DepartmentFunction.PR)
        return [] if found is None else [found]
    rows = [await department_by_code(session, ADS_CODE)]
    for function in ROLE_FUNCTIONS.values():
        rows.append(await function_department(session, function))
    return [row for row in rows if row is not None]


async def _role_department(
    session: AsyncSession, code: UnitCode, role: UnitMemberRole
) -> Department:
    if code is UnitCode.PR:
        found = await function_department(session, DepartmentFunction.PR)
    elif role in ROLE_FUNCTIONS:
        found = await function_department(session, ROLE_FUNCTIONS[role])
    else:
        found = await department_by_code(session, ADS_CODE)
    if found is None:
        raise DepartmentNotFoundError(
            "Chưa có phòng ban cho vai trò này.", details={"reason": "department_missing"}
        )
    return found


async def place(
    session: AsyncSession,
    code: UnitCode,
    user_id: uuid.UUID,
    role: UnitMemberRole,
    *,
    is_lead: bool = False,
    manager_user_id: uuid.UUID | None = None,
) -> None:
    """Tag ``user_id`` into ``code`` as ``role`` the old way: as a seat in the
    matching department. In the order flow a person has one such role, so
    their other order-flow seats (ADS, Biên Tập, Thiết Kế, Dựng) are closed;
    seats in other departments are left alone."""
    target = await _role_department(session, code, role)
    if code is UnitCode.ADS:
        for department in await _stream_departments(session, code):
            if department.id != target.id:
                await close_seat(session, department.id, user_id)
    head = role is UnitMemberRole.HEAD or (is_lead and role in ROLE_FUNCTIONS)
    if code is UnitCode.PR:
        existing = await open_seat(session, target.id, user_id)
        head = existing is not None and existing.position is Position.HEAD
    await seat_person(
        session,
        target.id,
        user_id,
        Position.HEAD if head else Position.MEMBER,
        manager_user_id=manager_user_id,
    )
    await sync_stream_tags(session, [user_id, *(await _reports_of(session, user_id))])


async def unplace(
    session: AsyncSession, code: UnitCode, user_id: uuid.UUID, *, guard_open_work: bool = True
) -> None:
    """Untag the old way: close the person's seats in the stream's departments."""
    reports = await _reports_of(session, user_id)
    for department in await _stream_departments(session, code):
        await close_seat(session, department.id, user_id)
    await sync_stream_tags(session, [user_id, *reports], guard_open_work=guard_open_work)


async def set_unit_manager(
    session: AsyncSession,
    code: UnitCode,
    user_id: uuid.UUID,
    manager_user_id: uuid.UUID | None,
    role: UnitMemberRole,
) -> None:
    """The own-Leader link the old way: on the seat behind the person's role."""
    department = await _role_department(session, code, role)
    seat = await open_seat(session, department.id, user_id)
    if seat is None:
        raise DepartmentValidationError(
            "Thành viên không có chỗ trong phòng này.",
            details={"reason": "unit_manager_invalid"},
        )
    if manager_user_id is None:
        seat.manager_user_id = None
    else:
        await check_manager(
            session, department.id, user_id, manager_user_id, reason="unit_manager_invalid"
        )
        if seat.position is Position.HEAD:
            raise DepartmentValidationError(
                "Trưởng phòng không có người quản lý trong phòng mình.",
                details={"reason": "unit_manager_invalid"},
            )
        seat.manager_user_id = manager_user_id
    await sync_stream_tags(session, [user_id])


async def _reports_of(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(DepartmentMember.user_id).where(
                    DepartmentMember.manager_user_id == user_id,
                    DepartmentMember.left_at.is_(None),
                )
            )
        ).all()
    )


# --- the order flow's old permission matrix -------------------------------------------

#: The old matrix's columns and the seats they are now: (department, position).
#: ``None`` = every production department. The Admin column is no department:
#: it stays in the order flow's settings.
MATRIX_SEATS: Mapping[str, tuple[str | None, Position]] = {
    "HEAD": (ADS_CODE, Position.HEAD),
    "ORDERER": (ADS_CODE, Position.MEMBER),
    "LEAD": (None, Position.HEAD),
    "STAFF": (None, Position.MEMBER),
}


async def _matrix_departments(session: AsyncSession, code: str | None) -> list[Department]:
    if code is not None:
        found = await department_by_code(session, code)
        return [] if found is None else [found]
    rows = [await function_department(session, function) for function in ROLE_FUNCTIONS.values()]
    return [row for row in rows if row is not None]


async def order_matrix(
    session: AsyncSession, stored: Mapping[str, Mapping[str, str]] | None
) -> dict[str, dict[str, str]]:
    """The order flow's permission matrix as the settings page shows it, read
    from the departments' grants (0061); the Admin column from ``stored``."""
    matrix = normalise_matrix(stored)
    for column, (code, position) in MATRIX_SEATS.items():
        found = await _matrix_departments(session, code)
        if not found:
            continue
        table = await load_grants(session, MODULE_ORDER, [found[0].id])
        cells = dict(table.get((found[0].id, position.value), ()))
        matrix[column] = {
            permission.value: cells.get(permission.value, "NONE") for permission in AdsPermission
        }
    return matrix


async def apply_order_matrix(
    session: AsyncSession, cells: Mapping[str, Mapping[str, str]]
) -> dict[str, dict[str, str]]:
    """Write an edited matrix onto the departments' grants. Returns what stays
    in the settings: the Admin column. Raises ``AdsPermissionMatrixError``."""
    resolve_matrix(cells)  # refuses unknown roles, permissions and scopes
    touched: set[uuid.UUID] = set()
    for column, row in cells.items():
        if column not in MATRIX_SEATS:
            continue
        code, position = MATRIX_SEATS[column]
        for department in await _matrix_departments(session, code):
            existing = {
                item.permission: item
                for item in (
                    await session.scalars(
                        select(DepartmentPermission).where(
                            DepartmentPermission.department_id == department.id,
                            DepartmentPermission.module == MODULE_ORDER,
                            DepartmentPermission.position == position,
                        )
                    )
                ).all()
            }
            for permission, scope in row.items():
                item = existing.get(permission)
                if scope == "NONE":
                    if item is not None:
                        await session.delete(item)
                elif item is None:
                    session.add(
                        DepartmentPermission(
                            department_id=department.id,
                            position=position,
                            module=MODULE_ORDER,
                            permission=permission,
                            scope=scope,
                        )
                    )
                else:
                    item.scope = scope
            touched.add(department.id)
    await session.flush()
    members = (
        (
            await session.scalars(
                select(DepartmentMember.user_id).where(
                    DepartmentMember.department_id.in_(list(touched)),
                    DepartmentMember.left_at.is_(None),
                )
            )
        ).all()
        if touched
        else []
    )
    await sync_stream_tags(session, members)
    return {key: dict(value) for key, value in cells.items() if key not in MATRIX_SEATS}


# --- the OWNER's screen ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeatRow:
    seat: DepartmentMember
    user: User


class DepartmentService:
    """Departments, seats and grants, as the OWNER edits them. Everybody else
    may read nothing here but their own seats (on ``/api/units/me``)."""

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    @staticmethod
    def _require_owner(actor: Actor) -> None:
        if actor.role is not Role.OWNER:
            raise DepartmentForbiddenError()

    @staticmethod
    def _may_read(actor: Actor) -> None:
        if actor.role not in (Role.OWNER, Role.ADMIN):
            raise DepartmentForbiddenError()

    async def departments(self, *, actor: Actor) -> list[tuple[Department, int]]:
        self._may_read(actor)
        rows = (
            await self._session.scalars(
                select(Department).order_by(Department.sort_order, Department.code)
            )
        ).all()
        counts: dict[uuid.UUID, int] = {}
        for department_id in (
            await self._session.scalars(
                select(DepartmentMember.department_id)
                .join(User, User.id == DepartmentMember.user_id)
                .where(DepartmentMember.left_at.is_(None), User.deleted_at.is_(None))
            )
        ).all():
            counts[department_id] = counts.get(department_id, 0) + 1
        return [(row, counts.get(row.id, 0)) for row in rows]

    async def department(self, department_id: uuid.UUID) -> Department:
        row = await self._session.get(Department, department_id)
        if row is None:
            raise DepartmentNotFoundError(
                "Không tìm thấy phòng ban.", details={"reason": "department_not_found"}
            )
        return row

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: str,
        name: str,
        function: DepartmentFunction = DepartmentFunction.NONE,
        sort_order: int | None = None,
    ) -> Department:
        self._require_owner(actor)
        cleaned_code = await self._code(code, exclude=None)
        if sort_order is None:
            orders = (await self._session.scalars(select(Department.sort_order))).all()
            sort_order = max(orders, default=-1) + 1
        row = Department(
            code=cleaned_code, name=_name(name), function=function, sort_order=sort_order
        )
        self._session.add(row)
        await self._session.flush()
        await self._record(
            actor, request_id, AuditAction.DEPARTMENT_CREATED, row.id, None, _snap(row)
        )
        return row

    async def update(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        department_id: uuid.UUID,
        code: str | None = None,
        name: str | None = None,
        function: DepartmentFunction | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> Department:
        self._require_owner(actor)
        row = await self.department(department_id)
        before = _snap(row)
        if code is not None:
            row.code = await self._code(code, exclude=row.id)
        if name is not None:
            row.name = _name(name)
        if function is not None:
            row.function = function
        if active is not None:
            row.active = active
        if sort_order is not None:
            row.sort_order = sort_order
        await self._session.flush()
        if function is not None or active is not None:
            # What the department is decides the streams of everybody in it.
            await sync_stream_tags(self._session, await self._member_ids(row.id))
        await self._record(
            actor, request_id, AuditAction.DEPARTMENT_UPDATED, row.id, before, _snap(row)
        )
        return row

    async def members(self, *, actor: Actor, department_id: uuid.UUID) -> list[SeatRow]:
        self._may_read(actor)
        await self.department(department_id)
        rows = (
            await self._session.execute(
                select(DepartmentMember, User)
                .join(User, User.id == DepartmentMember.user_id)
                .where(
                    DepartmentMember.department_id == department_id,
                    DepartmentMember.left_at.is_(None),
                    User.deleted_at.is_(None),
                )
                .order_by(DepartmentMember.position, User.full_name, User.id)
            )
        ).all()
        return [SeatRow(seat=seat, user=user) for seat, user in rows]

    async def seat(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        department_id: uuid.UUID,
        user_id: uuid.UUID,
        position: Position | None = None,
        manager_user_id: uuid.UUID | None = None,
        clear_manager: bool = False,
    ) -> SeatRow:
        """Add a person to the department, or change their seat."""
        self._require_owner(actor)
        department = await self.department(department_id)
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise DepartmentValidationError(
                "Không tìm thấy thành viên này.",
                details={"reason": "user_not_found", "field": "user_id"},
            )
        existing = await open_seat(self._session, department.id, user_id)
        before = None if existing is None else _seat_snap(existing)
        target = position or (existing.position if existing is not None else Position.MEMBER)
        row = await seat_person(
            self._session,
            department.id,
            user_id,
            target,
            manager_user_id=None if clear_manager else manager_user_id,
        )
        if clear_manager:
            row.manager_user_id = None
        await sync_stream_tags(
            self._session, [user_id, *(await _reports_of(self._session, user_id))]
        )
        await self._record(
            actor,
            request_id,
            AuditAction.DEPARTMENT_MEMBER_SET,
            department.id,
            before,
            _seat_snap(row),
        )
        return SeatRow(seat=row, user=user)

    async def unseat(
        self, *, actor: Actor, request_id: uuid.UUID, department_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        self._require_owner(actor)
        department = await self.department(department_id)
        existing = await open_seat(self._session, department.id, user_id)
        if existing is None:
            raise DepartmentNotFoundError(
                "Người này không thuộc phòng.", details={"reason": "member_not_seated"}
            )
        before = _seat_snap(existing)
        reports = await _reports_of(self._session, user_id)
        await close_seat(self._session, department.id, user_id)
        await sync_stream_tags(self._session, [user_id, *reports], guard_open_work=True)
        await self._record(
            actor, request_id, AuditAction.DEPARTMENT_MEMBER_REMOVED, department.id, before, None
        )

    async def grants(
        self, *, actor: Actor, department_id: uuid.UUID, module: str = MODULE_ORDER
    ) -> dict[str, dict[str, str]]:
        """``position -> permission -> scope`` (``NONE`` included)."""
        self._may_read(actor)
        await self.department(department_id)
        table = await load_grants(self._session, module, [department_id])
        return {
            position.value: {
                permission.value: dict(table.get((department_id, position.value), ())).get(
                    permission.value, "NONE"
                )
                for permission in AdsPermission
            }
            for position in Position
        }

    async def set_grants(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        department_id: uuid.UUID,
        cells: Mapping[str, Mapping[str, str]],
        module: str = MODULE_ORDER,
    ) -> dict[str, dict[str, str]]:
        """Replace what the department grants, position by position."""
        self._require_owner(actor)
        await self.department(department_id)
        before = await self.grants(actor=actor, department_id=department_id, module=module)
        wanted: dict[tuple[Position, AdsPermission], str] = {}
        for position_key, line in cells.items():
            try:
                position = Position(position_key)
            except ValueError as error:
                raise _bad_cell(position_key) from error
            for permission_key, scope in line.items():
                try:
                    permission = AdsPermission(permission_key)
                except ValueError as error:
                    raise _bad_cell(permission_key) from error
                if scope not in {item.value for item in allowed_scopes(permission)}:
                    raise _bad_cell(f"{permission_key}={scope}")
                wanted[(position, permission)] = scope
        existing = (
            await self._session.scalars(
                select(DepartmentPermission).where(
                    DepartmentPermission.department_id == department_id,
                    DepartmentPermission.module == module,
                )
            )
        ).all()
        by_cell = {(row.position, row.permission): row for row in existing}
        for (position, permission), scope in wanted.items():
            row = by_cell.get((position, permission.value))
            if scope == "NONE":
                if row is not None:
                    await self._session.delete(row)
            elif row is None:
                self._session.add(
                    DepartmentPermission(
                        department_id=department_id,
                        position=position,
                        module=module,
                        permission=permission.value,
                        scope=scope,
                    )
                )
            else:
                row.scope = scope
        await self._session.flush()
        await sync_stream_tags(self._session, await self._member_ids(department_id))
        after = await self.grants(actor=actor, department_id=department_id, module=module)
        await self._record(
            actor, request_id, AuditAction.DEPARTMENT_GRANTS_UPDATED, department_id, before, after
        )
        return after

    # --- helpers ------------------------------------------------------------------

    async def _member_ids(self, department_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self._session.scalars(
                    select(DepartmentMember.user_id).where(
                        DepartmentMember.department_id == department_id,
                        DepartmentMember.left_at.is_(None),
                    )
                )
            ).all()
        )

    async def _code(self, value: str, *, exclude: uuid.UUID | None) -> str:
        cleaned = normalise_department_code(value)
        if not cleaned:
            raise DepartmentValidationError(
                "Cần nhập mã phòng (chữ và số).",
                details={"reason": "department_code_missing", "field": "code"},
            )
        statement = select(Department.id).where(Department.code == cleaned)
        if exclude is not None:
            statement = statement.where(Department.id != exclude)
        if await self._session.scalar(statement) is not None:
            raise DepartmentValidationError(
                "Mã phòng này đã có.", details={"reason": "department_code_taken", "field": "code"}
            )
        return cleaned

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        entity_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="department",
            entity_id=str(entity_id),
            before_data=before,
            after_data=after,
        )


def _name(value: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned or len(cleaned) > 120:
        raise DepartmentValidationError(
            "Tên phòng từ 1 đến 120 ký tự.",
            details={"reason": "department_name_invalid", "field": "name"},
        )
    return cleaned


def _bad_cell(what: str) -> DepartmentValidationError:
    return DepartmentValidationError(
        f"Ô phân quyền không hợp lệ: {what}.",
        details={"reason": "department_grant_invalid", "field": "cells"},
    )


def _snap(row: Department) -> dict[str, Any]:
    return {
        "code": row.code,
        "name": row.name,
        "function": row.function.value,
        "active": row.active,
        "sort_order": row.sort_order,
    }


def _seat_snap(row: DepartmentMember) -> dict[str, Any]:
    return {
        "user_id": str(row.user_id),
        "position": row.position.value,
        "manager_user_id": None if row.manager_user_id is None else str(row.manager_user_id),
    }


__all__ = [
    "ADS_CODE",
    "FUNCTION_ROLES",
    "MATRIX_SEATS",
    "ROLE_FUNCTIONS",
    "DepartmentForbiddenError",
    "DepartmentNotFoundError",
    "DepartmentService",
    "DepartmentValidationError",
    "SeatRow",
    "apply_order_matrix",
    "check_manager",
    "close_seat",
    "ensure_default_departments",
    "holds_open_order_work",
    "order_matrix",
    "place",
    "seat_person",
    "set_unit_manager",
    "sync_stream_tags",
    "unplace",
]
