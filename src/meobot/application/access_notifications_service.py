"""The two 0.5.0 flows that used to send directly, moved onto the outbox.

Both were written before the transactional outbox existed, and both had the
same defect: the handler called ``bot.send_message`` itself, so a Telegram
failure at that exact moment lost the notification while leaving the business
record open. A stranger waited for a decision nobody had been asked to make; a
member waited for quota nobody knew they wanted.

Now the request and the intent to tell somebody about it commit together, and
a delivery failure is a retry rather than a lost message. The request is never
deleted or invalidated because its notification failed - the two are separate
concerns, which is the whole point of the outbox.

**Privacy is checked before the row exists.** Both templates are
``PERSONAL_PRIVATE``: a stranger's words and a named member's remaining
allowance are private-chat-only, and
:func:`~meobot.domain.notifications.routing.may_route` refuses a group
destination before anything durable is written.

**The neutral group fallback is still neutral.** When the owner cannot be
reached privately, the group sees one sentence naming the owner and nothing
else - no stranger id, no group name, no administrative buttons. It is queued
through the outbox like everything else.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    access_decision_key,
    access_request_key,
    quota_decision_key,
    quota_request_key,
)
from meobot.application.recipient_resolver import RecipientResolver
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.access import PendingGuestAccessRequest
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.quota import QuotaRequest
from meobot.domain.notifications.models import NotificationEvent

logger = get_logger(__name__)

#: What the group sees when the owner cannot be reached privately. Deliberately
#: says nothing about who asked or what they asked.
NEUTRAL_GROUP_NOTICE = "Mình đã chuyển yêu cầu này tới Trưởng phòng để xin phép trả lời."


async def queue_access_request_notification(
    session: AsyncSession,
    settings: Settings,
    *,
    request: PendingGuestAccessRequest,
    now: datetime | None = None,
) -> OutboundMessage | None:
    """Queue the owner's copy of one stranger's request.

    Returns:
        The queued row, or ``None`` when there is no owner to tell. A missing
        owner is a configuration problem, not a reason to drop the request -
        the row stays open and the buttons still work once one is configured.
    """
    owner = await RecipientResolver(session, settings).owner()
    if not owner.is_resolved or owner.telegram_chat_id is None:
        logger.info("access_request_owner_unreachable", extra={"request_id": str(request.id)})
        return None

    router = NotificationRouter(session, settings)
    result = await router.route(
        [
            RouteRequest(
                event_type=NotificationEvent.ACCESS_REQUEST_SUBMITTED,
                template_key="access.request_to_owner",
                payload={
                    "requester_name": request.requester_display_name or "không rõ",
                    "chat_label": request.chat_title or "một group",
                    # Already redacted and truncated when the request was
                    # opened - this is the preview the owner will act on.
                    "question_preview": request.question_preview or "",
                },
                idempotency_key=access_request_key(request.id, owner.telegram_chat_id),
                aggregate_type="access_request",
                aggregate_id=request.id,
                private_chat_id=owner.telegram_chat_id,
                source_chat_id=request.telegram_chat_id,
                destination_label="Chat riêng của Trưởng phòng",
                business_summary="Yêu cầu dùng MeoBot của một người lạ",
            )
        ]
    )
    queued = result.queued or result.duplicates
    return queued[0] if queued else None


async def queue_access_decision_notification(
    session: AsyncSession,
    settings: Settings,
    *,
    request: PendingGuestAccessRequest,
    action: str,
    outcome: str,
) -> OutboundMessage | None:
    """Tell the requester what the owner decided, in their own group.

    Only for decisions worth telling somebody about. Being ignored is not one:
    telling a stranger "you were refused" turns a silent decision into a
    conversation the owner chose not to have.
    """
    result = await NotificationRouter(session, settings).route(
        [
            RouteRequest(
                event_type=NotificationEvent.ACCESS_REQUEST_DECIDED,
                template_key="access.decision_to_requester",
                payload={"outcome": outcome},
                idempotency_key=access_decision_key(request.id, action),
                aggregate_type="access_request",
                aggregate_id=request.id,
                private_chat_id=request.telegram_chat_id,
                source_chat_id=request.telegram_chat_id,
                destination_label="Group người hỏi",
                business_summary="Kết quả yêu cầu dùng MeoBot",
            )
        ]
    )
    queued = result.queued or result.duplicates
    return queued[0] if queued else None


async def queue_quota_request_notification(
    session: AsyncSession,
    settings: Settings,
    *,
    request: QuotaRequest,
) -> OutboundMessage | None:
    """Queue the owner's copy of one member's request for more allowance."""
    owner = await RecipientResolver(session, settings).owner()
    if not owner.is_resolved or owner.telegram_chat_id is None:
        logger.info("quota_request_owner_unreachable", extra={"request_id": str(request.id)})
        return None

    result = await NotificationRouter(session, settings).route(
        [
            RouteRequest(
                event_type=NotificationEvent.QUOTA_REQUEST_SUBMITTED,
                template_key="quota.request_to_owner",
                payload={
                    "requester_name": request.requester_display_name
                    or str(request.requester_telegram_id),
                    "current_limit": request.limit_at_request,
                },
                idempotency_key=quota_request_key(request.id, owner.telegram_chat_id),
                aggregate_type="quota_request",
                aggregate_id=request.id,
                private_chat_id=owner.telegram_chat_id,
                source_chat_id=request.telegram_chat_id,
                created_by_user_id=request.user_id,
                destination_label="Chat riêng của Trưởng phòng",
                business_summary="Yêu cầu thêm lượt trò chuyện",
            )
        ]
    )
    queued = result.queued or result.duplicates
    return queued[0] if queued else None


async def queue_quota_decision_notification(
    session: AsyncSession,
    settings: Settings,
    *,
    request: QuotaRequest,
    action: str,
    outcome: str,
) -> OutboundMessage | None:
    """Tell the member what the owner decided about their allowance."""
    resolver = RecipientResolver(session, settings)
    destination = await resolver.private_destination(user_id=request.user_id)
    if not destination.is_resolved or destination.telegram_chat_id is None:
        return None

    result = await NotificationRouter(session, settings).route(
        [
            RouteRequest(
                event_type=NotificationEvent.QUOTA_REQUEST_DECIDED,
                template_key="quota.decision_to_member",
                payload={"outcome": outcome},
                idempotency_key=quota_decision_key(request.id, action),
                aggregate_type="quota_request",
                aggregate_id=request.id,
                recipient_user_id=request.user_id,
                private_chat_id=destination.telegram_chat_id,
                destination_label="Chat riêng",
                business_summary="Kết quả xin thêm lượt trò chuyện",
            )
        ]
    )
    queued = result.queued or result.duplicates
    return queued[0] if queued else None
