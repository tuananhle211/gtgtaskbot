"""The architectural rule of 0.6.0a2.1, stated as tests.

**A recognised business intent must never reach ConversationService.** Both
reported failures were the same mistake wearing different clothes: a sentence
naming a real operation was classified as conversation, and the model answered
it. The model has no chat id, no registry and no transaction - so whatever it
says about having done the thing is, at best, a guess that reads like a receipt.

What is checked here:

* every intent on the forbidden list classifies as operational, so the access
  gate never reserves a chat slot for it;
* commands still preempt an active business flow;
* an ordinary sentence still reaches the model, because the point is routing,
  not refusing;
* no handler on the cross-chat path sends into another chat.
"""

from __future__ import annotations

import itertools

import pytest
from aiogram import Bot, Dispatcher
from sqlalchemy import select

from meobot.db.models.notifications import OutboundMessage, TelegramChat
from meobot.domain.member.intents import MemberIntent, classify
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import RecordingSession, SqliteDatabase, make_group_update, make_update
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

BASE = 1_300_000
GROUP = -991_477

_counter = itertools.count(BASE)


def next_uid() -> int:
    return next(_counter)


#: Section 2 of the release note, verbatim: the free-chat model must never
#: receive a request whose recognised intent is one of these.
FORBIDDEN_FOR_FREE_CHAT: tuple[tuple[str, MemberIntent], ...] = (
    ("Nhắc tôi họp lúc 15 giờ.", MemberIntent.CREATE_REMINDER),
    ("Đổi lịch nhắc sang 16 giờ.", MemberIntent.EDIT_REMINDER),
    ("Huỷ lịch nhắc buổi sáng.", MemberIntent.CANCEL_REMINDER),
    ("Đăng ký group này làm group Test.", MemberIntent.REGISTER_CHAT),
    ("Gửi Chào buổi sáng vào group Test.", MemberIntent.MAKE_ANNOUNCEMENT),
    ("Thông báo cho toàn phòng: họp lúc 3 giờ.", MemberIntent.MAKE_ANNOUNCEMENT),
    ("Danh sách group đã đăng ký.", MemberIntent.VIEW_REGISTERED_CHATS),
    ("Gửi lại thông báo chưa gửi được.", MemberIntent.RETRY_DELIVERY),
    ("Nhắn riêng cho Linh.", MemberIntent.SEND_PRIVATE_MESSAGE),
)


@pytest.mark.parametrize(("sentence", "expected"), FORBIDDEN_FOR_FREE_CHAT)
def test_each_business_intent_is_recognised_and_free(sentence: str, expected: MemberIntent) -> None:
    match = classify(sentence)
    assert match.intent is expected, sentence
    # Operational means two things at once: deterministic, and never billed.
    assert match.is_operational, sentence


def test_ordinary_conversation_is_still_generative() -> None:
    """The rule is about routing, not about refusing to talk."""
    for sentence in (
        "Em nghĩ sao về kế hoạch nội dung tháng 8?",
        "Viết giúp tôi 5 bình luận cho bài này.",
        "Hôm nay trời đẹp nhỉ.",
    ):
        assert classify(sentence).intent is MemberIntent.GENERATIVE, sentence


async def test_a_send_request_does_not_reach_the_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """Even with nothing registered, this is a registry answer, not a chat."""
    bot, _session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_update(
            "Gửi Chào buổi sáng vào group Test.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=next_uid(),
        ),
    )
    assert counting_llm.chat_calls == 0


async def test_an_ordinary_question_still_reaches_the_model(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    bot, _session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_update(
            "Em nghĩ sao về kế hoạch nội dung tháng 8?",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=next_uid(),
        ),
    )
    assert counting_llm.chat_calls == 1


async def test_a_command_preempts_an_open_registration(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """ "/help" in the middle of a registration is help, not a group name."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "đăng ký group này làm group Test",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            update_id=next_uid(),
            chat_title="Test",
        ),
    )
    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/help",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            update_id=next_uid(),
            chat_title="Test",
        ),
    )
    reply = session.combined_text()
    assert reply, "/help was swallowed by the registration flow"
    assert "đang đăng ký group" not in reply
    # And the half-finished registration wrote nothing.
    async with bot_database.session() as db:
        assert list((await db.execute(select(TelegramChat))).scalars().all()) == []


# --- Section 9: no handler sends into another chat --------------------------
async def test_registration_never_sends_to_another_chat(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """Registration answers the group it happened in, and nowhere else.

    Stated as a test because the failure it guards against is invisible in
    review: one ``bot.send_message(owner_id, ...)`` in a handler would work
    perfectly in development and silently bypass the outbox, the privacy check
    and the retry budget in production.
    """
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "đăng ký group này làm group Test",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            update_id=next_uid(),
            chat_title="Test",
        ),
    )
    elsewhere = [
        request
        for request in session.sent_of("SendMessage")
        if int(getattr(request, "chat_id", 0) or 0) != GROUP
    ]
    assert elsewhere == []


async def test_the_send_path_queues_rather_than_sending(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """An unresolvable destination must not produce an outbox row either."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_update(
            "Thông báo cho group Không Tồn Tại: xin chào.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=next_uid(),
        ),
    )
    elsewhere = [
        request
        for request in session.sent_of("SendMessage")
        if int(getattr(request, "chat_id", 0) or 0) != OWNER_TELEGRAM_ID
    ]
    assert elsewhere == []
    async with bot_database.session() as db:
        assert list((await db.execute(select(OutboundMessage))).scalars().all()) == []


def test_the_free_chat_rule_is_authoritative_and_specific() -> None:
    """Section 10's rule, checked where it is defined rather than paraphrased."""
    from meobot.domain.assistant.identity import NEVER_CLAIM_BUSINESS_RESULT_RULE
    from meobot.integrations.llm.prompts import SYSTEM_PROMPT

    rule = NEVER_CLAIM_BUSINESS_RESULT_RULE
    # The three things it may not claim.
    assert "lịch nhắc" in rule
    assert "đăng ký group" in rule
    assert "Telegram" in rule
    # And the reason it may not: only a real service result counts.
    assert "có cấu trúc" in rule
    # The reported wrong answer, named so it cannot come back.
    assert "BotFather" in rule
    assert rule.strip() in SYSTEM_PROMPT


def test_the_rule_reaches_the_rendered_identity_block() -> None:
    """A rule only in the system prompt drifts from the one in context."""
    from meobot.domain.assistant.profile import default_assistant_profile

    rendered = default_assistant_profile().render_identity_block()
    assert "BotFather" in rendered
    assert "đăng ký group" in rendered
