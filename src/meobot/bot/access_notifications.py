"""Rendering the owner's approval buttons, and acknowledging the stranger.

**Where the approval goes.** Privately to the owner, through the transactional
outbox. An approval message carries a stranger's Telegram id, the group they
are in, and five buttons that can create an account - none of which belongs in
a group chat if it can be avoided.

Since 0.6.0a2 nothing in this module sends to another chat. The owner's copy is
queued by the access gate in the same transaction as the request, and the
buttons below are rebuilt from the stored request at delivery time by
:mod:`~meobot.application.outbound_keyboards` - which is what keeps their
signatures fresh across a retry. What remains here is the acknowledgement in
the *source* chat, and a neutral fallback line for the case where there is no
owner to queue anything for.

**The mention is id-based.** ``tg://user?id=...`` pings the owner whether or not
they have a public username. Requiring the person who configured the bot to also
expose a ``@handle`` would be a strange thing to demand of them.

**The buttons are bound, not just signed.** Each one commits to the acting
owner, the requester, the chat, the source message, the request and an expiry -
see :mod:`meobot.domain.access.callbacks`. Forwarding a button to somebody else
does not make it work for them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.access_request_service import AccessRequestService
from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import format_local, utcnow
from meobot.db.models.access import PendingGuestAccessRequest
from meobot.db.session import Database
from meobot.domain.access.callbacks import (
    CallbackBinding,
    build_access_callback,
)
from meobot.domain.access.models import (
    CALLBACK_TTL,
    GUEST_DEFAULT_QUESTION_LIMIT,
    AccessAction,
    QuotaAction,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Role

logger = get_logger(__name__)

#: Plain, unsigned callback: it starts a request, it does not grant anything.
#: The grant itself is behind the owner's signed buttons.
QUOTA_REQUEST_CALLBACK = "quota:request"

NEUTRAL_GROUP_NOTICE = "Mình đã chuyển yêu cầu này tới {owner} để xin phép trả lời."


def short_telegram_id(telegram_user_id: int) -> str:
    """A recognisable but abbreviated id for a notification.

    Enough to tell two people apart in a message; not the full identifier
    printed into a chat log that may be screenshotted.
    """
    text = str(telegram_user_id)
    return text if len(text) <= 5 else f"…{text[-5:]}"


def owner_mention(settings: Settings) -> str:
    """An id-based mention of the configured owner, already escaped."""
    label = role_label(Role.OWNER)
    identifier = settings.meobot_owner_telegram_id
    if identifier is None:
        return formatting.escape(label)
    return f'<a href="tg://user?id={identifier}">{formatting.escape(label)}</a>'


def binding_for(request: PendingGuestAccessRequest, *, owner_telegram_id: int) -> CallbackBinding:
    """The facts every button on this request is signed against."""
    return CallbackBinding(
        owner_telegram_id=owner_telegram_id,
        subject_telegram_id=request.requester_telegram_id,
        telegram_chat_id=request.telegram_chat_id,
        source_message_id=request.source_message_id,
    )


def access_keyboard(
    request: PendingGuestAccessRequest,
    *,
    settings: Settings,
    owner_telegram_id: int,
    now: datetime | None = None,
) -> InlineKeyboardMarkup:
    """The five decisions an owner may take about one stranger."""
    expires_at = (now or utcnow()) + CALLBACK_TTL
    binding = binding_for(request, owner_telegram_id=owner_telegram_id)

    def button(action: AccessAction) -> str:
        return build_access_callback(
            action,
            secret=settings.callback_secret,
            request_id=request.id,
            binding=binding,
            expires_at=expires_at,
        )

    return formatting.keyboard(
        [
            [("💬 Trả lời một lần", button(AccessAction.ANSWER_ONCE))],
            [
                (
                    f"⏱ Guest: {GUEST_DEFAULT_QUESTION_LIMIT} lượt / 24 giờ",
                    button(AccessAction.GRANT_GUEST),
                )
            ],
            [(f"➕ Thêm làm {role_label(Role.EMPLOYEE)}", button(AccessAction.ADD_AS_MEMBER))],
            [("🚫 Không trả lời lần này", button(AccessAction.IGNORE_ONCE))],
            [("🔕 Luôn bỏ qua trong group", button(AccessAction.IGNORE_IN_GROUP))],
        ]
    )


def confirm_member_keyboard(
    request: PendingGuestAccessRequest,
    *,
    settings: Settings,
    owner_telegram_id: int,
    now: datetime | None = None,
) -> InlineKeyboardMarkup:
    """The second step of adding a Member: an explicit commit, or a refusal."""
    expires_at = (now or utcnow()) + CALLBACK_TTL
    binding = binding_for(request, owner_telegram_id=owner_telegram_id)
    return formatting.keyboard(
        [
            [
                (
                    f"✅ Xác nhận thêm {role_label(Role.EMPLOYEE)}",
                    build_access_callback(
                        AccessAction.CONFIRM_MEMBER,
                        secret=settings.callback_secret,
                        request_id=request.id,
                        binding=binding,
                        expires_at=expires_at,
                    ),
                )
            ],
            [
                (
                    "❌ Huỷ",
                    build_access_callback(
                        AccessAction.IGNORE_ONCE,
                        secret=settings.callback_secret,
                        request_id=request.id,
                        binding=binding,
                        expires_at=expires_at,
                    ),
                )
            ],
        ]
    )


def render_access_request(request: PendingGuestAccessRequest, *, settings: Settings) -> str:
    """The approval message body.

    Everything dynamic is escaped, and the question is the stored preview -
    already secret-redacted and truncated by
    :func:`~meobot.application.access_request_service.build_preview`.
    """
    tz = settings.timezone
    lines = [
        "🔐 " + formatting.bold("Có người muốn nói chuyện với TasksBot"),
        "",
        formatting.escape(f"Tên: {request.requester_display_name or 'không rõ'}"),
    ]
    if request.requester_username:
        lines.append(formatting.escape(f"Username: @{request.requester_username}"))
    lines.extend(
        [
            formatting.escape(f"Telegram ID: {short_telegram_id(request.requester_telegram_id)}"),
            formatting.escape(f"Group: {request.chat_title or request.telegram_chat_id}"),
            formatting.escape(
                f"Thời điểm hỏi: {format_local(request.created_at, tz, fmt='%H:%M %d/%m/%Y')}"
            ),
            formatting.escape(
                f"Yêu cầu hết hạn: {format_local(request.expires_at, tz, fmt='%H:%M %d/%m/%Y')}"
            ),
            "",
            formatting.bold("Nội dung"),
            formatting.escape(request.question_preview or "(không có nội dung)"),
        ]
    )
    return "\n".join(lines)


async def notify_owner_of_access_request(
    *,
    message: Message,
    database: Database,
    settings: Settings,
    request_id: uuid.UUID,
    acknowledge: str | None,
) -> None:
    """Acknowledge the stranger in the group they wrote in.

    **This no longer sends anything to the owner.** Since 0.6.0a2 the owner's
    copy is queued through the transactional outbox by the access gate, in the
    same transaction as the request itself - see
    :func:`~meobot.application.access_notifications_service.queue_access_request_notification`.
    What is left here is the one reply that belongs in the *source* chat.

    Why the change: this function used to call ``bot.send_message`` directly,
    so a Telegram failure at that moment lost the notification while leaving
    the request open. A stranger then waited indefinitely for a decision nobody
    had been asked to make. A queued row survives that, and retries.

    ``acknowledge`` is ``None`` for a repeat mention or a request inside a
    rejection cooldown - in those cases nothing is said to anybody, which is
    what stops one persistent stranger from filling two chats with noise.
    """
    if acknowledge is None:
        return

    async with database.transaction() as session:
        requests = AccessRequestService(session)
        stored = await requests.by_id(request_id)
        if stored is None:  # pragma: no cover - written moments ago
            return
        queued = bool(stored.notified_at) or await _has_queued_owner_copy(session, request_id)
        await requests.mark_notified(stored, owner_telegram_id=settings.meobot_owner_telegram_id)

    if queued:
        await formatting.answer(message, formatting.escape(acknowledge))
        return

    # Nobody could be queued for - usually no configured owner. Neutral only:
    # no stranger id, no group name, no buttons. A group is not the place for
    # an administrative menu.
    await formatting.answer(message, NEUTRAL_GROUP_NOTICE.format(owner=owner_mention(settings)))


async def _has_queued_owner_copy(session: AsyncSession, request_id: uuid.UUID) -> bool:
    """Whether the outbox already holds the owner's copy of this request."""
    from meobot.db.models.notifications import OutboundMessage

    result = await session.execute(
        select(OutboundMessage.id).where(
            OutboundMessage.aggregate_type == "access_request",
            OutboundMessage.aggregate_id == request_id,
        )
    )
    return result.first() is not None


def quota_request_keyboard() -> InlineKeyboardMarkup:
    """The "ask for more" button a member sees when their day runs out."""
    return formatting.keyboard([[("📨 Xin thêm lượt", QUOTA_REQUEST_CALLBACK)]])


def quota_decision_keyboard(
    *,
    settings: Settings,
    request_id: uuid.UUID,
    binding: CallbackBinding,
    now: datetime | None = None,
) -> InlineKeyboardMarkup:
    """What the owner may do about one member's request for more quota."""
    expires_at = (now or utcnow()) + CALLBACK_TTL

    def button(action: QuotaAction) -> str:
        return build_access_callback(
            action,
            secret=settings.callback_secret,
            request_id=request_id,
            binding=binding,
            expires_at=expires_at,
        )

    return formatting.keyboard(
        [
            [("➕ Thêm 10 lượt hôm nay", button(QuotaAction.ADD_10_TODAY))],
            [("♻️ Đặt lại lượt hôm nay", button(QuotaAction.RESET_TODAY))],
            [("⚙️ Nâng hạn mức lên 50/ngày", button(QuotaAction.SET_CUSTOM_LIMIT))],
            [("🚫 Từ chối", button(QuotaAction.DENY))],
        ]
    )
