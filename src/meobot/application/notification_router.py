"""Turning a business event into outbound messages - or refusing to.

This is the only place that decides *who gets told what*. Handlers do not, and
the LLM certainly does not: it never sees a chat id, a user id or a
classification, and there is no path from a generated sentence to a destination.

**The privacy check happens here, before the outbox row exists.** That ordering
is the point. Checking at delivery time would mean a personal reason had
already been written into a durable table addressed to a group; checking here
means the row is never created and the caller is told why in Vietnamese.

This class never calls Telegram. It writes rows in the caller's transaction -
the same one carrying the business change - and a worker sends them later.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.outbox_service import OutboundRequest, OutboxService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.notifications import OutboundMessage, TelegramChat
from meobot.domain.notifications.models import (
    NotificationEvent,
    PrivacyClassification,
    RecipientType,
)
from meobot.domain.notifications.routing import RouteVerdict, may_route
from meobot.domain.notifications.templates import template_for

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """One "tell somebody this" instruction, before it is checked."""

    event_type: NotificationEvent
    template_key: str
    payload: dict[str, Any]
    idempotency_key: str
    aggregate_type: str
    aggregate_id: uuid.UUID | None = None
    #: Exactly one of these two decides the destination.
    recipient_user_id: uuid.UUID | None = None
    private_chat_id: int | None = None
    destination: TelegramChat | None = None
    source_chat_id: int | None = None
    created_by_user_id: uuid.UUID | None = None
    #: Human names for the failure alert, resolved now rather than later.
    destination_label: str | None = None
    business_summary: str | None = None
    #: True when this message is itself an alert, so a failure of it cannot
    #: produce another one.
    is_alert: bool = False
    #: When a worker may first take this. ``None`` means immediately. Set only
    #: to hold a later part of a long announcement behind an earlier one.
    available_at: datetime | None = None


@dataclass(slots=True)
class RouteResult:
    """What the router did with a batch of instructions."""

    queued: list[OutboundMessage] = field(default_factory=list)
    duplicates: list[OutboundMessage] = field(default_factory=list)
    refusals: list[tuple[RouteRequest, RouteVerdict]] = field(default_factory=list)

    @property
    def any_queued(self) -> bool:
        return bool(self.queued)

    @property
    def user_messages(self) -> list[str]:
        """Vietnamese explanations for whatever was refused."""
        return [verdict.message for _, verdict in self.refusals if verdict.message]


class NotificationRouter:
    """Validates and records outbound messages. Never sends one.

    Args:
        session: The **same** unit of work as the business change, so the two
            commit together.
        settings: Retry budget, and the attendance-group switch.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._outbox = OutboxService(session, settings)

    async def route(self, requests: Sequence[RouteRequest]) -> RouteResult:
        """Check and queue each instruction independently.

        One refused destination does not cancel the others: an HR approval that
        can reach the Member privately but not the attendance group should
        still reach the Member. The caller reports the partial result.
        """
        result = RouteResult()
        for request in requests:
            verdict, recipient_type, chat_id = self._check(request)
            if not verdict.allowed:
                logger.info(
                    "notification_refused",
                    extra={
                        "event_type": request.event_type.value,
                        "reason": verdict.reason,
                        "template_key": request.template_key,
                    },
                )
                result.refusals.append((request, verdict))
                continue

            template = template_for(request.template_key)
            # Render now, even though the worker renders again: a payload that
            # cannot produce a message must fail here, in the caller's
            # transaction, not hours later in a worker.
            template.render(request.payload)

            row, created = await self._outbox.enqueue(
                OutboundRequest(
                    event_type=request.event_type,
                    aggregate_type=request.aggregate_type,
                    aggregate_id=request.aggregate_id,
                    recipient_type=recipient_type,
                    telegram_chat_id=chat_id,
                    template_key=template.key,
                    template_version=template.version,
                    privacy_classification=template.classification,
                    payload=dict(request.payload),
                    idempotency_key=request.idempotency_key,
                    recipient_user_id=request.recipient_user_id,
                    registered_chat_id=(
                        request.destination.id if request.destination is not None else None
                    ),
                    source_chat_id=request.source_chat_id,
                    created_by_user_id=request.created_by_user_id,
                    destination_label=(
                        request.destination_label
                        or (
                            request.destination.display_name
                            if request.destination is not None
                            else None
                        )
                    ),
                    business_summary=request.business_summary,
                    is_alert=request.is_alert,
                    available_at=request.available_at,
                )
            )
            (result.queued if created else result.duplicates).append(row)
        return result

    def _check(self, request: RouteRequest) -> tuple[RouteVerdict, RecipientType, int]:
        """Resolve the destination kind and run the privacy rules."""
        template = template_for(request.template_key)

        if request.destination is not None:
            verdict = may_route(
                classification=template.classification,
                recipient_type=RecipientType.REGISTERED_CHAT,
                destination_allows_automated=request.destination.allow_automated_delivery,
                destination_is_active=request.destination.is_active,
                destination_can_send=request.destination.bot_can_send,
            )
            # A destination may also cap what it accepts, independently of the
            # template - an attendance group can be configured to take nothing
            # above PUBLIC_OPERATIONAL.
            if verdict.allowed and (
                template.classification.rank > request.destination.privacy_level.rank
            ):
                verdict = RouteVerdict(
                    allowed=False,
                    reason="destination_privacy_ceiling",
                    message=("Nơi nhận này không được phép nhận loại thông tin đó."),
                )
            return verdict, RecipientType.REGISTERED_CHAT, request.destination.telegram_chat_id

        if request.private_chat_id is not None:
            verdict = may_route(
                classification=template.classification,
                recipient_type=RecipientType.USER_PRIVATE,
            )
            return verdict, RecipientType.USER_PRIVATE, request.private_chat_id

        return (
            RouteVerdict(
                allowed=False,
                reason="no_destination",
                message="Mình chưa xác định được nơi nhận cho tin này.",
            ),
            RecipientType.USER_PRIVATE,
            0,
        )


# --- Idempotency keys -------------------------------------------------------
# Stable, derived only from business identity plus the entity version. Two
# presses of the same button, a redelivered update and a retried service call
# all produce the same string, and the unique constraint does the rest.
def announcement_key(announcement_id: uuid.UUID, chat_id: int, version: int) -> str:
    return f"announcement_published:{announcement_id}:{chat_id}:{version}"


def hr_submitted_key(request_id: uuid.UUID, version: int, owner_chat_id: int) -> str:
    return f"hr_request_submitted:{request_id}:{version}:{owner_chat_id}"


def hr_approved_key(request_id: uuid.UUID, version: int, requester_user_id: uuid.UUID) -> str:
    return f"hr_request_approved:{request_id}:{version}:{requester_user_id}"


def hr_rejected_key(request_id: uuid.UUID, version: int, requester_user_id: uuid.UUID) -> str:
    return f"hr_request_rejected:{request_id}:{version}:{requester_user_id}"


def hr_more_info_key(request_id: uuid.UUID, version: int, requester_user_id: uuid.UUID) -> str:
    return f"hr_more_info_requested:{request_id}:{version}:{requester_user_id}"


def hr_attendance_key(request_id: uuid.UUID, version: int, attendance_chat_id: int) -> str:
    return f"hr_attendance_update:{request_id}:{version}:{attendance_chat_id}"


def announcement_reminder_key(announcement_id: uuid.UUID, telegram_user_id: int) -> str:
    return f"announcement_reminder:{announcement_id}:{telegram_user_id}"


# --- 0.6.0a2 ----------------------------------------------------------------
def access_request_key(request_id: uuid.UUID, destination_chat_id: int) -> str:
    return f"access_request_submitted:{request_id}:{destination_chat_id}"


def access_decision_key(request_id: uuid.UUID, action: str) -> str:
    return f"access_request_decided:{request_id}:{action}"


def quota_request_key(request_id: uuid.UUID, destination_chat_id: int) -> str:
    return f"quota_request_submitted:{request_id}:{destination_chat_id}"


def quota_decision_key(request_id: uuid.UUID, action: str) -> str:
    return f"quota_request_decided:{request_id}:{action}"


def guest_reply_key(deferred_message_id: uuid.UUID) -> str:
    """One reply per held question, however many times processing is attempted."""
    return f"guest_reply:{deferred_message_id}"


def reminder_occurrence_key(reminder_id: uuid.UUID, scheduled_for: str) -> str:
    """One message per (reminder, instant) - the sweep may run twice, this may not."""
    return f"reminder_due:{reminder_id}:{scheduled_for}"


def delivery_failure_key(outbound_message_id: uuid.UUID) -> str:
    """One alert per failed message. A second sweep must not re-alert."""
    return f"delivery_failed_alert:{outbound_message_id}"


def delivery_recovery_key(outbound_message_id: uuid.UUID) -> str:
    return f"delivery_recovered_alert:{outbound_message_id}"


def announcement_delivered_key(outbound_message_id: uuid.UUID) -> str:
    """One "đã gửi" per delivered message, however often the worker settles it."""
    return f"announcement_delivered:{outbound_message_id}"


# --- 0.6.0a3 ----------------------------------------------------------------
def dispatch_part_key(
    dispatch_id: uuid.UUID, chat_row_id: uuid.UUID, version: int, part_number: int
) -> str:
    """One outbox row per (dispatch, destination, version, part).

    This is the constraint the whole multi-group release rests on. A typed
    "Xác nhận" arriving at the same moment as a pressed "✅ Gửi tới 3 group",
    a redelivered Telegram update and a retried service call all produce this
    exact string, and ``uq_outbound_messages_idempotency_key`` lets exactly one
    of them create a row.

    ``version`` is the *dispatch* version, which a retry bumps - so retrying a
    group that failed queues a genuinely new message rather than colliding with
    the settled one it is replacing.
    """
    return f"dispatch:{dispatch_id}:chat:{chat_row_id}:version:{version}:part:{part_number}"


def dispatch_summary_key(dispatch_id: uuid.UUID, version: int) -> str:
    """One "kết quả gửi" per dispatch version, however often the last part settles."""
    return f"dispatch_summary:{dispatch_id}:{version}"


def pr_content_approved_key(content_id: uuid.UUID, approval_event_id: uuid.UUID) -> str:
    """Keyed on the *approval*, not the content.

    Step 1F.2.3b. A piece can be approved, undone and approved again, and the
    second approval genuinely deserves a second message - the responsible person
    was told it was withdrawn and now needs to know it is back on. Keying on the
    content id alone would have sent that once, ever.
    """
    return f"pr_content_approved:{content_id}:{approval_event_id}"


def pr_approval_key(event: str, approval_event_id: uuid.UUID, recipient_user_id: uuid.UUID) -> str:
    """One message per recipient per approval decision.

    Step 1F.2.3d, and keyed on the approval row for the reason
    :func:`pr_content_approved_key` gives: a decision that is undone and taken
    again is genuinely news twice, and keying on the content would send it once.

    The recipient is in the key because ``pr.internal_review_approved`` goes to
    two people. Without it, the producer's message and the responsible person's
    message would collide on one key and only one of them would be written.
    """
    return f"{event}:{approval_event_id}:{recipient_user_id}"


def pr_production_assigned_key(content_id: uuid.UUID, producer_user_id: uuid.UUID) -> str:
    """One message per person per piece.

    Reassigning to somebody and back again does not tell the first person twice:
    they still hold what they were told they hold.
    """
    return f"pr_production_assigned:{content_id}:{producer_user_id}"


def pr_workflow_undone_key(content_id: uuid.UUID, transition_event_id: uuid.UUID) -> str:
    return f"pr_workflow_undone:{content_id}:{transition_event_id}"


def destination_health_key(chat_id: uuid.UUID, health_version: int, healthy: bool) -> str:
    """One alert per health *transition*.

    ``health_version`` is bumped only when the status actually changes, so a
    sweep that keeps finding the same broken group produces one alert rather
    than one every half hour.
    """
    state = "recovered" if healthy else "unhealthy"
    return f"destination_{state}:{chat_id}:{health_version}"


def classification_of(template_key: str) -> PrivacyClassification:
    """How private a template's content is. Used by audit payloads."""
    return template_for(template_key).classification
