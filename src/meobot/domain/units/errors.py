"""Errors of the units module. Subclasses of the core errors, so the API's
status map needs no new entry: a missing unit is a 404, a bad tag a 422, an
admin reaching outside their unit a 403."""

from __future__ import annotations

from typing import TYPE_CHECKING

from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError

if TYPE_CHECKING:
    from meobot.domain.units.models import UnitCode


class UnitNotFoundError(NotFoundError):
    """No such unit, or one this actor may not see - the two read the same."""

    code = "unit_not_found"


class UnitValidationError(ValidationError):
    code = "unit_validation_error"


class UnitAccessDeniedError(AuthorizationError):
    """The actor is in the unit but may not administer it."""

    code = "unit_forbidden"


class UnitTagForbiddenError(AuthorizationError):
    """The actor sees the stream but may not tag or untag this person in it. 403.

    ``code`` and ``details.reason`` are both ``unit_tag_forbidden``.
    """

    code = "unit_tag_forbidden"

    def __init__(
        self,
        unit: UnitCode | None = None,
        message: str = "Bạn không có quyền gắn hoặc gỡ thành viên ở luồng này.",
    ) -> None:
        details: dict[str, str] = {"reason": self.code}
        if unit is not None:
            details["unit"] = unit.value
        super().__init__(message, details=details)


__all__ = [
    "UnitAccessDeniedError",
    "UnitNotFoundError",
    "UnitTagForbiddenError",
    "UnitValidationError",
]
