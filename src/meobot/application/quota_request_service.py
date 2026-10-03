"""A member asking the owner for more chat quota today.

One open request per member per quota date. The unique constraint on
``(user_id, quota_date)`` makes that a database fact rather than a convention,
so pressing "Xin thêm lượt" repeatedly produces one owner notification and not a
stream of them - and tomorrow, when the date key changes, they may ask again.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.quota_service import QuotaService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.quota import QuotaRequest
from meobot.domain.access.models import PendingRequestStatus, QuotaAction
from meobot.domain.identity.models import Actor, Role

logger = get_logger(__name__)

#: A request that nobody answers stops being actionable at the end of the day
#: it was made for; the allowance it was about has reset by then anyway.
QUOTA_REQUEST_TTL = timedelta(hours=24)


class QuotaRequestService:
    """Opens, reuses and resolves :class:`QuotaRequest` rows.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Supplies the timezone that defines the quota date.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._quota = QuotaService(session, settings)

    async def open_or_reuse(
        self,
        *,
        user_id: uuid.UUID,
        telegram_user_id: int,
        display_name: str,
        chat_id: int,
        source_message_id: int,
    ) -> tuple[QuotaRequest, bool]:
        """Return ``(request, created)`` for today's request from this member."""
        now = utcnow()
        quota_date = self._quota.today(now)
        existing = await self.open_for(user_id=user_id)
        if existing is not None:
            return existing, False

        verdict = await self._quota.inspect_member(user_id=user_id, role=Role.EMPLOYEE)
        request = QuotaRequest(
            user_id=user_id,
            requester_telegram_id=telegram_user_id,
            requester_display_name=display_name[:300],
            telegram_chat_id=chat_id,
            source_message_id=source_message_id,
            quota_date=quota_date,
            limit_at_request=verdict.limit,
            status=PendingRequestStatus.OPEN,
            expires_at=now + QUOTA_REQUEST_TTL,
            notified_at=now,
        )
        self._session.add(request)
        await self._session.flush()
        logger.info(
            "quota_request_opened",
            extra={"user_id": str(user_id), "limit": verdict.limit},
        )
        return request, True

    async def open_for(self, *, user_id: uuid.UUID) -> QuotaRequest | None:
        """This member's still-open request for today, if there is one."""
        quota_date = self._quota.today()
        result = await self._session.execute(
            select(QuotaRequest).where(
                QuotaRequest.user_id == user_id,
                QuotaRequest.quota_date == quota_date,
                QuotaRequest.status == PendingRequestStatus.OPEN,
            )
        )
        return result.scalar_one_or_none()

    async def by_id(self, request_id: uuid.UUID) -> QuotaRequest | None:
        """Load one request by primary key."""
        return await self._session.get(QuotaRequest, request_id)

    async def resolve(
        self, request: QuotaRequest, *, actor: Actor, action: QuotaAction
    ) -> QuotaRequest:
        """Close a request. Idempotent, so a double-tapped button grants once."""
        if request.status is not PendingRequestStatus.OPEN:
            return request
        request.status = (
            PendingRequestStatus.REJECTED
            if action is QuotaAction.DENY
            else PendingRequestStatus.APPROVED
        )
        request.resolved_at = utcnow()
        request.resolved_action = action
        request.resolved_by_user_id = actor.user_id
        await self._session.flush()
        return request

    async def list_open(self) -> Sequence[QuotaRequest]:
        """Every quota request still awaiting a decision."""
        result = await self._session.execute(
            select(QuotaRequest)
            .where(QuotaRequest.status == PendingRequestStatus.OPEN)
            .order_by(QuotaRequest.created_at.desc())
        )
        return result.scalars().all()
