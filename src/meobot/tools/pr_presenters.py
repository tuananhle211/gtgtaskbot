"""Turning PR domain objects into something readable on a phone.

Every PR tool renders through here, so a content summary looks the same
whichever tool produced it and a change of wording is one edit rather than
eleven. Nothing in this module reads or writes the database: it takes objects
the services already loaded and returns text.

Two rules the formatting is built around:

* **No raw ORM dumps.** A manager reading a Telegram message wants the code,
  the stage and what is waiting on them - not forty columns.
* **``PASS_WITH_WARNINGS`` never renders as "passed".** It is a different
  string, a different icon, and it carries its warnings inline. Collapsing it
  would throw away the only thing that distinguishes it from ``PASS``, which is
  precisely the information a human reviewer is there to weigh.

Length is the caller's problem, not this module's: the bot already splits long
messages through :func:`meobot.bot.formatting.split_message`, and the list
formatters here bound their own output by taking a ``limit`` instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from meobot.application.pr_query_service import ContentReviewContext
from meobot.core.time import to_local
from meobot.db.models.pr import PrChannel, PrChannelAssignment, PrContentItem, PrTask
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.domain.pr.labels import STAGE_LABELS as DOMAIN_STAGE_LABELS
from meobot.domain.pr.labels import stage_label as domain_stage_label
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrApprovalDecision,
    PrApprovalStage,
    PrTaskStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability

#: How much of a script body is worth putting in a chat message before it stops
#: being a summary. A reviewer who needs the whole thing opens the document.
SCRIPT_EXCERPT_CHARS = 1200

#: Stage names as the team says them, so a message reads like a person wrote it.
#: Defined in :mod:`meobot.domain.pr.labels` since Step 1F.2.3b - the application
#: layer needs the same words and cannot import a Telegram presenter - and
#: re-exported here unchanged, so every caller in this package still reads one
#: table.
STAGE_LABELS = DOMAIN_STAGE_LABELS

TASK_STATUS_LABELS: dict[PrTaskStatus, str] = {
    PrTaskStatus.TODO: "Chưa làm",
    PrTaskStatus.IN_PROGRESS: "Đang làm",
    PrTaskStatus.BLOCKED: "Đang vướng",
    PrTaskStatus.IN_REVIEW: "Chờ duyệt",
    PrTaskStatus.REVISION_REQUIRED: "Cần sửa",
    PrTaskStatus.DONE: "Xong",
    PrTaskStatus.CANCELLED: "Đã huỷ",
}

APPROVAL_STAGE_LABELS: dict[PrApprovalStage, str] = {
    PrApprovalStage.TEAM_LEAD_REVIEW: "Trưởng nhóm duyệt",
    PrApprovalStage.HEAD_REVIEW: "Trưởng phòng duyệt",
    PrApprovalStage.INTERNAL_REVIEW: "Duyệt nội bộ",
}

DECISION_LABELS: dict[PrApprovalDecision, str] = {
    PrApprovalDecision.APPROVED: "✅ Duyệt",
    PrApprovalDecision.REVISION_REQUIRED: "↩️ Yêu cầu sửa",
    PrApprovalDecision.REJECTED: "❌ Từ chối",
}

CAPABILITY_LABELS: dict[PrCapability, str] = {
    PrCapability.PR_TEAM_LEAD_REVIEW: "Duyệt Trưởng nhóm",
    PrCapability.PR_HEAD_REVIEW: "Duyệt Trưởng phòng",
    PrCapability.PR_INTERNAL_REVIEW: "Duyệt nội bộ",
    PrCapability.PR_CONTENT_CREATE: "Tạo nội dung",
    PrCapability.PR_CONTENT_EDIT: "Sửa nội dung",
    PrCapability.PR_CONTENT_TRANSITION: "Chuyển bước nội dung",
    PrCapability.PR_CONTENT_CANCEL: "Huỷ nội dung",
    PrCapability.PR_TASK_MANAGE: "Quản lý task",
    PrCapability.PR_CHANNEL_MANAGE: "Quản lý kênh",
    PrCapability.PR_PUBLICATION_REGISTER: "Ghi nhận đã đăng",
}

#: The three AI outcomes, rendered so the middle one can never be mistaken for
#: the first. See the module docstring.
AI_RESULT_LABELS: dict[PrAiReviewResult, str] = {
    PrAiReviewResult.PASS: "✅ PASS",
    PrAiReviewResult.PASS_WITH_WARNINGS: "⚠️ PASS WITH WARNINGS",
    PrAiReviewResult.REVISION_REQUIRED: "🔁 REVISION REQUIRED",
}


def stage_label(stage: PrWorkflowStage) -> str:
    return domain_stage_label(stage)


def task_status_label(status: PrTaskStatus) -> str:
    return TASK_STATUS_LABELS.get(status, status.value)


def capability_label(capability: PrCapability) -> str:
    return CAPABILITY_LABELS.get(capability, capability.value)


def ai_result_label(result: PrAiReviewResult | None) -> str:
    return AI_RESULT_LABELS[result] if result is not None else "chưa có"


def _day(moment: datetime | None, tz: ZoneInfo) -> str:
    return to_local(moment, tz).strftime("%d/%m/%Y") if moment else "—"


def _day_time(moment: datetime | None, tz: ZoneInfo) -> str:
    return to_local(moment, tz).strftime("%d/%m %H:%M") if moment else "—"


def format_content_summary(
    content: PrContentItem,
    *,
    version: PrContentVersion | None = None,
    tz: ZoneInfo,
    owner_name: str | None = None,
    target_names: Sequence[str] = (),
) -> str:
    """One content item, as a card.

    Deliberately short: the fields a person needs to decide what to do next,
    and nothing that would push the interesting part off a phone screen.
    """
    lines = [
        f"📄 <b>{content.code}</b> — {content.title}",
        f"Bước: {stage_label(content.workflow_stage)}",
    ]
    if version is not None:
        lines.append(f"Phiên bản: v{version.version_no}")
    if owner_name:
        lines.append(f"Phụ trách: {owner_name}")
    if content.planned_publish_at:
        lines.append(f"Dự kiến đăng: {_day(content.planned_publish_at, tz)}")
    if target_names:
        lines.append(f"Kênh: {', '.join(target_names)}")
    return "\n".join(lines)


def format_content_list(
    items: Sequence[tuple[PrContentItem, int | None]],
    *,
    heading: str,
    empty: str,
) -> str:
    """A numbered list of content, one line each.

    ``(content, version_no)`` pairs rather than content alone, because "which
    draft" is the first thing a reviewer asks and looking it up per row at
    render time would be a query inside a formatter.
    """
    if not items:
        return empty
    lines = [heading]
    for index, (content, version_no) in enumerate(items, start=1):
        version = f" · v{version_no}" if version_no else ""
        lines.append(
            f"{index}. <b>{content.code}</b> — {content.title}"
            f"\n    {stage_label(content.workflow_stage)}{version}"
        )
    return "\n".join(lines)


def format_review_context(context: ContentReviewContext, *, tz: ZoneInfo) -> str:
    """Everything a human reviewer needs, in the order they need it.

    The AI verdict is rendered **in pieces** - result, score, summary, issues,
    suggestions, policy flags - exactly as
    :class:`~meobot.application.pr_query_service.ContentReviewContext` exposes
    it. There is no branch here that turns any of it into "AI đã duyệt".
    """
    content = context.content
    lines = [
        f"📄 <b>{content.code}</b> — {content.title}",
        f"Bước: {stage_label(content.workflow_stage)} · v{context.current_version.version_no}",
        "",
    ]

    script = context.current_version.script_text
    if script:
        excerpt = script.strip()
        if len(excerpt) > SCRIPT_EXCERPT_CHARS:
            excerpt = excerpt[:SCRIPT_EXCERPT_CHARS].rstrip() + "…"
        lines.extend(["<b>Nội dung:</b>", excerpt, ""])

    if context.ai_review is None:
        lines.append("🤖 <b>AI Review:</b> chưa có cho phiên bản này.")
    else:
        lines.append(f"🤖 <b>AI Review:</b> {ai_result_label(context.ai_result)}")
        if context.ai_score is not None:
            lines.append(f"Điểm: {_score(context.ai_score)}/100")
        if context.ai_summary:
            lines.append(context.ai_summary)
        lines.extend(_finding_block("Cảnh báo", context.ai_issues))
        lines.extend(_finding_block("Gợi ý", context.ai_suggestions))
        lines.extend(_finding_block("Cờ chính sách", context.ai_policy_flags))

    if context.approvals:
        lines.extend(["", "<b>Lịch sử duyệt:</b>"])
        for event in context.approvals:
            stage = APPROVAL_STAGE_LABELS.get(event.approval_stage, event.approval_stage.value)
            decision = DECISION_LABELS.get(event.decision, event.decision.value)
            note = f" — {event.comment}" if event.comment else ""
            lines.append(
                f"• {stage}: {decision} (v{event.version_reviewed}, "
                f"{_day_time(event.decided_at, tz)}){note}"
            )
    return "\n".join(lines)


def _finding_block(heading: str, findings: Sequence[Any]) -> list[str]:
    """A numbered block, or nothing at all when there is nothing to say."""
    if not findings:
        return []
    lines = ["", f"<b>{heading}:</b>"]
    for index, finding in enumerate(findings, start=1):
        if isinstance(finding, dict):
            message = finding.get("message") or finding.get("code") or str(finding)
            severity = finding.get("severity")
            where = finding.get("location")
            suffix = f" ({where})" if where else ""
            prefix = f"[{severity}] " if severity else ""
            lines.append(f"{index}. {prefix}{message}{suffix}")
        else:
            lines.append(f"{index}. {finding}")
    return lines


def _score(score: Decimal) -> str:
    """``84.00`` reads as ``84``; ``84.50`` keeps its half."""
    quantised = score.normalize()
    return format(quantised, "f")


def format_pending_review_list(
    rows: Sequence[dict[str, Any]], *, empty: str = "Hiện không có nội dung nào chờ bạn duyệt."
) -> str:
    """ "What is waiting for me", with the AI verdict on each line.

    The verdict belongs in the list rather than only in the detail view: a
    reviewer scanning ten items should be able to see which ones came with
    warnings before deciding what to open first.
    """
    if not rows:
        return empty
    lines = ["📋 <b>Nội dung đang chờ bạn duyệt</b>"]
    for index, row in enumerate(rows, start=1):
        verdict = row.get("ai_result_label") or "chưa có"
        warnings = row.get("warning_count") or 0
        warning_note = f" · {warnings} cảnh báo" if warnings else ""
        lines.append(
            f"{index}. <b>{row['code']}</b> — {row['title']}"
            f"\n    {row['stage_label']} · v{row['version_no']} · AI: {verdict}{warning_note}"
        )
    return "\n".join(lines)


def format_task_summary(task: PrTask, *, tz: ZoneInfo, assignee_names: Sequence[str] = ()) -> str:
    lines = [
        f"🧩 <b>{task.code}</b> — {task.title}",
        f"Trạng thái: {task_status_label(task.status)}",
        f"Loại: {task.task_type}",
    ]
    if task.deadline:
        lines.append(f"Hạn: {_day_time(task.deadline, tz)}")
    if assignee_names:
        lines.append(f"Người làm: {', '.join(assignee_names)}")
    return "\n".join(lines)


def format_task_list(tasks: Sequence[PrTask], *, tz: ZoneInfo, heading: str, empty: str) -> str:
    if not tasks:
        return empty
    lines = [heading]
    for index, task in enumerate(tasks, start=1):
        deadline = f" · hạn {_day_time(task.deadline, tz)}" if task.deadline else ""
        lines.append(
            f"{index}. <b>{task.code}</b> — {task.title}"
            f"\n    {task_status_label(task.status)}{deadline}"
        )
    return "\n".join(lines)


def format_channel_summary(channel: PrChannel) -> str:
    lines = [
        f"📺 <b>{channel.code}</b> — {channel.name}",
        f"Nhóm: {channel.category.value} · Trạng thái: {channel.status.value}",
    ]
    if channel.tier:
        lines.append(f"Tier: {channel.tier}")
    if channel.url:
        lines.append(channel.url)
    return "\n".join(lines)


def format_channel_list(channels: Sequence[PrChannel], *, empty: str) -> str:
    if not channels:
        return empty
    lines = ["📺 <b>Kênh</b>"]
    for index, channel in enumerate(channels, start=1):
        lines.append(f"{index}. <b>{channel.code}</b> — {channel.name} ({channel.category.value})")
    return "\n".join(lines)


def format_assignments(
    assignments: Sequence[PrChannelAssignment], *, names: dict[str, str], empty: str
) -> str:
    if not assignments:
        return empty
    lines = ["👥 <b>Phân công</b>"]
    for assignment in assignments:
        who = names.get(str(assignment.user_id), "—")
        until = assignment.effective_to.strftime("%d/%m/%Y") if assignment.effective_to else "nay"
        lines.append(
            f"• {who} — {assignment.assignment_role.value}"
            f" ({assignment.effective_from.strftime('%d/%m/%Y')} → {until})"
        )
    return "\n".join(lines)


def format_capability_list(capabilities: Sequence[PrCapability], *, who: str) -> str:
    if not capabilities:
        return f"{who} hiện chưa có quyền duyệt PR nào."
    listed = "\n".join(f"• {capability_label(item)}" for item in sorted(capabilities))
    return f"🔑 <b>Quyền PR của {who}</b>\n{listed}"


def format_capability_holders(names: Sequence[str], *, capability: PrCapability) -> str:
    label = capability_label(capability)
    if not names:
        return f"Hiện chưa có ai được cấp quyền {label}."
    listed = "\n".join(f"• {name}" for name in names)
    return f"🔑 <b>Đang có quyền {label}</b>\n{listed}"


def format_person_choices(names: Sequence[str], *, phrase: str) -> str:
    """The question asked when a name matches more than one person."""
    listed = "\n".join(f"{index}. {name}" for index, name in enumerate(names, start=1))
    return (
        f'Mình tìm thấy {len(names)} người khớp với "{phrase}":\n{listed}\n'
        "Bạn nói rõ tên đầy đủ giúp mình nhé."
    )


def format_content_choices(items: Sequence[PrContentItem], *, phrase: str) -> str:
    """The question asked when a description matches more than one content item."""
    listed = "\n".join(
        f"{index}. <b>{content.code}</b> — {content.title}"
        for index, content in enumerate(items, start=1)
    )
    return (
        f'Mình tìm thấy {len(items)} nội dung khớp với "{phrase}":\n{listed}\n'
        "Bạn cho mình mã nội dung cụ thể nhé."
    )


__all__: list[str] = [
    "AI_RESULT_LABELS",
    "APPROVAL_STAGE_LABELS",
    "CAPABILITY_LABELS",
    "STAGE_LABELS",
    "TASK_STATUS_LABELS",
    "ai_result_label",
    "capability_label",
    "format_assignments",
    "format_capability_holders",
    "format_capability_list",
    "format_channel_list",
    "format_channel_summary",
    "format_content_choices",
    "format_content_list",
    "format_content_summary",
    "format_pending_review_list",
    "format_person_choices",
    "format_review_context",
    "format_task_list",
    "format_task_summary",
    "stage_label",
    "task_status_label",
]
