"""The hybrid conversation architecture: chat, tool and clarify.

The property that matters most here is negative: **a chat decision cannot
execute anything.** It is enforced twice - once by the model (a ``chat``
decision may not carry an ``ActionPlan``) and once by the service (only the
tool branch reaches ``execute_plan``) - and both are tested, because a single
enforcement point is one refactor away from being removed.

The rest covers the reasons the old architecture was wrong: an ordinary
question came back as "unknown request", there was no way to ask for
clarification instead of guessing, and there was nowhere to keep context
between turns.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from meobot.application.chat_memory_service import ChatMemoryService
from meobot.application.conversation_service import ConversationService, ConversationTarget
from meobot.core.config import Settings
from meobot.domain.conversations.decision import ConversationDecision, ConversationMode
from meobot.domain.conversations.redaction import (
    PLACEHOLDER,
    looks_like_credential_file,
    prepare_for_storage,
    redact_secrets,
)
from meobot.domain.identity.models import Actor
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import ActionPlan, RiskLevel
from meobot.integrations.llm.base import (
    DecisionRequest,
    MessageRoute,
    PlanningRequest,
    RouteRequest,
)
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.tools.base import ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import SqliteDatabase, StubHealthService

BOT_ID = 999
CHAT_ID = 555


@pytest.fixture
def registry() -> ToolRegistry:
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


@pytest.fixture
def target() -> ConversationTarget:
    return ConversationTarget(bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=777000111)


def _service(
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


# --- The model's own invariants --------------------------------------------
def test_chat_decision_may_not_carry_an_action_plan() -> None:
    """The structural guarantee: chat mode is not an execution path."""
    with pytest.raises(PydanticValidationError, match="must not carry an action_plan"):
        ConversationDecision(
            mode=ConversationMode.CHAT,
            reply_text="Mình sẽ tạo Sheet ngay",
            action_plan=ActionPlan(intent="create", tool_name="spreadsheet.create_work"),
        )


def test_chat_decision_requires_reply_text() -> None:
    with pytest.raises(PydanticValidationError, match="requires reply_text"):
        ConversationDecision(mode=ConversationMode.CHAT)


def test_tool_decision_requires_an_action_plan() -> None:
    with pytest.raises(PydanticValidationError, match="requires an action_plan"):
        ConversationDecision(mode=ConversationMode.TOOL, reply_text="đang làm")


def test_clarify_decision_requires_a_question() -> None:
    with pytest.raises(PydanticValidationError, match="requires a clarification_question"):
        ConversationDecision(mode=ConversationMode.CLARIFY)


def test_clarify_decision_may_not_smuggle_a_plan() -> None:
    with pytest.raises(PydanticValidationError, match="must not carry an action_plan"):
        ConversationDecision(
            mode=ConversationMode.CLARIFY,
            clarification_question="Duyệt kịch bản nào?",
            action_plan=ActionPlan(intent="approve", tool_name="script.approve"),
        )


def test_internal_summary_is_never_user_visible() -> None:
    """It is a log label, not a message - and not chain-of-thought."""
    decision = ConversationDecision.chat("Chào bạn", internal_summary="matched:greeting")
    assert decision.user_visible_text() == "Chào bạn"
    assert "matched:greeting" not in (decision.user_visible_text() or "")


def test_only_tool_mode_reports_itself_as_executing() -> None:
    plan = ActionPlan(intent="health", tool_name="system.health", risk_level=RiskLevel.LOW)
    assert ConversationDecision.tool(plan).executes_a_tool is True
    assert ConversationDecision.chat("xin chào").executes_a_tool is False
    assert ConversationDecision.clarify("cái gì cơ?").executes_a_tool is False


# --- The service's routing --------------------------------------------------
async def test_capability_question_is_answered_conversationally(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """ "Bạn làm được gì?" - the question that used to return an error."""
    service = _service(settings, registry, bot_database)
    reply = await service.handle_message(
        actor=owner_actor, message="Bạn làm được gì?", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    assert "Sheet" in reply.text or "kịch bản" in reply.text


async def test_brainstorming_never_executes_a_tool(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """Asking for ideas is a conversation, not an operation."""
    service = _service(settings, registry, bot_database)
    reply = await service.handle_message(
        actor=owner_actor,
        message="Tôi đang bí ý tưởng content cho bác sĩ thẩm mỹ",
        request_id=request_id,
        target=target,
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    assert reply.plan is None


async def test_operational_request_goes_through_the_policy_engine(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """An operational phrase still becomes a plan, a decision and a tool run."""
    service = _service(settings, registry, bot_database)
    reply = await service.handle_message(
        actor=owner_actor, message="kiểm tra hệ thống", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.TOOL
    assert reply.plan is not None and reply.plan.tool_name == "system.health"
    assert reply.decision is not None, "the policy engine did not see this plan"
    assert reply.tool_result is not None


async def test_ambiguous_approval_asks_instead_of_guessing(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """ "Duyệt hết đi" must never resolve itself into an approval."""
    service = _service(settings, registry, bot_database)
    reply = await service.handle_message(
        actor=owner_actor, message="Duyệt hết đi", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CLARIFY
    assert reply.tool_result is None
    assert "?" in reply.text
    assert reply.text.count("?") == 1, "a clarification is one question, not an interrogation"


async def test_chat_mode_cannot_execute_even_a_forced_decision(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """A provider that *wants* to act while chatting still cannot.

    The reply it writes claims to have synchronised everything. Because the
    turn was routed to chat, the generation call is given no tool catalogue and
    the chat branch never reaches ``execute_plan`` - so nothing runs, whatever
    the sentence says. The test asserts on the absence of a tool result, not on
    the wording.
    """
    llm = FakeLLMProvider(
        forced_route=MessageRoute.chat(confidence=0.9, label="test:forced_chat"),
        forced_decision=ConversationDecision.chat("Mình đã đồng bộ xong tất cả Sheet rồi nhé!"),
    )
    service = _service(settings, registry, bot_database, llm)
    reply = await service.handle_message(
        actor=owner_actor,
        # Not one of the deterministic operational patterns, so routing is the
        # provider's to decide - which is the case this test is about.
        message="tình hình nội dung tuần này thế nào",
        request_id=request_id,
        target=target,
    )

    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None, "chat mode executed a tool"
    assert reply.plan is None
    assert not llm.seen_messages, "chat mode asked the provider to plan a tool call"


async def test_malformed_provider_output_executes_nothing(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """A provider that raises degrades to a clarification, never to a tool run.

    The message is deliberately operational ("đồng bộ các sheet"), which is the
    hard case: the router is unusable *and* the deterministic patterns say this
    is an operation, so the only thing standing between a broken provider and a
    real synchronisation is the rule that an unusable plan asks instead of
    guessing.
    """

    class BrokenProvider(FakeLLMProvider):
        async def route_message(self, request: RouteRequest) -> MessageRoute:
            raise RuntimeError("provider returned nonsense")

        async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
            return ActionPlan.unknown(reasoning="provider returned nonsense")

        async def decide(self, request: DecisionRequest) -> ConversationDecision:
            raise RuntimeError("provider returned nonsense")

    service = _service(settings, registry, bot_database, BrokenProvider())
    reply = await service.handle_message(
        actor=owner_actor, message="đồng bộ các sheet", request_id=request_id, target=target
    )

    assert reply.mode is ConversationMode.CLARIFY
    assert reply.tool_result is None
    assert "Traceback" not in reply.text
    assert "provider returned nonsense" not in reply.text


async def test_a_tool_that_did_not_run_is_never_reported_as_success(
    settings: Settings,
    registry: ToolRegistry,
    employee_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """A denied plan produces a refusal, not a claim that it worked."""
    plan = ActionPlan(
        intent="create_sheet_profile",
        tool_name="sheet_profile.create",
        risk_level=RiskLevel.MEDIUM,
        requires_confirmation=False,
        arguments={
            "name": "x",
            "spreadsheet_id": "abc",
            "sheet_name": "Tab",
            "field_mapping": {"script_body": ["Nội dung"]},
        },
    )
    llm = FakeLLMProvider(forced_decision=ConversationDecision.tool(plan))
    service = _service(settings, registry, bot_database, llm)

    reply = await service.handle_message(
        actor=employee_actor, message="tạo sheet profile", request_id=request_id, target=target
    )

    assert reply.tool_result is None
    assert reply.decision is not None and not reply.decision.allowed
    assert "⛔" in reply.text


async def test_chat_disabled_refuses_conversation_but_keeps_tools(
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """``CHAT_ENABLED=false`` turns off chat, not the whole bot."""
    disabled = Settings(chat_enabled=False)
    service = _service(disabled, registry, bot_database)

    chatty = await service.handle_message(
        actor=owner_actor, message="xin chào", request_id=request_id, target=target
    )
    assert chatty.mode is ConversationMode.CLARIFY

    operational = await service.handle_message(
        actor=owner_actor, message="kiểm tra hệ thống", request_id=request_id, target=target
    )
    assert operational.mode is ConversationMode.TOOL
    assert operational.tool_result is not None


# --- Memory -----------------------------------------------------------------
async def test_conversation_context_survives_a_restart(
    settings: Settings,
    registry: ToolRegistry,
    owner_actor: Actor,
    request_id: uuid.UUID,
    bot_database: SqliteDatabase,
    target: ConversationTarget,
) -> None:
    """History lives in the database, so a new service instance still has it.

    A fresh ``ConversationService`` over the same database is exactly what a
    restarted bot container is.
    """
    first = _service(settings, registry, bot_database)
    await first.handle_message(
        actor=owner_actor,
        message="Kênh của bác sĩ Tiến tập trung vào da liễu",
        request_id=request_id,
        target=target,
    )

    async with bot_database.session() as session:
        memory = ChatMemoryService(session, settings)
        context = await memory.load_context(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=owner_actor.telegram_user_id or 0
        )

    assert context.message_count >= 2
    assert any("bác sĩ Tiến" in turn.content for turn in context.history)


async def test_context_window_stays_bounded(
    settings: Settings,
    owner_actor: Actor,
    bot_database: SqliteDatabase,
) -> None:
    """However long the thread, the replayed history is capped."""
    async with bot_database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        thread = await memory.get_or_create_thread(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=1
        )
        for index in range(settings.chat_history_max_messages * 3):
            await memory.record_message(thread=thread, role="user", content=f"tin nhắn số {index}")

    async with bot_database.session() as session:
        context = await ChatMemoryService(session, settings).load_context(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=1
        )

    assert context.message_count == settings.chat_history_max_messages * 3
    assert len(context.history) == settings.chat_history_max_messages
    # The window keeps the *newest* turns, not the oldest.
    assert "tin nhắn số 59" in context.history[-1].content


async def test_new_thread_archives_the_previous_one_without_deleting_it(
    settings: Settings,
    bot_database: SqliteDatabase,
) -> None:
    """``/new_chat`` starts fresh; the old conversation is kept, not destroyed."""
    async with bot_database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        first = await memory.get_or_create_thread(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=2
        )
        await memory.record_message(thread=first, role="user", content="chủ đề cũ")
        first_id = first.id

    async with bot_database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        second = await memory.start_thread(bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=2)
        assert second.id != first_id

    async with bot_database.session() as session:
        memory = ChatMemoryService(session, settings)
        # The old messages are still there, they are simply not replayed.
        assert await memory.message_count(first_id) == 1
        context = await memory.load_context(bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=2)
        assert context.thread_id != first_id
        assert context.history == ()


# --- Redaction --------------------------------------------------------------
@pytest.mark.parametrize(
    "secret",
    [
        "8123456789:AAF-abcdefghijklmnopqrstuvwxyz0123456",
        "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        "ya29.a0AfB_abcdefghijklmnopqrstuv",
        "eyJhbGciOiJSUzI1NiIsImtpZCI6Ing.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4f",
    ],
)
def test_credentials_are_redacted_before_storage(secret: str) -> None:
    """Whatever shape a pasted credential takes, it does not reach the row."""
    stored = prepare_for_storage(f"khoá của mình là {secret} nhé")
    assert stored is not None
    assert secret not in stored
    assert PLACEHOLDER in stored


def test_database_password_is_redacted_but_the_dsn_stays_readable() -> None:
    stored = redact_secrets("postgresql://meobot:sup3r-s3cret@db:5432/meobot")
    assert "sup3r-s3cret" not in stored
    assert "meobot" in stored


def test_authorization_header_is_redacted() -> None:
    stored = redact_secrets("Authorization: Bearer abcdefghijklmnop0123456789")
    assert "abcdefghijklmnop0123456789" not in stored


def test_a_pasted_service_account_file_is_not_stored_at_all() -> None:
    """Partial redaction of a key file still leaves a key file."""
    blob = (
        '{"type": "service_account", "project_id": "meobot", '
        '"private_key_id": "abc123", "private_key": '
        '"-----BEGIN PRIVATE KEY-----\\nMIIEvQ...\\n-----END PRIVATE KEY-----\\n", '
        '"client_email": "meobot@example.iam.gserviceaccount.com"}'
    )
    assert looks_like_credential_file(blob)
    assert prepare_for_storage(blob) is None


def test_ordinary_vietnamese_content_is_left_alone() -> None:
    """Redaction must not mangle the content it is protecting."""
    text = "Kịch bản TikTok cho bác sĩ Tiến, deadline 15/08, hook nói về da liễu."
    assert prepare_for_storage(text) == text


async def test_secret_redaction_happens_before_persistence(
    settings: Settings,
    bot_database: SqliteDatabase,
) -> None:
    """End to end: the token never reaches ``conversation_messages``."""
    token = "8123456789:AAF-abcdefghijklmnopqrstuvwxyz0123456"
    async with bot_database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        thread = await memory.get_or_create_thread(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=3
        )
        stored = await memory.record_message(
            thread=thread, role="user", content=f"token là {token}"
        )

    assert stored is not None
    assert token not in stored.content
    assert PLACEHOLDER in stored.content


async def test_a_message_that_is_only_a_secret_is_dropped_entirely(
    settings: Settings,
    bot_database: SqliteDatabase,
) -> None:
    """Nothing worth keeping is kept, and the conversation continues anyway."""
    async with bot_database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        thread = await memory.get_or_create_thread(
            bot_id=BOT_ID, chat_id=CHAT_ID, telegram_user_id=4
        )
        stored = await memory.record_message(
            thread=thread,
            role="user",
            content='{"type": "service_account", "private_key_id": "x"}',
        )
    assert stored is None


def test_only_known_roles_are_storable(settings: Settings) -> None:
    """A typo'd role would silently corrupt the replayed history."""
    from meobot.application.chat_memory_service import VALID_ROLES

    assert {"user", "assistant", "tool"} == VALID_ROLES


# --- Mention stripping ------------------------------------------------------
def test_bot_mention_is_stripped_from_a_group_message() -> None:
    from meobot.bot.handlers.conversation import strip_bot_mention

    assert strip_bot_mention("@MeoBotDev đồng bộ các Sheet", "MeoBotDev") == "đồng bộ các Sheet"
    assert strip_bot_mention("đồng bộ @MeoBotDev ngay", "MeoBotDev") == "đồng bộ ngay"


def test_mention_stripping_is_case_insensitive() -> None:
    from meobot.bot.handlers.conversation import strip_bot_mention

    assert strip_bot_mention("@meobotdev xin chào", "MeoBotDev") == "xin chào"


def test_a_persons_mention_mid_sentence_is_content_not_addressing() -> None:
    """Stripping every @ would damage the message it is meant to clean up."""
    from meobot.bot.handlers.conversation import strip_bot_mention

    text = "kịch bản này do @linh_content viết"
    assert strip_bot_mention(text, "MeoBotDev") == text
    # Without a known username only a *leading* mention is treated as addressing.
    assert strip_bot_mention(text, None) == text


def test_private_chat_messages_need_no_mention() -> None:
    from meobot.bot.handlers.conversation import strip_bot_mention

    assert strip_bot_mention("đồng bộ các Sheet", "MeoBotDev") == "đồng bộ các Sheet"


def test_vietnamese_is_preserved_through_mention_stripping() -> None:
    from meobot.bot.handlers.conversation import strip_bot_mention

    assert (
        strip_bot_mention("@MeoBotDev tạo Sheet kịch bản bác sĩ Tiến", "MeoBotDev")
        == "tạo Sheet kịch bản bác sĩ Tiến"
    )


# --- Fake provider contract -------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        "xin chào",
        "chào bạn",
        "bạn là ai",
        "bạn làm được gì",
        "cảm ơn",
        "giúp tôi",
        "tôi đang bí ý tưởng",
    ],
)
async def test_fake_provider_answers_the_documented_openers(
    message: str,
    registry: ToolRegistry,
    owner_actor: Actor,
) -> None:
    """Offline, these must produce a real answer rather than an error.

    Given the Owner's full tool catalogue, so the test also proves these
    phrases are not accidentally routed to a tool.
    """
    decision = await FakeLLMProvider().decide(
        DecisionRequest(
            message=message,
            actor_role=owner_actor.role,
            available_tools=registry.summaries_for(owner_actor.role),
        )
    )
    assert decision.mode is ConversationMode.CHAT
    assert decision.reply_text
    assert decision.reply_text.strip()


async def test_fake_provider_admits_it_is_not_a_chatbot() -> None:
    """It must not be mistaken for a real conversational model."""
    decision = await FakeLLMProvider().decide(DecisionRequest(message="xin chào"))
    assert decision.reply_text is not None
    assert "LLM_PROVIDER=openai" in decision.reply_text


async def test_fake_provider_keeps_deterministic_tool_routing(
    registry: ToolRegistry, owner_actor: Actor
) -> None:
    """Operational phrases still route to tools when chat mode exists."""
    decision = await FakeLLMProvider().decide(
        DecisionRequest(
            message="kiểm tra hệ thống",
            actor_role=owner_actor.role,
            available_tools=registry.summaries_for(owner_actor.role),
        )
    )
    assert decision.mode is ConversationMode.TOOL
    assert decision.action_plan is not None
    assert decision.action_plan.tool_name == "system.health"


async def test_fake_provider_never_names_an_unavailable_tool(
    registry: ToolRegistry, employee_actor: Actor
) -> None:
    """The catalogue it is given is the catalogue it may use."""
    available = {tool.name for tool in registry.summaries_for(employee_actor.role)}
    decision = await FakeLLMProvider().decide(
        DecisionRequest(
            message="danh sách sheet",
            actor_role=employee_actor.role,
            available_tools=registry.summaries_for(employee_actor.role),
        )
    )
    if decision.action_plan is not None and decision.action_plan.tool_name is not None:
        assert decision.action_plan.tool_name in available
