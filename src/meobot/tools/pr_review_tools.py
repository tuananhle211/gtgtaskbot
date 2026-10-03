"""Telegram tools over the three human review gates.

These operate ``pr_approval_events`` through
:class:`~meobot.application.pr_approval_service.PrApprovalService` and nothing
else. No handler here writes an approval row, chooses a workflow stage, or
decides whether somebody may review - each of those is a service's job, and
duplicating any of them would give the bot an opinion the web client would not
share.

``pr.review.pending`` is the one with real judgement in it, and the judgement is
borrowed rather than made: it asks
:class:`~meobot.application.pr_capability_service.PrCapabilityService` which
gates this person may actually act at, and lists only content standing at those.
**A ``TEAM_LEAD`` role with no grant sees nothing**, which is the whole point of
Step 1C.1 and would be quietly undone by filtering on role instead.

``pr.review.approve`` never takes the stage from the caller. It reads the
content's current stage and derives the gate from
:data:`~meobot.domain.pr.workflow.STAGE_APPROVAL_GATES`, so a model that
guessed "HEAD_REVIEW" for content sitting at team-lead review cannot skip a
gate by mislabelling an argument. The service checks the same thing again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.core.errors import ToolExecutionError
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.domain.pr.content_views import (
    STAGE_FOR_APPROVAL_CAPABILITY,
    gate_stages_for,
)
from meobot.domain.pr.models import PrApprovalDecision, PrApprovalStage, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES
from meobot.tools.base import ToolContext, ToolDefinition, ToolResult
from meobot.tools.pr_errors import pr_errors
from meobot.tools.pr_presenters import (
    ai_result_label,
    format_pending_review_list,
    format_review_context,
    stage_label,
)
from meobot.tools.pr_support import PrServices, pr_services, resolve_content, timezone_of

#: The workflow stage each grant-backed capability lets somebody act at.
#:
#: Step 1F.2.7a moved the composition into the domain, where the board's queue
#: and the dashboard's already read it, and left this as the alias so the module
#: reads as it did. Three copies of one inverse mapping was two too many.
STAGE_FOR_CAPABILITY: Mapping[PrCapability, PrWorkflowStage] = STAGE_FOR_APPROVAL_CAPABILITY


class PendingArgs(BaseModel):
    """Arguments for ``pr.review.pending``."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=25)


class ReviewReferenceArgs(BaseModel):
    """Arguments for tools addressing one content item under review."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300, description="Mã nội dung (CNT-…).")


class DecisionArgs(BaseModel):
    """Arguments for a review decision.

    There is no ``approval_stage``: it is derived from where the content
    actually stands. See the module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300)
    comment: str | None = Field(default=None, max_length=2000)


class RevisionArgs(BaseModel):
    """Arguments for ``pr.review.request_revision``.

    ``comment`` is required here and optional on approval, because "sửa lại đi"
    with no reason is not a review - the author has nothing to act on.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300)
    comment: str = Field(min_length=3, max_length=2000, description="Lý do cần sửa.")


async def _my_stages(services: PrServices, context: ToolContext) -> list[PrWorkflowStage]:
    """The workflow stages this actor holds a review grant for, right now.

    Used **only** to word the empty state - "you have not been given a review
    right at any gate". It does not decide what is listed: since Step 1F.2.7a
    that is ``PrQueryService.content_awaiting``, which applies each grant's
    scope. Somebody who holds a narrow grant has a stage here and may still have
    nothing waiting, which is a different and correctly-worded sentence.
    """
    granted = await services.capabilities.capabilities_for_actor(context.actor)
    return list(gate_stages_for(granted))


async def _pending_handler(context: ToolContext, arguments: PendingArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        stages = await _my_stages(services, context)
        if not stages:
            return ToolResult(
                success=True,
                message=(
                    "Bạn hiện chưa được cấp quyền duyệt ở bước nào nên không có nội dung "
                    "nào chờ bạn."
                ),
                data={"count": 0, "stages": []},
                entity_type="pr_content",
            )

        # Scoped by the service, from the actor's own grants. Nothing here says
        # which items qualify, and nothing here may.
        contents = list(
            await services.queries.content_awaiting(actor=context.actor, limit=arguments.limit)
        )
        rows: list[dict[str, Any]] = []
        for content in contents:
            version = await services.content.current_version(content.id)
            review = (
                await services.ai_reviews.latest_gating_review(
                    content.id, version_no=version.version_no
                )
                if version is not None
                else None
            )
            rows.append(
                {
                    "code": content.code,
                    "title": content.title,
                    "stage_label": stage_label(content.workflow_stage),
                    "workflow_stage": content.workflow_stage.value,
                    "version_no": version.version_no if version else 0,
                    "ai_result": review.result.value if review else None,
                    "ai_result_label": ai_result_label(review.result) if review else None,
                    "warning_count": len(review.issues or []) if review else 0,
                }
            )
        return ToolResult(
            success=True,
            message=format_pending_review_list(rows),
            data={"count": len(rows), "stages": [stage.value for stage in stages], "items": rows},
            entity_type="pr_content",
        )


async def _context_handler(context: ToolContext, arguments: ReviewReferenceArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        content = await resolve_content(services, actor=context.actor, reference=arguments.content)
        review_context = await services.queries.get_content_review_context(
            actor=context.actor, content_id=content.id
        )
        return ToolResult(
            success=True,
            message=format_review_context(review_context, tz=timezone_of(context)),
            data={
                "code": content.code,
                "workflow_stage": content.workflow_stage.value,
                "version_no": review_context.current_version.version_no,
                "ai_result": (review_context.ai_result.value if review_context.ai_result else None),
                "ai_has_warnings": review_context.ai_has_warnings,
                "approval_count": len(review_context.approvals),
            },
            entity_type="pr_content",
            entity_id=content.code,
        )


async def _gate_for(content_stage: PrWorkflowStage) -> PrApprovalStage:
    """Which gate the content is standing at, or a refusal.

    Derived, never supplied. The service checks it again - this exists so the
    refusal a person reads names the stage rather than an argument they never
    typed.
    """
    gate = STAGE_APPROVAL_GATES.get(content_stage)
    if gate is None:
        raise ToolExecutionError(
            f"Nội dung đang ở bước {stage_label(content_stage)}, không phải bước chờ duyệt.",
            details={"reason": "not_at_a_review_gate", "current": content_stage.value},
        )
    return gate


async def _decide(
    context: ToolContext,
    *,
    reference: str,
    decision: PrApprovalDecision,
    comment: str | None,
    verb: str,
) -> ToolResult:
    """The one path every review decision takes, whatever asked for it.

    Text commands and (later) inline buttons both land here, so a button can
    never do something a sentence could not - there is no second implementation
    for them to diverge from.
    """
    with pr_errors():
        services = pr_services(context)
        content = await resolve_content(services, actor=context.actor, reference=reference)
        gate = await _gate_for(content.workflow_stage)
        version = await services.content.require_current_version(content.id)

        reviewer_user_id = context.actor.user_id
        if reviewer_user_id is None:
            raise ToolExecutionError(
                "Tài khoản của bạn chưa được đăng ký trong MeoBot nên chưa duyệt được.",
                details={"reason": "actor_has_no_user_row"},
            )

        outcome = await services.approvals.record_decision(
            actor=context.actor,
            request_id=context.request_id,
            command=RecordApprovalCommand(
                content_id=content.id,
                reviewer_user_id=reviewer_user_id,
                approval_stage=gate,
                decision=decision,
                version_reviewed=version.version_no,
                comment=comment,
            ),
        )
        return ToolResult(
            success=True,
            message=(
                f"{verb} <b>{content.code}</b> (v{version.version_no}).\n"
                f"Bước hiện tại: {stage_label(outcome.new_stage)}"
            ),
            data={
                "code": content.code,
                "approval_stage": gate.value,
                "decision": decision.value,
                "version_reviewed": version.version_no,
                "workflow_stage": outcome.new_stage.value,
            },
            entity_type="pr_content",
            entity_id=content.code,
        )


async def _approve_handler(context: ToolContext, arguments: DecisionArgs) -> ToolResult:
    return await _decide(
        context,
        reference=arguments.content,
        decision=PrApprovalDecision.APPROVED,
        comment=arguments.comment,
        verb="✅ Đã duyệt",
    )


async def _request_revision_handler(context: ToolContext, arguments: RevisionArgs) -> ToolResult:
    return await _decide(
        context,
        reference=arguments.content,
        decision=PrApprovalDecision.REVISION_REQUIRED,
        comment=arguments.comment,
        verb="↩️ Đã yêu cầu sửa",
    )


async def _reject_handler(context: ToolContext, arguments: DecisionArgs) -> ToolResult:
    return await _decide(
        context,
        reference=arguments.content,
        decision=PrApprovalDecision.REJECTED,
        comment=arguments.comment,
        verb="❌ Đã từ chối và huỷ",
    )


def build_pr_review_tools() -> list[ToolDefinition]:
    """The three human gates, plus the two queries that feed them.

    ``pr.review.reject`` is :attr:`~meobot.domain.policy.models.RiskLevel.HIGH`
    because rejecting cancels the content, and ``HIGH`` is what the existing
    :class:`~meobot.domain.policy.engine.PolicyEngine` already turns into a
    ``/confirm`` round trip. No PR-specific confirmation machinery was written:
    the risk level *is* the confirmation, and it is the same one script
    approval has used since 0.2.0.
    """
    return [
        ToolDefinition(
            name="pr.review.pending",
            description=(
                "Liệt kê nội dung PR đang chờ chính người dùng này duyệt, "
                "dựa trên quyền duyệt thực tế đã được cấp."
            ),
            handler=_pending_handler,
            arguments_model=PendingArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.review.context",
            description=(
                "Xem đầy đủ thông tin để duyệt một nội dung PR: kịch bản, kết quả AI review, "
                "điểm, cảnh báo, gợi ý, cờ chính sách và lịch sử duyệt."
            ),
            handler=_context_handler,
            arguments_model=ReviewReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.review.approve",
            description=(
                "Duyệt một nội dung PR ở đúng bước nó đang đứng "
                "(Trưởng nhóm, Trưởng phòng hoặc duyệt nội bộ)."
            ),
            handler=_approve_handler,
            arguments_model=DecisionArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_REVIEW,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.review.request_revision",
            description="Yêu cầu sửa một nội dung PR, kèm lý do bắt buộc.",
            handler=_request_revision_handler,
            arguments_model=RevisionArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_REVIEW,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.review.reject",
            description="Từ chối một nội dung PR. Nội dung sẽ bị huỷ.",
            handler=_reject_handler,
            arguments_model=DecisionArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_REVIEW,
            # NOT ``destructive``: in this repository that flag means
            # "deletes data", and ``PolicyEngine`` refuses any destructive tool
            # outright (``deny_destructive=True``). Rejecting cancels content,
            # revoking closes a grant and cancelling a task is a status change -
            # none of them removes a row. ``RiskLevel.HIGH`` is what produces
            # the ``/confirm`` round trip, and that is the protection these need.
            read_only=False,
        ),
    ]


__all__ = ["STAGE_FOR_CAPABILITY", "build_pr_review_tools"]
