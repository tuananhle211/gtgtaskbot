"""Telling the OWNER (chốt 27/10: the OWNER only) that a job is in trouble.

One message per incident - its id is in the idempotency key - through the
outbox like every other bot message, plus a line in the in-app inbox. The
Telegram message carries a "Chạy lại" button, rebuilt at delivery time
(:mod:`meobot.application.outbound_keyboards`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.cron import CronJobIncident
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.notifications.models import NotificationEvent

logger = get_logger(__name__)

#: ``user_notifications.target_kind``: the bell links to the monitor screen.
TARGET_KIND = "cron_job"
AGGREGATE_TYPE = "cron_job_incident"


class CronAlerts:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def failed(
        self, incident: CronJobIncident, *, job_label: str, problem: str, detail: str
    ) -> None:
        await self._send(
            incident,
            event=NotificationEvent.CRON_JOB_FAILED,
            template_key="cron.job_failed",
            key=f"cron_failed:{incident.id}",
            payload={"job_label": job_label, "problem": problem, "detail": detail},
            title=f"Tác vụ lỗi: {job_label}",
            body=f"{problem}. {detail}".strip(),
        )

    async def recovered(self, incident: CronJobIncident, *, job_label: str, detail: str) -> None:
        await self._send(
            incident,
            event=NotificationEvent.CRON_JOB_RECOVERED,
            template_key="cron.job_recovered",
            key=f"cron_recovered:{incident.id}",
            payload={"job_label": job_label, "detail": detail},
            title=f"Tác vụ đã chạy lại: {job_label}",
            body=detail,
        )

    async def _send(
        self,
        incident: CronJobIncident,
        *,
        event: NotificationEvent,
        template_key: str,
        key: str,
        payload: dict[str, str],
        title: str,
        body: str,
    ) -> None:
        owner = await RecipientResolver(self._session, self._settings).owner()
        if owner.is_resolved and owner.telegram_chat_id:
            await NotificationRouter(self._session, self._settings).route(
                [
                    RouteRequest(
                        event_type=event,
                        template_key=template_key,
                        payload=payload,
                        idempotency_key=key,
                        aggregate_type=AGGREGATE_TYPE,
                        aggregate_id=incident.id,
                        private_chat_id=owner.telegram_chat_id,
                        destination_label="Chat riêng",
                        business_summary=title[:300],
                        # An alert about a job must not raise an alert of its own.
                        is_alert=True,
                    )
                ]
            )
        else:
            logger.info("cron_alert_no_owner_chat", extra={"incident_id": str(incident.id)})
        inbox = UserNotificationService(self._session)
        for user_id in await self._owners():
            await inbox.record(
                recipient_user_id=user_id,
                event=event,
                title=title[:200],
                body=body[:1000],
                idempotency_key=f"{key}:{user_id}",
                target_kind=TARGET_KIND,
                target_id=incident.id,
            )

    async def _owners(self) -> list[uuid.UUID]:
        rows = await self._session.scalars(
            select(User.id).where(
                User.role == Role.OWNER, User.active.is_(True), User.deleted_at.is_(None)
            )
        )
        return list(rows)


__all__ = ["AGGREGATE_TYPE", "TARGET_KIND", "CronAlerts"]
