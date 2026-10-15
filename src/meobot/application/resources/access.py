"""What a person may do in the resources module, from their seats.

The departments grant the module's permissions per position
(:mod:`meobot.domain.resources.models`); this reads a person's seats and
grants once per request and answers every question the service asks:

* ``view_all`` - every resource (``VIEW: ALL``, Ban Giám đốc, OWNER, ADMIN);
* ``view_departments`` - the departments whose resources a head with
  ``VIEW: OWN`` sees;
* ``view_held`` - what the person holds themselves (any ``VIEW: OWN``);
* ``edit`` / ``import_excel`` / ``settings`` / ``report`` / ``hr`` - the
  module's other permissions, yes or no.

Somebody with no seat has nothing here (the OWNER and the ADMIN excepted).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.seats import load_grants
from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError
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
    view_departments: frozenset[uuid.UUID] = frozenset()
    view_held: bool = False
    edit: bool = False
    import_excel: bool = False
    settings: bool = False
    report: bool = False
    hr: bool = False

    @property
    def any_view(self) -> bool:
        return self.view_all or self.view_held or bool(self.view_departments)

    def require_view(self) -> None:
        if not self.any_view:
            raise ResourceForbiddenError("Bạn chưa được xem mục Tài nguyên.")

    def require(self, flag: str) -> None:
        if not getattr(self, flag):
            raise ResourceForbiddenError()


EVERYTHING = ResourceAccess(
    user_id=None,
    view_all=True,
    view_held=True,
    edit=True,
    import_excel=True,
    settings=True,
    report=True,
    hr=True,
)

_FLAGS: tuple[tuple[ResourcePermission, str], ...] = (
    (ResourcePermission.EDIT, "edit"),
    (ResourcePermission.IMPORT, "import_excel"),
    (ResourcePermission.SETTINGS, "settings"),
    (ResourcePermission.REPORT, "report"),
    (ResourcePermission.HR_STATUS, "hr"),
)


async def resource_access(session: AsyncSession, membership: UnitMembership) -> ResourceAccess:
    """What ``membership`` may do in the module."""
    if membership.sees_all:
        return replace(EVERYTHING, user_id=membership.user_id)
    seats = membership.seats
    if not seats or membership.user_id is None:
        return ResourceAccess(user_id=membership.user_id)
    table = await load_grants(session, MODULE_RESOURCES, [seat.department_id for seat in seats])
    view_all = False
    departments: set[uuid.UUID] = set()
    held = False
    flags: dict[str, bool] = {attr: False for _permission, attr in _FLAGS}
    for seat in seats:
        cells = dict(table.get((seat.department_id, seat.position.value), ()))
        if seat.function is DepartmentFunction.BOARD:
            view_all = True
            flags["report"] = True
        scope = cells.get(ResourcePermission.VIEW.value)
        if scope == "ALL":
            view_all = True
        elif scope == "OWN":
            held = True
            if seat.position is Position.HEAD:
                departments.add(seat.department_id)
        for permission, attr in _FLAGS:
            if cells.get(permission.value) == "ALL":
                flags[attr] = True
    return ResourceAccess(
        user_id=membership.user_id,
        view_all=view_all,
        view_departments=frozenset(departments),
        view_held=held,
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
