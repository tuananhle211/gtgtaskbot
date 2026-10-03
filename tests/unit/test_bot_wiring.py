"""Bot assembly: middleware placement, router registration, startup guards."""

from __future__ import annotations

import pytest
from aiogram import Dispatcher

from meobot.bot.main import run_bot
from meobot.bot.middlewares import (
    AccessGateMiddleware,
    ActorMiddleware,
    RequestContextMiddleware,
)
from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError

# The ``dispatcher`` fixture lives in conftest: routers are module-level
# singletons, so every test module has to share one Dispatcher instance.


def test_request_context_middleware_wraps_every_update(dispatcher: Dispatcher) -> None:
    installed = [type(mw) for mw in dispatcher.update.outer_middleware]
    assert RequestContextMiddleware in installed


def test_actor_middleware_sits_on_the_message_observer(dispatcher: Dispatcher) -> None:
    """It must see a Message so it can reply, and must run before any handler.

    Registering it on the ``update`` observer would hand it an
    :class:`~aiogram.types.Update` instead, and unregistered users would
    silently get no answer at all.
    """
    assert ActorMiddleware in [type(mw) for mw in dispatcher.message.outer_middleware]
    assert ActorMiddleware not in [type(mw) for mw in dispatcher.update.outer_middleware]
    assert dispatcher.message.event_name == "message"


def test_actor_middleware_is_outer_not_inner(dispatcher: Dispatcher) -> None:
    """Inner middleware only runs when a handler matches - too late to reject."""
    assert ActorMiddleware not in [type(mw) for mw in dispatcher.message.middleware]


def test_routers_are_registered(dispatcher: Dispatcher) -> None:
    names = [router.name for router in dispatcher.sub_routers]
    assert set(names) == {
        "guest",
        "commands",
        "dispatch",
        "chat",
        "invites",
        "sheets",
        "drive",
        "scripts",
        "people",
        # Step 1E: ``/web`` issues the PR admin login link. An exact command
        # with no free-text fallback, so its position only has to be ahead of
        # the conversation router.
        "web",
        "access",
        "notifications",
        "read_receipts",
        "group_admin",
        "reminders",
        "hr",
        "member",
        "conversation",
    }
    # The free-text router matches any non-command message, so it must be last.
    assert names[-1] == "conversation"
    # The guest router must be first: it consumes everything the access gate
    # authorised as a Guest, so a Guest can never reach a handler that expects
    # an Actor. Registering it later would let a command router see them first.
    assert names[0] == "guest"
    # ...and the command router next, so /help is never reached by a
    # sheets- or drive-flow state handler before its own handler runs.
    assert names[1] == "commands"
    # Then the multi-group dispatch router, which owns every turn while
    # somebody has an announcement draft open. Anything registered before it
    # would re-classify "Tất cả" and "Xác nhận" as fresh requests - which is
    # exactly the 0.6.0a3 defect - and anything it is registered after would
    # get to answer them first.
    assert names[2] == "dispatch"


def test_access_gate_runs_before_the_actor_requirement(dispatcher: Dispatcher) -> None:
    """Order is the whole design: "may we answer" precedes "are they a user".

    Reversed, ActorMiddleware would reject an unknown stranger before the gate
    could open an approval request, and a Guest could never be answered.
    """
    installed = [type(mw) for mw in dispatcher.message.outer_middleware]
    assert AccessGateMiddleware in installed
    assert ActorMiddleware in installed
    assert installed.index(AccessGateMiddleware) < installed.index(ActorMiddleware)


def test_actor_middleware_also_guards_callback_queries(dispatcher: Dispatcher) -> None:
    """Inline buttons are an execution surface too, not just messages."""
    assert ActorMiddleware in [type(mw) for mw in dispatcher.callback_query.outer_middleware]


def test_collaborators_are_injected(dispatcher: Dispatcher) -> None:
    """Handlers receive dependencies by name - no globals."""
    for key in (
        "settings",
        "database",
        "health_service",
        "conversation_service",
        "tool_registry",
        "sheets_client",
        "drive_client",
        "llm_provider",
    ):
        assert key in dispatcher.workflow_data


async def test_bot_refuses_to_start_without_a_token(settings: Settings) -> None:
    """The bot container fails loudly; api/worker/beat are unaffected."""
    assert settings.telegram_bot_token is None
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        await run_bot(settings)
