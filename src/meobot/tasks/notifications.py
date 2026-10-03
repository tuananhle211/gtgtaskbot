"""Draining the transactional outbox.

The shape that matters is **claim, release, send, settle** - three short
transactions with the network call in between, never inside:

1. a transaction claims a bounded batch with ``FOR UPDATE SKIP LOCKED`` and
   marks it ``PROCESSING``, then commits;
2. Telegram is called with **no** database transaction open;
3. a transaction records the outcome.

Holding a row lock across a Telegram request would turn a slow provider into a
stalled database - every other worker blocked behind one HTTPS call. Committing
the claim first costs a little (a crash between steps 1 and 3 strands a row) and
that is exactly what :func:`recover_stale` exists to settle.

Delivery guarantee
------------------

**Telegram delivery is AT LEAST ONCE**, with best-effort duplicate suppression.
An earlier version of this docstring claimed "at most once", and that was
wrong. Telegram's ``sendMessage`` accepts no application-level idempotency key,
so MeoBot cannot ask it to deduplicate, and there is a real window in which a
message is sent twice:

1. the worker claims a row and commits ``PROCESSING``;
2. Telegram accepts the message;
3. the worker dies before step 3 commits ``DELIVERED``;
4. :func:`recover_stale` returns the row to the queue and it is sent again.

The window is small and requires a crash inside it, but it exists, and a system
that claims otherwise will eventually be believed about something it cannot do.
What *is* guaranteed:

* **business mutations** happen effectively once - authoritative state
  transitions, unique business keys, entity version checks and callback binding
  see to that, and none of them depend on Telegram;
* **outbox intent** is exactly one durable row per business event and
  destination, enforced by ``uq_outbound_messages_idempotency_key``.

So a crash in that window can duplicate a *Telegram message*. It cannot
duplicate an approval, a reminder, an announcement or an outbox row.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from meobot.application.chat_registry_service import ChatRegistryService
from meobot.application.delivery_alert_service import DeliveryAlertService
from meobot.application.delivery_service import TelegramDeliveryService
from meobot.application.outbound_keyboards import markup_for
from meobot.application.outbox_service import OutboxService
from meobot.core.logging import get_logger
from meobot.db.models.notifications import OutboundMessage
from meobot.domain.notifications.models import FailureCategory, OutboxStatus, RecipientType
from meobot.tasks.celery_app import RETRY_KWARGS
from meobot.tasks.runtime import TaskContext, run_async

logger = get_logger(__name__)


@shared_task(
    name="notifications.drain_outbox",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def drain_outbox(limit: int | None = None) -> dict[str, Any]:
    """Claim due messages, deliver them, record what happened."""
    return run_async(lambda context: _drain(context, limit))


async def _drain(context: TaskContext, limit: int | None) -> dict[str, Any]:
    settings = context.settings
    if not settings.notification_outbox_enabled:
        # The switch stops *sending*, not queueing: business actions still
        # commit their outbound intent, and it goes out when this is back on.
        return {"claimed": 0, "delivered": 0, "skipped": "disabled"}

    # --- 1. Claim, and commit the claim before touching the network -------
    async with context.database.transaction() as session:
        claimed = await OutboxService(session, settings).claim_batch(limit=limit)
        pending = [
            _Snapshot(
                message_id=row.id,
                registered_chat_id=row.registered_chat_id,
                recipient_user_id=row.recipient_user_id,
                recipient_type=row.recipient_type,
            )
            for row in claimed
        ]
        messages = {row.id: row for row in claimed}
        # Buttons are rebuilt here, from the business record, so every attempt
        # carries a fresh signature and expiry. Storing markup on the row would
        # mean a retry an hour later delivered a button that no longer verified.
        markups = {row.id: await markup_for(session, settings, row) for row in claimed}

    if not pending:
        return {"claimed": 0, "delivered": 0}

    delivery = TelegramDeliveryService(context.notifier)
    delivered = failed = 0

    for snapshot in pending:
        # --- 2. Send with no transaction open -----------------------------
        result = await delivery.deliver(
            messages[snapshot.message_id], reply_markup=markups.get(snapshot.message_id)
        )

        # --- 3. Settle in its own short transaction -----------------------
        async with context.database.transaction() as session:
            outbox = OutboxService(session, settings)
            row = await outbox.by_id(snapshot.message_id)
            if row is None:  # pragma: no cover - deleted mid-flight
                continue
            if result.delivered:
                await outbox.mark_delivered(row, telegram_message_id=result.telegram_message_id)
                await _record_reachable(context, session, snapshot)
                await _on_delivered(context, session, row)
                delivered += 1
            else:
                settled = await outbox.mark_failed(
                    row,
                    category=result.category,
                    retry_after_seconds=result.retry_after_seconds,
                )
                await _record_unreachable(context, session, snapshot, result.category)
                if settled is OutboxStatus.PERMANENT_FAILURE:
                    # In the same transaction as the failure it reports, so
                    # "told once" does not depend on the alert being delivered.
                    await DeliveryAlertService(session, settings).alert_permanent_failure(row)
                    await _on_failed(context, session, row)
                failed += 1

    logger.info(
        "outbox_drained",
        extra={"claimed": len(pending), "delivered": delivered, "failed": failed},
    )
    return {"claimed": len(pending), "delivered": delivered, "failed": failed}


class _Snapshot:
    """The few fields needed after the claiming transaction has closed."""

    __slots__ = ("message_id", "recipient_type", "recipient_user_id", "registered_chat_id")

    def __init__(
        self,
        *,
        message_id: uuid.UUID,
        registered_chat_id: uuid.UUID | None,
        recipient_user_id: uuid.UUID | None,
        recipient_type: RecipientType,
    ) -> None:
        self.message_id = message_id
        self.registered_chat_id = registered_chat_id
        self.recipient_user_id = recipient_user_id
        self.recipient_type = recipient_type


async def _on_delivered(context: TaskContext, session: Any, row: OutboundMessage) -> None:
    """Settle the business record a delivered message belongs to.

    Done here, in the settle transaction, rather than by a polling task,
    because "delivered" is the exact moment two of these become true: a Guest's
    answer has arrived, so their allowance may finally be charged; and a
    reminder occurrence actually happened.

    Charging the Guest here and not earlier is the point. Generation failing,
    the provider being down, or Telegram refusing the group all leave the
    allowance untouched - a Guest is only charged for an answer they received.
    """
    from meobot.application.deferred_guest_service import DeferredGuestMessageService
    from meobot.core.time import utcnow
    from meobot.domain.deferred.models import AuthorizationMode
    from meobot.domain.notifications.models import NotificationEvent
    from meobot.domain.reminders.models import OccurrenceStatus

    settings = context.settings

    if row.event_type == NotificationEvent.GUEST_REPLY.value and row.aggregate_id is not None:
        service = DeferredGuestMessageService(session, settings)
        deferred = await service.by_id(row.aggregate_id)
        if deferred is not None:
            await service.mark_answered(deferred)
            if deferred.authorization_mode is AuthorizationMode.GUEST_WINDOW:
                await _charge_guest_question(
                    session,
                    settings,
                    bot_id=deferred.bot_identity,
                    chat_id=deferred.original_chat_id,
                    telegram_user_id=deferred.original_telegram_user_id,
                )
        return

    if row.event_type == NotificationEvent.REMINDER_DUE.value:
        occurrence = await _occurrence_for(session, row.id)
        if occurrence is not None:
            occurrence.status = OccurrenceStatus.DELIVERED
            occurrence.delivered_at = row.delivered_at or utcnow()
        return

    if row.event_type == NotificationEvent.DISPATCH_PART_PUBLISHED.value:
        await _settle_dispatch_part(session, settings, row, delivered=True)
        return

    if row.event_type == NotificationEvent.ANNOUNCEMENT_PUBLISHED.value:
        await _confirm_announcement_delivered(session, settings, row)


async def _settle_dispatch_part(
    session: Any, settings: Any, row: OutboundMessage, *, delivered: bool
) -> None:
    """Record one group's outcome for one part of a multi-group announcement.

    Done inside the transaction that settles the outbox row, so a destination's
    business outcome and its delivery record cannot disagree - and so the "kết
    quả gửi" summary is queued by whichever worker happens to settle the last
    destination, exactly once, rather than by a sweep that polls.

    Best effort by design: the announcement *did* arrive, and failing to update
    a counter is a worse outcome to escalate than to log.
    """
    from meobot.application.audit_service import AuditService
    from meobot.application.dispatch_service import DispatchService

    service = DispatchService(session, settings, AuditService(session))
    try:
        settled = await service.settle_delivery(
            outbound_message_id=row.id,
            delivered=delivered,
            category=row.last_error_category,
        )
        if settled is not None:
            await service.queue_summary(settled)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "dispatch_settlement_failed",
            extra={"message_id": str(row.id), "error": repr(exc)},
        )


async def _confirm_announcement_delivered(session: Any, settings: Any, row: Any) -> None:
    """Tell the sender "đã gửi" - now that it is true.

    The source chat was told "đã được xếp hàng gửi" when the button was pressed,
    which was all that could honestly be said: an outbox row is an intention.
    This is the other half, and it is queued through the outbox like everything
    else rather than sent from here, so a worker that dies mid-settle does not
    lose it.

    Best effort by design. The announcement *did* arrive; failing to confirm
    that is a worse outcome to escalate than to swallow, so a routing problem
    here is logged and the delivery still counts.
    """
    from meobot.application.notification_router import (
        NotificationRouter,
        RouteRequest,
        announcement_delivered_key,
    )
    from meobot.application.recipient_resolver import RecipientResolver
    from meobot.domain.notifications.models import NotificationEvent

    if row.created_by_user_id is None:
        # A bootstrap owner with no durable row, or a system-authored send.
        return
    destination = row.destination_label or "nơi nhận"
    try:
        private = await RecipientResolver(session, settings).private_destination(
            user_id=row.created_by_user_id
        )
        if not private.is_resolved:
            return
        await NotificationRouter(session, settings).route(
            [
                RouteRequest(
                    event_type=NotificationEvent.ANNOUNCEMENT_DELIVERED,
                    template_key="announcement.delivered",
                    payload={
                        "business_summary": (row.safe_payload_json or {}).get("content", ""),
                        "destination_label": destination,
                    },
                    idempotency_key=announcement_delivered_key(row.id),
                    aggregate_type="announcement",
                    aggregate_id=row.aggregate_id,
                    recipient_user_id=row.created_by_user_id,
                    private_chat_id=private.telegram_chat_id,
                    destination_label=destination,
                    # An acknowledgement of a delivery cannot itself raise a
                    # delivery alert, or one broken private chat loops.
                    is_alert=True,
                )
            ]
        )
    except Exception as exc:
        logger.warning(
            "announcement_delivery_confirmation_failed",
            extra={"message_id": str(row.id), "error": repr(exc)},
        )


async def _charge_guest_question(
    session: Any, settings: Any, *, bot_id: int, chat_id: int, telegram_user_id: int
) -> None:
    """Spend one of a Guest's ten answers, now that one has been delivered."""
    from meobot.application.group_policy_service import GroupPolicyService
    from meobot.application.quota_service import QuotaService

    policy = await GroupPolicyService(session).get(
        bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id
    )
    if policy is None:
        return
    quota = QuotaService(session, settings)
    reservation = await quota.reserve_guest(policy_id=policy.id)
    if reservation is None:
        # The window closed or the allowance ran out while the reply was in
        # flight. The answer has already been delivered; refusing to charge is
        # the conservative direction, and the window's own limits still hold.
        return
    await quota.commit(reservation)


async def _on_failed(context: TaskContext, session: Any, row: OutboundMessage) -> None:
    """Record that a business object's message will never arrive."""
    from meobot.application.deferred_guest_service import DeferredGuestMessageService
    from meobot.domain.notifications.models import NotificationEvent
    from meobot.domain.reminders.models import OccurrenceStatus

    if row.event_type == NotificationEvent.GUEST_REPLY.value and row.aggregate_id is not None:
        service = DeferredGuestMessageService(session, context.settings)
        deferred = await service.by_id(row.aggregate_id)
        if deferred is not None:
            # Nothing is charged: the Guest never received an answer.
            await service.mark_failed(deferred, category=row.last_error_category.value)
        return

    if row.event_type == NotificationEvent.DISPATCH_PART_PUBLISHED.value:
        # One group could not be reached. The other destinations of the same
        # announcement are untouched: that is the whole reason each one has its
        # own row.
        await _settle_dispatch_part(session, context.settings, row, delivered=False)
        return

    if row.event_type == NotificationEvent.REMINDER_DUE.value:
        occurrence = await _occurrence_for(session, row.id)
        if occurrence is not None:
            occurrence.status = OccurrenceStatus.FAILED


async def _occurrence_for(session: Any, outbox_message_id: uuid.UUID) -> Any:
    """The reminder occurrence one outbound message belongs to, if any."""
    from sqlalchemy import select

    from meobot.db.models.reminder import ReminderOccurrence

    result = await session.execute(
        select(ReminderOccurrence).where(ReminderOccurrence.outbox_message_id == outbox_message_id)
    )
    return result.scalar_one_or_none()


async def _record_reachable(context: TaskContext, session: Any, snapshot: _Snapshot) -> None:
    """A successful send is evidence the destination works."""
    from meobot.core.time import utcnow
    from meobot.db.models.user import User

    if snapshot.registered_chat_id is not None:
        await ChatRegistryService(session).record_health(
            chat_id=snapshot.registered_chat_id, category=FailureCategory.NONE
        )
    if snapshot.recipient_user_id is not None:
        user = await session.get(User, snapshot.recipient_user_id)
        if user is not None:
            user.private_chat_available = True
            user.last_private_delivery_at = utcnow()
            user.private_delivery_failure_category = None
            user.bot_blocked_at = None


async def _record_unreachable(
    context: TaskContext, session: Any, snapshot: _Snapshot, category: FailureCategory
) -> None:
    """Learn that a destination is unusable, so it stops being offered.

    Only for failures that say something about the *recipient*. A network blip
    means nothing about whether the group still exists.
    """
    from meobot.core.time import utcnow
    from meobot.db.models.user import User

    if not category.is_recipient_problem:
        return

    if snapshot.registered_chat_id is not None:
        await ChatRegistryService(session).record_health(
            chat_id=snapshot.registered_chat_id, category=category
        )
    if (
        snapshot.recipient_user_id is not None
        and category is FailureCategory.PRIVATE_CHAT_UNAVAILABLE
    ):
        user = await session.get(User, snapshot.recipient_user_id)
        if user is not None:
            user.private_chat_available = False
            user.private_delivery_failure_category = category.value
            user.bot_blocked_at = utcnow()


@shared_task(name="notifications.recover_stale")
def recover_stale() -> dict[str, Any]:
    """Return messages stranded by a worker that died mid-delivery.

    A claim older than ``NOTIFICATION_PROCESSING_TIMEOUT_SECONDS`` is assumed
    dead. The timeout is far longer than any real send, so this never steals a
    delivery that is actually in flight.
    """
    return run_async(_recover)


async def _recover(context: TaskContext) -> dict[str, Any]:
    async with context.database.transaction() as session:
        recovered = await OutboxService(session, context.settings).recover_stale()
    return {"recovered": recovered}


@shared_task(name="notifications.retry_message")
def retry_message(outbound_message_id: str) -> dict[str, Any]:
    """Put one failed message back in the queue, by hand.

    Used after somebody has fixed the thing automatic retries could not - added
    the bot back to a group, restored a permission.
    """
    return run_async(lambda context: _retry_one(context, uuid.UUID(outbound_message_id)))


async def _retry_one(context: TaskContext, message_id: uuid.UUID) -> dict[str, Any]:
    async with context.database.transaction() as session:
        outbox = OutboxService(session, context.settings)
        row = await outbox.by_id(message_id)
        if row is None:
            return {"retried": False, "reason": "not_found"}
        if row.status.is_settled and row.status.value == "DELIVERED":
            # Already sent. Retrying would duplicate it.
            return {"retried": False, "reason": "already_delivered"}
        await outbox.retry_now(row)
    return {"retried": True}
