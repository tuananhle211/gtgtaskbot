"""Chăm sóc khách hàng errors; ``details.reason`` = the code the web client branches on."""

from __future__ import annotations

from typing import Any

from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError


class CskhForbiddenError(AuthorizationError):
    code = "cskh_forbidden"

    def __init__(self, message: str = "Bạn chưa có quyền ở mục Chăm sóc khách hàng.") -> None:
        super().__init__(message, details={"reason": self.code})


class CskhNotFoundError(NotFoundError):
    """A page / conversation / thread / row the caller may not see is "not found" too."""

    def __init__(self, reason: str = "cskh_not_found", message: str = "Không tìm thấy.") -> None:
        super().__init__(message, details={"reason": reason})


class CskhInvalidError(ValidationError):
    """A refused value: ``reason`` names it, ``field`` the form field."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        field: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"reason": reason, **(extra or {})}
        if field:
            details["field"] = field
        super().__init__(message, details=details)


class CskhConflictError(ConflictError):
    def __init__(self, reason: str, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message, details={"reason": reason, **(extra or {})})


class CskhUpstreamRefusedError(ValidationError):
    """Pancake / POS / the Zalo bridge refused: the message says what to do."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, details={"reason": code})


__all__ = [
    "CskhConflictError",
    "CskhForbiddenError",
    "CskhInvalidError",
    "CskhNotFoundError",
    "CskhUpstreamRefusedError",
]
