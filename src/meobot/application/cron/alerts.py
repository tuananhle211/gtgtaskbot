"""Telling people that a job failed (0077; recipients per job since 0080).

Only failures are sent (chốt 28/10), and only for a job whose alerts are on
(``CronJob.notify``). The OWNER is always told - the only one who may press
"Chạy lại" - plus the job's extra recipients (``CronJob.notify_user_ids``;
empty = the OWNER alone). One message per incident and person, through the
outbox like every other bot message, plus a line in each one's in-app inbox.
The OWNER's Telegram message carries the "Chạy lại" button, rebuilt at
delivery time (:mod:`meobot.application.outbound_keyboards`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.cron import CronJobIncident
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.org.models import Position

logger = get_logger(__name__)

#: ``user_notifications.target_kind``: the bell links to the monitor screen.
TARGET_KIND = "cron_job"
AGGREGATE_TYPE = "cron_job_incident"


async def department_heads(session: AsyncSession, code: str) -> list[uuid.UUID]:
    """The active heads of the department with this code (a job's default
    extra recipients, e.g. the Trưởng phòng ADS for the Meta pulls)."""
    rows = await session.scalars(
        select(User.id)
        .join(DepartmentMember, DepartmentMember.user_id == User.id)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(
            Department.code == code,
            Department.active.is_(True),
            DepartmentMember.position == Position.HEAD,
            DepartmentMember.left_at.is_(None),
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
        .distinct()
    )
    return sorted(rows, key=str)


def _ids(values: Iterable[str]) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    for value in values:
        try:
            found.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return found


class CronAlerts:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def failed(
        self,
        incident: CronJobIncident,
        *,
        recipients: Iterable[str] = (),
        job_label: str,
        problem: str,
        detail: str,
    ) -> None:
        key = f"cron_failed:{incident.id}"
        payload = {"job_label": job_label, "problem": problem, "detail": detail}
        title = f"Tác vụ lỗi: {job_label}"
        body = f"{problem}. {detail}".strip()
        resolver = RecipientResolver(self._session, self._settings)
        router = NotificationRouter(self._session, self._settings)

        owners = await self._owners()
        extra = [
            user_id for user_id in await self._active(_ids(recipients)) if user_id not in owners
        ]
        requests: list[RouteRequest] = []
        owner = await resolver.owner()
        owner_chat = owner.telegram_chat_id if owner.is_resolved else None
        if owner_chat:
            requests.append(self._request(incident, key, payload, title, owner_chat, rerun=True))
        else:
            logger.info("cron_alert_no_owner_chat", extra={"incident_id": str(incident.id)})
        sent = {owner_chat} if owner_chat else set()
        for user_id in extra:
            resolved = await resolver.private_destination(user_id=user_id)
            chat = resolved.telegram_chat_id if resolved.is_resolved else None
            if not chat or chat in sent:
                continue
            sent.add(chat)
            requests.append(
                self._request(incident, f"{key}:{chat}", payload, title, chat, rerun=False)
            )
        if requests:
            await router.route(requests)

        inbox = UserNotificationService(self._session)
        for user_id in [*owners, *extra]:
            await inbox.record(
                recipient_user_id=user_id,
                event=NotificationEvent.CRON_JOB_FAILED,
                title=title[:200],
                body=body[:1000],
                idempotency_key=f"{key}:{user_id}",
                target_kind=TARGET_KIND,
                target_id=incident.id,
            )

    @staticmethod
    def _request(
        incident: CronJobIncident,
        key: str,
        payload: dict[str, str],
        title: str,
        chat: int,
        *,
        rerun: bool,
    ) -> RouteRequest:
        return RouteRequest(
            event_type=NotificationEvent.CRON_JOB_FAILED,
            template_key="cron.job_failed",
            # Only the OWNER may re-run: the others are told who can.
            payload={**payload, "can_rerun": "1" if rerun else "0"},
            idempotency_key=key,
            aggregate_type=AGGREGATE_TYPE,
            aggregate_id=incident.id,
            private_chat_id=chat,
            destination_label="Chat riêng",
            business_summary=title[:300],
            # An alert about a job must not raise an alert of its own.
            is_alert=True,
        )

    async def _owners(self) -> list[uuid.UUID]:
        rows = await self._session.scalars(
            select(User.id).where(
                User.role == Role.OWNER, User.active.is_(True), User.deleted_at.is_(None)
            )
        )
        return list(rows)

    async def _active(self, user_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        if not user_ids:
            return []
        rows = set(
            await self._session.scalars(
                select(User.id).where(
                    User.id.in_(user_ids),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        )
        return [user_id for user_id in user_ids if user_id in rows]


__all__ = ["AGGREGATE_TYPE", "TARGET_KIND", "CronAlerts", "department_heads"]
