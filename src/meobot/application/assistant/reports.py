"""The report assistant's numbers ("Trợ lý AI", 2026-10-31).

Every report here is read from the service the web screen it names reads -
the Ads team report and the weekly budgets, the points overview, the shared
task board and its dashboard, the resources report, the resource requests -
so a figure the bot gives and the figure on the screen cannot disagree. The
sentence is written here, from those numbers; no model ever writes a figure.

The person asking holds ``ASSISTANT CHAT`` (checked by the policy engine
before a tool runs, :data:`~meobot.domain.permissions.matrix.Permission.ASSISTANT_REPORT`),
and that permission means the **whole company**: each report reads as the
OWNER would - the Ads module with ``view_all``, the resources module with
everything, the board through :func:`company_viewer` (the system actor the
worker already uses: OWNER role, no row, so no "mine" / "awaiting me").

Nothing here writes. A missing figure says so ("chưa có dữ liệu"); nothing is
estimated, rounded into existence or carried over from another month.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.stats_service import Month, resolve_month
from meobot.application.ads.access import AdsAccess
from meobot.application.ads.budget_service import AdsBudgetService, BudgetLine
from meobot.application.ads.team_service import AdsTeamService, Figures, TeamReport
from meobot.application.audit_service import AuditService
from meobot.application.board.dashboard_service import DashboardService
from meobot.application.board.task_board_service import TaskBoardService
from meobot.application.performance.quota import QuotaService
from meobot.application.performance.report_service import PerformanceReportService
from meobot.application.pr_services import build_pr_services
from meobot.application.resources.access import EVERYTHING
from meobot.application.resources.reports import ResourceReportService
from meobot.application.resources.service import ResourceService
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.department import Department
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.resource import ResourceRequest
from meobot.db.models.user import User
from meobot.domain.ads.budget import BUDGET_STATUS_LABELS, BudgetStatus
from meobot.domain.board.models import PHASE_LABELS, TASK_STATES, BoardQuery, Phase, TaskRow
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.normalization import strip_accents as fold
from meobot.domain.pr.reporting import PrPublicationStatus
from meobot.domain.resources.models import ALERT_LABELS, StatusGroup
from meobot.domain.resources.requests import OPEN_STAGES, STAGE_LABELS, RequestStage

logger = get_logger(__name__)

NO_DATA = "chưa có dữ liệu"

#: The first line of every report reply: the assistant speaks as "em" to
#: "sếp" (2026-10-31).
REPORT_OPENER = "Dạ, em gửi sếp số liệu ạ:"

#: Every marketer's numbers, as the OWNER sees them on "Phòng Ads".
COMPANY_ADS = AdsAccess(user_id=None, view=True, view_all=True)

#: How many names a list in a reply shows before "và N người khác".
TOP = 5


def company_viewer(actor: Actor) -> Actor:
    """The board's reader for a company-wide report: the OWNER's view with no
    row of its own (the system actor the worker uses), named after the person
    asking for the logs. Read-only use only."""
    return Actor(user_id=None, role=Role.OWNER, full_name=actor.full_name)


# --- formatting --------------------------------------------------------------------


def money(value: float | None) -> str:
    if value is None:
        return NO_DATA
    return f"{round(value):,}".replace(",", ".") + " đ"


def count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def percent(value: float | None) -> str:
    if value is None:
        return "chưa tính được"
    return f"{value * 100:.1f}".replace(".", ",") + "%"


def month_label(month: Month) -> str:
    return f"{month.month:02d}/{month.year}"


@dataclass(frozen=True, slots=True)
class Report:
    """One answer: the lines a person reads, the exact numbers behind them,
    and the screen to check them on."""

    title: str
    lines: tuple[str, ...]
    data: dict[str, Any]
    #: A path of the web app (``/ads``), joined to ``WEB_BASE_URL`` when set.
    link: str | None = None
    #: Shown under the lines: where the numbers come from, what they leave out.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def message(self, base_url: str) -> str:
        """The reply, in the assistant's voice ("em" to "sếp"); the figures are
        the lines as written, nothing added between them."""
        parts = [REPORT_OPENER, f"**{self.title}**", *self.lines]
        parts.extend(f"_{note}_" for note in self.notes)
        if self.link:
            base = base_url.strip().rstrip("/")
            parts.append(f"Sếp đối chiếu trên web: {base}{self.link}" if base else self.link)
        return "\n".join(parts)


class AssistantReportService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def month(self, value: str | None) -> Month:
        return resolve_month(value, tz=self._settings.timezone)

    # --- Ads: revenue ------------------------------------------------------------

    async def _team(self, month: Month) -> TeamReport:
        return await AdsTeamService(self._session).report(
            COMPANY_ADS, month=date(month.year, month.month, 1)
        )

    async def ads_revenue(self, month_value: str | None = None) -> Report:
        month = self.month(month_value)
        team = await self._team(month)
        now, before = team.current, team.before
        lines = [
            f"- Doanh thu tổng (thực thu, xét KPI): {money(now.revenue)}"
            f" (tháng trước: {money(before.revenue)})",
            f"- DT hoa hồng: {money(now.revenue_net)}",
            f"- Chi phí Ads (Facebook): {money(now.spend)} · lập hoá đơn {money(now.invoice)}",
            f"- Ads / Doanh thu: {percent(now.ads_ratio)} (ngưỡng ≤ 40%)"
            f" · Ads / DT thực: {percent(now.ads_ratio_real)}",
            f"- SĐT: {count(now.phones)} · chi phí / SĐT {money(now.cost_per_phone)}",
        ]
        lines.append(_kpi_line(team))
        regions = team.regions
        lines.append(
            f"- Doanh thu Marketing: HN {money(regions.hn.marketing)}"
            f" · SG {money(regions.hcm.marketing)}"
        )
        lines.append(
            f"- Doanh thu Cơ sở: HN {money(regions.hn.site)} · SG {money(regions.hcm.site)}"
        )
        lines.append(
            "- Hiệu suất theo cơ sở (chi tiêu BM ÷ DT Marketing):"
            f" HN {percent(regions.hn.ads_ratio)}"
            f" · SG {percent(regions.hcm.ads_ratio)}"
        )
        for channel in team.channels:
            revenue = (
                f" · doanh thu {money(channel.figures.revenue)}"
                if channel.figures.revenue is not None
                else ""
            )
            lines.append(
                f"- {channel.label}: chi tiêu {money(channel.figures.spend)}{revenue}"
                f" · {count(channel.accounts)} tài khoản"
            )
        ranked = sorted(
            (line for line in team.marketers if line.current.revenue),
            key=lambda line: -(line.current.revenue or 0.0),
        )
        if ranked:
            lines.append("- Marketer doanh thu cao nhất:")
            lines.extend(
                f"  {index}. {line.name}: {money(line.current.revenue)}"
                + (f" · KPI {percent(line.kpi_rate)}" if line.kpi else "")
                for index, line in enumerate(ranked[:TOP], start=1)
            )
        return Report(
            title=f"Doanh thu Ads · tháng {month_label(month)}",
            lines=tuple(lines),
            data={
                "month": month.label,
                "period": [team.month.start.isoformat(), team.month.end.isoformat()],
                "current": _figures(now),
                "previous": _figures(before),
                "team_kpi": team.team_kpi,
                "kpi_revenue": team.kpi_revenue,
                "team_kpi_rate": team.team_kpi_rate,
                "month_progress": team.month_progress,
                "regions": {
                    "hn": _region(regions.hn),
                    "hcm": _region(regions.hcm),
                    "flows": dict(regions.flows),
                },
                "channels": [
                    {
                        "platform": channel.platform.value,
                        "spend": channel.figures.spend,
                        "revenue": channel.figures.revenue,
                        "accounts": channel.accounts,
                    }
                    for channel in team.channels
                ],
                "top_marketers": [
                    {
                        "user_id": str(line.user_id),
                        "name": line.name,
                        "revenue": line.current.revenue,
                        "kpi": line.kpi,
                        "kpi_rate": line.kpi_rate,
                    }
                    for line in ranked[:TOP]
                ],
            },
            link="/ads",
            notes=(
                "Doanh thu = thực thu từ CRM (gồm VAT), như ô Doanh thu tổng trên Dashboard Ads.",
            ),
        )

    # --- Ads: KPI and the weekly budgets -------------------------------------------

    async def ads_kpi_budget(
        self, month_value: str | None = None, person: str | None = None
    ) -> Report:
        month = self.month(month_value)
        team = await self._team(month)
        budget = await AdsBudgetService(self._session, AuditService(self._session)).month(
            COMPANY_ADS, date(month.year, month.month, 1)
        )
        marketers = list(team.marketers)
        budget_lines: list[BudgetLine] = list(budget.lines)
        title = f"KPI & ngân sách Ads · tháng {month_label(month)}"
        if person:
            wanted = fold(person)
            marketers = [line for line in marketers if wanted in fold(line.name)]
            budget_lines = [line for line in budget_lines if wanted in fold(line.name)]
            if not marketers and not budget_lines:
                return Report(
                    title=title,
                    lines=(f'- Không có marketer nào tên "{person}" trong Phòng Ads tháng này.',),
                    data={"month": month.label, "person": person, "found": False},
                    link="/ads/budget",
                )
        lines: list[str] = []
        if not person:
            lines.append(_kpi_line(team))
        with_kpi = sorted(
            (line for line in marketers if line.kpi),
            key=lambda line: -(line.kpi_rate or 0.0),
        )
        if with_kpi:
            lines.append("- KPI từng marketer (đạt / KPI):")
            shown = with_kpi if person else with_kpi[: TOP * 2]
            lines.extend(
                f"  • {line.name}: {money(line.kpi_revenue)} / {money(line.kpi)}"
                f" ({percent(line.kpi_rate)})"
                for line in shown
            )
            if len(with_kpi) > len(shown):
                lines.append(f"  … và {len(with_kpi) - len(shown)} người khác")
        elif marketers:
            lines.append("- Chưa marketer nào có KPI tháng này.")
        week_lines = [budget.total] if budget.total is not None and not person else []
        week_lines.extend(budget_lines if person else [])
        for line in week_lines:
            label = "Cả phòng" if line.user_id is None else line.name
            lines.append(f"- Ngân sách tuần · {label}:")
            for cell in line.cells:
                lines.append(
                    f"  {cell.week.label}: chi {money(cell.spend)} / ngân sách "
                    f"{money(cell.budget) if cell.budget is not None else 'chưa đặt'}"
                    f" ({BUDGET_STATUS_LABELS[cell.status]})"
                )
        over = [
            (line.name, cell.week.index)
            for line in budget_lines
            for cell in line.cells
            if cell.status is BudgetStatus.OVER
        ]
        if not person:
            if over:
                lines.append(
                    "- Vượt ngân sách: "
                    + ", ".join(f"{name} (tuần {week})" for name, week in over[: TOP * 2])
                    + (f" … và {len(over) - TOP * 2} ô khác" if len(over) > TOP * 2 else "")
                )
            else:
                lines.append("- Không ai vượt ngân sách tuần nào.")
        return Report(
            title=title + (f" · {person}" if person else ""),
            lines=tuple(lines),
            data={
                "month": month.label,
                "person": person,
                "team_kpi": None if person else team.team_kpi,
                "kpi_revenue": None if person else team.kpi_revenue,
                "team_kpi_rate": None if person else team.team_kpi_rate,
                "marketers": [
                    {
                        "user_id": str(line.user_id),
                        "name": line.name,
                        "kpi": line.kpi,
                        "kpi_revenue": line.kpi_revenue,
                        "kpi_rate": line.kpi_rate,
                    }
                    for line in marketers
                ],
                "budget": [
                    {
                        "user_id": None if line.user_id is None else str(line.user_id),
                        "name": line.name,
                        "weeks": [
                            {
                                "week": cell.week.index,
                                "budget": cell.budget,
                                "spend": cell.spend,
                                "status": cell.status.value,
                            }
                            for cell in line.cells
                        ],
                    }
                    for line in week_lines
                ],
                "over": [{"name": name, "week": week} for name, week in over],
            },
            link=f"/ads/budget?month={month.label}",
        )

    # --- points ---------------------------------------------------------------------

    async def performance(
        self,
        month_value: str | None = None,
        department: str | None = None,
        person: str | None = None,
    ) -> Report:
        service = PerformanceReportService(self._session, tz=self._settings.timezone)
        month = service.month(month_value)
        today = utcnow().astimezone(self._settings.timezone).date()
        quota = await QuotaService(self._session).month(month.year, month.month, today=today)
        title = f"Điểm hiệu suất · tháng {month_label(month)}"
        link = f"/performance/company?month={month.label}"
        if person:
            found = await self._people_named(person)
            if len(found) != 1:
                return self._not_one(title, "người", person, [user.full_name for user in found])
            user = found[0]
            report = await service.person(user.id, month)
            figures = report.figures
            lines = [
                f"- Tổng điểm: {count(figures.points)} / định mức {count(quota.monthly)}"
                f" (tới hôm nay {count(quota.to_date)})",
                f"- Bước đã xong: {count(figures.steps_done)}"
                f" · đang chờ duyệt / đang làm: {count(figures.pending_count)}"
                f" (dự kiến {count(figures.pending_points)} điểm, chưa tính)",
                f"- Trễ hạn: {count(figures.late_count)} / {count(figures.with_deadline)}"
                " bước có hạn",
                f"- Bị trả lại: lỗi người làm {count(figures.returns_fault)}"
                f" · đổi yêu cầu {count(figures.returns_other)}"
                f" · sai mã {count(figures.returns_codes)}",
                f"- Tháng trước: {count(figures.previous_points)} điểm",
            ]
            return Report(
                title=f"{title} · {report.name}",
                lines=tuple(lines),
                data={
                    "month": month.label,
                    "user_id": str(user.id),
                    "name": report.name,
                    "points": figures.points,
                    "steps_done": figures.steps_done,
                    "pending_points": figures.pending_points,
                    "pending_count": figures.pending_count,
                    "late_count": figures.late_count,
                    "with_deadline": figures.with_deadline,
                    "returns_fault": figures.returns_fault,
                    "returns_other": figures.returns_other,
                    "returns_codes": figures.returns_codes,
                    "previous_points": figures.previous_points,
                    "quota_monthly": quota.monthly,
                    "quota_to_date": quota.to_date,
                },
                link=f"/people/{user.id}",
                notes=("Điểm đã nhân hệ số; điểm đang chờ không cộng vào tổng.",),
            )
        if department:
            rows = await self._departments_named(department)
            if len(rows) != 1:
                return self._not_one(title, "phòng", department, [row.name for row in rows])
            chosen = rows[0]
            people = await service.department_people(chosen.id, month)
            team = sorted(await service.team(people, month), key=lambda row: -row.figures.points)
            total = sum(row.figures.points for row in team)
            lines = [
                f"- Tổng điểm phòng: {count(total)} · {count(len(team))} người"
                f" · định mức mỗi người {count(quota.monthly)}",
            ]
            lines.extend(
                f"  {index}. {row.name}: {count(row.figures.points)}"
                f" ({_share(row.figures.points, quota.monthly)} định mức)"
                for index, row in enumerate(team[: TOP * 2], start=1)
            )
            if len(team) > TOP * 2:
                lines.append(f"  … và {len(team) - TOP * 2} người khác")
            return Report(
                title=f"{title} · {chosen.name}",
                lines=tuple(lines),
                data={
                    "month": month.label,
                    "department_id": str(chosen.id),
                    "department": chosen.name,
                    "points": total,
                    "quota_monthly": quota.monthly,
                    "people": [
                        {
                            "user_id": str(row.user_id),
                            "name": row.name,
                            "points": row.figures.points,
                            "steps_done": row.figures.steps_done,
                            "late_count": row.figures.late_count,
                        }
                        for row in team
                    ],
                },
                link=f"/performance/team?month={month.label}&department_id={chosen.id}",
                notes=("Điểm đã nhân hệ số.",),
            )
        overview = await service.overview(month)
        lines = [
            f"- Toàn công ty: {count(overview.points)} điểm · {count(overview.steps_done)} bước"
            f" · {count(overview.people)} người có điểm",
            f"- Định mức mỗi người: {count(quota.monthly)} (tới hôm nay {count(quota.to_date)})",
        ]
        if overview.departments:
            lines.append("- Theo phòng:")
            lines.extend(
                f"  • {row.name}: {count(row.points)} điểm · {count(row.people)} người"
                f" · trễ {count(row.late_count)}/{count(row.with_deadline)}"
                for row in overview.departments
            )
        if overview.top:
            lines.append("- Cao điểm nhất:")
            lines.extend(
                f"  {index}. {row.name}: {count(row.figures.points)}"
                for index, row in enumerate(overview.top[:TOP], start=1)
            )
        if not overview.points:
            lines.append(f"- Tháng {month_label(month)} {NO_DATA} điểm nào.")
        return Report(
            title=title,
            lines=tuple(lines),
            data={
                "month": month.label,
                "points": overview.points,
                "steps_done": overview.steps_done,
                "people": overview.people,
                "quota_monthly": quota.monthly,
                "quota_to_date": quota.to_date,
                "departments": [
                    {
                        "name": row.name,
                        "points": row.points,
                        "people": row.people,
                        "steps_done": row.steps_done,
                        "late_count": row.late_count,
                        "with_deadline": row.with_deadline,
                    }
                    for row in overview.departments
                ],
                "top": [
                    {"user_id": str(row.user_id), "name": row.name, "points": row.figures.points}
                    for row in overview.top
                ],
            },
            link=link,
            notes=("Điểm đã nhân hệ số, như màn Hiệu suất - Toàn công ty.",),
        )

    # --- the order board (ORD) ------------------------------------------------------------

    def _board(self) -> TaskBoardService:
        return TaskBoardService(
            self._session,
            self._settings,
            directory=UnitDirectoryService(self._session),
            pr=build_pr_services(self._session, self._settings),
        )

    async def orders(
        self, actor: Actor, month_value: str | None = None, state: str | None = None
    ) -> Report:
        viewer = company_viewer(actor)
        board = self._board()
        month = self.month(month_value)
        first, last = _month_days(month)

        async def total(query: BoardQuery) -> int:
            return (await board.page(viewer, unit="ADS", query=query)).total

        states = {key: await total(BoardQuery(state=key, limit=1)) for key in TASK_STATES}
        overdue = await board.page(viewer, unit="ADS", query=BoardQuery(overdue=True, limit=TOP))
        summary = await DashboardService(self._session, self._settings, board).summary(
            viewer, unit="ADS", date_from=first, date_to=last
        )
        open_states = [key for key in TASK_STATES if key not in ("HOAN_THANH", "DA_HUY")]
        lines = [
            "- Đang mở: "
            + " · ".join(f"{TASK_STATES[key]} {count(states[key])}" for key in open_states),
            f"- Trễ hạn ngay lúc này: {count(overdue.total)}",
        ]
        lines.extend(_row_line(row, self._settings.timezone) for row in overdue.rows)
        lines.append(
            f"- Order đặt trong tháng {month_label(month)}: {count(summary.total)}"
            f" · hoàn thành {count(summary.completed)}"
            f" ({summary.progress_percent if summary.progress_percent is not None else 0}%)"
            f" · chờ duyệt {count(summary.pending_review)} · trễ hạn {count(summary.overdue)}"
        )
        link = "/tasks?unit=ADS"
        listed: list[TaskRow] = []
        if state and state in TASK_STATES:
            query = BoardQuery(state=state, limit=TOP * 2)
            page = await board.page(viewer, unit="ADS", query=query)
            listed = list(page.rows)
            lines.append(f"- {TASK_STATES[state]} ({count(page.total)}):")
            lines.extend(_row_line(row, self._settings.timezone) for row in listed)
            link = f"/tasks?unit=ADS&state={state}"
        return Report(
            title="Tình hình order (ORD)",
            lines=tuple(lines),
            data={
                "states": states,
                "overdue": overdue.total,
                "overdue_rows": [_row_data(row) for row in overdue.rows],
                "month": month.label,
                "month_total": summary.total,
                "month_completed": summary.completed,
                "month_pending_review": summary.pending_review,
                "month_overdue": summary.overdue,
                "state": state,
                "state_rows": [_row_data(row) for row in listed],
            },
            link=link,
            notes=("Bảng Order tổng (không gồm order sinh nhật).",),
        )

    # --- PR ---------------------------------------------------------------------------

    async def pr(self, actor: Actor, month_value: str | None = None) -> Report:
        viewer = company_viewer(actor)
        board = self._board()
        month = self.month(month_value)
        first, last = _month_days(month)
        phases = await board.count_by_phase(viewer, unit="PR", query=BoardQuery())
        summary = await DashboardService(self._session, self._settings, board).summary(
            viewer, unit="PR", date_from=first, date_to=last
        )
        published = int(
            await self._session.scalar(
                select(func.count())
                .select_from(PrPublication)
                .where(
                    PrPublication.status == PrPublicationStatus.PUBLISHED,
                    PrPublication.published_at >= month.start,
                    PrPublication.published_at < month.end,
                )
            )
            or 0
        )
        shown = [Phase.ORDER, Phase.REVIEW, Phase.PRODUCTION, Phase.FINAL_REVIEW]
        lines = [
            "- Đang mở theo pha: "
            + " · ".join(f"{_phase_name(phase)} {count(phases.get(phase, 0))}" for phase in shown),
            f"- Nội dung tạo trong tháng {month_label(month)}: {count(summary.total)}"
            f" · hoàn thành {count(summary.completed)} · chờ duyệt {count(summary.pending_review)}",
            f"- Bài đã đăng trong tháng (còn hiệu lực): {count(published)}",
        ]
        return Report(
            title="Tình hình PR",
            lines=tuple(lines),
            data={
                "phases": {phase.value: phases.get(phase, 0) for phase in Phase},
                "month": month.label,
                "month_total": summary.total,
                "month_completed": summary.completed,
                "month_pending_review": summary.pending_review,
                "published": published,
            },
            link="/tasks?unit=PR",
        )

    # --- resources ------------------------------------------------------------------------

    async def resources(self) -> Report:
        service = ResourceService(self._session, AuditService(self._session))
        reports = ResourceReportService(service)
        month = self.month(None)
        summary = await reports.summary(EVERYTHING, month.label)
        warnings = await reports.warnings(EVERYTHING)
        requests: dict[RequestStage, int] = {
            RequestStage(stage): int(number)
            for stage, number in (
                await self._session.execute(
                    select(ResourceRequest.stage, func.count())
                    .where(ResourceRequest.stage.in_(list(OPEN_STAGES)))
                    .group_by(ResourceRequest.stage)
                )
            ).all()
        }
        flagged = [row for row in warnings.rows if row.flagged]
        lines = [
            f"- Tổng tài nguyên: {count(summary.total)} · đã bàn giao {count(summary.handed_over)}",
            f"- Theo nhóm trạng thái: bình thường {count(summary.by_group[StatusGroup.NORMAL])}"
            f" · cần chú ý {count(summary.by_group[StatusGroup.ATTENTION])}"
            f" · mất {count(summary.by_group[StatusGroup.LOST])}"
            f" · đã đóng {count(summary.by_group[StatusGroup.CLOSED])}",
            f"- Cảnh báo: {count(len(warnings.rows))} tài nguyên ({count(len(flagged))} mức đỏ)",
        ]
        lines.extend(
            f"  • {ALERT_LABELS[kind]}: {count(number)}"
            for kind, number in warnings.counts.items()
            if number
        )
        for row in flagged[:TOP]:
            holder = row.holder.full_name if row.holder is not None else "Phòng Tài nguyên"
            labels = ", ".join(alert.label for alert in row.alerts)
            lines.append(f"  - {row.resource.code} ({row.type.name}, {holder}): {labels}")
        open_total = sum(requests.values())
        lines.append(
            f"- Đề xuất mua đang mở: {count(open_total)}"
            + (
                " ("
                + " · ".join(
                    f"{STAGE_LABELS[stage]} {count(requests[stage])}"
                    for stage in RequestStage
                    if stage in requests
                )
                + ")"
                if open_total
                else ""
            )
        )
        return Report(
            title="Tài nguyên",
            lines=tuple(lines),
            data={
                "total": summary.total,
                "handed_over": summary.handed_over,
                "by_group": {group.value: number for group, number in summary.by_group.items()},
                "warnings": len(warnings.rows),
                "flagged": len(flagged),
                "alerts": {kind.value: number for kind, number in warnings.counts.items()},
                "flagged_rows": [
                    {
                        "code": row.resource.code,
                        "type": row.type.name,
                        "holder": row.holder.full_name if row.holder is not None else None,
                        "alerts": [alert.kind.value for alert in row.alerts],
                    }
                    for row in flagged[:TOP]
                ],
                "open_requests": {stage.value: number for stage, number in requests.items()},
            },
            link="/resources/reports",
        )

    # --- the whole company, in one answer -----------------------------------------------------

    async def company(self, actor: Actor, month_value: str | None = None) -> Report:
        """The headline of every report. Each part is read on its own: one
        that fails says so, it never takes the others down nor gets a number."""
        month = self.month(month_value)
        lines: list[str] = []
        data: dict[str, Any] = {"month": month.label}
        parts: Sequence[tuple[str, Callable[[], Awaitable[Report]]]] = (
            ("ads", lambda: self.ads_revenue(month.label)),
            ("orders", lambda: self.orders(actor, month.label)),
            ("performance", lambda: self.performance(month.label)),
            ("pr", lambda: self.pr(actor, month.label)),
            ("resources", self.resources),
        )
        for key, build in parts:
            try:
                async with self._session.begin_nested():
                    report = await build()
            except Exception as error:
                logger.warning(
                    "assistant_report_part_failed",
                    extra={"part": key, "error": type(error).__name__},
                )
                lines.append(f"- {key}: không lấy được số liệu lúc này.")
                data[key] = None
                continue
            lines.append(f"• {report.title}")
            lines.extend(_headline(key, report))
            data[key] = report.data
        return Report(
            title=f"Tổng hợp công ty · tháng {month_label(month)}",
            lines=tuple(lines),
            data=data,
            link="/dashboard",
            notes=("Hỏi riêng từng mảng để xem chi tiết.",),
        )

    # --- look-ups -----------------------------------------------------------------------------

    async def _people_named(self, name: str) -> list[User]:
        wanted = fold(name).strip()
        rows = (await self._session.scalars(select(User).where(User.deleted_at.is_(None)))).all()
        exact = [user for user in rows if fold(user.full_name).strip() == wanted]
        return exact or [user for user in rows if wanted and wanted in fold(user.full_name)]

    async def _departments_named(self, name: str) -> list[Department]:
        wanted = fold(name).strip()
        rows = (
            await self._session.scalars(select(Department).where(Department.active.is_(True)))
        ).all()
        exact = [row for row in rows if wanted in (fold(row.name).strip(), fold(row.code).strip())]
        return exact or [row for row in rows if wanted and wanted in fold(row.name)]

    @staticmethod
    def _not_one(title: str, kind: str, asked: str, names: list[str]) -> Report:
        if not names:
            line = f'- Không tìm thấy {kind} nào tên "{asked}".'
        else:
            line = (
                f'- Có {len(names)} {kind} khớp "{asked}": '
                + ", ".join(names[:10])
                + ". Sếp nói rõ tên đầy đủ giúp em ạ."
            )
        return Report(
            title=title,
            lines=(line,),
            data={"asked": asked, "matches": names[:10], "found": False},
        )


# --- helpers ------------------------------------------------------------------------------


def _figures(figures: Figures) -> dict[str, Any]:
    return {
        "revenue": figures.revenue,
        "revenue_net": figures.revenue_net,
        "spend": figures.spend,
        "invoice": figures.invoice,
        "phones": figures.phones,
        "ads_ratio": figures.ads_ratio,
        "ads_ratio_real": figures.ads_ratio_real,
        "cost_per_phone": figures.cost_per_phone,
    }


def _region(region: Any) -> dict[str, Any]:
    return {
        "marketing": region.marketing,
        "site": region.site,
        "spend": region.spend,
        "ads_ratio": region.ads_ratio,
    }


def _kpi_line(team: TeamReport) -> str:
    if not team.team_kpi:
        return "- KPI team: chưa nhập KPI tháng này"
    return (
        f"- KPI team: {money(team.team_kpi)} · đã đạt {money(team.kpi_revenue)}"
        f" ({percent(team.team_kpi_rate)}) · tháng đã qua {percent(team.month_progress)}"
    )


def _share(points: int, quota: int) -> str:
    return percent(points / quota) if quota else "chưa có"


def _month_days(month: Month) -> tuple[date, date]:
    first = date(month.year, month.month, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


def _phase_name(phase: Phase) -> str:
    if phase is Phase.FINAL_REVIEW:
        return "Duyệt cuối"
    return PHASE_LABELS[phase]


def _when(at: datetime | None, tz: ZoneInfo) -> str:
    return "không có hạn" if at is None else at.astimezone(tz).strftime("%d/%m/%Y %H:%M")


def _row_line(row: TaskRow, tz: ZoneInfo) -> str:
    holder = row.current_person_name or "chưa có người"
    return (
        f"  • {row.code} · {row.title[:60]} · {row.status_label} · {holder}"
        f" · hạn {_when(row.deadline_at, tz)}"
    )


def _row_data(row: TaskRow) -> dict[str, Any]:
    return {
        "code": row.code,
        "title": row.title,
        "status": row.status_label,
        "state": row.state,
        "person": row.current_person_name,
        "deadline_at": None if row.deadline_at is None else row.deadline_at.isoformat(),
    }


def _headline(key: str, report: Report) -> list[str]:
    """The first lines of a report, for the company summary."""
    keep = {"ads": 4, "orders": 2, "performance": 1, "pr": 3, "resources": 3}[key]
    return [f"  {line.lstrip('- ').strip()}" for line in report.lines[:keep]]


__all__ = [
    "COMPANY_ADS",
    "NO_DATA",
    "AssistantReportService",
    "Report",
    "company_viewer",
    "count",
    "money",
    "percent",
]
