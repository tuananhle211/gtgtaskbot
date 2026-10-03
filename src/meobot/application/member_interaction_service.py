"""What MeoBot answers a Member with, assembled away from aiogram.

Handlers in this codebase parse an update, resolve an actor, call a service,
render, and send. This is the service. It knows nothing about aiogram: it
returns a :class:`MemberReply` - text plus an optional list of button
descriptions - and the transport turns that into a Telegram message.

That separation is what makes the interaction testable without Telegram, and
what stops Vietnamese copy leaking back into handlers: everything here reads
from :mod:`~meobot.domain.member.copy`.

**Scope is enforced here, not in a prompt.** Every method that returns data
takes the acting :class:`~meobot.domain.identity.models.Actor` and queries by
their own id. There is no method on this class that can return another person's
work, allowance or absence history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.hr_request_service import HrRequestService
from meobot.application.hr_statistics_service import HrStatisticsService, month_bounds
from meobot.application.quota_service import QuotaService
from meobot.application.work_schedule_service import WorkScheduleService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.domain.access.quota import QuotaOutcome
from meobot.domain.hr.models import format_days, status_label, type_label
from meobot.domain.hr.schedule import describe_period, format_local_date
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member import copy
from meobot.domain.member.intents import MemberIntent
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """One button, described without reference to aiogram.

    ``action`` and ``argument`` are what the transport signs into a callback;
    nothing here knows how that signing works.
    """

    label: str
    action: str
    argument: str = ""


@dataclass(frozen=True, slots=True)
class MemberReply:
    """Everything the transport needs to answer one Member turn."""

    text: str
    buttons: list[list[ButtonSpec]] = field(default_factory=list)
    #: Set when this reply showed a numbered list, so "số 2" can be bound to it.
    list_kind: str | None = None
    list_item_ids: list[str] = field(default_factory=list)


class MemberInteractionService:
    """Builds Member-facing replies from authoritative data.

    Args:
        session: Read/write unit of work. The caller owns the transaction.
        settings: Feature switches and the display timezone.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._quota = QuotaService(session, settings)
        self._hr = HrRequestService(session, AuditService(session))
        self._hr_stats = HrStatisticsService(session)
        self._schedules = WorkScheduleService(session)

    # --- Home and help ----------------------------------------------------
    async def home(self, actor: Actor) -> MemberReply:
        """The personalised card a Member lands on.

        Only shows what this person can actually do. Nothing belonging to
        Trưởng phòng, Quản trị viên or Trưởng nhóm appears on a Member's home -
        a button they would only be refused is worse than no button.
        """
        lines: list[str] = []

        open_requests = (
            await self._hr.for_requester(user_id=actor.user_id, open_only=True, limit=10)
            if actor.user_id
            else []
        )
        if open_requests:
            lines.append(f"{len(open_requests)} yêu cầu đang chờ duyệt")

        if self._settings.member_show_ai_allowance_on_home and actor.user_id is not None:
            verdict = await self._quota.inspect_member(user_id=actor.user_id, role=actor.role)
            if verdict.outcome is not QuotaOutcome.UNLIMITED:
                lines.append(
                    f"{max(0, verdict.remaining)}/{verdict.limit} lượt trò chuyện AI còn lại"
                )

        rows: list[list[ButtonSpec]] = [
            [
                ButtonSpec(copy.Button.MY_WORK.value, "work.list"),
                ButtonSpec(copy.Button.VIEW_PROGRESS.value, "work.progress"),
            ],
            [
                ButtonSpec(copy.Button.ASK_LEAVE.value, "hr.leave"),
                ButtonSpec(copy.Button.ASK_LATE.value, "hr.late"),
            ],
            [
                ButtonSpec(copy.Button.MY_REQUESTS.value, "hr.mine"),
                ButtonSpec(copy.Button.MY_HR_STATS.value, "hr.stats"),
            ],
            [
                ButtonSpec(copy.Button.MY_AI_ALLOWANCE.value, "quota.mine"),
                ButtonSpec(copy.Button.ASK_MEOBOT.value, "chat.open"),
            ],
        ]
        if self._is_manager(actor):
            # The owner sees their own home plus the one thing they need most.
            rows.append([ButtonSpec(copy.Button.PENDING_REQUESTS.value, "hr.pending")])

        return MemberReply(
            text=copy.home_card(name=self._first_name(actor), lines=lines), buttons=rows
        )

    def help(self, actor: Actor) -> MemberReply:
        """Member-scoped help. Teaches natural language, not commands."""
        return MemberReply(text=copy.HELP_MEMBER, buttons=self._home_shortcut())

    # --- AI allowance -----------------------------------------------------
    async def ai_allowance(self, actor: Actor) -> MemberReply:
        """How many conversations are left today, in Vietnamese."""
        if actor.user_id is None:
            return MemberReply(text=copy.AI_ALLOWANCE_UNLIMITED, buttons=self._home_shortcut())
        verdict = await self._quota.inspect_member(user_id=actor.user_id, role=actor.role)
        if verdict.outcome is QuotaOutcome.UNLIMITED:
            return MemberReply(text=copy.AI_ALLOWANCE_UNLIMITED, buttons=self._home_shortcut())
        remaining = max(0, verdict.remaining)
        text = copy.AI_ALLOWANCE.format(remaining=remaining, limit=verdict.limit)
        return MemberReply(text=text, buttons=self._home_shortcut())

    async def low_allowance_warning(self, actor: Actor) -> str | None:
        """The volunteered warning when somebody is nearly out, or ``None``."""
        if actor.user_id is None:
            return None
        verdict = await self._quota.inspect_member(user_id=actor.user_id, role=actor.role)
        if verdict.outcome is QuotaOutcome.UNLIMITED:
            return None
        remaining = max(0, verdict.remaining)
        if 0 < remaining <= copy.LOW_ALLOWANCE_THRESHOLD:
            return copy.AI_ALLOWANCE_LOW.format(remaining=remaining)
        return None

    # --- HR: the Member's own view ---------------------------------------
    async def my_hr_requests(self, actor: Actor) -> MemberReply:
        """This person's own requests, numbered so "số 2" can refer to one."""
        if actor.user_id is None:
            return MemberReply(text=copy.Problem.NO_PERMISSION.value)
        rows = await self._hr.for_requester(user_id=actor.user_id, limit=10)
        if not rows:
            return MemberReply(
                text="Bạn chưa gửi yêu cầu nghỉ phép hoặc đi muộn nào.",
                buttons=[
                    [
                        ButtonSpec(copy.Button.ASK_LEAVE.value, "hr.leave"),
                        ButtonSpec(copy.Button.ASK_LATE.value, "hr.late"),
                    ]
                ],
            )

        lines = ["📋 YÊU CẦU CỦA BẠN", ""]
        buttons: list[list[ButtonSpec]] = []
        for index, row in enumerate(rows, start=1):
            period = describe_period(row.request_type, day=row.work_date, end_day=row.end_date)
            lines.append(f"{index}. {type_label(row.request_type)} — {period}")
            lines.append(f"   Trạng thái: {status_label(row.status)}")
            if row.status.is_open:
                buttons.append(
                    [
                        ButtonSpec(
                            f"{index}. {copy.Button.WITHDRAW_REQUEST.value}",
                            "hr.withdraw",
                            str(row.id),
                        )
                    ]
                )
        buttons = buttons[: self._settings.member_max_button_items]
        return MemberReply(
            text="\n".join(lines),
            buttons=buttons or self._home_shortcut(),
            list_kind="hr_request",
            list_item_ids=[str(row.id) for row in rows],
        )

    async def my_hr_statistics(self, actor: Actor, *, today: date | None = None) -> MemberReply:
        """This month's own totals. Computed from rows, never generated."""
        if actor.user_id is None:
            return MemberReply(text=copy.Problem.NO_PERMISSION.value)
        schedule = await self._schedules.for_display()
        day = today or utcnow().astimezone(schedule.zone).date()
        start, end = month_bounds(day)
        summary = await self._hr_stats.personal(user_id=actor.user_id, start=start, end=end)

        lines = [
            f"📊 THỐNG KÊ CỦA BẠN — THÁNG {day.month}/{day.year}",
            "",
            "Nghỉ phép:",
            f"• Đã duyệt: {summary.leave.approved_count} yêu cầu",
            f"• Tổng thời gian: {format_days(summary.leave.approved_days)} ngày",
            f"• Đang chờ duyệt: {summary.leave.pending_count}",
            f"• Bị từ chối: {summary.leave.rejected_count}",
            "",
            "Đi muộn:",
            f"• Đã duyệt: {summary.late.approved_count} lần",
            f"• Tổng thời gian: {summary.late.total_minutes} phút",
        ]
        if summary.late.approved_count:
            average = f"{summary.late.average_minutes}".replace(".", ",")
            lines.append(f"• Trung bình: {average} phút/lần")
        if summary.latest is not None:
            period = describe_period(
                summary.latest.request_type,
                day=summary.latest.work_date,
                end_day=summary.latest.end_date,
            )
            lines.extend(
                [
                    "",
                    "Yêu cầu gần nhất:",
                    f"• {type_label(summary.latest.request_type)} — {period} — "
                    f"{status_label(summary.latest.status)}",
                ]
            )
        return MemberReply(text="\n".join(lines), buttons=self._home_shortcut())

    # --- HR: the manager's view -------------------------------------------
    async def pending_hr_requests(self, actor: Actor) -> MemberReply:
        """Requests awaiting a decision. Refuses anybody without the permission."""
        if not has_permission(actor.role, Permission.HR_REQUEST_APPROVE):
            return MemberReply(text=copy.Problem.NO_PERMISSION.value)
        rows = await self._hr.pending_for_approver(actor=actor, limit=10)
        if not rows:
            return MemberReply(text="Hiện không có yêu cầu nào đang chờ duyệt.")

        lines = ["📥 ĐƠN CHỜ DUYỆT", ""]
        buttons: list[list[ButtonSpec]] = []
        for index, row in enumerate(rows, start=1):
            name = await self._requester_name(row.requester_user_id)
            period = describe_period(row.request_type, day=row.work_date, end_day=row.end_date)
            lines.append(f"{index}. {name} — {type_label(row.request_type)} — {period}")
            buttons.append(
                [
                    ButtonSpec(f"{index}. {copy.Button.APPROVE.value}", "hr.approve", str(row.id)),
                    ButtonSpec(copy.Button.REFUSE.value, "hr.reject", str(row.id)),
                ]
            )
        return MemberReply(
            text="\n".join(lines),
            buttons=buttons[: self._settings.member_max_button_items],
            list_kind="hr_pending",
            list_item_ids=[str(row.id) for row in rows],
        )

    async def absence_today(self, actor: Actor, *, today: date | None = None) -> MemberReply:
        """Who is away today. Management-only, and enforced in the service."""
        if not has_permission(actor.role, Permission.HR_DEPARTMENT_REPORT):
            return MemberReply(text=copy.Problem.NO_PERMISSION.value)
        schedule = await self._schedules.for_display()
        day = today or utcnow().astimezone(schedule.zone).date()
        summary = await self._hr_stats.absence_on(actor=actor, day=day)

        lines = [
            "👥 TÌNH HÌNH NHÂN SỰ HÔM NAY",
            "",
            f"• Nghỉ cả ngày: {len(summary.full_day)} người",
            f"• Nghỉ buổi sáng: {len(summary.morning)} người",
            f"• Nghỉ buổi chiều: {len(summary.afternoon)} người",
            f"• Đi muộn được duyệt: {len(summary.late)} người",
            f"• Đơn đang chờ xử lý: {summary.pending_count}",
        ]
        away = summary.full_day + summary.morning + summary.afternoon
        if away:
            lines.extend(["", "Nghỉ hôm nay:"])
            lines.extend(f"• {name} — {type_label(row.request_type).lower()}" for name, row in away)
        if summary.late:
            lines.extend(["", "Đi muộn:"])
            for name, row in summary.late:
                when = (
                    row.expected_arrival_at.astimezone(schedule.zone).strftime("%H:%M")
                    if row.expected_arrival_at
                    else "chưa rõ"
                )
                lines.append(f"• {name} — dự kiến có mặt lúc {when}")
        return MemberReply(text="\n".join(lines), buttons=self._manager_shortcut())

    async def department_report(
        self, actor: Actor, *, period: str = "month", today: date | None = None
    ) -> MemberReply:
        """Monthly or weekly department totals. Every figure is computed."""
        if not has_permission(actor.role, Permission.HR_DEPARTMENT_REPORT):
            return MemberReply(text=copy.Problem.NO_PERMISSION.value)
        from meobot.application.hr_statistics_service import week_bounds

        schedule = await self._schedules.for_display()
        day = today or utcnow().astimezone(schedule.zone).date()
        start, end = week_bounds(day) if period == "week" else month_bounds(day)
        summary = await self._hr_stats.department(actor=actor, start=start, end=end)

        heading = (
            f"📊 BÁO CÁO NGHỈ PHÉP VÀ ĐI MUỘN — TUẦN {format_local_date(start)}"
            if period == "week"
            else f"📊 BÁO CÁO NGHỈ PHÉP VÀ ĐI MUỘN — THÁNG {day.month}/{day.year}"
        )
        lines = [
            heading,
            "",
            "Nghỉ phép:",
            f"• {summary.leave.approved_count} yêu cầu đã duyệt",
            f"• Tổng cộng: {format_days(summary.leave.approved_days)} ngày công",
            f"• {summary.leave.rejected_count} yêu cầu bị từ chối",
            f"• {summary.leave.pending_count} yêu cầu đang chờ",
            "",
            "Đi muộn:",
            f"• {summary.late.approved_count} lượt được ghi nhận",
            f"• Tổng thời gian: {summary.late.total_minutes} phút",
        ]
        if summary.late.approved_count:
            average = f"{summary.late.average_minutes}".replace(".", ",")
            lines.append(f"• Trung bình: {average} phút/lượt")

        attention: list[str] = []
        if summary.pending_over_a_day:
            attention.append(f"• {summary.pending_over_a_day} đơn đang chờ quá 24 giờ")
        for heavy_day, count in summary.days_over_threshold:
            attention.append(f"• Ngày {format_local_date(heavy_day)}: {count} người nghỉ")
        if attention:
            lines.extend(["", "Cần xử lý:", *attention])
        return MemberReply(text="\n".join(lines), buttons=self._manager_shortcut())

    # --- Honest gaps ------------------------------------------------------
    def not_built_yet(self, intent: MemberIntent) -> MemberReply:
        """The truthful answer for an understood intent with no module behind it."""
        area = {
            MemberIntent.VIEW_WORK: "work",
            MemberIntent.ACCEPT_WORK: "work",
            MemberIntent.UPDATE_PROGRESS: "work",
            MemberIntent.COMPLETE_WORK: "work",
            MemberIntent.SUBMIT_PROOF: "proof",
            MemberIntent.REPORT_ISSUE: "issue",
            MemberIntent.VIEW_CHANNELS: "channels",
            MemberIntent.VIEW_SCHEDULE_GAPS: "schedule_gaps",
            MemberIntent.VIEW_PERSONAL_REPORT: "personal_report",
        }.get(intent, "work")
        return MemberReply(text=copy.not_built_yet(area), buttons=self._home_shortcut())

    # --- Helpers ----------------------------------------------------------
    @staticmethod
    def _first_name(actor: Actor) -> str:
        """What to call somebody on a card: their given name, Vietnamese order."""
        parts = (actor.full_name or "").split()
        return parts[-1] if parts else "bạn"

    @staticmethod
    def _is_manager(actor: Actor) -> bool:
        return has_permission(actor.role, Permission.HR_REQUEST_APPROVE)

    @staticmethod
    def _home_shortcut() -> list[list[ButtonSpec]]:
        return [[ButtonSpec(copy.Button.GO_BACK.value, "home.open")]]

    @staticmethod
    def _manager_shortcut() -> list[list[ButtonSpec]]:
        return [
            [
                ButtonSpec(copy.Button.PENDING_REQUESTS.value, "hr.pending"),
                ButtonSpec(copy.Button.OFF_TODAY.value, "hr.today"),
            ]
        ]

    async def _requester_name(self, user_id: object) -> str:
        from meobot.db.models.user import User

        user = await self._session.get(User, user_id)
        return user.full_name if user is not None else "Không rõ"


def role_is_member(actor: Actor) -> bool:
    """True for the role this interaction layer is designed around."""
    return actor.role is Role.EMPLOYEE
