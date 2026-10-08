"""Registering groups, writing announcements, and checking what got sent.

**No handler here sends a cross-chat business message.** They reply into the
chat the person is typing in - a preview, a refusal, a confirmation - and
anything destined for somebody else goes through
:class:`~meobot.application.notification_router.NotificationRouter` into the
outbox, where a worker picks it up. That is the rule the whole release exists to
establish, and it is visible here as an absence: there is no ``bot.send_message``
to another chat in this file.

Registration happens *in the group being registered*. A private chat claiming to
be a group is refused, because that is exactly the shape an attempt to register
a destination out of band would take.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from meobot.application.announcement_service import AnnouncementService, default_purpose_for
from meobot.application.audit_service import AuditService
from meobot.application.chat_registry_service import ChatRegistryService
from meobot.application.member_interaction_service import ButtonSpec
from meobot.application.outbox_service import OutboxService
from meobot.application.recipient_resolver import ASK_WHICH_GROUP, RecipientResolver
from meobot.bot import formatting, member_keyboards
from meobot.bot.member_filters import InMemberFlow, MemberIntentFilter
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.dispatch.phrases import ContinuationKind, read_continuation
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member import copy
from meobot.domain.member.callbacks import data_pattern, parse
from meobot.domain.member.intents import MemberIntent
from meobot.domain.member.normalization import normalize
from meobot.domain.notifications.models import (
    ChatPurpose,
    PrivacyClassification,
    purpose_description,
    purpose_label,
    status_label,
)
from meobot.domain.notifications.naming import (
    display_name_for,
    extract_destination_name,
    extract_send_content,
    parse_group_name,
)

logger = get_logger(__name__)

router = Router(name="notifications")


class AnnouncementFlow(StatesGroup):
    """Composing one announcement. Backed by the same PostgreSQL FSM storage."""

    choosing_destination = State()
    confirming = State()


FLOW_STATES: tuple[str, ...] = tuple(
    str(state.state)
    for state in (AnnouncementFlow.choosing_destination, AnnouncementFlow.confirming)
)

REGISTER_ONLY_IN_GROUP = (
    "Bạn cần nhắn câu này **trong chính group** muốn đăng ký.\n"
    "Mở group đó rồi nhắn lại giúp mình nhé."
)
REGISTER_ASK_PURPOSE = (
    "Group này dùng để làm gì? Bạn nói rõ hơn giúp mình, ví dụ:\n"
    "“đăng ký đây là group thông báo toàn phòng” hoặc “đây là group của team Nội dung”."
)
REGISTERED = "✅ Đã đăng ký group này."
NOTHING_REGISTERED = "Chưa có group nào được đăng ký."
NOTHING_PENDING = "Hiện không có tin nào đang chờ gửi hoặc gửi lỗi."
ANNOUNCEMENT_QUEUED = "Thông báo đã được xếp hàng gửi tới {destination}."

#: Where a pending registration lives between the preview and the press. The
#: FSM store is PostgreSQL, so this survives a bot restart - and it is keyed by
#: (bot, chat, user), so two people registering two groups cannot collide.
REGISTER_DRAFT_KEY = "chat_registration"

#: How long a registration preview stays pressable. Same order as the signed
#: callback's own expiry; the draft is what carries the *name*, which the 64
#: byte callback has no room for.
REGISTER_DRAFT_TTL = timedelta(hours=1)


# --- Registering a group ----------------------------------------------------
@router.message(MemberIntentFilter({MemberIntent.REGISTER_CHAT}, allow_in_flow=True))
async def handle_register_chat(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    member_text: str,
) -> None:
    """Register the group this was said in, after a preview and a press.

    The group's identity is ``message.chat.id`` and nothing else. What the
    sentence contributes is a *name* - "đăng ký group này làm group Test" - and
    since 0.6.0a2.1 a name that matches none of the eight built-in purposes is
    no longer a dead end: it is registered as a general destination under the
    name the owner chose.
    """
    if message.chat.type == "private":
        await formatting.answer(message, formatting.escape(REGISTER_ONLY_IN_GROUP))
        return
    if message.bot is None:  # pragma: no cover
        return

    purpose = default_purpose_for(member_text)
    parsed_name = parse_group_name(member_text)
    if purpose is not None:
        # A named purpose carries its own display name, which is the one the
        # rest of MeoBot already uses in menus and delivery reports.
        display = purpose_label(purpose)
    else:
        purpose = ChatPurpose.GENERAL
        display = display_name_for(parsed_name, telegram_title=message.chat.title)
    if not display:
        # Neither a purpose, nor a name, nor a Telegram title to fall back on.
        await formatting.answer(message, formatting.escape(REGISTER_ASK_PURPOSE))
        return

    draft_id = uuid.uuid4()
    await state.update_data(
        **{
            REGISTER_DRAFT_KEY: {
                "id": str(draft_id),
                "display_name": display,
                "purpose": purpose.value,
                "telegram_chat_id": message.chat.id,
                "telegram_title": message.chat.title,
                "expires_at": (utcnow() + REGISTER_DRAFT_TTL).isoformat(),
                "version": 1,
            }
        }
    )

    body = "\n".join(
        [
            "Bạn đang đăng ký group:",
            "",
            formatting.escape(f"• Tên Telegram: {message.chat.title or 'không rõ'}"),
            formatting.escape(f"• Tên sử dụng: {display}"),
            formatting.escape(f"• Mục đích: {purpose_description(purpose)}"),
            formatting.escape("• Cho phép TasksBot gửi tự động: Có"),
            formatting.escape("• Dữ liệu cá nhân: Không được hiển thị"),
        ]
    )
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id if message.from_user else 0,
        chat_id=message.chat.id,
    )
    rows = [
        [ButtonSpec("✅ Xác nhận đăng ký", "chat.register.confirm", str(draft_id))],
        [ButtonSpec(copy.Button.DISCARD.value, "chat.register.cancel")],
    ]
    await formatting.answer(
        message,
        body,
        reply_markup=member_keyboards.render(rows, settings=settings, binding=binding),
    )


REGISTER_ACTIONS = ("chat.register.confirm", "chat.register.cancel")

REGISTER_DRAFT_GONE = (
    "Lời mời đăng ký này đã hết hạn. Bạn nhắn lại “đăng ký group này” giúp mình nhé."
)


@router.callback_query(F.data.regexp(data_pattern(REGISTER_ACTIONS)))
async def handle_register_confirm(
    query: CallbackQuery,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Commit a group registration. Idempotent and audited.

    The name and purpose come from the draft the preview stored, not from the
    callback: 64 bytes has room for a signature, an id and a version, and never
    had room for "Group Test". The signed callback still decides *whether* the
    press counts - it binds the presser, the chat, the bot and the draft id -
    and the draft decides *what* was previewed.
    """
    await query.answer()
    if query.message is None or query.bot is None or query.data is None:  # pragma: no cover
        return

    binding = member_keyboards.binding_for(
        bot_id=query.bot.id,
        telegram_user_id=query.from_user.id,
        chat_id=query.message.chat.id,
    )
    payload = parse(query.data, secret=settings.callback_secret, binding=binding)
    if payload is None:
        await query.answer(copy.Problem.BUTTON_NOT_YOURS.value, show_alert=True)
        return
    if payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(copy.Problem.BUTTON_EXPIRED.value))
        return
    if payload.action == "chat.register.cancel":
        await state.update_data(**{REGISTER_DRAFT_KEY: None})
        await formatting.edit_callback(query, formatting.escape(copy.CANCELLED))
        return

    chat = query.message.chat
    draft = _registration_draft(await state.get_data(), draft_id=payload.entity_id, chat_id=chat.id)
    if draft is None:
        await formatting.edit_callback(query, formatting.escape(REGISTER_DRAFT_GONE))
        return
    purpose, display_name = draft

    try:
        async with database.transaction() as session:
            registry = ChatRegistryService(session)
            row = await registry.register(
                actor=actor,
                bot_identity=query.bot.id,
                telegram_chat_id=chat.id,
                chat_type=chat.type,
                telegram_title=chat.title,
                display_name=display_name,
                purpose=purpose,
                privacy_level=PrivacyClassification.PUBLIC_OPERATIONAL,
            )
            await AuditService(session).record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.CHAT_REGISTERED.value,
                result=AuditResult.SUCCESS,
                entity_type="telegram_chat",
                entity_id=str(row.id),
                after_data={
                    "source_chat_id": chat.id,
                    "destination_chat_id": chat.id,
                    "purpose": purpose.value,
                    "actor_role": actor.role.value,
                    "actor_role_label": role_label(actor.role),
                },
            )
            summary = "\n".join(
                [
                    f"✅ Đã đăng ký {row.display_name}.",
                    "",
                    "Từ bây giờ bạn có thể nhắn riêng:",
                    f"“Thông báo cho {row.display_name}: Chào buổi sáng.”",
                ]
            )
    except MeoBotError as exc:
        await formatting.edit_callback(query, "⛔ " + formatting.escape(exc.message))
        return

    await state.update_data(**{REGISTER_DRAFT_KEY: None})
    await formatting.edit_callback(query, formatting.escape(summary))


def _registration_draft(
    data: dict[str, object], *, draft_id: uuid.UUID | None, chat_id: int
) -> tuple[ChatPurpose, str] | None:
    """Read back the previewed registration, or ``None`` if it cannot be trusted.

    Four things have to agree before a press means anything: the draft exists,
    it is the draft this button was drawn for, it was previewed in *this* chat,
    and it has not expired. A mismatch is not an error to explain in detail - it
    is a stale card, and the honest answer is to ask for the sentence again.
    """
    raw = data.get(REGISTER_DRAFT_KEY)
    if not isinstance(raw, dict):
        return None
    if draft_id is None or str(raw.get("id")) != str(draft_id):
        return None
    if int(raw.get("telegram_chat_id") or 0) != chat_id:
        return None
    try:
        expires_at = datetime.fromisoformat(str(raw.get("expires_at")))
    except ValueError:  # pragma: no cover - written by this module only
        return None
    if utcnow() >= expires_at:
        return None
    try:
        purpose = ChatPurpose(str(raw.get("purpose")))
    except ValueError:  # pragma: no cover - written by this module only
        return None
    display_name = str(raw.get("display_name") or "").strip()
    return (purpose, display_name) if display_name else None


# --- Listing destinations ---------------------------------------------------
@router.message(MemberIntentFilter({MemberIntent.VIEW_REGISTERED_CHATS}))
async def handle_list_chats(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """Show what MeoBot may send to."""
    if not AnnouncementService.may_broadcast(actor):
        await formatting.answer(message, formatting.escape(copy.Problem.NO_PERMISSION.value))
        return
    if message.bot is None:  # pragma: no cover
        return

    async with database.session() as session:
        rows = await ChatRegistryService(session).all_registered(bot_identity=message.bot.id)
    if not rows:
        await formatting.answer(message, formatting.escape(NOTHING_REGISTERED))
        return

    lines = ["👥 " + formatting.bold("Group đã đăng ký")]
    for index, row in enumerate(rows, start=1):
        state = "đang dùng" if row.is_active and row.bot_can_send else "đang tắt"
        lines.append(formatting.escape(f"{index}. {row.display_name} — {state}"))
    await formatting.answer(message, "\n".join(lines))


# --- Delivery status --------------------------------------------------------
@router.message(MemberIntentFilter({MemberIntent.VIEW_DELIVERY_STATUS}))
async def handle_delivery_status(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """ "Tin nào đang gửi lỗi?" - status in Vietnamese, never a raw state."""
    if not AnnouncementService.may_broadcast(actor):
        await formatting.answer(message, formatting.escape(copy.Problem.NO_PERMISSION.value))
        return

    async with database.session() as session:
        rows = await OutboxService(session, settings).unsettled(limit=10)
    if not rows:
        await formatting.answer(message, formatting.escape(NOTHING_PENDING))
        return

    lines = ["📬 " + formatting.bold("Tình trạng gửi tin")]
    for index, row in enumerate(rows, start=1):
        lines.append(
            formatting.escape(
                f"{index}. {_event_label(row.event_type)} — {status_label(row.status)}"
            )
        )
    await formatting.answer(message, "\n".join(lines))


def _event_label(event_type: str) -> str:
    """Vietnamese name for an event, so no internal key reaches a person."""
    return {
        "announcement_published": "Thông báo phòng",
        "hr_request_submitted": "Yêu cầu nghỉ phép / đi muộn",
        "hr_request_approved": "Kết quả duyệt",
        "hr_request_rejected": "Kết quả từ chối",
        "hr_more_info_requested": "Yêu cầu bổ sung thông tin",
        "hr_attendance_update": "Cập nhật nhân sự",
        "announcement_reminder": "Nhắc xác nhận thông báo",
    }.get(event_type, "Tin nhắn")


@router.message(MemberIntentFilter({MemberIntent.RETRY_DELIVERY}))
async def handle_retry_delivery(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """ "Gửi lại thông báo chưa gửi được." Queues them; the worker sends."""
    if not AnnouncementService.may_broadcast(actor):
        await formatting.answer(message, formatting.escape(copy.Problem.NO_PERMISSION.value))
        return

    async with database.transaction() as session:
        outbox = OutboxService(session, settings)
        rows = [row for row in await outbox.unsettled(limit=20) if row.status.is_settled]
        for row in rows:
            await outbox.retry_now(row)
    count = len(rows)
    text = f"🔄 Đã xếp lại {count} tin để gửi tiếp." if count else "Không có tin nào cần gửi lại."
    await formatting.answer(message, formatting.escape(text))


# --- Announcements ----------------------------------------------------------
@router.message(MemberIntentFilter({MemberIntent.MAKE_ANNOUNCEMENT}))
async def handle_announcement(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    member_text: str,
) -> None:
    """Draft an announcement and show it back before anything is sent."""
    if not AnnouncementService.may_broadcast(actor):
        await formatting.answer(
            message,
            formatting.escape(
                "Bạn chưa được phép gửi thông báo tới group. "
                f"Việc này thuộc quyền {role_label(Role.OWNER)}."
            ),
        )
        return
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return

    content = _announcement_body(member_text)
    if not content:
        await formatting.answer(
            message,
            formatting.escape("Bạn viết giúp mình nội dung thông báo nhé."),
        )
        return

    async with database.transaction() as session:
        resolution = await RecipientResolver(session, settings).resolve_chat(
            bot_identity=message.bot.id, text=member_text
        )
        service = AnnouncementService(session, settings, AuditService(session))

        if not resolution.is_resolved and not resolution.is_ambiguous:
            # A destination that is not registered is a registry problem and
            # must read like one. The reported failure answered this case with
            # BotFather and token instructions for a bot that was already
            # running - so no draft is created and nothing is queued here.
            await _unknown_destination(
                message, settings=settings, name=extract_destination_name(member_text)
            )
            return

        if resolution.is_ambiguous:
            draft = await service.draft(
                actor=actor,
                content=content,
                source_chat_id=message.chat.id,
                destination=None,
            )
            candidates = [(row.id, row.display_name) for row in resolution.candidates]
            draft_id = draft.id
            await state.set_state(AnnouncementFlow.choosing_destination)
            await state.update_data(announcement_draft_id=str(draft_id))
            await _ask_destination(
                message, settings=settings, draft_id=draft_id, candidates=candidates
            )
            return

        draft = await service.draft(
            actor=actor,
            content=content,
            source_chat_id=message.chat.id,
            destination=resolution.chat,
        )
        destination_name = resolution.chat.display_name if resolution.chat else ""
        draft_id = draft.id

    await state.set_state(AnnouncementFlow.confirming)
    await state.update_data(announcement_draft_id=str(draft_id))
    await _preview(
        message,
        settings=settings,
        draft_id=draft_id,
        content=content,
        destination_name=destination_name,
    )


def _announcement_body(text: str) -> str:
    """Strip the addressing and keep what the person actually wants said.

    "Gửi “Chào buổi sáng” vào group Test." must reach the group as *Chào buổi
    sáng* and not as the whole instruction - including the group's own name,
    which read as though MeoBot were talking to itself.
    """
    explicit = extract_send_content(text)
    if explicit:
        return explicit

    folded = normalize(text).matchable
    for marker in ("thong bao cho toan phong", "thong bao", "gui thong bao", "nhac"):
        index = folded.find(marker)
        if index >= 0:
            tail = text[index + len(marker) :]
            return tail.lstrip(" :,-\n").strip()
    return text.strip()


async def _unknown_destination(message: Message, *, settings: Settings, name: str) -> None:
    """Say which group was not found, and offer the registry.

    Deliberately specific about the *name*: "chưa tìm thấy group phù hợp" left
    the owner guessing whether MeoBot had misread the name, lost the group, or
    was not connected at all - and the model filled that gap with setup
    instructions.
    """
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    quoted = f"“{name}” " if name else ""
    body = "\n".join(
        [
            formatting.escape(f"TasksBot chưa tìm thấy group {quoted}trong danh sách đã đăng ký."),
            "",
            formatting.escape(
                "Bạn vào chính group đó và nhắn “đăng ký group này” để TasksBot ghi nhận nhé."
            ),
        ]
    )
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
    )
    rows = [
        [ButtonSpec("👥 Xem các group", "announce.destinations")],
        [ButtonSpec("✏️ Chọn lại nơi nhận", "announce.retarget")],
        [ButtonSpec(copy.Button.DISCARD.value, "announce.cancel")],
    ]
    await formatting.answer(
        message,
        body,
        reply_markup=member_keyboards.render(rows, settings=settings, binding=binding),
    )


async def _preview(
    message: Message,
    *,
    settings: Settings,
    draft_id: uuid.UUID,
    content: str,
    destination_name: str,
) -> None:
    """The mandatory look-before-you-send card."""
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    body = "\n".join(
        [
            "📢 " + formatting.bold("THÔNG BÁO"),
            "",
            formatting.escape(content),
            "",
            formatting.bold("Nơi nhận"),
            formatting.escape(f"• {destination_name}"),
            "",
            formatting.bold("Người gửi"),
            formatting.escape(f"• {role_label(Role.OWNER)}"),
        ]
    )
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
    )
    rows = [
        [ButtonSpec("✅ Gửi thông báo", "announce.send", str(draft_id))],
        [ButtonSpec(copy.Button.DISCARD.value, "announce.cancel", str(draft_id))],
    ]
    await formatting.answer(
        message,
        body,
        reply_markup=member_keyboards.render(rows, settings=settings, binding=binding),
    )


async def _ask_destination(
    message: Message,
    *,
    settings: Settings,
    draft_id: uuid.UUID,
    candidates: list[tuple[uuid.UUID, str]],
) -> None:
    """Exactly one question when two groups could be meant."""
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
    )
    rows = [
        [ButtonSpec(name, "announce.pick", str(chat_id))]
        for chat_id, name in candidates[: settings.member_max_button_items]
    ]
    rows.append([ButtonSpec(copy.Button.DISCARD.value, "announce.cancel", str(draft_id))])
    await formatting.answer(
        message,
        formatting.escape(ASK_WHICH_GROUP),
        reply_markup=member_keyboards.render(rows, settings=settings, binding=binding),
    )


ANNOUNCE_ACTIONS = (
    "announce.send",
    "announce.cancel",
    "announce.pick",
    "announce.destinations",
    "announce.retarget",
)

RETARGET_HELP = (
    "Bạn nhắn lại theo mẫu này giúp mình nhé:\n“Thông báo cho <tên group đã đăng ký>: <nội dung>.”"
)


@router.callback_query(F.data.regexp(data_pattern(ANNOUNCE_ACTIONS)))
async def handle_announcement_button(
    query: CallbackQuery,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Confirm, choose a destination for, or abandon a draft."""
    await query.answer()
    if query.message is None or query.bot is None or query.data is None:  # pragma: no cover
        return

    binding = member_keyboards.binding_for(
        bot_id=query.bot.id,
        telegram_user_id=query.from_user.id,
        chat_id=query.message.chat.id,
    )
    payload = parse(query.data, secret=settings.callback_secret, binding=binding)
    if payload is None:
        await query.answer(copy.Problem.BUTTON_NOT_YOURS.value, show_alert=True)
        return
    if payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(copy.Problem.BUTTON_EXPIRED.value))
        return
    # The two buttons offered when nothing was found carry no entity, because
    # no draft was created - so they are answered before the id is required.
    if payload.action == "announce.destinations":
        await _show_destinations(query, database=database)
        return
    if payload.action == "announce.retarget":
        await formatting.edit_callback(query, formatting.escape(RETARGET_HELP))
        return

    if payload.entity_id is None:
        # A cancel offered next to those two: nothing was written, so there is
        # nothing to roll back.
        await state.clear()
        await formatting.edit_callback(query, formatting.escape(copy.CANCELLED))
        return

    if payload.action == "announce.cancel":
        await state.clear()
        async with database.transaction() as session:
            service = AnnouncementService(session, settings, AuditService(session))
            # Already published means nothing to cancel, and nothing to
            # correct: a sent announcement cannot be recalled.
            with contextlib.suppress(MeoBotError):
                await service.cancel(announcement_id=payload.entity_id)
        await formatting.edit_callback(query, formatting.escape(copy.CANCELLED))
        return

    if payload.action == "announce.pick":
        await _pick_destination(
            query,
            settings=settings,
            database=database,
            actor=actor,
            chat_id=payload.entity_id,
            state=state,
        )
        return

    # announce.send
    await state.clear()
    try:
        async with database.transaction() as session:
            service = AnnouncementService(session, settings, AuditService(session))
            row, result = await service.publish(
                actor=actor, request_id=request_id, announcement_id=payload.entity_id
            )
            refusals = list(result.user_messages)
            queued = len(result.queued)
            # ``publish`` has already refused a draft with no destination, so
            # this is present - but the column is nullable and mypy is right to
            # say so, and "nơi nhận" is a safe thing to call an unnamed group.
            destination = (
                await ChatRegistryService(session).by_id(row.destination_chat_id)
                if row.destination_chat_id is not None
                else None
            )
            destination_name = destination.display_name if destination is not None else "nơi nhận"
    except MeoBotError as exc:
        await formatting.edit_callback(query, "⛔ " + formatting.escape(exc.message))
        return

    if refusals:
        await formatting.edit_callback(query, formatting.escape("\n".join(refusals)))
        return
    # "Xếp hàng gửi", not "đã gửi". An outbox row is a durable intention; the
    # worker settles it with Telegram afterwards and reports the real outcome.
    # Saying "đã gửi" here is the lie the release was opened to remove.
    await formatting.edit_callback(
        query,
        formatting.escape(
            ANNOUNCEMENT_QUEUED.format(destination=destination_name)
            if queued
            else "Thông báo này đã được xếp hàng gửi trước đó rồi."
        ),
    )


async def _pick_destination(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    actor: Actor,
    chat_id: uuid.UUID,
    state: FSMContext,
) -> None:
    """Attach the chosen group to the draft, then preview it."""
    data = await state.get_data()
    draft_id = data.get("announcement_draft_id")
    async with database.transaction() as session:
        registry = ChatRegistryService(session)
        destination = await registry.by_id(chat_id)
        if destination is None or draft_id is None:
            await formatting.edit_callback(
                query, formatting.escape(copy.Problem.BUTTON_EXPIRED.value)
            )
            return
        service = AnnouncementService(session, settings, AuditService(session))
        draft = await service.set_destination(
            actor=actor,
            announcement_id=uuid.UUID(str(draft_id)),
            destination=destination,
        )
        content = draft.content
        name = destination.display_name

    if isinstance(query.message, Message):
        await state.set_state(AnnouncementFlow.confirming)
        await _preview(
            query.message,
            settings=settings,
            draft_id=uuid.UUID(str(draft_id)),
            content=content,
            destination_name=name,
        )


async def _show_destinations(query: CallbackQuery, *, database: Database) -> None:
    """The registered-group list, in place of the card that could not resolve."""
    if query.bot is None:  # pragma: no cover
        return
    async with database.session() as session:
        rows = await ChatRegistryService(session).active(bot_identity=query.bot.id)
    if not rows:
        await formatting.edit_callback(
            query,
            formatting.escape(
                "Chưa có group nào được đăng ký.\n"
                "Bạn vào group cần dùng và nhắn “đăng ký group này” giúp mình nhé."
            ),
        )
        return
    lines = ["👥 " + formatting.bold("Group đã đăng ký")]
    lines.extend(formatting.escape(f"• {row.display_name}") for row in rows)
    await formatting.edit_callback(query, "\n".join(lines))


# --- Messaging one person ---------------------------------------------------
PRIVATE_SEND_UNAVAILABLE = (
    "TasksBot chưa gửi tin nhắn riêng cho từng người theo yêu cầu được.\n\n"
    "Hiện TasksBot gửi được tới các group đã đăng ký. Bạn nhắn ví dụ:\n"
    "“Thông báo cho <tên group đã đăng ký>: <nội dung>.”"
)


@router.message(MemberIntentFilter({MemberIntent.SEND_PRIVATE_MESSAGE}))
async def handle_private_send(message: Message, actor: Actor) -> None:
    """ "Nhắn riêng cho Linh." - recognised so it cannot be answered by a model.

    MeoBot has every piece except the one that matters: an announcement is
    addressed to a registered chat, and there is no aggregate for a message to
    one person. Rather than let this fall through to conversation - where the
    model would happily agree to send it - the limit is stated plainly.
    """
    await formatting.answer(message, formatting.escape(PRIVATE_SEND_UNAVAILABLE))


@router.message(InMemberFlow(*FLOW_STATES))
async def handle_flow_text(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """While a draft is open, plain text is not a new command.

    Without this the next message would be classified afresh and could start a
    second draft while the first is still on screen.

    Since 0.6.0a3 it also *answers* rather than only redirecting to the buttons.
    "Xác nhận" was reported as producing "Bạn muốn thực hiện thao tác gì và với
    đối tượng nào?", and telling somebody to press a button they can see is a
    better answer than that but still not the right one: the draft on screen is
    exactly what they just confirmed, and the words are read here by pattern -
    never by the conversation model.
    """
    reading = read_continuation(message.text or "", candidate_count=1)

    if reading.kind is ContinuationKind.CANCEL:
        await state.clear()
        await formatting.answer(message, formatting.escape(copy.CANCELLED))
        return

    if reading.kind is ContinuationKind.CONFIRM:
        await _confirm_single_draft(
            message,
            actor=actor,
            settings=settings,
            database=database,
            state=state,
            request_id=request_id,
        )
        return

    await formatting.answer(
        message,
        formatting.escape(
            "Bạn nhắn “xác nhận” để gửi, “huỷ” nếu không gửi nữa, "
            "hoặc bấm một trong các nút phía trên giúp mình nhé."
        ),
    )


async def _confirm_single_draft(
    message: Message,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Publish the draft this flow is holding, from a typed confirmation.

    Exactly what the ``announce.send`` button does, reached from the other
    direction. :meth:`AnnouncementService.publish` is idempotent, so a typed
    confirmation and a pressed button arriving together still queue one message.
    """
    data = await state.get_data()
    draft_id = data.get("announcement_draft_id")
    if draft_id is None:
        await formatting.answer(
            message,
            formatting.escape(
                "Thông báo bạn soạn trước đó không còn nữa. Bạn nhắn lại nội dung giúp mình nhé."
            ),
        )
        return

    await state.clear()
    try:
        async with database.transaction() as session:
            service = AnnouncementService(session, settings, AuditService(session))
            row, result = await service.publish(
                actor=actor, request_id=request_id, announcement_id=uuid.UUID(str(draft_id))
            )
            refusals = list(result.user_messages)
            queued = len(result.queued)
            destination = (
                await ChatRegistryService(session).by_id(row.destination_chat_id)
                if row.destination_chat_id is not None
                else None
            )
            name = destination.display_name if destination is not None else "nơi nhận"
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return

    if refusals:
        await formatting.answer(message, formatting.escape("\n".join(refusals)))
        return
    await formatting.answer(
        message,
        formatting.escape(
            ANNOUNCEMENT_QUEUED.format(destination=name)
            if queued
            else "Thông báo này đã được xếp hàng gửi trước đó rồi."
        ),
    )
