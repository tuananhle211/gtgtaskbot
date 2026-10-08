"""Regression tests for ``/help`` and the command registry.

The bug these exist for: ``/help`` sent a Markdown constant containing seven
underscores (``/script_types``, ``/add_sheet``, ``/sync_sheets``,
``/cancel_flow``, ``/pending_scripts``, ``/review_script``, ``/create_invite``).
An odd number of ``_`` is an unterminated italic entity, Telegram answered
``400 Can't find end of Italic entity``, and the user saw nothing at all.

So these tests check three separate things, because fixing only one would let
the bug come back in a different shape:

1. the *text* is valid for the parse mode we send it in;
2. the *routing* actually reaches the handler - in a clean chat and in the
   middle of an FSM flow;
3. the *registry* stays the single source for help, the Telegram menu and the
   handlers, so a new command cannot be added to only one of them.
"""

from __future__ import annotations

import re

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest

from meobot.bot import formatting
from meobot.bot.commands import (
    BY_NAME,
    COMMANDS,
    CommandSpec,
    commands_for,
    handler_commands,
    public_commands,
    render_help,
    telegram_commands,
)
from meobot.bot.handlers import (
    access_router,
    chat_router,
    commands_router,
    drive_router,
    guest_router,
    invites_router,
    people_router,
    scripts_router,
    sheets_router,
)
from meobot.bot.handlers.sheets import AddSheet
from meobot.bot.main import publish_command_menu
from meobot.domain.identity.models import Role
from tests.fakes import RecordingSession, SqliteDatabase, make_update

#: Every router that registers a slash command.
COMMAND_ROUTERS = (
    guest_router,
    commands_router,
    chat_router,
    invites_router,
    sheets_router,
    drive_router,
    scripts_router,
    people_router,
    access_router,
)

#: Telegram's HTML subset. Anything else in a rendered message is a bug.
_ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}
_TAG_PATTERN = re.compile(r"<\s*/?\s*([a-zA-Z-]+)")


# --- 1. The text is valid ---------------------------------------------------
@pytest.mark.parametrize("role", list(Role))
def test_help_uses_only_telegram_supported_tags(role: Role) -> None:
    """Every tag in the rendered help is one Telegram actually parses."""
    for tag in _TAG_PATTERN.findall(render_help(role)):
        assert tag.lower() in _ALLOWED_TAGS, f"unsupported tag <{tag}> for role {role}"


@pytest.mark.parametrize("role", list(Role))
def test_help_html_tags_are_balanced(role: Role) -> None:
    """The exact failure mode of the old Markdown help, checked for HTML.

    An unbalanced entity is what made Telegram reject the message; counting
    opens against closes catches it before a user ever sees the silence.
    """
    body = render_help(role)
    for tag in sorted(_ALLOWED_TAGS):
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", body))
        closes = len(re.findall(rf"</{tag}>", body))
        assert opens == closes, f"<{tag}> is unbalanced in help for {role}"


def test_help_is_not_sent_in_a_markdown_parse_mode() -> None:
    """The original bug, stated directly.

    Command names contain ``_``, which legacy Markdown reads as an italic
    delimiter. The help lists many of them and cannot control whether the count
    is even, so the only safe answer is not to use Markdown at all - and to
    leave the underscores unescaped, which HTML allows.
    """
    body = render_help(Role.OWNER)
    assert formatting.PARSE_MODE == "HTML"
    assert body.count("_") >= 7, "the help no longer lists the commands it used to"
    assert "/script_types" in body
    assert "\\_" not in body, "escaping underscores would be the wrong fix"


def test_help_survives_an_odd_number_of_underscores() -> None:
    """The exact input that used to break, rendered and split without loss.

    ``/script_types``, ``/add_sheet``, ``/sync_sheets``, ``/cancel_flow``,
    ``/pending_scripts``, ``/review_script`` and ``/create_invite`` are seven
    underscores. Under Markdown that is an unterminated italic entity; under
    HTML it is seven ordinary characters.
    """
    hazard = "\n".join(
        (
            "/script_types",
            "/add_sheet",
            "/sync_sheets",
            "/cancel_flow",
            "/pending_scripts",
            "/review_script",
            "/create_invite",
        )
    )
    assert hazard.count("_") % 2 == 1
    rendered = formatting.escape(hazard)
    assert rendered == hazard
    assert formatting.split_message(rendered) == [hazard]


def test_help_stays_within_telegram_limits_after_splitting() -> None:
    """A long help is split, never truncated."""
    chunks = formatting.split_message(render_help(Role.OWNER))
    assert chunks
    assert all(len(chunk) <= formatting.SAFE_CHUNK_LENGTH for chunk in chunks)
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_split_message_never_loses_content() -> None:
    """Splitting is lossless: every line survives somewhere."""
    body = "\n".join(f"dòng số {index} — nội dung tiếng Việt có dấu" for index in range(400))
    chunks = formatting.split_message(body)
    assert len(chunks) > 1
    rejoined = "\n".join(chunks)
    for index in (0, 199, 399):
        assert f"dòng số {index} " in rejoined


def test_escape_preserves_vietnamese_and_neutralises_markup() -> None:
    """Diacritics pass through; the four HTML specials do not."""
    assert formatting.escape("Kịch bản bác sĩ Tiến") == "Kịch bản bác sĩ Tiến"
    assert formatting.escape("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"
    # An underscore is not an HTML entity, so command names survive intact.
    assert formatting.escape("/create_script_sheet") == "/create_script_sheet"


# --- 2. The routing reaches the handler ------------------------------------
async def test_help_answers_in_a_normal_chat(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The whole path: update -> middleware -> router -> a visible reply."""
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("/help"))

    assert session.sent_texts(), "/help produced no outgoing message at all"
    assert "TasksBot" in session.combined_text()
    assert "/sheets" in session.combined_text()


async def test_help_answers_while_an_fsm_flow_is_active(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """``/help`` must work in the middle of ``/add_sheet``.

    The sheets router has a ``waiting_for_url`` handler matching any text. If
    ``/help`` were routed to that handler the Owner would get "Không nhận ra
    link" instead of the help - so this drives the real FSM state and checks
    which handler actually answered.
    """
    bot, session = bot_and_session
    key = _storage_key(bot)
    await dispatcher.storage.set_state(key, AddSheet.waiting_for_url.state)

    await dispatcher.feed_update(bot, make_update("/help", update_id=2, message_id=2))

    reply = session.combined_text()
    assert "TasksBot" in reply
    assert "Không nhận ra link" not in reply
    # The flow is untouched: /help answers, it does not cancel your work.
    assert await dispatcher.storage.get_state(key) == AddSheet.waiting_for_url.state


async def test_cancel_flow_works_from_inside_a_flow(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The other global command: it must escape any state."""
    bot, session = bot_and_session
    key = _storage_key(bot)
    await dispatcher.storage.set_state(key, AddSheet.waiting_for_worksheet.state)

    await dispatcher.feed_update(bot, make_update("/cancel_flow", update_id=3, message_id=3))

    assert "Đã huỷ" in session.combined_text()
    assert await dispatcher.storage.get_state(key) is None


async def test_slash_command_is_not_swallowed_by_the_free_text_handler(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The catch-all must never see a command.

    ``/capabilities`` answers from configuration; the conversation handler
    would answer something else entirely. Getting the capability sections back
    proves which one ran.
    """
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("/capabilities", update_id=4, message_id=4))

    reply = session.combined_text()
    assert "Dùng được ngay" in reply or "Chưa xây dựng" in reply


async def test_natural_message_still_reaches_the_conversation_handler(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The complement of the previous test: non-commands do get through."""
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("Bạn làm được gì?", update_id=5, message_id=5))

    assert session.sent_texts()
    assert "Sheet" in session.combined_text() or "kịch bản" in session.combined_text()


async def test_unregistered_user_gets_the_public_help_only(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """An unknown account is answered, told it is unknown, and shown /join.

    ``/help`` is public in the registry, so it reaches its handler with no
    actor bound. It must not leak the operational commands that account cannot
    use.
    """
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot, make_update("/help", user_id=424242, update_id=6, message_id=6)
    )

    reply = session.combined_text()
    assert "chưa được đăng ký" in reply
    assert "/join" in reply
    assert "/sheets" not in reply


async def test_unregistered_user_is_refused_from_operational_commands(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """A non-public command never reaches its handler for an unknown account."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot, make_update("/sheets", user_id=424243, update_id=7, message_id=7)
    )

    reply = session.combined_text()
    assert "chưa được đăng ký" in reply
    assert "Google Sheet đã cấu hình" not in reply


async def test_no_traceback_reaches_the_user_when_telegram_refuses_markup(
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """A rejected parse mode degrades to plain text, it does not vanish.

    This is the safety net under the original bug: even if some future message
    contains markup Telegram dislikes, the user still receives the content.
    """
    bot, session = bot_and_session
    message = make_update("x").message
    assert message is not None
    message._bot = bot

    session.fail_on["SendMessage"] = TelegramBadRequest(
        method=None,  # type: ignore[arg-type]
        message="Bad Request: can't parse entities",
    )
    await formatting.answer(message, "<b>Kết quả</b> quan trọng")

    texts = session.sent_texts()
    assert len(texts) == 2, "the message was not retried as plain text"
    assert "Kết quả quan trọng" in texts[1]
    assert "Traceback" not in texts[1]


# --- 3. The registry is the single source ----------------------------------
def test_every_registered_command_has_a_handler() -> None:
    """A spec without a handler would be a command that silently does nothing."""
    registered = handler_commands(COMMAND_ROUTERS)
    declared = {spec.command for spec in COMMANDS}
    missing = declared - registered
    assert not missing, f"declared in the registry but no handler: {sorted(missing)}"


def test_every_handler_command_is_declared() -> None:
    """A handler without a spec would be missing from /help and the menu.

    That is exactly how the old help text drifted out of date.
    """
    registered = handler_commands(COMMAND_ROUTERS)
    declared = {spec.command for spec in COMMANDS}
    undeclared = registered - declared
    assert not undeclared, f"handled but missing from the registry: {sorted(undeclared)}"


@pytest.mark.parametrize(
    "command",
    [
        "start",
        "help",
        "health",
        "script_types",
        "sheets",
        "add_sheet",
        "sync_sheets",
        "cancel_flow",
        "pending_scripts",
        "script",
        "review_script",
        "create_invite",
        "add_user",
        "join",
        "new_chat",
        "clear_chat",
        "chat_status",
        "capabilities",
        "drive_status",
        "sheet_templates",
        "drive_folders",
        "add_drive_folder",
        "create_work_sheet",
        "create_script_sheet",
        "created_sheets",
    ],
)
def test_documented_command_is_registered_and_handled(command: str) -> None:
    """Every command this release documents exists in both places."""
    assert command in BY_NAME
    assert command in handler_commands(COMMAND_ROUTERS)


def test_help_lists_only_commands_the_role_may_use() -> None:
    """An employee is not shown commands they would only be refused."""
    employee_help = render_help(Role.EMPLOYEE)
    owner_help = render_help(Role.OWNER)

    assert "/create_invite" in owner_help
    assert "/create_invite" not in employee_help
    assert "/add_drive_folder" in owner_help
    assert "/add_drive_folder" not in employee_help
    # ...but the universal ones are there for everybody.
    for command in ("/help", "/start", "/cancel_flow"):
        assert command in employee_help


def test_unregistered_help_offers_only_public_commands() -> None:
    """Somebody with no account is shown the two commands that work for them."""
    body = render_help(Role.EMPLOYEE, unregistered=True)
    assert "/join" in body
    assert "/sheets" not in body


def test_public_commands_come_from_the_registry() -> None:
    """The middleware's allow-list is derived, not duplicated."""
    from meobot.bot.middlewares import PUBLIC_COMMANDS

    assert "/join" in public_commands()
    assert "/join" in PUBLIC_COMMANDS
    # /start is public in the registry but must still be refused for an
    # unregistered account - its handler assumes a bound actor.
    assert "/start" not in PUBLIC_COMMANDS


def test_telegram_menu_is_built_from_the_registry() -> None:
    """setMyCommands and /help cannot disagree - same source."""
    menu = telegram_commands()
    assert menu, "the command menu is empty"

    names = [name for name, _ in menu]
    assert names == [spec.command for spec in commands_for(Role.EMPLOYEE)]
    # Telegram caps a description at 256 characters and rejects the whole call
    # if one is longer.
    assert all(1 <= len(description) <= 256 for _, description in menu)
    # The default menu is built for the least privileged role.
    assert "create_invite" not in names


async def test_set_my_commands_failure_does_not_stop_the_bot(
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """A Telegram hiccup must not turn into an outage.

    The menu is cosmetic; refusing to start without it would trade a missing
    menu for a bot that is down.
    """
    bot, session = bot_and_session
    session.fail_on["SetMyCommands"] = TelegramBadRequest(
        method=None,  # type: ignore[arg-type]
        message="Too Many Requests",
    )

    published = await publish_command_menu(bot)

    assert published is False
    assert session.sent_of("SetMyCommands"), "it did not even try"


async def test_set_my_commands_publishes_every_visible_command(
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The happy path actually sends the registry's commands."""
    bot, session = bot_and_session
    assert await publish_command_menu(bot) is True

    calls = session.sent_of("SetMyCommands")
    assert len(calls) == 1
    published = {command.command for command in calls[0].commands}  # type: ignore[attr-defined]
    assert published == {name for name, _ in telegram_commands()}
    assert "help" in published


@pytest.mark.parametrize(
    "command",
    ["sync_sheets", "script", "review_script", "create_invite", "join", "confirm", "cancel"],
)
def test_commands_with_arguments_document_their_usage(command: str) -> None:
    """A command invoked without arguments has something friendly to say."""
    spec: CommandSpec = BY_NAME[command]
    usage = spec.usage_text()
    assert spec.slash in usage
    assert usage.startswith("Cú pháp:")


def test_argument_commands_carry_a_concrete_example() -> None:
    """ "Cú pháp: /script <mã>" helps less than seeing a real one."""
    for name in ("script", "review_script", "join", "confirm"):
        assert BY_NAME[name].example, f"/{name} has no example"


def test_every_spec_has_a_usage_and_description() -> None:
    """Empty metadata would produce an empty help line or a rejected menu."""
    for spec in COMMANDS:
        assert spec.description.strip(), f"/{spec.command} has no description"
        assert spec.usage.startswith(f"/{spec.command}"), f"/{spec.command} usage is wrong"


def _storage_key(bot: Bot, *, chat_id: int = 555, user_id: int = 777000111):  # type: ignore[no-untyped-def]
    from aiogram.fsm.storage.base import StorageKey

    return StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=user_id)
