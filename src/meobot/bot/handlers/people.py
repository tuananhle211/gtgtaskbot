"""Owner commands for access, lifecycle and quota, driven by replying.

**Why replying is the interface.** Every one of these commands needs to name a
person, and the only identifier Telegram gives us that cannot be spoofed,
duplicated or changed is the numeric user id on a message. A display name is
none of those things - two colleagues can share one, and anybody can set theirs
to somebody else's. So the target comes from ``reply_to_message``, and when
there is no reply there is no target: MeoBot asks for one rather than guessing.

**What is refused outright**, before any permission is even consulted: a bot as
the target, the owner as the target, an anonymous administrator as the sender,
and any target that cannot be resolved to a numeric id.

Every mutation is audited with the internal role enum and the display label
side by side, and nothing here deletes a user - "xoá khỏi hệ thống" is a
revocation.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.access_request_service import AccessRequestService
from meobot.application.audit_service import AuditService
from meobot.application.group_policy_service import (
    DEFAULT_MUTE,
    GroupPolicyService,
    guest_deadline_text,
    parse_duration,
)
from meobot.application.quota_service import QuotaService
from meobot.application.user_service import UserService, status_label
from meobot.bot import formatting
from meobot.bot.addressing import replied_to_user
from meobot.bot.commands import spec_for
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import format_local, utcnow
from meobot.db.session import Database
from meobot.domain.access.models import (
    GUEST_DEFAULT_DURATION,
    GUEST_DEFAULT_QUESTION_LIMIT,
    GroupPolicyMode,
)
from meobot.domain.access.quota import MAX_DAILY_LIMIT, QUOTA_BONUS_STEP
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.labels import parse_role, role_label, role_labels
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

router = Router(name="people")

NEEDS_REPLY = (
    "Mình chưa xác định chắc chắn người bạn muốn quản lý.\n"
    "Hãy reply trực tiếp vào một tin nhắn của người đó."
)

TARGET_IS_BOT = "Không thể áp dụng thao tác này cho một bot."
TARGET_IS_OWNER = (
    f"Không thể thay đổi quyền của {role_label(Role.OWNER)}. Vai trò này đến từ cấu hình."
)
ANONYMOUS_SENDER = (
    "Mình không xác định được bạn là ai (tin nhắn ẩn danh), nên không thực hiện thao tác này."
)
GROUP_ONLY = "Lệnh này chỉ dùng trong group."


@dataclass(frozen=True, slots=True)
class Target:
    """A person one of these commands is about, resolved from a reply."""

    telegram_user_id: int
    display_name: str
    username: str | None

    @property
    def label(self) -> str:
        return f"{self.display_name} (@{self.username})" if self.username else self.display_name


async def _resolve_target(message: Message, settings: Settings) -> Target | str:
    """Read the target off the replied-to message, or return a refusal string.

    Deliberately total: every rejection path produces the sentence the user
    should see, so the callers stay a single ``isinstance`` check.
    """
    if message.from_user is None:
        return ANONYMOUS_SENDER
    telegram_id, username, display_name, is_bot = replied_to_user(message)
    if telegram_id is None:
        return NEEDS_REPLY
    if is_bot:
        return TARGET_IS_BOT
    if settings.meobot_owner_telegram_id is not None and (
        telegram_id == settings.meobot_owner_telegram_id
    ):
        return TARGET_IS_OWNER
    return Target(
        telegram_user_id=telegram_id,
        display_name=display_name or str(telegram_id),
        username=username,
    )


def _require(actor: Actor, permission: Permission) -> str | None:
    """The refusal for a missing permission, or ``None`` when it is held."""
    if has_permission(actor.role, permission):
        return None
    return f"⛔ Chỉ {role_label(Role.OWNER)} được thực hiện thao tác này."


async def _run(
    message: Message,
    actor: Actor,
    settings: Settings,
    permission: Permission,
    body: Callable[[Target, AsyncSession], Awaitable[str]],
    database: Database,
    *,
    group_only: bool = False,
) -> None:
    """Shared shape: resolve, authorise, act in one transaction, answer once."""
    denial = _require(actor, permission)
    if denial is not None:
        await formatting.answer(message, formatting.escape(denial))
        return
    if group_only and message.chat.type == "private":
        await formatting.answer(message, formatting.escape(GROUP_ONLY))
        return

    target = await _resolve_target(message, settings)
    if isinstance(target, str):
        await formatting.answer(message, formatting.escape(target))
        return

    try:
        async with database.transaction() as session:
            reply = await body(target, session)
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return
    await formatting.answer(message, formatting.escape(reply))


def _bot_id(message: Message) -> int:
    return message.bot.id if message.bot is not None else 0


# --- Group response policy --------------------------------------------------
@router.message(Command("allow_user"))
async def handle_allow_user(
    message: Message, actor: Actor, settings: Settings, database: Database, request_id: uuid.UUID
) -> None:
    """Let MeoBot answer this person in this group under the normal rules.

    ``allow`` widens nothing on its own: the person still needs to be a
    registered, active user, and a suspended or revoked account stays blocked.
    """

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).set_mode(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
            mode=GroupPolicyMode.ALLOW,
        )
        await _audit_policy(session, actor, request_id, row.id, target, "allow")
        return f"✅ MeoBot sẽ trả lời {target.label} trong group này (theo quyền sẵn có)."

    await _run(
        message,
        actor,
        settings,
        Permission.GROUP_MEMBER_POLICY_MANAGE,
        body,
        database,
        group_only=True,
    )


@router.message(Command("ignore_user"))
async def handle_ignore_user(
    message: Message, actor: Actor, settings: Settings, database: Database, request_id: uuid.UUID
) -> None:
    """Silently drop this person's messages in this group."""

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).set_mode(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
            mode=GroupPolicyMode.IGNORE,
        )
        await _audit_policy(session, actor, request_id, row.id, target, "ignore")
        return (
            f"🔕 MeoBot sẽ im lặng với {target.label} trong group này. "
            "Các group khác và chat riêng không thay đổi."
        )

    await _run(
        message,
        actor,
        settings,
        Permission.GROUP_MEMBER_POLICY_MANAGE,
        body,
        database,
        group_only=True,
    )


@router.message(Command("mute_user"))
async def handle_mute_user(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Stay quiet for this person here until a deadline, then resume by itself."""
    duration = parse_duration((command.args or "").strip()) or DEFAULT_MUTE

    async def body(target: Target, session: AsyncSession) -> str:
        until = utcnow() + duration
        row = await GroupPolicyService(session).set_mode(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
            mode=GroupPolicyMode.MUTE_UNTIL,
            muted_until=until,
        )
        await _audit_policy(session, actor, request_id, row.id, target, "mute_until")
        local = format_local(until, settings.timezone, fmt="%H:%M %d/%m/%Y")
        return f"🔇 Im lặng với {target.label} trong group này tới {local}."

    await _run(
        message,
        actor,
        settings,
        Permission.GROUP_MEMBER_POLICY_MANAGE,
        body,
        database,
        group_only=True,
    )


@router.message(Command("unmute_user"))
async def handle_unmute_user(
    message: Message, actor: Actor, settings: Settings, database: Database, request_id: uuid.UUID
) -> None:
    """Return this person to the default rules in this group."""

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).reset_to_inherit(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
        )
        await _audit_policy(session, actor, request_id, row.id, target, "inherit")
        return f"🔔 Đã bỏ hạn chế với {target.label} trong group này."

    await _run(
        message,
        actor,
        settings,
        Permission.GROUP_MEMBER_POLICY_MANAGE,
        body,
        database,
        group_only=True,
    )


async def _audit_policy(
    session: AsyncSession,
    actor: Actor,
    request_id: uuid.UUID,
    policy_id: uuid.UUID,
    target: Target,
    mode: str,
) -> None:
    """One audit row per policy change, with the enum-first convention."""
    await AuditService(session).record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.GROUP_POLICY_SET.value,
        result=AuditResult.SUCCESS,
        entity_type="group_member_policy",
        entity_id=str(policy_id),
        after_data={
            "mode": mode,
            "target_telegram_id": target.telegram_user_id,
            "actor_role": actor.role.value,
            "actor_role_label": role_label(actor.role),
        },
    )


# --- Guest access -----------------------------------------------------------
@router.message(Command("guest_user"))
async def handle_guest_user(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Grant a temporary Guest window in this group.

    Usage: ``/guest_user [số lượt] [thời hạn]`` - defaults to 10 questions and
    24 hours, whichever runs out first.
    """
    limit, duration = _parse_guest_args(command.args)

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).grant_guest(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
            question_limit=limit,
            duration=duration,
        )
        await AuditService(session).record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.GUEST_ACCESS_GRANTED.value,
            result=AuditResult.SUCCESS,
            entity_type="group_member_policy",
            entity_id=str(row.id),
            after_data={
                "target_telegram_id": target.telegram_user_id,
                "chat_id": message.chat.id,
                "question_limit": limit,
                "expires_at": row.guest_expires_at.isoformat() if row.guest_expires_at else None,
            },
        )
        deadline = guest_deadline_text(row, settings.timezone)
        return (
            f"⏱ {target.label} là Guest trong group này: {limit} lượt, hết hạn {deadline}.\n"
            "Guest chỉ trò chuyện, không dùng được công cụ hay dữ liệu nội bộ."
        )

    await _run(
        message, actor, settings, Permission.GUEST_ACCESS_MANAGE, body, database, group_only=True
    )


def _parse_guest_args(args: str | None) -> tuple[int, timedelta]:
    """Read ``[số lượt] [thời hạn]`` with the documented defaults."""
    parts = (args or "").split()
    limit = GUEST_DEFAULT_QUESTION_LIMIT
    duration = GUEST_DEFAULT_DURATION
    if parts:
        try:
            limit = max(1, min(100, int(parts[0])))
        except ValueError:
            limit = GUEST_DEFAULT_QUESTION_LIMIT
    if len(parts) > 1:
        duration = parse_duration(parts[1]) or GUEST_DEFAULT_DURATION
    return limit, duration


@router.message(Command("extend_guest"))
async def handle_extend_guest(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Add questions and/or time to an existing Guest window."""
    extra_questions, extra_duration = _parse_extend_args(command.args)

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).extend_guest(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
            extra_questions=extra_questions,
            extra_duration=extra_duration,
        )
        if row is None:
            return f"{target.label} chưa từng được cấp quyền Guest trong group này."
        await AuditService(session).record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.GUEST_ACCESS_EXTENDED.value,
            result=AuditResult.SUCCESS,
            entity_type="group_member_policy",
            entity_id=str(row.id),
            after_data={
                "target_telegram_id": target.telegram_user_id,
                "question_limit": row.guest_question_limit,
                "expires_at": row.guest_expires_at.isoformat() if row.guest_expires_at else None,
            },
        )
        used = f"{row.guest_questions_used}/{row.guest_question_limit}"
        return f"➕ {target.label}: đã dùng {used} lượt."

    await _run(
        message, actor, settings, Permission.GUEST_ACCESS_MANAGE, body, database, group_only=True
    )


def _parse_extend_args(args: str | None) -> tuple[int, timedelta | None]:
    parts = (args or "").split()
    extra = QUOTA_BONUS_STEP
    duration: timedelta | None = None
    if parts:
        try:
            extra = max(0, min(100, int(parts[0])))
        except ValueError:
            extra = QUOTA_BONUS_STEP
    if len(parts) > 1:
        duration = parse_duration(parts[1])
    return extra, duration


@router.message(Command("reset_guest"))
async def handle_reset_guest(
    message: Message, actor: Actor, settings: Settings, database: Database, request_id: uuid.UUID
) -> None:
    """Give a Guest a full allowance and a fresh 24 hours."""

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).reset_guest(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
        )
        await AuditService(session).record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.GUEST_ACCESS_EXTENDED.value,
            result=AuditResult.SUCCESS,
            entity_type="group_member_policy",
            entity_id=str(row.id),
            after_data={"target_telegram_id": target.telegram_user_id, "reset": True},
        )
        return f"♻️ {target.label} có lại {row.guest_question_limit} lượt trong 24 giờ."

    await _run(
        message, actor, settings, Permission.GUEST_ACCESS_MANAGE, body, database, group_only=True
    )


@router.message(Command("revoke_guest"))
async def handle_revoke_guest(
    message: Message, actor: Actor, settings: Settings, database: Database, request_id: uuid.UUID
) -> None:
    """End a Guest window immediately."""

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).revoke_guest(
            actor=actor,
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
        )
        if row is None:
            return f"{target.label} không có quyền Guest nào trong group này."
        await AuditService(session).record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.GUEST_ACCESS_REVOKED.value,
            result=AuditResult.SUCCESS,
            entity_type="group_member_policy",
            entity_id=str(row.id),
            after_data={"target_telegram_id": target.telegram_user_id},
        )
        return f"⛔ Đã thu hồi quyền Guest của {target.label} trong group này."

    await _run(
        message, actor, settings, Permission.GUEST_ACCESS_MANAGE, body, database, group_only=True
    )


@router.message(Command("guest_info"))
async def handle_guest_info(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """Show one Guest's remaining allowance in this group."""

    async def body(target: Target, session: AsyncSession) -> str:
        row = await GroupPolicyService(session).get(
            bot_id=_bot_id(message),
            chat_id=message.chat.id,
            telegram_user_id=target.telegram_user_id,
        )
        if row is None or row.guest_granted_at is None:
            return f"{target.label} chưa từng được cấp quyền Guest trong group này."
        mode = GroupPolicyService.effective_mode(row)
        deadline = guest_deadline_text(row, settings.timezone)
        return (
            f"👤 {target.label}\n"
            f"Trạng thái: {mode.value}\n"
            f"Đã dùng: {row.guest_questions_used}/{row.guest_question_limit} lượt\n"
            f"Hết hạn: {deadline}"
        )

    await _run(
        message,
        actor,
        settings,
        Permission.GROUP_MEMBER_POLICY_READ,
        body,
        database,
        group_only=True,
    )


@router.message(Command("group_guests"))
async def handle_group_guests(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """List every policy MeoBot holds for this group."""
    denial = _require(actor, Permission.GROUP_MEMBER_POLICY_READ)
    if denial is not None:
        await formatting.answer(message, formatting.escape(denial))
        return
    if message.chat.type == "private":
        await formatting.answer(message, formatting.escape(GROUP_ONLY))
        return

    async with database.session() as session:
        rows = await GroupPolicyService(session).list_for_chat(
            bot_id=_bot_id(message), chat_id=message.chat.id
        )
    if not rows:
        await formatting.answer(message, "Group này chưa có chính sách riêng nào.")
        return

    lines = ["👥 " + formatting.bold("Chính sách trong group này")]
    for row in rows:
        mode = GroupPolicyService.effective_mode(row)
        detail = (
            f"{row.guest_questions_used}/{row.guest_question_limit} lượt"
            if (mode is GroupPolicyMode.GUEST)
            else mode.value
        )
        lines.append(formatting.escape(f"• {row.telegram_user_id}: {detail}"))
    await formatting.answer(message, "\n".join(lines))


# --- User lifecycle ---------------------------------------------------------
@router.message(Command("user_info"))
async def handle_user_info(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """Show the authoritative record for one person."""

    async def body(target: Target, session: AsyncSession) -> str:
        user = await UserService(session, AuditService(session)).by_telegram_id(
            target.telegram_user_id
        )
        if user is None:
            return f"{target.label} chưa phải thành viên hệ thống."
        quota = await QuotaService(session, settings).inspect_member(
            user_id=user.id, role=user.role
        )
        remaining = "không giới hạn" if quota.remaining < 0 else f"{quota.remaining} lượt"
        return (
            f"👤 {user.full_name}\n"
            f"Vai trò: {role_label(user.role)}\n"
            f"Trạng thái: {status_label(user.status)}\n"
            f"Còn lại hôm nay: {remaining}"
        )

    await _run(message, actor, settings, Permission.USER_READ, body, database)


@router.message(Command("suspend_user"))
async def handle_suspend_user(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Block an account globally, reversibly."""
    reason = (command.args or "").strip() or None

    async def body(target: Target, session: AsyncSession) -> str:
        users = UserService(session, AuditService(session))
        user = await users.by_telegram_id(target.telegram_user_id)
        if user is None:
            return f"{target.label} chưa phải thành viên hệ thống."
        await users.suspend(actor=actor, request_id=request_id, user_id=user.id, reason=reason)
        await _archive_threads(session, settings, target.telegram_user_id)
        return (
            f"🚫 Đã tạm khoá {user.full_name}. Họ không dùng được MeoBot ở chat riêng "
            "hay bất kỳ group nào cho tới khi được mở lại."
        )

    await _run(message, actor, settings, Permission.USER_STATUS_MANAGE, body, database)


@router.message(Command("enable_user"))
async def handle_enable_user(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Put a suspended account back on the air, with a fresh conversation."""

    async def body(target: Target, session: AsyncSession) -> str:
        users = UserService(session, AuditService(session))
        user = await users.by_telegram_id(target.telegram_user_id)
        if user is None:
            return f"{target.label} chưa phải thành viên hệ thống."
        await users.enable(actor=actor, request_id=request_id, user_id=user.id)
        await _archive_threads(session, settings, target.telegram_user_id)
        return f"✅ {user.full_name} dùng lại được MeoBot. Mạch trò chuyện bắt đầu lại từ đầu."

    await _run(message, actor, settings, Permission.USER_STATUS_MANAGE, body, database)


@router.message(Command("revoke_user"))
async def handle_revoke_user(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """End an account's access for good - without deleting anything."""
    reason = (command.args or "").strip() or None

    async def body(target: Target, session: AsyncSession) -> str:
        users = UserService(session, AuditService(session))
        user = await users.by_telegram_id(target.telegram_user_id)
        if user is None:
            return f"{target.label} chưa phải thành viên hệ thống."
        await users.revoke(actor=actor, request_id=request_id, user_id=user.id, reason=reason)
        await _archive_threads(session, settings, target.telegram_user_id)
        return (
            f"⛔ Đã thu hồi quyền của {user.full_name}.\n"
            "Mình sẽ vô hiệu hóa quyền sử dụng và giữ lại lịch sử audit; "
            "không xóa dữ liệu cũ."
        )

    await _run(message, actor, settings, Permission.USER_STATUS_MANAGE, body, database)


@router.message(Command("change_user_role"))
async def handle_change_user_role(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Move somebody to a different authoritative role."""
    role = parse_role((command.args or "").strip())

    async def body(target: Target, session: AsyncSession) -> str:
        if role is None:
            choices = role_labels({Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN})
            return f"Vai trò không hợp lệ. Chọn: {choices}"
        users = UserService(session, AuditService(session))
        user = await users.by_telegram_id(target.telegram_user_id)
        if user is None:
            return f"{target.label} chưa phải thành viên hệ thống."
        await users.change_role(actor=actor, request_id=request_id, user_id=user.id, role=role)
        return f"🔁 {user.full_name} giờ có vai trò {role_label(role)}."

    await _run(message, actor, settings, Permission.USER_ROLE_MANAGE, body, database)


async def _archive_threads(
    session: AsyncSession, settings: Settings, telegram_user_id: int
) -> None:
    """Close this person's open conversations, keeping every message.

    A status change should not leave a half-finished conversation waiting to be
    resumed - when they come back it starts clean - but archiving is not
    deleting: the history stays for audit and for continuity.
    """
    from sqlalchemy import update

    from meobot.db.models.confirmation_request import ConfirmationRequestRow
    from meobot.db.models.conversation import ConversationThread
    from meobot.domain.policy.models import ConfirmationState

    await session.execute(
        update(ConversationThread)
        .where(
            ConversationThread.telegram_user_id == telegram_user_id,
            ConversationThread.active.is_(True),
        )
        .values(active=False, archived_at=utcnow())
    )
    # A pending confirmation belongs to a conversation that is now over. It is
    # marked rejected rather than left pending, so a high-risk action approved
    # before a suspension cannot be redeemed after it.
    await session.execute(
        update(ConfirmationRequestRow)
        .where(
            ConfirmationRequestRow.telegram_user_id == telegram_user_id,
            ConfirmationRequestRow.status == ConfirmationState.PENDING,
        )
        .values(status=ConfirmationState.REJECTED)
    )


# --- Quota ------------------------------------------------------------------
@router.message(Command("quota"))
async def handle_quota(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """Show the caller their own remaining chat allowance."""
    async with database.transaction() as session:
        verdict = (
            await QuotaService(session, settings).inspect_member(
                user_id=actor.user_id or uuid.uuid4(), role=actor.role
            )
            if actor.user_id is not None
            else None
        )

    if actor.user_id is None or verdict is None or verdict.remaining < 0:
        await formatting.answer(
            message,
            formatting.escape(
                f"Vai trò {role_label(actor.role)} không giới hạn số lượt trò chuyện AI."
            ),
        )
        return
    await formatting.answer(
        message,
        formatting.escape(
            f"💬 Còn {verdict.remaining}/{verdict.limit} lượt trò chuyện AI hôm nay."
        ),
    )


@router.message(Command("user_quota"))
async def handle_user_quota(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """Show one member's remaining allowance."""

    async def body(target: Target, session: AsyncSession) -> str:
        user = await UserService(session, AuditService(session)).by_telegram_id(
            target.telegram_user_id
        )
        if user is None:
            return f"{target.label} chưa phải thành viên hệ thống."
        verdict = await QuotaService(session, settings).inspect_member(
            user_id=user.id, role=user.role
        )
        if verdict.remaining < 0:
            return f"{user.full_name} ({role_label(user.role)}) không bị giới hạn."
        return (
            f"💬 {user.full_name}: còn {verdict.remaining}/{verdict.limit} lượt hôm nay "
            f"(đã dùng {verdict.used})."
        )

    await _run(message, actor, settings, Permission.USER_READ, body, database)


@router.message(Command("add_user_quota"))
async def handle_add_user_quota(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Give one member extra messages for today only."""
    amount = _parse_int(command.args, default=QUOTA_BONUS_STEP)

    async def body(target: Target, session: AsyncSession) -> str:
        return await _quota_change(
            session,
            settings,
            actor,
            request_id,
            target,
            lambda quota, user_id: quota.add_bonus(user_id=user_id, amount=amount),
            f"➕ Đã thêm {amount} lượt cho hôm nay",
        )

    await _run(message, actor, settings, Permission.USER_QUOTA_MANAGE, body, database)


@router.message(Command("reset_user_quota"))
async def handle_reset_user_quota(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Set one member's consumption for today back to zero."""

    async def body(target: Target, session: AsyncSession) -> str:
        return await _quota_change(
            session,
            settings,
            actor,
            request_id,
            target,
            lambda quota, user_id: quota.reset_today(user_id=user_id),
            "♻️ Đã đặt lại lượt hôm nay",
        )

    await _run(message, actor, settings, Permission.USER_QUOTA_MANAGE, body, database)


@router.message(Command("set_user_daily_quota"))
async def handle_set_user_daily_quota(
    message: Message,
    command: CommandObject,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Set a standing daily limit that survives midnight."""
    limit = _parse_int(command.args, default=-1)
    if limit < 0:
        await formatting.answer(
            message, formatting.escape(spec_for("set_user_daily_quota").usage_text())
        )
        return

    async def body(target: Target, session: AsyncSession) -> str:
        return await _quota_change(
            session,
            settings,
            actor,
            request_id,
            target,
            lambda quota, user_id: quota.set_daily_limit(
                user_id=user_id,
                limit=limit,
                actor_user_id=actor.user_id,
                actor_telegram_id=actor.telegram_user_id,
            ),
            f"⚙️ Hạn mức mới: {min(limit, MAX_DAILY_LIMIT)} lượt/ngày",
        )

    await _run(message, actor, settings, Permission.USER_QUOTA_MANAGE, body, database)


@router.message(Command("clear_user_quota_override"))
async def handle_clear_user_quota_override(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Remove a standing limit and go back to the default."""

    async def body(target: Target, session: AsyncSession) -> str:
        return await _quota_change(
            session,
            settings,
            actor,
            request_id,
            target,
            lambda quota, user_id: quota.clear_daily_limit(user_id=user_id),
            "↩️ Đã bỏ hạn mức riêng",
        )

    await _run(message, actor, settings, Permission.USER_QUOTA_MANAGE, body, database)


async def _quota_change(
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    request_id: uuid.UUID,
    target: Target,
    change: Callable[[QuotaService, uuid.UUID], Awaitable[object]],
    summary: str,
) -> str:
    """Apply one quota change and audit the before/after numbers."""
    users = UserService(session, AuditService(session))
    user = await users.by_telegram_id(target.telegram_user_id)
    if user is None:
        return f"{target.label} chưa phải thành viên hệ thống."

    quota = QuotaService(session, settings)
    before = await quota.inspect_member(user_id=user.id, role=user.role)
    after = await change(quota, user.id)
    await AuditService(session).record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.QUOTA_OVERRIDDEN.value,
        result=AuditResult.SUCCESS,
        entity_type="user",
        entity_id=str(user.id),
        before_data={"limit": before.limit, "used": before.used},
        after_data={
            "limit": getattr(after, "limit", before.limit),
            "used": getattr(after, "used", before.used),
            "target_telegram_id": target.telegram_user_id,
            "actor_role": actor.role.value,
            "actor_role_label": role_label(actor.role),
        },
    )
    remaining = getattr(after, "remaining", 0)
    return f"{summary} cho {user.full_name}. Còn lại: {remaining} lượt."


def _parse_int(args: str | None, *, default: int) -> int:
    try:
        return int((args or "").strip())
    except ValueError:
        return default


# --- Pending access requests ------------------------------------------------
@router.message(Command("pending_access"))
async def handle_pending_access(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """List strangers currently waiting for a decision."""
    denial = _require(actor, Permission.GUEST_ACCESS_MANAGE)
    if denial is not None:
        await formatting.answer(message, formatting.escape(denial))
        return

    async with database.session() as session:
        rows = await AccessRequestService(session).list_open()
    if not rows:
        await formatting.answer(message, "Không có yêu cầu nào đang chờ.")
        return

    lines = ["⏳ " + formatting.bold("Đang chờ duyệt")]
    for row in rows:
        who = row.requester_display_name or str(row.requester_telegram_id)
        where = row.chat_title or str(row.telegram_chat_id)
        lines.append(formatting.escape(f"• {who} — {where}"))
    await formatting.answer(message, "\n".join(lines))
