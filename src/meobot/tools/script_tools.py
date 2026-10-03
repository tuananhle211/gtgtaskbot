"""Script workflow tools.

``script.approve_for_production`` and ``script.request_revision`` are the first
*high-risk* tools in MeoBot: the policy engine demands an explicit confirmation
before either can run, and both re-check the actor's permission and the exact
version inside the application service.

Notice what is still missing, on purpose: nothing here publishes, schedules or
promotes a script into a video. An approved script is a script that may be
filmed.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.audit_service import AuditService
from meobot.application.script_presenter import (
    format_pending_list,
    format_script_detail,
    short_id,
)
from meobot.application.script_review_service import ScriptReviewService
from meobot.application.script_service import ScriptService
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.integrations.llm.base import LLMProvider
from meobot.tools.base import ToolContext, ToolDefinition, ToolResult

#: Pending listings stay short enough to read on a phone.
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25


class ListPendingArgs(BaseModel):
    """Arguments for ``script.list_pending``."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0, le=1000)


class ScriptReferenceArgs(BaseModel):
    """Arguments for tools that address one script."""

    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1, max_length=100, description="Mã ngắn, mã sheet hoặc UUID.")


class ApproveArgs(BaseModel):
    """Arguments for ``script.approve_for_production``."""

    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=1000)


class RevisionArgs(BaseModel):
    """Arguments for ``script.request_revision``."""

    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)


async def _list_pending_handler(context: ToolContext, arguments: ListPendingArgs) -> ToolResult:
    session = context.require_session()
    service = ScriptService(session, AuditService(session))
    scripts = list(await service.list_pending(limit=arguments.limit, offset=arguments.offset))
    total = await service.count_pending()
    return ToolResult(
        success=True,
        message=format_pending_list(scripts, total=total, offset=arguments.offset),
        data={
            "total": total,
            "items": [
                {
                    "id": str(script.id),
                    "short_id": short_id(script.id),
                    "external_script_id": script.external_script_id,
                    "status": script.status.value,
                    "title": script.current_version.title if script.current_version else None,
                }
                for script in scripts
            ],
        },
        entity_type="script",
    )


async def _get_handler(context: ToolContext, arguments: ScriptReferenceArgs) -> ToolResult:
    session = context.require_session()
    service = ScriptService(session, AuditService(session))
    script = await service.resolve(arguments.script_id)
    detail = await service.detail(script.id)
    return ToolResult(
        success=True,
        message=format_script_detail(detail, timezone=ZoneInfo(context.settings.app_timezone)),
        data={
            "id": str(script.id),
            "status": script.status.value,
            "version_number": detail.version_number,
            "has_review": detail.review is not None,
            "review_matches_version": detail.review_matches_version,
        },
        entity_type="script",
        entity_id=str(script.id),
    )


async def _review_result_handler(
    context: ToolContext, arguments: ScriptReferenceArgs
) -> ToolResult:
    session = context.require_session()
    service = ScriptService(session, AuditService(session))
    script = await service.resolve(arguments.script_id)
    review = await ScriptReviewService(session, AuditService(session)).latest_review(script.id)
    if review is None:
        return ToolResult(
            success=True,
            message="Kịch bản này chưa có review nào.",
            data={"id": str(script.id)},
            entity_type="script",
            entity_id=str(script.id),
        )
    return ToolResult(
        success=True,
        message=(
            f"🤖 Review gần nhất: {review.overall_score}/100 ({review.verdict.value})\n"
            f"{review.summary}"
        ),
        data={
            "review_id": str(review.id),
            "overall_score": review.overall_score,
            "verdict": review.verdict.value,
            "script_version_id": str(review.script_version_id),
        },
        entity_type="script",
        entity_id=str(script.id),
    )


def build_script_tools(*, llm: LLMProvider) -> list[ToolDefinition]:
    """Tools over the script workflow.

    Args:
        llm: Provider used by ``script.request_review`` when the review is run
            inline (the Telegram path enqueues a Celery task instead).
    """

    async def request_review_handler(
        context: ToolContext, arguments: ScriptReferenceArgs
    ) -> ToolResult:
        session = context.require_session()
        audit = AuditService(session)
        scripts = ScriptService(session, audit)
        script = await scripts.resolve(arguments.script_id)
        await scripts.queue_for_review(script_id=script.id)
        review = await ScriptReviewService(session, audit, llm).review_script(
            actor=context.actor,
            request_id=context.request_id,
            script_id=script.id,
        )
        return ToolResult(
            success=True,
            message=(
                f"🤖 Đã review `{short_id(script.id)}`: "
                f"{review.overall_score}/100 ({review.verdict.value})\n{review.summary}"
            ),
            data={
                "script_id": str(script.id),
                "review_id": str(review.id),
                "overall_score": review.overall_score,
                "verdict": review.verdict.value,
            },
            entity_type="script",
            entity_id=str(script.id),
        )

    async def approve_handler(context: ToolContext, arguments: ApproveArgs) -> ToolResult:
        session = context.require_session()
        service = ScriptService(session, AuditService(session))
        script = await service.resolve(arguments.script_id)
        approval = await service.approve_for_production(
            actor=context.actor,
            request_id=context.request_id,
            script_id=script.id,
            comment=arguments.comment,
        )
        return ToolResult(
            success=True,
            message=(
                f"✅ Đã duyệt SẢN XUẤT kịch bản `{short_id(script.id)}`.\n"
                "Lưu ý: duyệt sản xuất KHÔNG đồng nghĩa với duyệt đăng bài."
            ),
            data={
                "script_id": str(script.id),
                "approval_id": str(approval.id),
                "script_version_id": str(approval.script_version_id),
                "status": approval.status_after.value,
            },
            entity_type="script",
            entity_id=str(script.id),
        )

    async def revision_handler(context: ToolContext, arguments: RevisionArgs) -> ToolResult:
        session = context.require_session()
        service = ScriptService(session, AuditService(session))
        script = await service.resolve(arguments.script_id)
        record = await service.request_revision(
            actor=context.actor,
            request_id=context.request_id,
            script_id=script.id,
            comment=arguments.comment,
        )
        return ToolResult(
            success=True,
            message=f"✏️ Đã yêu cầu sửa kịch bản `{short_id(script.id)}`.",
            data={
                "script_id": str(script.id),
                "record_id": str(record.id),
                "status": record.status_after.value,
            },
            entity_type="script",
            entity_id=str(script.id),
        )

    return [
        ToolDefinition(
            name="script.list_pending",
            description="Liệt kê các kịch bản đang chờ review hoặc chờ duyệt.",
            handler=_list_pending_handler,
            arguments_model=ListPendingArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="script.get",
            description="Xem chi tiết một kịch bản: nội dung, trạng thái, review gần nhất.",
            handler=_get_handler,
            arguments_model=ScriptReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="script.review_result",
            description="Xem kết quả AI review gần nhất của một kịch bản.",
            handler=_review_result_handler,
            arguments_model=ScriptReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="script.request_review",
            description="Chấm điểm một kịch bản bằng AI theo rubric của thể loại.",
            handler=request_review_handler,
            arguments_model=ScriptReferenceArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SCRIPT_REVIEW,
            read_only=False,
        ),
        ToolDefinition(
            name="script.approve_for_production",
            description=(
                "Duyệt kịch bản cho phép SẢN XUẤT (quay dựng). "
                "Không cấp quyền đăng bài lên mạng xã hội."
            ),
            handler=approve_handler,
            arguments_model=ApproveArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_APPROVE,
            read_only=False,
        ),
        ToolDefinition(
            name="script.request_revision",
            description="Yêu cầu tác giả sửa lại kịch bản, kèm ghi chú.",
            handler=revision_handler,
            arguments_model=RevisionArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_APPROVE,
            read_only=False,
        ),
    ]


__all__ = ["build_script_tools"]
