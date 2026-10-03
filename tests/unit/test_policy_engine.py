"""Policy engine: the rules that decide what an LLM plan is allowed to do."""

from __future__ import annotations

import uuid

import pytest

from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import (
    ActionPlan,
    DecisionCode,
    PolicyContext,
    RiskLevel,
)
from meobot.domain.scripts.workflow import ScriptStatus
from meobot.domain.videos.workflow import VideoStatus


class StubTool:
    """A ToolPolicy-shaped object; the engine only reads these attributes."""

    def __init__(
        self,
        name: str,
        *,
        risk_level: RiskLevel = RiskLevel.LOW,
        required_permission: Permission | None = None,
        destructive: bool = False,
        requires_ownership: bool = False,
        min_role: Role | None = None,
    ) -> None:
        self.name = name
        self.risk_level = risk_level
        self.required_permission = required_permission
        self.destructive = destructive
        self.requires_ownership = requires_ownership
        self.min_role = min_role


TOOLS = {
    tool.name: tool
    for tool in [
        StubTool("system.health", required_permission=Permission.SYSTEM_HEALTH),
        StubTool("script_type.list", required_permission=Permission.SCRIPT_TYPE_READ),
        StubTool(
            "sheet_profile.create",
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SHEET_PROFILE_WRITE,
        ),
        StubTool(
            "script.approve",
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_APPROVE,
        ),
        StubTool(
            "publish.facebook",
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.PUBLISH_SOCIAL,
        ),
        StubTool(
            "data.delete",
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.DATA_DELETE,
            destructive=True,
        ),
        StubTool(
            "hr.leave_request",
            risk_level=RiskLevel.LOW,
            required_permission=Permission.HR_REQUEST_LEAVE,
            requires_ownership=True,
        ),
    ]
}


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine(TOOLS)


def plan_for(tool_name: str | None, **kwargs: object) -> ActionPlan:
    return ActionPlan(
        intent=kwargs.pop("intent", "test_intent"),  # type: ignore[arg-type]
        tool_name=tool_name,
        risk_level=kwargs.pop("risk_level", RiskLevel.LOW),  # type: ignore[arg-type]
        requires_confirmation=False,
        arguments=kwargs.pop("arguments", {}),  # type: ignore[arg-type]
    )


# --- Low risk ---------------------------------------------------------------
def test_health_check_runs_without_confirmation(engine: PolicyEngine, owner_actor: Actor) -> None:
    decision = engine.evaluate(plan_for("system.health"), owner_actor)
    assert decision.executable
    assert decision.code is DecisionCode.ALLOWED


def test_employee_may_read_script_types(engine: PolicyEngine, employee_actor: Actor) -> None:
    decision = engine.evaluate(plan_for("script_type.list"), employee_actor)
    assert decision.executable


# --- Medium risk ------------------------------------------------------------
def test_medium_risk_runs_without_confirmation_for_admin(engine: PolicyEngine) -> None:
    admin = Actor(user_id=uuid.uuid4(), telegram_user_id=1, full_name="Admin", role=Role.ADMIN)
    decision = engine.evaluate(plan_for("sheet_profile.create"), admin)
    assert decision.executable
    assert decision.effective_risk is RiskLevel.MEDIUM


def test_employee_cannot_create_sheet_profile(engine: PolicyEngine, employee_actor: Actor) -> None:
    decision = engine.evaluate(plan_for("sheet_profile.create"), employee_actor)
    assert not decision.allowed
    assert decision.code is DecisionCode.MISSING_PERMISSION
    assert decision.missing_permission is Permission.SHEET_PROFILE_WRITE


# --- High risk --------------------------------------------------------------
def test_script_approval_requires_confirmation(
    engine: PolicyEngine, team_lead_actor: Actor
) -> None:
    decision = engine.evaluate(
        plan_for("script.approve"),
        team_lead_actor,
        PolicyContext(script_status=ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL),
    )
    assert decision.allowed
    assert decision.requires_confirmation
    assert not decision.executable
    assert decision.code is DecisionCode.CONFIRMATION_REQUIRED


def test_confirmed_high_risk_action_executes(engine: PolicyEngine, team_lead_actor: Actor) -> None:
    decision = engine.evaluate(
        plan_for("script.approve"),
        team_lead_actor,
        PolicyContext(script_status=ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL, confirmed=True),
    )
    assert decision.executable


def test_employee_cannot_approve_scripts(engine: PolicyEngine, employee_actor: Actor) -> None:
    decision = engine.evaluate(plan_for("script.approve"), employee_actor)
    assert not decision.allowed
    assert decision.code is DecisionCode.MISSING_PERMISSION


def test_publishing_requires_confirmation(engine: PolicyEngine) -> None:
    admin = Actor(user_id=uuid.uuid4(), telegram_user_id=1, full_name="Admin", role=Role.ADMIN)
    decision = engine.evaluate(
        plan_for("publish.facebook"),
        admin,
        PolicyContext(video_status=VideoStatus.APPROVED_FOR_PUBLISH),
    )
    assert decision.requires_confirmation


# --- Destructive ------------------------------------------------------------
def test_delete_is_denied_even_for_the_owner(engine: PolicyEngine, owner_actor: Actor) -> None:
    """v1 refuses data deletion outright, regardless of role."""
    decision = engine.evaluate(plan_for("data.delete"), owner_actor)
    assert not decision.allowed
    assert decision.code is DecisionCode.DESTRUCTIVE_DENIED


def test_delete_can_be_enabled_deliberately(owner_actor: Actor) -> None:
    """The refusal is a policy choice, not a hard-coded impossibility."""
    permissive = PolicyEngine(TOOLS, deny_destructive=False)
    decision = permissive.evaluate(plan_for("data.delete"), owner_actor)
    assert decision.allowed
    assert decision.requires_confirmation


# --- Workflow state ---------------------------------------------------------
def test_approval_blocked_in_wrong_workflow_state(
    engine: PolicyEngine, team_lead_actor: Actor
) -> None:
    decision = engine.evaluate(
        plan_for("script.approve"),
        team_lead_actor,
        PolicyContext(script_status=ScriptStatus.DRAFT),
    )
    assert not decision.allowed
    assert decision.code is DecisionCode.INVALID_WORKFLOW_STATE


def test_publish_blocked_when_video_not_approved(engine: PolicyEngine) -> None:
    """An approved script is not permission to publish - the video must be approved."""
    admin = Actor(user_id=uuid.uuid4(), telegram_user_id=1, full_name="Admin", role=Role.ADMIN)
    decision = engine.evaluate(
        plan_for("publish.facebook"),
        admin,
        PolicyContext(video_status=VideoStatus.EDITING, confirmed=True),
    )
    assert not decision.allowed
    assert decision.code is DecisionCode.INVALID_WORKFLOW_STATE


# --- Ownership --------------------------------------------------------------
def test_employee_cannot_act_on_someone_elses_resource(
    engine: PolicyEngine, employee_actor: Actor
) -> None:
    decision = engine.evaluate(
        plan_for("hr.leave_request"),
        employee_actor,
        PolicyContext(resource_owner_user_id=uuid.uuid4()),
    )
    assert not decision.allowed
    assert decision.code is DecisionCode.NOT_RESOURCE_OWNER


def test_employee_may_act_on_own_resource(engine: PolicyEngine, employee_actor: Actor) -> None:
    decision = engine.evaluate(
        plan_for("hr.leave_request"),
        employee_actor,
        PolicyContext(resource_owner_user_id=employee_actor.user_id),
    )
    assert decision.executable


def test_team_lead_bypasses_ownership(engine: PolicyEngine, team_lead_actor: Actor) -> None:
    decision = engine.evaluate(
        plan_for("hr.leave_request"),
        team_lead_actor,
        PolicyContext(resource_owner_user_id=uuid.uuid4()),
    )
    assert decision.executable


# --- Rejections -------------------------------------------------------------
def test_unknown_tool_is_denied(engine: PolicyEngine, owner_actor: Actor) -> None:
    """A hallucinated tool name cannot become an action."""
    decision = engine.evaluate(plan_for("meta.transfer_money"), owner_actor)
    assert not decision.allowed
    assert decision.code is DecisionCode.UNKNOWN_TOOL


def test_unknown_intent_is_not_an_error(engine: PolicyEngine, owner_actor: Actor) -> None:
    decision = engine.evaluate(ActionPlan.unknown(), owner_actor)
    assert not decision.allowed
    assert decision.code is DecisionCode.UNKNOWN_INTENT


def test_unregistered_actor_is_denied(engine: PolicyEngine) -> None:
    stranger = Actor(user_id=None, telegram_user_id=None, full_name="?", role=Role.EMPLOYEE)
    decision = engine.evaluate(plan_for("system.health"), stranger)
    assert not decision.allowed
    assert decision.code is DecisionCode.NOT_REGISTERED


def test_inactive_actor_is_denied(engine: PolicyEngine, employee_actor: Actor) -> None:
    disabled = employee_actor.model_copy(update={"active": False})
    decision = engine.evaluate(plan_for("system.health"), disabled)
    assert not decision.allowed
    assert decision.code is DecisionCode.INACTIVE_ACTOR


def test_tool_risk_overrides_a_lying_plan(engine: PolicyEngine, team_lead_actor: Actor) -> None:
    """An LLM claiming 'low risk' cannot downgrade a high-risk tool."""
    plan = ActionPlan(
        intent="approve_script",
        tool_name="script.approve",
        risk_level=RiskLevel.LOW,
        requires_confirmation=False,
    )
    decision = engine.evaluate(plan, team_lead_actor)
    assert decision.effective_risk is RiskLevel.HIGH
    assert decision.requires_confirmation
