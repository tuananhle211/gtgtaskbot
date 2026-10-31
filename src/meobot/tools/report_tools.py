"""The report assistant's tools ("Trợ lý AI", 2026-10-31).

Read-only, low risk, one per report. Each is a translator onto
:class:`~meobot.application.assistant.reports.AssistantReportService`: the
handler validates a month / a name, the service reads the same numbers the web
screen shows, and the reply is the service's own sentence - so the figure a
person reads is the figure in ``ToolResult.data``, never a model's.

Gated by :data:`~meobot.domain.permissions.matrix.Permission.ASSISTANT_REPORT`:
the OWNER by role, anybody else by the personal grant ``ASSISTANT CHAT``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from meobot.application.assistant.reports import AssistantReportService, Report
from meobot.core.time import utcnow
from meobot.domain.assistant.reports import ReportTool, month_in
from meobot.domain.board.models import TASK_STATES
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult

MONTH_HELP = 'Tháng "YYYY-MM" (vd "2026-10"). Bỏ trống = tháng này.'


def _month(value: str | None) -> str | None:
    """``YYYY-MM`` from what a model or a person wrote ("2026-10", "10/2026",
    "tháng 10", "tháng trước"); blank = this month. Anything else is refused,
    never guessed."""
    if value is None or not value.strip():
        return None
    found = month_in(value, utcnow().date())
    if found is None:
        raise ValueError('tháng phải có dạng "YYYY-MM"')
    return found


class MonthArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str | None = Field(default=None, max_length=20, description=MONTH_HELP)

    @field_validator("month")
    @classmethod
    def _valid_month(cls, value: str | None) -> str | None:
        return _month(value)


class KpiArguments(MonthArguments):
    person: str | None = Field(
        default=None,
        max_length=120,
        description="Tên marketer cần xem (bỏ trống = cả phòng Ads).",
    )


class PerformanceArguments(MonthArguments):
    department: str | None = Field(
        default=None, max_length=120, description="Tên phòng ban (bỏ trống = toàn công ty)."
    )
    person: str | None = Field(
        default=None, max_length=120, description="Tên một người (bỏ trống = toàn công ty)."
    )


class OrderArguments(MonthArguments):
    state: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "Liệt kê order ở một trạng thái: "
            + ", ".join(f"{key} ({label})" for key, label in TASK_STATES.items())
            + ". Bỏ trống = chỉ đếm."
        ),
    )

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip().upper()
        if not cleaned:
            return None
        if cleaned not in TASK_STATES:
            raise ValueError("trạng thái không có trong danh sách")
        return cleaned


def _result(context: ToolContext, tool: ReportTool, report: Report) -> ToolResult:
    return ToolResult(
        success=True,
        message=report.message(context.settings.web_base_url),
        data={**report.data, "link": report.link},
        entity_type="report",
        entity_id=tool.value,
    )


ReportCall = Callable[[AssistantReportService, ToolContext, Any], Awaitable[Report]]


def _tool(
    name: ReportTool, description: str, arguments: type[BaseModel], call: ReportCall
) -> ToolDefinition:
    async def handler(context: ToolContext, args: Any) -> ToolResult:
        service = AssistantReportService(context.require_session(), context.settings)
        return _result(context, name, await call(service, context, args))

    handler.__name__ = name.value.replace(".", "_")
    return ToolDefinition(
        name=name.value,
        description=description,
        handler=handler,
        arguments_model=arguments,
        risk_level=RiskLevel.LOW,
        required_permission=Permission.ASSISTANT_REPORT,
        read_only=True,
    )


def build_report_tools() -> list[ToolDefinition]:
    return [
        _tool(
            ReportTool.COMPANY,
            "Báo cáo tổng hợp toàn công ty trong một câu trả lời: doanh thu Ads, order, "
            "điểm hiệu suất, PR, tài nguyên. Dùng khi hỏi tình hình chung / tổng hợp A-Z.",
            MonthArguments,
            lambda service, context, args: service.company(context.actor, args.month),
        ),
        _tool(
            ReportTool.ADS_REVENUE,
            "Báo cáo doanh thu Phòng Ads theo tháng: doanh thu tổng (thực thu CRM), DT hoa "
            "hồng, chi phí Ads, Ads / Doanh thu, SĐT, KPI team, doanh thu Marketing / Cơ sở "
            "HN-SG, hiệu suất theo cơ sở, chi tiêu từng nền tảng, marketer doanh thu cao nhất.",
            MonthArguments,
            lambda service, context, args: service.ads_revenue(args.month),
        ),
        _tool(
            ReportTool.ADS_KPI_BUDGET,
            "KPI doanh thu của từng marketer và ngân sách tuần Phòng Ads (chi tiêu so với "
            "ngân sách, vượt / đạt / chưa đến ngưỡng) theo tháng; có thể xem một marketer.",
            KpiArguments,
            lambda service, context, args: service.ads_kpi_budget(args.month, args.person),
        ),
        _tool(
            ReportTool.PERFORMANCE,
            "Điểm hiệu suất theo tháng: toàn công ty (theo phòng, người cao điểm nhất, định "
            "mức), một phòng ban, hoặc một người (điểm, định mức, trễ hạn, bị trả lại).",
            PerformanceArguments,
            lambda service, context, args: service.performance(
                args.month, args.department, args.person
            ),
        ),
        _tool(
            ReportTool.ORDERS,
            "Tình hình order / task ORD: số order theo trạng thái (chờ duyệt, chờ phân công, "
            "đang làm, đang sửa...), order trễ hạn, order đặt trong tháng; liệt kê order ở "
            "một trạng thái.",
            OrderArguments,
            lambda service, context, args: service.orders(context.actor, args.month, args.state),
        ),
        _tool(
            ReportTool.RESOURCES,
            "Tài nguyên (Via, Page, TKQC, BM...): tổng số, trạng thái, cảnh báo (người giữ "
            "nghỉ việc, quá hạn rà soát, die / mất) và đề xuất mua tài nguyên đang mở.",
            NoArguments,
            lambda service, context, args: service.resources(),
        ),
        _tool(
            ReportTool.PR,
            "Tình hình PR theo tháng: task PR đang mở theo pha, nội dung tạo / hoàn thành "
            "trong tháng, số bài đã đăng.",
            MonthArguments,
            lambda service, context, args: service.pr(context.actor, args.month),
        ),
    ]


__all__ = ["build_report_tools"]
