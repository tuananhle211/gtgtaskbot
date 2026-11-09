"""Who uses "Chăm sóc khách hàng" (0094). See :mod:`meobot.domain.cskh.permissions`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from meobot.application.org.access import Access
from meobot.domain.cskh.errors import CskhForbiddenError
from meobot.domain.cskh.permissions import MODULE_CSKH, CskhPermission
from meobot.domain.units.models import UnitMembership


@dataclass(frozen=True, slots=True)
class CskhAccess:
    user_id: uuid.UUID | None
    #: Read the suggestions, send or dismiss them.
    reply: bool = False
    #: Wire the pages (the OWNER, or whom they ticked).
    manage: bool = False

    @property
    def sees(self) -> bool:
        return self.reply or self.manage

    def require_reply(self) -> None:
        if not self.sees:
            raise CskhForbiddenError()

    def require_manage(self) -> None:
        if not self.manage:
            raise CskhForbiddenError("Chỉ chủ sở hữu cấu hình kênh chăm sóc khách hàng.")


def cskh_access(membership: UnitMembership) -> CskhAccess:
    access = membership.access if isinstance(membership.access, Access) else None
    manage = membership.is_owner or bool(
        access and access.allows(MODULE_CSKH, CskhPermission.MANAGE.value)
    )
    reply = (
        manage
        or membership.sees_all
        or bool(access and access.allows(MODULE_CSKH, CskhPermission.REPLY.value))
    )
    return CskhAccess(user_id=membership.user_id, reply=reply, manage=manage)


__all__ = ["CskhAccess", "cskh_access"]
