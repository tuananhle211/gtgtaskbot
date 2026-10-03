"""Script Type Registry tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.audit_service import AuditService
from meobot.application.script_type_service import ScriptTypeService
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult


class GetScriptTypeArgs(BaseModel):
    """Arguments for ``script_type.get``."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=64)


async def _list_handler(context: ToolContext, arguments: NoArguments) -> ToolResult:
    session = context.require_session()
    service = ScriptTypeService(session, AuditService(session))
    script_types = await service.list_script_types(active_only=True)
    if not script_types:
        return ToolResult(
            success=True,
            message="Chưa có thể loại kịch bản nào đang hoạt động.",
            data={"items": []},
        )
    lines = [f"• {item.code} — {item.name} (v{item.current_version})" for item in script_types]
    return ToolResult(
        success=True,
        message="📚 Thể loại kịch bản đang hoạt động:\n" + "\n".join(lines),
        data={
            "items": [
                {
                    "id": str(item.id),
                    "code": item.code,
                    "name": item.name,
                    "current_version": item.current_version,
                }
                for item in script_types
            ]
        },
        entity_type="script_type",
    )


async def _get_handler(context: ToolContext, arguments: GetScriptTypeArgs) -> ToolResult:
    session = context.require_session()
    service = ScriptTypeService(session, AuditService(session))
    script_type = await service.get_by_code(arguments.code)
    rubric = await service.get_rubric(script_type.id)
    criteria = "\n".join(
        f"  - {criterion.code}: {criterion.name} ({criterion.weight}%)"
        for criterion in rubric.criteria
    )
    return ToolResult(
        success=True,
        message=(
            f"📄 {script_type.name} ({script_type.code}) — version {script_type.current_version}\n"
            f"Tiêu chí chấm điểm:\n{criteria}"
        ),
        data={
            "code": script_type.code,
            "name": script_type.name,
            "current_version": script_type.current_version,
            "rubric": rubric.model_dump(mode="json"),
        },
        entity_type="script_type",
        entity_id=str(script_type.id),
    )


def build_script_type_tools() -> list[ToolDefinition]:
    """Read-only tools over the Script Type Registry.

    Writing a new script type or rubric version stays out of the LLM's reach in
    milestone 1: it is done through the API (see ``/api/v1/script-types``).
    """
    return [
        ToolDefinition(
            name="script_type.list",
            description="Liệt kê các thể loại kịch bản đang hoạt động.",
            handler=_list_handler,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_TYPE_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="script_type.get",
            description="Xem chi tiết một thể loại kịch bản và rubric chấm điểm.",
            handler=_get_handler,
            arguments_model=GetScriptTypeArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_TYPE_READ,
            read_only=True,
        ),
    ]
