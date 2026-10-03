"""Bot entry point: build collaborators, register handlers, long-poll.

The bot container is the only one that requires ``TELEGRAM_BOT_TOKEN``. Without
it this process exits with a clear error while API, worker and beat keep
running. Missing Google credentials are *not* a startup failure: the Drive and
Sheets clients degrade to placeholders that fail loudly only when used.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from meobot.application.conversation_service import ConversationService
from meobot.application.health_service import HealthService
from meobot.bot.commands import telegram_commands
from meobot.bot.handlers import (
    access_router,
    chat_router,
    commands_router,
    conversation_router,
    dispatch_router,
    drive_router,
    group_admin_router,
    guest_router,
    hr_router,
    invites_router,
    member_router,
    notifications_router,
    people_router,
    read_receipts_router,
    reminders_router,
    scripts_router,
    sheets_router,
    web_router,
)
from meobot.bot.middlewares import (
    AccessGateMiddleware,
    ActorMiddleware,
    DeduplicationMiddleware,
    RequestContextMiddleware,
)
from meobot.bot.storage import PostgresStorage
from meobot.core.config import Settings, get_settings
from meobot.core.errors import ConfigurationError
from meobot.core.logging import configure_logging, get_logger
from meobot.db.session import Database
from meobot.domain.policy.engine import PolicyEngine
from meobot.integrations.google.factory import build_drive_client, build_sheets_client
from meobot.integrations.llm.factory import build_llm_provider
from meobot.tools.registry import build_default_registry

logger = get_logger(__name__)


def build_dispatcher(settings: Settings, database: Database) -> Dispatcher:
    """Wire middlewares, routers and injected collaborators.

    Collaborators passed to :class:`Dispatcher` are injected into handlers by
    parameter name - that is aiogram's dependency injection, and the reason no
    handler reaches for a global.

    Call this once per process: the routers are module-level singletons and
    aiogram refuses to attach a router to a second Dispatcher.
    """
    health_service = HealthService(database, settings)
    llm = build_llm_provider(settings)
    sheets_client = build_sheets_client(settings)
    drive_client = build_drive_client(settings)
    registry = build_default_registry(
        health_service=health_service,
        sheets=sheets_client,
        llm=llm,
        drive=drive_client,
    )
    policy = PolicyEngine(registry.policies())

    conversation_service = ConversationService(
        llm=llm,
        registry=registry,
        policy=policy,
        database=database,
        settings=settings,
    )

    dispatcher = Dispatcher(
        # Conversation state lives in PostgreSQL, so an /add_sheet or a
        # /create_script_sheet in progress survives a bot restart.
        storage=PostgresStorage(database, ttl_seconds=settings.conversation_ttl_seconds),
        settings=settings,
        database=database,
        health_service=health_service,
        conversation_service=conversation_service,
        tool_registry=registry,
        sheets_client=sheets_client,
        drive_client=drive_client,
        llm_provider=llm,
    )
    # RequestContextMiddleware wraps every update kind so any log line can be
    # correlated. ActorMiddleware is an outer middleware on messages and
    # callback queries: it must see the event (to be able to reply) and it must
    # run even when no handler matches, so unregistered users always get an answer.
    # Deduplication runs first: a redelivered update must not even get a
    # correlation id, let alone reach a handler and answer twice.
    dispatcher.update.outer_middleware(DeduplicationMiddleware())
    dispatcher.update.outer_middleware(RequestContextMiddleware())
    # The access gate runs before the actor requirement: "may MeoBot respond to
    # this at all" is a different question from "is this a registered user",
    # and a Guest is the case where the two answers differ.
    dispatcher.message.outer_middleware(AccessGateMiddleware(database, settings))
    actor_middleware = ActorMiddleware(database, settings)
    dispatcher.message.outer_middleware(actor_middleware)
    dispatcher.callback_query.outer_middleware(actor_middleware)
    # First: a Guest\'s update is consumed here and never offered to a handler
    # that expects an Actor.
    dispatcher.include_router(guest_router)
    dispatcher.include_router(commands_router)
    # Second, and before everything that classifies text afresh: while somebody
    # has an open multi-group announcement draft, that draft owns their turn.
    # This is the ordering the 0.6.0a3 defects came down to - "Tất cả" and
    # "Xác nhận" were being re-classified as conversation, so MeoBot asked
    # again for what the draft was already holding. A router placed after the
    # Member or conversation routers cannot fix that, however good its filters.
    dispatcher.include_router(dispatch_router)
    dispatcher.include_router(chat_router)
    dispatcher.include_router(invites_router)
    dispatcher.include_router(sheets_router)
    dispatcher.include_router(drive_router)
    dispatcher.include_router(scripts_router)
    dispatcher.include_router(people_router)
    dispatcher.include_router(access_router)
    # ``/web`` is an exact command with no free-text fallback, so it can sit
    # anywhere ahead of the conversation router. Here, next to the other
    # explicit-command routers.
    dispatcher.include_router(web_router)
    # The Vietnamese interaction layer: the HR flow first (it owns any turn
    # with an active flow), then the general Member router. Both answer only
    # what they recognise deterministically and let everything else fall
    # through to ordinary conversation - which is the only metered branch.
    # Cross-chat routing first: registering a group and writing an
    # announcement are specific intents, and neither should be reachable by
    # the broader Member router.
    dispatcher.include_router(notifications_router)
    dispatcher.include_router(read_receipts_router)
    dispatcher.include_router(group_admin_router)
    # Reminders before HR: "nhắc" is a common word, and the reminder patterns
    # are the most specific reading of it.
    dispatcher.include_router(reminders_router)
    dispatcher.include_router(hr_router)
    dispatcher.include_router(member_router)
    # Last: it matches any non-command text, so every command router must have
    # had its chance first.
    dispatcher.include_router(conversation_router)
    return dispatcher


async def publish_command_menu(bot: Bot) -> bool:
    """Publish the Telegram command menu from the command registry.

    Built for the least privileged role, because Telegram's default menu is
    global: advertising ``/create_invite`` to an employee who would only be
    refused is worse than omitting it.

    Returns True when Telegram accepted the menu. A failure here is logged and
    swallowed - a missing menu is a cosmetic problem, and refusing to start the
    bot over it would turn a Telegram hiccup into an outage.
    """
    commands = [
        BotCommand(command=name, description=description[:256])
        for name, description in telegram_commands()
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as exc:
        logger.warning(
            "set_my_commands_failed",
            extra={"error": type(exc).__name__, "command_count": len(commands)},
        )
        return False
    logger.info("set_my_commands_published", extra={"command_count": len(commands)})
    return True


async def run_bot(settings: Settings | None = None) -> None:
    """Start long polling until cancelled.

    Raises:
        ConfigurationError: When ``TELEGRAM_BOT_TOKEN`` is not set.
    """
    resolved = settings or get_settings()
    configure_logging(
        service="bot",
        level=resolved.log_level,
        log_format=resolved.log_format,
    )

    if resolved.telegram_bot_token is None:
        raise ConfigurationError(
            "TELEGRAM_BOT_TOKEN is required to start the bot container. "
            "Set it in .env, or stop the 'bot' service until you have a token."
        )
    if resolved.meobot_owner_telegram_id is None:
        logger.warning(
            "owner_not_configured",
            extra={"hint": "Set MEOBOT_OWNER_TELEGRAM_ID so the owner can use the bot."},
        )
    if not resolved.google_enabled:
        logger.info(
            "google_not_configured",
            extra={"hint": "Sheet and Drive commands will report a configuration error."},
        )

    database = Database(resolved)
    bot = Bot(token=resolved.telegram_bot_token.get_secret_value())
    dispatcher = build_dispatcher(resolved, database)

    logger.info("bot_starting", extra={"app_env": resolved.app_env})
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await publish_command_menu(bot)
        await dispatcher.start_polling(bot, handle_signals=True)
    finally:
        await bot.session.close()
        await database.dispose()
        logger.info("bot_stopped")


def run() -> None:
    """Console-script entry point (``meobot-bot``)."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    run()
