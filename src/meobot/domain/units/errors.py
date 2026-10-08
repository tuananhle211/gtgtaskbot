"""Errors of the units module. Subclasses of the core errors, so the API's
status map needs no new entry: a missing unit is a 404, a bad tag a 422, an
admin reaching outside their unit a 403."""

from __future__ import annotations

from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError


class UnitNotFoundError(NotFoundError):
    """No such unit, or one this actor may not see - the two read the same."""

    code = "unit_not_found"


class UnitValidationError(ValidationError):
    code = "unit_validation_error"


class UnitAccessDeniedError(AuthorizationError):
    """The actor is in the unit but may not administer it."""

    code = "unit_forbidden"


__all__ = ["UnitAccessDeniedError", "UnitNotFoundError", "UnitValidationError"]
