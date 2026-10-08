"""The one place that decides whether an incoming Telegram update is answered.

Before this existed, ``ActorMiddleware`` asked a single question - "is the
sender a registered user?" - and refused everybody else. That is the right
question for a business operation and the wrong one for a conversation: it
cannot express "a stranger tagged MeoBot in a group", "this person is muted
here", or "this member has used their twenty messages".

So the gate runs *before* the registered-actor requirement, in a fixed order,
and every step can only narrow what the previous one allowed:

1. parse the sender and the chat;
2. record the observed Telegram account (metadata only);
3. reject bots and anonymous senders - neither has an identity to authorise;
4. resolve the authoritative system user, if there is one;
5. check global lifecycle status;
6. check the per-group response policy;
7. open or reuse an approval request for an unknown person;
8. check the mention/reply requirement;
9. reserve a Guest question or a Member chat slot;
10. build the principal the transport is allowed to use.

Nothing before step 10 calls a provider, plans a tool, or writes a conversation
message. That ordering is the privacy guarantee: an ignored message and an
unapproved stranger's message never become memory, because the code that would
store them is downstream of a gate they never pass.

Steps 5 and 6 are deliberately in that order. A per-group ``allow`` can widen
nothing: suspension and revocation are properties of the account and are
checked first, so no chat-level setting can put a blocked account back on air.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.access_notifications_service import queue_access_request_notification
from meobot.application.access_request_service import AccessRequestService
from meobot.application.deferred_guest_service import DeferredGuestMessageService
from meobot.application.group_policy_service import GroupPolicyService
from meobot.application.hr_notifications import mark_private_chat_reachable
from meobot.application.identity_service import IdentityService
from meobot.application.observed_user_service import ObservedUserService
from meobot.application.quota_service import QuotaService, Reservation
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.access.models import (
    AccessDecision,
    AccessOutcome,
    GroupPolicyMode,
    GuestPrincipal,
    UserStatus,
)
from meobot.domain.identity.models import Actor
from meobot.domain.member.intents import is_operational

logger = get_logger(__name__)

BLOCKED_SUSPENDED = (
    "Tài khoản của bạn đang tạm khoá nên TasksBot chưa thể hỗ trợ. Vui lòng liên hệ Trưởng phòng."
)

BLOCKED_REVOKED = (
    "Tài khoản của bạn không còn quyền sử dụng TasksBot. Vui lòng liên hệ Trưởng phòng."
)

PENDING_ACKNOWLEDGEMENT = (
    "Mình đã chuyển yêu cầu của bạn tới Trưởng phòng để xin phép trả lời. Bạn chờ một chút nhé."
)


def quota_exhausted_message(limit: int, reset_local: str) -> str:
    """What a member sees on their twenty-first message of the day."""
    return (
        f"Bạn đã sử dụng hết {limit} lượt trò chuyện AI hôm nay.\n"
        f"Quota sẽ được đặt lại lúc 00:00 ngày mai ({reset_local}).\n"
        "Các lệnh nghiệp vụ thuộc quyền Member vẫn tiếp tục hoạt động."
    )


@dataclass(frozen=True, slots=True)
class IncomingUpdate:
    """The transport facts the gate needs, with nothing Telegram-specific left.

    Built by the middleware from the aiogram event so the gate itself stays
    testable without constructing Telegram objects.
    """

    bot_id: int
    chat_id: int
    chat_type: str
    telegram_user_id: int | None
    is_bot: bool
    message_id: int
    text: str
    is_command: bool
    addressed_to_bot: bool
    chat_title: str | None = None
    username: str | None = None
    display_name: str | None = None

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    @property
    def is_natural_chat(self) -> bool:
        """A free-text message aimed at MeoBot. Necessary, not sufficient.

        Being conversational is only half the test for whether a chat slot is
        spent - see :meth:`is_generative`.
        """
        return not self.is_command and self.addressed_to_bot and bool(self.text.strip())

    def is_generative(self, *, normalization_enabled: bool = True) -> bool:
        """True when answering this needs a model, and so costs a slot.

        **This is the quota boundary, and it is deliberately deterministic.**
        "Hôm nay tôi có việc gì?", "toi xin nghi sang mai" and "tôi còn bao
        nhiêu lượt?" are all answered from the database, so they cost nothing.
        "Viết giúp tôi 5 bình luận" needs the provider, so it costs one.

        Asking the model to make this call would be circular: the decision has
        to happen *before* the call it is deciding about. It would also mean an
        outage stopped somebody filing leave, which is exactly backwards.
        """
        if not self.is_natural_chat:
            return False
        return not is_operational(self.text, normalization_enabled=normalization_enabled)


@dataclass(slots=True)
class GateResult:
    """What the gate concluded, plus anything the transport must settle later."""

    decision: AccessDecision
    actor: Actor | None = None
    guest: GuestPrincipal | None = None
    #: A held quota slot. The transport commits it after Telegram accepts the
    #: reply and releases it on any failure - see
    #: :class:`~meobot.application.quota_service.QuotaService`.
    reservation: Reservation | None = None

    @property
    def outcome(self) -> AccessOutcome:
        return self.decision.outcome

    def take_reservation(self) -> Reservation | None:
        """Hand the held slot to the caller, exactly once.

        Settling has to happen on every exit path of a turn, and those paths
        overlap - an error handler that releases, followed by a ``finally``
        that releases again, would refund a slot twice and let one member spend
        the same allowance repeatedly. Taking it clears it, so the second call
        gets ``None`` and does nothing.
        """
        held, self.reservation = self.reservation, None
        return held


class AccessGate:
    """Runs the ordered access checks for one incoming update.

    Args:
        database: Opens its own short transaction; the gate runs before any
            handler and therefore owns its own unit of work.
        settings: Supplies the owner Telegram id and the quota timezone.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    async def evaluate(self, update: IncomingUpdate, *, now: datetime | None = None) -> GateResult:
        """Decide what happens to this update. Never raises."""
        moment = now or utcnow()
        try:
            return await self._evaluate(update, moment)
        except Exception:
            # A gate failure must not become an open door, and must not become
            # an outage either: stay quiet and let the operator see the trace.
            logger.exception(
                "access_gate_failed",
                extra={"chat_id": update.chat_id, "telegram_user_id": update.telegram_user_id},
            )
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.SILENT, reason="gate_error")
            )

    async def _evaluate(self, update: IncomingUpdate, now: datetime) -> GateResult:
        # 3. Bots and anonymous senders have no identity to authorise.
        if update.telegram_user_id is None:
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.SILENT, reason="anonymous_sender")
            )
        if update.is_bot:
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.SILENT, reason="bot_sender")
            )

        async with self._database.transaction() as session:
            # 2. Metadata only. Never message content.
            await ObservedUserService(session).record(
                telegram_user_id=update.telegram_user_id,
                username=update.username,
                display_name=update.display_name,
                is_bot=update.is_bot,
                chat_id=update.chat_id,
            )

            # Telegram will not let a bot open a conversation, so the only
            # way to learn that a private chat is reachable is to be messaged
            # in one. Recorded here, on the path every private message takes.
            if update.is_private and update.telegram_user_id is not None:
                await mark_private_chat_reachable(
                    session,
                    telegram_user_id=update.telegram_user_id,
                    chat_id=update.chat_id,
                )

            # 4. The authoritative identity, if this person has one.
            identity = IdentityService(session, self._settings)
            user = await identity.get_user_by_telegram_id(update.telegram_user_id)
            actor = await identity.resolve_actor(
                update.telegram_user_id,
                telegram_username=update.username,
                full_name=update.display_name,
            )

            # 5. Global lifecycle. Checked before any per-group setting, so an
            #    ``allow`` policy can never resurrect a blocked account.
            if user is not None and not user.may_use_meobot:
                return self._blocked_for_status(user.status, private=update.is_private)

            policy = GroupPolicyService(session)
            mode = GroupPolicyMode.INHERIT
            if not update.is_private:
                mode = await self.__resolve_mode(policy, update, now)

            # 6. Ignore and mute are silent by definition: the point is that the
            #    person cannot tell they are being ignored, and nothing they
            #    write is kept.
            if mode in {GroupPolicyMode.IGNORE, GroupPolicyMode.MUTE_UNTIL}:
                return GateResult(
                    decision=AccessDecision(outcome=AccessOutcome.SILENT, reason=f"policy_{mode}")
                )

            if mode is GroupPolicyMode.GUEST:
                return await self._guest_turn(session, policy, update, now)

            # 7. Nobody we know, and no Guest grant here.
            if actor is None:
                return await self._unknown_person(session, update, now)

            # 8. Registered, but this message was not aimed at MeoBot.
            if not update.addressed_to_bot:
                return GateResult(
                    decision=AccessDecision(outcome=AccessOutcome.NOT_ADDRESSED),
                    actor=actor,
                )

            # 9. Metered only for *generative* conversation. Slash commands,
            #    buttons, and every Vietnamese operational request - viewing
            #    work, filing leave, reporting progress - are answered from the
            #    database and cost nothing.
            reservation: Reservation | None = None
            metered = update.is_generative(
                normalization_enabled=self._settings.member_vietnamese_normalization_enabled
            )
            if metered and actor.user_id is not None:
                quota = QuotaService(session, self._settings)
                reservation, verdict = await quota.reserve_member(
                    user_id=actor.user_id, role=actor.role, now=now
                )
                # The reservation is the verdict. Branching on the *verdict*
                # would refuse the last message of the day, whose reservation
                # legitimately fills the allowance.
                if reservation is None and quota.is_metered(actor.role):
                    local = self._format_reset(quota, now)
                    return GateResult(
                        decision=AccessDecision(
                            outcome=AccessOutcome.BLOCKED,
                            actor_user_id=actor.user_id,
                            message=quota_exhausted_message(verdict.limit, local),
                            reason="quota_exhausted",
                        ),
                        actor=actor,
                    )

            # 10. A registered principal; the normal pipeline may run.
            return GateResult(
                decision=AccessDecision(
                    outcome=AccessOutcome.REGISTERED, actor_user_id=actor.user_id
                ),
                actor=actor,
                reservation=reservation,
            )

    @staticmethod
    async def __resolve_mode(
        policy: GroupPolicyService, update: IncomingUpdate, now: datetime
    ) -> GroupPolicyMode:
        return await policy.resolve_mode(
            bot_id=update.bot_id,
            chat_id=update.chat_id,
            telegram_user_id=update.telegram_user_id or 0,
            now=now,
        )

    @staticmethod
    def _blocked_for_status(status: UserStatus, *, private: bool) -> GateResult:
        """Refuse a suspended or revoked account, quietly in a group.

        The refusal is shown in a private chat, where it is between MeoBot and
        the person. In a group it is withheld: announcing somebody's account
        status to their colleagues is not MeoBot's to do, and the reason behind
        it never leaves the audit trail either way.
        """
        message = BLOCKED_REVOKED if status is UserStatus.REVOKED else BLOCKED_SUSPENDED
        return GateResult(
            decision=AccessDecision(
                outcome=AccessOutcome.BLOCKED,
                message=message,
                notify_in_group=False,
                reason=f"status_{status.value}",
            )
        )

    async def _guest_turn(
        self,
        session: AsyncSession,
        policy: GroupPolicyService,
        update: IncomingUpdate,
        now: datetime,
    ) -> GateResult:
        """A Guest's message: chat only, in this group only, if a slot is left."""
        guest = await policy.active_guest(
            bot_id=update.bot_id,
            chat_id=update.chat_id,
            telegram_user_id=update.telegram_user_id or 0,
            display_name=update.display_name or "Khách",
            username=update.username,
            now=now,
        )
        if guest is None:
            # The window closed between the mode check and here, or the grant
            # belongs to a different chat. Fall back to the stranger path.
            return await self._unknown_person(session, update, now)

        if update.is_command:
            # A Guest has no tools and no commands. Checked before the
            # addressing rule because a bare ``/pending_scripts`` in a group
            # carries no mention, and it still has to be refused rather than
            # fall through to a handler.
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.GUEST, reason="guest_command"),
                guest=guest,
            )

        if not update.addressed_to_bot:
            # A Guest talking to the room. Silent, not ``NOT_ADDRESSED``: the
            # latter falls through to the actor rules, which would answer
            # "this account is not registered" to every message they send.
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.SILENT, reason="guest_bystander")
            )

        quota = QuotaService(session, self._settings)
        reservation = await quota.reserve_guest(policy_id=guest.policy_id, now=now)
        if reservation is None:
            # Out of questions or out of time. The next mention opens a fresh
            # approval request rather than silently extending the grant.
            return await self._unknown_person(session, update, now)

        return GateResult(
            decision=AccessDecision(outcome=AccessOutcome.GUEST),
            guest=guest,
            reservation=reservation,
        )

    async def _unknown_person(
        self, session: AsyncSession, update: IncomingUpdate, now: datetime
    ) -> GateResult:
        """Somebody MeoBot does not know, in a group.

        Only a *direct* address opens a request. Overhearing a group's ordinary
        conversation must never produce an owner notification.
        """
        if update.is_private:
            # There is no group to be a Guest in, and "this account is not
            # registered" is both the right answer and the one ActorMiddleware
            # has always given. The gate steps out of the way.
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.DEFER, reason="unknown_private")
            )
        if not update.addressed_to_bot:
            # A stranger talking to their colleagues in a group MeoBot happens
            # to be in. Answering "you are not registered" to every one of those
            # messages is the noise this release exists to remove.
            return GateResult(
                decision=AccessDecision(outcome=AccessOutcome.SILENT, reason="unknown_bystander")
            )

        requests = AccessRequestService(session)
        request, should_notify = await requests.open_or_reuse(
            bot_id=update.bot_id,
            chat_id=update.chat_id,
            chat_title=update.chat_title,
            requester_telegram_id=update.telegram_user_id or 0,
            requester_username=update.username,
            requester_display_name=update.display_name,
            source_message_id=update.message_id,
            text=update.text,
            now=now,
        )

        # Keep the question itself, so approving this person answers what they
        # actually asked instead of making them ask again. Nothing is generated
        # here: the text is stored, redacted and bounded, and the model is not
        # consulted until somebody has decided.
        await DeferredGuestMessageService(session, self._settings).capture(
            request=request,
            bot_identity=update.bot_id,
            text=update.text,
            reply_to_message_id=update.message_id,
            now=now,
        )

        if should_notify:
            # The owner's copy goes through the outbox, in this transaction.
            # Before 0.6.0a2 the handler sent it itself, so a Telegram failure
            # at that moment lost the notification while leaving the request
            # open - the stranger waited for a decision nobody had been asked
            # to make.
            await queue_access_request_notification(
                session, self._settings, request=request, now=now
            )

        return GateResult(
            decision=AccessDecision(
                outcome=AccessOutcome.PENDING_APPROVAL,
                pending_request_id=request.id,
                message=PENDING_ACKNOWLEDGEMENT if should_notify else None,
                reason="notify_owner" if should_notify else "already_pending",
            )
        )

    def _format_reset(self, quota: QuotaService, now: datetime) -> str:
        """Local wall-clock time of the next reset, for the refusal message."""
        from meobot.core.time import format_local

        return format_local(quota.reset_hint(now), self._settings.timezone, fmt="%d/%m/%Y")


def owner_telegram_id(settings: Settings) -> int | None:
    """The account that receives approval requests, or ``None`` if unset."""
    return settings.meobot_owner_telegram_id


def owner_mention(settings: Settings, label: str = "Trưởng phòng") -> str:
    """An id-based Telegram mention for the owner.

    ``tg://user?id=...`` works whether or not the owner has a username, which
    ``@handle`` does not - and requiring the person who configured the bot to
    also expose a public username would be a strange thing to demand.
    """
    from meobot.bot.formatting import escape

    identifier = owner_telegram_id(settings)
    if identifier is None:
        return escape(label)
    return f'<a href="tg://user?id={identifier}">{escape(label)}</a>'
