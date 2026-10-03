"""Routing by intent, as an aiogram filter rather than an early return.

This exists because of a bug worth remembering. The Member routers first tried
to "fall through" by matching every text message and returning early when the
intent was not theirs. In aiogram 3 that does not fall through: a handler whose
filters matched has *handled* the update, so returning ``None`` stops
propagation and ordinary conversation was silently swallowed.

A filter is the honest mechanism. When the intent is not one this router owns,
the filter returns ``False``, the handler never runs, and the update carries on
to the conversation router exactly as it did before.

The filter also injects what it worked out, so the handler does not classify the
message a second time.
"""

from __future__ import annotations

from collections.abc import Container

from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from meobot.bot.addressing import bot_username_of, is_addressed_to_bot, strip_bot_mention
from meobot.core.config import Settings
from meobot.domain.member.intents import MemberIntent, classify


class MemberIntentFilter(Filter):
    """Matches when a message means one of ``intents``.

    Injects ``member_intent`` and ``member_text`` (the message with MeoBot's own
    ``@mention`` stripped) into the handler.
    """

    def __init__(self, intents: Container[MemberIntent], *, allow_in_flow: bool = False) -> None:
        self._intents = intents
        self._allow_in_flow = allow_in_flow

    async def __call__(
        self,
        message: Message,
        settings: Settings | None = None,
        state: FSMContext | None = None,
        **_: object,
    ) -> bool | dict[str, object]:
        if settings is None or not message.text:
            return False
        if not is_addressed_to_bot(message, require_mention=settings.chat_group_requires_mention):
            return False
        if not self._allow_in_flow and state is not None and await state.get_state() is not None:
            # A guided flow owns this turn. Intercepting it here would answer
            # the wrong question and abandon the flow half-finished.
            return False

        text = strip_bot_mention(message.text, bot_username_of(message)).strip()
        if not text:
            return False

        match = classify(
            text, normalization_enabled=settings.member_vietnamese_normalization_enabled
        )
        if match.intent not in self._intents:
            return False
        return {"member_intent": match.intent, "member_text": text}


class InMemberFlow(Filter):
    """Matches while one of ``states`` is active.

    Scoped to explicit state names rather than "any state at all", so an
    ``/add_sheet`` in progress is never captured by an HR handler.
    """

    def __init__(self, *states: str) -> None:
        self._states = frozenset(states)

    async def __call__(
        self, message: Message, state: FSMContext | None = None, **_: object
    ) -> bool:
        if state is None:
            return False
        current = await state.get_state()
        return current is not None and current in self._states
