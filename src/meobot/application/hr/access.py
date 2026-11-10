"""Who may see and change whose HR profile (module ``HR``, 0096).

* everybody: their own whole profile;
* ``VIEW OWN`` (every Trưởng phòng by default): the work information of the
  people seated in the departments they head;
* ``VIEW ALL`` / ``EDIT`` (Phòng HCNS): every profile's work information;
  ``EDIT`` also changes them;
* ``SENSITIVE`` (HCNS + the OWNER - Q11): ID number, phone, birthday,
  personal email, addresses, bank account, pay, insurance and tax;
* ``EXPORT``: the Excel file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import Access, load_access
from meobot.application.org.departments import department_people
from meobot.core.errors import AuthorizationError, NotFoundError
from meobot.domain.hr.profiles import MODULE_HR, HrPermission
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMembership


class HrForbiddenError(AuthorizationError):
    code = "hr_forbidden"

    def __init__(self, message: str = "Bạn không có quyền với hồ sơ nhân sự này.") -> None:
        super().__init__(message, details={"reason": self.code})


class HrNotFoundError(NotFoundError):
    code = "hr_not_found"

    def __init__(self, message: str = "Không tìm thấy hồ sơ.") -> None:
        super().__init__(message, details={"reason": self.code})


@dataclass(frozen=True, slots=True)
class HrAccess:
    user_id: uuid.UUID | None
    view_all: bool = False
    edit: bool = False
    sensitive: bool = False
    export: bool = False
    #: ``VIEW OWN``: the people of the departments one heads.
    team: frozenset[uuid.UUID] = field(default_factory=frozenset)

    @property
    def lists(self) -> bool:
        """May open "Quản lý hồ sơ" (anybody beyond oneself)."""
        return self.view_all or bool(self.team - {self.user_id})

    def may_view(self, user_id: uuid.UUID) -> bool:
        return self.view_all or user_id == self.user_id or user_id in self.team

    def sees_sensitive(self, user_id: uuid.UUID) -> bool:
        return user_id == self.user_id or (self.sensitive and self.may_view(user_id))

    def require_view(self, user_id: uuid.UUID) -> None:
        if not self.may_view(user_id):
            raise HrNotFoundError()

    def require_edit(self) -> None:
        if not self.edit:
            raise HrForbiddenError("Chỉ phòng Hành chính nhân sự sửa được hồ sơ.")


async def hr_access(session: AsyncSession, membership: UnitMembership) -> HrAccess:
    if membership.user_id is None:
        return HrAccess(user_id=None)
    access = (
        membership.access
        if isinstance(membership.access, Access)
        else await load_access(session, user_id=membership.user_id, role=Role.EMPLOYEE)
    )

    def scope(permission: HrPermission) -> str | None:
        return access.scope(MODULE_HR, permission.value)

    edit = scope(HrPermission.EDIT) == "ALL"
    view = scope(HrPermission.VIEW)
    view_all = edit or view == "ALL"
    team = (
        await department_people(session, membership.user_id)
        if view == "OWN" and not view_all
        else frozenset()
    )
    return HrAccess(
        user_id=membership.user_id,
        view_all=view_all,
        edit=edit,
        sensitive=scope(HrPermission.SENSITIVE) == "ALL",
        export=scope(HrPermission.EXPORT) == "ALL",
        team=team,
    )


__all__ = ["HrAccess", "HrForbiddenError", "HrNotFoundError", "hr_access"]
