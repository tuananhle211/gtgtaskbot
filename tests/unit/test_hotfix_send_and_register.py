"""The two cross-chat defects 0.6.0a2.1 was opened to fix, turn by turn.

Both were reported against a running bot, and both had the same shape: a
sentence that names a real business operation reached the conversation model
instead of a handler, and the model answered with something that sounded like
success.

* **C** - the owner wrote "@bot đăng ký group này làm group Test." inside the
  group. MeoBot talked about it, asked what the group was for, and never wrote a
  ``telegram_chats`` row. The registration intent *was* recognised; what was
  missing is that a group named by the person - rather than by one of the eight
  built-in purposes - had nowhere to be stored, so the handler fell back to
  asking a question it already had the answer to.
* **D** - the owner wrote "Gửi ‘Chào buổi sáng’ vào group Test." privately.
  Nothing recognised it at all: it classified ``GENERATIVE``, went to the LLM,
  and came back as instructions about BotFather and tokens for a bot that was
  already running and already in the group.

These drive the real Dispatcher, so what they pin is the whole path -
classification, routing priority, the handler, the service and the outbox - and
not a parser in isolation.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.application.chat_registry_service import ChatRegistryService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.notifications import Announcement, OutboundMessage, TelegramChat
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.callbacks import MemberBinding, build
from meobot.domain.notifications.models import AnnouncementStatus, ChatPurpose, RecipientType
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import (
    BOT_ID,
    RecordingSession,
    SqliteDatabase,
    bot_reply_message,
    drawn_button,
    make_group_update,
    make_update,
)
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

#: Update ids are deduplicated process-wide, so every test module needs its own
#: range or one file's updates are silently swallowed by another's.
BASE = 1_200_000
#: The group in the report. Its Telegram title is "Test" and the owner asks for
#: it to be registered under that same name.
TEST_GROUP = -990_477
EMPLOYEE = 990_101


def uid(offset: int) -> int:
    return BASE + offset


#: Parametrized cases cannot derive an update id from ``hash()``: it is salted
#: per process, so two cases collide on some runs and not others - and a
#: collision is invisible, because the deduplication middleware simply drops the
#: second update. A counter is boring and always distinct.
_counter = itertools.count(BASE + 5_000)


def next_uid() -> int:
    return next(_counter)


def press(
    data: str, *, update_id: int, from_user_id: int = OWNER_TELEGRAM_ID, chat_id: int | None = None
) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=TelegramUser(
                id=from_user_id, is_bot=False, first_name="Owner", username="owner"
            ),
            chat_instance=f"ci-{update_id}",
            data=data,
            message=bot_reply_message(
                chat_id=chat_id if chat_id is not None else from_user_id, message_id=update_id
            ),
        ),
    )


def button_for(
    action: str,
    *,
    settings: Settings,
    telegram_user_id: int = OWNER_TELEGRAM_ID,
    chat_id: int | None = None,
    entity_id: uuid.UUID | None = None,
    version: int = 0,
) -> str:
    return build(
        action,
        secret=settings.callback_secret,
        binding=MemberBinding(
            bot_id=BOT_ID,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id if chat_id is not None else telegram_user_id,
        ),
        expires_at=utcnow() + timedelta(hours=1),
        entity_id=entity_id,
        version=version,
    )


async def rows_of(database: SqliteDatabase, model: type) -> list:
    async with database.session() as session:
        return list((await session.execute(select(model))).scalars().all())


def cross_chat_sends(session: RecordingSession, *, source_chat_id: int) -> list:
    """Any ``sendMessage`` aimed at a chat other than the one being replied to."""
    return [
        request
        for request in session.sent_of("SendMessage")
        if int(getattr(request, "chat_id", 0) or 0) != source_chat_id
    ]


async def add_user(
    database: SqliteDatabase,
    *,
    telegram_id: int,
    role: Role = Role.EMPLOYEE,
    name: str = "Nguyễn Thị Linh",
) -> uuid.UUID:
    async with database.transaction() as session:
        user = User(
            telegram_user_id=telegram_id,
            telegram_username="u",
            full_name=name,
            role=role,
            active=True,
            status=UserStatus.ACTIVE,
            telegram_private_chat_id=telegram_id,
            private_chat_available=True,
        )
        session.add(user)
        await session.flush()
        return user.id


async def register_test_group(
    database: SqliteDatabase,
    *,
    chat_id: int = TEST_GROUP,
    display_name: str = "Group Test",
    purpose: ChatPurpose = ChatPurpose.GENERAL,
    title: str = "Test",
) -> uuid.UUID:
    """The destination as it exists once the owner has registered it."""
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with database.transaction() as session:
        row = await ChatRegistryService(session).register(
            actor=owner,
            bot_identity=BOT_ID,
            telegram_chat_id=chat_id,
            chat_type="supergroup",
            telegram_title=title,
            display_name=display_name,
            purpose=purpose,
        )
        return row.id


# --- Reproduction C: "@bot đăng ký group này làm group Test." ----------------
REGISTER_SENTENCE = "@MeoBotTest đăng ký group này làm group Test."


class TestReproductionRegisterNamedGroup:
    """The exact sentence from the report, said in the group it names."""

    async def test_the_preview_appears_instead_of_a_question(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        """It answered "group này dùng để làm gì?" - it had just been told."""
        bot, session = bot_and_session

        await dispatcher.feed_update(
            bot,
            make_group_update(
                REGISTER_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=TEST_GROUP,
                update_id=uid(1),
                chat_title="Test",
            ),
        )

        reply = session.combined_text()
        assert "Bạn đang đăng ký group" in reply
        assert "Test" in reply
        # The deterministic handler ran, so no chat slot was spent.
        assert counting_llm.chat_calls == 0
        # And nothing is durable yet.
        assert await rows_of(bot_database, TelegramChat) == []

    async def test_the_preview_names_the_group_the_owner_asked_for(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            make_group_update(
                REGISTER_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=TEST_GROUP,
                update_id=uid(2),
                chat_title="Test",
            ),
        )
        reply = session.combined_text()
        assert "Tên Telegram" in reply
        assert "Tên sử dụng" in reply
        assert "Group Test" in reply
        assert "Mục đích" in reply

    async def test_confirming_creates_exactly_one_chat_row(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            make_group_update(
                REGISTER_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=TEST_GROUP,
                update_id=uid(3),
                chat_title="Test",
            ),
        )
        await dispatcher.feed_update(
            bot,
            press(
                drawn_button(session, "✅ Xác nhận"),
                update_id=uid(4),
                chat_id=TEST_GROUP,
            ),
        )

        rows = await rows_of(bot_database, TelegramChat)
        assert len(rows) == 1
        # The identity comes from the update, never from anything parsed.
        assert rows[0].telegram_chat_id == TEST_GROUP
        assert rows[0].display_name == "Group Test"
        assert rows[0].is_active

    async def test_confirming_twice_still_leaves_one_row(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            make_group_update(
                REGISTER_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=TEST_GROUP,
                update_id=uid(5),
                chat_title="Test",
            ),
        )
        data = drawn_button(session, "✅ Xác nhận")
        await dispatcher.feed_update(bot, press(data, update_id=uid(6), chat_id=TEST_GROUP))
        await dispatcher.feed_update(bot, press(data, update_id=uid(7), chat_id=TEST_GROUP))

        assert len(await rows_of(bot_database, TelegramChat)) == 1

    async def test_a_private_chat_cannot_register_a_group(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            make_update(
                "đăng ký group này làm group Test",
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(8),
            ),
        )
        assert "trong chính group" in session.combined_text()
        assert await rows_of(bot_database, TelegramChat) == []

    async def test_an_employee_cannot_register_a_group(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        bot, session = bot_and_session
        await add_user(bot_database, telegram_id=EMPLOYEE, role=Role.EMPLOYEE)

        await dispatcher.feed_update(
            bot,
            make_group_update(
                REGISTER_SENTENCE,
                user_id=EMPLOYEE,
                chat_id=TEST_GROUP,
                update_id=uid(9),
                chat_title="Test",
            ),
        )
        # Pressing the real button: the refusal has to come from the service,
        # not from the employee being unable to produce a valid payload.
        await dispatcher.feed_update(
            bot,
            press(
                drawn_button(session, "✅ Xác nhận"),
                update_id=uid(10),
                from_user_id=EMPLOYEE,
                chat_id=TEST_GROUP,
            ),
        )
        assert await rows_of(bot_database, TelegramChat) == []
        assert "Chỉ" in session.combined_text()

    @pytest.mark.parametrize(
        "sentence",
        [
            "Đăng ký group này.",
            "Đăng ký đây là group Test.",
            "Dùng group này để nhận thông báo.",
            "Đây là group thông báo toàn phòng.",
            "Đây là group Content.",
            "Đây là group Báo cáo.",
            "Dang ky group nay lam group Test.",
        ],
    )
    async def test_every_documented_phrasing_previews(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        sentence: str,
    ) -> None:
        """None of these may end in a question about what the group is for."""
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            make_group_update(
                sentence,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=TEST_GROUP,
                update_id=next_uid(),
                chat_title="Test",
            ),
        )
        assert "Bạn đang đăng ký group" in session.combined_text()


# --- Reproduction D: "Gửi ‘Chào buổi sáng’ vào group Test." ------------------
SEND_SENTENCE = "Gửi “Chào buổi sáng” vào group Test."


class TestReproductionSendToNamedGroup:
    """The exact private sentence from the report.

    It reached the model, which answered with BotFather setup instructions.
    """

    async def test_it_never_reaches_the_conversation_model(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        bot, session = bot_and_session
        await register_test_group(bot_database)

        await dispatcher.feed_update(
            bot,
            make_update(
                SEND_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(30),
            ),
        )

        assert counting_llm.chat_calls == 0, "a send request is not conversation"
        reply = session.combined_text()
        for forbidden in ("BotFather", "botfather", "token", "tích hợp"):
            assert forbidden not in reply, f"setup instructions leaked: {forbidden}"

    async def test_the_preview_shows_the_content_and_the_destination(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_test_group(bot_database)

        await dispatcher.feed_update(
            bot,
            make_update(
                SEND_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(31),
            ),
        )

        reply = session.combined_text()
        assert "THÔNG BÁO" in reply
        assert "Chào buổi sáng" in reply
        assert "Nơi nhận" in reply
        assert "Group Test" in reply
        # Nothing is queued before the press.
        assert await rows_of(bot_database, OutboundMessage) == []

    async def test_confirming_queues_exactly_one_outbound_message(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        bot, session = bot_and_session
        await register_test_group(bot_database)
        await dispatcher.feed_update(
            bot,
            make_update(
                SEND_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(32),
            ),
        )
        drafts = await rows_of(bot_database, Announcement)
        assert len(drafts) == 1

        await dispatcher.feed_update(
            bot,
            press(
                button_for("announce.send", settings=settings, entity_id=drafts[0].id),
                update_id=uid(33),
            ),
        )

        queued = await rows_of(bot_database, OutboundMessage)
        assert len(queued) == 1
        assert queued[0].telegram_chat_id == TEST_GROUP
        assert queued[0].recipient_type is RecipientType.REGISTERED_CHAT
        # The handler queued; it did not send.
        assert cross_chat_sends(session, source_chat_id=OWNER_TELEGRAM_ID) == []

        published = await rows_of(bot_database, Announcement)
        assert published[0].status is AnnouncementStatus.PUBLISHED

    async def test_the_source_chat_is_told_it_is_queued_not_sent(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        """ "Đã gửi" before Telegram has settled would be a lie."""
        bot, session = bot_and_session
        await register_test_group(bot_database)
        await dispatcher.feed_update(
            bot,
            make_update(
                SEND_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(34),
            ),
        )
        drafts = await rows_of(bot_database, Announcement)
        session.requests.clear()
        await dispatcher.feed_update(
            bot,
            press(
                button_for("announce.send", settings=settings, entity_id=drafts[0].id),
                update_id=uid(35),
            ),
        )
        reply = session.combined_text()
        assert "xếp hàng gửi" in reply
        assert "Đã gửi thông báo tới" not in reply

    @pytest.mark.parametrize(
        "sentence",
        [
            "Gửi Chào buổi sáng vào group Test.",
            "Thông báo cho group Test: Chào buổi sáng.",
            "Gửi nội dung này vào group Test: Chào buổi sáng.",
            "gui chao buoi sang vao group test",
            "Nhắn vào group Test: Chào buổi sáng.",
        ],
    )
    async def test_every_documented_send_phrasing_previews(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
        sentence: str,
    ) -> None:
        bot, session = bot_and_session
        await register_test_group(bot_database)
        await dispatcher.feed_update(
            bot,
            make_update(
                sentence,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=next_uid(),
            ),
        )
        reply = session.combined_text()
        assert "THÔNG BÁO" in reply, sentence
        assert "Group Test" in reply, sentence
        assert counting_llm.chat_calls == 0, sentence


class TestUnknownDestination:
    """An unregistered group is a registry problem, and must read like one."""

    async def test_it_names_the_group_and_offers_the_registry(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        bot, session = bot_and_session
        # Nothing registered at all.
        await dispatcher.feed_update(
            bot,
            make_update(
                SEND_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(50),
            ),
        )

        reply = session.combined_text()
        assert "chưa tìm thấy group" in reply
        assert "Test" in reply
        assert counting_llm.chat_calls == 0
        assert await rows_of(bot_database, OutboundMessage) == []
        assert await rows_of(bot_database, Announcement) == []

    async def test_it_never_mentions_botfather_or_tokens(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """The reported answer. A destination miss is not a setup problem."""
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            make_update(
                SEND_SENTENCE,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(51),
            ),
        )
        reply = session.combined_text().lower()
        for forbidden in ("botfather", "token", "whitelist", "tích hợp telegram"):
            assert forbidden not in reply


class TestAliasResolution:
    """A registered group answers to the names a person would actually use."""

    @pytest.mark.parametrize(
        "sentence",
        [
            "Gửi Chào buổi sáng vào group Test.",
            "Gửi Chào buổi sáng vào nhóm Test.",
            "Thông báo cho Test: Chào buổi sáng.",
        ],
    )
    async def test_the_same_group_resolves_from_each_name(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        sentence: str,
    ) -> None:
        bot, session = bot_and_session
        await register_test_group(bot_database)
        await dispatcher.feed_update(
            bot,
            make_update(
                sentence,
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=next_uid(),
            ),
        )
        assert "Group Test" in session.combined_text(), sentence

    async def test_two_matching_groups_produce_one_question(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """Never a guess: two destinations under one name means one question.

        Two different Telegram groups can honestly both be called "Group Test" -
        the registry keys on the chat id, not the name. Picking the more recent
        one would be right about half the time, and wrong in a way that posts an
        internal message into a group the owner did not mean.
        """
        bot, session = bot_and_session
        await register_test_group(bot_database)
        await register_test_group(bot_database, chat_id=TEST_GROUP - 1, title="Test (cũ)")

        await dispatcher.feed_update(
            bot,
            make_update(
                "Gửi Chào buổi sáng vào group Test.",
                user_id=OWNER_TELEGRAM_ID,
                chat_id=OWNER_TELEGRAM_ID,
                update_id=uid(70),
            ),
        )
        reply = session.combined_text()
        assert "group nào" in reply.lower()
        assert await rows_of(bot_database, OutboundMessage) == []
