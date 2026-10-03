"""The periodic destination health sweep.

Probes a bounded batch of registered groups, records what changed, and queues
one alert per *transition* - never one per check. A group that has been broken
since Tuesday produces one message on Tuesday, not one every half hour until
somebody fixes it.

Nothing here sends a visible message to the destination being checked.
``getChat`` and ``getChatMember`` read state; a "test message" would be a
notification nobody asked for, in a group full of real colleagues.

The alerts themselves go through the outbox on ``q_notifications``, like every
other cross-chat message MeoBot sends.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from meobot.application.destination_health_service import (
    DestinationHealthService,
    HealthTransition,
)
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    destination_health_key,
)
from meobot.application.recipient_resolver import RecipientResolver
from meobot.core.logging import get_logger
from meobot.domain.notifications.models import (
    NotificationEvent,
    health_label,
)
from meobot.integrations.telegram.notifier import HealthProbe
from meobot.tasks.runtime import TaskContext, run_async

logger = get_logger(__name__)


@shared_task(name="notifications.sweep_destination_health")
def sweep_destination_health() -> dict[str, Any]:
    """Check a bounded batch of destinations and alert on any transition."""
    return run_async(_sweep)


async def _sweep(context: TaskContext) -> dict[str, Any]:
    settings = context.settings
    if not settings.notification_destination_health_enabled:
        return {"checked": 0, "skipped": "disabled"}

    probe = context.notifier
    if not isinstance(probe, HealthProbe):
        # The configured transport cannot read chat state - the null notifier,
        # for instance, when no token is set. Say so once rather than failing.
        logger.info("destination_health_unsupported_transport")
        return {"checked": 0, "skipped": "transport_cannot_probe"}

    checked = 0
    transitions: list[HealthTransition] = []
    async with context.database.transaction() as session:
        service = DestinationHealthService(session, settings, probe)
        for chat in await service.due_batch():
            transition = await service.check(chat)
            checked += 1
            if transition is not None:
                transitions.append(transition)

        if transitions:
            await _alert(session, settings, transitions)

    logger.info(
        "destination_health_swept",
        extra={"checked": checked, "transitions": len(transitions)},
    )
    return {"checked": checked, "transitions": len(transitions)}


async def _alert(session: Any, settings: Any, transitions: list[HealthTransition]) -> None:
    """Queue one message per health transition, to the owner.

    Bound to ``health_version``, which only moves when the status actually
    changes - so re-running this sweep cannot produce a second alert about a
    problem that has not changed.
    """
    owner = await RecipientResolver(session, settings).owner()
    if not owner.is_resolved:
        logger.info("destination_health_alert_no_owner")
        return

    router = NotificationRouter(session, settings)
    requests: list[RouteRequest] = []
    for transition in transitions:
        if transition.became_healthy:
            if not settings.notification_recovery_alert_enabled:
                continue
            template_key = "destination.recovered"
            event = NotificationEvent.DESTINATION_RECOVERED
            payload: dict[str, Any] = {"destination_label": transition.chat.display_name}
        else:
            if not settings.notification_failure_alert_enabled:
                continue
            template_key = "destination.unhealthy"
            event = NotificationEvent.DESTINATION_UNHEALTHY
            payload = {
                "destination_label": transition.chat.display_name,
                "health_label": health_label(transition.current),
            }

        requests.append(
            RouteRequest(
                event_type=event,
                template_key=template_key,
                payload=payload,
                idempotency_key=destination_health_key(
                    transition.chat.id,
                    transition.health_version,
                    healthy=transition.current.is_healthy,
                ),
                aggregate_type="telegram_chat",
                aggregate_id=transition.chat.id,
                private_chat_id=owner.telegram_chat_id,
                destination_label="Chat riêng",
                business_summary=f"Tình trạng nơi nhận: {transition.chat.display_name}"[:300],
                # An alert about a broken destination must not itself be able
                # to produce an alert about a broken destination.
                is_alert=True,
            )
        )

    if requests:
        await router.route(requests)
