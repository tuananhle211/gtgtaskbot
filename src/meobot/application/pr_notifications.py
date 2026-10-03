"""Turning PR workflow events into messages, in the same transaction.

Step 1F.2.3b. The join between the PR services - which have never known that
Telegram exists, and still do not - and the notification router. A separate
module for the reason ``hr_notifications`` is one: the workflow should not learn
about chats, and this way it does not.

Two channels, one record
------------------------

Step 1F.2.3d added the web notification centre, and with it a rule this module
now enforces: **the durable record of what somebody was told does not depend on
Telegram being able to reach them.** Every method writes a
:class:`~meobot.db.models.user_notification.UserNotification` row first, then
attempts a Telegram message if that person has a private chat. Before, an
unreachable recipient meant nothing was written anywhere and nobody was told
anything; see :meth:`PrNotificationService._send`.

What is sent, and to whom
-------------------------

Six messages, each to the people who now have something to do - or have stopped
having it, or need to know a decision landed:

* **Head approval** goes to whoever is *responsible* for the content. That is the
  same relation ``MY_CONTENT`` and the "Người phụ trách" filter use - the owner,
  or somebody holding an unfinished task on it - because the question "who should
  be told this is approved" and the question "whose work is this" have to have
  one answer. It says what to do next, which is the whole point: an approval
  nobody is told about is a piece that waits at ``APPROVED`` until somebody
  scrolls past it;
* **producer assignment** goes to the producer. Not to the manager who decided
  it: the person who pressed the button already knows;
* **an undone decision** goes to whoever was told the decision in the first
  place, and says plainly that it no longer holds;
* **Team Lead approval** goes to the responsible person as *status*. They have
  nothing to do - the piece is a Head's decision now - but silence between
  submitting and hearing back reads as nobody having looked at it;
* **an internal-review revision** goes to the producer, who is the only person
  who can act on it, with the reviewer's note attached;
* **an internal-review approval** goes to the producer *and* the responsible
  person, because it is two pieces of news: work accepted, and piece ready.

What is deliberately not sent
------------------------------

* nothing for a **self-claim**, and nothing to whoever pressed the button on any
  of the six - telling somebody what they just did is noise;
* nothing to **every holder of a review capability**. When a Team Lead approves,
  the Heads are not messaged: that is a broadcast, it arrives per item, and
  ``MY_ACTIONS`` is already a better review queue than a stream of interruptions.
  A capability is not a person;
* nothing when a **priority changes**. A manager retriaging a backlog would
  otherwise produce a notification per item, which is how people learn to ignore
  the bell. The change is audited and visible on the card;
* nothing to a group. Every template here is ``PERSONAL_PRIVATE``: they name a
  person's work and a reviewer's decision, and the routing rules would have to
  judge that for every group. A private chat needs no judgement;
* no **Telegram** message when the recipient is unreachable or inactive. The
  resolver says so and this logs it, rather than queueing a row for a chat that
  will refuse it. The inbox row is still written - that is the change above.

Failure is not silent, and not fatal
-------------------------------------

Every function here runs on the **caller's session**, inside the transaction
carrying the workflow change, so the intent to notify commits with the decision
or not at all. What it does *not* do is fail the decision: a person who cannot be
reached is a logged fact, not a reason to refuse an approval. The router itself
refuses a bad destination the same way - see
:class:`~meobot.application.notification_router.RouteResult`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    RouteResult,
    pr_approval_key,
    pr_content_approved_key,
    pr_production_assigned_key,
    pr_workflow_undone_key,
)
from meobot.application.pr_content_query import responsible_for
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.domain.identity.models import Actor
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.web import web_body, web_title
from meobot.domain.pr.labels import stage_label

logger = get_logger(__name__)

#: What each undo is called, in the words the recipient will recognise from the
#: button somebody pressed. Keyed on the undo kind's *value* so this module does
#: not import the service that defines it - the dependency runs the other way.
UNDO_DESCRIPTIONS: dict[str, str] = {
    "UNDO_TEAM_LEAD_APPROVAL": "duyệt của Trưởng nhóm đã được hoàn tác",
    "UNDO_HEAD_APPROVAL": "duyệt của Trưởng phòng đã được hoàn tác",
    "UNDO_INTERNAL_REVIEW": "duyệt nội bộ đã được hoàn tác",
    "UNDO_REVISION": "yêu cầu sửa đã được hoàn tác",
}


class PrNotificationService:
    """Queues the messages PR workflow changes should produce.

    Args:
        session: The same unit of work as the workflow change.
        settings: Supplies the panel's base URL for the deep link, and the retry
            budget the router reads.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)
        self._inbox = UserNotificationService(session)

    async def on_head_approved(
        self, *, content: PrContentItem, approval: PrApprovalEvent
    ) -> RouteResult:
        """Tell the responsible person the script is approved and needs a producer."""
        recipient_id = await self._responsible_user(content)
        if recipient_id is None:
            return RouteResult()
        return await self._send(
            event_type=NotificationEvent.PR_CONTENT_APPROVED,
            template_key="pr.content_approved",
            payload=self._base_payload(content),
            idempotency_key=pr_content_approved_key(content.id, approval.id),
            content=content,
            recipient_user_id=recipient_id,
            created_by_user_id=approval.reviewer_user_id,
        )

    async def on_team_lead_approved(
        self, *, content: PrContentItem, approval: PrApprovalEvent
    ) -> RouteResult:
        """Tell the responsible person their script cleared the first gate.

        Step 1F.2.3d, and the one message in this module that is **status rather
        than a task**: the piece is now waiting on a Head, and its author has
        nothing to do. It is sent anyway because the alternative is that the
        author learns nothing between submitting and either an approval days
        later or a revision request - and silence in that gap reads as *nobody
        has looked at it*.

        Who this does **not** go to: the Heads. Everybody holding
        ``PR_HEAD_REVIEW`` would be a broadcast, and it would arrive per item -
        which for a reviewer with a real queue is the definition of noise.
        ``MY_ACTIONS`` already shows every item standing at a gate they hold, and
        a list somebody chooses to open beats a message that interrupts them.
        The rule this module keeps is *personal and actionable*, and a
        capability is not a person.

        Skipped when the Team Lead approved their own piece, for the reason every
        other self-action here is: telling somebody what they just did is noise.
        """
        recipient_id = await self._responsible_user(content)
        if recipient_id is None or recipient_id == approval.reviewer_user_id:
            return RouteResult()
        return await self._send(
            event_type=NotificationEvent.PR_CONTENT_TEAM_LEAD_APPROVED,
            template_key="pr.team_lead_approved",
            payload=self._base_payload(content),
            idempotency_key=pr_approval_key("pr_team_lead_approved", approval.id, recipient_id),
            content=content,
            recipient_user_id=recipient_id,
            created_by_user_id=approval.reviewer_user_id,
        )

    async def on_production_revision_required(
        self, *, content: PrContentItem, approval: PrApprovalEvent, note: str | None = None
    ) -> RouteResult:
        """Tell the producer their cut came back.

        Step 1F.2.3d. To the producer and nobody else: they are the only person
        who can act on it, and the work is stopped until they do. The reviewer's
        note travels with it when there is one, because "cần sửa" with no reason
        costs the reader a trip to the panel to learn anything at all.

        Falls back to the responsible person when no producer is named - an
        internal review can only have happened if somebody produced the cut, but
        a producer can be cleared afterwards, and the piece still needs an owner
        who knows it came back.
        """
        recipient_id = content.producer_user_id or await self._responsible_user(content)
        if recipient_id is None or recipient_id == approval.reviewer_user_id:
            return RouteResult()
        payload = self._base_payload(content)
        if note:
            payload["note"] = note
        return await self._send(
            event_type=NotificationEvent.PR_PRODUCTION_REVISION_REQUIRED,
            template_key="pr.production_revision_required",
            payload=payload,
            idempotency_key=pr_approval_key(
                "pr_production_revision_required", approval.id, recipient_id
            ),
            content=content,
            recipient_user_id=recipient_id,
            created_by_user_id=approval.reviewer_user_id,
        )

    async def on_internal_review_approved(
        self, *, content: PrContentItem, approval: PrApprovalEvent
    ) -> RouteResult:
        """Tell the producer and the responsible person the cut passed.

        Step 1F.2.3d, and the only event here with **two** recipients, because
        it is two different pieces of news: the producer's work was accepted, and
        the responsible person's piece is ready for what comes next. One message
        addressed vaguely to whichever of them the code found first would have
        left the other to discover it by scrolling.

        They are frequently the same person, and the ``set`` below is what stops
        that from producing two identical rows. Whoever pressed the button is
        dropped for the usual reason.

        The results are merged, so a caller sees one
        :class:`~meobot.application.notification_router.RouteResult` describing
        everything that was queued and everything that was refused.
        """
        recipients: list[uuid.UUID] = []
        for candidate in (content.producer_user_id, await self._responsible_user(content)):
            if candidate is None or candidate == approval.reviewer_user_id:
                continue
            if candidate not in recipients:
                recipients.append(candidate)

        merged = RouteResult()
        for recipient_id in recipients:
            result = await self._send(
                event_type=NotificationEvent.PR_INTERNAL_REVIEW_APPROVED,
                template_key="pr.internal_review_approved",
                payload=self._base_payload(content),
                idempotency_key=pr_approval_key(
                    "pr_internal_review_approved", approval.id, recipient_id
                ),
                content=content,
                recipient_user_id=recipient_id,
                created_by_user_id=approval.reviewer_user_id,
            )
            merged.queued.extend(result.queued)
            merged.duplicates.extend(result.duplicates)
            merged.refusals.extend(result.refusals)
        return merged

    async def on_producer_assigned(
        self, *, content: PrContentItem, producer_user_id: uuid.UUID, actor: Actor
    ) -> RouteResult:
        """Tell the producer, unless they are the one who took it.

        The self-claim case reaches this method too - the caller does not have to
        remember which command it is in - and is dropped here, once.
        """
        if actor.user_id is not None and actor.user_id == producer_user_id:
            return RouteResult()
        return await self._send(
            event_type=NotificationEvent.PR_PRODUCTION_ASSIGNED,
            template_key="pr.production_assigned",
            payload=self._base_payload(content),
            idempotency_key=pr_production_assigned_key(content.id, producer_user_id),
            content=content,
            recipient_user_id=producer_user_id,
            created_by_user_id=actor.user_id,
        )

    async def on_workflow_undone(
        self, *, content: PrContentItem, kind: object, actor: Actor
    ) -> RouteResult:
        """Tell whoever was told the decision that it has been taken back.

        The producer first when there is one - after an internal-review undo the
        cut is back in front of them - and the responsible person otherwise,
        which is who a withdrawn Head approval concerns.
        """
        recipient_id = content.producer_user_id or await self._responsible_user(content)
        if recipient_id is None:
            return RouteResult()
        if actor.user_id is not None and actor.user_id == recipient_id:
            # The person who pressed undo does not need telling what they undid.
            return RouteResult()

        kind_value = getattr(kind, "value", str(kind))
        payload = self._base_payload(content)
        payload["what"] = UNDO_DESCRIPTIONS.get(kind_value, "một bước duyệt đã được hoàn tác")
        payload["stage_label"] = stage_label(content.workflow_stage)
        return await self._send(
            event_type=NotificationEvent.PR_WORKFLOW_UNDONE,
            template_key="pr.workflow_undone",
            payload=payload,
            # Keyed on the *reversal*, which is unique per undo, so a piece
            # approved and undone twice produces two messages - as it should.
            idempotency_key=pr_workflow_undone_key(content.id, uuid.uuid4()),
            content=content,
            recipient_user_id=recipient_id,
            created_by_user_id=actor.user_id,
        )

    # --- Internals --------------------------------------------------------
    async def _send(
        self,
        *,
        event_type: NotificationEvent,
        template_key: str,
        payload: dict[str, object],
        idempotency_key: str,
        content: PrContentItem,
        recipient_user_id: uuid.UUID,
        created_by_user_id: uuid.UUID | None,
    ) -> RouteResult:
        """Record this event in the person's inbox, then try to reach them.

        The order is the point, and it is the correction Step 1F.2.3d makes to
        this module.

        Before it, the first thing every method here did was resolve a private
        Telegram chat and return early when there was none. That was correct
        while Telegram was the only channel - queueing a row for a chat that will
        refuse it helps nobody - but it meant the **durable record of what
        somebody was told did not exist unless Telegram could carry it**. A
        colleague who has never opened the bot was told nothing, anywhere, and
        the web panel had nothing to show them.

        So the inbox row is written **first and unconditionally**, and Telegram
        becomes what it always was: one delivery channel, attempted when a
        destination exists. An unreachable recipient is still a logged fact and
        still not an error - the approval must not fail because somebody has not
        started the bot - but now they see it the moment they open the panel.

        Both writes are on the caller's session, inside the workflow's
        transaction, so the decision and the record of announcing it commit
        together or not at all.
        """
        await self._inbox.record(
            recipient_user_id=recipient_user_id,
            event=event_type,
            title=web_title(event_type),
            body=web_body(
                event_type,
                title=content.title,
                what=str(payload.get("what", "")),
                note=str(payload.get("note", "")),
            ),
            idempotency_key=idempotency_key,
            target_kind="pr_content",
            target_id=content.id,
        )

        chat_id = await self._private_chat(recipient_user_id)
        if chat_id is None:
            # No Telegram destination. The inbox row above is the notification;
            # this only means one channel is unavailable.
            return RouteResult()

        result = await self._router.route(
            [
                RouteRequest(
                    event_type=event_type,
                    template_key=template_key,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    aggregate_type="pr_content_item",
                    aggregate_id=content.id,
                    recipient_user_id=recipient_user_id,
                    private_chat_id=chat_id,
                    created_by_user_id=created_by_user_id,
                    business_summary=f"{content.code} — {content.title}",
                )
            ]
        )
        if result.refusals:
            logger.info(
                "pr_notification_refused",
                extra={
                    "pr_content_id": str(content.id),
                    "event_type": event_type.value,
                    "reasons": [verdict.reason for _, verdict in result.refusals],
                },
            )
        return result

    def _base_payload(self, content: PrContentItem) -> dict[str, object]:
        """Title, code and the link that opens the piece in the panel."""
        return {
            "title": content.title,
            "content_code": content.code,
            "link": f"{self._settings.web_base_url.rstrip('/')}/pr/content/{content.id}",
        }

    async def _responsible_user(self, content: PrContentItem) -> uuid.UUID | None:
        """Who is responsible for this piece.

        The owner first, then anybody holding an unfinished task - the same two
        relationships :func:`responsible_for` is, asked in that order so a piece
        with both is announced once and to the person accountable for it.

        Returns a **user id**, not a destination. Since Step 1F.2.3d the two are
        separate questions: who should be told is a business fact, and whether
        Telegram can reach them is a delivery detail that must not decide it.
        """
        found = await self._session.execute(
            select(PrContentItem.owner_user_id).where(
                PrContentItem.id == content.id, responsible_for(content.owner_user_id)
            )
        )
        return found.scalars().first() or content.owner_user_id

    async def _private_chat(self, user_id: uuid.UUID) -> int | None:
        """A person's private chat id, or ``None`` with a logged reason.

        Unreachable is not an error: a teammate who has never opened the bot is
        a normal state of the world, an approval must not fail because of it,
        and since Step 1F.2.3d they still get the notification - in the panel.
        """
        resolved = await self._resolver.private_destination(user_id=user_id)
        if not resolved.is_resolved or resolved.telegram_chat_id is None:
            logger.info(
                "pr_notification_recipient_unreachable",
                extra={"user_id": str(user_id), "reason": resolved.message},
            )
            return None
        return resolved.telegram_chat_id


__all__: list[str] = ["UNDO_DESCRIPTIONS", "PrNotificationService"]
