"""Pending approval requests for people MeoBot does not know.

When a stranger tags MeoBot in a group, nothing happens except this: a row is
written and the owner is told once. No provider is called, no conversation
thread is opened, no message is stored as memory, no ``users`` row appears. The
stranger's words exist only as a redacted preview on the request, which is what
lets the owner judge the ask without the message becoming part of anything.

Two counters keep the owner's phone quiet:

* **one open request per person per group.** Tagging MeoBot ten more times
  updates ``mention_count`` on the row that already exists;
* **a cooldown after refusal.** Once the owner says no, that person cannot
  generate another notification in that group for 24 hours, even though a new
  request row may be opened after the first expires.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.access import PendingGuestAccessRequest
from meobot.domain.access.models import (
    PENDING_REQUEST_TTL,
    REJECTION_NOTIFY_COOLDOWN,
    AccessAction,
    PendingRequestStatus,
)
from meobot.domain.conversations.redaction import redact_secrets
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

#: How much of the stranger's message the owner is shown.
PREVIEW_LENGTH = 200


def build_preview(text: str) -> str:
    """A safe, short version of what the stranger asked.

    Secrets are stripped first: somebody's first message to a bot is exactly
    where a pasted token or password turns up, and this preview is forwarded to
    a different chat.
    """
    cleaned = " ".join(redact_secrets(text or "").split())
    if len(cleaned) <= PREVIEW_LENGTH:
        return cleaned
    return cleaned[: PREVIEW_LENGTH - 1].rstrip() + "…"


class AccessRequestService:
    """Opens, reuses and resolves :class:`PendingGuestAccessRequest` rows.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_or_reuse(
        self,
        *,
        bot_id: int,
        chat_id: int,
        chat_title: str | None,
        requester_telegram_id: int,
        requester_username: str | None,
        requester_display_name: str | None,
        source_message_id: int,
        text: str,
        now: datetime | None = None,
    ) -> tuple[PendingGuestAccessRequest, bool]:
        """Return ``(request, should_notify_owner)``.

        ``should_notify_owner`` is ``False`` for a repeat mention against an
        open request and for anyone inside a rejection cooldown - that flag is
        the whole anti-spam mechanism, and the caller must respect it.
        """
        moment = now or utcnow()
        existing = await self.open_request_for(
            bot_id=bot_id,
            chat_id=chat_id,
            requester_telegram_id=requester_telegram_id,
            now=moment,
        )
        if existing is not None:
            existing.mention_count += 1
            # Deliberately not updated: ``source_message_id`` and the preview
            # stay pinned to the message the owner was actually shown, so
            # ANSWER_ONCE answers the question they read.
            await self._session.flush()
            return existing, False

        cooldown_until = await self._cooldown_until(
            bot_id=bot_id, chat_id=chat_id, requester_telegram_id=requester_telegram_id
        )
        in_cooldown = cooldown_until is not None and moment < cooldown_until

        request = PendingGuestAccessRequest(
            bot_id=bot_id,
            telegram_chat_id=chat_id,
            chat_title=(chat_title or None) and chat_title[:300],
            requester_telegram_id=requester_telegram_id,
            requester_username=(requester_username or None) and requester_username[:100],
            requester_display_name=(requester_display_name or None)
            and requester_display_name[:300],
            source_message_id=source_message_id,
            question_preview=build_preview(text),
            status=PendingRequestStatus.OPEN,
            expires_at=moment + PENDING_REQUEST_TTL,
            notify_cooldown_until=cooldown_until,
            mention_count=1,
        )
        self._session.add(request)
        await self._session.flush()
        logger.info(
            "access_request_opened",
            extra={
                "request_id": str(request.id),
                "chat_id": chat_id,
                "requester_telegram_id": requester_telegram_id,
                "notify": not in_cooldown,
            },
        )
        return request, not in_cooldown

    async def open_request_for(
        self,
        *,
        bot_id: int,
        chat_id: int,
        requester_telegram_id: int,
        now: datetime | None = None,
    ) -> PendingGuestAccessRequest | None:
        """The still-open request for this person here, expiring it if stale."""
        moment = now or utcnow()
        result = await self._session.execute(
            select(PendingGuestAccessRequest).where(
                PendingGuestAccessRequest.bot_id == bot_id,
                PendingGuestAccessRequest.telegram_chat_id == chat_id,
                PendingGuestAccessRequest.requester_telegram_id == requester_telegram_id,
                PendingGuestAccessRequest.status == PendingRequestStatus.OPEN,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if moment >= ensure_utc(row.expires_at):
            # Expiry is arithmetic here too: the row is closed the moment
            # somebody looks at it, so no sweeper task can be late.
            row.status = PendingRequestStatus.EXPIRED
            await self._session.flush()
            return None
        return row

    async def _cooldown_until(
        self, *, bot_id: int, chat_id: int, requester_telegram_id: int
    ) -> datetime | None:
        """The refusal cooldown carried over from this person's last request."""
        result = await self._session.execute(
            select(PendingGuestAccessRequest)
            .where(
                PendingGuestAccessRequest.bot_id == bot_id,
                PendingGuestAccessRequest.telegram_chat_id == chat_id,
                PendingGuestAccessRequest.requester_telegram_id == requester_telegram_id,
            )
            .order_by(PendingGuestAccessRequest.created_at.desc())
            .limit(1)
        )
        previous = result.scalar_one_or_none()
        if previous is None or previous.notify_cooldown_until is None:
            return None
        return ensure_utc(previous.notify_cooldown_until)

    async def mark_notified(
        self,
        request: PendingGuestAccessRequest,
        *,
        owner_telegram_id: int | None,
        now: datetime | None = None,
    ) -> None:
        """Record that the owner has been told, so repeats stay silent."""
        request.notified_at = now or utcnow()
        request.notified_owner_telegram_id = owner_telegram_id
        await self._session.flush()

    async def resolve(
        self,
        request: PendingGuestAccessRequest,
        *,
        actor: Actor,
        action: AccessAction,
        now: datetime | None = None,
    ) -> PendingGuestAccessRequest:
        """Close a request with the owner's decision.

        Idempotent by design: resolving an already-resolved request returns it
        unchanged, so a double-tapped button cannot grant twice.
        """
        if request.status is not PendingRequestStatus.OPEN:
            return request
        moment = now or utcnow()
        rejected = action in {AccessAction.IGNORE_ONCE, AccessAction.IGNORE_IN_GROUP}
        request.status = (
            PendingRequestStatus.REJECTED if rejected else PendingRequestStatus.APPROVED
        )
        request.resolved_at = moment
        request.resolved_action = action
        request.resolved_by_user_id = actor.user_id
        request.resolved_by_telegram_id = actor.telegram_user_id
        if rejected:
            request.notify_cooldown_until = moment + REJECTION_NOTIFY_COOLDOWN
        await self._session.flush()
        logger.info(
            "access_request_resolved",
            extra={"request_id": str(request.id), "action": action.value},
        )
        return request

    async def by_id(self, request_id: uuid.UUID) -> PendingGuestAccessRequest | None:
        """Load one request by primary key."""
        return await self._session.get(PendingGuestAccessRequest, request_id)

    async def list_open(
        self, *, now: datetime | None = None
    ) -> Sequence[PendingGuestAccessRequest]:
        """Every request still awaiting a decision, newest first."""
        moment = now or utcnow()
        result = await self._session.execute(
            select(PendingGuestAccessRequest)
            .where(
                PendingGuestAccessRequest.status == PendingRequestStatus.OPEN,
                PendingGuestAccessRequest.expires_at > moment,
            )
            .order_by(PendingGuestAccessRequest.created_at.desc())
        )
        return result.scalars().all()
