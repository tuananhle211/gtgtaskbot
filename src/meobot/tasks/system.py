"""System tasks: liveness ping and the periodic heartbeat."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from meobot.core.context import get_request_id
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.tasks.celery_app import RETRY_KWARGS

logger = get_logger(__name__)


@shared_task(name="system.ping", bind=False)
def ping() -> dict[str, Any]:
    """Cheapest possible round-trip through the broker.

    Used by ``/health`` in the bot and by operators to verify a worker consumes
    ``q_default``.
    """
    payload = {
        "pong": True,
        "at": utcnow().isoformat(),
        "request_id": get_request_id(),
    }
    logger.info("system_ping", extra={"task": "system.ping"})
    return payload


@shared_task(
    name="system.periodic_heartbeat",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def periodic_heartbeat() -> dict[str, Any]:
    """Beat-scheduled proof that the scheduler and worker are both alive.

    Kept intentionally dependency-free: it must succeed even when PostgreSQL is
    briefly unavailable, so a failing heartbeat means 'the queue is broken',
    not 'the database is busy'.

    TODO(milestone-3): also refresh a ``system_settings`` heartbeat row so the
    bot's ``/health`` can report the age of the last successful beat.
    """
    now = utcnow()
    logger.info("system_heartbeat", extra={"task": "system.periodic_heartbeat"})
    return {"heartbeat_at": now.isoformat(), "request_id": get_request_id()}


@shared_task(
    name="conversations.cleanup_expired",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def cleanup_expired_conversations() -> dict[str, Any]:
    """Delete abandoned multi-step Telegram conversations.

    An unfinished ``/add_sheet`` should not keep a half-built profile in the
    database forever, and the state carries no value once it has expired.
    """
    from meobot.application.conversation_state_service import (
        ConversationStateService,
    )
    from meobot.tasks.runtime import TaskContext, run_async

    async def work(context: TaskContext) -> int:
        async with context.database.transaction() as session:
            service = ConversationStateService(
                session, ttl_seconds=context.settings.conversation_ttl_seconds
            )
            return await service.purge_expired()

    deleted = run_async(work)
    logger.info("conversations_cleanup", extra={"deleted": deleted})
    return {"deleted": deleted, "request_id": get_request_id()}
