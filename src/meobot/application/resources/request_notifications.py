"""Telling people about a resource request (0070).

Two notices, both on the transactional outbox like every other module's:

* **to the Phòng Tài nguyên** when a request reaches their desk (``FUNDING`` -
  "Duyệt chi"), so a new ticket is not sitting unseen;
* **to the requester** when the resources have been bought and stocked
  (``DELIVERED``), so they know to confirm receipt ("Xác nhận nhận").

Each writes the web inbox row and, for a reachable private chat, queues a
Telegram copy - the same shape ``OrderNotificationService`` uses, so a person
reads one sentence in both places. Nothing here decides who may act; it only
tells the people the workflow already routes the request to.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.org.seats import load_seats
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.resource import ResourceRequest
from meobot.domain.identity.models import Actor
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.web import web_title
from meobot.domain.org.models import DepartmentFunction

logger = get_logger(__name__)

TEMPLATE_KEY = "resources.request"


class ResourceRequestNotificationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)
        self._inbox = UserNotificationService(session)

    # --- the events -----------------------------------------------------------

    async def submitted_to_resource_team(self, *, actor: Actor, row: ResourceRequest) -> None:
        """A request landed at ``FUNDING``: everyone in a Phòng Tài nguyên is told."""
        recipients = await self._resource_team()
        await self._tell(
            actor=actor,
            row=row,
            event=NotificationEvent.RESOURCE_REQUEST_SUBMITTED,
            recipients=recipients,
            body=f"Phiếu đề xuất {row.code} đang chờ Phòng Tài nguyên duyệt chi và mua.",
            suffix="funding",
        )

    async def delivered_to_requester(self, *, actor: Actor, row: ResourceRequest) -> None:
        """The resources are in stock: the requester is asked to confirm receipt."""
        await self._tell(
            actor=actor,
            row=row,
            event=NotificationEvent.RESOURCE_REQUEST_DELIVERED,
            recipients=[row.requester_user_id],
            body=f"Tài nguyên cho phiếu {row.code} đã được mua và nhập kho. Vào xác nhận đã nhận.",
            suffix="delivered",
        )

    # --- who ------------------------------------------------------------------

    async def _resource_team(self) -> list[uuid.UUID]:
        """Everyone seated in a department whose function is RESOURCES."""
        seats = await load_seats(self._session)
        return sorted(
            {
                user_id
                for user_id, rows in seats.items()
                for seat in rows
                if seat.function is DepartmentFunction.RESOURCES
            },
            key=str,
        )

    # --- the send -------------------------------------------------------------

    async def _tell(
        self,
        *,
        actor: Actor,
        row: ResourceRequest,
        event: NotificationEvent,
        recipients: list[uuid.UUID],
        body: str,
        suffix: str,
    ) -> None:
        title = (row.purpose or "Đề xuất tài nguyên").strip()[:120] or "Đề xuất tài nguyên"
        link = f"{self._settings.web_base_url.rstrip('/')}/resources/requests/{row.id}"
        seen: set[uuid.UUID] = set()
        for recipient in recipients:
            if recipient in seen or recipient == actor.user_id:
                continue
            seen.add(recipient)
            key = f"resource_request:{row.id}:{event.value}:{suffix}:{recipient}"
            await self._inbox.record(
                recipient_user_id=recipient,
                event=event,
                title=web_title(event),
                body=body,
                idempotency_key=key,
                target_kind="resource_request",
                target_id=row.id,
            )
            resolved = await self._resolver.private_destination(user_id=recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                logger.info(
                    "resource_request_notification_recipient_unreachable",
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
                            "code": row.code,
                            "body": body,
                            "link": link,
                        },
                        idempotency_key=key,
                        aggregate_type="resource_request",
                        aggregate_id=row.id,
                        recipient_user_id=recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=actor.user_id,
                        business_summary=f"{row.code} — {title}",
                    )
                ]
            )


__all__ = ["TEMPLATE_KEY", "ResourceRequestNotificationService"]
