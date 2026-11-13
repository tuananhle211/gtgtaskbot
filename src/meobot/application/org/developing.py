"""Modules still being built ("Tính năng đang phát triển", user 13/11/2026).

A module listed here is hidden from everybody but the OWNER: no menu, no page,
its API answers 403, and the permission screens (Phòng ban, a person's
"Quyền") leave it out - whatever grants exist stay in the database and come
back when the module is released. The list is ``system_settings``
``developing_modules`` (``{"modules": [...]}``), seeded by 0105 with
``FINANCE``, and only the OWNER changes it (``PUT
/api/permissions/developing-modules``).

Only the modules in :data:`DEVELOPABLE` honour the switch (each one's access
check asks :func:`hidden_modules`); a new module joins by doing the same.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.org.access import request_memo
from meobot.core.errors import AuthorizationError, ValidationError
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.permissions import MODULE_FINANCE
from meobot.domain.identity.models import Actor, Role

SETTINGS_KEY = "developing_modules"
#: The modules that can be hidden this way, with their names.
DEVELOPABLE = MappingProxyType({MODULE_FINANCE: "Tài chính"})


class DevelopingForbiddenError(AuthorizationError):
    code = "developing_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu đổi được tính năng đang phát triển.", details={"reason": self.code}
        )


class DevelopingInvalidError(ValidationError):
    code = "developing_invalid"

    def __init__(self, module: str) -> None:
        super().__init__(
            f"Module {module} không có trong danh sách.", details={"reason": self.code}
        )


async def developing_modules(session: AsyncSession) -> frozenset[str]:
    """The modules currently hidden from everybody but the OWNER."""
    memo = request_memo(session, SETTINGS_KEY)
    if "value" not in memo:
        row = await session.scalar(select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY))
        modules = (row.value or {}).get("modules") if row is not None else []
        memo["value"] = frozenset(str(item) for item in modules or [] if item in DEVELOPABLE)
    found: frozenset[str] = memo["value"]
    return found


async def hidden_modules(session: AsyncSession, role: Role | None) -> frozenset[str]:
    """What ``role`` may not see: the developing modules, unless OWNER."""
    if role is Role.OWNER:
        return frozenset()
    return await developing_modules(session)


async def set_developing(
    session: AsyncSession,
    *,
    actor: Actor,
    request_id: uuid.UUID,
    modules: Iterable[str],
) -> frozenset[str]:
    if actor.role is not Role.OWNER:
        raise DevelopingForbiddenError()
    wanted = frozenset(modules)
    for module in wanted:
        if module not in DEVELOPABLE:
            raise DevelopingInvalidError(module)
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY))
    before = sorted((row.value or {}).get("modules") or []) if row is not None else []
    value: dict[str, object] = {"modules": sorted(wanted)}
    if row is None:
        session.add(
            SystemSetting(
                key=SETTINGS_KEY,
                value=value,
                description="Module đang phát triển: chỉ chủ sở hữu thấy.",
                updated_by=actor.user_id,
            )
        )
    else:
        row.value = value
        row.version = int(row.version) + 1
        row.updated_by = actor.user_id
    await session.flush()
    await AuditService(session).record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.DEVELOPING_MODULES_UPDATED.value,
        result=AuditResult.SUCCESS,
        entity_type="system_setting",
        entity_id=SETTINGS_KEY,
        before_data={"modules": before},
        after_data={"modules": value["modules"]},
    )
    return wanted


__all__ = [
    "DEVELOPABLE",
    "SETTINGS_KEY",
    "DevelopingForbiddenError",
    "DevelopingInvalidError",
    "developing_modules",
    "hidden_modules",
    "set_developing",
]
