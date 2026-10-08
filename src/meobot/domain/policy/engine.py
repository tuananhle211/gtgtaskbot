"""The policy engine: the single chokepoint between intent and execution.

Every LLM-originated action passes through :meth:`PolicyEngine.evaluate`. The
engine is pure and synchronous - it reads facts, it never performs I/O - which
makes it exhaustively testable.
"""

from __future__ import annotations

from collections.abc import Mapping

from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import has_permission
from meobot.domain.policy.models import (
    ActionPlan,
    DecisionCode,
    PolicyContext,
    PolicyDecision,
    RiskLevel,
    ToolPolicy,
)
from meobot.domain.scripts.workflow import ScriptStatus
from meobot.domain.videos.workflow import PUBLISHABLE_STATUSES, VideoStatus

#: Workflow guards: tool name -> the states from which it may run.
SCRIPT_STATE_GUARDS: Mapping[str, frozenset[ScriptStatus]] = {
    "script.approve": frozenset({ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL}),
    "script.approve_for_production": frozenset(
        {ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL, ScriptStatus.AI_REVIEWED}
    ),
    "script.request_revision": frozenset(
        {
            ScriptStatus.IMPORTED,
            ScriptStatus.SUBMITTED_FOR_REVIEW,
            ScriptStatus.REVIEWING,
            ScriptStatus.AI_REVIEWED,
            ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
            ScriptStatus.APPROVED_FOR_PRODUCTION,
        }
    ),
    "script.request_review": frozenset(
        {
            ScriptStatus.DRAFT,
            ScriptStatus.IMPORTED,
            ScriptStatus.SUBMITTED_FOR_REVIEW,
            ScriptStatus.REVIEWING,
            ScriptStatus.AI_REVIEWED,
            ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
            ScriptStatus.REVISION_REQUIRED,
            ScriptStatus.APPROVED_FOR_PRODUCTION,
        }
    ),
}

VIDEO_STATE_GUARDS: Mapping[str, frozenset[VideoStatus]] = {
    "video.approve": frozenset({VideoStatus.WAITING_FOR_VIDEO_APPROVAL}),
    "publish.facebook": PUBLISHABLE_STATUSES,
    "publish.tiktok": PUBLISHABLE_STATUSES,
}


#: Team leads and above are not restricted by per-resource ownership.
_OWNERSHIP_BYPASS_RANK = 20


class PolicyEngine:
    """Decides whether an :class:`ActionPlan` may execute, and under what terms.

    Args:
        tool_policies: Policy metadata keyed by tool name, normally supplied by
            :class:`meobot.tools.registry.ToolRegistry`.
        deny_destructive: When True (the default for v1), any tool flagged
            ``destructive`` is refused regardless of the actor's role.
        confirm_from: Lowest risk level that requires explicit confirmation.
    """

    def __init__(
        self,
        tool_policies: Mapping[str, ToolPolicy],
        *,
        deny_destructive: bool = True,
        confirm_from: RiskLevel = RiskLevel.HIGH,
    ) -> None:
        self._policies = tool_policies
        self._deny_destructive = deny_destructive
        self._confirm_from = confirm_from

    def evaluate(
        self,
        plan: ActionPlan,
        actor: Actor,
        context: PolicyContext | None = None,
    ) -> PolicyDecision:
        """Return the verdict for ``plan`` executed by ``actor``.

        The checks run in a fixed order - registration, activity, tool
        existence, permission, destructiveness, ownership, workflow state,
        confirmation - so a denial always reports the *first* failing reason.
        """
        ctx = context or PolicyContext()

        if actor.telegram_user_id is None and actor.user_id is None:
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.NOT_REGISTERED,
                reason="Tài khoản chưa được đăng ký với TasksBot.",
                effective_risk=plan.risk_level,
            )

        if not actor.active:
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.INACTIVE_ACTOR,
                reason="Tài khoản đã bị vô hiệu hoá.",
                effective_risk=plan.risk_level,
            )

        if plan.tool_name is None:
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.UNKNOWN_INTENT,
                reason="TasksBot chưa hiểu yêu cầu này.",
                effective_risk=RiskLevel.LOW,
            )

        policy = self._policies.get(plan.tool_name)
        if policy is None:
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.UNKNOWN_TOOL,
                reason=f"Công cụ {plan.tool_name!r} không tồn tại.",
                effective_risk=RiskLevel.HIGH,
            )

        # The tool's declared risk always wins over whatever the LLM claimed.
        effective_risk = RiskLevel.max_of(policy.risk_level, plan.risk_level)

        if policy.min_role is not None and actor.role.rank < policy.min_role.rank:
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.MISSING_PERMISSION,
                reason=f"Cần vai trò {role_label(policy.min_role)} trở lên.",
                effective_risk=effective_risk,
            )

        permission = policy.required_permission
        if permission is not None and not has_permission(actor.role, permission):
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.MISSING_PERMISSION,
                reason=f"Vai trò {role_label(actor.role)} không có quyền {permission.value}.",
                effective_risk=effective_risk,
                missing_permission=permission,
            )

        if policy.destructive and self._deny_destructive:
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.DESTRUCTIVE_DENIED,
                reason="Hành động xoá dữ liệu bị từ chối trong phiên bản hiện tại.",
                effective_risk=RiskLevel.HIGH,
            )

        ownership_denial = self._check_ownership(policy, actor, ctx, effective_risk)
        if ownership_denial is not None:
            return ownership_denial

        workflow_denial = self._check_workflow_state(plan.tool_name, ctx, effective_risk)
        if workflow_denial is not None:
            return workflow_denial

        needs_confirmation = effective_risk.rank >= self._confirm_from.rank
        if needs_confirmation and not ctx.confirmed:
            return PolicyDecision(
                allowed=True,
                code=DecisionCode.CONFIRMATION_REQUIRED,
                reason="Hành động rủi ro cao, cần xác nhận trước khi thực hiện.",
                effective_risk=effective_risk,
                requires_confirmation=True,
            )

        return PolicyDecision(
            allowed=True,
            code=DecisionCode.ALLOWED,
            reason="Được phép thực hiện.",
            effective_risk=effective_risk,
            requires_confirmation=False,
        )

    def _check_ownership(
        self,
        policy: ToolPolicy,
        actor: Actor,
        ctx: PolicyContext,
        effective_risk: RiskLevel,
    ) -> PolicyDecision | None:
        """Employees may only touch resources they own; team leads and up may not."""
        if not policy.requires_ownership or ctx.resource_owner_user_id is None:
            return None
        if actor.role.rank >= _OWNERSHIP_BYPASS_RANK:
            return None
        if actor.user_id is not None and actor.user_id == ctx.resource_owner_user_id:
            return None
        return PolicyDecision(
            allowed=False,
            code=DecisionCode.NOT_RESOURCE_OWNER,
            reason="Bạn không sở hữu tài nguyên này.",
            effective_risk=effective_risk,
        )

    def _check_workflow_state(
        self,
        tool_name: str,
        ctx: PolicyContext,
        effective_risk: RiskLevel,
    ) -> PolicyDecision | None:
        """Reject actions that do not match the entity's current workflow state.

        A guard is only enforced when the caller actually supplied the relevant
        status; a missing status means 'not applicable to this call'.
        """
        script_guard = SCRIPT_STATE_GUARDS.get(tool_name)
        if (
            script_guard is not None
            and ctx.script_status is not None
            and ctx.script_status not in script_guard
        ):
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.INVALID_WORKFLOW_STATE,
                reason=(
                    f"Kịch bản đang ở trạng thái {ctx.script_status.value!r}, "
                    "không thể thực hiện thao tác này."
                ),
                effective_risk=effective_risk,
            )

        video_guard = VIDEO_STATE_GUARDS.get(tool_name)
        if (
            video_guard is not None
            and ctx.video_status is not None
            and ctx.video_status not in video_guard
        ):
            return PolicyDecision(
                allowed=False,
                code=DecisionCode.INVALID_WORKFLOW_STATE,
                reason=(
                    f"Video đang ở trạng thái {ctx.video_status.value!r}, chưa được duyệt để đăng."
                ),
                effective_risk=effective_risk,
            )
        return None
