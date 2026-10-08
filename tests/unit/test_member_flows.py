"""Member interaction through the real Dispatcher: buttons, flows, quota.

These drive actual aiogram updates against the real router stack with a
recording transport, so what they assert is what a Member would see - including
which router won, whether a chat slot was spent, and whether a button belonged
to the person who pressed it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from meobot.application.member_list_service import MemberListService, read_position
from meobot.application.quota_service import QuotaService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.hr import HrRequest, MemberListContext
from meobot.db.models.quota import DailyAiUsage
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.hr.models import HrRequestStatus, HrRequestType
from meobot.domain.identity.models import Role
from meobot.domain.member import copy
from meobot.domain.member.callbacks import MemberBinding, build, parse
from meobot.domain.member.normalization import normalize
from tests.fakes import BOT_ID, RecordingSession, SqliteDatabase, bot_reply_message, make_update
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

BASE = 700_000
MEMBER = 960_001
OTHER = 960_002


def uid(offset: int) -> int:
    return BASE + offset


async def add_member(
    database: SqliteDatabase, *, telegram_id: int = MEMBER, name: str = "Nguyễn Thị Linh"
) -> uuid.UUID:
    async with database.transaction() as session:
        user = User(
            telegram_user_id=telegram_id,
            telegram_username="linh",
            full_name=name,
            role=Role.EMPLOYEE,
            active=True,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        return user.id


def press(
    data: str, *, update_id: int, from_user_id: int = MEMBER, chat_id: int = MEMBER
) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=TelegramUser(
                id=from_user_id, is_bot=False, first_name="Linh", username="linh"
            ),
            chat_instance=f"ci-{update_id}",
            data=data,
            message=bot_reply_message(chat_id=chat_id, message_id=update_id),
        ),
    )


def button_for(
    action: str,
    *,
    settings: Settings,
    telegram_user_id: int = MEMBER,
    chat_id: int = MEMBER,
    entity_id: uuid.UUID | None = None,
    version: int = 0,
    expires_at: datetime | None = None,
) -> str:
    return build(
        action,
        secret=settings.callback_secret,
        binding=MemberBinding(bot_id=BOT_ID, telegram_user_id=telegram_user_id, chat_id=chat_id),
        expires_at=expires_at or (utcnow() + timedelta(hours=1)),
        entity_id=entity_id,
        version=version,
    )


# --- Home and help ----------------------------------------------------------
async def test_bat_dau_opens_the_member_home(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """The landing card, and it costs nothing."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot, make_update("Bắt đầu", user_id=MEMBER, chat_id=MEMBER, update_id=uid(1))
    )

    reply = session.combined_text()
    assert "Chào Linh" in reply
    assert "Bạn muốn làm gì?" in reply
    assert counting_llm.chat_calls == 0


@pytest.mark.parametrize(
    ("text", "offset"),
    [
        ("Tôi cần làm gì?", 2),
        ("TasksBot ơi", 3),
        ("Mở menu", 4),
    ],
)
async def test_every_home_phrase_opens_the_home(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    text: str,
    offset: int,
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot, make_update(text, user_id=MEMBER, chat_id=MEMBER, update_id=uid(offset))
    )
    assert "Chào Linh" in session.combined_text()


async def test_the_home_offers_only_member_buttons(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """No management function appears on a Member's home."""
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot, make_update("Bắt đầu", user_id=MEMBER, chat_id=MEMBER, update_id=uid(5))
    )

    markup = str(session.sent_of("SendMessage")[0].reply_markup)
    for management_label in (
        copy.Button.PENDING_REQUESTS.value,
        copy.Button.WEEKLY_REPORT.value,
        copy.Button.BY_PERSON.value,
    ):
        assert management_label not in markup


async def test_the_home_shows_the_remaining_allowance(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot, make_update("Bắt đầu", user_id=MEMBER, chat_id=MEMBER, update_id=uid(6))
    )
    assert "lượt trò chuyện AI còn lại" in session.combined_text()


async def test_help_is_member_scoped_and_free(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot, make_update("Giúp tôi", user_id=MEMBER, chat_id=MEMBER, update_id=uid(7))
    )

    reply = session.combined_text()
    assert "TASKSBOT CÓ THỂ GIÚP BẠN" in reply
    assert "Bạn không cần nhớ câu lệnh" in reply
    assert counting_llm.chat_calls == 0


# --- The AI allowance -------------------------------------------------------
async def test_asking_about_the_allowance_costs_nothing(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
    settings: Settings,
) -> None:
    """ "Tôi còn bao nhiêu lượt?" must not itself spend a lượt."""
    bot, session = bot_and_session
    user_id = await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update("Tôi còn bao nhiêu lượt?", user_id=MEMBER, chat_id=MEMBER, update_id=uid(10)),
    )

    assert "20/20 lượt trò chuyện AI" in session.combined_text()
    assert counting_llm.chat_calls == 0
    async with bot_database.session() as active:
        rows = (
            (await active.execute(select(DailyAiUsage).where(DailyAiUsage.user_id == user_id)))
            .scalars()
            .all()
        )
    assert all(row.used_count == 0 for row in rows)


async def test_the_allowance_card_never_says_quota(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot, make_update("con bn luot", user_id=MEMBER, chat_id=MEMBER, update_id=uid(11))
    )
    assert "quota" not in session.combined_text().lower()


async def test_a_generative_request_spends_exactly_one(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, _ = bot_and_session
    user_id = await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Viết giúp tôi 5 bình luận tự nhiên.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(12),
        ),
    )

    assert counting_llm.chat_calls == 1
    async with bot_database.session() as active:
        row = (
            await active.execute(select(DailyAiUsage).where(DailyAiUsage.user_id == user_id))
        ).scalar_one()
    assert row.used_count == 1


async def test_operational_work_still_runs_when_the_allowance_is_gone(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """Running out of chat must not stop somebody filing leave."""
    bot, session = bot_and_session
    user_id = await add_member(bot_database)
    async with bot_database.transaction() as active:
        row = await QuotaService(active, settings).usage_row(user_id=user_id)
        row.used_count = 20

    await dispatcher.feed_update(
        bot, make_update("Bắt đầu", user_id=MEMBER, chat_id=MEMBER, update_id=uid(13))
    )
    assert "Chào Linh" in session.combined_text()

    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        make_update("Đơn nghỉ của tôi.", user_id=MEMBER, chat_id=MEMBER, update_id=uid(14)),
    )
    assert "YÊU CẦU CỦA BẠN" in session.combined_text() or "chưa gửi yêu cầu" in (
        session.combined_text()
    )
    assert counting_llm.chat_calls == 0


# --- Buttons ----------------------------------------------------------------
async def test_a_button_opens_the_thing_it_says(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot, press(button_for("quota.mine", settings=settings), update_id=uid(20))
    )

    assert "lượt trò chuyện AI" in session.combined_text()


async def test_another_member_cannot_press_your_button(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The acting Telegram id is inside the signature, not merely checked."""
    bot, session = bot_and_session
    await add_member(bot_database)
    await add_member(bot_database, telegram_id=OTHER, name="Người Khác")

    signed_for_linh = button_for("quota.mine", settings=settings)
    await dispatcher.feed_update(
        bot, press(signed_for_linh, update_id=uid(21), from_user_id=OTHER, chat_id=OTHER)
    )

    assert "lượt trò chuyện AI hôm nay" not in session.combined_text()


async def test_an_expired_button_is_refused(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    stale = button_for("quota.mine", settings=settings, expires_at=utcnow() - timedelta(minutes=1))
    await dispatcher.feed_update(bot, press(stale, update_id=uid(22)))

    assert copy.Problem.BUTTON_EXPIRED.value in session.combined_text()


def test_a_button_fits_telegrams_limit(settings: Settings) -> None:
    data = button_for(
        "hr.approve",
        settings=settings,
        entity_id=uuid.uuid4(),
        version=99,
        telegram_user_id=9_999_999_999,
        chat_id=-1_002_345_678_901,
    )
    assert len(data.encode("utf-8")) <= 64


@pytest.mark.parametrize("field", ["bot_id", "telegram_user_id", "chat_id"])
def test_changing_any_bound_fact_invalidates_a_button(settings: Settings, field: str) -> None:
    binding = MemberBinding(bot_id=1, telegram_user_id=2, chat_id=3)
    data = build(
        "hr.approve",
        secret=settings.callback_secret,
        binding=binding,
        expires_at=utcnow() + timedelta(hours=1),
        entity_id=uuid.uuid4(),
        version=1,
    )
    import dataclasses

    tampered = dataclasses.replace(binding, **{field: getattr(binding, field) + 1})
    assert parse(data, secret=settings.callback_secret, binding=tampered) is None


def test_a_uuid_survives_the_round_trip(settings: Settings) -> None:
    """The 22-character packing must be lossless, or buttons target nothing."""
    entity = uuid.uuid4()
    data = button_for("hr.approve", settings=settings, entity_id=entity, version=3)
    payload = parse(
        data,
        secret=settings.callback_secret,
        binding=MemberBinding(bot_id=BOT_ID, telegram_user_id=MEMBER, chat_id=MEMBER),
    )
    assert payload is not None
    assert payload.entity_id == entity
    assert payload.version == 3


# --- Numbered list context --------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("viec so 2", 2),
        ("so 3", 3),
        ("cai thu hai", 2),
        ("viec so 10", 10),
    ],
)
def test_a_numbered_reference_is_read(text: str, expected: int) -> None:
    assert read_position(normalize(text).matchable) == expected


def test_a_message_with_no_number_reads_as_none() -> None:
    assert read_position(normalize("viec nay").matchable) is None


async def test_viec_so_2_resolves_against_the_list_that_produced_it(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    items = [str(uuid.uuid4()) for _ in range(3)]
    async with bot_database.transaction() as session:
        service = MemberListService(session, settings)
        version = await service.remember(
            bot_id=BOT_ID, chat_id=MEMBER, telegram_user_id=MEMBER, kind="hr", item_ids=items
        )
        resolved = await service.resolve(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            position=2,
            expected_version=version,
        )
    assert resolved is not None
    assert resolved.item_id == items[1]


async def test_a_stale_numbered_reference_targets_nothing(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """The failure this whole mechanism exists to prevent."""
    async with bot_database.transaction() as session:
        service = MemberListService(session, settings)
        old_version = await service.remember(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            item_ids=[str(uuid.uuid4()) for _ in range(3)],
        )
        # A newer list arrives before they say "số 2".
        await service.remember(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            item_ids=[str(uuid.uuid4()) for _ in range(3)],
        )
        resolved = await service.resolve(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            position=2,
            expected_version=old_version,
        )
    assert resolved is None, "a stale number must not act on a new list"


async def test_an_expired_list_resolves_to_nothing(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    async with bot_database.transaction() as session:
        service = MemberListService(session, settings)
        await service.remember(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            item_ids=[str(uuid.uuid4())],
        )
        row = (await session.execute(select(MemberListContext))).scalar_one()
        row.expires_at = utcnow() - timedelta(seconds=1)

    async with bot_database.session() as session:
        resolved = await MemberListService(session, settings).resolve(
            bot_id=BOT_ID, chat_id=MEMBER, telegram_user_id=MEMBER, kind="hr", position=1
        )
    assert resolved is None


async def test_one_item_needs_no_clarification(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """ "Tôi làm xong rồi" works when there is genuinely only one candidate."""
    only = str(uuid.uuid4())
    async with bot_database.transaction() as session:
        service = MemberListService(session, settings)
        await service.remember(
            bot_id=BOT_ID, chat_id=MEMBER, telegram_user_id=MEMBER, kind="hr", item_ids=[only]
        )
        assert (
            await service.only_item(
                bot_id=BOT_ID, chat_id=MEMBER, telegram_user_id=MEMBER, kind="hr"
            )
            == only
        )


async def test_several_items_force_a_question(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    async with bot_database.transaction() as session:
        service = MemberListService(session, settings)
        await service.remember(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            item_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        )
        assert (
            await service.only_item(
                bot_id=BOT_ID, chat_id=MEMBER, telegram_user_id=MEMBER, kind="hr"
            )
            is None
        )


async def test_one_persons_list_is_not_another_persons(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    async with bot_database.transaction() as session:
        service = MemberListService(session, settings)
        await service.remember(
            bot_id=BOT_ID,
            chat_id=MEMBER,
            telegram_user_id=MEMBER,
            kind="hr",
            item_ids=[str(uuid.uuid4())],
        )
        assert (
            await service.resolve(
                bot_id=BOT_ID,
                chat_id=MEMBER,
                telegram_user_id=OTHER,
                kind="hr",
                position=1,
            )
            is None
        )


# --- The HR flow, end to end ------------------------------------------------
async def test_filing_leave_asks_one_question_then_previews(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """ "Ngày mai tôi xin nghỉ" is missing the period, and only the period."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update("Ngày mai tôi xin nghỉ.", user_id=MEMBER, chat_id=MEMBER, update_id=uid(40)),
    )

    reply = session.combined_text()
    assert "Bạn muốn nghỉ khoảng thời gian nào?" in reply
    # Exactly one question - the reason is not asked at the same time.
    assert "lý do" not in reply.lower()
    assert counting_llm.chat_calls == 0
    async with bot_database.session() as active:
        assert (await active.execute(select(HrRequest))).scalars().all() == []


async def test_a_complete_sentence_goes_straight_to_the_preview(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """Nothing already supplied is asked about again."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin nghỉ buổi sáng vì có việc gia đình.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(41),
        ),
    )

    reply = session.combined_text()
    assert "Bạn đang tạo yêu cầu nghỉ phép" in reply
    assert "Sáng ngày" in reply
    # The approver is named by role label, and OWNER is "Chủ sở hữu".
    assert "Người duyệt: Chủ sở hữu" in reply
    assert "Bạn muốn nghỉ khoảng thời gian nào?" not in reply


async def test_the_preview_creates_nothing_until_confirmed(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _ = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin nghỉ cả ngày.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(42),
        ),
    )
    async with bot_database.session() as active:
        assert (await active.execute(select(HrRequest))).scalars().all() == []

    await dispatcher.feed_update(
        bot, press(button_for("flow.confirm", settings=settings), update_id=uid(43))
    )

    async with bot_database.session() as active:
        rows = (await active.execute(select(HrRequest))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status is HrRequestStatus.PENDING
    assert rows[0].request_type is HrRequestType.FULL_DAY_LEAVE


async def test_confirming_twice_files_one_request(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """A double tap on a slow connection must not file two days off."""
    bot, _ = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin nghỉ cả ngày.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(44),
        ),
    )
    confirm = button_for("flow.confirm", settings=settings)
    await dispatcher.feed_update(bot, press(confirm, update_id=uid(45)))
    await dispatcher.feed_update(bot, press(confirm, update_id=uid(46)))

    async with bot_database.session() as active:
        rows = (await active.execute(select(HrRequest))).scalars().all()
    assert len(rows) == 1


async def test_a_late_request_computes_the_arrival_time(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """With no configured schedule MeoBot asks rather than assuming."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update("Mai tôi đi muộn 30 phút.", user_id=MEMBER, chat_id=MEMBER, update_id=uid(47)),
    )

    reply = session.combined_text()
    assert "chưa có cấu hình giờ làm việc" in reply
    assert "dự kiến có mặt lúc mấy giờ" in reply


async def test_a_stated_arrival_time_previews_immediately(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin đi muộn, khoảng 9 giờ tôi có mặt.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(48),
        ),
    )

    reply = session.combined_text()
    assert "Bạn đang tạo yêu cầu đi muộn" in reply
    assert "09:00" in reply


async def test_discarding_leaves_nothing_behind(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Ngày mai tôi xin nghỉ cả ngày.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(49),
        ),
    )
    await dispatcher.feed_update(
        bot, press(button_for("flow.discard", settings=settings), update_id=uid(50))
    )

    assert copy.CANCELLED in session.combined_text()
    async with bot_database.session() as active:
        assert (await active.execute(select(HrRequest))).scalars().all() == []


async def test_an_active_flow_survives_a_restart(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The draft lives in PostgreSQL-backed FSM storage, not in process memory.

    Simulated by reading the stored state back through a *fresh* storage
    object, which is what a restarted process would do.
    """
    bot, _ = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update("Ngày mai tôi xin nghỉ.", user_id=MEMBER, chat_id=MEMBER, update_id=uid(51)),
    )

    from aiogram.fsm.storage.base import StorageKey

    from meobot.bot.storage import PostgresStorage

    fresh = PostgresStorage(bot_database, ttl_seconds=settings.member_flow_ttl_seconds)  # type: ignore[arg-type]
    key = StorageKey(bot_id=bot.id, chat_id=MEMBER, user_id=MEMBER)
    restored = await fresh.get_data(key)

    assert restored.get("hr_draft"), "the half-finished request did not survive"
    assert await fresh.get_state(key) is not None


async def test_the_flow_owns_the_turn_while_it_is_open(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """Answering the question must not be re-read as a brand-new intent."""
    bot, session = bot_and_session
    await add_member(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update("Ngày mai tôi xin nghỉ.", user_id=MEMBER, chat_id=MEMBER, update_id=uid(52)),
    )

    session.requests.clear()
    await dispatcher.feed_update(
        bot, make_update("buổi sáng", user_id=MEMBER, chat_id=MEMBER, update_id=uid(53))
    )

    reply = session.combined_text()
    assert "Bạn đang tạo yêu cầu nghỉ phép" in reply
    assert "Sáng ngày" in reply


# --- Authorization ----------------------------------------------------------
async def test_a_member_cannot_open_the_pending_queue(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Cho tôi xem đơn đang chờ duyệt.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(60),
        ),
    )

    assert copy.Problem.NO_PERMISSION.value in session.combined_text()


async def test_a_member_cannot_see_who_is_off(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot, make_update("Hôm nay ai nghỉ?", user_id=MEMBER, chat_id=MEMBER, update_id=uid(61))
    )

    assert copy.Problem.NO_PERMISSION.value in session.combined_text()


async def test_a_member_cannot_read_the_department_report(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot,
        make_update(
            "Cho tôi báo cáo nhân sự tháng 7.",
            user_id=MEMBER,
            chat_id=MEMBER,
            update_id=uid(62),
        ),
    )

    assert copy.Problem.NO_PERMISSION.value in session.combined_text()


# --- Honest gaps ------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "offset"),
    [
        ("Hôm nay tôi có việc gì?", 70),
        ("Tôi gửi ảnh bằng chứng.", 71),
        ("page cua toi", 72),
        ("toi thieu bai nao", 73),
    ],
)
async def test_an_unbuilt_feature_says_so_instead_of_faking_it(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    text: str,
    offset: int,
) -> None:
    """Returning a fake success would be worse than returning nothing."""
    bot, session = bot_and_session
    await add_member(bot_database)

    await dispatcher.feed_update(
        bot, make_update(text, user_id=MEMBER, chat_id=MEMBER, update_id=uid(offset))
    )

    assert "đang được hoàn thiện" in session.combined_text()
