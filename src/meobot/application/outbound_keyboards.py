"""Rebuilding a queued message's buttons at delivery time.

Some outbound messages need an inline keyboard - the owner's approval card, the
quota decision menu, an announcement's "✅ Đã đọc". A keyboard cannot simply be
stored in the payload: it carries signatures bound to an expiry, and a signature
minted when the row was written would be stale by the time a retried message is
delivered an hour later.

So the buttons are **rebuilt from the business record**, in the claiming
transaction, immediately before the send. That gives a fresh expiry on every
attempt and keeps the outbox payload to exactly the fields its template
declared - no markup, no signature, no secret in a durable row.

Anything not listed here delivers as plain text, which is the common case.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.notifications import OutboundMessage
from meobot.domain.notifications.models import NotificationEvent

logger = get_logger(__name__)


async def markup_for(
    session: AsyncSession, settings: Settings, message: OutboundMessage
) -> dict[str, Any] | None:
    """The inline keyboard one queued message should carry, if any.

    Returns ``None`` for the ordinary case - most notifications are text.
    Never raises: a keyboard that cannot be built degrades to a message
    without buttons, which still tells somebody what happened.
    """
    try:
        return await _markup_for(session, settings, message)
    except Exception:  # pragma: no cover - a keyboard is never worth a failure
        logger.warning(
            "outbound_keyboard_build_failed",
            extra={"outbound_message_id": str(message.id), "event_type": message.event_type},
        )
        return None


async def _markup_for(
    session: AsyncSession, settings: Settings, message: OutboundMessage
) -> dict[str, Any] | None:
    aggregate_id = message.aggregate_id
    if aggregate_id is None:
        return None

    if message.event_type == NotificationEvent.ACCESS_REQUEST_SUBMITTED.value:
        return await _access_request_markup(session, settings, message)
    if message.event_type == NotificationEvent.QUOTA_REQUEST_SUBMITTED.value:
        return await _quota_request_markup(session, settings, message)
    if message.event_type == NotificationEvent.ANNOUNCEMENT_PUBLISHED.value:
        return await _announcement_markup(session, settings, message)
    return None


async def _access_request_markup(
    session: AsyncSession, settings: Settings, message: OutboundMessage
) -> dict[str, Any] | None:
    """The owner's five decisions, signed against the acting owner.

    The binding names ``telegram_chat_id`` of the delivery - the owner's own
    private chat - as the acting owner, exactly as the direct-send version did,
    so a forwarded button still fails for anybody else.
    """
    from meobot.application.access_request_service import AccessRequestService
    from meobot.bot.access_notifications import access_keyboard

    request = await AccessRequestService(session).by_id(_require_id(message))
    if request is None:
        return None
    keyboard = access_keyboard(
        request, settings=settings, owner_telegram_id=message.telegram_chat_id
    )
    return keyboard.model_dump(exclude_none=True)


async def _quota_request_markup(
    session: AsyncSession, settings: Settings, message: OutboundMessage
) -> dict[str, Any] | None:
    """What the owner may do about one member's allowance request."""
    from meobot.application.quota_request_service import QuotaRequestService
    from meobot.bot.access_notifications import quota_decision_keyboard
    from meobot.domain.access.callbacks import CallbackBinding

    request = await QuotaRequestService(session, settings).by_id(_require_id(message))
    if request is None:
        return None
    keyboard = quota_decision_keyboard(
        settings=settings,
        request_id=request.id,
        binding=CallbackBinding(
            owner_telegram_id=message.telegram_chat_id,
            subject_telegram_id=request.requester_telegram_id,
            telegram_chat_id=request.telegram_chat_id,
            source_message_id=request.source_message_id,
        ),
    )
    return keyboard.model_dump(exclude_none=True)


async def _announcement_markup(
    session: AsyncSession, settings: Settings, message: OutboundMessage
) -> dict[str, Any] | None:
    """ "✅ Đã đọc" and "❓ Tôi cần hỏi thêm", when the author asked for receipts.

    The acting user is bound at *press* time rather than here: one group
    message is seen by many people, so the button cannot be bound to a
    recipient. Who pressed it comes from the update itself, which is not
    something a forwarded button can change.
    """
    from meobot.bot.announcement_keyboards import read_receipt_keyboard
    from meobot.db.models.notifications import Announcement, TelegramChat

    if not settings.announcement_read_receipt_enabled:
        return None
    if not message.safe_payload_json.get("request_read_receipt"):
        return None

    announcement = await session.get(Announcement, _require_id(message))
    chat = (
        await session.get(TelegramChat, message.registered_chat_id)
        if message.registered_chat_id is not None
        else None
    )
    keyboard = read_receipt_keyboard(
        announcement_id=_require_id(message),
        settings=settings,
        chat_id=message.telegram_chat_id,
        bot_id=chat.bot_identity if chat is not None else 0,
        version=announcement.version if announcement is not None else 1,
    )
    return keyboard.model_dump(exclude_none=True) if keyboard is not None else None


def _require_id(message: OutboundMessage) -> uuid.UUID:
    """The aggregate id, which ``_markup_for`` has already checked is present."""
    assert message.aggregate_id is not None
    return message.aggregate_id
