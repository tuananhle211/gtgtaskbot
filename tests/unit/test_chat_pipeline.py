"""The chat-first pipeline: what a user gets when the model misbehaves.

The production failure these tests exist for: every ordinary message came back
as *"Mình chưa xử lý được câu này (trợ lý AI đang trục trặc). Bạn thử nhắn lại
ngắn gọn hơn"* - including in reply to "Hello", which cannot be shortened.

So the assertions here are mostly about **degradation**, not about happy paths:

* a router that fails must land a message in chat, never in a tool;
* a chat generation that fails twice must still produce something useful;
* nothing in any of that may execute a tool or claim one ran;
* the operational phrases must keep reaching the policy engine unchanged.
"""

from __future__ import annotations

import uuid

import pytest

from meobot.application.conversation_service import ConversationService, ConversationTarget
from meobot.bot.handlers.conversation import PROVIDER_DOWN_REPLY
from meobot.core.config import Settings
from meobot.domain.conversations.decision import ConversationMode
from meobot.domain.conversations.patterns import (
    deterministic_route,
    fallback_route,
    is_greeting,
    looks_operational,
)
from meobot.domain.identity.models import Actor
from meobot.domain.policy.engine import PolicyEngine
from meobot.integrations.llm.base import MessageRoute, PlanningRequest, RouteMode, RouteRequest
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.tools.base import ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import SqliteDatabase, StubHealthService

#: The sentence this whole change exists to make impossible.
FORBIDDEN_ADVICE = "ngắn gọn hơn"

BOT_ID = 999
CHAT_ID = 555


@pytest.fixture
def registry() -> ToolRegistry:
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


@pytest.fixture
def target() -> ConversationTarget:
    return ConversationTarget(bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=777000111)


def service(
    settings: Settings,
    registry: ToolRegistry,
    database: SqliteDatabase,
    llm: FakeLLMProvider | None = None,
) -> ConversationService:
    return ConversationService(
        llm=llm or FakeLLMProvider(),
        registry=registry,
        policy=PolicyEngine(registry.policies()),
        database=database,  # type: ignore[arg-type]
        settings=settings,
    )


# --- Deterministic routing -------------------------------------------------
@pytest.mark.parametrize("message", ["Hello", "hi", "Chào bạn", "xin chào", "Alo"])
def test_greetings_are_recognised_without_a_model(message: str) -> None:
    assert is_greeting(message)
    settled = deterministic_route(message)
    assert settled is not None
    assert settled.route.mode is RouteMode.CHAT
    assert settled.canned_reply_kind == "greeting"


def test_a_greeting_wrapped_around_a_request_is_not_short_circuited() -> None:
    """ "chào bạn, đồng bộ Sheet giúp mình" is a request, not an opener."""
    assert not is_greeting("chào bạn, đồng bộ các Sheet giúp mình")


@pytest.mark.parametrize(
    "message",
    ["Đồng bộ các Sheet", "tạo Sheet kịch bản tháng 8", "duyệt kịch bản TT-0312"],
)
def test_operational_phrases_are_recognised_without_a_model(message: str) -> None:
    assert looks_operational(message)


@pytest.mark.parametrize(
    "message",
    [
        "cho mình vài ý tưởng content",
        "tôi đang bí ý tưởng",
        "viết lại hook này hay hơn đi",
    ],
)
def test_creative_requests_are_not_mistaken_for_operations(message: str) -> None:
    assert not looks_operational(message)


def test_a_failed_router_never_produces_a_tool_route() -> None:
    """The single most important fallback property."""
    for message in ["Hello", "Bạn làm được gì?", "Đồng bộ các Sheet", "duyệt hết đi"]:
        assert fallback_route(message).mode is not RouteMode.TOOL


def test_a_failed_router_sends_an_ordinary_message_to_chat() -> None:
    assert fallback_route("hôm nay trời đẹp nhỉ").mode is RouteMode.CHAT


def test_a_failed_router_asks_about_an_operational_message() -> None:
    """It looks like an operation but nothing verified it - so ask."""
    route = fallback_route("Đồng bộ các Sheet")
    assert route.mode is RouteMode.CLARIFY
    assert route.missing_information


# --- "Hello" and friends ---------------------------------------------------
async def test_hello_gets_a_natural_answer(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """The reported failure, as a test."""
    reply = await service(settings, registry, bot_database).handle_message(
        actor=owner_actor, message="Hello", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    assert FORBIDDEN_ADVICE not in reply.text
    assert "trục trặc" not in reply.text
    assert "TasksBot" in reply.text


async def test_hello_still_works_when_every_provider_call_fails(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """A total provider outage degrades the answer; it does not remove it."""
    llm = FakeLLMProvider(fail_route=True, fail_chat=True)
    reply = await service(settings, registry, bot_database, llm).handle_message(
        actor=owner_actor, message="Hello", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    assert FORBIDDEN_ADVICE not in reply.text
    assert "TasksBot" in reply.text


async def test_route_failure_still_lets_an_ordinary_message_be_answered(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Routing is broken; conversation is not."""
    llm = FakeLLMProvider(fail_route=True)
    reply = await service(settings, registry, bot_database, llm).handle_message(
        actor=owner_actor,
        message="hôm nay team mình nên tập trung vào đâu",
        request_id=request_id,
        target=target,
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    assert reply.text.strip()


async def test_chat_generation_failure_retries_then_falls_back(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Two attempts, then a deterministic answer - never an apology alone."""
    calls: list[bool] = []

    class FailingChat(FakeLLMProvider):
        async def generate_chat_reply(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.minimal)
            raise RuntimeError("provider down")

    reply = await service(settings, registry, bot_database, FailingChat()).handle_message(
        actor=owner_actor,
        message="mình muốn bàn hướng nội dung quý tới",
        request_id=request_id,
        target=target,
    )

    assert calls == [False, True], "expected one full attempt then one minimal retry"
    assert reply.mode is ConversationMode.CHAT
    assert reply.text.strip()
    assert FORBIDDEN_ADVICE not in reply.text


async def test_capabilities_are_generated_from_the_registry_not_the_model(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """ "Bạn làm được gì?" must not depend on a fragile LLM response."""
    llm = FakeLLMProvider(fail_route=True, fail_chat=True)
    reply = await service(settings, registry, bot_database, llm).handle_message(
        actor=owner_actor, message="Bạn làm được gì?", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    # The four buckets are the answer, and they come from live configuration.
    assert "Có thể làm ngay" in reply.text
    assert "Chưa được xây dựng" in reply.text
    # Every listed capability is a live registry description, not a written
    # list that could drift from what MeoBot actually does.
    from meobot.application.capability_service import CapabilityService

    report = CapabilityService(registry, settings).report_for(owner_actor)
    assert report.available_now
    assert all(item in reply.text for item in report.available_now)


async def test_capabilities_respect_the_actor_permissions(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    employee_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """An employee is not told about capabilities they cannot use."""
    chat = service(settings, registry, bot_database, FakeLLMProvider(fail_chat=True))

    owner_reply = await chat.handle_message(
        actor=owner_actor, message="Bạn làm được gì?", request_id=request_id, target=target
    )
    employee_reply = await chat.handle_message(
        actor=employee_actor, message="Bạn làm được gì?", request_id=request_id, target=target
    )

    assert owner_reply.text != employee_reply.text
    # Each is told their own role, by its display label.
    assert "Chủ sở hữu" in owner_reply.text
    assert "Nhân viên" in employee_reply.text
    assert "EMPLOYEE" not in employee_reply.text


async def test_who_am_i_talking_to_uses_the_authoritative_role(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    reply = await service(settings, registry, bot_database).handle_message(
        actor=owner_actor,
        message="Bạn đang nói chuyện với ai?",
        request_id=request_id,
        target=target,
    )
    assert "Chủ sở hữu" in reply.text
    assert "OWNER" not in reply.text
    assert reply.tool_result is None


# --- Tool boundary ---------------------------------------------------------
async def test_sync_the_sheets_still_routes_to_a_tool(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Conversation quality must not have cost the operational path."""
    reply = await service(settings, registry, bot_database).handle_message(
        actor=owner_actor, message="Đồng bộ các Sheet", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.TOOL
    assert reply.decision is not None, "the policy engine did not see this plan"


async def test_approve_everything_asks_and_executes_nothing(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    reply = await service(settings, registry, bot_database).handle_message(
        actor=owner_actor, message="Duyệt hết đi", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CLARIFY
    assert reply.tool_result is None
    assert reply.plan is None


async def test_an_unplannable_operation_asks_rather_than_guessing(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Routed to "tool", but nothing could be planned. Ask; never improvise."""

    class NoPlan(FakeLLMProvider):
        async def plan_tool_action(self, request: PlanningRequest):  # type: ignore[no-untyped-def]
            from meobot.domain.policy.models import ActionPlan

            return ActionPlan.unknown(reasoning="no idea")

    reply = await service(settings, registry, bot_database, NoPlan()).handle_message(
        actor=owner_actor, message="Đồng bộ các Sheet", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CLARIFY
    assert reply.tool_result is None


async def test_chat_never_asks_the_provider_to_plan_a_tool(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """The chat branch does not touch the planner at all."""
    llm = FakeLLMProvider()
    await service(settings, registry, bot_database, llm).handle_message(
        actor=owner_actor,
        message="Tôi đang bí ý tưởng content",
        request_id=request_id,
        target=target,
    )
    assert llm.seen_messages == [], "chat mode reached the tool planner"


async def test_chat_generation_is_never_given_a_tool_catalogue(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Structural: a chat request has no field that could carry a tool."""
    llm = FakeLLMProvider()
    await service(settings, registry, bot_database, llm).handle_message(
        actor=owner_actor,
        message="mình cần góp ý cho hướng nội dung tháng này",
        request_id=request_id,
        target=target,
    )

    assert llm.seen_chats, "chat generation never ran"
    request = llm.seen_chats[-1]
    assert not hasattr(request, "available_tools")
    rendered = request.prompt_context
    assert "arguments_schema" not in rendered
    assert "json_schema" not in rendered


# --- Memory ----------------------------------------------------------------
async def test_a_follow_up_turn_keeps_the_channel_and_audience(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """The multi-turn conversation from the brief, offline."""
    chat = service(settings, registry, bot_database)

    await chat.handle_message(
        actor=owner_actor,
        message="Tôi đang bí ý tưởng content",
        request_id=request_id,
        target=target,
    )
    await chat.handle_message(
        actor=owner_actor, message="Cho kênh bác sĩ Tiến", request_id=request_id, target=target
    )
    third = await chat.handle_message(
        actor=owner_actor,
        message="Đối tượng là phụ nữ trên 35 tuổi",
        request_id=request_id,
        target=target,
    )

    # The subject survived two turns without being asked for again.
    assert "kênh" in third.text.lower() or "phu nu" in third.text.lower().replace("ụ", "u")
    assert "Đây là nội dung cho thương hiệu" not in third.text


async def test_the_prompt_carries_the_previous_turns(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    llm = FakeLLMProvider()
    chat = service(settings, registry, bot_database, llm)

    await chat.handle_message(
        actor=owner_actor,
        message="Tôi muốn làm nội dung cho kênh bác sĩ Tiến",
        request_id=request_id,
        target=target,
    )
    await chat.handle_message(
        actor=owner_actor,
        message="Tôi muốn chạm đến nỗi đau trong hôn nhân",
        request_id=request_id,
        target=target,
    )

    latest = llm.seen_chats[-1]
    replayed = " ".join(turn.content for turn in latest.history)
    assert "bác sĩ Tiến" in replayed


async def test_a_new_chat_does_not_reuse_the_old_context(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """``/new_chat`` archives; it does not leak."""
    from meobot.application.chat_memory_service import ChatMemoryService

    llm = FakeLLMProvider()
    chat = service(settings, registry, bot_database, llm)
    await chat.handle_message(
        actor=owner_actor,
        message="Tôi muốn làm nội dung cho kênh bác sĩ Tiến",
        request_id=request_id,
        target=target,
    )

    async with bot_database.transaction() as session:
        await ChatMemoryService(session, settings).start_thread(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=target.telegram_user_id
        )

    await chat.handle_message(
        actor=owner_actor, message="tiếp tục nhé", request_id=request_id, target=target
    )

    latest = llm.seen_chats[-1]
    replayed = " ".join(turn.content for turn in latest.history)
    assert "bác sĩ Tiến" not in replayed


async def test_context_survives_a_new_service_instance(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """What "restarting the bot preserves the thread" means in a test.

    Memory lives in PostgreSQL, so a fresh service object - the thing a restart
    produces - finds the same open thread.
    """
    await service(settings, registry, bot_database).handle_message(
        actor=owner_actor,
        message="Tôi muốn làm nội dung cho kênh bác sĩ Tiến",
        request_id=request_id,
        target=target,
    )

    llm = FakeLLMProvider()
    restarted = service(settings, registry, bot_database, llm)
    await restarted.handle_message(
        actor=owner_actor, message="Tiếp tục ý tưởng vừa rồi", request_id=request_id, target=target
    )

    replayed = " ".join(turn.content for turn in llm.seen_chats[-1].history)
    assert "bác sĩ Tiến" in replayed


async def test_a_pasted_secret_is_redacted_before_it_is_stored(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    from meobot.application.chat_memory_service import ChatMemoryService

    secret = "sk-live-abcdefghijklmnop1234567890"
    llm = FakeLLMProvider()
    await service(settings, registry, bot_database, llm).handle_message(
        actor=owner_actor,
        message=f"khoá của mình là {secret}, bạn giữ hộ nhé",
        request_id=request_id,
        target=target,
    )

    async with bot_database.session() as session:
        memory = ChatMemoryService(session, settings)
        thread = await memory.active_thread(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=target.telegram_user_id
        )
        assert thread is not None
        stored = " ".join(row.content for row in await memory.recent_messages(thread.id))

    assert secret not in stored


# --- The last-resort message ----------------------------------------------
def test_the_outage_message_names_a_reference_and_not_the_users_message() -> None:
    """What the user sees only when everything else has already failed."""
    rendered = PROVIDER_DOWN_REPLY.format(reference="a1b2c3d4")
    assert "a1b2c3d4" in rendered
    assert "Các lệnh vận hành vẫn hoạt động" in rendered
    assert FORBIDDEN_ADVICE not in rendered


def test_routes_built_by_helpers_carry_no_reasoning() -> None:
    """``short_reason_label`` is a log label, not chain-of-thought."""
    route = MessageRoute.chat(label="greeting")
    assert route.short_reason_label == "greeting"
    assert len(route.short_reason_label or "") <= 80


async def test_routing_request_sends_names_only_not_schemas(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Routing does not need argument schemas, and sending them was the problem."""
    captured: list[RouteRequest] = []

    class Recording(FakeLLMProvider):
        async def route_message(self, request: RouteRequest) -> MessageRoute:
            captured.append(request)
            return MessageRoute.chat(label="test")

    await service(settings, registry, bot_database, Recording()).handle_message(
        actor=owner_actor,
        message="mình đang nghĩ về kế hoạch quý sau",
        request_id=request_id,
        target=target,
    )

    assert captured, "the router was not consulted"
    assert all(isinstance(name, str) for name in captured[0].tool_names)
    assert "arguments_schema" not in captured[0].prompt_context
