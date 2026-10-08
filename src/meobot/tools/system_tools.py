"""System tools: health and info. All read-only, all low risk."""

from __future__ import annotations

from meobot.application.health_service import HealthService
from meobot.core.logging import get_logger
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult

logger = get_logger(__name__)


def build_system_tools(health_service: HealthService) -> list[ToolDefinition]:
    """Build the system tool set bound to a concrete health service."""

    async def health_handler(context: ToolContext, arguments: NoArguments) -> ToolResult:
        report = await health_service.check()
        lines = ["🩺 Tình trạng hệ thống:", *report.as_lines()]
        return ToolResult(
            success=report.healthy,
            message="\n".join(lines),
            data=report.model_dump(mode="json"),
            entity_type="system",
        )

    async def info_handler(context: ToolContext, arguments: NoArguments) -> ToolResult:
        summary = context.settings.safe_summary()
        return ToolResult(
            success=True,
            message=(
                f"{summary['app_name']} — môi trường {summary['app_env']}, "
                f"LLM provider: {summary['llm_provider']}."
            ),
            data=dict(summary),
            entity_type="system",
        )

    return [
        ToolDefinition(
            name="system.health",
            description="Kiểm tra tình trạng PostgreSQL, Redis và Celery worker.",
            handler=health_handler,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SYSTEM_HEALTH,
            read_only=True,
        ),
        ToolDefinition(
            name="system.info",
            description="Xem thông tin cấu hình không nhạy cảm của TasksBot.",
            handler=info_handler,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SYSTEM_INFO,
            read_only=True,
        ),
    ]
