"""aiogram middlewares: correlation id, actor resolution and the owner-only gate."""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TelegramUser

from meobot.application.access_gate import AccessGate, GateResult, IncomingUpdate
from meobot.application.actor_profile_service import ActorProfileService
from meobot.application.identity_service import IdentityService
from meobot.bot import formatting
from meobot.bot.access_notifications import (
    notify_owner_of_access_request,
    quota_request_keyboard,
)
from meobot.bot.addressing import bot_username_of, is_addressed_to_bot
from meobot.bot.commands import (
    has_full_bot,
    owner_only_command_reply,
    owner_only_commands,
    public_commands,
)
from meobot.bot.texts import NOT_REGISTERED
from meobot.core.config import Settings
from meobot.core.context import new_request_id, request_context
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.access.models import AccessOutcome
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class RequestContextMiddleware(BaseMiddleware):
    """Bind a fresh correlation id to every update."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        request_id = new_request_id()
        with request_context(request_id):
            data["request_id"] = uuid.UUID(request_id)
            return await handler(event, data)


#: How many update ids :class:`DeduplicationMiddleware` remembers. Telegram
#: redelivers an update when our acknowledgement is lost, and a redelivered
#: message must not produce a second answer - or, worse, a second tool run.
#: A few thousand ids is minutes of traffic and a few hundred kilobytes.
SEEN_UPDATE_CAPACITY = 4096


class DeduplicationMiddleware(BaseMiddleware):
    """Process each Telegram ``update_id`` exactly once.

    Long polling is at-least-once. On a restart, on a network hiccup, or when
    ``getUpdates`` is answered twice, the same update arrives again. Without
    this, "đồng bộ các Sheet" delivered twice is two synchronisations, and an
    ordinary message is two replies to one question.

    In-memory on purpose: it protects one process's lifetime, which is the
    window Telegram redelivers in. Idempotency of the *actions* themselves is
    enforced separately and durably by the tool layer.
    """

    def __init__(self, capacity: int = SEEN_UPDATE_CAPACITY) -> None:
        self._capacity = capacity
        self._seen: OrderedDict[int, None] = OrderedDict()

    def already_processed(self, update_id: int) -> bool:
        """True when this id has been seen. Records it either way."""
        if update_id in self._seen:
            self._seen.move_to_end(update_id)
            return True
        self._seen[update_id] = None
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_id = getattr(event, "update_id", None)
        if isinstance(update_id, int) and self.already_processed(update_id):
            logger.info("update_skipped_duplicate", extra={"update_id": update_id})
            return None
        return await handler(event, data)


#: Commands an *unregistered* account may run, read off the command registry
#: rather than duplicated here. ``/join`` is how somebody becomes registered in
#: the first place, so refusing it would make invite codes unusable.
#:
#: ``/start`` is marked public in the registry but is deliberately excluded
#: here: an unregistered account must get the "not registered" message, and the
#: ``/start`` handler assumes an actor is bound.
PUBLIC_COMMANDS: frozenset[str] = public_commands() - {"/start"}


#: Commands only the owner may run, read off the registry the same way: every
#: command whose spec is not marked ``basic``.
OWNER_ONLY_COMMANDS: frozenset[str] = owner_only_commands()


def _is_public_command(event: TelegramObject) -> bool:
    """True when the update is one an unregistered account may send."""
    if not isinstance(event, Message):
        return False
    text = (event.text or "").strip()
    if not text.startswith("/"):
        return False
    # '/join@MeoBot CODE' -> '/join'
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    return command in PUBLIC_COMMANDS


def _command_for_this_bot(message: Message) -> str | None:
    """``/name`` of a slash command aimed at this bot, else ``None``.

    Read from the text or the caption, as aiogram's ``Command`` filter does. A
    command suffixed with *another* bot's username (``/sheets@OtherBot`` in a
    shared group) is not ours to refuse - no handler of ours would run it.
    """
    text = (message.text or message.caption or "").strip()
    if not text.startswith("/"):
        return None
    command, _, mention = text.split(maxsplit=1)[0].partition("@")
    username = bot_username_of(message)
    if mention and username and mention.lower() != username.lower():
        return None
    return command.lower()


class AccessGateMiddleware(BaseMiddleware):
    """Run :class:`~meobot.application.access_gate.AccessGate` before anything else.

    Registered as an outer middleware on messages, *ahead* of
    :class:`ActorMiddleware`, so the question "may MeoBot respond to this at
    all" is settled before the question "is this a registered user". The two
    are genuinely different: a muted colleague is registered and must be
    ignored; a Guest is unregistered and must be answered.

    What this middleware puts in ``data``:

    * ``access`` - the :class:`~meobot.application.access_gate.GateResult`,
      including any held quota slot the handler has to settle;
    * ``guest_principal`` - present only for a Guest, and the filter the
      guest-only router matches on.

    What it does *not* do is bind ``actor``. That stays
    :class:`ActorMiddleware`'s job, so no code path gained a new way to obtain
    an actor, and a Guest cannot acquire one.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings
        self._gate = AccessGate(database, settings)

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        update = self._describe(event)
        if update is None:
            return await handler(event, data)

        result = await self._gate.evaluate(update)
        data["access"] = result
        outcome = result.outcome

        if outcome is AccessOutcome.SILENT:
            # Ignored, muted, a bot, or an anonymous sender. Nothing is sent
            # and nothing is stored: the whole point of "ignore" is that the
            # person cannot tell, and that their words leave no trace.
            return None

        if outcome is AccessOutcome.BLOCKED:
            if self._quota_is_moot(result):
                # Out of AI turns, but this person has no AI chat on Telegram
                # anyway. Let the turn through so the conversation handler
                # answers with what they *can* use, not "hết lượt".
                return await handler(event, data)
            await self._answer_blocked(event, result)
            return None

        if outcome is AccessOutcome.PENDING_APPROVAL:
            await self._notify_owner(event, result)
            return None

        if outcome is AccessOutcome.GUEST and result.guest is not None:
            data["guest_principal"] = result.guest
            return await handler(event, data)

        return await handler(event, data)

    def _describe(self, message: Message) -> IncomingUpdate | None:
        """Translate the aiogram event into the gate's transport-free input."""
        if message.bot is None:  # pragma: no cover - always present in practice
            return None
        sender = message.from_user
        text = message.text or message.caption or ""
        return IncomingUpdate(
            bot_id=message.bot.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            telegram_user_id=sender.id if sender is not None else None,
            username=sender.username if sender is not None else None,
            display_name=sender.full_name if sender is not None else None,
            is_bot=bool(sender.is_bot) if sender is not None else False,
            message_id=message.message_id,
            text=text,
            is_command=text.strip().startswith("/"),
            addressed_to_bot=is_addressed_to_bot(
                message, require_mention=self._settings.chat_group_requires_mention
            ),
        )

    @staticmethod
    def _quota_is_moot(result: GateResult) -> bool:
        """True for a quota refusal of somebody who only has the basic bot."""
        return (
            result.decision.reason == "quota_exhausted"
            and result.actor is not None
            and not has_full_bot(result.actor.role)
        )

    @staticmethod
    async def _answer_blocked(message: Message, result: GateResult) -> None:
        """Tell the sender why, when telling them is appropriate.

        A quota notice is the member's own business and is fine in any chat. An
        account-status refusal is withheld in a group: whether somebody is
        suspended is between them and the owner, not an announcement.
        """
        decision = result.decision
        if decision.message is None:
            return
        if message.chat.type != "private" and not decision.notify_in_group:
            return
        markup = None
        if decision.reason == "quota_exhausted" and result.actor is not None:
            markup = quota_request_keyboard()
        await formatting.answer(message, formatting.escape(decision.message), reply_markup=markup)

    async def _notify_owner(self, message: Message, result: GateResult) -> None:
        """Ask the owner what to do about a stranger, at most once."""
        if result.decision.pending_request_id is None:  # pragma: no cover - defensive
            return
        await notify_owner_of_access_request(
            message=message,
            database=self._database,
            settings=self._settings,
            request_id=result.decision.pending_request_id,
            acknowledge=result.decision.message,
        )


class ActorMiddleware(BaseMiddleware):
    """Resolve the Telegram sender into an :class:`Actor`, or refuse the update.

    Unregistered accounts never reach a handler - except for the commands in
    :data:`PUBLIC_COMMANDS`, which is how a new employee redeems an invite.
    This is the outermost *authorisation* boundary of the bot; the policy engine
    is the inner one.

    One case is delegated: when :class:`AccessGateMiddleware` has already
    authorised a Guest, this middleware lets the update through **without
    binding an actor**. That is not a relaxation. Every handler that touches
    business data declares ``actor: Actor``, and the guest-only router is
    included first and consumes the update - so a Guest reaches exactly one
    handler, and it is the one that has no actor to work with.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user: TelegramUser | None = data.get("event_from_user")
        if telegram_user is None:
            return None

        if data.get("guest_principal") is not None:
            # Authorised by the gate as a Guest. No actor is bound, so nothing
            # that needs authority can run.
            return await handler(event, data)

        async with self._database.session() as session:
            identity = IdentityService(session, self._settings)
            actor = await identity.resolve_actor(
                telegram_user.id,
                telegram_username=telegram_user.username,
                full_name=telegram_user.full_name,
            )

        if actor is None or not actor.active:
            if _is_public_command(event):
                # No actor is bound: the /join handler must not assume one.
                return await handler(event, data)
            logger.info(
                "update_rejected_unregistered",
                extra={"telegram_user_id": telegram_user.id},
            )
            if isinstance(event, Message):
                await event.answer(NOT_REGISTERED)
            elif isinstance(event, CallbackQuery):
                await event.answer(NOT_REGISTERED, show_alert=True)
            return None

        await self._ensure_profile(actor, telegram_user.full_name)
        data["actor"] = actor
        return await handler(event, data)

    async def _ensure_profile(self, actor: Actor, telegram_display_name: str | None) -> None:
        """Open a conversational profile on first contact.

        Telegram's display name only *initialises* the profile; it never
        overwrites a name the person set themselves. Failure is swallowed: a
        profile is context, and an update must not be dropped because a
        descriptive row could not be written.
        """
        try:
            async with self._database.transaction() as session:
                await ActorProfileService(session, self._settings).ensure_row(
                    actor, telegram_display_name=telegram_display_name
                )
        except Exception:
            logger.warning(
                "actor_profile_bootstrap_failed",
                extra={"telegram_user_id": actor.telegram_user_id},
            )


class BasicCommandsMiddleware(BaseMiddleware):
    """Refuse owner-only commands for everybody but the owner.

    On Telegram only the owner gets the whole bot; Nhân viên, Trưởng nhóm and
    Quản trị viên get the commands marked ``basic`` in
    :mod:`meobot.bot.commands` and work on the web. This is the single place
    that rule is enforced for slash commands - registered right after
    :class:`ActorMiddleware`, so the actor is known and no handler has run.
    Free text is the conversation handler's half of the same rule.

    Anything without a bound actor passes untouched: an unregistered account
    was already limited to :data:`PUBLIC_COMMANDS`, and a Guest is consumed by
    the guest router. Unknown commands pass too - nothing would answer them.
    """

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        actor: Actor | None = data.get("actor")
        if actor is None or has_full_bot(actor.role) or not isinstance(event, Message):
            return await handler(event, data)

        command = _command_for_this_bot(event)
        if command is None or command not in OWNER_ONLY_COMMANDS:
            return await handler(event, data)

        logger.info(
            "command_refused_owner_only",
            extra={"command": command, "telegram_user_id": actor.telegram_user_id},
        )
        await formatting.answer(event, formatting.escape(owner_only_command_reply(actor.role)))
        return None
