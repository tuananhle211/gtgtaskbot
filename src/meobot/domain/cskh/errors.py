"""Chăm sóc khách hàng errors; ``details.reason`` = the code the web client branches on."""

from __future__ import annotations

from typing import Any

from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError


class CskhForbiddenError(AuthorizationError):
    code = "cskh_forbidden"

    def __init__(self, message: str = "Bạn chưa có quyền ở mục Chăm sóc khách hàng.") -> None:
        super().__init__(message, details={"reason": self.code})


class CskhPageNotFoundError(NotFoundError):
    code = "cskh_page_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy page.", details={"reason": self.code})


class CskhReplyNotFoundError(NotFoundError):
    code = "cskh_reply_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy tin trả lời.", details={"reason": self.code})


class CskhInvalidError(ValidationError):
    """A refused value: ``reason`` names it, ``field`` the form field."""

    def __init__(self, reason: str, message: str, *, field: str | None = None) -> None:
        details: dict[str, Any] = {"reason": reason}
        if field:
            details["field"] = field
        super().__init__(message, details=details)


class CskhReplyDecidedError(ConflictError):
    code = "cskh_reply_decided"

    def __init__(self) -> None:
        super().__init__(
            "Tin này đã được xử lý (gửi, bỏ qua hoặc không còn chờ duyệt).",
            details={"reason": self.code},
        )


class CskhPancakeError(ValidationError):
    """Pancake refused (token, 24 h window...): the message says what to do."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, details={"reason": code})


__all__ = [
    "CskhForbiddenError",
    "CskhInvalidError",
    "CskhPageNotFoundError",
    "CskhPancakeError",
    "CskhReplyDecidedError",
    "CskhReplyNotFoundError",
]
