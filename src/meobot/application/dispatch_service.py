"""Confirming one announcement into N durable, independently-settling deliveries.

**One confirmation, one decision, N outcomes.** That shape is the whole point.
A person presses "✅ Gửi tới 3 group" once; three groups then succeed or fail on
their own, and a group that has removed the bot must not hold up the other two
or make the sender believe nothing went out.

Everything in :meth:`DispatchService.confirm` happens in the caller's single
transaction: the dispatch row, the recipient snapshot, the parts, and one outbox
intent per (destination, part). Either all of it commits or none of it does, so
there is no state in which somebody has been told "đã xếp hàng gửi" and no
outbox row exists.

**Permission is checked twice.** Once when the candidates are drafted, so the
preview is honest, and again here, immediately before the commit - because a
Trưởng nhóm can lose an assignment between reading a preview and pressing the
button, and the preview is not authorisation. If a destination fails the second
check the whole confirmation is refused and says which group; quietly sending to
a shorter list is how somebody comes to believe a group was told something.

**Parts are ordered by construction.** Every part of a long announcement gets
its outbox row at confirmation time - the intent is durable with the decision -
but parts after the first are held, and released only when the part before them
actually lands. "Part 2 arrived first" is a worse failure than "part 2 arrived a
minute later".

Nothing here calls Telegram.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    dispatch_part_key,
    dispatch_summary_key,
)
from meobot.application.outbox_service import OutboxService
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConflictError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.dispatch import (
    MessageDispatch,
    MessageDispatchDraft,
    MessageDispatchDraftRecipient,
    MessageDispatchPart,
    MessageDispatchRecipient,
    MessageDispatchRecipientPart,
)
from meobot.db.models.notifications import OutboundMessage, TelegramChat
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.dispatch.models import (
    DispatchPartStatus,
    DispatchRecipientStatus,
    DispatchStatus,
)
from meobot.domain.dispatch.privacy import classify_announcement
from meobot.domain.dispatch.splitting import marker_for, split_announcement
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import (
    ChatPurpose,
    FailureCategory,
    NotificationEvent,
    OutboxStatus,
)
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

#: How long a part that is waiting for the one before it is held. Never reached
#: in practice - a part is released when its predecessor lands, or cancelled
#: when its predecessor permanently fails - so this is a backstop against a
#: worker dying between the two, not a schedule.
PART_HOLD = timedelta(days=365)

CANNOT_BROADCAST = (
    f"Bạn chưa được phép gửi thông báo tới group. Việc này thuộc quyền {role_label(Role.OWNER)}."
)
NO_DESTINATION_SELECTED = (
    "Bạn chưa chọn group nào để gửi. Bạn chọn ít nhất một group giúp mình nhé."
)
NOT_ALLOWED_HERE = (
    "Bạn không còn được phép gửi vào {name} nên TasksBot chưa gửi thông báo này.\n"
    "Bạn bỏ nơi nhận đó rồi gửi lại giúp mình nhé."
)
PRIVATE_CONTENT = "Thông báo này có thể chứa thông tin cá nhân và không phù hợp để gửi vào group."
ALREADY_CONFIRMED = "Thông báo này đã được xác nhận gửi rồi."


@dataclass(frozen=True, slots=True)
class DestinationOutcome:
    """One group's result, ready to be read out to the sender."""

    display_name: str
    status: DispatchRecipientStatus
    failure_category: FailureCategory = FailureCategory.NONE


class DispatchService:
    """Turns a confirmed draft into durable per-destination deliveries.

    Args:
        session: Unit of work. Confirmation writes the dispatch, the recipient
            snapshot and every outbox intent here, together.
        settings: Retry budget, via the outbox.
        audit: Audit writer sharing the session.
    """

    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._router = NotificationRouter(session, settings)
        self._outbox = OutboxService(session, settings)
        self._assignments = ChatAssignmentService(session)

    # --- Authorization ----------------------------------------------------
    @staticmethod
    def may_broadcast(actor: Actor) -> bool:
        """Whether this person may address any group at all.

        The same narrow permission single-destination announcements use.
        Speaking to a room of colleagues in MeoBot's voice is not a settings
        change, so it does not ride on ``SETTINGS_WRITE``.
        """
        return has_permission(actor.role, Permission.ANNOUNCEMENT_BROADCAST)

    async def may_use(self, actor: Actor, destination: TelegramChat) -> bool:
        """Whether this person may send to *this* group.

        A Trưởng nhóm reaches exactly the groups they are explicitly assigned
        to manage - never a group that merely shares a purpose with one, and
        never the department-wide destination, which stays with the owner
        however many teams somebody leads.
        """
        if self.may_broadcast(actor):
            return True
        if destination.purpose is ChatPurpose.DEPARTMENT_ANNOUNCEMENTS:
            return False
        return await self._assignments.may_broadcast_to(user_id=actor.user_id, chat=destination)

    async def permitted_ids(self, actor: Actor, chats: Sequence[TelegramChat]) -> set[uuid.UUID]:
        """Which of these destinations this person may address."""
        allowed: set[uuid.UUID] = set()
        for chat in chats:
            if await self.may_use(actor, chat):
                allowed.add(chat.id)
        return allowed

    # --- Confirming -------------------------------------------------------
    async def confirm(
        self, *, actor: Actor, request_id: uuid.UUID, draft: MessageDispatchDraft
    ) -> tuple[MessageDispatch, bool]:
        """Confirm a draft into one dispatch. Idempotent.

        Returns ``(dispatch, created)``. ``created=False`` means this draft had
        already been confirmed - by a button press that arrived a moment before
        a typed "Xác nhận", or by a redelivered update - and the answer is the
        dispatch that already exists, not a second one.

        Raises:
            ConflictError: Nothing is selected, or the draft is not open.
            AuthorizationError: The actor may not broadcast, or may not use one
                of the selected destinations any more.
            ValidationError: The content is too private to post in a group.
        """
        if draft.dispatch_id is not None:
            existing = await self._session.get(MessageDispatch, draft.dispatch_id)
            if existing is not None:
                return existing, False
        if not self.may_broadcast(actor) and actor.user_id is None:
            raise AuthorizationError(CANNOT_BROADCAST)

        from meobot.application.dispatch_draft_service import DispatchDraftService

        drafts = DispatchDraftService(self._session, self._settings)
        chosen = await drafts.selected(draft.id)
        if not chosen:
            raise ConflictError(NO_DESTINATION_SELECTED)

        # --- Privacy, again, on the exact text about to be committed -------
        verdict = classify_announcement(draft.rendered_text)
        if not verdict.may_reach_a_group:
            raise ValidationError(PRIVATE_CONTENT)

        # --- Permission, again, immediately before the commit --------------
        destinations: list[tuple[MessageDispatchDraftRecipient, TelegramChat]] = []
        for row in chosen:
            chat = await self._session.get(TelegramChat, row.recipient_chat_row_id)
            if chat is None:
                raise ConflictError(
                    f"Group “{row.display_name}” không còn trong danh sách đã đăng ký."
                )
            if not await self.may_use(actor, chat):
                raise AuthorizationError(NOT_ALLOWED_HERE.format(name=row.display_name))
            destinations.append((row, chat))

        parts = split_announcement(draft.rendered_text)
        if not parts:
            raise ConflictError("Thông báo này chưa có nội dung.")

        now = utcnow()
        dispatch = MessageDispatch(
            bot_identity=draft.bot_identity,
            created_by_user_id=draft.created_by_user_id,
            created_by_telegram_id=draft.created_by_telegram_id,
            source_chat_id=draft.source_chat_id,
            content=draft.rendered_text,
            privacy_classification=verdict.classification,
            status=DispatchStatus.QUEUED,
            recipient_count=len(destinations),
            total_parts=parts[0].total,
            version=1,
            confirmed_at=now,
        )
        self._session.add(dispatch)
        await self._session.flush()

        part_rows: list[MessageDispatchPart] = []
        for part in parts:
            stored = MessageDispatchPart(
                dispatch_id=dispatch.id,
                part_number=part.number,
                total_parts=part.total,
                content=part.content,
                created_at=now,
            )
            self._session.add(stored)
            part_rows.append(stored)
        await self._session.flush()

        for draft_row, chat in destinations:
            recipient = MessageDispatchRecipient(
                dispatch_id=dispatch.id,
                telegram_chat_row_id=chat.id,
                telegram_chat_id=chat.telegram_chat_id,
                destination_display_name=(draft_row.display_name or chat.display_name)[:200],
                position=draft_row.position,
                status=DispatchRecipientStatus.QUEUED,
                attempt_version=1,
            )
            self._session.add(recipient)
            await self._session.flush()
            await self._queue_parts(
                dispatch=dispatch, recipient=recipient, chat=chat, part_rows=part_rows
            )

        await drafts.mark_confirmed(draft, dispatch_id=dispatch.id)

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ANNOUNCEMENT_PUBLISHED.value,
            result=AuditResult.SUCCESS,
            entity_type="message_dispatch",
            entity_id=str(dispatch.id),
            after_data={
                "source_chat_id": dispatch.source_chat_id,
                # Every destination, by name and by chat id: a cross-chat
                # action has to stay reconstructable afterwards.
                "destination_chat_ids": [chat.telegram_chat_id for _, chat in destinations],
                "destination_names": [
                    str(getattr(row, "display_name", "")) for row, _ in destinations
                ],
                "recipient_count": len(destinations),
                "part_count": len(parts),
                "privacy_classification": verdict.classification.value,
                "actor_role": actor.role.value,
                "actor_role_label": role_label(actor.role),
            },
        )
        logger.info(
            "dispatch_confirmed",
            extra={
                "dispatch_id": str(dispatch.id),
                "recipients": len(destinations),
                "parts": len(parts),
            },
        )
        return dispatch, True

    async def _queue_parts(
        self,
        *,
        dispatch: MessageDispatch,
        recipient: MessageDispatchRecipient,
        chat: TelegramChat,
        part_rows: Sequence[MessageDispatchPart],
        only_parts: set[int] | None = None,
    ) -> bool:
        """One outbox intent per part, with everything after the first held.

        The parts sent are the **stored** ones, never a re-split of the
        content: a worker running a later version of the splitter must not be
        able to deliver different words from the ones the sender confirmed.

        Returns:
            Whether anything was actually queued. ``False`` means the router
            refused this destination, and the caller must not then report it as
            on its way - which is precisely what pressing "thử lại nơi lỗi" on
            a group MeoBot has been removed from would otherwise do.
        """
        first_wanted = min(only_parts) if only_parts else 1
        for part_row in part_rows:
            number = part_row.part_number
            if only_parts is not None and number not in only_parts:
                continue
            held = number > first_wanted
            result = await self._router.route(
                [
                    RouteRequest(
                        event_type=NotificationEvent.DISPATCH_PART_PUBLISHED,
                        template_key="dispatch.group_part",
                        payload={
                            "content": part_row.content,
                            "part_marker": marker_for(number, part_row.total_parts),
                            "is_last_part": number == part_row.total_parts,
                        },
                        idempotency_key=dispatch_part_key(
                            dispatch.id, chat.id, dispatch.version, number
                        ),
                        aggregate_type="message_dispatch",
                        aggregate_id=dispatch.id,
                        destination=chat,
                        source_chat_id=dispatch.source_chat_id,
                        created_by_user_id=dispatch.created_by_user_id,
                        destination_label=recipient.destination_display_name,
                        business_summary=_summary_of(dispatch.content),
                        available_at=(utcnow() + PART_HOLD) if held else None,
                    )
                ]
            )
            landed = result.queued or result.duplicates
            if not landed:
                # The router refused this destination - disabled, opted out, or
                # over its privacy ceiling. That is a real outcome for this
                # group and none of the others, so it is recorded as one.
                recipient.status = DispatchRecipientStatus.SKIPPED
                recipient.failure_category = FailureCategory.DESTINATION_REFUSED
                recipient.failed_at = utcnow()
                await self._session.flush()
                return False
            self._session.add(
                MessageDispatchRecipientPart(
                    recipient_id=recipient.id,
                    dispatch_part_id=part_row.id,
                    part_number=number,
                    outbound_message_id=landed[0].id,
                    status=DispatchPartStatus.QUEUED,
                )
            )
        await self._session.flush()
        return True

    # --- Settling ---------------------------------------------------------
    async def settle_delivery(
        self,
        *,
        outbound_message_id: uuid.UUID,
        delivered: bool,
        category: FailureCategory = FailureCategory.NONE,
    ) -> MessageDispatch | None:
        """Record what happened to one part at one destination.

        Called by the worker inside the transaction that settles the outbox
        row, so a destination's business outcome and its delivery record cannot
        disagree.

        Returns:
            The dispatch when this settlement was the one that finished it, and
            ``None`` otherwise - so the summary is sent exactly once, by
            whichever worker happened to settle last.
        """
        part = await self._part_for(outbound_message_id)
        if part is None:
            return None
        recipient = await self._session.get(MessageDispatchRecipient, part.recipient_id)
        if recipient is None:  # pragma: no cover - cascade would have removed it
            return None
        dispatch = await self._session.get(MessageDispatch, recipient.dispatch_id)
        if dispatch is None:  # pragma: no cover
            return None

        now = utcnow()
        if delivered:
            part.status = DispatchPartStatus.DELIVERED
            part.delivered_at = now
            await self._release_next_part(recipient, after=part.part_number)
        else:
            part.status = DispatchPartStatus.FAILED
            part.failed_at = now
            recipient.failure_category = category
            # Nothing after a failed part may go out: the group would read the
            # second half of an announcement whose first half never arrived.
            await self._cancel_parts_after(recipient, after=part.part_number)
        await self._session.flush()

        await self._recompute_recipient(recipient)
        settled = await self._recompute_dispatch(dispatch)
        return dispatch if settled else None

    async def _part_for(
        self, outbound_message_id: uuid.UUID
    ) -> MessageDispatchRecipientPart | None:
        result = await self._session.execute(
            select(MessageDispatchRecipientPart).where(
                MessageDispatchRecipientPart.outbound_message_id == outbound_message_id
            )
        )
        return result.scalar_one_or_none()

    async def _parts_of(self, recipient_id: uuid.UUID) -> list[MessageDispatchRecipientPart]:
        result = await self._session.execute(
            select(MessageDispatchRecipientPart)
            .where(MessageDispatchRecipientPart.recipient_id == recipient_id)
            .order_by(MessageDispatchRecipientPart.part_number.asc())
        )
        return list(result.scalars().all())

    async def _release_next_part(self, recipient: MessageDispatchRecipient, *, after: int) -> None:
        """Let the next part go, now that this one has actually landed."""
        for part in await self._parts_of(recipient.id):
            if part.part_number != after + 1 or part.outbound_message_id is None:
                continue
            row = await self._session.get(OutboundMessage, part.outbound_message_id)
            if row is not None:
                await self._outbox.release(row)
            return

    async def _cancel_parts_after(self, recipient: MessageDispatchRecipient, *, after: int) -> None:
        """Stop the rest of a long announcement whose earlier part failed."""
        for part in await self._parts_of(recipient.id):
            if part.part_number <= after or part.outbound_message_id is None:
                continue
            row = await self._session.get(OutboundMessage, part.outbound_message_id)
            if row is not None and not row.status.is_settled:
                await self._outbox.cancel(row)

    async def _recompute_recipient(self, recipient: MessageDispatchRecipient) -> None:
        """Roll one destination's parts up into one honest status."""
        parts = await self._parts_of(recipient.id)
        delivered = [part for part in parts if part.status is DispatchPartStatus.DELIVERED]
        failed = [part for part in parts if part.status is DispatchPartStatus.FAILED]
        recipient.delivered_parts = len(delivered)
        recipient.failed_parts = len(failed)

        if len(delivered) == len(parts) and parts:
            recipient.status = DispatchRecipientStatus.DELIVERED
            recipient.delivered_at = recipient.delivered_at or utcnow()
            recipient.failure_category = FailureCategory.NONE
        elif failed and delivered:
            # Some of a long announcement arrived and some did not. Reporting
            # this as either "đã gửi" or "chưa gửi được" would be false.
            recipient.status = DispatchRecipientStatus.PARTIAL_FAILURE
            recipient.failed_at = utcnow()
        elif failed:
            recipient.status = DispatchRecipientStatus.FAILED
            recipient.failed_at = utcnow()
        else:
            recipient.status = DispatchRecipientStatus.QUEUED
        await self._session.flush()

    async def _recompute_dispatch(self, dispatch: MessageDispatch) -> bool:
        """Update the counters; report whether this settled the last destination."""
        recipients = await self.recipients(dispatch.id)
        settled = [row for row in recipients if row.status.is_settled]
        dispatch.recipient_count = len(recipients)
        dispatch.delivered_count = sum(
            1 for row in recipients if row.status is DispatchRecipientStatus.DELIVERED
        )
        dispatch.failed_count = sum(
            1
            for row in recipients
            if row.status
            in {
                DispatchRecipientStatus.FAILED,
                DispatchRecipientStatus.PARTIAL_FAILURE,
                DispatchRecipientStatus.SKIPPED,
            }
        )
        dispatch.retrying_count = len(recipients) - len(settled)

        was_settled = dispatch.status.is_settled
        if len(settled) < len(recipients):
            dispatch.status = DispatchStatus.IN_PROGRESS if settled else DispatchStatus.QUEUED
            await self._session.flush()
            return False

        if dispatch.delivered_count == len(recipients):
            dispatch.status = DispatchStatus.COMPLETED
        elif dispatch.delivered_count:
            dispatch.status = DispatchStatus.PARTIALLY_FAILED
        else:
            dispatch.status = DispatchStatus.FAILED
        dispatch.completed_at = dispatch.completed_at or utcnow()
        await self._session.flush()
        return not was_settled

    # --- Retrying ---------------------------------------------------------
    async def retry_failed(self, *, actor: Actor, dispatch: MessageDispatch) -> tuple[int, int]:
        """Queue only the destinations, and only the parts, that did not arrive.

        Returns ``(destinations, parts)``. A destination that already received
        the announcement is deliberately untouched: the person pressing "thử
        lại nơi lỗi" is fixing the ones that failed, and re-sending the others
        would post the same announcement twice in groups that read it an hour
        ago.

        A destination MeoBot still cannot reach - it was removed from the group
        and nobody has added it back - is **not** counted. Pressing a button
        does not repair a group, and reporting "đã xếp lại 1 group" when nothing
        was queued is the same lie as "đã gửi" before Telegram has answered.

        The dispatch version is bumped first, so every requeued part gets a new
        idempotency key rather than colliding with the settled row it replaces.
        """
        recipients = [row for row in await self.recipients(dispatch.id) if row.status.needs_retry]
        if not recipients:
            return (0, 0)

        parts = await self.parts(dispatch.id)
        dispatch.version += 1
        await self._session.flush()

        requeued_parts = 0
        requeued_destinations = 0
        for recipient in recipients:
            chat = await self._session.get(TelegramChat, recipient.telegram_chat_row_id)
            if chat is None or not await self.may_use(actor, chat):
                continue
            existing = {part.part_number: part for part in await self._parts_of(recipient.id)}
            missing = {
                number
                for number, part in existing.items()
                if part.status is not DispatchPartStatus.DELIVERED
            } or {part.part_number for part in parts}
            # Drop the rows for the parts being re-queued, keeping the
            # delivered ones. That is what makes a retry send the third message
            # to a group that already read the first two, rather than all three.
            for number in sorted(missing):
                stale = existing.get(number)
                if stale is not None:
                    await self._session.delete(stale)
            await self._session.flush()

            queued = await self._queue_parts(
                dispatch=dispatch,
                recipient=recipient,
                chat=chat,
                part_rows=parts,
                only_parts=missing,
            )
            if not queued:
                # Still refused. Pressing the button does not make a group
                # work, and saying "đã xếp lại" when nothing was queued is the
                # same lie in a different place.
                continue
            recipient.status = DispatchRecipientStatus.QUEUED
            recipient.failure_category = FailureCategory.NONE
            recipient.failed_at = None
            recipient.attempt_version += 1
            requeued_parts += len(missing)
            requeued_destinations += 1

        if requeued_destinations:
            dispatch.status = DispatchStatus.IN_PROGRESS
            dispatch.completed_at = None
            # A new round of deliveries deserves a new report, and the key it
            # is queued under carries the bumped version - so this cannot
            # re-send the previous round's summary.
            dispatch.summary_sent_at = None
        await self._session.flush()
        await self._recompute_dispatch(dispatch)
        logger.info(
            "dispatch_retried",
            extra={
                "dispatch_id": str(dispatch.id),
                "requested": len(recipients),
                "recipients": requeued_destinations,
                "parts": requeued_parts,
            },
        )
        return (requeued_destinations, requeued_parts)

    # --- Reporting --------------------------------------------------------
    async def recipients(self, dispatch_id: uuid.UUID) -> list[MessageDispatchRecipient]:
        result = await self._session.execute(
            select(MessageDispatchRecipient)
            .where(MessageDispatchRecipient.dispatch_id == dispatch_id)
            .order_by(MessageDispatchRecipient.position.asc())
        )
        return list(result.scalars().all())

    async def parts(self, dispatch_id: uuid.UUID) -> list[MessageDispatchPart]:
        result = await self._session.execute(
            select(MessageDispatchPart)
            .where(MessageDispatchPart.dispatch_id == dispatch_id)
            .order_by(MessageDispatchPart.part_number.asc())
        )
        return list(result.scalars().all())

    async def by_id(self, dispatch_id: uuid.UUID) -> MessageDispatch | None:
        return await self._session.get(MessageDispatch, dispatch_id)

    async def outcomes(self, dispatch_id: uuid.UUID) -> list[DestinationOutcome]:
        """Every destination's own result, in the order they were shown."""
        return [
            DestinationOutcome(
                display_name=row.destination_display_name,
                status=row.status,
                failure_category=row.failure_category,
            )
            for row in await self.recipients(dispatch_id)
        ]

    async def queue_summary(self, dispatch: MessageDispatch) -> bool:
        """Tell the sender how it went, once, through the outbox.

        Through the outbox rather than sent from the worker for the same reason
        everything else is: a worker that dies mid-report must not lose it. The
        summary is flagged as an alert so that failing to deliver *it* cannot
        raise an alert about the alert, which is how a broken private chat
        becomes an infinite loop.
        """
        if dispatch.summary_sent_at is not None or dispatch.created_by_user_id is None:
            return False

        from meobot.application.recipient_resolver import RecipientResolver

        private = await RecipientResolver(self._session, self._settings).private_destination(
            user_id=dispatch.created_by_user_id
        )
        if not private.is_resolved:
            return False

        outcomes = await self.outcomes(dispatch.id)
        delivered = [item for item in outcomes if item.status is DispatchRecipientStatus.DELIVERED]
        failed = [item for item in outcomes if item.status is not DispatchRecipientStatus.DELIVERED]

        result = await self._router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.DISPATCH_SUMMARY,
                    template_key="dispatch.summary",
                    payload={
                        "delivered": len(delivered),
                        "total": len(outcomes),
                        "delivered_lines": "\n".join(
                            f"✅ {item.display_name}" for item in delivered
                        ),
                        "failed_lines": "\n".join(
                            f"⚠️ {item.display_name}\n{_reason_for(item.failure_category)}"
                            for item in failed
                        ),
                    },
                    idempotency_key=dispatch_summary_key(dispatch.id, dispatch.version),
                    aggregate_type="message_dispatch",
                    aggregate_id=dispatch.id,
                    recipient_user_id=dispatch.created_by_user_id,
                    private_chat_id=private.telegram_chat_id,
                    business_summary=_summary_of(dispatch.content),
                    is_alert=True,
                )
            ]
        )
        dispatch.summary_sent_at = utcnow()
        await self._session.flush()
        return result.any_queued

    async def cancel_pending(self, dispatch: MessageDispatch) -> int:
        """Stop everything of this dispatch that has not gone out yet."""
        cancelled = 0
        for recipient in await self.recipients(dispatch.id):
            for part in await self._parts_of(recipient.id):
                if part.outbound_message_id is None:
                    continue
                row = await self._session.get(OutboundMessage, part.outbound_message_id)
                if row is not None and row.status is OutboxStatus.PENDING:
                    await self._outbox.cancel(row)
                    cancelled += 1
        return cancelled


def _summary_of(content: str) -> str:
    """A short description of an announcement, for a failure report."""
    flattened = " ".join((content or "").split())
    return flattened[:120] if len(flattened) <= 120 else f"{flattened[:117]}..."


def _reason_for(category: FailureCategory) -> str:
    """Why one destination did not receive it, in Vietnamese."""
    return {
        FailureCategory.BOT_NOT_IN_CHAT: "TasksBot không còn ở trong group này.",
        FailureCategory.BOT_CANNOT_SEND: "TasksBot hiện không có quyền gửi tin trong group này.",
        FailureCategory.CHAT_NOT_FOUND: "TasksBot không còn tìm thấy group này.",
        FailureCategory.PRIVATE_CHAT_UNAVAILABLE: "Chưa mở được cuộc trò chuyện với người nhận.",
        FailureCategory.DESTINATION_REFUSED: "Nơi nhận này đang không nhận thông báo tự động.",
        FailureCategory.RATE_LIMITED: "Telegram đang giới hạn tốc độ gửi.",
        FailureCategory.NETWORK: "Kết nối tới Telegram đang gặp sự cố.",
        FailureCategory.PROVIDER_UNAVAILABLE: "Telegram tạm thời không phản hồi.",
        FailureCategory.NONE: "Chưa gửi xong.",
    }.get(category, "TasksBot chưa gửi được vào group này.")
