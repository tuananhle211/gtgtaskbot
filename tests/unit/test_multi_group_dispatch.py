"""The 0.6.0a3 defects, turn by turn, through the real Dispatcher.

Four failures were reported against a running bot, and all four have the same
root: an announcement could name exactly one destination, so a *set* of
recipients had nowhere to be recorded and the conversation went round again
asking for what should already have been held.

* **A** - the owner was shown three candidate groups, replied "Tất cả", and was
  asked *"Bạn muốn thực hiện thao tác gì với tất cả các group này?"*.
* **B** - the owner was shown a preview, replied "Xác nhận", and was asked what
  action and which recipients they meant, or told *"Mình chưa thể gửi tin trong
  lượt này"*.
* **C** - a long announcement was capped at 3000 characters and the rest
  dropped, silently.
* **D** - the exact registered group title had to be typed.

These drive the real Dispatcher with the real router order, a real (SQLite)
schema and a recording Telegram transport, so what they pin is the whole path -
classification, routing priority, the draft, the service, the outbox - and not a
parser in isolation. Nothing here reaches Telegram, an LLM, Google, Meta or
TikTok.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Sequence

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.application.chat_registry_service import ChatRegistryService
from meobot.db.models.dispatch import (
    MessageDispatch,
    MessageDispatchDraft,
    MessageDispatchDraftRecipient,
    MessageDispatchPart,
    MessageDispatchRecipient,
    MessageDispatchRecipientPart,
)
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.dispatch.models import DispatchStatus, DraftStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import ChatPurpose, RecipientType
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import (
    BOT_ID,
    RecordingSession,
    SqliteDatabase,
    bot_reply_message,
    drawn_button,
    make_update,
)
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

#: Update ids are deduplicated process-wide, so this module needs its own range
#: or one file's updates are silently swallowed by another's.
BASE = 1_500_000
_counter = itertools.count(BASE)


def next_uid() -> int:
    return next(_counter)


#: The three groups from the report.
SAYKENG = -900_101
KET_BAN = -900_102
TEST_GROUP = -900_103
#: A fourth, belonging to a different department, so "các group phòng PR" has
#: something it must *not* select.
SEEDING = -900_104

TEAM_LEAD = 910_201
EMPLOYEE = 910_202

ANNOUNCEMENT = (
    "Chào mọi người, mình là MeoBot — trợ lý hỗ trợ điều hành và sáng tạo nội dung "
    "cho Phòng PR Truyền thông Apexmed."
)


# --- Fixtures in the small ---------------------------------------------------
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


async def register(
    database: SqliteDatabase,
    *,
    chat_id: int,
    display_name: str,
    title: str,
    purpose: ChatPurpose = ChatPurpose.GENERAL,
    aliases: Sequence[str] = (),
    tags: Sequence[str] = (),
    department: str | None = None,
    team: str | None = None,
    brand: str | None = None,
) -> uuid.UUID:
    """One registered destination, with the metadata natural naming resolves against."""
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
            aliases=list(aliases),
            tags=list(tags),
            department=department,
            team=team,
            brand=brand,
        )
        return row.id


async def register_the_three(database: SqliteDatabase) -> None:
    """The registry as it stood in the report, plus one group outside PR.

    Registered oldest-first so ``active`` - which orders newest first - lists
    them as Saykeng, Kết bạn, Test, matching the card in the screenshot.
    """
    await register(
        database,
        chat_id=SEEDING,
        display_name="Group Seeding",
        title="Seeding",
        purpose=ChatPurpose.SEEDING_TEAM,
        tags=("seeding",),
        department="Kinh doanh",
    )
    await register(
        database,
        chat_id=TEST_GROUP,
        display_name="Test",
        title="Test",
        aliases=("Test",),
        department="PR Truyền thông",
    )
    await register(
        database,
        chat_id=KET_BAN,
        display_name="KẾT BẠN BỐN PHƯƠNG",
        title="KẾT BẠN BỐN PHƯƠNG",
        aliases=("Kết bạn", "Bốn phương"),
        tags=("cộng đồng", "thử nghiệm"),
        department="PR Truyền thông",
    )
    await register(
        database,
        chat_id=SAYKENG,
        display_name="Saykeng - Vựa Idea",
        title="Saykeng - Vựa Idea",
        aliases=("Saykeng", "Vựa Idea", "Idea"),
        tags=("brainstorm", "nội dung", "sáng tạo"),
        purpose=ChatPurpose.CONTENT_TEAM,
        department="PR Truyền thông",
        brand="Apexmed",
    )


def owner_says(text: str, *, user_id: int = OWNER_TELEGRAM_ID) -> Update:
    """One private message, as Telegram would deliver it."""
    update_id = next_uid()
    return make_update(text, user_id=user_id, chat_id=user_id, update_id=update_id)


def press(data: str, *, from_user_id: int = OWNER_TELEGRAM_ID) -> Update:
    update_id = next_uid()
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=TelegramUser(
                id=from_user_id, is_bot=False, first_name="Owner", username="owner"
            ),
            chat_instance=f"ci-{update_id}",
            data=data,
            message=bot_reply_message(chat_id=from_user_id, message_id=update_id),
        ),
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


#: Sentences that must never appear again. Each is the reported answer to a
#: question the draft already had the answer to.
FORBIDDEN = (
    "Bạn muốn thực hiện thao tác gì",
    "Mình chưa thể gửi tin trong lượt này",
    "BotFather",
    "whitelist",
)


def assert_no_reported_answers(session: RecordingSession) -> None:
    reply = session.combined_text()
    for phrase in FORBIDDEN:
        assert phrase not in reply, f"the reported answer came back: {phrase!r}"


# --- Reproduction A + B: the exact screenshot flow ---------------------------
class TestScreenshotRegression:
    """Three candidates, "Tất cả", a preview, "Xác nhận". End to end."""

    async def test_the_whole_flow_produces_one_dispatch_and_three_destinations(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        bot, session = bot_and_session
        await add_user(
            bot_database, telegram_id=OWNER_TELEGRAM_ID, role=Role.OWNER, name="Trưởng phòng"
        )
        await register_the_three(bot_database)

        # 1. The owner writes the announcement and describes the destinations.
        await dispatcher.feed_update(
            bot,
            owner_says(
                f"Gửi thông báo này vào các group của phòng PR Truyền thông: {ANNOUNCEMENT}"
            ),
        )
        first = session.combined_text()
        assert "MeoBot hiểu" in first
        assert "Saykeng" in first
        assert "KẾT BẠN BỐN PHƯƠNG" in first
        assert "Test" in first
        # Nothing durable has been queued: a candidate list is not a decision.
        assert await rows_of(bot_database, OutboundMessage) == []

        # 2. "Tất cả". This is the turn that asked what action was wanted.
        session.requests.clear()
        await dispatcher.feed_update(bot, owner_says("Tất cả"))
        after_all = session.combined_text()
        assert "THÔNG BÁO" in after_all, "‘Tất cả’ must expand into the preview"
        assert "Nơi nhận — 3 group" in after_all
        assert_no_reported_answers(session)
        assert await rows_of(bot_database, OutboundMessage) == []

        # 3. "Xác nhận". This is the turn that asked which recipients were meant.
        session.requests.clear()
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        receipt = session.combined_text()
        assert "xếp hàng gửi tới 3 group" in receipt
        assert_no_reported_answers(session)

        # 4. Exactly one dispatch, three destination records, three outbox rows.
        dispatches = await rows_of(bot_database, MessageDispatch)
        assert len(dispatches) == 1
        assert dispatches[0].recipient_count == 3

        recipients = await rows_of(bot_database, MessageDispatchRecipient)
        assert len(recipients) == 3
        assert {row.telegram_chat_id for row in recipients} == {SAYKENG, KET_BAN, TEST_GROUP}

        queued = await rows_of(bot_database, OutboundMessage)
        assert len(queued) == 3
        assert {row.telegram_chat_id for row in queued} == {SAYKENG, KET_BAN, TEST_GROUP}
        assert all(row.recipient_type is RecipientType.REGISTERED_CHAT for row in queued)

        # 5. The handler queued; it did not send.
        assert cross_chat_sends(session, source_chat_id=OWNER_TELEGRAM_ID) == []
        # 6. And not one turn of this cost a chat slot.
        assert counting_llm.chat_calls == 0

    async def test_the_seeding_group_is_not_swept_in(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """ "Các group của phòng PR" is three groups, and there are four registered."""
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot,
            owner_says(
                f"Gửi thông báo này vào các group của phòng PR Truyền thông: {ANNOUNCEMENT}"
            ),
        )
        await dispatcher.feed_update(bot, owner_says("Tất cả"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        queued = await rows_of(bot_database, OutboundMessage)
        assert SEEDING not in {row.telegram_chat_id for row in queued}


# --- Reproduction B in isolation --------------------------------------------
class TestTextConfirmation:
    """ "Xác nhận" is connected to the draft it is looking at."""

    @pytest.mark.parametrize(
        "phrase", ["Xác nhận", "Đúng rồi", "Gửi đi", "Gửi tất cả nhé", "Ok gửi nhé", "Chốt"]
    )
    async def test_every_documented_confirmation_sends_the_draft(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
        phrase: str,
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        assert "THÔNG BÁO" in session.combined_text(), phrase

        session.requests.clear()
        await dispatcher.feed_update(bot, owner_says(phrase))

        if "tất cả" in phrase.lower():
            # "Gửi tất cả" widens the selection first and previews again; the
            # confirmation is the next turn. Never a send without a look.
            await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        assert len(await rows_of(bot_database, MessageDispatch)) == 1, phrase
        assert counting_llm.chat_calls == 0, phrase

    async def test_a_duplicate_typed_confirmation_creates_no_second_dispatch(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        assert len(await rows_of(bot_database, MessageDispatch)) == 1
        assert len(await rows_of(bot_database, MessageDispatchRecipient)) == 2
        assert len(await rows_of(bot_database, OutboundMessage)) == 2

    async def test_pressing_the_button_after_typing_confirms_nothing_twice(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """The two routes into confirmation must not add up to two dispatches."""
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        button = drawn_button(session, "✅ Gửi tới")
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        await dispatcher.feed_update(bot, press(button))

        assert len(await rows_of(bot_database, MessageDispatch)) == 1
        assert len(await rows_of(bot_database, OutboundMessage)) == 2

    async def test_a_preview_drawn_before_the_selection_changed_cannot_send(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """What somebody read has to be what goes out.

        The confirm button is signed against the draft *version*, so a card
        that said "Gửi tới 3 group" stops working the moment the selection
        becomes something else - and says so instead of sending.
        """
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot,
            owner_says(
                "Gửi thông báo này vào các group của phòng PR Truyền thông: " + ANNOUNCEMENT
            ),
        )
        await dispatcher.feed_update(bot, owner_says("Tất cả"))
        stale = drawn_button(session, "✅ Gửi tới")

        # The list changes after that card was drawn.
        await dispatcher.feed_update(bot, owner_says("Bỏ Test"))
        session.requests.clear()
        await dispatcher.feed_update(bot, press(stale))

        assert await rows_of(bot_database, MessageDispatch) == []
        assert await rows_of(bot_database, OutboundMessage) == []
        assert "đã thay đổi" in session.combined_text()

    async def test_a_duplicate_button_press_creates_no_duplicate(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        button = drawn_button(session, "✅ Gửi tới")
        await dispatcher.feed_update(bot, press(button))
        await dispatcher.feed_update(bot, press(button))

        assert len(await rows_of(bot_database, MessageDispatch)) == 1
        assert len(await rows_of(bot_database, OutboundMessage)) == 2


# --- Reproduction D: the exact name is not required --------------------------
class TestNaturalResolution:
    """A group is reachable by the names people actually use for it."""

    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            # The full registered display name.
            ("Gửi cho các group Saykeng - Vựa Idea: xin chào", {SAYKENG}),
            # A short alias.
            ("Gửi cho các group Saykeng: xin chào", {SAYKENG}),
            # An alias with accents, and one without.
            ("Gửi cho các nhóm Vựa Idea: xin chào", {SAYKENG}),
            ("Gửi cho các nhóm idea: xin chào", {SAYKENG}),
            ("Gửi cho các group ket ban: xin chào", {KET_BAN}),
            # A tag.
            ("Gửi thông báo này vào các group sáng tạo: xin chào", {SAYKENG}),
            # A brand.
            ("Gửi thông báo này vào các group Apexmed: xin chào", {SAYKENG}),
            # A purpose.
            ("Gửi thông báo này vào các nhóm seeding: xin chào", {SEEDING}),
            # Two named groups at once.
            ("Gửi cho group Test và group Kết bạn: xin chào", {KET_BAN, TEST_GROUP}),
            ("Gửi cho group Test với group kết bạn: xin chào", {KET_BAN, TEST_GROUP}),
        ],
    )
    async def test_each_documented_phrasing_resolves(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
        sentence: str,
        expected: set[int],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says(sentence))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        queued = await rows_of(bot_database, OutboundMessage)
        assert {row.telegram_chat_id for row in queued} == expected, sentence
        assert counting_llm.chat_calls == 0, sentence

    async def test_all_registered_groups_expands_to_the_whole_registry(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi thông báo này cho tất cả group: {ANNOUNCEMENT}")
        )
        assert "MeoBot tìm thấy" in session.combined_text()

        await dispatcher.feed_update(bot, owner_says("Tất cả"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        queued = await rows_of(bot_database, OutboundMessage)
        assert {row.telegram_chat_id for row in queued} == {
            SAYKENG,
            KET_BAN,
            TEST_GROUP,
            SEEDING,
        }

    async def test_an_exclusion_is_honoured(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot,
            owner_says(f"Gửi thông báo này cho tất cả group trừ group Test: {ANNOUNCEMENT}"),
        )
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        queued = await rows_of(bot_database, OutboundMessage)
        assert TEST_GROUP not in {row.telegram_chat_id for row in queued}
        assert len(queued) == 3

    async def test_an_unknown_name_is_never_guessed(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        """A near-miss must not post an internal announcement into a real group."""
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says("Gửi cho các group Marketing Quốc Tế: xin chào")
        )

        reply = session.combined_text()
        assert "chưa tìm thấy" in reply
        assert await rows_of(bot_database, OutboundMessage) == []
        assert await rows_of(bot_database, MessageDispatch) == []
        assert counting_llm.chat_calls == 0
        for forbidden in ("BotFather", "token", "whitelist"):
            assert forbidden not in reply

    async def test_two_groups_under_one_name_produce_one_question(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register(bot_database, chat_id=TEST_GROUP, display_name="Test", title="Test")
        await register(bot_database, chat_id=TEST_GROUP - 1, display_name="Test", title="Test (cũ)")
        await dispatcher.feed_update(
            bot, owner_says("Gửi cho các group Test và group Test: xin chào")
        )

        reply = session.combined_text()
        assert "nhiều group cùng tên" in reply
        assert await rows_of(bot_database, OutboundMessage) == []


# --- The active draft --------------------------------------------------------
class TestActiveDraft:
    """While a draft is open it owns the turn. Free chat never sees it."""

    async def _open_three(self, dispatcher: Dispatcher, bot: Bot, database: SqliteDatabase) -> None:
        await register_the_three(database)
        await dispatcher.feed_update(
            bot,
            owner_says(
                f"Gửi thông báo này vào các group của phòng PR Truyền thông: {ANNOUNCEMENT}"
            ),
        )

    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            ("Tất cả", {SAYKENG, KET_BAN, TEST_GROUP}),
            ("Cả ba", {SAYKENG, KET_BAN, TEST_GROUP}),
            ("Hai group đầu", {SAYKENG, KET_BAN}),
            ("Group 1 và 3", {SAYKENG, TEST_GROUP}),
            ("Bỏ Test", {SAYKENG, KET_BAN}),
            ("Chỉ Saykeng", {SAYKENG}),
            ("Không gửi Kết bạn", {SAYKENG, TEST_GROUP}),
        ],
    )
    async def test_each_documented_reply_changes_the_selection(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
        reply: str,
        expected: set[int],
    ) -> None:
        bot, _session = bot_and_session
        await self._open_three(dispatcher, bot, bot_database)

        await dispatcher.feed_update(bot, owner_says(reply))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        queued = await rows_of(bot_database, OutboundMessage)
        assert {row.telegram_chat_id for row in queued} == expected, reply
        assert counting_llm.chat_calls == 0, reply

    async def test_cancel_flow_abandons_the_draft(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await self._open_three(dispatcher, bot, bot_database)
        await dispatcher.feed_update(bot, owner_says("/cancel_flow"))

        drafts = await rows_of(bot_database, MessageDispatchDraft)
        assert [row.status for row in drafts] == [DraftStatus.CANCELLED]
        # And a confirmation afterwards sends nothing.
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        assert await rows_of(bot_database, OutboundMessage) == []

    @pytest.mark.parametrize("word", ["Huỷ", "Thôi", "Không gửi nữa"])
    async def test_every_documented_cancellation_abandons_the_draft(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        word: str,
    ) -> None:
        bot, _session = bot_and_session
        await self._open_three(dispatcher, bot, bot_database)
        await dispatcher.feed_update(bot, owner_says(word))
        assert await rows_of(bot_database, OutboundMessage) == []
        drafts = await rows_of(bot_database, MessageDispatchDraft)
        assert drafts[0].status is DraftStatus.CANCELLED

    async def test_the_draft_survives_a_restart(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """The draft is a row, not FSM state.

        A deploy in the middle of somebody composing a department announcement
        must not turn their next "Xác nhận" into conversation - which is exactly
        what an in-memory draft would do.
        """
        bot, session = bot_and_session
        await self._open_three(dispatcher, bot, bot_database)

        # Simulate the process restarting: everything transient is discarded,
        # and only what is in the database survives. Closing the FSM storage is
        # the closest an offline test gets to that, and it is the exact thing
        # an in-memory draft would not survive.
        await dispatcher.storage.close()

        await dispatcher.feed_update(bot, owner_says("Tất cả"))
        assert "THÔNG BÁO" in session.combined_text()

    async def test_an_unrecognised_reply_re_shows_the_card_instead_of_chatting(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        bot, session = bot_and_session
        await self._open_three(dispatcher, bot, bot_database)
        session.requests.clear()

        await dispatcher.feed_update(bot, owner_says("ừm để mình nghĩ đã"))

        assert counting_llm.chat_calls == 0
        assert_no_reported_answers(session)
        reply = session.combined_text()
        assert "group" in reply.lower()
        # And the picker is still on screen, so the next tap still works.
        assert drawn_button(session, "✅ Xong")

    async def test_another_person_cannot_confirm_this_draft(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await add_user(bot_database, telegram_id=TEAM_LEAD, role=Role.TEAM_LEAD)
        await self._open_three(dispatcher, bot, bot_database)

        await dispatcher.feed_update(
            bot, make_update("Xác nhận", user_id=TEAM_LEAD, chat_id=TEAM_LEAD, update_id=next_uid())
        )
        assert await rows_of(bot_database, MessageDispatch) == []


# --- Long announcements ------------------------------------------------------
LONG_PARAGRAPH = (
    "Phòng PR Truyền thông xin thông báo lịch làm việc và các đầu việc trọng tâm "
    "trong tuần này để mọi người cùng nắm và chủ động sắp xếp công việc cá nhân. "
)


class TestLongAnnouncements:
    """Reproduction C: a long announcement was capped and the rest dropped."""

    async def test_a_long_announcement_is_split_and_nothing_is_lost(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        body = "\n\n".join(f"Mục {index}. {LONG_PARAGRAPH}" for index in range(40))

        await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {body}"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        parts = await rows_of(bot_database, MessageDispatchPart)
        assert len(parts) > 1, "a long announcement must be split, not truncated"
        rebuilt = "".join(part.content for part in sorted(parts, key=lambda row: row.part_number))
        assert "".join(rebuilt.split()) == "".join(body.split())

    async def test_one_outbox_row_per_part_per_destination(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        body = "\n\n".join(f"Mục {index}. {LONG_PARAGRAPH}" for index in range(40))

        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {body}")
        )
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        parts = await rows_of(bot_database, MessageDispatchPart)
        queued = await rows_of(bot_database, OutboundMessage)
        assert len(queued) == 2 * len(parts)
        # And every part is linked to the outbox row carrying it, so a retry
        # can find the ones that did not arrive.
        links = await rows_of(bot_database, MessageDispatchRecipientPart)
        assert len(links) == 2 * len(parts)
        assert all(link.outbound_message_id is not None for link in links)

    async def test_only_the_first_part_is_due_immediately(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """Part 2 must not overtake part 1.

        Both intents are durable from the moment of confirmation; the later one
        is held until the earlier one actually lands.
        """
        from meobot.core.time import ensure_utc, utcnow

        bot, _session = bot_and_session
        await register_the_three(bot_database)
        body = "\n\n".join(f"Mục {index}. {LONG_PARAGRAPH}" for index in range(40))
        await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {body}"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        now = utcnow()
        queued = await rows_of(bot_database, OutboundMessage)
        due = [row for row in queued if ensure_utc(row.available_at) <= now]
        assert len(due) == 1

    async def test_the_preview_says_how_many_messages_it_will_be(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        body = "\n\n".join(f"Mục {index}. {LONG_PARAGRAPH}" for index in range(40))
        await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {body}"))
        assert "tin liên tiếp" in session.combined_text()


# --- Permissions -------------------------------------------------------------
class TestPermissions:
    async def test_an_employee_cannot_broadcast(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await add_user(bot_database, telegram_id=EMPLOYEE, role=Role.EMPLOYEE)

        await dispatcher.feed_update(
            bot,
            make_update(
                f"Gửi cho tất cả group: {ANNOUNCEMENT}",
                user_id=EMPLOYEE,
                chat_id=EMPLOYEE,
                update_id=next_uid(),
            ),
        )
        await dispatcher.feed_update(
            bot, make_update("Xác nhận", user_id=EMPLOYEE, chat_id=EMPLOYEE, update_id=next_uid())
        )
        assert await rows_of(bot_database, OutboundMessage) == []
        assert await rows_of(bot_database, MessageDispatch) == []

    async def test_a_team_lead_reaches_only_the_groups_they_manage(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        from meobot.application.chat_assignment_service import ChatAssignmentService

        bot, _session = bot_and_session
        chat_ids = {}
        await register_the_three(bot_database)
        lead_id = await add_user(bot_database, telegram_id=TEAM_LEAD, role=Role.TEAM_LEAD)

        async with bot_database.transaction() as db:
            saykeng = await ChatRegistryService(db).by_telegram_id(
                bot_identity=BOT_ID, telegram_chat_id=SAYKENG
            )
            assert saykeng is not None
            chat_ids["saykeng"] = saykeng.id
            owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
            lead = await db.get(User, lead_id)
            assert lead is not None
            await ChatAssignmentService(db).assign_manager(actor=owner, chat=saykeng, target=lead)

        await dispatcher.feed_update(
            bot,
            make_update(
                f"Gửi cho tất cả group: {ANNOUNCEMENT}",
                user_id=TEAM_LEAD,
                chat_id=TEAM_LEAD,
                update_id=next_uid(),
            ),
        )
        await dispatcher.feed_update(
            bot, make_update("Xác nhận", user_id=TEAM_LEAD, chat_id=TEAM_LEAD, update_id=next_uid())
        )

        queued = await rows_of(bot_database, OutboundMessage)
        assert {row.telegram_chat_id for row in queued} == {SAYKENG}

    async def test_a_team_lead_never_sees_a_group_they_do_not_manage(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """A list is itself information. An assignment does not grant it."""
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await add_user(bot_database, telegram_id=TEAM_LEAD, role=Role.TEAM_LEAD)

        await dispatcher.feed_update(
            bot,
            make_update(
                "Danh sách group", user_id=TEAM_LEAD, chat_id=TEAM_LEAD, update_id=next_uid()
            ),
        )
        reply = session.combined_text()
        assert "KẾT BẠN BỐN PHƯƠNG" not in reply
        assert "Saykeng" not in reply


# --- Privacy -----------------------------------------------------------------
class TestPrivacy:
    async def test_a_payslip_is_not_posted_into_a_group(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot,
            owner_says("Gửi cho tất cả group: Bảng lương của Linh tháng này là 15 triệu."),
        )
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        assert await rows_of(bot_database, OutboundMessage) == []
        assert "thông tin cá nhân" in session.combined_text()

    async def test_the_content_is_never_silently_edited(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says("Gửi cho tất cả group: Số tài khoản của anh là 0123456789.")
        )
        drafts = await rows_of(bot_database, MessageDispatchDraft)
        assert drafts
        assert "0123456789" in drafts[0].rendered_text


# --- The registry list -------------------------------------------------------
class TestRegistryListing:
    @pytest.mark.parametrize(
        "question",
        [
            "Xem các group đã đăng ký.",
            "Có những group nào?",
            "Danh sách group.",
            "MeoBot đang gửi được vào những nhóm nào?",
            "Cho chị chọn group.",
            "Xem cac group da dang ky",
        ],
    )
    async def test_every_documented_question_shows_the_registry(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
        question: str,
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says(question))

        reply = session.combined_text()
        assert "CÁC GROUP ĐÃ ĐĂNG KÝ" in reply, question
        assert "Saykeng" in reply, question
        assert counting_llm.chat_calls == 0, question

    async def test_the_list_shows_status_and_the_other_names(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says("Danh sách group."))

        reply = session.combined_text()
        assert "Trạng thái:" in reply
        assert "Tên gọi:" in reply
        assert "Vựa Idea" in reply

    async def test_no_numeric_chat_id_ever_reaches_the_screen(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says("Danh sách group."))
        reply = session.combined_text()
        for chat_id in (SAYKENG, KET_BAN, TEST_GROUP, SEEDING):
            assert str(chat_id) not in reply
            assert str(abs(chat_id)) not in reply

    async def test_the_list_paginates(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        for index in range(9):
            await register(
                bot_database,
                chat_id=-950_000 - index,
                display_name=f"Group Số {index}",
                title=f"Số {index}",
            )
        await dispatcher.feed_update(bot, owner_says("Danh sách group."))
        assert "Trang 1/2" in session.combined_text()

    async def test_a_disabled_group_is_shown_as_paused(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await register_the_three(bot_database)
        async with bot_database.transaction() as db:
            registry = ChatRegistryService(db)
            row = await registry.by_telegram_id(bot_identity=BOT_ID, telegram_chat_id=TEST_GROUP)
            assert row is not None
            row.bot_can_send = False

        await dispatcher.feed_update(bot, owner_says("Danh sách group."))
        assert "không có quyền gửi" in session.combined_text()

    async def test_an_unreachable_group_is_left_out_of_all_registered(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """ "Tất cả group" means the ones that can actually receive it.

        And the count of what was left out is shown, so somebody who meant all
        four finds out rather than assuming.
        """
        bot, session = bot_and_session
        await register_the_three(bot_database)
        async with bot_database.transaction() as db:
            row = await ChatRegistryService(db).by_telegram_id(
                bot_identity=BOT_ID, telegram_chat_id=TEST_GROUP
            )
            assert row is not None
            row.bot_can_send = False

        await dispatcher.feed_update(bot, owner_says(f"Gửi cho tất cả group: {ANNOUNCEMENT}"))
        reply = session.combined_text()
        assert "3 group có thể gửi" in reply
        assert "không có quyền gửi" in reply

        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        queued = await rows_of(bot_database, OutboundMessage)
        assert TEST_GROUP not in {row.telegram_chat_id for row in queued}


# --- The dispatch record -----------------------------------------------------
class TestDispatchRecord:
    async def test_the_recipient_snapshot_is_exact(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """Names are copied at confirmation time, not read through later.

        A group renamed after the fact still reports under the name the sender
        confirmed - otherwise "đã gửi tới X" names somewhere they never chose.
        """
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        async with bot_database.transaction() as db:
            row = await ChatRegistryService(db).by_telegram_id(
                bot_identity=BOT_ID, telegram_chat_id=SAYKENG
            )
            assert row is not None
            row.display_name = "Đã đổi tên"

        recipients = await rows_of(bot_database, MessageDispatchRecipient)
        assert "Saykeng - Vựa Idea" in {row.destination_display_name for row in recipients}

    async def test_nothing_is_queued_before_the_confirmation(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        assert await rows_of(bot_database, OutboundMessage) == []
        assert await rows_of(bot_database, MessageDispatch) == []
        assert len(await rows_of(bot_database, MessageDispatchDraft)) == 1

    async def test_the_idempotency_key_names_dispatch_chat_version_and_part(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says(f"Gửi cho các group Saykeng: {ANNOUNCEMENT}"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        dispatches = await rows_of(bot_database, MessageDispatch)
        queued = await rows_of(bot_database, OutboundMessage)
        assert queued[0].idempotency_key.startswith(f"dispatch:{dispatches[0].id}:chat:")
        assert ":version:1:part:1" in queued[0].idempotency_key

    async def test_a_fresh_dispatch_starts_queued(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says(f"Gửi cho các group Saykeng: {ANNOUNCEMENT}"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        dispatches = await rows_of(bot_database, MessageDispatch)
        assert dispatches[0].status is DispatchStatus.QUEUED


# --- The 0.6.0a3.post1 hotfix ------------------------------------------------
#: The exact sentence from the production report. Short enough to be a single
#: part, addressed at the whole registry, and the first thing that ever wrote a
#: ``message_dispatch_drafts`` row on the deployed schema - which is why it is
#: the sentence that found the missing timestamp defaults.
EVENING = "Gửi cho tất cả group: Buổi tối vui vẻ."


class TestSendToAllGroupsRegression:
    """ "Gửi cho tất cả group: Buổi tối vui vẻ." — the reported turn, both halves.

    The production failure was an ``IntegrityError`` on the draft insert, so
    everything downstream of it was unreachable in the wild. These pin the whole
    sequence: the draft flushes, the preview appears, nothing is queued yet, and
    a confirmation produces exactly one of each durable row.
    """

    async def _register_three_active(self, database: SqliteDatabase) -> None:
        """Three registered groups, all active and reachable."""
        await register_the_three(database)
        async with database.transaction() as db:
            registry = ChatRegistryService(db)
            row = await registry.by_telegram_id(bot_identity=BOT_ID, telegram_chat_id=SEEDING)
            assert row is not None
            # The fourth registration belongs to another department; disabling
            # it makes "tất cả group" exactly the three from the report.
            row.is_active = False

    async def test_the_preview_appears_and_nothing_is_queued_yet(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        bot, session = bot_and_session
        await self._register_three_active(bot_database)

        await dispatcher.feed_update(bot, owner_says(EVENING))

        # 1. The deterministic handler matched - no chat slot was spent.
        assert counting_llm.chat_calls == 0
        # 2. The draft flushed, and holds the three active groups.
        drafts = await rows_of(bot_database, MessageDispatchDraft)
        assert len(drafts) == 1
        assert drafts[0].rendered_text == "Buổi tối vui vẻ."
        selected = [
            row
            for row in await rows_of(bot_database, MessageDispatchDraftRecipient)
            if row.selected
        ]
        assert len(selected) == 3
        # 3. A preview is on screen...
        reply = session.combined_text()
        assert "3 group có thể gửi" in reply or "THÔNG BÁO" in reply
        assert_no_reported_answers(session)
        # 4. ...and nothing durable has been queued.
        assert await rows_of(bot_database, MessageDispatch) == []
        assert await rows_of(bot_database, OutboundMessage) == []

    async def test_confirming_creates_exactly_one_of_each_durable_row(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await self._register_three_active(bot_database)

        await dispatcher.feed_update(bot, owner_says(EVENING))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        assert len(await rows_of(bot_database, MessageDispatch)) == 1
        assert len(await rows_of(bot_database, MessageDispatchRecipient)) == 3
        assert len(await rows_of(bot_database, MessageDispatchPart)) == 1
        assert len(await rows_of(bot_database, MessageDispatchRecipientPart)) == 3
        assert len(await rows_of(bot_database, OutboundMessage)) == 3

        queued = await rows_of(bot_database, OutboundMessage)
        assert {row.telegram_chat_id for row in queued} == {SAYKENG, KET_BAN, TEST_GROUP}
        assert all(row.recipient_type is RecipientType.REGISTERED_CHAT for row in queued)
        # The handler queued; it did not send.
        assert cross_chat_sends(session, source_chat_id=OWNER_TELEGRAM_ID) == []

    async def test_every_row_carries_the_timestamps_its_model_expects(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """The offline mirror of the integration test.

        This cannot catch the production defect on its own - SQLite builds its
        schema from the models, which were already right - but it does pin the
        *models*, so a future change that drops a default fails here rather
        than in somebody's Telegram client.
        """
        bot, _session = bot_and_session
        await self._register_three_active(bot_database)
        await dispatcher.feed_update(bot, owner_says(EVENING))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        for model in (
            MessageDispatchDraft,
            MessageDispatchDraftRecipient,
            MessageDispatch,
            MessageDispatchPart,
            MessageDispatchRecipient,
            MessageDispatchRecipientPart,
        ):
            rows = await rows_of(bot_database, model)
            assert rows, model.__name__
            for row in rows:
                assert row.created_at is not None, model.__name__
                if hasattr(row, "updated_at"):
                    assert row.updated_at is not None, model.__name__

    async def test_a_duplicate_confirmation_is_idempotent(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        await self._register_three_active(bot_database)

        await dispatcher.feed_update(bot, owner_says(EVENING))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        assert len(await rows_of(bot_database, MessageDispatch)) == 1
        assert len(await rows_of(bot_database, MessageDispatchRecipient)) == 3
        assert len(await rows_of(bot_database, MessageDispatchRecipientPart)) == 3
        assert len(await rows_of(bot_database, OutboundMessage)) == 3


class TestPersistenceFailureIsAnswered:
    """A write that fails must still produce a reply.

    The reported symptom was not only the ``IntegrityError`` - it was that the
    exception escaped the handler, aiogram logged it, and the person who had
    just typed out a department announcement saw **nothing at all**. They had no
    way to know whether it had gone out.
    """

    async def test_a_failed_draft_write_tells_the_sender_nothing_was_sent(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from meobot.application import dispatch_draft_service

        bot, session = bot_and_session
        await register_the_three(bot_database)

        async def explode(*_args: object, **_kwargs: object) -> None:
            # The shape of the production failure: a NOT NULL violation raised
            # by the flush that writes the draft.
            raise IntegrityError("INSERT INTO message_dispatch_drafts", {}, Exception("null value"))

        monkeypatch.setattr(
            dispatch_draft_service.DispatchDraftService, "open", explode, raising=True
        )

        await dispatcher.feed_update(bot, owner_says(f"Gửi cho tất cả group: {ANNOUNCEMENT}"))

        reply = session.combined_text()
        assert "MeoBot chưa tạo được bản xem trước cho thông báo này." in reply
        assert "Chưa có nội dung nào được gửi." in reply
        assert "Bạn thử lại sau khi hệ thống được cập nhật nhé." in reply
        # And the claim it makes is true.
        assert await rows_of(bot_database, MessageDispatch) == []
        assert await rows_of(bot_database, OutboundMessage) == []

    async def test_a_failed_confirmation_says_nothing_was_sent(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sqlalchemy.exc import OperationalError

        from meobot.application import dispatch_service

        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(bot, owner_says(f"Gửi cho tất cả group: {ANNOUNCEMENT}"))
        session.requests.clear()

        async def explode(*_args: object, **_kwargs: object) -> None:
            raise OperationalError("INSERT INTO message_dispatches", {}, Exception("boom"))

        monkeypatch.setattr(dispatch_service.DispatchService, "confirm", explode, raising=True)
        await dispatcher.feed_update(bot, owner_says("Xác nhận"))

        reply = session.combined_text()
        assert "Chưa có nội dung nào được gửi." in reply
        assert await rows_of(bot_database, MessageDispatch) == []
        assert await rows_of(bot_database, OutboundMessage) == []

    async def test_a_failed_button_press_is_answered_too(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sqlalchemy.exc import OperationalError

        from meobot.application import dispatch_draft_service

        bot, session = bot_and_session
        await register_the_three(bot_database)
        await dispatcher.feed_update(
            bot, owner_says(f"Gửi cho group Saykeng và group Test: {ANNOUNCEMENT}")
        )
        button = drawn_button(session, "☑️ Chọn lại group")
        session.requests.clear()

        async def explode(*_args: object, **_kwargs: object) -> None:
            raise OperationalError("SELECT", {}, Exception("boom"))

        monkeypatch.setattr(
            dispatch_draft_service.DispatchDraftService, "active", explode, raising=True
        )
        await dispatcher.feed_update(bot, press(button))

        assert "Chưa có nội dung nào được gửi." in session.combined_text()
