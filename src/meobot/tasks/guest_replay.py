"""Answering the question a stranger asked before anybody approved them.

Deliberately a background task rather than work done inside the owner's
callback handler. Three reasons, in order of how much they cost when ignored:

1. **The owner's button must not wait on a model.** A callback that calls an
   LLM makes the owner stare at a spinner for the length of a generation, and
   Telegram will time the callback out before a slow provider finishes.
2. **A crash must not lose the decision.** The approval is committed before
   this runs; if generation fails, the authorization is still there and the
   question is still answerable.
3. **Retries must be safe.** ``claim_for_processing`` is one conditional
   ``UPDATE``, so a redelivered update, a double-tapped button, a Celery retry
   and a restarted worker all converge on exactly one answer.

The generated reply goes into the outbox like everything else. No handler and
no task calls ``sendMessage`` for another chat.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from meobot.application.deferred_guest_service import DeferredGuestMessageService
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    guest_reply_key,
)
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.deferred import DeferredGuestMessage
from meobot.domain.access.models import GuestPrincipal
from meobot.domain.notifications.models import NotificationEvent
from meobot.tasks.celery_app import RETRY_KWARGS
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)

#: Said in the group when generation failed. Honest, and it does not blame the
#: person who asked.
GENERATION_FAILED = "Xin lỗi, MeoBot chưa trả lời được câu hỏi này. Bạn hỏi lại giúp mình nhé."


@shared_task(
    name="notifications.process_deferred_guest_message",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
)
def process_deferred_guest_message(deferred_message_id: str) -> dict[str, Any]:
    """Generate and queue the reply to one held question."""
    return run_async(lambda context: _process(context, uuid.UUID(deferred_message_id)))


async def _process(context: TaskContext, message_id: uuid.UUID) -> dict[str, Any]:
    settings = context.settings
    if not settings.notification_guest_replay_enabled:
        return {"answered": False, "reason": "disabled"}

    # --- 1. Claim. Whoever loses this race does nothing at all. -----------
    async with context.database.transaction() as session:
        service = DeferredGuestMessageService(session, settings)
        claimed = await service.claim_for_processing(message_id)
        if claimed is None:
            return {"answered": False, "reason": "already_handled_or_expired"}
        snapshot = _Snapshot.of(claimed)

    # --- 2. Generate, with no transaction open and no tools in reach. -----
    conversation = _build_conversation_service(context)
    guest = GuestPrincipal(
        policy_id=uuid.uuid4(),
        telegram_user_id=snapshot.telegram_user_id,
        telegram_chat_id=snapshot.chat_id,
        display_name="Khách",
        granted_at=utcnow(),
        expires_at=utcnow(),
        question_limit=1,
    )
    try:
        reply = await conversation.handle_guest_message(
            guest=guest, message=snapshot.text, request_id=current_request_id()
        )
        answer = reply.text.strip()
    except Exception as exc:
        logger.warning(
            "deferred_guest_generation_failed",
            extra={"deferred_message_id": str(message_id), "error": type(exc).__name__},
        )
        # The provider failed, not the person. Give the authorization back so a
        # retry can still answer, and charge the Guest nothing.
        async with context.database.transaction() as session:
            service = DeferredGuestMessageService(session, settings)
            row = await service.by_id(message_id)
            if row is not None:
                await service.release_claim(row)
        return {"answered": False, "reason": "generation_failed"}

    if not answer:
        answer = GENERATION_FAILED

    # --- 3. Queue the reply. Never send it from here. ---------------------
    async with context.database.transaction() as session:
        service = DeferredGuestMessageService(session, settings)
        row = await service.by_id(message_id)
        if row is None:  # pragma: no cover - deleted mid-flight
            return {"answered": False, "reason": "vanished"}

        router = NotificationRouter(session, settings)
        result = await router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.GUEST_REPLY,
                    template_key="guest.reply",
                    payload={"answer": answer},
                    idempotency_key=guest_reply_key(row.id),
                    aggregate_type="deferred_guest_message",
                    aggregate_id=row.id,
                    destination=await _destination_for(session, snapshot),
                    private_chat_id=(
                        snapshot.chat_id if snapshot.chat_id == snapshot.telegram_user_id else None
                    ),
                    source_chat_id=snapshot.chat_id,
                    destination_label="Group người hỏi",
                    business_summary="Trả lời câu hỏi của khách",
                )
            ]
        )
        messages = result.queued or result.duplicates
        if not messages:
            await service.mark_failed(row, category="destination_refused")
            return {"answered": False, "reason": "no_destination"}
        await service.mark_queued(row, outbox_message_id=messages[0].id)

    logger.info("deferred_guest_message_queued", extra={"deferred_message_id": str(message_id)})
    return {"answered": True}


async def _destination_for(session: Any, snapshot: _Snapshot) -> Any:
    """The registered group to reply into, when the question came from one.

    An unregistered group is still a valid place to answer a question that was
    asked there - the bot is present and was addressed - so a missing
    registration is not a refusal, it just means there is no ``TelegramChat``
    row to attach.
    """
    from meobot.application.chat_registry_service import ChatRegistryService

    if snapshot.chat_id >= 0:
        return None
    return await ChatRegistryService(session).by_telegram_id(
        bot_identity=snapshot.bot_identity, telegram_chat_id=snapshot.chat_id
    )


class _Snapshot:
    """The fields needed once the claiming transaction has closed."""

    __slots__ = ("bot_identity", "chat_id", "reply_to_message_id", "telegram_user_id", "text")

    def __init__(
        self,
        *,
        text: str,
        chat_id: int,
        telegram_user_id: int,
        bot_identity: int,
        reply_to_message_id: int | None,
    ) -> None:
        self.text = text
        self.chat_id = chat_id
        self.telegram_user_id = telegram_user_id
        self.bot_identity = bot_identity
        self.reply_to_message_id = reply_to_message_id

    @classmethod
    def of(cls, row: DeferredGuestMessage) -> _Snapshot:
        return cls(
            text=row.sanitized_text,
            chat_id=row.original_chat_id,
            telegram_user_id=row.original_telegram_user_id,
            bot_identity=row.bot_identity,
            reply_to_message_id=row.reply_to_message_id,
        )


def _build_conversation_service(context: TaskContext) -> Any:
    """A conversation service with a tool registry the Guest path never reads.

    ``handle_guest_message`` does not consult the registry or the policy engine
    at all - a Guest cannot execute a tool for the same reason a function
    without a database handle cannot write to one - but the constructor needs
    them, so they are built and left unused.
    """
    from meobot.application.conversation_service import ConversationService
    from meobot.application.health_service import HealthService
    from meobot.domain.policy.engine import PolicyEngine
    from meobot.tools.registry import build_default_registry

    registry = build_default_registry(
        health_service=HealthService(context.database, context.settings),
        sheets=context.sheets,
        llm=context.llm,
        drive=context.drive,
    )
    return ConversationService(
        llm=context.llm,
        registry=registry,
        policy=PolicyEngine(registry.policies()),
        database=context.database,
        settings=context.settings,
    )


@shared_task(name="notifications.purge_deferred_guest_messages")
def purge_deferred_guest_messages() -> dict[str, Any]:
    """Blank the text of held questions nobody decided about in time."""
    return run_async(_purge)


async def _purge(context: TaskContext) -> dict[str, Any]:
    async with context.database.transaction() as session:
        purged = await DeferredGuestMessageService(session, context.settings).purge_expired()
    return {"purged": purged}
