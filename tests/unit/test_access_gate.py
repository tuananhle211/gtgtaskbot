"""The access gate: who MeoBot answers, where, and how often.

These tests drive the *real* aiogram Dispatcher with a recording transport, so
what they assert is what the deployed bot would do - middleware order, router
order, filters and all. Nothing here reaches Telegram or a real model: the
provider is the deterministic offline fake pinned in ``conftest``.

The properties being pinned down, in the order the gate checks them:

* a bot and an anonymous sender are dropped before anything is written;
* a suspended or revoked account is blocked globally, and is not announced to
  a group;
* ignore and mute are silent, leave no memory, and are scoped to one chat;
* a stranger who tags MeoBot produces exactly one owner request and no LLM call;
* a Guest is answered, is bounded by ten questions and 24 hours, and cannot
  reach a tool, a private chat, or another group;
* a Member gets twenty answers per local day across every chat, and the
  twenty-first never reaches a provider.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from sqlalchemy import select

from meobot.application.access_gate import AccessGate, IncomingUpdate
from meobot.application.group_policy_service import GroupPolicyService
from meobot.application.quota_service import QuotaService
from meobot.bot.texts import BASIC_ONLY_CHAT
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.access import GroupMemberResponsePolicy, PendingGuestAccessRequest
from meobot.db.models.conversation import ConversationMessage, ConversationThread
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.observed_user import ObservedTelegramUser
from meobot.db.models.quota import DailyAiUsage
from meobot.db.models.user import User
from meobot.domain.access.models import (
    AccessOutcome,
    GroupPolicyMode,
    GuestPrincipal,
    UserStatus,
)
from meobot.domain.access.quota import MEMBER_DEFAULT_DAILY_LIMIT
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.templates import render
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import (
    BOT_ID,
    RecordingSession,
    SqliteDatabase,
    bot_reply_message,
    make_group_update,
    make_update,
)

#: The shared Dispatcher's deduplication middleware remembers every update id
#: it has ever seen, so each test module works in a range of its own.
BASE = 300_000

STRANGER = 900_001
MEMBER = 900_002
GROUP_A = -1001
GROUP_B = -1002


def uid(offset: int) -> int:
    """A unique update id for this module."""
    return BASE + offset


# --- Helpers ----------------------------------------------------------------
async def add_user(
    database: SqliteDatabase,
    *,
    telegram_user_id: int,
    role: Role = Role.EMPLOYEE,
    status: UserStatus = UserStatus.ACTIVE,
    full_name: str = "Thành Viên",
) -> uuid.UUID:
    """Insert a registered user directly, bypassing the command surface."""
    async with database.transaction() as session:
        user = User(
            telegram_user_id=telegram_user_id,
            telegram_username="tv",
            full_name=full_name,
            role=role,
            active=status is UserStatus.ACTIVE,
            status=status,
        )
        session.add(user)
        await session.flush()
        return user.id


async def grant_guest(
    database: SqliteDatabase,
    *,
    telegram_user_id: int,
    chat_id: int = GROUP_A,
    question_limit: int = 10,
    duration: timedelta = timedelta(hours=24),
) -> uuid.UUID:
    """Give somebody a Guest window without going through the buttons."""
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with database.transaction() as session:
        row = await GroupPolicyService(session).grant_guest(
            actor=owner,
            bot_id=BOT_ID,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            question_limit=question_limit,
            duration=duration,
        )
        return row.id


async def rows_of(database: SqliteDatabase, model: type) -> list:
    async with database.session() as session:
        result = await session.execute(select(model))
        return list(result.scalars().all())


async def guest_row(database: SqliteDatabase, policy_id: uuid.UUID) -> GroupMemberResponsePolicy:
    async with database.session() as session:
        row = await session.get(GroupMemberResponsePolicy, policy_id)
        assert row is not None
        return row


class CountingProvider:
    """Wraps the fake provider and counts how often it is asked to generate."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.chat_calls = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def generate_chat_reply(self, request: object) -> object:
        self.chat_calls += 1
        return await self._inner.generate_chat_reply(request)  # type: ignore[attr-defined]


@pytest.fixture
def counting_llm(dispatcher: Dispatcher) -> CountingProvider:
    """Swap the dispatcher's provider for one that counts generations.

    The point of most of these tests is *that no provider call happened*, which
    is only observable by counting.
    """
    service = dispatcher.workflow_data["conversation_service"]
    original = service._llm
    counter = CountingProvider(original)
    service._llm = counter
    dispatcher.workflow_data["llm_provider"] = counter
    try:
        yield counter
    finally:
        service._llm = original
        dispatcher.workflow_data["llm_provider"] = original


# --- Sender screening -------------------------------------------------------
async def test_a_bot_sender_is_dropped_before_anything_is_written(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Two bots talking to each other is a loop, not a conversation."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "xin chào", update_id=uid(1), user_id=999_999, is_bot=True, full_name="Other Bot"
        ),
    )

    assert session.sent_texts() == []
    assert counting_llm.chat_calls == 0
    assert await rows_of(bot_database, PendingGuestAccessRequest) == []


async def test_an_observed_user_is_recorded_but_is_not_a_system_user(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """Being seen is not being registered."""
    bot, _ = bot_and_session
    await dispatcher.feed_update(
        bot, make_group_update("có ai không", update_id=uid(2), user_id=STRANGER)
    )

    observed = await rows_of(bot_database, ObservedTelegramUser)
    assert [row.telegram_user_id for row in observed] == [STRANGER]
    assert await rows_of(bot_database, User) == []


async def test_a_renamed_account_stays_one_identity(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """A new username must update the row, never create a second one."""
    bot, _ = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update("chào", update_id=uid(3), user_id=STRANGER, username="ten_cu"),
    )
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "chào lần nữa",
            update_id=uid(4),
            user_id=STRANGER,
            username="ten_moi",
            message_id=2,
        ),
    )

    observed = await rows_of(bot_database, ObservedTelegramUser)
    assert len(observed) == 1
    assert observed[0].latest_username == "ten_moi"


# --- Unknown people ---------------------------------------------------------
async def test_an_unknown_mention_creates_one_request_and_calls_no_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """The core promise: a stranger's question is never sent to a model."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update("TasksBot ơi giúp mình với", update_id=uid(10), user_id=STRANGER),
    )

    requests = await rows_of(bot_database, PendingGuestAccessRequest)
    assert len(requests) == 1
    assert requests[0].requester_telegram_id == STRANGER
    assert counting_llm.chat_calls == 0
    # Nothing the stranger said became conversation memory.
    assert await rows_of(bot_database, ConversationMessage) == []
    assert await rows_of(bot_database, ConversationThread) == []
    # ...and no account appeared.
    assert await rows_of(bot_database, User) == []
    assert session.sent_texts(), "the stranger got no acknowledgement at all"


async def test_a_reply_to_meobot_also_counts_as_addressing_it(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, _ = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "thế còn cái này thì sao",
            update_id=uid(11),
            user_id=STRANGER,
            mention=False,
            reply_to=bot_reply_message(),
        ),
    )

    assert len(await rows_of(bot_database, PendingGuestAccessRequest)) == 1


async def test_overheard_group_chatter_creates_nothing(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """A stranger talking to colleagues must not summon the owner."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "mai họp lúc mấy giờ", update_id=uid(12), user_id=STRANGER, mention=False
        ),
    )

    assert await rows_of(bot_database, PendingGuestAccessRequest) == []
    assert session.sent_texts() == []


async def test_repeated_mentions_do_not_spam_the_owner(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """One open request per person per group; repeats only bump the counter."""
    bot, session = bot_and_session
    for index in range(4):
        await dispatcher.feed_update(
            bot,
            make_group_update(
                f"cho mình hỏi lần {index}",
                update_id=uid(20 + index),
                user_id=STRANGER,
                message_id=index + 1,
            ),
        )

    requests = await rows_of(bot_database, PendingGuestAccessRequest)
    assert len(requests) == 1
    assert requests[0].mention_count == 4

    # Exactly one queued copy for the owner, however many times the stranger
    # mentions the bot. Since 0.6.0a2 the handler queues it rather than sending
    # it, so the transport must have seen nothing addressed to the owner.
    queued = await rows_of(bot_database, OutboundMessage)
    owner_rows = [
        row for row in queued if row.event_type == NotificationEvent.ACCESS_REQUEST_SUBMITTED.value
    ]
    assert len(owner_rows) == 1
    assert owner_rows[0].telegram_chat_id == OWNER_TELEGRAM_ID
    assert not [
        request
        for request in session.sent_of("SendMessage")
        if int(getattr(request, "chat_id", 0) or 0) == OWNER_TELEGRAM_ID
    ], "the owner's copy must go through the outbox, not straight out of the handler"


async def test_the_owner_notification_names_the_person_and_the_group(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, _session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "mình cần tư vấn nội dung", update_id=uid(30), user_id=STRANGER, full_name="Người Lạ"
        ),
    )

    # The message the owner will receive lives in the outbox. Rendering the
    # stored payload is what they will actually read.
    queued = await rows_of(bot_database, OutboundMessage)
    owner_rows = [
        row for row in queued if row.event_type == NotificationEvent.ACCESS_REQUEST_SUBMITTED.value
    ]
    assert len(owner_rows) == 1
    owner_text = render(owner_rows[0].template_key, dict(owner_rows[0].safe_payload_json))

    assert "Người Lạ" in owner_text
    assert "Nhóm Nội Dung" in owner_text
    assert "mình cần tư vấn nội dung" in owner_text
    # A raw Telegram id is never printed into a message that may be forwarded.
    assert str(STRANGER) not in owner_text


# --- Guest ------------------------------------------------------------------
async def test_a_guest_is_answered_in_the_approved_group(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    bot, session = bot_and_session
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)

    await dispatcher.feed_update(
        bot, make_group_update("chào TasksBot", update_id=uid(40), user_id=STRANGER)
    )

    assert counting_llm.chat_calls == 1
    assert session.sent_texts()
    row = await guest_row(bot_database, policy_id)
    assert row.guest_questions_used == 1
    assert row.guest_reserved_count == 0


async def test_a_guest_grant_defaults_to_ten_questions_and_24_hours(
    bot_database: SqliteDatabase,
) -> None:
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)
    row = await guest_row(bot_database, policy_id)

    assert row.guest_question_limit == 10
    assert row.guest_expires_at is not None
    window = row.guest_expires_at - row.guest_granted_at
    assert timedelta(hours=23, minutes=59) < window <= timedelta(hours=24)


async def test_a_guest_in_group_a_is_a_stranger_in_group_b(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """A Guest grant is scoped to exactly one chat."""
    bot, _ = bot_and_session
    await grant_guest(bot_database, telegram_user_id=STRANGER, chat_id=GROUP_A)

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "chào", update_id=uid(41), user_id=STRANGER, chat_id=GROUP_B, chat_title="Nhóm Khác"
        ),
    )

    assert counting_llm.chat_calls == 0
    requests = await rows_of(bot_database, PendingGuestAccessRequest)
    assert [row.telegram_chat_id for row in requests] == [GROUP_B]


async def test_a_guest_cannot_use_private_chat(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    bot, session = bot_and_session
    await grant_guest(bot_database, telegram_user_id=STRANGER)

    await dispatcher.feed_update(
        bot, make_update("chào TasksBot", user_id=STRANGER, update_id=uid(42), chat_id=STRANGER)
    )

    assert counting_llm.chat_calls == 0
    assert "chưa được đăng ký" in session.combined_text()


async def test_a_guest_asking_for_internal_data_is_refused_for_free(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """A deterministic denial costs no provider call and no Guest question."""
    bot, session = bot_and_session
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)

    await dispatcher.feed_update(
        bot,
        make_group_update("/pending_scripts", update_id=uid(43), user_id=STRANGER, mention=False),
    )

    assert counting_llm.chat_calls == 0
    assert "Guest chỉ có thể trò chuyện" in session.combined_text()
    row = await guest_row(bot_database, policy_id)
    assert row.guest_questions_used == 0
    assert row.guest_reserved_count == 0


async def test_a_guest_gets_exactly_ten_answers_and_the_eleventh_calls_no_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    bot, _ = bot_and_session
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)

    for index in range(10):
        await dispatcher.feed_update(
            bot,
            make_group_update(
                f"câu hỏi {index}",
                update_id=uid(50 + index),
                user_id=STRANGER,
                message_id=index + 1,
            ),
        )
    assert counting_llm.chat_calls == 10
    row = await guest_row(bot_database, policy_id)
    assert row.guest_questions_used == 10

    # The eleventh: no generation, and a fresh approval request instead.
    await dispatcher.feed_update(
        bot,
        make_group_update("câu hỏi 11", update_id=uid(61), user_id=STRANGER, message_id=11),
    )
    assert counting_llm.chat_calls == 10
    assert len(await rows_of(bot_database, PendingGuestAccessRequest)) == 1


async def test_an_expired_guest_is_blocked_even_with_questions_left(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Whichever limit runs out first ends access - here it is the clock."""
    bot, _ = bot_and_session
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)
    async with bot_database.transaction() as session:
        row = await session.get(GroupMemberResponsePolicy, policy_id)
        assert row is not None
        row.guest_expires_at = utcnow() - timedelta(minutes=1)

    await dispatcher.feed_update(
        bot, make_group_update("còn lượt mà", update_id=uid(70), user_id=STRANGER)
    )

    assert counting_llm.chat_calls == 0
    row = await guest_row(bot_database, policy_id)
    assert row.guest_questions_used == 0


async def test_a_provider_failure_costs_a_guest_nothing(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """Reserve, fail, release: the allowance is untouched."""
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)

    async with bot_database.transaction() as session:
        quota = QuotaService(session, settings)
        reservation = await quota.reserve_guest(policy_id=policy_id)
        assert reservation is not None
    async with bot_database.transaction() as session:
        await QuotaService(session, settings).release(reservation)

    row = await guest_row(bot_database, policy_id)
    assert row.guest_questions_used == 0
    assert row.guest_reserved_count == 0


async def test_two_simultaneous_guests_cannot_share_the_last_slot(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """The conditional UPDATE is what makes the final question atomic."""
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER, question_limit=1)

    async with bot_database.transaction() as session:
        quota = QuotaService(session, settings)
        first = await quota.reserve_guest(policy_id=policy_id)
        second = await quota.reserve_guest(policy_id=policy_id)

    assert first is not None
    assert second is None


async def test_a_guest_grant_survives_a_restart(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """The allowance lives in PostgreSQL, not in process memory."""
    policy_id = await grant_guest(bot_database, telegram_user_id=STRANGER)
    async with bot_database.transaction() as session:
        reservation = await QuotaService(session, settings).reserve_guest(policy_id=policy_id)
        await QuotaService(session, settings).commit(reservation)

    # A "restart" is a brand new service over the same rows.
    async with bot_database.session() as session:
        guest = await GroupPolicyService(session).active_guest(
            bot_id=BOT_ID, chat_id=GROUP_A, telegram_user_id=STRANGER
        )
    assert guest is not None
    assert guest.questions_used == 1
    assert guest.questions_remaining == 9


async def test_an_ignored_guest_message_never_reaches_memory_or_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    bot, session = bot_and_session
    await grant_guest(bot_database, telegram_user_id=STRANGER)
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as active:
        await GroupPolicyService(active).set_mode(
            actor=owner,
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            telegram_user_id=STRANGER,
            mode=GroupPolicyMode.IGNORE,
        )

    await dispatcher.feed_update(
        bot, make_group_update("còn ai nghe không", update_id=uid(80), user_id=STRANGER)
    )

    assert counting_llm.chat_calls == 0
    assert session.sent_texts() == []
    assert await rows_of(bot_database, ConversationMessage) == []


# --- Group policies ---------------------------------------------------------
async def test_ignore_in_one_group_does_not_affect_another(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Ignored in A means silence in A; B still answers - with the basic bot,
    since a member has no AI chat on Telegram."""
    bot, transport = bot_and_session
    await add_user(bot_database, telegram_user_id=MEMBER)
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as session:
        await GroupPolicyService(session).set_mode(
            actor=owner,
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            telegram_user_id=MEMBER,
            mode=GroupPolicyMode.IGNORE,
        )

    await dispatcher.feed_update(
        bot, make_group_update("có nghe không", update_id=uid(90), user_id=MEMBER, chat_id=GROUP_A)
    )
    assert transport.sent_texts() == []

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "còn ở đây thì sao",
            update_id=uid(91),
            user_id=MEMBER,
            chat_id=GROUP_B,
            chat_title="Nhóm Khác",
        ),
    )
    assert transport.sent_texts() == [BASIC_ONLY_CHAT]
    assert counting_llm.chat_calls == 0


async def test_a_mute_expires_without_a_scheduled_task(bot_database: SqliteDatabase) -> None:
    """Expiry is arithmetic at read time, so nothing can be late."""
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as session:
        service = GroupPolicyService(session)
        await service.set_mode(
            actor=owner,
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            telegram_user_id=MEMBER,
            mode=GroupPolicyMode.MUTE_UNTIL,
            muted_until=utcnow() + timedelta(hours=1),
        )
        during = await service.resolve_mode(bot_id=BOT_ID, chat_id=GROUP_A, telegram_user_id=MEMBER)
        after = await service.resolve_mode(
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            telegram_user_id=MEMBER,
            now=utcnow() + timedelta(hours=2),
        )

    assert during is GroupPolicyMode.MUTE_UNTIL
    assert after is GroupPolicyMode.INHERIT


async def test_allow_does_not_override_suspension(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Lifecycle is global and is checked first; a chat setting cannot widen it."""
    bot, session = bot_and_session
    await add_user(bot_database, telegram_user_id=MEMBER, status=UserStatus.SUSPENDED)
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as active:
        await GroupPolicyService(active).set_mode(
            actor=owner,
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            telegram_user_id=MEMBER,
            mode=GroupPolicyMode.ALLOW,
        )

    await dispatcher.feed_update(
        bot, make_group_update("cho mình hỏi", update_id=uid(100), user_id=MEMBER)
    )

    assert counting_llm.chat_calls == 0
    # ...and their status was not announced to their colleagues.
    assert session.sent_texts() == []


async def test_a_suspended_user_is_told_privately(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    bot, session = bot_and_session
    await add_user(bot_database, telegram_user_id=MEMBER, status=UserStatus.SUSPENDED)

    await dispatcher.feed_update(
        bot, make_update("chào", user_id=MEMBER, chat_id=MEMBER, update_id=uid(101))
    )

    assert counting_llm.chat_calls == 0
    assert "tạm khoá" in session.combined_text()


async def test_a_revoked_user_is_blocked_in_every_chat(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    bot, _ = bot_and_session
    await add_user(bot_database, telegram_user_id=MEMBER, status=UserStatus.REVOKED)

    for index, chat in enumerate((GROUP_A, GROUP_B)):
        await dispatcher.feed_update(
            bot,
            make_group_update(
                "còn dùng được không",
                update_id=uid(110 + index),
                user_id=MEMBER,
                chat_id=chat,
            ),
        )
    await dispatcher.feed_update(
        bot, make_update("còn dùng được không", user_id=MEMBER, chat_id=MEMBER, update_id=uid(112))
    )

    assert counting_llm.chat_calls == 0


# --- Member quota -----------------------------------------------------------
async def test_a_member_chat_never_reaches_a_model_or_spends_a_slot(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Only the owner chats with the AI on Telegram.

    A member's free text - past what used to be their daily allowance - gets
    the basic-bot pointer every time, never a model call and never a charge.
    """
    bot, session = bot_and_session
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)

    for index in range(MEMBER_DEFAULT_DAILY_LIMIT + 1):
        await dispatcher.feed_update(
            bot,
            make_update(
                f"trò chuyện {index}",
                user_id=MEMBER,
                chat_id=MEMBER,
                update_id=uid(200 + index),
                message_id=index + 1,
            ),
        )

    assert counting_llm.chat_calls == 0
    assert session.sent_texts() == [BASIC_ONLY_CHAT] * (MEMBER_DEFAULT_DAILY_LIMIT + 1)
    async with bot_database.session() as active:
        rows = (
            (await active.execute(select(DailyAiUsage).where(DailyAiUsage.user_id == user_id)))
            .scalars()
            .all()
        )
    assert all(row.used_count == 0 and row.reserved_count == 0 for row in rows)


async def test_an_exhausted_allowance_is_not_announced_to_a_member(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
    settings: Settings,
) -> None:
    """Spent yesterday's way, today: "hết lượt" would point at a chat they lack."""
    bot, session = bot_and_session
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)
    async with bot_database.transaction() as active:
        row = await QuotaService(active, settings).usage_row(user_id=user_id)
        row.used_count = MEMBER_DEFAULT_DAILY_LIMIT

    await dispatcher.feed_update(
        bot, make_update("một câu nữa", user_id=MEMBER, chat_id=MEMBER, update_id=uid(230))
    )

    assert session.sent_texts() == [BASIC_ONLY_CHAT]
    assert counting_llm.chat_calls == 0


async def test_a_member_is_charged_nothing_in_any_chat(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Private chat and every group alike: no model, no ledger usage."""
    bot, _ = bot_and_session
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)

    await dispatcher.feed_update(
        bot, make_update("riêng tư", user_id=MEMBER, chat_id=MEMBER, update_id=uid(240))
    )
    await dispatcher.feed_update(
        bot, make_group_update("nhóm A", update_id=uid(241), user_id=MEMBER, chat_id=GROUP_A)
    )
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "nhóm B", update_id=uid(242), user_id=MEMBER, chat_id=GROUP_B, chat_title="Nhóm Khác"
        ),
    )

    assert counting_llm.chat_calls == 0
    async with bot_database.session() as active:
        rows = (
            (await active.execute(select(DailyAiUsage).where(DailyAiUsage.user_id == user_id)))
            .scalars()
            .all()
        )
    assert len(rows) <= 1, "a second ledger row would be a second allowance"
    assert all(row.used_count == 0 for row in rows)


async def test_operational_commands_still_work_after_the_quota_runs_out(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Chat quota meters conversation, never the member's business permissions."""
    bot, session = bot_and_session
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)
    async with bot_database.transaction() as active:
        quota = QuotaService(active, settings)
        row = await quota.usage_row(user_id=user_id)
        row.used_count = MEMBER_DEFAULT_DAILY_LIMIT

    await dispatcher.feed_update(
        bot, make_update("/help", user_id=MEMBER, chat_id=MEMBER, update_id=uid(250))
    )

    assert "TasksBot" in session.combined_text()


async def test_a_business_tool_turn_does_not_consume_chat_quota(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """A reserved slot released after a tool turn leaves the ledger untouched."""
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)

    async with bot_database.transaction() as session:
        quota = QuotaService(session, settings)
        reservation, _ = await quota.reserve_member(user_id=user_id, role=Role.EMPLOYEE)
    async with bot_database.transaction() as session:
        await QuotaService(session, settings).release(reservation)

    async with bot_database.session() as session:
        row = (
            await session.execute(select(DailyAiUsage).where(DailyAiUsage.user_id == user_id))
        ).scalar_one()
    assert row.used_count == 0
    assert row.reserved_count == 0


async def test_a_duplicate_update_consumes_at_most_one_slot(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,
) -> None:
    """Telegram's at-least-once delivery must not be answered - or billed - twice.

    Run as the owner, the only account whose free text reaches the model.
    """
    bot, session = bot_and_session
    await add_user(bot_database, telegram_user_id=MEMBER, role=Role.OWNER)

    update = make_update("nhắc lại", user_id=MEMBER, chat_id=MEMBER, update_id=uid(260))
    await dispatcher.feed_update(bot, update)
    await dispatcher.feed_update(bot, update)

    assert counting_llm.chat_calls == 1
    assert len(session.sent_texts()) == 1


async def test_two_simultaneous_members_cannot_share_the_last_slot(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)
    async with bot_database.transaction() as session:
        row = await QuotaService(session, settings).usage_row(user_id=user_id)
        row.used_count = MEMBER_DEFAULT_DAILY_LIMIT - 1

    async with bot_database.transaction() as session:
        quota = QuotaService(session, settings)
        first, _ = await quota.reserve_member(user_id=user_id, role=Role.EMPLOYEE)
        second, verdict = await quota.reserve_member(user_id=user_id, role=Role.EMPLOYEE)

    assert first is not None
    assert second is None
    assert not verdict.allowed


async def test_the_local_midnight_reset_needs_no_scheduled_task(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """A new local day is a new key, so a fresh row starts at zero."""
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)
    now = utcnow()

    async with bot_database.transaction() as session:
        quota = QuotaService(session, settings)
        today = await quota.usage_row(user_id=user_id, now=now)
        today.used_count = MEMBER_DEFAULT_DAILY_LIMIT
        await session.flush()
        exhausted = await quota.inspect_member(user_id=user_id, role=Role.EMPLOYEE, now=now)
        tomorrow = await quota.inspect_member(
            user_id=user_id, role=Role.EMPLOYEE, now=now + timedelta(days=1)
        )

    assert not exhausted.allowed
    assert tomorrow.allowed
    assert tomorrow.used == 0


async def test_usage_survives_a_restart(bot_database: SqliteDatabase, settings: Settings) -> None:
    user_id = await add_user(bot_database, telegram_user_id=MEMBER)
    async with bot_database.transaction() as session:
        quota = QuotaService(session, settings)
        reservation, _ = await quota.reserve_member(user_id=user_id, role=Role.EMPLOYEE)
        await quota.commit(reservation)

    async with bot_database.session() as session:
        verdict = await QuotaService(session, settings).inspect_member(
            user_id=user_id, role=Role.EMPLOYEE
        )
    assert verdict.used == 1


@pytest.mark.parametrize("role", [Role.TEAM_LEAD, Role.ADMIN, Role.OWNER])
async def test_privileged_roles_are_not_metered(
    bot_database: SqliteDatabase, settings: Settings, role: Role
) -> None:
    user_id = await add_user(bot_database, telegram_user_id=MEMBER, role=role)
    async with bot_database.session() as session:
        verdict = await QuotaService(session, settings).inspect_member(user_id=user_id, role=role)
    assert verdict.remaining == -1


# --- The gate as a unit -----------------------------------------------------
def _update(**overrides: object) -> IncomingUpdate:
    base = {
        "bot_id": BOT_ID,
        "chat_id": GROUP_A,
        "chat_type": "supergroup",
        "telegram_user_id": STRANGER,
        "is_bot": False,
        "message_id": 1,
        "text": "xin chào",
        "is_command": False,
        "addressed_to_bot": True,
    }
    base.update(overrides)
    return IncomingUpdate(**base)  # type: ignore[arg-type]


async def test_the_gate_drops_an_anonymous_sender(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """An anonymous admin has no user id, so there is nobody to authorise."""
    result = await AccessGate(bot_database, settings).evaluate(  # type: ignore[arg-type]
        _update(telegram_user_id=None)
    )
    assert result.outcome is AccessOutcome.SILENT
    assert result.decision.reason == "anonymous_sender"


async def test_the_gate_defers_for_an_unknown_private_account(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """The pre-existing "not registered" answer is still the right one."""
    result = await AccessGate(bot_database, settings).evaluate(  # type: ignore[arg-type]
        _update(chat_type="private", chat_id=STRANGER)
    )
    assert result.outcome is AccessOutcome.DEFER


async def test_a_guest_principal_has_no_role_at_all() -> None:
    """The type-level guarantee: there is no permission set to look up."""
    guest = GuestPrincipal(
        policy_id=uuid.uuid4(),
        telegram_user_id=STRANGER,
        telegram_chat_id=GROUP_A,
        granted_at=utcnow(),
        expires_at=utcnow() + timedelta(hours=24),
    )
    assert not hasattr(guest, "role")
    assert not hasattr(guest, "user_id")
    assert not isinstance(guest, Actor)
