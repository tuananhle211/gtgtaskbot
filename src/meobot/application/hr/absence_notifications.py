"""Telling people about a leave / late / early / explain request (0100).

Every change of a request tells everybody it concerns (user 12/11/2026,
:meth:`meobot.application.hr.absences.HrAbsenceService._announce` decides who
and what): the requester, the approvers of the step it waits on, the other
approvers of a step somebody just decided, whoever decided an earlier step,
and HCNS (the ``TIMESHEET`` holders) when it is sent and when it ends.

Each person gets one message per change with their own heading: the web inbox
row first, then a Telegram copy for a reachable private chat that also lists
every step of the chain - the shape of
:mod:`meobot.application.performance.review_notifications`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.domain.notifications.models import NotificationEvent

logger = get_logger(__name__)

TEMPLATE_KEY = "hr.absence"
TARGET_KIND = "hr_absence"


@dataclass(frozen=True, slots=True)
class Notice:
    """One message to one person: the bold heading and the sentence under it."""

    recipient: uuid.UUID
    event: NotificationEvent
    heading: str
    body: str


class AbsenceNotifier:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)
        self._inbox = UserNotificationService(session)

    async def send(
        self,
        notices: Iterable[Notice],
        *,
        title: str,
        chain: str,
        request_id: uuid.UUID,
        key: str,
        sender: uuid.UUID | None,
    ) -> None:
        """Send each notice once (the first one for a person wins).

        ``key`` names the change (``submit``, ``step2``, ``final``, ``cancel``):
        together with the event and the person it makes the message idempotent.
        """
        path = f"/hr/leave/{request_id}"
        link = f"{self._settings.web_base_url.rstrip('/')}{path}"
        seen: set[uuid.UUID] = set()
        for notice in notices:
            if notice.recipient in seen:
                continue
            seen.add(notice.recipient)
            idempotency = f"hr_absence:{request_id}:{key}:{notice.event.value}:{notice.recipient}"
            await self._inbox.record(
                recipient_user_id=notice.recipient,
                event=notice.event,
                title=notice.heading,
                body=notice.body,
                idempotency_key=idempotency,
                target_kind=TARGET_KIND,
                target_id=request_id,
            )
            resolved = await self._resolver.private_destination(user_id=notice.recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                logger.info(
                    "absence_notification_recipient_unreachable",
                    extra={"user_id": str(notice.recipient), "reason": resolved.message},
                )
                continue
            await self._router.route(
                [
                    RouteRequest(
                        event_type=notice.event,
                        template_key=TEMPLATE_KEY,
                        payload={
                            "heading": notice.heading.upper(),
                            "title": title,
                            "code": "Đơn từ",
                            "body": f"{notice.body}\n\n{chain}" if chain else notice.body,
                            "link": link,
                        },
                        idempotency_key=idempotency,
                        aggregate_type=TARGET_KIND,
                        aggregate_id=request_id,
                        recipient_user_id=notice.recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=sender,
                        business_summary=f"Đơn từ — {title}",
                    )
                ]
            )


__all__ = ["TARGET_KIND", "TEMPLATE_KEY", "AbsenceNotifier", "Notice"]
