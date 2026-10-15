"""The resources module's one service: pick-lists, settings, the inventory
and its histories, the look-up by employee.

Every read goes through the caller's :class:`ResourceAccess`: a resource
outside what they may see is a 404 everywhere, a write they may not make a
403. Every write leaves its line in the append-only histories (who, when,
old → new) and an audit entry; nothing is ever deleted (rule 5: a resource
that served its time is set "Ngừng dùng" or "Đã thu hồi").
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_support import supports_row_locks
from meobot.application.resources.access import (
    ResourceAccess,
    ResourceForbiddenError,
    ResourceNotFoundError,
    ResourceValidationError,
)
from meobot.application.resources.alerts import Alert, alerts_for
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.resource import (
    Resource,
    ResourceChange,
    ResourceCodeCounter,
    ResourceHandover,
    ResourceOption,
    ResourceStatusHistory,
)
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.resources.models import (
    DEFAULT_HANDOVER_CODE,
    DEFAULT_REVIEW_DAYS,
    DEFAULT_STATUS_CODE,
    EMPLOYMENT_STATUS_LABELS,
    SEED_OPTIONS,
    EmploymentStatus,
    HandoverReason,
    OptionKind,
    StatusGroup,
    format_resource_code,
    is_resource_code,
    looks_like_secret,
)

#: The ``system_settings`` row the module's settings live in.
SETTINGS_KEY = "resources"
MAX_REVIEW_DAYS = 3650

#: The fields of a resource a person edits, and the pick-list each id points at.
OPTION_FIELDS: Mapping[str, OptionKind] = {
    "type_id": OptionKind.TYPE,
    "role_id": OptionKind.ROLE,
    "two_factor_id": OptionKind.TWO_FACTOR,
    "origin_id": OptionKind.ORIGIN,
    "handover_id": OptionKind.HANDOVER,
    "status_id": OptionKind.STATUS,
}
USER_FIELDS: tuple[str, ...] = ("holder_user_id", "two_factor_holder_user_id")
TEXT_FIELDS: tuple[str, ...] = ("external_id", "name", "link", "email", "phone", "purpose", "note")
#: What the change log calls each field.
FIELD_LABELS: Mapping[str, str] = {
    "code": "Mã tài nguyên",
    "department_id": "Ban quản lý",
    "type_id": "Loại",
    "external_id": "ID tài nguyên",
    "name": "Tên tài nguyên",
    "link": "Link",
    "holder_user_id": "Người được bàn giao",
    "role_id": "Quyền đang có",
    "email": "Email đăng ký",
    "phone": "SĐT đăng ký",
    "two_factor_id": "Bảo mật 2 lớp",
    "two_factor_holder_user_id": "Người giữ mã 2 lớp",
    "origin_id": "Nguồn gốc",
    "started_on": "Ngày bắt đầu dùng",
    "purpose": "Mục đích sử dụng",
    "handover_id": "Bàn giao về Tài nguyên",
    "status_id": "Trạng thái",
    "note": "Ghi chú",
    "reviewed_at": "Ngày rà soát",
    "created": "Tạo mới",
    "imported": "Nhập từ Excel",
}
LIMITS: Mapping[str, int] = {
    "external_id": 200,
    "name": 300,
    "email": 200,
    "phone": 40,
    "link": 2100,
    "purpose": 4000,
    "note": 4000,
}


# --- inputs and outputs ---------------------------------------------------------------


@dataclass(slots=True)
class ResourceInput:
    """What a resource is made of, as the form sends it."""

    department_id: uuid.UUID
    type_id: uuid.UUID
    external_id: str
    name: str
    handover_id: uuid.UUID | None = None
    status_id: uuid.UUID | None = None
    link: str | None = None
    holder_user_id: uuid.UUID | None = None
    role_id: uuid.UUID | None = None
    email: str | None = None
    phone: str | None = None
    two_factor_id: uuid.UUID | None = None
    two_factor_holder_user_id: uuid.UUID | None = None
    origin_id: uuid.UUID | None = None
    started_on: date | None = None
    purpose: str | None = None
    note: str | None = None


@dataclass(slots=True)
class ResourceFilters:
    department_id: uuid.UUID | None = None
    type_id: uuid.UUID | None = None
    #: A person, or ``"none"`` for the resources held by nobody (at the Phòng TN).
    holder: str | None = None
    status_id: uuid.UUID | None = None
    handover_id: uuid.UUID | None = None
    employment_status: EmploymentStatus | None = None
    q: str | None = None
    alerts_only: bool = False
    mine: bool = False
    sort: str = "code"
    desc: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class Person:
    user_id: uuid.UUID
    full_name: str
    active: bool
    employment_status: EmploymentStatus
    #: The departments the person sits in, in order.
    departments: tuple[Department, ...] = ()

    @property
    def employment_status_label(self) -> str:
        return EMPLOYMENT_STATUS_LABELS[self.employment_status]


@dataclass(frozen=True, slots=True)
class ResourceRow:
    resource: Resource
    department: Department
    type: ResourceOption
    status: ResourceOption
    handover: ResourceOption
    role: ResourceOption | None
    two_factor: ResourceOption | None
    origin: ResourceOption | None
    holder: Person | None
    two_factor_holder: Person | None
    alerts: tuple[Alert, ...]

    @property
    def flagged(self) -> bool:
        return any(alert.level == "bad" for alert in self.alerts)

    @property
    def secret_warning(self) -> bool:
        return looks_like_secret(self.resource.note) or looks_like_secret(self.resource.purpose)


@dataclass(frozen=True, slots=True)
class ResourcePage:
    rows: list[ResourceRow]
    total: int
    review_days: int


@dataclass(frozen=True, slots=True)
class HandoverLine:
    row: ResourceHandover
    from_person: Person | None
    to_person: Person | None
    actor: Person | None


@dataclass(frozen=True, slots=True)
class StatusLine:
    row: ResourceStatusHistory
    from_status: ResourceOption | None
    to_status: ResourceOption
    actor: Person | None


@dataclass(frozen=True, slots=True)
class ChangeLine:
    row: ResourceChange
    actor: Person | None

    @property
    def field_label(self) -> str:
        return FIELD_LABELS.get(self.row.field, self.row.field)


@dataclass(frozen=True, slots=True)
class ResourceDetail:
    row: ResourceRow
    handovers: list[HandoverLine]
    statuses: list[StatusLine]
    changes: list[ChangeLine]
    can_edit: bool


@dataclass(frozen=True, slots=True)
class PersonLookup:
    person: Person
    holding: list[ResourceRow]
    held_before: list[ResourceRow]
    by_type: list[tuple[ResourceOption, int]]


@dataclass(slots=True)
class OptionCatalog:
    """Every option by id, and by (kind, code)."""

    by_id: dict[uuid.UUID, ResourceOption] = field(default_factory=dict)

    def get(self, option_id: uuid.UUID | None) -> ResourceOption | None:
        return None if option_id is None else self.by_id.get(option_id)

    def by_code(self, kind: OptionKind, code: str) -> ResourceOption | None:
        wanted = code.strip().upper()
        return next(
            (row for row in self.by_id.values() if row.kind is kind and row.code == wanted), None
        )

    def of_kind(self, kind: OptionKind, *, include_inactive: bool = False) -> list[ResourceOption]:
        rows = [
            row
            for row in self.by_id.values()
            if row.kind is kind and (include_inactive or row.active)
        ]
        return sorted(rows, key=lambda row: (row.sort_order, row.name))

    def group_of(self, status_id: uuid.UUID | None) -> StatusGroup | None:
        row = self.get(status_id)
        return None if row is None else row.status_group


#: Free text keeps its lines; a one-line field collapses its whitespace.
MULTILINE_FIELDS = ("note", "purpose")


def _text(value: str | None, *, field_name: str, required: bool = False) -> str | None:
    cleaned = (
        (value or "").strip()
        if field_name in MULTILINE_FIELDS or field_name == "link"
        else " ".join((value or "").split())
    )
    if not cleaned:
        if required:
            raise ResourceValidationError(
                f"Cần nhập {FIELD_LABELS[field_name].lower()}.",
                details={"reason": f"resource_{field_name}_missing", "field": field_name},
            )
        return None
    limit = LIMITS.get(field_name)
    if limit is not None and len(cleaned) > limit:
        raise ResourceValidationError(
            f"{FIELD_LABELS[field_name]} tối đa {limit} ký tự.",
            details={"reason": f"resource_{field_name}_too_long", "field": field_name},
        )
    if field_name == "link" and not cleaned.lower().startswith(("http://", "https://")):
        raise ResourceValidationError(
            "Link phải bắt đầu bằng http:// hoặc https://.",
            details={"reason": "resource_link_invalid", "field": "link"},
        )
    return cleaned


@dataclass(frozen=True, slots=True)
class LoadedPerson:
    user: User
    person: Person


def _person_of(user: User, departments: Sequence[Department] = ()) -> Person:
    return Person(
        user_id=user.id,
        full_name=user.full_name,
        active=bool(user.active) and user.deleted_at is None,
        employment_status=user.employment_status,
        departments=tuple(departments),
    )


class ResourceService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    @property
    def session(self) -> AsyncSession:
        return self._session

    # --- pick-lists -----------------------------------------------------------------

    async def ensure_seed_options(self) -> None:
        """The requirement's lists, created when the table is empty (migration
        0065 seeds the same; a database built from the models calls this)."""
        if await self._session.scalar(select(func.count()).select_from(ResourceOption)):
            return
        order: dict[OptionKind, int] = {}
        for kind, code, name, group in SEED_OPTIONS:
            order[kind] = order.get(kind, -1) + 1
            self._session.add(
                ResourceOption(
                    kind=kind, code=code, name=name, status_group=group, sort_order=order[kind]
                )
            )
        await self._session.flush()

    async def catalog(self) -> OptionCatalog:
        rows = (await self._session.scalars(select(ResourceOption))).all()
        return OptionCatalog(by_id={row.id: row for row in rows})

    async def create_option(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        kind: OptionKind,
        name: str,
        status_group: StatusGroup | None = None,
    ) -> ResourceOption:
        access.require("settings")
        cleaned = _option_name(name)
        if kind is OptionKind.STATUS and status_group is None:
            raise ResourceValidationError(
                "Mỗi trạng thái cần một nhóm (Bình thường / Cần chú ý / Mất / Đã đóng).",
                details={"reason": "resource_status_group_missing", "field": "status_group"},
            )
        catalog = await self.catalog()
        code = _option_code(
            cleaned, taken={row.code for row in catalog.of_kind(kind, include_inactive=True)}
        )
        order = (
            max(
                (row.sort_order for row in catalog.of_kind(kind, include_inactive=True)), default=-1
            )
            + 1
        )
        row = ResourceOption(
            kind=kind,
            code=code,
            name=cleaned,
            status_group=status_group if kind is OptionKind.STATUS else None,
            sort_order=order,
        )
        self._session.add(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_OPTION_CREATED,
            "resource_option",
            row.id,
            None,
            _option_snap(row),
        )
        return row

    async def update_option(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        option_id: uuid.UUID,
        name: str | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
        status_group: StatusGroup | None = None,
    ) -> ResourceOption:
        access.require("settings")
        row = await self._session.get(ResourceOption, option_id)
        if row is None:
            raise ResourceNotFoundError()
        before = _option_snap(row)
        if name is not None:
            row.name = _option_name(name)
        if active is not None:
            row.active = active
        if sort_order is not None:
            row.sort_order = sort_order
        if status_group is not None and row.kind is OptionKind.STATUS:
            row.status_group = status_group
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_OPTION_UPDATED,
            "resource_option",
            row.id,
            before,
            _option_snap(row),
        )
        return row

    async def reorder_options(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        kind: OptionKind,
        ids: Sequence[uuid.UUID],
    ) -> list[ResourceOption]:
        access.require("settings")
        catalog = await self.catalog()
        rows = {row.id: row for row in catalog.of_kind(kind, include_inactive=True)}
        if set(ids) != set(rows):
            raise ResourceValidationError(
                "Danh sách sắp xếp phải gồm đúng các lựa chọn của nhóm này.",
                details={"reason": "resource_option_order_invalid", "field": "ids"},
            )
        for position, option_id in enumerate(ids):
            rows[option_id].sort_order = position
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_OPTION_UPDATED,
            "resource_option_kind",
            None,
            None,
            {"kind": kind.value, "order": [str(item) for item in ids]},
        )
        return [rows[option_id] for option_id in ids]

    # --- settings -------------------------------------------------------------------

    async def review_days(self) -> int:
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        if row is None:
            return DEFAULT_REVIEW_DAYS
        value = row.value.get("review_days") if isinstance(row.value, dict) else None
        return int(value) if isinstance(value, int) and value > 0 else DEFAULT_REVIEW_DAYS

    async def set_review_days(
        self, *, access: ResourceAccess, actor: Actor, request_id: uuid.UUID, days: int
    ) -> int:
        access.require("settings")
        if days < 1 or days > MAX_REVIEW_DAYS:
            raise ResourceValidationError(
                f"Ngưỡng rà soát từ 1 đến {MAX_REVIEW_DAYS} ngày.",
                details={"reason": "resource_review_days_invalid", "field": "review_days"},
            )
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        before = await self.review_days()
        if row is None:
            row = SystemSetting(
                key=SETTINGS_KEY,
                value={"review_days": days},
                description="Quản lý tài nguyên: ngưỡng 'quá N ngày chưa rà soát'.",
                updated_by=actor.user_id,
            )
            self._session.add(row)
        else:
            row.value = {**(row.value or {}), "review_days": days}
            row.version = int(row.version) + 1
            row.updated_by = actor.user_id
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_SETTINGS_UPDATED,
            "resource_settings",
            None,
            {"review_days": before},
            {"review_days": days},
        )
        return days

    # --- reading --------------------------------------------------------------------

    def _scope(self, access: ResourceAccess) -> Any:
        """The SQL that keeps a listing inside what ``access`` may see."""
        if access.view_all:
            return None
        clauses: list[Any] = []
        if access.view_departments:
            clauses.append(Resource.department_id.in_(list(access.view_departments)))
        if access.view_held and access.user_id is not None:
            clauses.append(Resource.holder_user_id == access.user_id)
        if not clauses:
            raise ResourceForbiddenError("Bạn chưa được xem mục Tài nguyên.")
        return or_(*clauses)

    async def list_resources(
        self, access: ResourceAccess, filters: ResourceFilters
    ) -> ResourcePage:
        scope = self._scope(access)
        statement = select(Resource)
        if scope is not None:
            statement = statement.where(scope)
        if filters.department_id is not None:
            statement = statement.where(Resource.department_id == filters.department_id)
        if filters.type_id is not None:
            statement = statement.where(Resource.type_id == filters.type_id)
        if filters.status_id is not None:
            statement = statement.where(Resource.status_id == filters.status_id)
        if filters.handover_id is not None:
            statement = statement.where(Resource.handover_id == filters.handover_id)
        if filters.mine and access.user_id is not None:
            statement = statement.where(Resource.holder_user_id == access.user_id)
        if filters.holder:
            if filters.holder.lower() == "none":
                statement = statement.where(Resource.holder_user_id.is_(None))
            else:
                try:
                    statement = statement.where(
                        Resource.holder_user_id == uuid.UUID(filters.holder)
                    )
                except ValueError:
                    statement = statement.where(Resource.id.is_(None))
        if filters.employment_status is not None:
            statement = statement.join(User, User.id == Resource.holder_user_id).where(
                User.employment_status == filters.employment_status
            )
        if filters.q:
            needle = f"%{filters.q.strip()}%"
            statement = statement.where(
                or_(
                    Resource.code.ilike(needle),
                    Resource.external_id.ilike(needle),
                    Resource.name.ilike(needle),
                )
            )
        resources = (await self._session.scalars(statement)).all()
        rows = await self.rows_of(resources)
        if filters.alerts_only:
            rows = [row for row in rows if row.alerts]
        rows = _sort_rows(rows, filters.sort, filters.desc)
        total = len(rows)
        page = rows[filters.offset : filters.offset + filters.limit]
        return ResourcePage(rows=page, total=total, review_days=await self.review_days())

    async def rows_of(self, resources: Sequence[Resource]) -> list[ResourceRow]:
        """The rows of a listing, with everything a screen draws."""
        if not resources:
            return []
        catalog = await self.catalog()
        review_days = await self.review_days()
        departments = {
            row.id: row for row in (await self._session.scalars(select(Department))).all()
        }
        user_ids = {
            item
            for resource in resources
            for item in (resource.holder_user_id, resource.two_factor_holder_user_id)
            if item is not None
        }
        people = await self.people_by_id(user_ids)
        seats = await self._open_seats(
            [resource.holder_user_id for resource in resources if resource.holder_user_id]
        )
        now = utcnow()
        rows: list[ResourceRow] = []
        for resource in resources:
            holder = people.get(resource.holder_user_id) if resource.holder_user_id else None
            holder_user = holder.user if holder is not None else None
            seated = resource.holder_user_id is not None and resource.department_id in seats.get(
                resource.holder_user_id, set()
            )
            two_factor_holder = (
                people.get(resource.two_factor_holder_user_id)
                if resource.two_factor_holder_user_id
                else None
            )
            type_option = catalog.get(resource.type_id)
            status = catalog.get(resource.status_id)
            handover = catalog.get(resource.handover_id)
            department = departments.get(resource.department_id)
            if type_option is None or status is None or handover is None or department is None:
                # A row whose references were removed by hand: not drawable.
                continue
            rows.append(
                ResourceRow(
                    resource=resource,
                    department=department,
                    type=type_option,
                    status=status,
                    handover=handover,
                    role=catalog.get(resource.role_id),
                    two_factor=catalog.get(resource.two_factor_id),
                    origin=catalog.get(resource.origin_id),
                    holder=holder.person if holder is not None else None,
                    two_factor_holder=two_factor_holder.person
                    if two_factor_holder is not None
                    else None,
                    alerts=tuple(
                        alerts_for(
                            resource,
                            holder=holder_user,
                            holder_seated=seated,
                            status_group=catalog.group_of(resource.status_id),
                            review_days=review_days,
                            now=now,
                        )
                    ),
                )
            )
        return rows

    async def get(self, access: ResourceAccess, resource_id: uuid.UUID) -> ResourceDetail:
        resource = await self._visible(access, resource_id)
        rows = await self.rows_of([resource])
        if not rows:
            raise ResourceNotFoundError()
        people_ids: set[uuid.UUID] = set()
        handovers = (
            await self._session.scalars(
                select(ResourceHandover)
                .where(ResourceHandover.resource_id == resource.id)
                .order_by(ResourceHandover.created_at.desc(), ResourceHandover.id.desc())
            )
        ).all()
        statuses = (
            await self._session.scalars(
                select(ResourceStatusHistory)
                .where(ResourceStatusHistory.resource_id == resource.id)
                .order_by(ResourceStatusHistory.created_at.desc(), ResourceStatusHistory.id.desc())
            )
        ).all()
        changes = (
            await self._session.scalars(
                select(ResourceChange)
                .where(ResourceChange.resource_id == resource.id)
                .order_by(ResourceChange.created_at.desc(), ResourceChange.id.desc())
            )
        ).all()
        for line in handovers:
            people_ids.update(
                item for item in (line.from_user_id, line.to_user_id, line.actor_user_id) if item
            )
        for status_line in statuses:
            if status_line.actor_user_id:
                people_ids.add(status_line.actor_user_id)
        for change in changes:
            if change.actor_user_id:
                people_ids.add(change.actor_user_id)
        people = await self.people_by_id(people_ids)
        catalog = await self.catalog()

        def person(user_id: uuid.UUID | None) -> Person | None:
            found = people.get(user_id) if user_id else None
            return found.person if found is not None else None

        return ResourceDetail(
            row=rows[0],
            handovers=[
                HandoverLine(
                    row=line,
                    from_person=person(line.from_user_id),
                    to_person=person(line.to_user_id),
                    actor=person(line.actor_user_id),
                )
                for line in handovers
            ],
            statuses=[
                StatusLine(
                    row=line,
                    from_status=catalog.get(line.from_status_id),
                    to_status=catalog.get(line.to_status_id) or rows[0].status,
                    actor=person(line.actor_user_id),
                )
                for line in statuses
            ],
            changes=[ChangeLine(row=line, actor=person(line.actor_user_id)) for line in changes],
            can_edit=access.edit,
        )

    async def _visible(self, access: ResourceAccess, resource_id: uuid.UUID) -> Resource:
        scope = self._scope(access)
        statement = select(Resource).where(Resource.id == resource_id)
        if scope is not None:
            statement = statement.where(scope)
        row = await self._session.scalar(statement)
        if row is None:
            raise ResourceNotFoundError()
        return row

    # --- people ---------------------------------------------------------------------

    async def people_by_id(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, LoadedPerson]:
        wanted = list({item for item in user_ids if item is not None})
        if not wanted:
            return {}
        users = (await self._session.scalars(select(User).where(User.id.in_(wanted)))).all()
        departments = await self._departments_of(wanted)
        return {
            user.id: LoadedPerson(user=user, person=_person_of(user, departments.get(user.id, ())))
            for user in users
        }

    async def people(self, access: ResourceAccess, *, q: str | None = None) -> list[Person]:
        """Active accounts, for the pickers and the look-up: name, departments,
        employment status."""
        access.require_view()
        statement = (
            select(User)
            .where(User.deleted_at.is_(None), User.active.is_(True))
            .order_by(User.full_name, User.id)
        )
        if q:
            statement = statement.where(User.full_name.ilike(f"%{q.strip()}%"))
        users = (await self._session.scalars(statement)).all()
        departments = await self._departments_of([user.id for user in users])
        return [_person_of(user, departments.get(user.id, ())) for user in users]

    async def person_lookup(self, access: ResourceAccess, user_id: uuid.UUID) -> PersonLookup:
        """ "Tra cứu theo nhân viên": what one person holds and held."""
        if not (access.report or access.view_all or user_id == access.user_id):
            raise ResourceForbiddenError()
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise ResourceNotFoundError()
        departments = await self._departments_of([user.id])
        person = _person_of(user, departments.get(user.id, ()))
        holding = (
            await self._session.scalars(
                select(Resource).where(Resource.holder_user_id == user.id).order_by(Resource.code)
            )
        ).all()
        touched = (
            await self._session.scalars(
                select(ResourceHandover.resource_id).where(
                    or_(
                        ResourceHandover.from_user_id == user.id,
                        ResourceHandover.to_user_id == user.id,
                    )
                )
            )
        ).all()
        before_ids = set(touched) - {resource.id for resource in holding}
        held_before = (
            (
                await self._session.scalars(
                    select(Resource)
                    .where(Resource.id.in_(list(before_ids)))
                    .order_by(Resource.code)
                )
            ).all()
            if before_ids
            else []
        )
        holding_rows = await self.rows_of(holding)
        counts: dict[uuid.UUID, int] = {}
        for row in holding_rows:
            counts[row.type.id] = counts.get(row.type.id, 0) + 1
        catalog = await self.catalog()
        by_type = [
            (option, counts[option.id])
            for option in catalog.of_kind(OptionKind.TYPE, include_inactive=True)
            if option.id in counts
        ]
        return PersonLookup(
            person=person,
            holding=holding_rows,
            held_before=await self.rows_of(held_before),
            by_type=by_type,
        )

    async def _departments_of(
        self, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[Department]]:
        if not user_ids:
            return {}
        rows = (
            await self._session.execute(
                select(DepartmentMember.user_id, Department)
                .join(Department, Department.id == DepartmentMember.department_id)
                .where(
                    DepartmentMember.user_id.in_(list(set(user_ids))),
                    DepartmentMember.left_at.is_(None),
                    Department.active.is_(True),
                )
                .order_by(Department.sort_order, Department.code)
            )
        ).all()
        found: dict[uuid.UUID, list[Department]] = {}
        for user_id, department in rows:
            found.setdefault(user_id, []).append(department)
        return found

    async def _open_seats(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
        """The departments each person has an open seat in (active or not:
        a seat in a retired department still counts as "still there")."""
        wanted = list({item for item in user_ids if item})
        if not wanted:
            return {}
        rows = (
            await self._session.execute(
                select(DepartmentMember.user_id, DepartmentMember.department_id).where(
                    DepartmentMember.user_id.in_(wanted), DepartmentMember.left_at.is_(None)
                )
            )
        ).all()
        found: dict[uuid.UUID, set[uuid.UUID]] = {}
        for user_id, department_id in rows:
            found.setdefault(user_id, set()).add(department_id)
        return found

    # --- writing --------------------------------------------------------------------

    async def create(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        data: ResourceInput,
        code: str | None = None,
        reviewed_at: datetime | None = None,
        audit: bool = True,
        status_line: bool = True,
    ) -> Resource:
        """A new resource with its first history lines. ``code`` keeps an old
        code (the Excel import); otherwise one is allocated. ``status_line``
        off = the caller writes the status history itself (the import's
        month columns)."""
        access.require("edit")
        catalog = await self.catalog()
        values = await self._validate(data, catalog, exclude=None)
        department = values.pop("_department")
        if code is not None:
            code = code.strip().upper()
            if not is_resource_code(code):
                raise ResourceValidationError(
                    f"Mã tài nguyên {code!r} không đúng dạng ADS-001.",
                    details={"reason": "resource_code_invalid", "field": "code"},
                )
            if await self._session.scalar(select(Resource.id).where(Resource.code == code)):
                raise ResourceValidationError(
                    f"Mã tài nguyên {code} đã có.",
                    details={"reason": "resource_code_taken", "field": "code"},
                )
            await self._bump_counter(code)
        else:
            code = await self._allocate_code(department.code)
        now = utcnow()
        row = Resource(
            code=code,
            reviewed_at=reviewed_at or now,
            created_by_user_id=actor.user_id,
            **values,
        )
        self._session.add(row)
        await self._session.flush()
        if status_line:
            self._session.add(
                ResourceStatusHistory(
                    resource_id=row.id,
                    from_status_id=None,
                    to_status_id=row.status_id,
                    note="Tạo mới",
                    actor_user_id=actor.user_id,
                )
            )
        if row.holder_user_id is not None:
            self._session.add(
                ResourceHandover(
                    resource_id=row.id,
                    from_user_id=None,
                    to_user_id=row.holder_user_id,
                    reason=HandoverReason.NEW,
                    note="Tạo mới",
                    actor_user_id=actor.user_id,
                )
            )
        self._session.add(
            ResourceChange(
                resource_id=row.id,
                field="created",
                old_value=None,
                new_value=row.code,
                actor_user_id=actor.user_id,
            )
        )
        await self._session.flush()
        if audit:
            await self._record(
                actor,
                request_id,
                AuditAction.RESOURCE_CREATED,
                "resource",
                row.id,
                None,
                await self._snap(row),
            )
        return row

    async def update(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        resource_id: uuid.UUID,
        changes: Mapping[str, Any],
    ) -> Resource:
        """Edit the fields of a resource. The holder and the status have their
        own doors (they write their own histories); the code never changes."""
        access.require("edit")
        row = await self._visible(access, resource_id)
        catalog = await self.catalog()
        current = ResourceInput(
            **{item.name: getattr(row, item.name) for item in fields(ResourceInput)}
        )
        for key, value in changes.items():
            if key in ("holder_user_id", "status_id"):
                raise ResourceValidationError(
                    "Đổi người giữ và cập nhật trạng thái có nút riêng.",
                    details={"reason": "resource_field_not_editable", "field": key},
                )
            if not hasattr(current, key):
                raise ResourceValidationError(
                    f"Trường không hợp lệ: {key}.",
                    details={"reason": "resource_field_invalid", "field": key},
                )
            setattr(current, key, value)
        values = await self._validate(current, catalog, exclude=row.id)
        values.pop("_department")
        before = await self._snap(row)
        changed: list[tuple[str, Any, Any]] = []
        for key, value in values.items():
            old = getattr(row, key)
            if old != value:
                changed.append((key, old, value))
                setattr(row, key, value)
        if not changed:
            return row
        people = await self.people_by_id(
            [item for _k, old, new in changed for item in (old, new) if isinstance(item, uuid.UUID)]
        )
        for key, old, new in changed:
            self._session.add(
                ResourceChange(
                    resource_id=row.id,
                    field=key,
                    old_value=self._display(key, old, catalog, people),
                    new_value=self._display(key, new, catalog, people),
                    actor_user_id=actor.user_id,
                )
            )
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_UPDATED,
            "resource",
            row.id,
            before,
            await self._snap(row),
        )
        return row

    async def change_holder(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        resource_id: uuid.UUID,
        holder_user_id: uuid.UUID | None,
        reason: HandoverReason,
        note: str | None = None,
        handover_id: uuid.UUID | None = None,
    ) -> Resource:
        """ "Đổi người được bàn giao": one line in the handover history.
        ``None`` = back at the Phòng Tài nguyên."""
        access.require("edit")
        row = await self._visible(access, resource_id)
        catalog = await self.catalog()
        if holder_user_id is not None:
            await self._check_user(holder_user_id, field_name="holder_user_id")
        if (
            reason is HandoverReason.RETURN
            and holder_user_id is not None
            and holder_user_id == row.holder_user_id
        ):
            raise ResourceValidationError(
                "Thu hồi thì người giữ mới phải khác người đang giữ (hoặc để trống).",
                details={"reason": "resource_holder_unchanged", "field": "holder_user_id"},
            )
        if reason is not HandoverReason.RETURN and holder_user_id == row.holder_user_id:
            raise ResourceValidationError(
                "Người giữ mới trùng người đang giữ.",
                details={"reason": "resource_holder_unchanged", "field": "holder_user_id"},
            )
        before = await self._snap(row)
        people = await self.people_by_id(
            [item for item in (row.holder_user_id, holder_user_id) if item]
        )
        old_holder = row.holder_user_id
        self._session.add(
            ResourceHandover(
                resource_id=row.id,
                from_user_id=old_holder,
                to_user_id=holder_user_id,
                reason=reason,
                note=_text(note, field_name="note"),
                actor_user_id=actor.user_id,
            )
        )
        row.holder_user_id = holder_user_id
        self._session.add(
            ResourceChange(
                resource_id=row.id,
                field="holder_user_id",
                old_value=self._display("holder_user_id", old_holder, catalog, people),
                new_value=self._display("holder_user_id", holder_user_id, catalog, people),
                actor_user_id=actor.user_id,
            )
        )
        if handover_id is not None and handover_id != row.handover_id:
            option = self._option(catalog, handover_id, OptionKind.HANDOVER, "handover_id")
            self._session.add(
                ResourceChange(
                    resource_id=row.id,
                    field="handover_id",
                    old_value=self._display("handover_id", row.handover_id, catalog, people),
                    new_value=option.name,
                    actor_user_id=actor.user_id,
                )
            )
            row.handover_id = option.id
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_HOLDER_CHANGED,
            "resource",
            row.id,
            before,
            await self._snap(row),
        )
        return row

    async def update_status(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        resource_id: uuid.UUID,
        status_id: uuid.UUID,
        note: str | None = None,
    ) -> Resource:
        """ "Cập nhật trạng thái": one line in the status history, and the
        review date moves to now."""
        access.require("edit")
        row = await self._visible(access, resource_id)
        catalog = await self.catalog()
        option = self._option(catalog, status_id, OptionKind.STATUS, "status_id")
        before = await self._snap(row)
        now = utcnow()
        self._session.add(
            ResourceStatusHistory(
                resource_id=row.id,
                from_status_id=row.status_id,
                to_status_id=option.id,
                review_only=option.id == row.status_id,
                note=_text(note, field_name="note"),
                actor_user_id=actor.user_id,
            )
        )
        if option.id != row.status_id:
            self._session.add(
                ResourceChange(
                    resource_id=row.id,
                    field="status_id",
                    old_value=self._display("status_id", row.status_id, catalog, {}),
                    new_value=option.name,
                    actor_user_id=actor.user_id,
                )
            )
        row.status_id = option.id
        row.reviewed_at = now
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_STATUS_UPDATED,
            "resource",
            row.id,
            before,
            await self._snap(row),
        )
        return row

    async def mark_reviewed(
        self,
        *,
        access: ResourceAccess,
        actor: Actor,
        request_id: uuid.UUID,
        resource_id: uuid.UUID,
        note: str | None = None,
    ) -> Resource:
        """ "Đánh dấu đã rà soát": a line saying so, status unchanged."""
        access.require("edit")
        row = await self._visible(access, resource_id)
        before = await self._snap(row)
        row.reviewed_at = utcnow()
        self._session.add(
            ResourceStatusHistory(
                resource_id=row.id,
                from_status_id=row.status_id,
                to_status_id=row.status_id,
                review_only=True,
                note=_text(note, field_name="note") or "Đã rà soát",
                actor_user_id=actor.user_id,
            )
        )
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.RESOURCE_REVIEWED,
            "resource",
            row.id,
            before,
            await self._snap(row),
        )
        return row

    async def add_status_line(
        self,
        resource: Resource,
        *,
        from_status_id: uuid.UUID | None,
        to_status_id: uuid.UUID,
        at: datetime,
        actor: Actor,
        note: str | None,
    ) -> None:
        """A dated status line (the Excel import's month columns)."""
        self._session.add(
            ResourceStatusHistory(
                resource_id=resource.id,
                from_status_id=from_status_id,
                to_status_id=to_status_id,
                note=note,
                actor_user_id=actor.user_id,
                created_at=at,
            )
        )

    # --- helpers --------------------------------------------------------------------

    async def _validate(
        self, data: ResourceInput, catalog: OptionCatalog, *, exclude: uuid.UUID | None
    ) -> dict[str, Any]:
        department = await self._session.get(Department, data.department_id)
        if department is None or not department.active:
            raise ResourceValidationError(
                "Ban quản lý không hợp lệ.",
                details={"reason": "resource_department_invalid", "field": "department_id"},
            )
        values: dict[str, Any] = {"department_id": department.id, "_department": department}
        for field_name, kind in OPTION_FIELDS.items():
            option_id = getattr(data, field_name)
            if option_id is None:
                if field_name == "status_id":
                    option_id = _required_default(
                        catalog, OptionKind.STATUS, DEFAULT_STATUS_CODE
                    ).id
                elif field_name == "handover_id":
                    option_id = _required_default(
                        catalog, OptionKind.HANDOVER, DEFAULT_HANDOVER_CODE
                    ).id
                elif field_name == "type_id":
                    raise ResourceValidationError(
                        "Cần chọn loại tài nguyên.",
                        details={"reason": "resource_type_missing", "field": "type_id"},
                    )
                else:
                    values[field_name] = None
                    continue
            values[field_name] = self._option(catalog, option_id, kind, field_name).id
        for field_name in TEXT_FIELDS:
            values[field_name] = _text(
                getattr(data, field_name),
                field_name=field_name,
                required=field_name in ("external_id", "name"),
            )
        for field_name in USER_FIELDS:
            user_id = getattr(data, field_name)
            if user_id is not None:
                await self._check_user(user_id, field_name=field_name)
            values[field_name] = user_id
        values["started_on"] = data.started_on
        # Rule 3: one id per type.
        statement = select(Resource.id).where(
            Resource.type_id == values["type_id"], Resource.external_id == values["external_id"]
        )
        if exclude is not None:
            statement = statement.where(Resource.id != exclude)
        if await self._session.scalar(statement) is not None:
            raise ResourceValidationError(
                "ID này đã có trong cùng loại tài nguyên.",
                details={"reason": "resource_id_taken", "field": "external_id"},
            )
        return values

    @staticmethod
    def _option(
        catalog: OptionCatalog, option_id: uuid.UUID, kind: OptionKind, field_name: str
    ) -> ResourceOption:
        option = catalog.get(option_id)
        if option is None or option.kind is not kind:
            raise ResourceValidationError(
                f"Lựa chọn không hợp lệ cho {FIELD_LABELS[field_name].lower()}.",
                details={"reason": "resource_option_invalid", "field": field_name},
            )
        return option

    async def _check_user(self, user_id: uuid.UUID, *, field_name: str) -> User:
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise ResourceValidationError(
                "Không tìm thấy thành viên này.",
                details={"reason": "resource_holder_invalid", "field": field_name},
            )
        return user

    @staticmethod
    def _display(
        field_name: str, value: Any, catalog: OptionCatalog, people: Mapping[uuid.UUID, Any]
    ) -> str | None:
        if value is None:
            return None
        if field_name in OPTION_FIELDS:
            option = catalog.get(value)
            return option.name if option is not None else str(value)
        if field_name in USER_FIELDS:
            found = people.get(value)
            return found.person.full_name if found is not None else str(value)
        if field_name == "department_id":
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    async def _allocate_code(self, prefix: str) -> str:
        """``PREFIX-NNN``, from the prefix's own counter; gap-free and
        race-safe on PostgreSQL by one upsert, a read-then-write offline."""
        for _attempt in range(1000):
            number = await self._next_number(prefix)
            code = format_resource_code(prefix, number)
            # An imported old code may already sit on this number: skip it.
            if await self._session.scalar(select(Resource.id).where(Resource.code == code)) is None:
                return code
        raise ResourceValidationError(
            "Không cấp được mã tài nguyên.", details={"reason": "resource_code_exhausted"}
        )

    async def _next_number(self, prefix: str) -> int:
        if supports_row_locks(self._session):
            statement = (
                pg_insert(ResourceCodeCounter)
                .values(prefix=prefix, last_no=1)
                .on_conflict_do_update(
                    index_elements=[ResourceCodeCounter.prefix],
                    set_={"last_no": ResourceCodeCounter.last_no + 1},
                )
                .returning(ResourceCodeCounter.last_no)
            )
            return int((await self._session.execute(statement)).scalar_one())
        row = await self._session.get(ResourceCodeCounter, prefix)
        if row is None:
            self._session.add(ResourceCodeCounter(prefix=prefix, last_no=1))
            await self._session.flush()
            return 1
        number = int(row.last_no) + 1
        await self._session.execute(
            update(ResourceCodeCounter)
            .where(ResourceCodeCounter.prefix == prefix)
            .values(last_no=number)
        )
        await self._session.flush()
        return number

    async def _bump_counter(self, code: str) -> None:
        """An old code keeps its number: the prefix's counter moves past it."""
        prefix, _sep, digits = code.rpartition("-")
        number = int(digits)
        row = await self._session.get(ResourceCodeCounter, prefix)
        if row is None:
            self._session.add(ResourceCodeCounter(prefix=prefix, last_no=number))
        elif int(row.last_no) < number:
            await self._session.execute(
                update(ResourceCodeCounter)
                .where(ResourceCodeCounter.prefix == prefix)
                .values(last_no=number)
            )
        await self._session.flush()

    async def _snap(self, row: Resource) -> dict[str, Any]:
        return {
            "code": row.code,
            "department_id": str(row.department_id),
            "type_id": str(row.type_id),
            "external_id": row.external_id,
            "name": row.name,
            "holder_user_id": None if row.holder_user_id is None else str(row.holder_user_id),
            "status_id": str(row.status_id),
            "handover_id": str(row.handover_id),
            "reviewed_at": None if row.reviewed_at is None else row.reviewed_at.isoformat(),
        }

    async def record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        entity_type: str,
        entity_id: uuid.UUID | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        """One audit entry, on the caller's session."""
        await self._record(actor, request_id, action, entity_type, entity_id, before, after)

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        entity_type: str,
        entity_id: uuid.UUID | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            before_data=before,
            after_data=after,
        )


def _required_default(catalog: OptionCatalog, kind: OptionKind, code: str) -> ResourceOption:
    option = catalog.by_code(kind, code)
    if option is None:
        rows = catalog.of_kind(kind)
        if not rows:
            raise ResourceValidationError(
                "Chưa có lựa chọn nào trong danh sách này.",
                details={"reason": "resource_option_invalid", "field": kind.value.lower()},
            )
        return rows[0]
    return option


def _option_name(value: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned or len(cleaned) > 120:
        raise ResourceValidationError(
            "Tên lựa chọn từ 1 đến 120 ký tự.",
            details={"reason": "resource_option_name_invalid", "field": "name"},
        )
    return cleaned


def _option_code(name: str, *, taken: set[str]) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = "".join(character if character.isalnum() else "_" for character in folded.upper()).strip(
        "_"
    )
    base = "_".join(part for part in base.split("_") if part)[:32] or "OPTION"
    code = base
    suffix = 2
    while code in taken:
        code = f"{base[:28]}_{suffix}"
        suffix += 1
    return code


def _option_snap(row: ResourceOption) -> dict[str, Any]:
    return {
        "kind": row.kind.value,
        "code": row.code,
        "name": row.name,
        "status_group": None if row.status_group is None else row.status_group.value,
        "sort_order": row.sort_order,
        "active": row.active,
    }


def _sort_rows(rows: list[ResourceRow], sort: str, desc: bool) -> list[ResourceRow]:
    keys: dict[str, Any] = {
        "code": lambda row: row.resource.code,
        "name": lambda row: row.resource.name.lower(),
        "type": lambda row: (row.type.sort_order, row.type.name),
        "external_id": lambda row: row.resource.external_id,
        "department": lambda row: (row.department.sort_order, row.department.code),
        "holder": lambda row: (
            row.holder is None,
            row.holder.full_name.lower() if row.holder else "",
        ),
        "employee": lambda row: (
            row.holder is None,
            row.holder.employment_status.value if row.holder else "",
        ),
        "status": lambda row: (row.status.sort_order, row.status.name),
        "handover": lambda row: (row.handover.sort_order, row.handover.name),
        "reviewed_at": lambda row: (
            row.resource.reviewed_at is None,
            ensure_utc(row.resource.reviewed_at or datetime.min.replace(tzinfo=UTC)),
        ),
    }
    key = keys.get(sort, keys["code"])
    return sorted(rows, key=key, reverse=desc)


__all__ = [
    "FIELD_LABELS",
    "OPTION_FIELDS",
    "ChangeLine",
    "HandoverLine",
    "LoadedPerson",
    "OptionCatalog",
    "Person",
    "PersonLookup",
    "ResourceDetail",
    "ResourceFilters",
    "ResourceInput",
    "ResourcePage",
    "ResourceRow",
    "ResourceService",
    "StatusLine",
]
