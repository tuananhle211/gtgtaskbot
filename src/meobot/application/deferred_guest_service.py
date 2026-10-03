"""Holding a stranger's first question, and releasing it once somebody decides.

The problem this solves is small and the constraint that shapes it is hard:
**Telegram's Bot API cannot fetch an arbitrary past message.** A bot sees a
message once, as it arrives. So "answer the question they asked before you
approved them" is only possible if MeoBot kept the question - and keeping an
unapproved stranger's words needs a boundary, not a convenient column.

The boundary is that **holding is not processing**. Between arrival and
approval, this question has not been through the LLM, has produced no
conversation memory, has created no
:class:`~meobot.domain.identity.models.Actor` and has run no tool. It is text,
an address, a hash and an expiry. The owner's decision is what turns it into
work.

Idempotency is one statement. :meth:`claim_for_processing` is a conditional
``UPDATE ... WHERE status IN (authorized)`` - so a double-tapped owner button,
a redelivered Telegram update, a Celery retry and a restarted worker all race,
and whoever loses updates zero rows and does nothing. One question, one answer.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger, redact_text
from meobot.core.time import utcnow
from meobot.db.models.access import PendingGuestAccessRequest
from meobot.db.models.deferred import DeferredGuestMessage
from meobot.domain.deferred.models import (
    AuthorizationMode,
    DeferredMessageStatus,
    status_for_mode,
)

logger = get_logger(__name__)

#: Longer than any question worth replaying, short enough that this never
#: becomes a general-purpose store of other people's messages.
MAX_STORED_LENGTH = 1000


def _hash(text: str) -> str:
    """A stable fingerprint that survives purging the text itself.

    Lets an audit answer "was this the message that was refused" without
    keeping what it said. Not a password hash and not used as one - a plain
    digest is exactly right for proving two strings were the same.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DeferredGuestMessageService:
    """Stores, authorizes, claims and purges held questions.

    Args:
        session: Unit of work. :meth:`capture` **must** share the session that
            created the access request, so a held question and the request it
            belongs to commit together.
        settings: Supplies the expiry and retention windows.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Holding ----------------------------------------------------------
    async def capture(
        self,
        *,
        request: PendingGuestAccessRequest,
        bot_identity: int,
        text: str,
        reply_to_message_id: int | None = None,
        now: datetime | None = None,
    ) -> DeferredGuestMessage | None:
        """Keep one question against one pending access request.

        Idempotent: a stranger who sends five messages while waiting does not
        get five answers when approved. The owner read one question and
        approved that one, so the first capture wins and the rest are ignored.

        Returns:
            The stored row, or ``None`` when the feature is switched off or the
            message carried nothing worth replaying.
        """
        if not self._settings.notification_guest_replay_enabled:
            return None

        cleaned = redact_text((text or "").strip())[:MAX_STORED_LENGTH]
        if not cleaned:
            return None

        existing = await self.for_request(request.id)
        if existing is not None:
            return existing

        moment = now or utcnow()
        row = DeferredGuestMessage(
            pending_access_request_id=request.id,
            bot_identity=bot_identity,
            original_chat_id=request.telegram_chat_id,
            original_message_id=request.source_message_id,
            original_telegram_user_id=request.requester_telegram_id,
            sanitized_text=cleaned,
            original_text_hash=_hash(cleaned),
            reply_to_message_id=reply_to_message_id or request.source_message_id,
            status=DeferredMessageStatus.PENDING_APPROVAL,
            expires_at=moment
            + timedelta(seconds=self._settings.deferred_guest_message_ttl_seconds),
            version=1,
        )
        try:
            # A savepoint: this shares the transaction that created the access
            # request, and losing a capture race must not discard the request.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            return await self.for_request(request.id)
        logger.info(
            "deferred_guest_message_captured",
            extra={"deferred_message_id": str(row.id), "chat_id": request.telegram_chat_id},
        )
        return row

    async def for_request(self, request_id: uuid.UUID) -> DeferredGuestMessage | None:
        """The held question for one access request, if there is one."""
        result = await self._session.execute(
            select(DeferredGuestMessage).where(
                DeferredGuestMessage.pending_access_request_id == request_id
            )
        )
        return result.scalar_one_or_none()

    async def by_id(self, message_id: uuid.UUID) -> DeferredGuestMessage | None:
        return await self._session.get(DeferredGuestMessage, message_id)

    # --- Deciding ---------------------------------------------------------
    async def authorize(
        self,
        *,
        pending_access_request_id: uuid.UUID,
        mode: AuthorizationMode,
        now: datetime | None = None,
    ) -> DeferredGuestMessage | None:
        """Mark a held question answerable. Safe to call twice.

        Returns ``None`` when there is nothing to authorize, when it has
        already been processed, or when it expired while the owner was
        deciding - a reply to a question somebody asked yesterday and has since
        forgotten is worse than no reply.
        """
        row = await self.for_request(pending_access_request_id)
        if row is None:
            return None
        moment = now or utcnow()
        if row.status is not DeferredMessageStatus.PENDING_APPROVAL:
            # Already decided. Returning it unchanged keeps a second press
            # harmless rather than raising at the owner.
            return row if row.status.is_authorized else None
        if _expired(row, moment):
            await self.expire(row, now=moment)
            return None

        row.status = status_for_mode(mode)
        row.authorization_mode = mode
        row.version += 1
        await self._session.flush()
        logger.info(
            "deferred_guest_message_authorized",
            extra={"deferred_message_id": str(row.id), "mode": mode.value},
        )
        return row

    async def reject(
        self, *, pending_access_request_id: uuid.UUID, now: datetime | None = None
    ) -> DeferredGuestMessage | None:
        """Refuse a held question and purge what it said.

        The stranger never became a user. Keeping their words would mean the
        least-trusted people in the system have the longest-lived data in it.
        """
        row = await self.for_request(pending_access_request_id)
        if row is None:
            return None
        row.status = DeferredMessageStatus.REJECTED
        row.processed_at = now or utcnow()
        row.sanitized_text = ""
        row.version += 1
        await self._session.flush()
        return row

    async def expire(
        self, row: DeferredGuestMessage, *, now: datetime | None = None
    ) -> DeferredGuestMessage:
        """Let a held question lapse, purging its text."""
        row.status = DeferredMessageStatus.EXPIRED
        row.processed_at = now or utcnow()
        row.sanitized_text = ""
        row.version += 1
        await self._session.flush()
        return row

    # --- Processing -------------------------------------------------------
    async def claim_for_processing(
        self, message_id: uuid.UUID, *, now: datetime | None = None
    ) -> DeferredGuestMessage | None:
        """Take exclusive responsibility for answering one held question.

        One conditional ``UPDATE``. Whoever loses the race updates zero rows
        and returns ``None``, which is what makes a double-tapped button, a
        redelivered update, a task retry and a worker restart all produce
        exactly one answer.
        """
        moment = now or utcnow()
        result: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            update(DeferredGuestMessage)
            .where(
                DeferredGuestMessage.id == message_id,
                DeferredGuestMessage.status.in_(
                    [
                        DeferredMessageStatus.AUTHORIZED_ONCE,
                        DeferredMessageStatus.AUTHORIZED_GUEST,
                    ]
                ),
            )
            .values(status=DeferredMessageStatus.PROCESSING, processing_started_at=moment)
        )
        if not int(result.rowcount or 0):
            return None
        await self._session.flush()
        row = await self.by_id(message_id)
        if row is not None:
            await self._session.refresh(row)
        if row is not None and _expired(row, moment):
            await self.expire(row, now=moment)
            return None
        return row

    async def mark_queued(self, row: DeferredGuestMessage, *, outbox_message_id: uuid.UUID) -> None:
        """The answer exists and is waiting for the delivery worker."""
        row.status = DeferredMessageStatus.RESPONSE_QUEUED
        row.outbox_message_id = outbox_message_id
        row.version += 1
        await self._session.flush()

    async def mark_answered(
        self, row: DeferredGuestMessage, *, now: datetime | None = None
    ) -> None:
        """Terminal. Set once Telegram has accepted the reply."""
        row.status = DeferredMessageStatus.ANSWERED
        row.processed_at = now or utcnow()
        row.version += 1
        await self._session.flush()

    async def mark_failed(
        self, row: DeferredGuestMessage, *, category: str, now: datetime | None = None
    ) -> None:
        """Terminal. Generation failed, so nothing is charged and nothing is sent."""
        row.status = DeferredMessageStatus.FAILED
        row.failure_category = category
        row.processed_at = now or utcnow()
        row.version += 1
        await self._session.flush()

    async def release_claim(self, row: DeferredGuestMessage) -> None:
        """Hand a claimed question back after a *retryable* failure.

        Used when the provider was briefly unavailable: the owner's decision
        still stands, so the authorization is restored rather than burnt.
        """
        row.status = status_for_mode(row.authorization_mode or AuthorizationMode.ANSWER_ONCE)
        row.processing_started_at = None
        row.version += 1
        await self._session.flush()

    # --- Housekeeping -----------------------------------------------------
    async def purge_expired(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Blank the text of every held question nobody decided about in time."""
        moment = now or utcnow()
        result = await self._session.execute(
            select(DeferredGuestMessage)
            .where(
                DeferredGuestMessage.status == DeferredMessageStatus.PENDING_APPROVAL,
                DeferredGuestMessage.expires_at < moment,
            )
            .limit(limit)
        )
        rows: Sequence[DeferredGuestMessage] = result.scalars().all()
        for row in rows:
            await self.expire(row, now=moment)
        if rows:
            logger.info("deferred_guest_messages_expired", extra={"count": len(rows)})
        return len(rows)


def _expired(row: DeferredGuestMessage, moment: datetime) -> bool:
    """True once a held question is too old to be worth answering."""
    from meobot.core.time import ensure_utc

    return ensure_utc(row.expires_at) < moment
