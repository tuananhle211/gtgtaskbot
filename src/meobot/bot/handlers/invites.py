"""People commands: ``/create_invite``, ``/add_user`` for managers, ``/join``
for staff.

``/join`` is the one command an *unregistered* Telegram account may run, so it
is registered on a router that bypasses the actor middleware's rejection - see
:mod:`meobot.bot.middlewares`.

Every role this module prints is a display label
(:mod:`meobot.domain.identity.labels`); every role it *reads* is folded back to
the authoritative enum before a service or the policy engine sees it. "MEMBER",
"member" and "nhân viên" all reach :class:`~meobot.domain.identity.models.Role`
``EMPLOYEE``, and an alias that does not resolve is refused rather than guessed
at.
"""

from __future__ import annotations

import uuid

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from meobot.application.audit_service import AuditService
from meobot.application.invite_service import DEFAULT_EXPIRY_DAYS, InviteService
from meobot.application.user_service import UserService
from meobot.bot import formatting
from meobot.bot.commands import spec_for
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.identity.invites import INVITABLE_ROLES
from meobot.domain.identity.labels import parse_role_prefix, role_label, role_labels
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

router = Router(name="invites")

MAX_USES_PER_COMMAND = 20

#: Roles a command may hand out, spelled the way a user reads them.
INVITABLE_LABELS = role_labels(INVITABLE_ROLES)


@router.message(Command("create_invite"))
async def handle_create_invite(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Create an invite code.

    Usage: ``/create_invite [vai trò] [số lượt] [số ngày]`` - defaults to one
    Member code valid for a week.
    """
    if not has_permission(actor.role, Permission.USER_MANAGE):
        await formatting.answer(
            message,
            formatting.escape(
                f"⛔ Chỉ {role_label(Role.OWNER)} và {role_label(Role.ADMIN)} mới tạo được mã mời."
            ),
        )
        return

    role, max_uses, days, error = _parse(command.args)
    if error is not None:
        await formatting.answer(message, formatting.escape(error))
        return

    try:
        async with database.transaction() as session:
            service = InviteService(session, AuditService(session))
            invite, code = await service.create(
                actor=actor,
                request_id=request_id,
                role=role,
                expires_in_days=days,
                max_uses=max_uses,
            )
            expires = invite.expires_at.date().isoformat() if invite.expires_at else "không"
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return

    await formatting.answer(
        message,
        "🎟 "
        + formatting.bold(f"Mã mời {role_label(role)}")
        + "\n\n"
        + formatting.code(code)
        + "\n\n"
        + formatting.escape(f"Số lượt: {max_uses} · Hết hạn: {expires}")
        + "\n"
        + formatting.escape(f"Người được mời gõ: /join {code}")
        + "\n\n"
        + formatting.escape("MeoBot chỉ lưu bản băm của mã. Nếu mất, hãy tạo mã mới."),
    )


@router.message(Command("add_user"))
async def handle_add_user(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Register somebody directly, without an invite code.

    Usage: ``/add_user <telegram_id> <vai trò> [họ tên]``. The role argument
    accepts every alias - ``MEMBER``, ``EMPLOYEE``, "nhân viên", "trưởng nhóm",
    ``TEAM_LEAD`` - and is folded to the authoritative enum here, before
    :class:`~meobot.application.user_service.UserService` checks whether this
    actor may grant it.
    """
    telegram_user_id, role, full_name, error = _parse_add_user(command.args)
    if error is not None or telegram_user_id is None or role is None:
        await formatting.answer(
            message, formatting.escape(error or spec_for("add_user").usage_text())
        )
        return

    try:
        async with database.transaction() as session:
            service = UserService(session, AuditService(session))
            user = await service.add_user(
                actor=actor,
                request_id=request_id,
                telegram_user_id=telegram_user_id,
                role=role,
                full_name=full_name,
            )
            added_name = user.full_name
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return

    await formatting.answer(
        message,
        formatting.escape(f"✅ Đã thêm {added_name} với vai trò {role_label(role)}.")
        + "\n"
        + formatting.escape("Họ gõ /start để bắt đầu dùng MeoBot."),
    )


@router.message(Command("join"))
async def handle_join(
    message: Message,
    command: CommandObject,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Register the sender using an invite code."""
    code = (command.args or "").strip()
    if not code:
        await formatting.answer(message, formatting.escape(spec_for("join").usage_text()))
        return
    if message.from_user is None:  # pragma: no cover - channel posts have no sender
        return

    try:
        async with database.transaction() as session:
            service = InviteService(session, AuditService(session))
            user = await service.redeem(
                request_id=request_id,
                code=code,
                telegram_user_id=message.from_user.id,
                telegram_username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            role = user.role
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return

    await formatting.answer(
        message,
        formatting.escape("✅ Đăng ký thành công. Vai trò của bạn: ")
        + formatting.bold(role_label(role))
        + formatting.escape(".")
        + "\n"
        + formatting.escape("Gõ /help để xem những gì bạn làm được."),
    )


def _parse(args: str | None) -> tuple[Role, int, int, str | None]:
    """Parse ``[vai trò] [số lượt] [số ngày]`` with forgiving defaults."""
    parts = (args or "").split()
    role = Role.EMPLOYEE
    max_uses = 1
    days = DEFAULT_EXPIRY_DAYS

    if parts:
        # A label can be several words ("trưởng nhóm"), so the role is taken
        # off the front rather than assumed to be exactly one token.
        parsed, parts = parse_role_prefix(parts)
        if parsed is None:
            return role, max_uses, days, f"Vai trò không hợp lệ. Chọn: {INVITABLE_LABELS}"
        role = parsed
        if role not in INVITABLE_ROLES:
            return (
                role,
                max_uses,
                days,
                f"Không thể mời vai trò {role_label(role)}. Chọn: {INVITABLE_LABELS}",
            )

    if parts:
        try:
            max_uses = int(parts[0])
        except ValueError:
            return role, max_uses, days, "Số lượt dùng phải là số nguyên."
        if not 1 <= max_uses <= MAX_USES_PER_COMMAND:
            return role, max_uses, days, f"Số lượt dùng phải từ 1 đến {MAX_USES_PER_COMMAND}."

    if len(parts) > 1:
        try:
            days = int(parts[1])
        except ValueError:
            return role, max_uses, days, "Số ngày phải là số nguyên."

    return role, max_uses, days, None


def _parse_add_user(args: str | None) -> tuple[int | None, Role | None, str, str | None]:
    """Parse ``<telegram_id> <vai trò> [họ tên]``.

    Returns ``(telegram_id, role, full_name, error)``; the first three are only
    meaningful when ``error`` is ``None``.
    """
    parts = (args or "").split()
    usage = spec_for("add_user").usage_text()
    if len(parts) < 2:
        return None, None, "", usage

    try:
        telegram_user_id = int(parts[0])
    except ValueError:
        return None, None, "", f"Telegram ID phải là một số.\n{usage}"

    role, rest = parse_role_prefix(parts[1:])
    if role is None:
        return None, None, "", f"Vai trò không hợp lệ. Chọn: {INVITABLE_LABELS}"
    if role not in INVITABLE_ROLES:
        return (
            None,
            None,
            "",
            f"Không thể thêm thành viên với vai trò {role_label(role)}. Chọn: {INVITABLE_LABELS}",
        )

    return telegram_user_id, role, " ".join(rest), None
