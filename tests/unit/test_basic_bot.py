"""The basic bot: on Telegram only the owner gets everything.

Nhân viên, Trưởng nhóm and Quản trị viên keep the basic commands, their
notifications, HR requests and reminders, and work on the web. Everything else -
AI chat and every tool behind it, sheets, drive, scripts, people and quota
commands - is the owner's. These drive the real Dispatcher, so what they assert
is which handler ran, or that none did.
"""

from __future__ import annotations

import re

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommandScopeChat
from sqlalchemy import select

from meobot.bot.commands import (
    BY_NAME,
    COMMANDS,
    commands_for,
    has_full_bot,
    owner_only_command_reply,
    owner_only_commands,
    render_help,
    telegram_commands,
)
from meobot.bot.main import publish_command_menu
from meobot.bot.texts import BASIC_ONLY_CHAT
from meobot.db.models.quota import DailyAiUsage
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Role
from meobot.domain.member import copy
from tests.fakes import RecordingSession, SqliteDatabase, make_group_update, make_update
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

BASE = 1_700_000
PERSON = 1_700_001
GROUP = -1_700_001

NON_OWNERS = (Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN)

#: What a non-owner keeps, by name. Spelled out so a change to the rule has to
#: change this test too.
BASIC = {"start", "help", "whoami", "web", "join", "cancel", "cancel_flow", "create_invite"}


def uid(offset: int) -> int:
    return BASE + offset


async def add_person(database: SqliteDatabase, role: Role) -> None:
    async with database.transaction() as session:
        session.add(
            User(
                telegram_user_id=PERSON,
                telegram_username="nguoi",
                full_name="Trần Văn Nam",
                role=role,
                active=True,
                status=UserStatus.ACTIVE,
            )
        )


def listed_commands(help_body: str) -> set[str]:
    """Command names ``/help`` lists - the first token of each usage line."""
    return set(re.findall(r"^/(\w+)", help_body, flags=re.MULTILINE))


# --- The registry -----------------------------------------------------------
def test_only_the_owner_has_the_full_bot() -> None:
    assert has_full_bot(Role.OWNER)
    for role in NON_OWNERS:
        assert not has_full_bot(role)


def test_the_basic_commands_are_exactly_these() -> None:
    assert {spec.command for spec in COMMANDS if spec.basic} == BASIC
    assert owner_only_commands() == {spec.slash for spec in COMMANDS if spec.command not in BASIC}


@pytest.mark.parametrize("role", NON_OWNERS)
def test_a_non_owner_help_lists_only_basic_commands(role: Role) -> None:
    body = render_help(role)
    listed = listed_commands(body)

    assert listed <= BASIC
    assert {"start", "help", "whoami", "web"} <= listed
    for command in ("sheets", "new_chat", "user_info", "quota", "confirm", "health"):
        assert command not in listed
    # The help no longer promises an AI chat, and points at the web instead.
    assert "brainstorm" not in body
    assert "/web" in body


def test_create_invite_is_listed_where_its_handler_may_allow_it() -> None:
    assert "create_invite" not in listed_commands(render_help(Role.EMPLOYEE))
    assert "create_invite" in listed_commands(render_help(Role.TEAM_LEAD))
    assert "create_invite" in listed_commands(render_help(Role.ADMIN))


def test_the_owner_help_still_lists_everything() -> None:
    assert listed_commands(render_help(Role.OWNER)) == {spec.command for spec in COMMANDS}
    assert [spec.command for spec in commands_for(Role.OWNER)] == [s.command for s in COMMANDS]


def test_the_refusal_names_what_the_person_can_use() -> None:
    employee = owner_only_command_reply(Role.EMPLOYEE)
    assert employee == (
        "Lệnh này chỉ dành cho chủ sở hữu. Bạn dùng được: /start, /help, /whoami, /web."
    )
    assert owner_only_command_reply(Role.ADMIN).endswith("/web, /create_invite.")


def test_the_default_menu_is_basic() -> None:
    names = {name for name, _ in telegram_commands()}
    assert names <= BASIC
    assert "web" in names
    assert "sheets" not in names
    assert "create_invite" not in names


# --- Commands through the real Dispatcher ------------------------------------
@pytest.mark.parametrize(
    ("role", "command", "handler_marker", "offset"),
    [
        (Role.EMPLOYEE, "/sheets", "Google Sheet", 1),
        (Role.EMPLOYEE, "/new_chat", "cuộc trò chuyện", 2),
        (Role.EMPLOYEE, "/user_info", "reply", 3),
        (Role.ADMIN, "/sheets", "Google Sheet", 4),
        (Role.ADMIN, "/new_chat", "cuộc trò chuyện", 5),
        (Role.ADMIN, "/user_info", "reply", 6),
        (Role.TEAM_LEAD, "/confirm 8f3a21", "xác nhận", 7),
        (Role.EMPLOYEE, "/quota", "lượt", 8),
    ],
)
async def test_a_non_owner_is_refused_owner_only_commands(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
    role: Role,
    command: str,
    handler_marker: str,
    offset: int,
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, role)

    await dispatcher.feed_update(
        bot, make_update(command, user_id=PERSON, chat_id=PERSON, update_id=uid(offset))
    )

    # One reply, and it is the refusal: the handler never ran.
    assert session.sent_texts() == [owner_only_command_reply(role)]
    assert handler_marker not in session.combined_text()
    assert counting_llm.chat_calls == 0


async def test_a_command_for_another_bot_is_not_ours_to_refuse(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.EMPLOYEE)

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/sheets@SomeOtherBot", user_id=PERSON, chat_id=GROUP, update_id=uid(10), mention=False
        ),
    )

    assert session.sent_texts() == []


@pytest.mark.parametrize(
    ("command", "marker", "offset"),
    [
        ("/help", "/whoami", 20),
        ("/whoami", "TasksBot đang nói chuyện với", 21),
        ("/cancel_flow", "thao tác", 22),
        # Reaches its handler, which refuses only because the test deployment
        # has no WEB_BASE_URL.
        ("/web", "Web admin", 23),
    ],
)
async def test_basic_commands_still_work_for_a_member(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    command: str,
    marker: str,
    offset: int,
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.EMPLOYEE)

    await dispatcher.feed_update(
        bot, make_update(command, user_id=PERSON, chat_id=PERSON, update_id=uid(offset))
    )

    reply = session.combined_text()
    assert marker.lower() in reply.lower()
    assert "chỉ dành cho chủ sở hữu" not in reply


async def test_a_member_help_over_telegram_is_the_basic_list(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.EMPLOYEE)

    await dispatcher.feed_update(
        bot, make_update("/help", user_id=PERSON, chat_id=PERSON, update_id=uid(24))
    )

    assert listed_commands(session.combined_text()) <= BASIC
    assert "/sheets" not in session.combined_text()


async def test_the_owner_still_runs_owner_commands(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.OWNER)

    await dispatcher.feed_update(
        bot, make_update("/help", user_id=PERSON, chat_id=PERSON, update_id=uid(30))
    )
    assert "/sheets" in session.combined_text()

    session.requests.clear()
    await dispatcher.feed_update(
        bot, make_update("/sheets", user_id=PERSON, chat_id=PERSON, update_id=uid(31))
    )
    assert "chỉ dành cho chủ sở hữu" not in session.combined_text()
    assert session.sent_texts()


# --- Free text --------------------------------------------------------------
@pytest.mark.parametrize(("role", "offset"), [(Role.EMPLOYEE, 40), (Role.ADMIN, 41)])
async def test_a_non_owners_free_text_never_reaches_the_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
    role: Role,
    offset: int,
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, role)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Viết cho mình 3 ý tưởng video tuần này",
            user_id=PERSON,
            chat_id=PERSON,
            update_id=uid(offset),
        ),
    )

    assert counting_llm.chat_calls == 0
    assert session.sent_texts() == [BASIC_ONLY_CHAT]
    async with bot_database.session() as active:
        rows = (await active.execute(select(DailyAiUsage))).scalars().all()
    assert all(row.used_count == 0 and row.reserved_count == 0 for row in rows)


async def test_a_non_owner_in_a_group_is_answered_only_when_addressed(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.EMPLOYEE)

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "chiều nay họp mấy giờ nhỉ",
            user_id=PERSON,
            chat_id=GROUP,
            update_id=uid(50),
            mention=False,
        ),
    )
    assert session.sent_texts() == []

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "viết giúp mình caption", user_id=PERSON, chat_id=GROUP, update_id=uid(51)
        ),
    )
    assert session.sent_texts() == [BASIC_ONLY_CHAT]
    assert counting_llm.chat_calls == 0


async def test_the_owners_free_text_still_reaches_the_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.OWNER)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Viết cho mình 3 ý tưởng video tuần này",
            user_id=PERSON,
            chat_id=PERSON,
            update_id=uid(60),
        ),
    )

    assert counting_llm.chat_calls == 1
    assert BASIC_ONLY_CHAT not in session.sent_texts()


async def test_hr_requests_still_work_for_a_member(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.EMPLOYEE)

    await dispatcher.feed_update(
        bot, make_update("Thống kê nghỉ phép của tôi", user_id=PERSON, update_id=uid(70))
    )

    assert "THỐNG KÊ CỦA BẠN" in session.combined_text()
    assert counting_llm.chat_calls == 0


# --- The home card ----------------------------------------------------------
@pytest.mark.parametrize(("role", "offset"), [(Role.EMPLOYEE, 80), (Role.ADMIN, 81)])
async def test_a_non_owner_home_has_no_quota_or_chat_button(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    role: Role,
    offset: int,
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, role)

    await dispatcher.feed_update(
        bot, make_update("Bắt đầu", user_id=PERSON, chat_id=PERSON, update_id=uid(offset))
    )

    markup = str(session.sent_of("SendMessage")[0].reply_markup)
    assert copy.Button.MY_WORK.value in markup
    assert copy.Button.ASK_LEAVE.value in markup
    assert copy.Button.MY_AI_ALLOWANCE.value not in markup
    assert copy.Button.ASK_MEOBOT.value not in markup


async def test_the_owner_home_keeps_the_chat_buttons(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_person(bot_database, Role.OWNER)

    await dispatcher.feed_update(
        bot, make_update("Bắt đầu", user_id=PERSON, chat_id=PERSON, update_id=uid(82))
    )

    markup = str(session.sent_of("SendMessage")[0].reply_markup)
    assert copy.Button.MY_AI_ALLOWANCE.value in markup
    assert copy.Button.ASK_MEOBOT.value in markup


# --- The Telegram menu ------------------------------------------------------
async def test_owners_get_the_full_menu_in_their_own_chat(
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session

    assert await publish_command_menu(bot, owner_chat_ids=[111, 222, 111]) is True

    calls = session.sent_of("SetMyCommands")
    assert len(calls) == 3, "one default menu, then one per distinct owner chat"
    default, *owners = calls
    assert default.scope is None  # type: ignore[attr-defined]
    assert {command.command for command in default.commands} <= BASIC  # type: ignore[attr-defined]
    for call, chat_id in zip(owners, (111, 222), strict=True):
        scope = call.scope  # type: ignore[attr-defined]
        assert isinstance(scope, BotCommandScopeChat)
        assert scope.chat_id == chat_id
        assert {command.command for command in call.commands} == set(BY_NAME)  # type: ignore[attr-defined]
