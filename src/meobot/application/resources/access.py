"""What a person may do in the resources module, from their seats.

The departments grant the module's permissions per position
(:mod:`meobot.domain.resources.models`); this reads a person's seats and
grants once per request and answers every question the service asks:

* ``view_all`` - every resource (``VIEW: ALL``, Ban Giám đốc, OWNER, ADMIN);
* ``view_held`` - what the person holds themselves (everybody, 2026-10-21);
* ``managed_people`` - the people attached to a head ("Trưởng quản lý"),
  whose resources they see, edit and update (``may_manage``);
* ``create`` - add a resource (the Phòng Tài nguyên);
* ``edit`` - edit EVERY resource and change any holder;
* ``import_excel`` / ``settings`` / ``report`` / ``hr`` - the
  module's other permissions, yes or no.

Somebody with no seat has nothing here (the OWNER and the ADMIN excepted).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import Access, load_access
from meobot.application.org.departments import reports_of
from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from meobot.domain.resources.models import MODULE_RESOURCES, ResourcePermission
from meobot.domain.units.models import UnitMembership


class ResourceForbiddenError(AuthorizationError):
    code = "resource_forbidden"

    def __init__(self, message: str = "Bạn không có quyền này trong mục Tài nguyên.") -> None:
        super().__init__(message, details={"reason": "resource_forbidden"})


class ResourceNotFoundError(NotFoundError):
    """No such resource - or one outside what this person may see. The same
    sentence either way: the person learns nothing about what they may not see."""

    code = "resource_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy.", details={"reason": "resource_not_found"})


class ResourceValidationError(ValidationError):
    code = "resource_validation_error"


@dataclass(frozen=True, slots=True)
class ResourceAccess:
    user_id: uuid.UUID | None
    view_all: bool = False
    view_held: bool = False
    #: 2026-10-21: the people attached to this head (seated in a department
    #: they head, with them as "Trưởng quản lý") - whose resources they see,
    #: edit and update.
    managed_people: frozenset[uuid.UUID] = frozenset()
    #: Add a resource ("Thêm tài nguyên"): the Phòng Tài nguyên.
    create: bool = False
    #: Delete a resource ("Xoá", 0071): the Phòng Tài nguyên.
    delete: bool = False
    #: Edit EVERY resource, and change any holder.
    edit: bool = False
    import_excel: bool = False
    settings: bool = False
    report: bool = False
    hr: bool = False

    @property
    def any_view(self) -> bool:
        return self.view_all or self.view_held

    @property
    def view_list(self) -> bool:
        """ "Danh sách tài nguyên" is worth a menu entry: more than one's own
        to look at (every resource, or a head's people's). A plain member has
        "Tài nguyên của tôi" only (2026-10-21)."""
        return self.view_all or self.edit or self.create or bool(self.managed_people)

    def require_view(self) -> None:
        if not self.any_view:
            raise ResourceForbiddenError("Bạn chưa được xem mục Tài nguyên.")

    def require(self, flag: str) -> None:
        if not getattr(self, flag):
            raise ResourceForbiddenError()

    def may_manage(self, *, holder_user_id: uuid.UUID | None, department_id: uuid.UUID) -> bool:
        """Edit, update the status of and review this resource: whoever may
        edit every one; anybody what they hold; a head what the people
        attached to them hold."""
        if self.edit:
            return True
        return holder_user_id is not None and (
            holder_user_id == self.user_id or holder_user_id in self.managed_people
        )


EVERYTHING = ResourceAccess(
    user_id=None,
    view_all=True,
    view_held=True,
    create=True,
    delete=True,
    edit=True,
    import_excel=True,
    settings=True,
    report=True,
    hr=True,
)

_FLAGS: tuple[tuple[ResourcePermission, str], ...] = (
    (ResourcePermission.CREATE, "create"),
    (ResourcePermission.DELETE, "delete"),
    (ResourcePermission.EDIT, "edit"),
    (ResourcePermission.IMPORT, "import_excel"),
    (ResourcePermission.SETTINGS, "settings"),
    (ResourcePermission.REPORT, "report"),
    (ResourcePermission.HR_STATUS, "hr"),
)


async def resource_access(session: AsyncSession, membership: UnitMembership) -> ResourceAccess:
    """What ``membership`` may do in the module, from its effective access
    (0066: seats' grants plus personal grants)."""
    if membership.sees_all:
        return replace(EVERYTHING, user_id=membership.user_id)
    if membership.user_id is None:
        return ResourceAccess(user_id=None)
    access = (
        membership.access
        if isinstance(membership.access, Access)
        else await load_access(session, user_id=membership.user_id, role=Role.EMPLOYEE)
    )
    view_all = False
    # 2026-10-21: everybody sees (and edits, updates) what they hold.
    held = True
    flags: dict[str, bool] = {attr: False for _permission, attr in _FLAGS}
    if any(seat.function is DepartmentFunction.BOARD for seat in access.seats):
        view_all = True
        flags["report"] = True
    for grant in access.of(MODULE_RESOURCES):
        if grant.permission == ResourcePermission.VIEW.value:
            if grant.scope == "ALL":
                view_all = True
            continue
        for permission, attr in _FLAGS:
            if grant.permission == permission.value and grant.scope == "ALL":
                flags[attr] = True
    # A head: only what the people attached to them hold ("Trưởng quản lý" =
    # them) - not their department's other resources, not unattached members'.
    heads = any(seat.position is Position.HEAD for seat in access.seats)
    managed = await reports_of(session, membership.user_id) if heads else frozenset()
    return ResourceAccess(
        user_id=membership.user_id,
        view_all=view_all,
        view_held=held,
        managed_people=managed,
        **flags,
    )


__all__ = [
    "EVERYTHING",
    "ResourceAccess",
    "ResourceForbiddenError",
    "ResourceNotFoundError",
    "ResourceValidationError",
    "resource_access",
]
