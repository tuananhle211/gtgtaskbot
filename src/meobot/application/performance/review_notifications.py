"""Telling the Trưởng phòng PR there is something to approve (2026-10-31).

* a PR piece's task codes sent for approval (``pr_point_claims``) ->
  "Có mã task PR chờ bạn duyệt";
* phone numbers or "việc khác" handed in on "Báo cáo công việc/Seeding" ->
  "Có báo cáo công việc chờ bạn duyệt".

To every active head of a PR department, else the OWNER(s) so nothing waits
unseen; never to the person who handed it in. The web inbox row is written
first, then a Telegram copy for a reachable private chat - the same shape as
:mod:`meobot.application.resources.request_notifications`.
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
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.web import web_title
from meobot.domain.org.models import DepartmentFunction, Position

logger = get_logger(__name__)

TEMPLATE_KEY = "performance.review"
SEEDING_TITLE = "Báo cáo công việc/Seeding"


class ReviewNotifier:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)
        self._inbox = UserNotificationService(session)

    async def seeding_waiting(self, *, actor: Actor, what: str, kind: str, key: str) -> None:
        """``what`` = "5 số điện thoại" / "1 việc khác (300 điểm)"."""
        await self._tell(
            actor=actor,
            event=NotificationEvent.SEEDING_REVIEW_REQUESTED,
            title=SEEDING_TITLE,
            code=kind,
            body=f"{await self._name(actor.user_id)} vừa nộp {what}, đang chờ bạn duyệt.",
            path="/seeding?tab=review",
            key=f"seeding:{key}",
            target_kind="seeding",
            target_id=None,
        )

    async def pr_claim_waiting(
        self,
        *,
        actor: Actor,
        claim_id: uuid.UUID,
        content_code: str,
        content_title: str,
        stage_label: str,
        points: int,
        sent: str,
    ) -> None:
        """``sent`` tells one sending of the claim from the next (a claim is
        sent again after a refusal)."""
        await self._tell(
            actor=actor,
            event=NotificationEvent.PR_POINTS_REVIEW_REQUESTED,
            title=content_title,
            code=content_code,
            body=(
                f"{await self._name(actor.user_id)} gửi mã task {stage_label} "
                f"({points} điểm), đang chờ bạn duyệt."
            ),
            path="/performance/pr-claims",
            key=f"pr_claim:{claim_id}:{sent}",
            target_kind="pr_claim",
            target_id=claim_id,
        )

    # --- who ------------------------------------------------------------------

    async def reviewers(self) -> list[uuid.UUID]:
        """Every active head of a PR department, else the OWNER(s)."""
        heads = (
            await self._session.scalars(
                select(DepartmentMember.user_id)
                .join(Department, Department.id == DepartmentMember.department_id)
                .join(User, User.id == DepartmentMember.user_id)
                .where(
                    Department.function == DepartmentFunction.PR,
                    Department.active.is_(True),
                    DepartmentMember.position == Position.HEAD,
                    DepartmentMember.left_at.is_(None),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
                .distinct()
            )
        ).all()
        if heads:
            return sorted(heads, key=str)
        owners = (
            await self._session.scalars(
                select(User.id).where(
                    User.role == Role.OWNER,
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        return sorted(owners, key=str)

    async def _name(self, user_id: uuid.UUID | None) -> str:
        user = await self._session.get(User, user_id) if user_id is not None else None
        return user.full_name if user is not None and user.full_name else "Một thành viên"

    # --- the send -------------------------------------------------------------

    async def _tell(
        self,
        *,
        actor: Actor,
        event: NotificationEvent,
        title: str,
        code: str,
        body: str,
        path: str,
        key: str,
        target_kind: str,
        target_id: uuid.UUID | None,
    ) -> None:
        link = f"{self._settings.web_base_url.rstrip('/')}{path}"
        for recipient in await self.reviewers():
            if recipient == actor.user_id:
                continue
            idempotency = f"{key}:{event.value}:{recipient}"
            await self._inbox.record(
                recipient_user_id=recipient,
                event=event,
                title=web_title(event),
                body=body,
                idempotency_key=idempotency,
                target_kind=target_kind,
                target_id=target_id,
            )
            resolved = await self._resolver.private_destination(user_id=recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                logger.info(
                    "review_notification_recipient_unreachable",
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
                            "code": code,
                            "body": body,
                            "link": link,
                        },
                        idempotency_key=idempotency,
                        aggregate_type=target_kind,
                        aggregate_id=target_id,
                        recipient_user_id=recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=actor.user_id,
                        business_summary=f"{code} — {title}",
                    )
                ]
            )


__all__ = ["SEEDING_TITLE", "TEMPLATE_KEY", "ReviewNotifier"]
