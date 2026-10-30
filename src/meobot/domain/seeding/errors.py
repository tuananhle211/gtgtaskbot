"""Errors of the seeding module (0085)."""

from __future__ import annotations

from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError


class SeedingForbiddenError(AuthorizationError):
    code = "seeding_forbidden"

    def __init__(self, message: str = "Bạn không có quyền ở mục Seeding.") -> None:
        super().__init__(message, details={"reason": "seeding_forbidden"})


class SeedingInvalidError(ValidationError):
    code = "seeding_invalid"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        details: dict[str, object] = {"reason": "seeding_invalid"}
        if field:
            details["field"] = field
        super().__init__(message, details=details)


class SeedingNotFoundError(NotFoundError):
    code = "seeding_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy.", details={"reason": "seeding_not_found"})


__all__ = ["SeedingForbiddenError", "SeedingInvalidError", "SeedingNotFoundError"]
