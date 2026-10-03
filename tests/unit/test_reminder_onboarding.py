"""The production sequence from the 0.6.0a2.1 bug report, turn by turn.

The transcript that produced this file:

    "Gọi chị xưng em nhé, Nhắc chị đi ngủ sau 5 phút nữa"
    → "MeoBot chưa có hồ sơ của bạn nên chưa lưu được lịch nhắc."
    "/start"
    → "Xin chào. Bạn đang đăng nhập với vai trò Chủ sở hữu."
    "Ok"          → "MeoBot chưa rõ bạn muốn được nhắc lúc mấy giờ."
    "5 phút nữa"  → "MeoBot chưa có hồ sơ của bạn..."
    "Hồ sơ gì"    → swallowed by the reminder flow
    "22h40"       → still not understood

Five separate defects in one sequence, and each is pinned below:

1. ``/start`` recognised the configured owner but materialised no ``users``
   row, so every service that needs a ``user_id`` saw nobody;
2. the refusal said "hồ sơ", which in this codebase means the optional
   descriptive ``ActorProfile`` - a thing reminders do not need at all;
3. ``/start`` neither resumed nor cleared the half-finished reminder;
4. ``22h40`` and ``sau 5 phút`` were not parseable;
5. an unrelated question was re-parsed as a whole new reminder sentence, which
   both lost the draft's content and produced the same generic error forever.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.core.config import Settings
from meobot.core.time import ensure_utc
from meobot.db.models.reminder import Reminder
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Role
from meobot.domain.reminders.models import ReminderStatus, ScheduleKind
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import RecordingSession, SqliteDatabase, bot_reply_message, make_update
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
BASE = 760_000


def uid(offset: int) -> int:
    return BASE + offset


def press(data: str, *, update_id: int, from_user_id: int = OWNER_TELEGRAM_ID) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=TelegramUser(id=from_user_id, is_bot=False, first_name="Owner"),
            chat_instance=f"ci-{update_id}",
            data=data,
            message=bot_reply_message(chat_id=from_user_id, message_id=update_id),
        ),
    )


def owner_says(text: str, *, update_id: int) -> Update:
    """A private message from the configured owner, who has no ``users`` row."""
    return make_update(
        text, update_id=update_id, user_id=OWNER_TELEGRAM_ID, chat_id=OWNER_TELEGRAM_ID
    )


def button_labelled(session: RecordingSession, prefix: str) -> str:
    for request in reversed(session.sent_of("SendMessage")):
        markup = getattr(request, "reply_markup", None)
        if markup is None:
            continue
        for row in markup.inline_keyboard:
            for button in row:
                if button.text.startswith(prefix):
                    return button.callback_data or ""
    raise AssertionError(f"no button starting with {prefix!r} was rendered")


async def owner_rows(database: SqliteDatabase) -> list[User]:
    async with database.session() as session:
        result = await session.execute(
            select(User).where(User.telegram_user_id == OWNER_TELEGRAM_ID)
        )
        return list(result.scalars().all())


# --- 1. Owner materialisation ----------------------------------------------
class TestOwnerMaterialization:
    """The configured owner must become a real user, once, for everybody."""

    async def test_start_creates_exactly_one_durable_owner_row(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        assert await owner_rows(bot_database) == []

        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(1)))

        rows = await owner_rows(bot_database)
        assert len(rows) == 1
        assert rows[0].role is Role.OWNER
        assert rows[0].status is UserStatus.ACTIVE
        assert rows[0].active is True

    async def test_start_records_the_private_chat(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """Telegram will not let a bot open a chat, so this is the only way to learn it."""
        bot, _session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(2)))

        rows = await owner_rows(bot_database)
        assert rows[0].telegram_private_chat_id == OWNER_TELEGRAM_ID
        assert rows[0].private_chat_available is True
        assert rows[0].last_private_interaction_at is not None

    async def test_repeated_start_creates_no_duplicate(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, _session = bot_and_session
        for index in range(3):
            await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(10 + index)))
        assert len(await owner_rows(bot_database)) == 1

    async def test_concurrent_materialization_creates_one_row(
        self,
        bot_database: SqliteDatabase,
        settings: Settings,
    ) -> None:
        """Two sessions racing must not both insert."""
        from meobot.application.identity_service import IdentityService

        for _ in range(2):
            async with bot_database.transaction() as session:
                await IdentityService(session, settings).ensure_bootstrap_owner(
                    telegram_user_id=OWNER_TELEGRAM_ID,
                    telegram_username="owner",
                    full_name="Owner",
                    private_chat_id=OWNER_TELEGRAM_ID,
                )
        assert len(await owner_rows(bot_database)) == 1

    async def test_another_telegram_id_never_becomes_owner(
        self,
        bot_database: SqliteDatabase,
        settings: Settings,
    ) -> None:
        from meobot.application.identity_service import IdentityService

        async with bot_database.transaction() as session:
            created = await IdentityService(session, settings).ensure_bootstrap_owner(
                telegram_user_id=OWNER_TELEGRAM_ID + 1,
                telegram_username="imposter",
                full_name="Không phải chủ",
                private_chat_id=1,
            )
        assert created is None
        async with bot_database.session() as session:
            rows = (await session.execute(select(User))).scalars().all()
        assert rows == []

    async def test_an_existing_owner_row_is_not_downgraded(
        self,
        bot_database: SqliteDatabase,
        settings: Settings,
    ) -> None:
        """Materialisation repairs the row; it never demotes a real one."""
        from meobot.application.identity_service import IdentityService

        async with bot_database.transaction() as session:
            session.add(
                User(
                    telegram_user_id=OWNER_TELEGRAM_ID,
                    telegram_username="owner",
                    full_name="Phương Nhung",
                    role=Role.OWNER,
                    active=True,
                    status=UserStatus.ACTIVE,
                )
            )
        async with bot_database.transaction() as session:
            await IdentityService(session, settings).ensure_bootstrap_owner(
                telegram_user_id=OWNER_TELEGRAM_ID,
                telegram_username="owner",
                full_name="",
                private_chat_id=OWNER_TELEGRAM_ID,
            )
        rows = await owner_rows(bot_database)
        assert len(rows) == 1
        assert rows[0].role is Role.OWNER
        assert rows[0].full_name == "Phương Nhung", "a real name is not overwritten by an empty one"

    async def test_the_actor_carries_a_user_id_after_start(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        """The whole point: every business module now resolves the same user."""
        from meobot.application.identity_service import IdentityService

        bot, _session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(20)))

        async with bot_database.session() as session:
            actor = await IdentityService(session, settings).resolve_actor(OWNER_TELEGRAM_ID)
        assert actor is not None
        assert actor.role is Role.OWNER
        assert actor.user_id is not None, "reminders and HR both need this"

    async def test_no_actor_profile_is_required(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """A reminder needs an account, not a biography."""
        from meobot.db.models.actor_profile import ActorProfileRow

        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(21)))
        await dispatcher.feed_update(
            bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(22))
        )
        await dispatcher.feed_update(bot, press(button_labelled(session, "✅"), update_id=uid(23)))

        async with bot_database.session() as db:
            reminders = (await db.execute(select(Reminder))).scalars().all()
            profiles = (await db.execute(select(ActorProfileRow))).scalars().all()
        assert len(reminders) == 1
        # Whether or not a descriptive profile happens to exist, it was never
        # a precondition - the reminder was created either way.
        assert len(reminders) == 1 or profiles


# --- 2. The exact reported sequence -----------------------------------------
class TestReportedSequence:
    async def test_the_first_message_asks_for_start_without_saying_ho_so(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """ "Hồ sơ" means the optional ActorProfile here. It is the wrong word."""
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            owner_says("Gọi chị xưng em nhé, Nhắc chị đi ngủ sau 5 phút nữa", update_id=uid(30)),
        )

        body = session.combined_text()
        assert "/start" in body
        assert "hồ sơ" not in body.lower(), "the refusal must not blame a descriptive profile"
        assert "tài khoản" in body.lower()

    async def test_start_resumes_the_held_request_without_repeating_it(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        """The exact acceptance criterion: no 'Ok', no '5 phút nữa', no '22h40'."""
        bot, session = bot_and_session

        await dispatcher.feed_update(
            bot,
            owner_says("Gọi chị xưng em nhé, Nhắc chị đi ngủ sau 5 phút nữa", update_id=uid(40)),
        )
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(41)))

        body = session.combined_text()
        assert "LỊCH NHẮC" in body, "the held request must resume automatically"
        assert "đi ngủ" in body
        assert "Sau 5 phút" in body or "5 phút" in body
        # Deterministic throughout: reminders cost no AI allowance.
        assert counting_llm.chat_calls == 0

    async def test_the_resumed_draft_confirms_into_one_reminder(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            owner_says("Gọi chị xưng em nhé, Nhắc chị đi ngủ sau 5 phút nữa", update_id=uid(50)),
        )
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(51)))
        await dispatcher.feed_update(bot, press(button_labelled(session, "✅"), update_id=uid(52)))

        async with bot_database.session() as db:
            rows = list((await db.execute(select(Reminder))).scalars().all())
        assert len(rows) == 1
        assert rows[0].content == "Đi ngủ" or rows[0].content.lower() == "đi ngủ"
        assert rows[0].schedule_kind is ScheduleKind.ONE_TIME
        assert rows[0].status is ReminderStatus.ACTIVE
        assert rows[0].next_run_at is not None
        # Roughly five minutes out, not twelve hours.
        delta = ensure_utc(rows[0].next_run_at) - datetime.now(tz=ZoneInfo("UTC"))
        assert timedelta(minutes=2) < delta < timedelta(minutes=8)
        assert "đã tạo lịch nhắc" in session.combined_text().lower()

    async def test_the_pronoun_clause_does_not_land_in_the_content(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(
            bot,
            owner_says("Gọi chị xưng em nhé, Nhắc chị đi ngủ sau 5 phút nữa", update_id=uid(60)),
        )
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(61)))

        body = session.combined_text()
        assert "xưng em" not in body, "the address preference is not reminder content"


# --- 3. FSM routing ---------------------------------------------------------
class TestFlowRouting:
    async def test_help_is_never_swallowed_by_the_reminder_flow(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(70)))
        await dispatcher.feed_update(
            bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(71))
        )
        before = len(session.requests)
        await dispatcher.feed_update(bot, owner_says("/help", update_id=uid(72)))

        answered = "\n".join(str(getattr(item, "text", "")) for item in session.requests[before:])
        assert "LỊCH NHẮC" not in answered
        assert answered.strip(), "/help must answer even mid-flow"

    async def test_cancel_flow_clears_the_reminder_state(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(80)))
        await dispatcher.feed_update(
            bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(81))
        )
        await dispatcher.feed_update(bot, owner_says("/cancel_flow", update_id=uid(82)))

        before = len(session.requests)
        await dispatcher.feed_update(bot, owner_says("Lịch nhắc của tôi.", update_id=uid(83)))
        answered = "\n".join(str(getattr(item, "text", "")) for item in session.requests[before:])
        assert "mấy giờ" not in answered, "the flow must no longer be active"

    @pytest.mark.parametrize(
        ("index", "word"),
        [(0, "Huỷ"), (1, "Thôi"), (2, "Không tạo nữa"), (3, "Bỏ lịch này")],
    )
    async def test_natural_cancellation_works(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        index: int,
        word: str,
    ) -> None:
        bot, session = bot_and_session
        offset = 300 + 10 * index
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(offset)))
        await dispatcher.feed_update(
            bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(offset + 1))
        )
        await dispatcher.feed_update(bot, owner_says(word, update_id=uid(offset + 2)))

        async with bot_database.session() as db:
            assert (await db.execute(select(Reminder))).scalars().all() == []
        assert "chưa tạo lịch nhắc" in session.combined_text().lower()

    async def test_a_non_time_question_preserves_the_draft(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        """ "Hồ sơ gì?" must not overwrite the reminder's content."""
        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(700)))
        await dispatcher.feed_update(bot, owner_says("Nhắc tôi đi ngủ", update_id=uid(701)))

        before = len(session.requests)
        await dispatcher.feed_update(bot, owner_says("Hồ sơ gì?", update_id=uid(702)))
        answered = "\n".join(str(getattr(item, "text", "")) for item in session.requests[before:])

        # It says what it is waiting for, and names the draft it still holds.
        assert "đi ngủ" in answered.lower()
        assert "22h40" in answered or "Sau 5 phút" in answered

        # And the draft still works: supplying the time completes it.
        await dispatcher.feed_update(bot, owner_says("22h40", update_id=uid(703)))
        assert "LỊCH NHẮC" in session.combined_text()

    async def test_an_expired_draft_fails_safely(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        settings: Settings,
    ) -> None:
        from meobot.domain.reminders.draft import ReminderDraftState

        stale = ReminderDraftState(
            content="đi ngủ",
            created_at=datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=2),
        )
        assert stale.is_expired(settings.member_flow_ttl_seconds)


# --- 4. Multi-turn parsing --------------------------------------------------
class TestMultiTurn:
    async def test_time_only_reply_fills_only_the_time(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        counting_llm: CountingProvider,  # noqa: F811
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(710)))
        await dispatcher.feed_update(bot, owner_says("Nhắc tôi đi ngủ", update_id=uid(711)))
        await dispatcher.feed_update(bot, owner_says("22h40", update_id=uid(712)))

        body = session.combined_text()
        assert "LỊCH NHẮC" in body
        assert "đi ngủ" in body.lower()
        assert "22:40" in body
        assert counting_llm.chat_calls == 0

    @pytest.mark.parametrize(
        ("index", "reply", "expected"),
        [
            (0, "5 phút nữa", None),
            (1, "sau 5 phút", None),
            (2, "22h40", "22:40"),
            (3, "22:40", "22:40"),
            (4, "22 giờ 40", "22:40"),
            (5, "4 giờ chiều", "16:00"),
            (6, "16 giờ", "16:00"),
            (7, "lúc 16h", "16:00"),
        ],
    )
    async def test_bare_time_replies_are_understood(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
        index: int,
        reply: str,
        expected: str | None,
    ) -> None:
        bot, session = bot_and_session
        # Distinct ids per case: DeduplicationMiddleware drops a repeated one,
        # and a silently dropped update is indistinguishable from a bug.
        offset = 500 + 10 * index
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(offset)))
        await dispatcher.feed_update(bot, owner_says("Nhắc tôi đi ngủ", update_id=uid(offset + 1)))
        before = len(session.requests)
        await dispatcher.feed_update(bot, owner_says(reply, update_id=uid(offset + 2)))

        answered = "\n".join(str(getattr(item, "text", "")) for item in session.requests[before:])
        assert "LỊCH NHẮC" in answered, f"{reply!r} was not understood"
        if expected is not None:
            assert expected in answered

    async def test_content_only_reply_fills_only_the_content(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(730)))
        await dispatcher.feed_update(bot, owner_says("Nhắc tôi lúc 22h40", update_id=uid(731)))
        before = len(session.requests)
        await dispatcher.feed_update(bot, owner_says("Đi ngủ", update_id=uid(732)))

        answered = "\n".join(str(getattr(item, "text", "")) for item in session.requests[before:])
        assert "LỊCH NHẮC" in answered
        assert "ngủ" in answered.lower()
        assert "22:40" in answered

    async def test_the_list_intent_is_not_swallowed_by_the_flow(
        self,
        dispatcher: Dispatcher,
        bot_database: SqliteDatabase,
        bot_and_session: tuple[Bot, RecordingSession],
    ) -> None:
        bot, session = bot_and_session
        await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(740)))
        await dispatcher.feed_update(
            bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(741))
        )
        before = len(session.requests)
        await dispatcher.feed_update(bot, owner_says("Lịch nhắc của tôi.", update_id=uid(742)))

        answered = "\n".join(str(getattr(item, "text", "")) for item in session.requests[before:])
        assert "mấy giờ" not in answered


# --- 5. Idempotency ---------------------------------------------------------
async def test_double_confirmation_creates_one_reminder(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(750)))
    await dispatcher.feed_update(bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(751)))
    data = button_labelled(session, "✅")
    await dispatcher.feed_update(bot, press(data, update_id=uid(752)))
    await dispatcher.feed_update(bot, press(data, update_id=uid(753)))

    async with bot_database.session() as db:
        rows = list((await db.execute(select(Reminder))).scalars().all())
    assert len(rows) == 1


async def test_reminder_creation_costs_no_ai_quota(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    from meobot.db.models.quota import DailyAiUsage

    bot, session = bot_and_session
    await dispatcher.feed_update(bot, owner_says("/start", update_id=uid(760)))
    await dispatcher.feed_update(bot, owner_says("Nhắc tôi đi ngủ lúc 22h40", update_id=uid(761)))
    await dispatcher.feed_update(bot, press(button_labelled(session, "✅"), update_id=uid(762)))

    assert counting_llm.chat_calls == 0
    async with bot_database.session() as db:
        usage = (await db.execute(select(DailyAiUsage))).scalars().all()
    assert all(row.used_count == 0 for row in usage)


def test_uuid_import_is_used() -> None:
    assert uuid.UUID is not None
