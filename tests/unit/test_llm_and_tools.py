"""Fake LLM routing and the full message -> plan -> policy -> tool pipeline."""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr

from meobot.application.conversation_service import ConversationService
from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError, ToolArgumentError, ToolNotFoundError
from meobot.db.models.audit_log import AuditLog
from meobot.domain.conversations.decision import ConversationMode
from meobot.domain.identity.models import Actor, Role
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import ActionPlan, RiskLevel
from meobot.integrations.llm.base import PlanningRequest
from meobot.integrations.llm.factory import build_llm_provider
from meobot.integrations.llm.fake import FakeLLMProvider, strip_accents
from meobot.tools.base import ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import FakeDatabase, StubHealthService


@pytest.fixture
def registry() -> ToolRegistry:
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


@pytest.fixture
def conversation(settings: Settings, registry: ToolRegistry) -> ConversationService:
    return ConversationService(
        llm=FakeLLMProvider(),
        registry=registry,
        policy=PolicyEngine(registry.policies()),
        database=FakeDatabase(),  # type: ignore[arg-type]
        settings=settings,
    )


# --- Provider selection -----------------------------------------------------
def test_fake_provider_is_the_default(settings: Settings) -> None:
    provider = build_llm_provider(settings)
    assert provider.name == "fake"


def test_unimplemented_provider_fails_loudly(settings: Settings) -> None:
    """Production must not silently downgrade to the fake provider."""
    with pytest.raises(ConfigurationError, match="not implemented"):
        build_llm_provider(settings.model_copy(update={"llm_provider": "openclaw"}))


def test_real_provider_requires_a_key(settings: Settings) -> None:
    """A misconfigured real provider fails at startup, not at first message."""
    with pytest.raises(ConfigurationError, match="LLM_API_KEY"):
        build_llm_provider(settings.model_copy(update={"llm_provider": "openai"}))


def test_real_provider_requires_an_explicit_model(settings: Settings) -> None:
    """No model name is hardcoded anywhere."""
    with pytest.raises(ConfigurationError, match="LLM_MODEL"):
        build_llm_provider(
            settings.model_copy(
                update={"llm_provider": "openai", "llm_api_key": SecretStr("test-key")}
            )
        )


# --- Fake routing -----------------------------------------------------------
def test_strip_accents_folds_vietnamese() -> None:
    assert strip_accents("Sức khoẻ hệ thống") == "suc khoe he thong"


@pytest.mark.parametrize(
    "message",
    ["kiểm tra hệ thống", "health check đi", "hệ thống thế nào", "ping"],
)
async def test_health_intent_is_recognised(message: str, registry: ToolRegistry) -> None:
    provider = FakeLLMProvider()
    plan = await provider.plan(
        PlanningRequest(
            message=message,
            actor_role=Role.OWNER,
            available_tools=registry.summaries_for(Role.OWNER),
        )
    )
    assert plan.tool_name == "system.health"
    assert plan.risk_level is RiskLevel.LOW


async def test_script_type_intent_is_recognised(registry: ToolRegistry) -> None:
    provider = FakeLLMProvider()
    plan = await provider.plan(
        PlanningRequest(
            message="cho xem các thể loại kịch bản",
            actor_role=Role.OWNER,
            available_tools=registry.summaries_for(Role.OWNER),
        )
    )
    assert plan.tool_name == "script_type.list"


async def test_unrecognised_message_becomes_unknown(registry: ToolRegistry) -> None:
    """The fake provider never guesses - it says 'unknown'."""
    provider = FakeLLMProvider()
    plan = await provider.plan(
        PlanningRequest(
            message="đặt hộ tôi vé máy bay đi Đà Lạt",
            actor_role=Role.OWNER,
            available_tools=registry.summaries_for(Role.OWNER),
        )
    )
    assert plan.is_unknown
    assert plan.tool_name is None


async def test_provider_never_names_a_tool_the_actor_cannot_see() -> None:
    """With an empty catalogue there is nothing to propose."""
    provider = FakeLLMProvider()
    plan = await provider.plan(
        PlanningRequest(message="kiểm tra hệ thống", actor_role=Role.EMPLOYEE, available_tools=[])
    )
    assert plan.is_unknown


# --- Registry ---------------------------------------------------------------
def test_registry_exposes_expected_tools(registry: ToolRegistry) -> None:
    assert "system.health" in registry
    assert "script_type.list" in registry
    assert "sheet_profile.create" in registry


def test_no_destructive_or_publishing_tool_is_registered_in_v1(
    registry: ToolRegistry,
) -> None:
    """Milestone 1 ships no tool that can publish or delete."""
    for tool in registry.policies().values():
        assert not tool.destructive
    assert not any(name.startswith("publish.") for name in registry.names)
    assert not any(name.endswith(".delete") for name in registry.names)


def test_employee_sees_fewer_tools_than_owner(registry: ToolRegistry) -> None:
    employee_tools = {tool.name for tool in registry.visible_to(Role.EMPLOYEE)}
    owner_tools = {tool.name for tool in registry.visible_to(Role.OWNER)}
    assert employee_tools < owner_tools
    assert "sheet_profile.create" not in employee_tools


def test_unknown_tool_lookup_raises(registry: ToolRegistry) -> None:
    with pytest.raises(ToolNotFoundError):
        registry.get("script.approve")


def test_duplicate_registration_is_refused(registry: ToolRegistry) -> None:
    existing = registry.get("system.health")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(existing)


async def test_tool_arguments_are_validated(registry: ToolRegistry, owner_actor: Actor) -> None:
    """Arguments proposed by a model are schema-checked before the handler runs."""
    tool = registry.get("sheet_profile.create")
    with pytest.raises(ToolArgumentError):
        tool.parse_arguments({"name": "only a name"})


# --- End-to-end pipeline (fakes only) --------------------------------------
async def test_message_routes_through_policy_into_the_tool(
    conversation: ConversationService, owner_actor: Actor, request_id: uuid.UUID
) -> None:
    reply = await conversation.handle_message(
        actor=owner_actor, message="kiểm tra hệ thống", request_id=request_id
    )

    assert reply.plan is not None and reply.plan.tool_name == "system.health"
    assert reply.decision is not None and reply.decision.executable
    assert "Tình trạng hệ thống" in reply.text


async def test_unrecognised_message_answers_instead_of_refusing(
    conversation: ConversationService, owner_actor: Actor, request_id: uuid.UUID
) -> None:
    """An off-topic message is answered, not reported as an unknown request.

    Before 0.3.0 every message was an attempted tool call, so anything without
    a matching tool came back as "MeoBot chưa hiểu yêu cầu này". Now it is a
    chat decision: the user gets a useful answer and, crucially, no tool runs.
    """
    reply = await conversation.handle_message(
        actor=owner_actor, message="hôm nay trời đẹp nhỉ", request_id=request_id
    )
    assert reply.mode is ConversationMode.CHAT
    assert reply.tool_result is None
    assert reply.plan is None
    assert reply.text.strip()


async def test_denied_plan_is_audited_and_not_executed(
    settings: Settings, registry: ToolRegistry, employee_actor: Actor, request_id: uuid.UUID
) -> None:
    """A plan naming a tool the employee may not use is refused and logged."""
    database = FakeDatabase()
    forced = ActionPlan(
        intent="create_sheet_profile",
        tool_name="sheet_profile.create",
        risk_level=RiskLevel.MEDIUM,
        requires_confirmation=False,
        arguments={},
    )
    service = ConversationService(
        llm=FakeLLMProvider(forced_plan=forced),
        registry=registry,
        policy=PolicyEngine(registry.policies()),
        database=database,  # type: ignore[arg-type]
        settings=settings,
    )

    reply = await service.handle_message(
        actor=employee_actor, message="tạo sheet profile mới", request_id=request_id
    )

    assert reply.text.startswith("⛔")
    assert reply.tool_result is None
    audit_rows = database.fake_session.added_of(AuditLog)
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "tool.denied"


async def test_high_risk_plan_stops_for_confirmation(
    settings: Settings, registry: ToolRegistry, owner_actor: Actor, request_id: uuid.UUID
) -> None:
    """Even the owner cannot execute a high-risk plan without confirming it."""
    database = FakeDatabase()
    registry.register(_high_risk_tool_definition())
    forced = ActionPlan(
        intent="danger",
        tool_name="test.high_risk",
        risk_level=RiskLevel.HIGH,
        requires_confirmation=True,
    )
    service = ConversationService(
        llm=FakeLLMProvider(forced_plan=forced),
        registry=registry,
        policy=PolicyEngine(registry.policies()),
        database=database,  # type: ignore[arg-type]
        settings=settings,
    )

    reply = await service.handle_message(
        actor=owner_actor, message="làm việc nguy hiểm", request_id=request_id
    )

    assert reply.confirmation_token is not None
    assert "/confirm" in reply.text
    assert reply.tool_result is None


def _high_risk_tool_definition():  # type: ignore[no-untyped-def]
    """A high-risk tool used only to prove the confirmation gate fires."""
    from meobot.domain.permissions.matrix import Permission
    from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult

    async def handler(context: ToolContext, arguments: NoArguments) -> ToolResult:
        return ToolResult(success=True, message="đã chạy")

    return ToolDefinition(
        name="test.high_risk",
        description="Chỉ dùng trong test.",
        handler=handler,
        risk_level=RiskLevel.HIGH,
        required_permission=Permission.SETTINGS_WRITE,
        read_only=False,
    )
