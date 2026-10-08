"""The transport contract for free-text messages.

These run real :class:`~aiogram.types.Update` objects through the real
Dispatcher - middlewares, filters, router order and all - because that is the
only way to prove a message is not swallowed by another handler.

What is checked: the update reaches the conversation handler exactly once,
mentions are stripped, a private chat needs no mention, a group does, typing is
sent before the slow call rather than after it, and model text cannot break the
HTML parse mode or be silently truncated.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TelegramUser

from meobot.bot import formatting
from meobot.bot.handlers.conversation import (
    PROVIDER_DOWN_REPLY,
    is_addressed_to_bot,
    short_reference,
    strip_bot_mention,
)
from meobot.bot.middlewares import DeduplicationMiddleware
from meobot.core.config import Settings
from meobot.core.time import utcnow
from tests.fakes import RecordingSession, SqliteDatabase, make_update

OWNER_ID = 777000111


def group_message(
    text: str,
    *,
    reply_to: Message | None = None,
    chat_id: int = -100123,
) -> Message:
    """A message in a group, where MeoBot must not answer uninvited."""
    return Message(
        message_id=42,
        date=utcnow(),
        chat=Chat(id=chat_id, type="supergroup"),
        from_user=TelegramUser(id=OWNER_ID, is_bot=False, first_name="Owner"),
        text=text,
        reply_to_message=reply_to,
    )


def private_message(text: str) -> Message:
    return Message(
        message_id=42,
        date=utcnow(),
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=TelegramUser(id=OWNER_ID, is_bot=False, first_name="Owner"),
        text=text,
    )


def bot_reply(text: str = "trước đó") -> Message:
    """A message *from the bot*, so replying to it addresses MeoBot."""
    return Message(
        message_id=41,
        date=utcnow(),
        chat=Chat(id=-100123, type="supergroup"),
        from_user=TelegramUser(id=123456, is_bot=True, first_name="TasksBot"),
        text=text,
    )


# --- Mentions --------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@meobot Hello", "Hello"),
        ("Hello @meobot", "Hello"),
        ("@meobot", ""),
        ("Bạn ơi @meobot", "Bạn ơi"),
        ("đồng bộ @meobot các Sheet", "đồng bộ các Sheet"),
    ],
)
def test_the_bot_mention_is_removed_before_routing(raw: str, expected: str) -> None:
    assert strip_bot_mention(raw, "meobot") == expected


def test_a_mention_of_a_person_is_content_not_a_recipient() -> None:
    """ "@linh viết kịch bản này" names a colleague, and must survive."""
    assert strip_bot_mention("hỏi @linh về kịch bản này", "meobot") == ("hỏi @linh về kịch bản này")


def test_a_leading_mention_is_dropped_when_the_username_is_unknown() -> None:
    """Before ``getMe`` resolves, a leading mention is still addressing us."""
    assert strip_bot_mention("@somebot đồng bộ Sheet", None) == "đồng bộ Sheet"


# --- Where MeoBot answers --------------------------------------------------
def test_a_private_message_never_requires_a_mention() -> None:
    """Demanding one here is the most common way to make a bot look broken."""
    assert is_addressed_to_bot(private_message("Hello"), require_mention=True)


def test_a_group_message_without_a_mention_is_ignored() -> None:
    assert not is_addressed_to_bot(group_message("mai họp lúc mấy giờ"), require_mention=True)


def test_a_group_message_is_answered_when_the_bot_is_replied_to() -> None:
    message = group_message("triển khai hướng số 3", reply_to=bot_reply())
    assert is_addressed_to_bot(message, require_mention=True)


def test_a_group_can_be_configured_to_answer_everything() -> None:
    assert is_addressed_to_bot(group_message("bất kỳ"), require_mention=False)


# --- Exactly once ----------------------------------------------------------
def test_a_redelivered_update_is_processed_once() -> None:
    """Long polling is at-least-once; answering twice is not acceptable."""
    middleware = DeduplicationMiddleware()

    assert middleware.already_processed(7) is False
    assert middleware.already_processed(7) is True
    assert middleware.already_processed(8) is False


def test_the_deduplication_memory_stays_bounded() -> None:
    """It protects one process's lifetime, not the history of the universe."""
    middleware = DeduplicationMiddleware(capacity=3)
    for update_id in range(10):
        assert middleware.already_processed(update_id) is False

    # The oldest ids have been evicted, so they no longer suppress anything.
    assert middleware.already_processed(0) is False
    assert middleware.already_processed(9) is True


async def test_one_update_produces_one_answer(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    update = make_update("Hello", update_id=4242, message_id=11)

    await dispatcher.feed_update(bot, update)
    first = len(session.sent_of("SendMessage"))
    await dispatcher.feed_update(bot, update)

    assert first >= 1
    assert len(session.sent_of("SendMessage")) == first, "the redelivered update answered again"


# --- Typing ----------------------------------------------------------------
async def test_typing_is_sent_before_the_answer(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The indicator is only useful while the user is waiting.

    The test environment pins ``CHAT_TYPING_INDICATOR=false`` so the rest of
    the suite is not full of chat actions; this test swaps in a settings object
    with it on, through the same injection the handler reads it from.
    """
    monkeypatch.setitem(
        dispatcher.workflow_data,
        "settings",
        settings.model_copy(update={"chat_typing_indicator": True}),
    )
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("Hello", update_id=101, message_id=12))

    kinds = [type(request).__name__ for request in session.requests]
    assert "SendChatAction" in kinds, "no typing indicator was sent"
    assert kinds.index("SendChatAction") < kinds.index("SendMessage")


# --- Free text is not swallowed -------------------------------------------
async def test_free_text_reaches_the_conversation_handler(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("Hello", update_id=201, message_id=13))

    assert session.sent_texts()
    combined = session.combined_text()
    assert "TasksBot" in combined
    assert "ngắn gọn hơn" not in combined


async def test_a_bare_mention_is_answered_rather_than_dropped(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("@meobot_test", update_id=202, message_id=14))

    assert session.sent_texts(), "a bare mention was silently dropped"


# --- Rendering -------------------------------------------------------------
def test_model_text_cannot_break_the_html_parse_mode() -> None:
    """An unmatched "<" in a generated sentence would reject the whole message."""
    generated = 'Thử <b>đậm</b> & một dấu < chưa đóng, rồi "trích dẫn"'
    escaped = formatting.escape(generated)

    assert "<b>" not in escaped
    assert "&lt;" in escaped and "&amp;" in escaped
    # Vietnamese and quotes survive untouched.
    assert "đậm" in escaped
    assert '"trích dẫn"' in escaped


def test_line_breaks_and_vietnamese_survive_escaping() -> None:
    text = "Dòng một\nDòng hai\n\nDòng ba: ưu tiên quý tới"
    assert formatting.escape(text) == text


def test_a_long_reply_is_split_not_truncated() -> None:
    """A truncated instruction is a different instruction."""
    paragraphs = "\n\n".join(f"Đoạn {index}: " + "nội dung " * 50 for index in range(30))
    chunks = formatting.split_message(paragraphs)

    assert len(chunks) > 1
    assert all(len(chunk) <= formatting.SAFE_CHUNK_LENGTH for chunk in chunks)
    # Every paragraph is still present somewhere.
    rejoined = "\n".join(chunks)
    for index in range(30):
        assert f"Đoạn {index}:" in rejoined


def test_a_single_enormous_line_is_cut_rather_than_dropped() -> None:
    chunks = formatting.split_message("x" * 10000)
    assert len(chunks) > 1
    assert sum(len(chunk) for chunk in chunks) == 10000


# --- The failure message ---------------------------------------------------
def test_the_reference_id_is_short_enough_to_read_aloud() -> None:
    import uuid as uuid_module

    reference = short_reference(uuid_module.UUID("a1b2c3d4-0000-0000-0000-000000000000"))
    assert reference == "a1b2c3d4"
    assert len(PROVIDER_DOWN_REPLY.format(reference=reference)) < 300


def test_the_failure_message_never_blames_the_users_wording() -> None:
    rendered = PROVIDER_DOWN_REPLY.format(reference="deadbeef")
    assert "ngắn gọn hơn" not in rendered
    assert "deadbeef" in rendered


def test_make_update_builds_what_telegram_would_send() -> None:
    """Guards the fixture the tests above depend on."""
    update = make_update("Hello", update_id=1, message_id=1)
    assert isinstance(update, Update)
    assert update.message is not None
    assert update.message.text == "Hello"
    assert isinstance(update.message.date, datetime)
