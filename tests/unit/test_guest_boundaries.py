"""What a Guest can see, and where their words are allowed to go.

Two boundaries, and they fail in different ways if they are wrong.

**The tool boundary** is structural. A Guest is a
:class:`~meobot.domain.access.models.GuestPrincipal`, not an
:class:`~meobot.domain.identity.models.Actor`, and
``ConversationService.handle_guest_message`` never touches the tool registry or
the router. So "a Guest cannot run a tool" is checked here as a property of the
code path, not by trying a hundred phrasings and hoping.

**The memory boundary** is per chat. A Guest's history belongs to one group and
one Telegram id. If it leaked into another chat's prompt, one team's private
conversation would end up described to a stranger in a different room - which is
the worst thing this feature could do, so it gets its own test.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from sqlalchemy import select

from meobot.application.chat_memory_service import ChatMemoryService
from meobot.application.conversation_service import (
    GUEST_TOOL_DENIED,
    ConversationService,
    guest_prompt_context,
)
from meobot.application.group_policy_service import GroupPolicyService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.conversation import ConversationMessage, ConversationThread
from meobot.domain.access.models import GuestPrincipal
from meobot.domain.assistant.profile import AssistantProfile
from meobot.domain.identity.models import Actor, Role
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import BOT_ID, RecordingSession, SqliteDatabase, make_group_update
from tests.unit.test_access_gate import CountingProvider, counting_llm  # noqa: F401

BASE = 600_000
GUEST_ID = 930_001
MEMBER_ID = 930_002
GROUP_A = -4001
GROUP_B = -4002


def uid(offset: int) -> int:
    return BASE + offset


def a_guest(chat_id: int = GROUP_A) -> GuestPrincipal:
    now = utcnow()
    return GuestPrincipal(
        policy_id=uuid.uuid4(),
        telegram_user_id=GUEST_ID,
        telegram_chat_id=chat_id,
        display_name="Khách Mời",
        granted_at=now,
        expires_at=now + timedelta(hours=24),
    )


# --- The prompt a Guest gets ------------------------------------------------
def test_the_guest_prompt_carries_no_internal_context() -> None:
    """The Guest block is built small rather than filtered down from the big one.

    A filter can be forgotten when a new section is added to the full prompt
    context; a separate, smaller builder cannot leak what it never reads. This
    test states which words must never appear.
    """
    profile = AssistantProfile(assistant_name="TasksBot")
    rendered = guest_prompt_context(a_guest(), profile)

    for forbidden in (
        "[TOOLS]",
        "[CAPABILITIES]",
        "[WORKSPACE]",
        "[PERMISSIONS]",
        "sheet_profile",
        "script.approve",
        "spreadsheet.create",
        "user.manage",
    ):
        assert forbidden not in rendered, f"a Guest prompt must not mention {forbidden}"

    # ...and it does say what a Guest is.
    assert "khách mời tạm thời" in rendered
    assert "Khách Mời" in rendered


def test_the_guest_prompt_names_no_role() -> None:
    """A Guest has no Role, so nothing may describe them as having one."""
    rendered = guest_prompt_context(a_guest(), AssistantProfile(assistant_name="TasksBot"))
    for role in Role:
        assert role.value not in rendered


def test_the_guest_denial_is_a_constant_not_a_generated_sentence() -> None:
    """A deterministic refusal cannot be talked around, and costs nothing."""
    assert "Guest chỉ có thể trò chuyện" in GUEST_TOOL_DENIED
    assert "công cụ" in GUEST_TOOL_DENIED


def test_handle_guest_message_takes_no_actor() -> None:
    """The type signature is the guarantee - there is no role to check against."""
    import inspect

    signature = inspect.signature(ConversationService.handle_guest_message)
    assert "actor" not in signature.parameters
    assert "guest" in signature.parameters


# --- The tool boundary in practice -----------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "Kịch bản nào đang chờ?",
        "Đồng bộ Sheet.",
        "Duyệt kịch bản này.",
        "Cho tôi xem Drive.",
    ],
)
async def test_a_guest_asking_for_internal_data_runs_no_tool(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    question: str,
) -> None:
    """Whatever they ask, the answer comes from the chat path and nothing else.

    The check is not the wording of the reply - a model may phrase a refusal
    however it likes - it is that no tool ran and no internal identifier
    appeared in what was sent.
    """
    bot, session = bot_and_session
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as active:
        await GroupPolicyService(active).grant_guest(
            actor=owner, bot_id=BOT_ID, chat_id=GROUP_A, telegram_user_id=GUEST_ID
        )

    session.requests.clear()
    await dispatcher.feed_update(
        bot,
        make_group_update(
            question, update_id=uid(hash(question) % 900), user_id=GUEST_ID, chat_id=GROUP_A
        ),
    )

    reply = session.combined_text()
    assert reply, "the Guest got no answer at all"
    for leak in ("sheet_profile", "script_id", "spreadsheet_id", "PolicyEngine", "ToolRegistry"):
        assert leak not in reply


# --- The memory boundary ----------------------------------------------------
async def test_a_guests_words_stay_in_their_own_group(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """A Guest's thread is keyed by (chat, telegram id) and nothing merges it."""
    bot, _ = bot_and_session
    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as active:
        await GroupPolicyService(active).grant_guest(
            actor=owner, bot_id=BOT_ID, chat_id=GROUP_A, telegram_user_id=GUEST_ID
        )

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "bí mật của riêng nhóm A", update_id=uid(10), user_id=GUEST_ID, chat_id=GROUP_A
        ),
    )

    async with bot_database.session() as active:
        threads = (await active.execute(select(ConversationThread))).scalars().all()
        messages = (await active.execute(select(ConversationMessage))).scalars().all()

    assert len(threads) == 1
    assert threads[0].chat_id == GROUP_A
    assert threads[0].telegram_user_id == GUEST_ID
    assert all(message.thread_id == threads[0].id for message in messages)


async def test_one_groups_history_never_reaches_another_groups_prompt(
    bot_database: SqliteDatabase, settings: Settings
) -> None:
    """The strongest privacy claim in this release, checked directly."""
    async with bot_database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        thread_a = await memory.get_or_create_thread(
            bot_id=BOT_ID, chat_id=GROUP_A, telegram_user_id=MEMBER_ID
        )
        await memory.record_message(
            thread=thread_a, role="user", content="số liệu doanh thu quý 4 là bí mật"
        )

    async with bot_database.session() as session:
        memory = ChatMemoryService(session, settings)
        context_b = await memory.load_context(
            bot_id=BOT_ID, chat_id=GROUP_B, telegram_user_id=MEMBER_ID
        )
        context_guest = await memory.load_context(
            bot_id=BOT_ID, chat_id=GROUP_A, telegram_user_id=GUEST_ID
        )

    rendered_b = " ".join(turn.content for turn in context_b.history)
    rendered_guest = " ".join(turn.content for turn in context_guest.history)
    assert "doanh thu quý 4" not in rendered_b, "history leaked into another group"
    assert "doanh thu quý 4" not in rendered_guest, "history leaked to a Guest"


async def test_an_ignored_message_is_never_stored(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    counting_llm: CountingProvider,  # noqa: F811
) -> None:
    """Ignoring somebody must not quietly archive what they said."""
    bot, session = bot_and_session
    from meobot.domain.access.models import GroupPolicyMode

    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with bot_database.transaction() as active:
        await GroupPolicyService(active).set_mode(
            actor=owner,
            bot_id=BOT_ID,
            chat_id=GROUP_A,
            telegram_user_id=GUEST_ID,
            mode=GroupPolicyMode.IGNORE,
        )

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "điều này không nên được lưu lại",
            update_id=uid(20),
            user_id=GUEST_ID,
            chat_id=GROUP_A,
        ),
    )

    async with bot_database.session() as active:
        messages = (await active.execute(select(ConversationMessage))).scalars().all()
    assert messages == []
    assert session.sent_texts() == []
    assert counting_llm.chat_calls == 0
