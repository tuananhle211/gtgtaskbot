"""Who uses "Chăm sóc khách hàng" (0102). See :mod:`meobot.domain.cskh.permissions`.

``INBOX`` / ``ZALO`` are ``NONE`` / ``OWN`` / ``ALL``; ``OWN`` reaches the pages
one is a member of (``cskh_page_members``) / the Zalo nicks handed to one. A
``MANAGE`` holder sees every page and nick too. OWNER / ADMIN everything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import Access
from meobot.db.models.cskh import CskhPageMember, CskhZaloAccount
from meobot.domain.cskh.errors import CskhForbiddenError
from meobot.domain.cskh.permissions import MODULE_CSKH, CskhPermission
from meobot.domain.units.models import UnitMembership


@dataclass(frozen=True, slots=True)
class CskhAccess:
    user_id: uuid.UUID | None
    #: ``NONE`` / ``OWN`` / ``ALL``.
    inbox: str = "NONE"
    zalo: str = "NONE"
    manage: bool = False

    @property
    def sees_inbox(self) -> bool:
        return self.inbox != "NONE"

    @property
    def sees_zalo(self) -> bool:
        return self.zalo != "NONE"

    @property
    def sees(self) -> bool:
        return self.sees_inbox or self.sees_zalo

    def require_inbox(self) -> None:
        if not self.sees_inbox:
            raise CskhForbiddenError("Bạn chưa có quyền ở Hộp chat page.")

    def require_zalo(self) -> None:
        if not self.sees_zalo:
            raise CskhForbiddenError("Bạn chưa có quyền ở Hộp chat Zalo.")

    def require_manage(self) -> None:
        if not self.manage:
            raise CskhForbiddenError("Chỉ người quản trị hộp chat làm được việc này.")

    def require_any(self) -> None:
        if not self.sees:
            raise CskhForbiddenError()


def cskh_access(membership: UnitMembership) -> CskhAccess:
    access = membership.access if isinstance(membership.access, Access) else None

    def scope(permission: CskhPermission) -> str:
        if membership.sees_all:
            return "ALL"
        return access.scope(MODULE_CSKH, permission.value) if access else "NONE"

    manage = membership.sees_all or scope(CskhPermission.MANAGE) == "ALL"
    inbox = "ALL" if manage else scope(CskhPermission.INBOX)
    zalo = "ALL" if manage else scope(CskhPermission.ZALO)
    return CskhAccess(user_id=membership.user_id, inbox=inbox, zalo=zalo, manage=manage)


async def page_scope(session: AsyncSession, access: CskhAccess) -> set[str] | None:
    """None = every page; else the page ids the caller's inbox reaches."""
    if access.inbox == "ALL":
        return None
    if access.inbox != "OWN" or access.user_id is None:
        return set()
    rows = await session.scalars(
        select(CskhPageMember.page_id).where(CskhPageMember.user_id == access.user_id)
    )
    return set(rows.all())


async def zalo_scope(session: AsyncSession, access: CskhAccess) -> set[str] | None:
    """None = every nick; else the nicks handed to the caller."""
    if access.zalo == "ALL":
        return None
    if access.zalo != "OWN" or access.user_id is None:
        return set()
    rows = await session.scalars(
        select(CskhZaloAccount.id).where(CskhZaloAccount.user_id == access.user_id)
    )
    return set(rows.all())


__all__ = ["CskhAccess", "cskh_access", "page_scope", "zalo_scope"]
