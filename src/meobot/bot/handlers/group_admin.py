"""Giving a Trưởng nhóm authority over one group, in natural Vietnamese.

"Gán Linh quản lý group Content." Two names have to be resolved before anything
happens, and both are resolved from stored data rather than guessed:

* **the person**, by name against registered users - and they must already hold
  the Trưởng nhóm role. Assigning an EMPLOYEE is refused rather than silently
  promoting them, because a permission grant that quietly widens somebody's
  role is exactly the thing a permission system exists to prevent;
* **the group**, through the same resolver every other destination uses, which
  asks a question when two could be meant instead of picking the closer name.

Only the owner reaches any of this. It is a preview-and-confirm flow like every
other change MeoBot makes on somebody's behalf: the preview states exactly what
the assignment does *and does not* grant, so "gửi được toàn phòng" is never
something anybody has to infer.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.application.recipient_resolver import RecipientResolver
from meobot.bot import formatting
from meobot.bot.member_filters import MemberIntentFilter
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.notifications import TelegramChat
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.callbacks import MemberBinding, build, data_pattern, parse
from meobot.domain.member.intents import MemberIntent

logger = get_logger(__name__)

router = Router(name="group_admin")

HANDLED: frozenset[MemberIntent] = frozenset(
    {
        MemberIntent.ASSIGN_GROUP_MANAGER,
        MemberIntent.REMOVE_GROUP_MANAGER,
        MemberIntent.VIEW_GROUP_MANAGERS,
    }
)

ASSIGN_ACTIONS = ("group.assign.confirm", "group.assign.cancel")

ONLY_OWNER = f"Chỉ {role_label(Role.OWNER)} được giao quyền quản lý group."
NEEDS_BOTH = (
    "Bạn cho mình biết tên người và tên group giúp nhé.\nVí dụ: “Gán Linh quản lý group Content.”"
)
NO_SUCH_PERSON = "TasksBot chưa tìm thấy ai tên như vậy trong danh sách thành viên."
NO_SUCH_GROUP = "TasksBot chưa tìm thấy group nào đã đăng ký với tên đó."
AMBIGUOUS_GROUP = "Bạn muốn chọn group nào?"
CANCELLED = "TasksBot chưa thay đổi quyền quản lý group nào."
STALE = "Nút này không còn hiệu lực."
ASSIGNED = "✅ TasksBot đã giao quyền quản lý group."
REVOKED = "✅ TasksBot đã bỏ quyền quản lý group của người này."


@router.message(MemberIntentFilter(HANDLED))
async def handle_group_admin(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
    member_intent: MemberIntent,
    member_text: str,
) -> None:
    """Route one group-management request."""
    if actor.role is not Role.OWNER:
        await formatting.answer(message, formatting.escape(ONLY_OWNER))
        return

    if member_intent is MemberIntent.VIEW_GROUP_MANAGERS:
        await _show_managers(message, database, settings, member_text)
        return

    resolved = await _resolve(message, database, settings, member_text)
    if resolved is None:
        return
    user, chat = resolved

    if member_intent is MemberIntent.REMOVE_GROUP_MANAGER:
        await _revoke(message, actor, database, user_id=user.id, chat_row_id=chat.id)
        return

    await _preview(message, settings, state, user=user, chat=chat)


async def _resolve(
    message: Message, database: Database, settings: Settings, text: str
) -> tuple[User, TelegramChat] | None:
    """Find the named person and the named group, or explain what is missing."""
    async with database.session() as session:
        chat_resolution = await RecipientResolver(session, settings).resolve_chat(
            bot_identity=message.bot.id if message.bot is not None else 0, text=text
        )
        if chat_resolution.is_ambiguous:
            await formatting.answer(
                message,
                formatting.escape(AMBIGUOUS_GROUP),
                reply_markup=formatting.keyboard(
                    [[(row.display_name, f"noop:{row.id}")] for row in chat_resolution.candidates]
                ),
            )
            return None
        if chat_resolution.chat is None:
            await formatting.answer(message, formatting.escape(NO_SUCH_GROUP))
            return None

        user = await _find_person(session, text, exclude=chat_resolution.chat.display_name)

    if user is None:
        await formatting.answer(message, formatting.escape(NO_SUCH_PERSON))
        return None
    return user, chat_resolution.chat


async def _find_person(session: object, text: str, *, exclude: str) -> User | None:
    """Match a name in the sentence against registered users.

    Matching whole names against the message rather than extracting a name from
    it: extraction needs grammar, and "Linh" appearing in the sentence is
    exactly as strong a signal without any.
    """
    from sqlalchemy import select

    from meobot.domain.access.models import UserStatus
    from meobot.domain.member.normalization import strip_accents

    folded = strip_accents(text)
    folded_exclude = strip_accents(exclude)
    result = await session.execute(  # type: ignore[attr-defined]
        select(User).where(User.status == UserStatus.ACTIVE)
    )
    matches: list[User] = []
    for candidate in result.scalars().all():
        name = strip_accents(candidate.full_name or "")
        if not name:
            continue
        # The group's own name often contains a word that is also a person's
        # name; the group has already been resolved, so exclude it.
        parts = [part for part in name.split() if part and part not in folded_exclude]
        if any(part in folded for part in parts):
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


async def _preview(
    message: Message, settings: Settings, state: FSMContext, *, user: User, chat: TelegramChat
) -> None:
    """State exactly what the assignment grants, and what it does not."""
    body = "\n".join(
        [
            formatting.escape("Bạn đang gán quyền quản lý group:"),
            "",
            formatting.escape(f"• Người được gán: {user.full_name}"),
            formatting.escape(f"• Vai trò hệ thống: {role_label(user.role)}"),
            formatting.escape(f"• Group: {chat.display_name}"),
            formatting.escape("• Có thể gửi thông báo: Có"),
            formatting.escape("• Có thể xem xác nhận đã đọc: Có"),
            formatting.escape("• Không có quyền gửi toàn phòng"),
            "",
            formatting.escape("Bạn kiểm tra lại trước khi xác nhận nhé."),
        ]
    )
    binding = MemberBinding(
        bot_id=message.bot.id if message.bot is not None else 0,
        telegram_user_id=message.from_user.id if message.from_user is not None else 0,
        chat_id=message.chat.id,
    )
    expires_at = utcnow() + timedelta(seconds=settings.member_flow_ttl_seconds)

    def button(action: str, entity: uuid.UUID) -> str:
        return build(
            action,
            secret=settings.callback_secret,
            binding=binding,
            expires_at=expires_at,
            entity_id=entity,
        )

    # Two ids do not fit in Telegram's 64 bytes, so the button carries the
    # group and the *durable* FSM store carries the person. The FSM store is
    # PostgreSQL-backed, so a restart between the preview and the press does
    # not silently assign the wrong person - it asks again.
    await state.update_data(
        pending_assignment_user_id=str(user.id),
        pending_assignment_chat_id=str(chat.id),
    )

    await formatting.answer(
        message,
        body,
        reply_markup=formatting.keyboard(
            [
                [("✅ Xác nhận", button("group.assign.confirm", chat.id))],
                [("❌ Huỷ", button("group.assign.cancel", chat.id))],
            ]
        ),
    )


@router.callback_query(F.data.regexp(data_pattern(ASSIGN_ACTIONS)))
async def handle_assignment_button(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Apply the owner's confirmation."""
    await query.answer()
    chat_id = query.message.chat.id if isinstance(query.message, Message) else 0
    payload = parse(
        query.data or "",
        secret=settings.callback_secret,
        binding=MemberBinding(
            bot_id=query.bot.id if query.bot is not None else 0,
            telegram_user_id=query.from_user.id,
            chat_id=chat_id,
        ),
    )
    if payload is None or payload.entity_id is None or payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(STALE))
        return
    if payload.action == "group.assign.cancel":
        await state.clear()
        await formatting.edit_callback(query, formatting.escape(CANCELLED))
        return

    data = await state.get_data()
    stored_user = data.get("pending_assignment_user_id")
    stored_chat = data.get("pending_assignment_chat_id")
    if not stored_user or str(stored_chat) != str(payload.entity_id):
        # Nothing held, or held for a different group than the button names.
        await formatting.edit_callback(query, formatting.escape(CANCELLED))
        return
    user_id = uuid.UUID(str(stored_user))

    async with database.transaction() as session:
        chat = await session.get(TelegramChat, payload.entity_id)
        target = await session.get(User, user_id)
        if chat is None or target is None:
            await formatting.edit_callback(query, formatting.escape(STALE))
            return
        try:
            await ChatAssignmentService(session).assign_manager(
                actor=actor, chat=chat, target=target
            )
        except MeoBotError as exc:
            await formatting.edit_callback(query, "⛔ " + formatting.escape(exc.message))
            return
        summary = formatting.escape(
            f"{ASSIGNED}\n\n• {target.full_name} quản lý group {chat.display_name}"
        )

    await state.clear()
    await formatting.edit_callback(query, summary)


async def _revoke(
    message: Message,
    actor: Actor,
    database: Database,
    *,
    user_id: uuid.UUID,
    chat_row_id: uuid.UUID,
) -> None:
    """Withdraw an assignment. Takes effect for anything future, immediately."""
    async with database.transaction() as session:
        try:
            await ChatAssignmentService(session).revoke(
                actor=actor, chat_row_id=chat_row_id, user_id=user_id
            )
        except MeoBotError as exc:
            await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
            return
    await formatting.answer(message, formatting.escape(REVOKED))


async def _show_managers(
    message: Message, database: Database, settings: Settings, text: str
) -> None:
    """ "Ai đang quản lý group Content?"."""
    async with database.session() as session:
        resolution = await RecipientResolver(session, settings).resolve_chat(
            bot_identity=message.bot.id if message.bot is not None else 0, text=text
        )
        if resolution.chat is None:
            await formatting.answer(message, formatting.escape(NO_SUCH_GROUP))
            return
        managers = list(
            await ChatAssignmentService(session).managers_of(chat_row_id=resolution.chat.id)
        )
        name = resolution.chat.display_name

    if not managers:
        lead = role_label(Role.TEAM_LEAD)
        await formatting.answer(
            message,
            formatting.escape(f"Group {name} hiện chưa có {lead} nào quản lý."),
        )
        return
    lines = [formatting.escape(f"Group {name} đang được quản lý bởi:")]
    lines.extend(formatting.escape(f"• {row.full_name}") for row in managers)
    await formatting.answer(message, "\n".join(lines))
