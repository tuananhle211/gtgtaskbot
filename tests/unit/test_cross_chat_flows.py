"""Cross-chat workflows through the real Dispatcher.

The architectural claim these defend: **a handler never sends a business message
to another chat.** Every one of these drives a real aiogram update and then
checks that the only thing the transport saw was a reply into the *source* chat,
while the message for somebody else is sitting in the outbox waiting for a
worker.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.application.chat_registry_service import ChatRegistryService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.hr import HrRequest
from meobot.db.models.notifications import Announcement, OutboundMessage, TelegramChat
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.callbacks import MemberBinding, build
from meobot.domain.notifications.models import (
    ChatPurpose,
    PrivacyClassification,
    RecipientType,
)
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

BASE = 800_000
GROUP = -880_001
MEMBER = 980_001


def uid(offset: int) -> int:
    return BASE + offset


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


async def add_user(
    database: SqliteDatabase,
    *,
    telegram_id: int,
    role: Role = Role.EMPLOYEE,
    name: str = "Nguyễn Thị Linh",
    private: bool = True,
) -> uuid.UUID:
    async with database.transaction() as session:
        user = User(
            telegram_user_id=telegram_id,
            telegram_username="u",
            full_name=name,
            role=role,
            active=True,
            status=UserStatus.ACTIVE,
            telegram_private_chat_id=telegram_id if private else None,
            private_chat_available=private,
        )
        session.add(user)
        await session.flush()
        return user.id


async def register(
    database: SqliteDatabase,
    *,
    purpose: ChatPurpose = ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
    chat_id: int = GROUP,
    name: str = "Group thông báo toàn phòng",
) -> uuid.UUID:
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with database.transaction() as session:
        row = await ChatRegistryService(session).register(
            actor=owner,
            bot_identity=BOT_ID,
            telegram_chat_id=chat_id,
            chat_type="supergroup",
            telegram_title="Nhóm thử",
            display_name=name,
            purpose=purpose,
        )
        return row.id


async def rows_of(database: SqliteDatabase, model: type) -> list:
    async with database.session() as session:
        return list((await session.execute(select(model))).scalars().all())


def cross_chat_sends(session: RecordingSession, *, source_chat_id: int) -> list:
    """Any ``sendMessage`` aimed at a chat other than the one being replied to.

    This is the architectural assertion: there should never be one.
    """
    return [
        request
        for request in session.sent_of("SendMessage")
        if int(getattr(request, "chat_id", 0) or 0) != source_chat_id
    ]


# --- Registering a group ----------------------------------------------------
async def test_registering_a_group_needs_a_confirmation(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "đăng ký đây là group thông báo toàn phòng",
            update_id=uid(1),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
        ),
    )

    reply = session.combined_text()
    assert "Bạn đang đăng ký group" in reply
    assert "Group thông báo toàn phòng" in reply
    assert await rows_of(bot_database, TelegramChat) == [], "preview must not register"
    assert counting_llm.chat_calls == 0


async def test_confirming_registers_the_group(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "đăng ký đây là group thông báo toàn phòng",
            update_id=uid(2),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
        ),
    )
    # The real button, not a forged one: since 0.6.0a2.1 the confirmation
    # carries the id of the draft that was previewed, and that draft is what
    # holds the name and the purpose.
    data = drawn_button(session, "✅ Xác nhận")
    await dispatcher.feed_update(bot, press(data, update_id=uid(3), chat_id=GROUP))

    rows = await rows_of(bot_database, TelegramChat)
    assert len(rows) == 1
    assert rows[0].telegram_chat_id == GROUP
    assert rows[0].purpose is ChatPurpose.DEPARTMENT_ANNOUNCEMENTS
    assert "Đã đăng ký" in session.combined_text()


async def test_a_member_cannot_register_a_group_over_telegram(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await add_user(bot_database, telegram_id=MEMBER)

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "đăng ký đây là group thông báo toàn phòng",
            update_id=uid(4),
            user_id=MEMBER,
            chat_id=GROUP,
        ),
    )
    await dispatcher.feed_update(
        bot,
        press(
            button_for(
                "chat.register.confirm",
                settings=settings,
                telegram_user_id=MEMBER,
                chat_id=GROUP,
            ),
            update_id=uid(5),
            from_user_id=MEMBER,
            chat_id=GROUP,
        ),
    )

    assert await rows_of(bot_database, TelegramChat) == []


async def test_registration_is_refused_in_a_private_chat(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """A private chat claiming to be a group is exactly the attack shape."""
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_update(
            "đăng ký đây là group thông báo toàn phòng",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(6),
        ),
    )

    assert "trong chính group" in session.combined_text()
    assert await rows_of(bot_database, TelegramChat) == []


async def test_listing_registered_groups(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await register(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Cho tôi xem các group đã đăng ký.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(7),
        ),
    )

    reply = session.combined_text()
    # 0.6.0a3 replaced the one-line-per-group list with a card that also shows
    # each destination's state and the other names it answers to, and offers to
    # pick from it. What must not change is the two things this test was
    # written for: the group is named, and its numeric chat id is not.
    assert "CÁC GROUP ĐÃ ĐĂNG KÝ" in reply
    assert "Group thông báo toàn phòng" in reply
    assert "Trạng thái:" in reply
    assert str(GROUP) not in reply, "an internal chat id must not be shown"


# --- Announcements ----------------------------------------------------------
async def test_an_owner_announcement_previews_before_sending(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session
    await register(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: Chiều nay 15 giờ họp duyệt kế hoạch tháng.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(10),
        ),
    )

    reply = session.combined_text()
    assert "THÔNG BÁO" in reply
    assert "Nơi nhận" in reply
    assert "Group thông báo toàn phòng" in reply
    # Nothing has left this chat yet.
    assert cross_chat_sends(session, source_chat_id=OWNER_TELEGRAM_ID) == []
    assert await rows_of(bot_database, OutboundMessage) == []
    assert counting_llm.chat_calls == 0, "a template is not a generation"


async def test_confirming_queues_exactly_one_outbound_message(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The heart of the release: the handler queues, it does not send."""
    bot, session = bot_and_session
    await register(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: Chiều nay 15 giờ họp.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(11),
        ),
    )
    drafts = await rows_of(bot_database, Announcement)
    assert len(drafts) == 1

    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        press(
            button_for("announce.send", settings=settings, entity_id=drafts[0].id),
            update_id=uid(12),
        ),
    )

    outbound = await rows_of(bot_database, OutboundMessage)
    assert len(outbound) == 1
    assert outbound[0].telegram_chat_id == GROUP
    assert outbound[0].recipient_type is RecipientType.REGISTERED_CHAT
    # And still nothing was sent to the group by the handler itself.
    assert cross_chat_sends(session, source_chat_id=OWNER_TELEGRAM_ID) == []


async def test_confirming_twice_queues_once(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await register(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: Họp lúc 3 giờ.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(13),
        ),
    )
    drafts = await rows_of(bot_database, Announcement)
    data = button_for("announce.send", settings=settings, entity_id=drafts[0].id)

    await dispatcher.feed_update(bot, press(data, update_id=uid(14)))
    await dispatcher.feed_update(bot, press(data, update_id=uid(15)))

    assert len(await rows_of(bot_database, OutboundMessage)) == 1


async def test_a_member_cannot_broadcast(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await register(bot_database)
    await add_user(bot_database, telegram_id=MEMBER)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: nghỉ sớm nhé.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(16),
        ),
    )

    assert "chưa được phép" in session.combined_text()
    assert await rows_of(bot_database, Announcement) == []
    assert await rows_of(bot_database, OutboundMessage) == []


async def test_an_unregistered_destination_is_refused(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: họp gấp.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(17),
        ),
    )

    assert "chưa tìm thấy group" in session.combined_text()
    assert await rows_of(bot_database, OutboundMessage) == []


async def test_an_announcement_works_without_accents(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await register(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "thong bao cho toan phong: chieu nay 3 gio hop",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(18),
        ),
    )

    assert "Nơi nhận" in session.combined_text()
    assert len(await rows_of(bot_database, Announcement)) == 1


async def test_another_user_cannot_press_the_owners_send_button(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await register(bot_database)
    await add_user(bot_database, telegram_id=MEMBER)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: xin chào.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(19),
        ),
    )
    drafts = await rows_of(bot_database, Announcement)
    signed_for_owner = button_for("announce.send", settings=settings, entity_id=drafts[0].id)

    await dispatcher.feed_update(
        bot,
        press(signed_for_owner, update_id=uid(20), from_user_id=MEMBER, chat_id=MEMBER),
    )

    assert await rows_of(bot_database, OutboundMessage) == []


# --- Delivery status --------------------------------------------------------
async def test_delivery_status_reads_as_vietnamese(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    await register(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho toàn phòng: nội dung.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(21),
        ),
    )
    drafts = await rows_of(bot_database, Announcement)
    await dispatcher.feed_update(
        bot,
        press(
            button_for("announce.send", settings=settings, entity_id=drafts[0].id),
            update_id=uid(22),
        ),
    )

    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        make_update(
            "Tin nào đang gửi lỗi?",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=uid(23),
        ),
    )

    reply = session.combined_text()
    assert "Đang chờ gửi" in reply
    assert "PENDING" not in reply
    assert "announcement_published" not in reply


# --- HR routing through the outbox -----------------------------------------
async def test_an_hr_submission_queues_the_approver_card(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The handler no longer sends the card; it queues it."""
    bot, session = bot_and_session
    await add_user(bot_database, telegram_id=MEMBER)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin nghỉ buổi sáng vì có việc gia đình.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(30),
        ),
    )
    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        press(
            button_for("flow.confirm", settings=settings, telegram_user_id=MEMBER, chat_id=MEMBER),
            update_id=uid(31),
            from_user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )

    requests = await rows_of(bot_database, HrRequest)
    assert len(requests) == 1
    outbound = await rows_of(bot_database, OutboundMessage)
    assert len(outbound) == 1
    assert outbound[0].telegram_chat_id == OWNER_TELEGRAM_ID
    assert outbound[0].privacy_classification is PrivacyClassification.PERSONAL_PRIVATE
    # The handler replied only into the Member's own chat.
    assert cross_chat_sends(session, source_chat_id=MEMBER) == []


async def test_the_reason_is_queued_only_for_the_approver(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await add_user(bot_database, telegram_id=MEMBER)
    await register(bot_database, purpose=ChatPurpose.ATTENDANCE, chat_id=-880_002)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin nghỉ buổi sáng vì có việc gia đình.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(32),
        ),
    )
    await dispatcher.feed_update(
        bot,
        press(
            button_for("flow.confirm", settings=settings, telegram_user_id=MEMBER, chat_id=MEMBER),
            update_id=uid(33),
            from_user_id=MEMBER,
            chat_id=MEMBER,
        ),
    )

    outbound = await rows_of(bot_database, OutboundMessage)
    for message in outbound:
        if message.recipient_type is RecipientType.REGISTERED_CHAT:
            assert "gia đình" not in str(message.safe_payload_json)
