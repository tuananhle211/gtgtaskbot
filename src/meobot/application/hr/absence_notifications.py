"""Telling people about a leave / late / early request (0100).

* a step opens -> its approvers: "Có đơn từ chờ bạn duyệt";
* the chain approves it / a step refuses it -> the requester.

The web inbox row first, then a Telegram copy for a reachable private chat -
the shape of :mod:`meobot.application.performance.review_notifications`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.web import web_title

logger = get_logger(__name__)

TEMPLATE_KEY = "hr.absence"
TARGET_KIND = "hr_absence"


class AbsenceNotifier:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)
        self._inbox = UserNotificationService(session)

    async def tell(
        self,
        *,
        recipients: Iterable[uuid.UUID],
        event: NotificationEvent,
        title: str,
        body: str,
        request_id: uuid.UUID,
        key: str,
        sender: uuid.UUID | None,
    ) -> None:
        path = f"/hr/leave/{request_id}"
        link = f"{self._settings.web_base_url.rstrip('/')}{path}"
        for recipient in dict.fromkeys(recipients):
            if recipient == sender:
                continue
            idempotency = f"hr_absence:{request_id}:{key}:{event.value}:{recipient}"
            await self._inbox.record(
                recipient_user_id=recipient,
                event=event,
                title=web_title(event),
                body=body,
                idempotency_key=idempotency,
                target_kind=TARGET_KIND,
                target_id=request_id,
            )
            resolved = await self._resolver.private_destination(user_id=recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                logger.info(
                    "absence_notification_recipient_unreachable",
                    extra={"user_id": str(recipient), "reason": resolved.message},
                )
                continue
            await self._router.route(
                [
                    RouteRequest(
                        event_type=event,
                        template_key=TEMPLATE_KEY,
                        payload={
                            "heading": web_title(event).upper(),
                            "title": title,
                            "code": "Đơn từ",
                            "body": body,
                            "link": link,
                        },
                        idempotency_key=idempotency,
                        aggregate_type=TARGET_KIND,
                        aggregate_id=request_id,
                        recipient_user_id=recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=sender,
                        business_summary=f"Đơn từ — {title}",
                    )
                ]
            )


__all__ = ["TARGET_KIND", "TEMPLATE_KEY", "AbsenceNotifier"]
