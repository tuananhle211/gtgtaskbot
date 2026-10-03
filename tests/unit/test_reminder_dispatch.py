"""The reminder flow through the real aiogram Dispatcher.

The service-level tests in ``test_reminders.py`` prove the arithmetic and the
durability. This file proves the thing a user actually experiences: typing an
ordinary Vietnamese sentence into Telegram produces a preview, and pressing the
button produces a reminder.

It matters as a separate file because the failure it guards against is a
*routing* failure, not a logic one. A reminder router included in the wrong
place would be silently shadowed by the HR router or swallowed by the
conversation router, every service test would still pass, and the feature would
not exist for anybody.
"""

from __future__ import annotations

import uuid

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.db.models.reminder import Reminder
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Role
from meobot.domain.member.intents import MemberIntent, classify
from meobot.domain.reminders.models import ReminderStatus, ScheduleKind
from tests.fakes import RecordingSession, SqliteDatabase, bot_reply_message, make_update
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

BASE = 740_000
MEMBER = 970_101


def uid(offset: int) -> int:
    return BASE + offset


async def add_member(database: SqliteDatabase) -> uuid.UUID:
    async with database.transaction() as session:
        user = User(
            telegram_user_id=MEMBER,
            telegram_username="linh",
            full_name="Nguyễn Thị Linh",
            role=Role.EMPLOYEE,
            active=True,
            status=UserStatus.ACTIVE,
            telegram_private_chat_id=MEMBER,
            private_chat_available=True,
        )
        session.add(user)
        await session.flush()
        return user.id


def press(data: str, *, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=TelegramUser(id=MEMBER, is_bot=False, first_name="Linh", username="linh"),
            chat_instance=f"ci-{update_id}",
            data=data,
            message=bot_reply_message(chat_id=MEMBER, message_id=update_id),
        ),
    )


def confirm_button(session: RecordingSession) -> str:
    """The callback data behind '✅ Tạo lịch nhắc' in the last message sent."""
    for request in reversed(session.sent_of("SendMessage")):
        markup = getattr(request, "reply_markup", None)
        if markup is None:
            continue
        for row in markup.inline_keyboard:
            for button in row:
                if button.text.startswith("✅ Tạo lịch nhắc"):
                    return button.callback_data or ""
    raise AssertionError("no confirmation button was rendered")


async def test_a_reminder_sentence_reaches_the_reminder_router(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """The exact sentence from the bug report, end to end."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.",
            update_id=uid(1),
            user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )

    body = session.combined_text()
    assert "LỊCH NHẮC" in body
    assert "đi dạy" in body
    assert "16:00 mỗi thứ Năm" in body
    # Deterministic: no chat slot, no provider call.
    assert counting_llm.chat_calls == 0
    # And nothing durable exists yet.
    async with bot_database.session() as db:
        assert (await db.execute(select(Reminder))).scalars().all() == []


async def test_the_preview_never_claims_the_reminder_exists(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The sentence that started this release must not appear before a commit."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.",
            update_id=uid(2),
            user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )

    body = session.combined_text().lower()
    for claim in ("đã ghi nhận", "đã tạo", "đã kích hoạt"):
        assert claim not in body, f"the preview claims {claim!r} before anything was written"


async def test_confirming_creates_exactly_one_reminder(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.",
            update_id=uid(3),
            user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )
    await dispatcher.feed_update(bot, press(confirm_button(session), update_id=uid(4)))

    async with bot_database.session() as db:
        rows = list((await db.execute(select(Reminder))).scalars().all())
    assert len(rows) == 1
    assert rows[0].content == "đi dạy"
    assert rows[0].schedule_kind is ScheduleKind.WEEKLY
    assert rows[0].status is ReminderStatus.ACTIVE
    assert rows[0].next_run_at is not None

    # And only now does MeoBot say so.
    assert "đã tạo lịch nhắc" in session.combined_text().lower()


async def test_pressing_confirm_twice_creates_one_reminder(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.",
            update_id=uid(5),
            user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )
    data = confirm_button(session)
    await dispatcher.feed_update(bot, press(data, update_id=uid(6)))
    await dispatcher.feed_update(bot, press(data, update_id=uid(7)))

    async with bot_database.session() as db:
        rows = list((await db.execute(select(Reminder))).scalars().all())
    assert len(rows) == 1, "the FSM draft is cleared on commit, so a second press adds nothing"


async def test_an_ambiguous_hour_asks_one_question(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Nhắc tôi 4 giờ họp với chị Linh", update_id=uid(8), user_id=MEMBER, chat_id=MEMBER
        ),
    )

    assert "Bạn muốn nhắc lúc mấy giờ?" in session.combined_text()
    labels = [
        button.text
        for request in session.sent_of("SendMessage")
        if getattr(request, "reply_markup", None) is not None
        for row in request.reply_markup.inline_keyboard
        for button in row
    ]
    assert "04:00 sáng" in labels
    assert "16:00 chiều" in labels
    async with bot_database.session() as db:
        assert (await db.execute(select(Reminder))).scalars().all() == []


async def test_listing_reminders_works_after_creating_one(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.",
            update_id=uid(9),
            user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )
    await dispatcher.feed_update(bot, press(confirm_button(session), update_id=uid(10)))
    await dispatcher.feed_update(
        bot, make_update("Lịch nhắc của tôi.", update_id=uid(11), user_id=MEMBER, chat_id=MEMBER)
    )

    body = session.combined_text()
    assert "LỊCH NHẮC CỦA BẠN" in body
    assert "đi dạy" in body
    assert "Hoạt động" in body


async def test_ordinary_chat_still_falls_through(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """The reminder router must not swallow anything that is not a reminder.

    This is the aiogram trap the member routers already fell into once: a
    handler whose filters matched has handled the update, so a router that
    matches too broadly silently eats ordinary conversation.
    """
    bot, _session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Cho mình vài ý tưởng nội dung tuần này",
            update_id=uid(12),
            user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )
    assert counting_llm.chat_calls == 1, "an ordinary question must reach the conversation router"


def test_the_word_remind_alone_is_not_a_reminder() -> None:
    """ "Nhắc team họp" is a broadcast; "nhắc tôi họp" is a reminder."""
    assert classify("Nhắc tôi họp lúc 9 giờ").intent is MemberIntent.CREATE_REMINDER
    assert classify("Nhắc những người chưa đọc.").intent is MemberIntent.REMIND_UNREAD
