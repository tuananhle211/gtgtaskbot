"""User-facing Vietnamese strings.

Centralised so wording can be reviewed by a human, and so tests can assert on a
constant instead of a literal copied into a handler.

The ``/help`` body is **not** here. It used to be, as a hand-maintained
Markdown constant, and it drifted from the handlers and then broke Telegram's
parser outright (an odd number of ``_`` in the command names is an unterminated
italic entity). It is now generated from
:mod:`meobot.bot.commands` - the same registry that feeds ``setMyCommands`` and
the tests.
"""

from __future__ import annotations

from meobot.domain.identity.labels import GUEST_LABEL, role_label
from meobot.domain.identity.models import Role

WELCOME_OWNER = (
    f"Xin chào. TasksBot đã sẵn sàng.\nBạn đang đăng nhập với vai trò {role_label(Role.OWNER)}."
)

NOT_REGISTERED = "Tài khoản Telegram này chưa được đăng ký với TasksBot."

INTERNAL_ERROR = "Đã có lỗi xảy ra khi xử lý yêu cầu. Kỹ thuật đã được ghi nhận log."


def welcome_for(role: Role, full_name: str, *, guest: bool = False) -> str:
    """Greeting shown by ``/start``.

    Always the display label, never the enum: ``OWNER`` is "Chủ sở hữu" and
    ``EMPLOYEE`` is "Nhân viên" to everybody who reads a Telegram message.
    """
    if guest:
        return f"Xin chào {full_name}. TasksBot đã sẵn sàng.\nVai trò của bạn: {GUEST_LABEL}."
    if role is Role.OWNER:
        return WELCOME_OWNER
    return f"Xin chào {full_name}. TasksBot đã sẵn sàng.\nVai trò của bạn: {role_label(role)}."
