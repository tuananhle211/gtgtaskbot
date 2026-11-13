"""Tài chính errors; ``details.reason`` = the code the web client branches on."""

from __future__ import annotations

from typing import Any

from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError


class FinanceForbiddenError(AuthorizationError):
    code = "finance_forbidden"

    def __init__(self, message: str = "Bạn chưa có quyền ở mục Tài chính.") -> None:
        super().__init__(message, details={"reason": self.code})


class FinanceNotFoundError(NotFoundError):
    def __init__(self, reason: str = "finance_not_found", message: str = "Không tìm thấy.") -> None:
        super().__init__(message, details={"reason": reason})


class FinanceInvalidError(ValidationError):
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


__all__ = ["FinanceForbiddenError", "FinanceInvalidError", "FinanceNotFoundError"]
