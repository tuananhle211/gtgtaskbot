"""The owner's approval buttons, end to end through the real Dispatcher.

These buttons grant access to a system, so the tests are mostly about the ways
a press must *fail*: pressed by somebody else, pressed after expiry, pressed
twice, pressed against a tampered payload.

The one that is easy to get wrong is the binding. Nothing about *who* or
*where* travels inside the 64 bytes of ``callback_data`` - it is all re-read
from the stored request and folded into the HMAC - so these tests check that
changing any of those facts invalidates the button.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.application.access_request_service import AccessRequestService, build_preview
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.access import GroupMemberResponsePolicy, PendingGuestAccessRequest
from meobot.db.models.deferred import DeferredGuestMessage
from meobot.db.models.user import User
from meobot.domain.access.callbacks import (
    CallbackBinding,
    build_access_callback,
    parse_access_callback,
    peek_request_id,
)
from meobot.domain.access.models import (
    AccessAction,
    GroupPolicyMode,
    PendingRequestStatus,
    UserStatus,
)
from meobot.domain.deferred.models import AuthorizationMode, DeferredMessageStatus
from meobot.domain.identity.models import Actor, Role
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import (
    BOT_ID,
    RecordingSession,
    SqliteDatabase,
    bot_reply_message,
    make_group_message,
    make_group_update,
)
from tests.unit.test_access_gate import CountingProvider, counting_llm, rows_of  # noqa: F401

BASE = 400_000
STRANGER = 910_001
GROUP_A = -2001


def uid(offset: int) -> int:
    return BASE + offset


async def open_request(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    offset: int,
    user_id: int = STRANGER,
    chat_id: int = GROUP_A,
    text: str = "cho mình hỏi về dịch vụ",
) -> None:
    """Drive a stranger's mention so a real pending request exists."""
    await dispatcher.feed_update(
        bot,
        make_group_update(
            text, update_id=uid(offset), user_id=user_id, chat_id=chat_id, message_id=offset
        ),
    )


async def stored_request(database: SqliteDatabase) -> PendingGuestAccessRequest:
    rows = await rows_of(database, PendingGuestAccessRequest)
    assert len(rows) == 1, f"expected exactly one request, found {len(rows)}"
    return rows[0]


def press(
    data: str,
    *,
    update_id: int,
    from_user_id: int = OWNER_TELEGRAM_ID,
    chat_id: int = OWNER_TELEGRAM_ID,
) -> Update:
    """Build a callback-query update, as Telegram would deliver a button press."""
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=TelegramUser(
                id=from_user_id, is_bot=False, first_name="Ai đó", username="ai_do"
            ),
            chat_instance=f"ci-{update_id}",
            data=data,
            message=bot_reply_message(chat_id=chat_id, message_id=update_id),
        ),
    )


def button(
    request: PendingGuestAccessRequest,
    action: AccessAction,
    settings: Settings,
    *,
    owner_telegram_id: int = OWNER_TELEGRAM_ID,
    expires_at: object = None,
) -> str:
    """Recreate one of the owner's buttons for this request."""
    return build_access_callback(
        action,
        secret=settings.callback_secret,
        request_id=request.id,
        binding=CallbackBinding(
            owner_telegram_id=owner_telegram_id,
            subject_telegram_id=request.requester_telegram_id,
            telegram_chat_id=request.telegram_chat_id,
            source_message_id=request.source_message_id,
        ),
        expires_at=expires_at or (utcnow() + timedelta(hours=1)),  # type: ignore[arg-type]
    )


# --- Callback signing -------------------------------------------------------
def test_a_button_fits_telegrams_limit(settings: Settings) -> None:
    data = build_access_callback(
        AccessAction.GRANT_GUEST,
        secret=settings.callback_secret,
        request_id=uuid.uuid4(),
        binding=CallbackBinding(
            owner_telegram_id=777000111,
            subject_telegram_id=9_999_999_999,
            telegram_chat_id=-1_002_345_678_901,
            source_message_id=999_999,
        ),
        expires_at=utcnow() + timedelta(hours=24),
    )
    assert len(data.encode("utf-8")) <= 64


def test_a_button_round_trips_against_its_binding(settings: Settings) -> None:
    request_id = uuid.uuid4()
    binding = CallbackBinding(
        owner_telegram_id=777000111,
        subject_telegram_id=555,
        telegram_chat_id=-999,
        source_message_id=42,
    )
    expires = utcnow() + timedelta(hours=1)
    data = build_access_callback(
        AccessAction.ANSWER_ONCE,
        secret=settings.callback_secret,
        request_id=request_id,
        binding=binding,
        expires_at=expires,
    )

    payload = parse_access_callback(data, secret=settings.callback_secret, binding=binding)
    assert payload is not None
    assert payload.action is AccessAction.ANSWER_ONCE
    assert payload.request_id == request_id
    assert peek_request_id(data) == ("ga", request_id)


@pytest.mark.parametrize(
    "field", ["owner_telegram_id", "subject_telegram_id", "telegram_chat_id", "source_message_id"]
)
def test_changing_any_bound_fact_invalidates_the_button(settings: Settings, field: str) -> None:
    """The binding is the point: none of these travel on the wire."""
    binding = CallbackBinding(
        owner_telegram_id=777000111,
        subject_telegram_id=555,
        telegram_chat_id=-999,
        source_message_id=42,
    )
    data = build_access_callback(
        AccessAction.GRANT_GUEST,
        secret=settings.callback_secret,
        request_id=uuid.uuid4(),
        binding=binding,
        expires_at=utcnow() + timedelta(hours=1),
    )
    tampered = binding.model_copy(update={field: getattr(binding, field) + 1})

    assert parse_access_callback(data, secret=settings.callback_secret, binding=tampered) is None


def test_a_button_from_another_deployment_is_rejected(settings: Settings) -> None:
    binding = CallbackBinding(
        owner_telegram_id=1, subject_telegram_id=2, telegram_chat_id=3, source_message_id=4
    )
    data = build_access_callback(
        AccessAction.GRANT_GUEST,
        secret="a-different-deployment",
        request_id=uuid.uuid4(),
        binding=binding,
        expires_at=utcnow() + timedelta(hours=1),
    )
    assert parse_access_callback(data, secret=settings.callback_secret, binding=binding) is None


@pytest.mark.parametrize("data", ["", "garbage", "ga|x|y", "ga|not-a-uuid|gg|1|deadbeef00"])
def test_malformed_button_data_is_rejected(settings: Settings, data: str) -> None:
    binding = CallbackBinding(
        owner_telegram_id=1, subject_telegram_id=2, telegram_chat_id=3, source_message_id=4
    )
    assert (
        peek_request_id(data) is None
        or parse_access_callback(data, secret=settings.callback_secret, binding=binding) is None
    )


# --- Pressing the buttons ---------------------------------------------------
async def test_a_non_owner_press_is_rejected_and_changes_nothing(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await open_request(dispatcher, bot, offset=1)
    request = await stored_request(bot_database)

    await dispatcher.feed_update(
        bot,
        press(
            button(request, AccessAction.GRANT_GUEST, settings),
            update_id=uid(2),
            from_user_id=555_555,
            chat_id=555_555,
        ),
    )

    assert await rows_of(bot_database, GroupMemberResponsePolicy) == []
    refreshed = await stored_request(bot_database)
    assert refreshed.status is PendingRequestStatus.OPEN


async def test_an_expired_button_cannot_be_executed(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    await open_request(dispatcher, bot, offset=3)
    request = await stored_request(bot_database)

    stale = button(
        request, AccessAction.GRANT_GUEST, settings, expires_at=utcnow() - timedelta(minutes=1)
    )
    await dispatcher.feed_update(bot, press(stale, update_id=uid(4)))

    assert await rows_of(bot_database, GroupMemberResponsePolicy) == []
    assert "không còn hiệu lực" in session.combined_text()


async def test_granting_guest_creates_a_bounded_window(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await open_request(dispatcher, bot, offset=5)
    request = await stored_request(bot_database)

    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.GRANT_GUEST, settings), update_id=uid(6))
    )

    policies = await rows_of(bot_database, GroupMemberResponsePolicy)
    assert len(policies) == 1
    policy = policies[0]
    assert policy.mode is GroupPolicyMode.GUEST
    assert policy.guest_question_limit == 10
    assert policy.telegram_chat_id == GROUP_A
    window = policy.guest_expires_at - policy.guest_granted_at
    assert timedelta(hours=23, minutes=59) < window <= timedelta(hours=24)
    # No account was created.
    assert await rows_of(bot_database, User) == []


async def test_pressing_grant_twice_grants_once(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Idempotency: a double tap, or a second owner device, must not stack."""
    bot, _ = bot_and_session
    await open_request(dispatcher, bot, offset=7)
    request = await stored_request(bot_database)
    data = button(request, AccessAction.GRANT_GUEST, settings)

    await dispatcher.feed_update(bot, press(data, update_id=uid(8)))
    async with bot_database.transaction() as session:
        row = (await session.execute(select(GroupMemberResponsePolicy))).scalar_one()
        row.guest_questions_used = 4
    await dispatcher.feed_update(bot, press(data, update_id=uid(9)))

    policies = await rows_of(bot_database, GroupMemberResponsePolicy)
    assert len(policies) == 1
    # The second press did not reset the counter: it did not run at all.
    assert policies[0].guest_questions_used == 4
    assert (await stored_request(bot_database)).status is PendingRequestStatus.APPROVED


async def test_answer_once_replies_and_grants_nothing(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """One reply to one message, and no continuing access of any kind.

    Since 0.6.0a2 the answer is produced by a worker rather than inside the
    owner's callback: pressing the button authorises the held question and
    returns. So what this asserts at the handler boundary is that the question
    was stored, is now authorised for exactly one chat-only answer, and that
    the owner's press cost a model call of zero - the generation happens later,
    in :mod:`meobot.tasks.guest_replay`.
    """
    bot, _session = bot_and_session
    await open_request(dispatcher, bot, offset=10)
    request = await stored_request(bot_database)
    assert counting_llm.chat_calls == 0, "the stranger's question must not reach a model"

    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.ANSWER_ONCE, settings), update_id=uid(11))
    )

    # The owner's button never waits on a model.
    assert counting_llm.chat_calls == 0

    held = await rows_of(bot_database, DeferredGuestMessage)
    assert len(held) == 1
    assert held[0].status is DeferredMessageStatus.AUTHORIZED_ONCE
    assert held[0].authorization_mode is AuthorizationMode.ANSWER_ONCE
    # The reply will be aimed at the message the owner actually read.
    assert held[0].original_chat_id == GROUP_A
    assert held[0].reply_to_message_id == 10

    # No Guest window, no account.
    assert await rows_of(bot_database, GroupMemberResponsePolicy) == []
    assert await rows_of(bot_database, User) == []


async def test_answer_once_does_not_let_the_next_message_through(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, _ = bot_and_session
    await open_request(dispatcher, bot, offset=12)
    request = await stored_request(bot_database)
    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.ANSWER_ONCE, settings), update_id=uid(13))
    )
    calls_after_the_one_reply = counting_llm.chat_calls

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "thế còn câu này", update_id=uid(14), user_id=STRANGER, chat_id=GROUP_A, message_id=99
        ),
    )

    assert counting_llm.chat_calls == calls_after_the_one_reply


async def test_adding_a_member_takes_two_presses(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The highest-risk action shows a preview before it commits."""
    bot, session = bot_and_session
    await open_request(dispatcher, bot, offset=15)
    request = await stored_request(bot_database)

    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.ADD_AS_MEMBER, settings), update_id=uid(16))
    )
    assert await rows_of(bot_database, User) == [], "the preview must not create the account"
    assert "Xác nhận thêm thành viên" in session.combined_text()

    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.CONFIRM_MEMBER, settings), update_id=uid(17))
    )

    users = await rows_of(bot_database, User)
    assert len(users) == 1
    assert users[0].telegram_user_id == STRANGER
    assert users[0].role is Role.EMPLOYEE
    assert users[0].status is UserStatus.ACTIVE
    assert "vai trò Nhân viên" in session.combined_text()


async def test_adding_a_member_does_not_carry_over_guest_questions(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await open_request(dispatcher, bot, offset=18)
    request = await stored_request(bot_database)
    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.GRANT_GUEST, settings), update_id=uid(19))
    )

    # A second request, then promote them.
    async with bot_database.transaction() as session:
        stored = await session.get(PendingGuestAccessRequest, request.id)
        assert stored is not None
        stored.status = PendingRequestStatus.OPEN
    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.ADD_AS_MEMBER, settings), update_id=uid(20))
    )
    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.CONFIRM_MEMBER, settings), update_id=uid(21))
    )

    policies = await rows_of(bot_database, GroupMemberResponsePolicy)
    assert policies[0].mode is GroupPolicyMode.INHERIT, "the Guest window must be closed"


async def test_ignore_in_group_silences_only_that_group(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session
    await open_request(dispatcher, bot, offset=22)
    request = await stored_request(bot_database)

    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.IGNORE_IN_GROUP, settings), update_id=uid(23))
    )

    policies = await rows_of(bot_database, GroupMemberResponsePolicy)
    assert policies[0].mode is GroupPolicyMode.IGNORE
    assert policies[0].telegram_chat_id == GROUP_A

    # Now they are invisible here...
    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "còn ai nghe không", update_id=uid(24), user_id=STRANGER, chat_id=GROUP_A
        ),
    )
    assert session.sent_texts() == []
    assert counting_llm.chat_calls == 0

    # ...but not in a different group.
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "ở đây thì sao",
            update_id=uid(25),
            user_id=STRANGER,
            chat_id=-2002,
            chat_title="Nhóm Khác",
        ),
    )
    open_requests = [
        row
        for row in await rows_of(bot_database, PendingGuestAccessRequest)
        if row.telegram_chat_id == -2002
    ]
    assert len(open_requests) == 1


async def test_ignore_once_closes_the_request_and_sets_a_cooldown(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await open_request(dispatcher, bot, offset=26)
    request = await stored_request(bot_database)

    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.IGNORE_ONCE, settings), update_id=uid(27))
    )

    refreshed = await stored_request(bot_database)
    assert refreshed.status is PendingRequestStatus.REJECTED
    assert refreshed.notify_cooldown_until is not None
    # No policy row: "not this time" is not "never again".
    assert await rows_of(bot_database, GroupMemberResponsePolicy) == []


async def test_a_refused_stranger_does_not_ping_the_owner_again(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The 24-hour cooldown, checked against the owner's inbox."""
    bot, session = bot_and_session
    await open_request(dispatcher, bot, offset=28)
    request = await stored_request(bot_database)
    await dispatcher.feed_update(
        bot, press(button(request, AccessAction.IGNORE_ONCE, settings), update_id=uid(29))
    )

    session.requests.clear()
    await open_request(dispatcher, bot, offset=30, text="cho mình hỏi lại")

    owner_messages = [
        item
        for item in session.sent_of("SendMessage")
        if int(getattr(item, "chat_id", 0) or 0) == OWNER_TELEGRAM_ID
    ]
    assert owner_messages == []


# --- Preview safety ---------------------------------------------------------
def test_the_preview_strips_secrets_before_the_owner_sees_them() -> None:
    """A stranger's first message is exactly where a pasted token turns up."""
    preview = build_preview("token của mình là sk-abcdef1234567890abcdef1234567890 nhé")
    assert "sk-abcdef1234567890abcdef1234567890" not in preview


def test_the_preview_is_bounded() -> None:
    preview = build_preview("dài " * 500)
    assert len(preview) <= 200


async def test_a_request_expires_by_arithmetic(bot_database: SqliteDatabase) -> None:
    """No sweeper task: a stale request is closed the moment it is read."""
    async with bot_database.transaction() as session:
        service = AccessRequestService(session)
        request, _ = await service.open_or_reuse(
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            chat_title="Nhóm",
            requester_telegram_id=STRANGER,
            requester_username=None,
            requester_display_name="Người Lạ",
            source_message_id=1,
            text="chào",
        )
        request.expires_at = utcnow() - timedelta(seconds=1)

    async with bot_database.transaction() as session:
        found = await AccessRequestService(session).open_request_for(
            bot_id=BOT_ID, chat_id=GROUP_A, requester_telegram_id=STRANGER
        )
    assert found is None

    rows = await rows_of(bot_database, PendingGuestAccessRequest)
    assert rows[0].status is PendingRequestStatus.EXPIRED


async def test_resolving_twice_is_idempotent(bot_database: SqliteDatabase) -> None:
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as session:
        service = AccessRequestService(session)
        request, _ = await service.open_or_reuse(
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            chat_title="Nhóm",
            requester_telegram_id=STRANGER,
            requester_username=None,
            requester_display_name="Người Lạ",
            source_message_id=1,
            text="chào",
        )
        await service.resolve(request, actor=owner, action=AccessAction.GRANT_GUEST)
        first_action = request.resolved_action
        await service.resolve(request, actor=owner, action=AccessAction.IGNORE_IN_GROUP)

    assert first_action is AccessAction.GRANT_GUEST
    rows = await rows_of(bot_database, PendingGuestAccessRequest)
    assert rows[0].resolved_action is AccessAction.GRANT_GUEST


def test_a_group_message_helper_addresses_the_bot() -> None:
    """Guard for the fixtures themselves: the mention must actually be there."""
    message = make_group_message(text="chào")
    assert message.text is not None
    assert message.text.startswith("@MeoBotTest ")
