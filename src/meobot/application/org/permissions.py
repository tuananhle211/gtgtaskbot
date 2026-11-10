"""The personal layer (0066): one person's own rows, and the page that shows
everything a person holds.

**The OWNER and an ADMIN (2026-11-10)**: the OWNER reads and edits anybody's
page, an ADMIN anybody's but the OWNER's and their own; everybody else is
refused (403 ``permission_forbidden``). They tick or untick **every** box,
the defaults a department seat gives included: a personal row sets the
person's scope of that permission (0098: ``NONE`` takes a default away, see
:mod:`meobot.application.org.access`). What the system role itself holds
(the OWNER everything, an ADMIN all but the OWNER-only permissions) stays
locked - change the role instead. The OWNER-only permissions ("Trợ lý AI",
"Hệ số điểm", …) are ticked by an ADMIN too. Nobody sets
``MANAGE_PERMISSIONS`` here (it is set on "Phòng ban").

The PR module's three review gates are not granted here: they carry a
content-type and channel scope and go through the PR capability service
(``/api/pr/members/.../capabilities``), which still asks
:meth:`~meobot.application.org.access.Access.manages` (the PR head included).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.org.access import (
    SOURCE_PERSONAL,
    Access,
    AccessCache,
    EffectiveGrant,
    load_personal_grants,
    widest,
)
from meobot.application.org.seats import builtin_order_grants
from meobot.application.pr_capability_service import PrCapabilityGrant, PrCapabilityService
from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.department import Department
from meobot.db.models.user import User
from meobot.db.models.user_permission import UserPermission
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.models import MODULE_ORDER, MODULE_PR
from meobot.domain.org.modules import MODULES, CommonPermission, ModuleSpec, module_spec
from meobot.domain.pr.policy import PrCapability


class PermissionForbiddenError(AuthorizationError):
    code = "permission_forbidden"

    def __init__(
        self, message: str = "Bạn không được cấp hay thu hồi quyền của người này."
    ) -> None:
        super().__init__(message, details={"reason": "permission_forbidden"})


class PermissionValidationError(ValidationError):
    code = "permission_validation_error"


@dataclass(frozen=True, slots=True)
class EffectiveRow:
    """One permission of one module, as the person holds it: the widest
    scope and every source behind it."""

    module: str
    permission: str
    label: str
    scope: str
    sources: tuple[EffectiveGrant, ...]
    #: A PR review gate: the scoped grants behind it.
    gate_grants: tuple[PrCapabilityGrant, ...] = ()
    personal_only: bool = False
    #: Held by the system role itself - the OWNER everything, an ADMIN all
    #: but the OWNER-only permissions - so not ticked off on the page.
    by_role: bool = False
    #: What the seats (and the order flow's built-in grants) give: the default.
    inherited: str = "NONE"
    #: The scope a personal row sets (``None`` = the default stands).
    personal: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleOverview:
    module: str
    label: str
    rows: tuple[EffectiveRow, ...]
    #: The viewer may tick / untick this module's boxes for this person.
    can_manage: bool
    #: What the viewer may grant here: ``permission -> scopes``.
    grantable: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class PersonOverview:
    user: User
    access: Access
    departments: tuple[tuple[Department, str], ...]
    modules: tuple[ModuleOverview, ...]
    personal: Sequence[UserPermission]


class PersonalPermissionService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit
        self._cache = AccessCache(session)
        self._pr = PrCapabilityService(session, audit)

    async def _user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFoundError("Không tìm thấy.", details={"reason": "account_not_found"})
        return user

    async def _may_edit(self, actor: Actor, user: User) -> None:
        """The OWNER: anybody. An ADMIN: anybody but the OWNER and themselves."""
        mine = await self._cache.for_actor(actor)
        if mine.is_owner:
            return
        if mine.is_admin:
            if user.role is Role.OWNER:
                raise PermissionForbiddenError(
                    "Quản trị viên không sửa được phân quyền của chủ sở hữu."
                )
            if user.id == actor.user_id:
                raise PermissionForbiddenError("Không tự sửa phân quyền của chính mình.")
            return
        raise PermissionForbiddenError(
            "Chỉ chủ sở hữu và quản trị viên xem và sửa được phân quyền cá nhân."
        )

    async def overview(self, *, actor: Actor, user_id: uuid.UUID) -> PersonOverview:
        """Everything one person holds, module by module, with its sources -
        for the person's "Quyền" page. The OWNER's and an ADMIN's (not on the
        OWNER, not on themselves)."""
        user = await self._user(user_id)
        await self._may_edit(actor, user)
        theirs = await self._cache.for_user(user.id, user.role)
        subject = Actor(user_id=user.id, full_name=user.full_name, role=user.role)
        gates: dict[PrCapability, list[PrCapabilityGrant]] = {}
        for grant in await self._pr.approval_grants_for(subject):
            gates.setdefault(grant.capability, []).append(grant)
        modules: list[ModuleOverview] = []
        for spec in MODULES.values():
            rows: list[EffectiveRow] = []
            for key in spec.permission_keys():
                # The seats' grants as they stand before any override, then
                # the person's own rows.
                sources = tuple(
                    grant
                    for grant in (
                        *theirs.seat_grants,
                        *(item for item in theirs.grants if item.source == SOURCE_PERSONAL),
                    )
                    if grant.module == spec.module and grant.permission == key
                )
                gate_grants: tuple[PrCapabilityGrant, ...] = ()
                if spec.module == MODULE_PR and key in spec.personal_only:
                    gate_grants = tuple(gates.get(PrCapability(key), ()))
                    scope = "ALL" if theirs.sees_all else ("SCOPED" if gate_grants else "NONE")
                else:
                    scope = theirs.scope(spec.module, key)
                by_role = theirs.is_owner or (theirs.is_admin and key not in spec.owner_only)
                inherited = self._inherited(theirs, spec.module, key)
                personal = theirs.override(spec.module, key)
                if not by_role and key not in spec.personal_only:
                    # The order flow's built-in grants count like the seats'.
                    scope = personal if personal is not None else widest([scope, inherited])
                rows.append(
                    EffectiveRow(
                        module=spec.module,
                        permission=key,
                        label=spec.label_of(key),
                        scope=scope,
                        sources=sources,
                        gate_grants=gate_grants,
                        personal_only=key in spec.personal_only,
                        by_role=by_role,
                        inherited=inherited,
                        personal=personal,
                    )
                )
            modules.append(
                ModuleOverview(
                    module=spec.module,
                    label=spec.label,
                    rows=tuple(rows),
                    can_manage=True,
                    grantable=self._grantable(spec),
                )
            )
        departments = (
            (
                await self._session.execute(
                    select(Department).where(
                        Department.id.in_([seat.department_id for seat in theirs.seats])
                    )
                )
            )
            .scalars()
            .all()
            if theirs.seats
            else []
        )
        by_id = {row.id: row for row in departments}
        return PersonOverview(
            user=user,
            access=theirs,
            departments=tuple(
                (by_id[seat.department_id], seat.position.value)
                for seat in theirs.seats
                if seat.department_id in by_id
            ),
            modules=tuple(modules),
            personal=await load_personal_grants(self._session, user.id),
        )

    @staticmethod
    def _inherited(access: Access, module: str, permission: str) -> str:
        """The default: what the seats give (with the order flow's built-in
        grants), before any personal row."""
        scopes = [
            grant.scope
            for grant in access.seat_grants
            if grant.module == module and grant.permission == permission
        ]
        if module == MODULE_ORDER:
            scopes.extend(
                grant.scope
                for grant in builtin_order_grants(access.seats)
                if grant.permission == permission
            )
        return widest(scopes)

    @staticmethod
    def _grantable(spec: ModuleSpec) -> dict[str, list[str]]:
        """What may be ticked in a module: every permission with the scopes it
        takes, but ``MANAGE_PERMISSIONS`` and the PR review gates."""
        return {
            key: [scope for scope in ("OWN", "ALL") if scope in spec.allowed(key)]
            for key in spec.permission_keys()
            if key != CommonPermission.MANAGE_PERMISSIONS.value and key not in spec.personal_only
        }

    async def grant(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        module: str,
        permission: str,
        scope: str,
        note: str | None = None,
        effective_to: date | None = None,
    ) -> UserPermission:
        user = await self._user(user_id)
        await self._may_edit(actor, user)
        if module not in MODULES:
            raise PermissionValidationError(
                "Không có module này.",
                details={"reason": "permission_module_invalid", "field": "module"},
            )
        spec = module_spec(module)
        try:
            key = spec.permission(permission)
        except ValueError as error:
            raise PermissionValidationError(
                "Không có quyền này trong module.",
                details={"reason": "permission_invalid", "field": "permission"},
            ) from error
        if key in spec.personal_only:
            raise PermissionValidationError(
                "Quyền duyệt PR cấp theo phạm vi kênh / loại nội dung "
                "ở màn Thành viên & Phân quyền (PR).",
                details={"reason": "permission_use_pr_grant", "field": "permission"},
            )
        grantable = self._grantable(spec)
        if key not in grantable or (scope != "NONE" and scope not in grantable[key]):
            raise PermissionValidationError(
                "Quyền này không cấp riêng được với phạm vi đó.",
                details={"reason": "permission_not_grantable", "field": "permission"},
            )
        if effective_to is not None and effective_to < utcnow().date():
            raise PermissionValidationError(
                "Ngày hết hạn phải từ hôm nay trở đi.",
                details={"reason": "permission_expiry_past", "field": "effective_to"},
            )
        for existing in await load_personal_grants(self._session, user.id):
            if existing.module == module and existing.permission == key and existing.scope == scope:
                raise PermissionValidationError(
                    "Người này đã được cấp đúng quyền này.",
                    details={"reason": "permission_duplicate", "grant_id": str(existing.id)},
                )
        row = UserPermission(
            user_id=user.id,
            module=module,
            permission=key,
            scope=scope,
            note=" ".join((note or "").split()) or None,
            granted_by_user_id=actor.user_id,
            effective_to=effective_to,
        )
        self._session.add(row)
        await self._session.flush()
        self._cache.forget(user.id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_PERMISSION_GRANTED.value,
            result=AuditResult.SUCCESS,
            entity_type="user_permission",
            entity_id=str(row.id),
            after_data={
                "user_id": str(user.id),
                "module": module,
                "permission": key,
                "scope": scope,
                "effective_to": None if effective_to is None else effective_to.isoformat(),
            },
        )
        return row

    async def set_personal(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        module: str,
        wanted: Mapping[str, str | None],
    ) -> list[UserPermission]:
        """The checklist's save: ``permission -> scope`` the person should
        hold (``None`` = not ticked) for one module, the permissions sent only.
        Equal to the default (seats + built-in) -> no personal row; else one
        personal row with that scope (``NONE`` when a default is unticked).
        Returns the personal rows left."""
        user = await self._user(user_id)
        await self._may_edit(actor, user)
        if module not in MODULES:
            raise PermissionValidationError(
                "Không có module này.",
                details={"reason": "permission_module_invalid", "field": "module"},
            )
        spec = module_spec(module)
        theirs = await self._cache.for_user(user.id, user.role)
        grantable = self._grantable(spec)
        for permission, scope in wanted.items():
            if permission not in grantable:
                raise PermissionValidationError(
                    "Quyền này không sửa được ở đây.",
                    details={"reason": "permission_not_grantable", "field": permission},
                )
            target = scope or "NONE"
            if target != "NONE" and target not in grantable[permission]:
                raise PermissionValidationError(
                    "Phạm vi không hợp lệ.",
                    details={"reason": "permission_scope_invalid", "field": permission},
                )
            by_role = theirs.is_owner or (theirs.is_admin and permission not in spec.owner_only)
            if by_role:
                raise PermissionValidationError(
                    "Quyền này có sẵn theo vai trò hệ thống; muốn bớt thì đổi vai trò.",
                    details={"reason": "permission_held_by_role", "field": permission},
                )
            default = self._inherited(theirs, module, permission)
            current = [
                row
                for row in await load_personal_grants(self._session, user.id)
                if row.module == module and row.permission == permission
            ]
            if target == default:
                for row in current:
                    await self.revoke(actor=actor, request_id=request_id, grant_id=row.id)
                continue
            if len(current) == 1 and current[0].scope == target:
                continue
            for row in current:
                await self.revoke(actor=actor, request_id=request_id, grant_id=row.id)
            await self.grant(
                actor=actor,
                request_id=request_id,
                user_id=user.id,
                module=module,
                permission=permission,
                scope=target,
            )
            theirs = await self._cache.for_user(user.id, user.role)
        return [
            row
            for row in await load_personal_grants(self._session, user.id)
            if row.module == module
        ]

    async def revoke(
        self, *, actor: Actor, request_id: uuid.UUID, grant_id: uuid.UUID
    ) -> UserPermission:
        row = await self._session.get(UserPermission, grant_id)
        if row is None or row.revoked_at is not None:
            raise NotFoundError(
                "Không tìm thấy quyền này.", details={"reason": "permission_not_found"}
            )
        user = await self._user(row.user_id)
        await self._may_edit(actor, user)
        row.revoked_at = utcnow()
        row.revoked_by_user_id = actor.user_id
        await self._session.flush()
        self._cache.forget(user.id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_PERMISSION_REVOKED.value,
            result=AuditResult.SUCCESS,
            entity_type="user_permission",
            entity_id=str(row.id),
            before_data={
                "user_id": str(user.id),
                "module": row.module,
                "permission": row.permission,
                "scope": row.scope,
            },
            after_data={"revoked_at": row.revoked_at.isoformat()},
        )
        return row


__all__ = [
    "EffectiveRow",
    "ModuleOverview",
    "PermissionForbiddenError",
    "PermissionValidationError",
    "PersonOverview",
    "PersonalPermissionService",
]
