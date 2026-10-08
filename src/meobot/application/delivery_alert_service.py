"""Telling somebody their message did not arrive, before they think to ask.

Until 0.6.0a2 a permanently failed message sat in the outbox and the only way
to discover it was to ask *"tin nào chưa gửi được?"*. Somebody who does not know
a message failed does not know to ask, which made the honest answer to "did the
team get my announcement?" unavailable to the only person who needed it.

Four rules hold this together, and each one exists because the obvious
implementation gets it wrong:

**Alert once per terminal transition.** ``failure_alert_sent`` is set inside the
same transaction that settles the failure. So the guarantee does not depend on
the alert being delivered - a second sweep, a restart or a manual retry cannot
produce a second alert about the same failure.

**An alert can never alert about itself.** Alerts are written with
``is_alert=True`` and are skipped by this service. Without that, a recipient
whose private chat is unreachable would produce an alert, whose failure would
produce an alert, forever.

**No provider text, ever.** The reason a user sees is one of a fixed set of
Vietnamese sentences chosen from a :class:`FailureCategory`. Telegram's own
error strings can echo message content, and an exception name helps nobody.

**No identifiers.** A numeric chat id is not a name. The destination's display
name is captured when the message is created, so the alert can name it even if
the registration has since been removed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    delivery_failure_key,
    delivery_recovery_key,
)
from meobot.application.recipient_resolver import RecipientResolver
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.notifications import OutboundMessage
from meobot.domain.notifications.models import (
    FailureCategory,
    NotificationEvent,
    OutboxStatus,
)

logger = get_logger(__name__)

#: What each failure means, in words somebody can act on. Deliberately about
#: *what to do*, not about what Telegram returned.
REASON_LABELS: dict[FailureCategory, str] = {
    FailureCategory.PRIVATE_CHAT_UNAVAILABLE: (
        "Người nhận chưa bắt đầu cuộc trò chuyện với TasksBot, nên TasksBot chưa "
        "nhắn riêng cho họ được."
    ),
    FailureCategory.BOT_NOT_IN_CHAT: "TasksBot không còn ở trong group này.",
    FailureCategory.BOT_CANNOT_SEND: "TasksBot hiện không có quyền gửi tin trong group này.",
    FailureCategory.CHAT_NOT_FOUND: "TasksBot không còn tìm thấy nơi nhận này.",
    FailureCategory.DESTINATION_REFUSED: "Nơi nhận này không được phép nhận loại tin đó.",
    FailureCategory.RATE_LIMITED: "Telegram đang giới hạn số tin gửi đi.",
    FailureCategory.NETWORK: "Kết nối tới Telegram không ổn định.",
    FailureCategory.PROVIDER_UNAVAILABLE: "Telegram tạm thời không phản hồi.",
    FailureCategory.UNKNOWN: "TasksBot chưa xác định được nguyên nhân.",
    FailureCategory.NONE: "TasksBot chưa xác định được nguyên nhân.",
}

UNKNOWN_DESTINATION = "Nơi nhận đã đăng ký"
UNKNOWN_SUMMARY = "Một thông báo của bạn"


def reason_label(category: FailureCategory) -> str:
    """The Vietnamese sentence shown for one failure category."""
    return REASON_LABELS.get(category, REASON_LABELS[FailureCategory.UNKNOWN])


class DeliveryAlertService:
    """Creates the "this did not arrive" and "it arrived after all" messages.

    Args:
        session: The **same** unit of work that settled the delivery, so the
            alert and the ``failure_alert_sent`` flag commit together.
        settings: Feature switches for the two alert kinds.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def alert_permanent_failure(
        self, message: OutboundMessage, *, now: datetime | None = None
    ) -> OutboundMessage | None:
        """Queue one alert about one permanently failed message.

        Returns:
            The alert's own outbox row, or ``None`` when no alert is warranted -
            the feature is off, this message is itself an alert, one has already
            been sent, or nobody can be told.
        """
        if not self._settings.notification_failure_alert_enabled:
            return None
        if message.is_alert:
            # The recursion guard. An alert that cannot be delivered is logged
            # and dropped; it never produces an alert about the alert.
            logger.warning(
                "delivery_alert_itself_failed",
                extra={
                    "outbound_message_id": str(message.id),
                    "category": message.last_error_category.value,
                },
            )
            return None
        if message.failure_alert_sent:
            return None

        moment = now or utcnow()
        # Set the flag first and unconditionally. Whether or not the alert can
        # be routed, this failure has now been reported on - a destination
        # nobody can be told about must not be retried forever.
        message.failure_alert_sent = True
        message.failure_alert_at = moment

        destination = await self._alert_destination(message)
        if destination is None:
            logger.info(
                "delivery_alert_no_recipient",
                extra={"outbound_message_id": str(message.id)},
            )
            return None

        result = await NotificationRouter(self._session, self._settings).route(
            [
                RouteRequest(
                    event_type=NotificationEvent.DELIVERY_FAILED_ALERT,
                    template_key="delivery.failed_alert",
                    payload={
                        "business_summary": message.business_summary or UNKNOWN_SUMMARY,
                        "destination_label": message.destination_label or UNKNOWN_DESTINATION,
                        "reason_label": reason_label(message.last_error_category),
                    },
                    idempotency_key=delivery_failure_key(message.id),
                    aggregate_type="outbound_message",
                    aggregate_id=message.id,
                    private_chat_id=destination,
                    source_chat_id=message.source_chat_id,
                    created_by_user_id=message.created_by_user_id,
                    destination_label="Chat riêng",
                    business_summary="Báo tin chưa gửi được",
                    is_alert=True,
                )
            ]
        )
        queued = result.queued or result.duplicates
        return queued[0] if queued else None

    async def alert_recovery(
        self, message: OutboundMessage, *, now: datetime | None = None
    ) -> OutboundMessage | None:
        """Queue one "it went through after all" message.

        Only for a message that had already been reported as failed. Telling
        somebody a message succeeded when they were never told it failed is
        noise.
        """
        if not self._settings.notification_recovery_alert_enabled:
            return None
        if message.is_alert or not message.failure_alert_sent:
            return None

        destination = await self._alert_destination(message)
        if destination is None:
            return None

        result = await NotificationRouter(self._session, self._settings).route(
            [
                RouteRequest(
                    event_type=NotificationEvent.DELIVERY_FAILED_ALERT,
                    template_key="delivery.recovered_alert",
                    payload={
                        "business_summary": message.business_summary or UNKNOWN_SUMMARY,
                        "destination_label": message.destination_label or UNKNOWN_DESTINATION,
                    },
                    idempotency_key=delivery_recovery_key(message.id),
                    aggregate_type="outbound_message",
                    aggregate_id=message.id,
                    private_chat_id=destination,
                    source_chat_id=message.source_chat_id,
                    created_by_user_id=message.created_by_user_id,
                    destination_label="Chat riêng",
                    business_summary="Báo tin đã gửi được",
                    is_alert=True,
                )
            ]
        )
        queued = result.queued or result.duplicates
        return queued[0] if queued else None

    async def _alert_destination(self, message: OutboundMessage) -> int | None:
        """Where to send an alert about ``message``.

        The person who caused the message, if MeoBot can reach them privately;
        otherwise the configured owner, who is the one who can fix a broken
        destination. Never the group that failed - it is the thing that is
        broken.
        """
        resolver = RecipientResolver(self._session, self._settings)
        if message.created_by_user_id is not None:
            initiator = await resolver.private_destination(user_id=message.created_by_user_id)
            if initiator.is_resolved:
                return initiator.telegram_chat_id
        owner = await resolver.owner()
        return owner.telegram_chat_id if owner.is_resolved else None

    async def pending_failures(self, *, limit: int = 50) -> Sequence[OutboundMessage]:
        """Terminally failed messages nobody has been told about yet.

        A reconciliation net for failures settled by a path that did not alert -
        a manual status change, or a settle transaction that rolled back after
        marking the failure.
        """
        result = await self._session.execute(
            select(OutboundMessage)
            .where(
                OutboundMessage.status == OutboxStatus.PERMANENT_FAILURE,
                OutboundMessage.failure_alert_sent.is_(False),
                OutboundMessage.is_alert.is_(False),
            )
            .order_by(OutboundMessage.failed_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def by_id(self, message_id: uuid.UUID) -> OutboundMessage | None:
        return await self._session.get(OutboundMessage, message_id)
