"""The transactional outbox: durable intent to send, written with the business change.

The guarantee this buys: **an approved leave request and the intent to tell
somebody about it are committed together, or neither is.** Telegram then becomes
a separate, retryable problem. Before this existed, a handler sent the message
itself, so a network blip during ``sendMessage`` could leave an approval that
nobody was ever told about - or, with a retry in the wrong place, an approval
announced twice.

``idempotency_key`` is unique in the database, and that constraint is the whole
anti-duplication mechanism. A double-tapped button, a redelivered Telegram
update and a retried service call all produce the same key; the second insert
loses, and :meth:`OutboxService.enqueue` reports the existing row instead of
raising. Nothing downstream has to remember to check.

Claiming is ``FOR UPDATE SKIP LOCKED`` so two workers cannot take the same row,
and the Telegram call happens **outside** the claiming transaction - holding a
row lock across a network request would turn a slow provider into a stalled
database.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.notifications import DeliveryAttempt, OutboundMessage
from meobot.domain.notifications.models import (
    DeliveryOutcome,
    FailureCategory,
    NotificationEvent,
    OutboxStatus,
    PrivacyClassification,
    RecipientType,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    """One message somebody wants sent, before it is durable."""

    event_type: NotificationEvent
    aggregate_type: str
    aggregate_id: uuid.UUID | None
    recipient_type: RecipientType
    telegram_chat_id: int
    template_key: str
    template_version: int
    privacy_classification: PrivacyClassification
    payload: dict[str, Any]
    idempotency_key: str
    recipient_user_id: uuid.UUID | None = None
    registered_chat_id: uuid.UUID | None = None
    source_chat_id: int | None = None
    created_by_user_id: uuid.UUID | None = None
    #: What to call the destination when reporting a failure. Resolved now,
    #: because a numeric chat id is never shown to a user and the registration
    #: may be gone by the time an alert is written.
    destination_label: str | None = None
    #: A short human description of the business event, for the same reason.
    business_summary: str | None = None
    #: True when this message is itself a failure alert. A failed alert must
    #: never produce an alert about the alert.
    is_alert: bool = False
    #: When a worker may first take this. ``None`` means immediately, which is
    #: every case except one: part 2 of a long announcement is queued at
    #: confirmation time - so the intent is durable with the decision - and
    #: held until part 1 has actually landed, because "part 2 arrived first" is
    #: worse than "part 2 arrived late". See
    #: :class:`~meobot.application.dispatch_service.DispatchService`.
    available_at: datetime | None = None


class OutboxService:
    """Creates and settles :class:`OutboundMessage` rows.

    Args:
        session: Unit of work. For :meth:`enqueue` this **must** be the same
            session as the business change, or the atomicity guarantee is lost.
        settings: Retry budget and batch size.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Writing ----------------------------------------------------------
    async def enqueue(self, request: OutboundRequest) -> tuple[OutboundMessage, bool]:
        """Record one outbound message. Returns ``(row, created)``.

        ``created=False`` means this exact notification was already queued -
        the caller treats that as success, because it is: somebody is going to
        be told exactly once.
        """
        existing = await self.by_idempotency_key(request.idempotency_key)
        if existing is not None:
            return existing, False

        row = OutboundMessage(
            event_type=request.event_type.value,
            aggregate_type=request.aggregate_type,
            aggregate_id=request.aggregate_id,
            recipient_type=request.recipient_type,
            recipient_user_id=request.recipient_user_id,
            telegram_chat_id=request.telegram_chat_id,
            registered_chat_id=request.registered_chat_id,
            template_key=request.template_key,
            template_version=request.template_version,
            privacy_classification=request.privacy_classification,
            safe_payload_json=dict(request.payload),
            status=OutboxStatus.PENDING,
            available_at=request.available_at or utcnow(),
            attempt_count=0,
            max_attempts=self._settings.notification_max_attempts,
            idempotency_key=request.idempotency_key,
            last_error_category=FailureCategory.NONE,
            source_chat_id=request.source_chat_id,
            created_by_user_id=request.created_by_user_id,
            destination_label=request.destination_label,
            business_summary=request.business_summary,
            is_alert=request.is_alert,
            failure_alert_sent=False,
            version=1,
        )
        try:
            # A savepoint, not the whole transaction. This method is called
            # *inside* the transaction carrying a business change, and rolling
            # that back because a notification was already queued would undo an
            # approval to avoid sending a duplicate message - which is exactly
            # backwards.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # Lost the race against a concurrent identical enqueue. That is the
            # constraint doing its job, not an error.
            duplicate = await self.by_idempotency_key(request.idempotency_key)
            if duplicate is None:  # pragma: no cover - only a real DB fault
                raise
            return duplicate, False

        logger.info(
            "outbound_message_enqueued",
            extra={
                "outbound_message_id": str(row.id),
                "event_type": row.event_type,
                "recipient_type": row.recipient_type.value,
            },
        )
        return row, True

    async def by_idempotency_key(self, key: str) -> OutboundMessage | None:
        """Find an already-queued message by its business key."""
        result = await self._session.execute(
            select(OutboundMessage).where(OutboundMessage.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    # --- Claiming ---------------------------------------------------------
    async def claim_batch(
        self, *, limit: int | None = None, now: datetime | None = None
    ) -> Sequence[OutboundMessage]:
        """Take up to ``limit`` due messages for this worker.

        ``SKIP LOCKED`` is what makes two workers safe: the second one steps
        over rows the first has locked instead of blocking on them. SQLite -
        which the offline tests use - has no such clause and serialises writes
        anyway, so the same code is correct on both.
        """
        moment = now or utcnow()
        size = limit or self._settings.notification_worker_batch_size

        statement = (
            select(OutboundMessage)
            .where(
                OutboundMessage.status.in_([OutboxStatus.PENDING, OutboxStatus.RETRY_WAIT]),
                OutboundMessage.available_at <= moment,
            )
            .order_by(OutboundMessage.available_at.asc())
            .limit(size)
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)

        rows = list((await self._session.execute(statement)).scalars().all())
        for row in rows:
            row.status = OutboxStatus.PROCESSING
            row.processing_started_at = moment
            row.attempt_count += 1
        await self._session.flush()
        return rows

    async def recover_stale(self, *, now: datetime | None = None) -> int:
        """Return messages stuck in ``PROCESSING`` to the queue.

        A worker that dies mid-delivery leaves a row claimed forever. Rather
        than a heartbeat protocol, a claim older than the processing timeout is
        assumed dead - the timeout is far longer than any real send, so a live
        delivery is never stolen.
        """
        moment = now or utcnow()
        cutoff = moment - timedelta(seconds=self._settings.notification_processing_timeout_seconds)
        result: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            update(OutboundMessage)
            .where(
                OutboundMessage.status == OutboxStatus.PROCESSING,
                OutboundMessage.processing_started_at < cutoff,
            )
            .values(status=OutboxStatus.RETRY_WAIT, available_at=moment)
        )
        recovered = int(result.rowcount or 0)
        if recovered:
            logger.warning("outbound_messages_recovered", extra={"count": recovered})
        await self._session.flush()
        return recovered

    # --- Settling ---------------------------------------------------------
    async def mark_delivered(
        self,
        row: OutboundMessage,
        *,
        telegram_message_id: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Record a successful delivery. Terminal."""
        moment = now or utcnow()
        row.status = OutboxStatus.DELIVERED
        row.delivered_at = moment
        row.last_error_category = FailureCategory.NONE
        row.version += 1
        await self._record_attempt(
            row,
            outcome=DeliveryOutcome.DELIVERED,
            category=FailureCategory.NONE,
            telegram_message_id=telegram_message_id,
            now=moment,
        )
        await self._session.flush()

    async def mark_failed(
        self,
        row: OutboundMessage,
        *,
        category: FailureCategory,
        retry_after_seconds: int | None = None,
        now: datetime | None = None,
    ) -> OutboxStatus:
        """Record a failure and decide whether to try again.

        Three outcomes, and the distinction matters more than the retry maths:
        a network blip is worth retrying, a person who has never started the
        bot is not, and a refused destination must stop immediately.
        """
        moment = now or utcnow()
        row.last_error_category = category
        row.version += 1

        if category.is_recipient_problem or category is FailureCategory.DESTINATION_REFUSED:
            outcome = DeliveryOutcome.RECIPIENT_UNAVAILABLE
            if category is FailureCategory.DESTINATION_REFUSED:
                outcome = DeliveryOutcome.PERMANENT_FAILURE
            row.status = OutboxStatus.PERMANENT_FAILURE
            row.failed_at = moment
        elif not category.is_retryable or row.attempt_count >= row.max_attempts:
            outcome = DeliveryOutcome.PERMANENT_FAILURE
            row.status = OutboxStatus.PERMANENT_FAILURE
            row.failed_at = moment
        else:
            outcome = DeliveryOutcome.TEMPORARY_FAILURE
            row.status = OutboxStatus.RETRY_WAIT
            row.available_at = moment + timedelta(
                seconds=self.backoff_seconds(row.attempt_count, retry_after_seconds)
            )

        await self._record_attempt(
            row,
            outcome=outcome,
            category=category,
            retry_after_seconds=retry_after_seconds,
            now=moment,
        )
        await self._session.flush()
        return row.status

    def backoff_seconds(self, attempt: int, retry_after: int | None = None) -> int:
        """How long before the next try.

        Telegram's own ``retry_after`` wins when it sends one - arguing with a
        rate limiter is how a temporary limit becomes a longer one. Otherwise
        exponential with jitter, so a batch of failures does not come back in
        lockstep.
        """
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self._settings.notification_retry_max_seconds)
        base = self._settings.notification_retry_base_seconds * (2 ** max(0, attempt - 1))
        capped = min(base, self._settings.notification_retry_max_seconds)
        jitter = random.uniform(0, capped * 0.25)  # noqa: S311 - spreading retries, not crypto
        return int(min(capped + jitter, self._settings.notification_retry_max_seconds))

    async def cancel(self, row: OutboundMessage, *, now: datetime | None = None) -> None:
        """Stop trying to send something nobody wants sent any more."""
        row.status = OutboxStatus.CANCELLED
        row.failed_at = now or utcnow()
        row.version += 1
        await self._session.flush()

    async def release(self, row: OutboundMessage, *, now: datetime | None = None) -> None:
        """Make a held message due now, without touching its attempt history.

        Used for the next part of a long announcement: it was queued with the
        rest at confirmation time and held so it could not overtake the part
        before it, and this is the moment that part landed.
        """
        if row.status is not OutboxStatus.PENDING:
            return
        row.available_at = now or utcnow()
        await self._session.flush()

    async def retry_now(self, row: OutboundMessage, *, now: datetime | None = None) -> None:
        """Put a failed message back in the queue, by hand.

        The attempt counter is reset because a human has just done something -
        added the bot back to the group, restored a permission - that the
        automatic retries could not.
        """
        row.status = OutboxStatus.PENDING
        row.available_at = now or utcnow()
        row.attempt_count = 0
        row.failed_at = None
        row.last_error_category = FailureCategory.NONE
        row.version += 1
        await self._session.flush()

    async def _record_attempt(
        self,
        row: OutboundMessage,
        *,
        outcome: DeliveryOutcome,
        category: FailureCategory,
        telegram_message_id: int | None = None,
        retry_after_seconds: int | None = None,
        now: datetime,
    ) -> None:
        """Append one attempt. Never carries a provider response body."""
        self._session.add(
            DeliveryAttempt(
                outbound_message_id=row.id,
                attempt_number=row.attempt_count,
                started_at=ensure_utc(row.processing_started_at or now),
                finished_at=now,
                result=outcome,
                telegram_message_id=telegram_message_id,
                error_category=category,
                retry_after_seconds=retry_after_seconds,
                created_at=now,
            )
        )

    # --- Reading ----------------------------------------------------------
    async def by_id(self, message_id: uuid.UUID) -> OutboundMessage | None:
        return await self._session.get(OutboundMessage, message_id)

    async def attempts(self, message_id: uuid.UUID) -> Sequence[DeliveryAttempt]:
        """Full attempt history for one message, oldest first."""
        result = await self._session.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.outbound_message_id == message_id)
            .order_by(DeliveryAttempt.attempt_number.asc())
        )
        return result.scalars().all()

    async def unsettled(self, *, limit: int = 20) -> Sequence[OutboundMessage]:
        """Messages still trying, or that gave up. What "tin nào gửi lỗi" shows."""
        result = await self._session.execute(
            select(OutboundMessage)
            .where(
                OutboundMessage.status.in_(
                    [
                        OutboxStatus.PENDING,
                        OutboxStatus.PROCESSING,
                        OutboxStatus.RETRY_WAIT,
                        OutboxStatus.PERMANENT_FAILURE,
                    ]
                )
            )
            .order_by(OutboundMessage.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def for_aggregate(
        self, *, aggregate_type: str, aggregate_id: uuid.UUID
    ) -> Sequence[OutboundMessage]:
        """Every message queued about one business object."""
        result = await self._session.execute(
            select(OutboundMessage)
            .where(
                OutboundMessage.aggregate_type == aggregate_type,
                OutboundMessage.aggregate_id == aggregate_id,
            )
            .order_by(OutboundMessage.created_at.asc())
        )
        return result.scalars().all()
