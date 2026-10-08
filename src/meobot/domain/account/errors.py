"""Errors of the account module (password login, profile, member statistics).

Subclasses of the core errors, so the API's status map needs only one new
entry - :class:`LoginLockedError` is a 429. Every error carries its specific
reason twice: as ``code`` and as ``details.reason``, so a client may branch on
either, the way ``/api/orders`` clients read ``details.reason``.
"""

from __future__ import annotations

from typing import Any

from meobot.core.errors import (
    AuthorizationError,
    ConflictError,
    MeoBotError,
    NotFoundError,
    ValidationError,
)

#: The one sentence every failed login reads - unknown id, wrong password,
#: deactivated account alike. Telling them apart would tell a stranger which
#: Telegram ids have accounts.
LOGIN_FAILED_MESSAGE = "Sai ID Telegram hoặc mật khẩu."
LOGIN_LOCKED_MESSAGE = "Đăng nhập sai quá nhiều lần. Vui lòng thử lại sau ít phút."
#: A sentence, not a secret.
PASSWORD_CHANGE_REQUIRED_MESSAGE = "Bạn cần đổi mật khẩu mặc định trước khi tiếp tục."  # noqa: S105


def _with_reason(reason: str, details: dict[str, Any] | None) -> dict[str, Any]:
    return {**(details or {}), "reason": reason}


class LoginFailedError(AuthorizationError):
    """Rendered by the login route itself as a 401 (it must commit the counter)."""

    code = "login_failed"

    def __init__(self) -> None:
        super().__init__(LOGIN_FAILED_MESSAGE, details={"reason": self.code})


class LoginLockedError(MeoBotError):
    """Too many consecutive failures: password login is refused for a while. 429."""

    code = "login_locked"

    def __init__(self) -> None:
        super().__init__(LOGIN_LOCKED_MESSAGE, details={"reason": self.code})


class PasswordChangeRequiredError(AuthorizationError):
    """A default-password session asked for something other than changing it. 403."""

    code = "password_change_required"

    def __init__(self) -> None:
        super().__init__(PASSWORD_CHANGE_REQUIRED_MESSAGE, details={"reason": self.code})


class PasswordRejectedError(ValidationError):
    """The new or current password failed a rule. 422, ``code`` = the reason.

    ``password_empty``, ``password_too_long``, ``password_is_default`` or
    ``current_password_wrong``.
    """

    code = "password_rejected"

    def __init__(self, reason: str, message: str, *, field: str) -> None:
        super().__init__(message, details=_with_reason(reason, {"field": field}))
        self.code = reason


class AccountValidationError(ValidationError):
    """Bad profile or query input. 422, ``code`` = the reason."""

    code = "account_validation_error"

    def __init__(self, reason: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=_with_reason(reason, details))
        self.code = reason


class AccountMembersForbiddenError(AuthorizationError):
    """The member list (or a reset) is not for this person. 403."""

    code = "account_members_forbidden"

    def __init__(self, message: str = "Bạn không có quyền xem danh sách thành viên.") -> None:
        super().__init__(message, details={"reason": self.code})


class PasswordResetForbiddenError(AuthorizationError):
    """This person may not reset that account's password. 403."""

    code = "password_reset_forbidden"

    def __init__(self, message: str = "Bạn không có quyền đặt lại mật khẩu tài khoản này.") -> None:
        super().__init__(message, details={"reason": self.code})


class AccountStatusForbiddenError(AuthorizationError):
    """Deactivate/reactivate refused: not OWNER/ADMIN, oneself, or an OWNER. 403."""

    code = "account_status_forbidden"

    def __init__(
        self, message: str = "Bạn không có quyền thay đổi trạng thái tài khoản này."
    ) -> None:
        super().__init__(message, details={"reason": self.code})


class PasswordResetUndeliverableError(ConflictError):
    """An admin reset for somebody MeoBot cannot message privately. 409.

    The temporary password travels only by Telegram DM; without a private chat
    there is nowhere to send it, so nothing is changed.
    """

    code = "password_reset_undeliverable"

    def __init__(self) -> None:
        super().__init__(
            "Không gửi được mật khẩu tạm: thành viên này chưa nhắn riêng cho bot trên "
            "Telegram. Nhờ họ mở bot và bấm Start rồi thử lại.",
            details={"reason": self.code},
        )


class AccountNotFoundError(NotFoundError):
    """No such account, or one outside what this person may see. 404."""

    code = "account_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy.", details={"reason": self.code})


class AvatarRejectedError(ValidationError):
    """An uploaded profile picture failed a check. 422, ``code`` = the reason.

    ``avatar_too_large`` (over 300 KB decoded) or ``avatar_invalid_image`` (an
    unsupported type, data that is not base64, or bytes that are not the
    declared image type). ``details.field`` names the offending field.
    """

    code = "avatar_rejected"

    def __init__(self, reason: str, message: str, *, field: str) -> None:
        super().__init__(message, details=_with_reason(reason, {"field": field}))
        self.code = reason


class AvatarNotFoundError(NotFoundError):
    """No profile picture for that account (or no such account). 404."""

    code = "avatar_not_found"

    def __init__(self) -> None:
        super().__init__("Chưa có ảnh đại diện.", details={"reason": self.code})


__all__ = [
    "LOGIN_FAILED_MESSAGE",
    "LOGIN_LOCKED_MESSAGE",
    "PASSWORD_CHANGE_REQUIRED_MESSAGE",
    "AccountMembersForbiddenError",
    "AccountNotFoundError",
    "AccountStatusForbiddenError",
    "AccountValidationError",
    "AvatarNotFoundError",
    "AvatarRejectedError",
    "LoginFailedError",
    "LoginLockedError",
    "PasswordChangeRequiredError",
    "PasswordRejectedError",
    "PasswordResetForbiddenError",
    "PasswordResetUndeliverableError",
]
