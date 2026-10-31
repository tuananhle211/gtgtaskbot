"""One person's effective permissions, in every module, computed once.

Three layers, added together (:mod:`meobot.domain.org.modules`):

1. the **system role** - the OWNER and an ADMIN hold everything, everywhere;
2. the **department seats** - what each department grants the position the
   person holds there (``department_permissions``);
3. the **personal grants** - what somebody gave this person on top
   (``user_permissions``), while not revoked and not past its end date.

The result, :class:`Access`, is a flat list of :class:`EffectiveGrant` - one
per (module, permission, scope, source) - that every module's own view is
derived from: the order flow's :class:`~meobot.domain.orders.permissions.AdsPermissions`,
the resources module's :class:`~meobot.application.resources.access.ResourceAccess`,
the PR capability checks. It also answers the one question the personal
layer needs: :meth:`Access.manages` - may this person shape that person's
permissions in this module?

The PR module's three scoped review gates are not in here: they stay in
``pr_user_capabilities`` and are read by the PR capability service.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.seats import load_seats
from meobot.core.time import utcnow
from meobot.db.models.department import DepartmentPermission
from meobot.db.models.user_permission import UserPermission
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.models import DepartmentFunction, Position, Seat
from meobot.domain.org.modules import MODULES, CommonPermission

SOURCE_DEPARTMENT = "DEPARTMENT"
SOURCE_PERSONAL = "PERSONAL"
SOURCE_ROLE = "ROLE"


@dataclass(frozen=True, slots=True)
class EffectiveGrant:
    module: str
    permission: str
    #: ``OWN`` / ``ALL``.
    scope: str
    #: ``DEPARTMENT`` (from a seat) or ``PERSONAL`` (a ``user_permissions`` row).
    source: str
    seat: Seat | None = None
    grant_id: uuid.UUID | None = None
    granted_by_user_id: uuid.UUID | None = None
    effective_to: date | None = None
    note: str | None = None

    @property
    def function(self) -> DepartmentFunction:
        return self.seat.function if self.seat is not None else DepartmentFunction.NONE


@dataclass(frozen=True, slots=True)
class Access:
    user_id: uuid.UUID | None
    role: Role
    seats: tuple[Seat, ...] = ()
    grants: tuple[EffectiveGrant, ...] = ()

    @property
    def is_owner(self) -> bool:
        return self.role is Role.OWNER

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def sees_all(self) -> bool:
        """OWNER or ADMIN: every permission of every module, scope ``ALL``."""
        return self.is_owner or self.is_admin

    def heads(self, function: DepartmentFunction) -> bool:
        """Heads a department of this function (a Trưởng phòng PR, say)."""
        return any(
            seat.function is function and seat.position is Position.HEAD for seat in self.seats
        )

    def of(self, module: str) -> tuple[EffectiveGrant, ...]:
        return tuple(grant for grant in self.grants if grant.module == module)

    def scope(self, module: str, permission: str) -> str:
        """``ALL`` / ``OWN`` / ``NONE``: the widest scope held. An OWNER-only
        permission (``ModuleSpec.owner_only``) is not an ADMIN's by role."""
        if self.is_owner:
            return "ALL"
        if self.is_admin:
            spec = MODULES.get(module)
            if spec is None or permission not in spec.owner_only:
                return "ALL"
        found = "NONE"
        for grant in self.grants:
            if grant.module != module or grant.permission != permission:
                continue
            if grant.scope == "ALL":
                return "ALL"
            found = "OWN"
        return found

    def holds(self, module: str, permission: str) -> bool:
        return self.scope(module, permission) != "NONE"

    def allows(self, module: str, permission: str) -> bool:
        """Held with scope ``ALL``."""
        return self.scope(module, permission) == "ALL"

    def own_departments(self, module: str, permission: str) -> frozenset[uuid.UUID]:
        """The departments an ``OWN`` grant reaches as their head."""
        return frozenset(
            grant.seat.department_id
            for grant in self.grants
            if grant.module == module
            and grant.permission == permission
            and grant.scope == "OWN"
            and grant.seat is not None
            and grant.seat.position is Position.HEAD
        )

    def manages(self, module: str, target: Access) -> bool:
        """May this person grant / revoke ``module``'s permissions for ``target``?

        Asked by the PR screen's scoped review grants. The personal "Quyền"
        page is the OWNER's alone (2026-10-31, ``PersonalPermissionService``).

        * the OWNER: anybody;
        * an ADMIN: anybody but the OWNER;
        * ``MANAGE_PERMISSIONS: ALL``: anybody but OWNER / ADMIN and oneself;
        * ``MANAGE_PERMISSIONS: OWN``: the **members** (not heads) of a
          department one heads - never OWNER / ADMIN, never oneself.
        """
        if target.user_id is not None and target.user_id == self.user_id:
            return self.is_owner  # the OWNER may edit their own personal grants; nobody else
        if self.is_owner:
            return True
        if target.is_owner:
            return False
        if self.is_admin:
            return True
        if target.is_admin:
            return False
        manage = CommonPermission.MANAGE_PERMISSIONS.value
        scope = self.scope(module, manage)
        if scope == "ALL":
            return True
        if scope != "OWN":
            return False
        headed = self.own_departments(module, manage)
        return any(
            seat.department_id in headed and seat.position is not Position.HEAD
            for seat in target.seats
        )

    @classmethod
    def for_system(cls, actor: Actor) -> Access:
        """An actor with no user row (the bootstrap owner, the worker)."""
        return cls(user_id=actor.user_id, role=actor.role)


async def load_all_grants(
    session: AsyncSession, department_ids: Iterable[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[tuple[str, str, str]]]:
    """``(department, position) -> [(module, permission, scope), ...]`` for
    every module, in one query."""
    ids = list(set(department_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(DepartmentPermission).where(DepartmentPermission.department_id.in_(ids))
        )
    ).all()
    found: dict[tuple[uuid.UUID, str], list[tuple[str, str, str]]] = {}
    for row in rows:
        if row.scope != "NONE" and row.module in MODULES and MODULES[row.module].department_cells:
            found.setdefault((row.department_id, row.position.value), []).append(
                (row.module, row.permission, row.scope)
            )
    return found


async def load_personal_grants(
    session: AsyncSession, user_id: uuid.UUID, *, on: date | None = None
) -> Sequence[UserPermission]:
    """The personal grants in force today: not revoked, not past their end."""
    day = on or utcnow().date()
    return list(
        (
            await session.scalars(
                select(UserPermission)
                .where(
                    UserPermission.user_id == user_id,
                    UserPermission.revoked_at.is_(None),
                    or_(UserPermission.effective_to.is_(None), UserPermission.effective_to >= day),
                )
                .order_by(UserPermission.created_at)
            )
        ).all()
    )


async def load_access(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    role: Role,
    seats: Sequence[Seat] | None = None,
    on: date | None = None,
) -> Access:
    """Everything one person holds, from their seats and their personal grants."""
    if user_id is None:
        return Access(user_id=None, role=role)
    held = (
        tuple(seats)
        if seats is not None
        else (await load_seats(session, [user_id])).get(user_id, ())
    )
    grants: list[EffectiveGrant] = []
    if held:
        table = await load_all_grants(session, [seat.department_id for seat in held])
        for seat in held:
            for module, permission, scope in table.get(
                (seat.department_id, seat.position.value), ()
            ):
                grants.append(
                    EffectiveGrant(
                        module=module,
                        permission=permission,
                        scope=scope,
                        source=SOURCE_DEPARTMENT,
                        seat=seat,
                    )
                )
    for row in await load_personal_grants(session, user_id, on=on):
        if row.module not in MODULES:
            continue
        grants.append(
            EffectiveGrant(
                module=row.module,
                permission=row.permission,
                scope=row.scope,
                source=SOURCE_PERSONAL,
                grant_id=row.id,
                granted_by_user_id=row.granted_by_user_id,
                effective_to=row.effective_to,
                note=row.note,
            )
        )
    return Access(user_id=user_id, role=role, seats=held, grants=tuple(grants))


async def access_for(session: AsyncSession, actor: Actor) -> Access:
    """:func:`load_access` for an authenticated actor."""
    return await load_access(session, user_id=actor.user_id, role=actor.role)


@dataclass(slots=True)
class AccessCache:
    """Per-request memo: one load per person, however many checks."""

    session: AsyncSession
    found: dict[tuple[uuid.UUID | None, Role], Access] = field(default_factory=dict)

    async def for_actor(self, actor: Actor) -> Access:
        return await self.for_user(actor.user_id, actor.role)

    async def for_user(self, user_id: uuid.UUID | None, role: Role) -> Access:
        # Keyed by role too: a role changed mid-request must not read the old answer.
        key = (user_id, role)
        if key not in self.found:
            self.found[key] = await load_access(self.session, user_id=user_id, role=role)
        return self.found[key]

    def forget(self, user_id: uuid.UUID) -> None:
        for key in [key for key in self.found if key[0] == user_id]:
            del self.found[key]


__all__ = [
    "SOURCE_DEPARTMENT",
    "SOURCE_PERSONAL",
    "SOURCE_ROLE",
    "Access",
    "AccessCache",
    "EffectiveGrant",
    "access_for",
    "load_access",
    "load_all_grants",
    "load_personal_grants",
]
