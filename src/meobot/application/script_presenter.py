"""Rendering scripts and reviews for Telegram.

Both the bot and the Celery worker send these messages, so the formatting lives
in the application layer rather than in ``bot/texts.py``: a task must never
import the transport layer.

Everything here is a pure function over already-loaded rows.
"""

from __future__ import annotations

import uuid
from typing import Any
from zoneinfo import ZoneInfo

from meobot.application.script_service import ScriptDetail
from meobot.core.time import format_local
from meobot.db.models.script import Script
from meobot.db.models.script_review import ScriptReview
from meobot.domain.conversations.callbacks import CallbackAction, build_callback
from meobot.domain.scripts.workflow import ScriptStatus

#: How each status reads in Telegram.
STATUS_LABELS: dict[ScriptStatus, str] = {
    ScriptStatus.DRAFT: "📝 nháp",
    ScriptStatus.IMPORTED: "📥 mới nhập",
    ScriptStatus.SUBMITTED_FOR_REVIEW: "⏳ chờ AI review",
    ScriptStatus.REVIEWING: "🤖 đang review",
    ScriptStatus.AI_REVIEWED: "🔍 đã review",
    ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL: "🖐 chờ duyệt",
    ScriptStatus.REVISION_REQUIRED: "✏️ cần sửa",
    ScriptStatus.APPROVED_FOR_PRODUCTION: "✅ duyệt sản xuất",
    ScriptStatus.IN_PRODUCTION: "🎬 đang quay",
    ScriptStatus.ARCHIVED: "🗄 lưu trữ",
}

#: Long fields are trimmed so one script never exceeds a Telegram message.
BODY_PREVIEW = 700
LIST_TITLE_LENGTH = 48


def short_id(script_id: uuid.UUID) -> str:
    """The 8-character id shown to humans; accepted back by ``/script``."""
    return script_id.hex[:8]


def status_label(status: ScriptStatus) -> str:
    return STATUS_LABELS.get(status, status.value)


def format_pending_list(
    scripts: list[Script],
    *,
    total: int,
    offset: int = 0,
) -> str:
    """Render ``/pending_scripts``."""
    if not scripts:
        return "🎉 Không có kịch bản nào đang chờ xử lý."

    shown = f"{offset + 1}-{offset + len(scripts)}"
    lines = [f"📋 Kịch bản đang chờ ({shown} / {total}):"]
    for script in scripts:
        version = script.current_version
        title = (version.title if version else script.external_script_id)[:LIST_TITLE_LENGTH]
        lines.append(
            f"• `{short_id(script.id)}` {title} — {status_label(script.status)}"
            + (f" (v{version.version_number})" if version else "")
        )
    lines.append("\nXem chi tiết: /script <mã>")
    return "\n".join(lines)


def format_script_detail(detail: ScriptDetail, *, timezone: ZoneInfo) -> str:
    """Render ``/script <id>``."""
    script = detail.script
    version = detail.version
    lines = [
        f"🎬 *{version.title if version else script.external_script_id}*",
        f"Mã: `{short_id(script.id)}` (sheet: {script.external_script_id})",
        f"Trạng thái: {status_label(script.status)}",
        f"Tác giả: {script.author or '—'}",
        f"Thể loại: {detail.script_type.name if detail.script_type else '—'}",
        f"Phiên bản hiện tại: v{detail.version_number}",
    ]
    if script.deadline:
        lines.append(f"Deadline: {script.deadline.isoformat()}")
    if detail.profile:
        source = f"{detail.profile.name} · {detail.profile.sheet_name}"
        if script.source_row_number:
            source += f" · dòng {script.source_row_number}"
        lines.append(f"Nguồn: {source}")
        if detail.profile.spreadsheet_url:
            lines.append(detail.profile.spreadsheet_url)
    if version and version.hook:
        lines.append(f"\n🪝 Hook: {version.hook[:300]}")

    if detail.review is not None:
        lines.append("")
        lines.append(
            f"🤖 Review gần nhất: {detail.review.overall_score}/100 ({detail.review.verdict.value})"
        )
        lines.append(detail.review.summary[:400])
        lines.append(
            f"_Chấm lúc {format_local(detail.review.created_at, timezone, '%d/%m %H:%M')}_"
        )
        if not detail.review_matches_version:
            lines.append("⚠️ Review này thuộc phiên bản cũ. Nội dung đã thay đổi — cần review lại.")
    else:
        lines.append("\n🤖 Chưa có review nào. Dùng /review_script để chấm.")

    return "\n".join(lines)


def format_full_script(detail: ScriptDetail) -> str:
    """Render the body of a script for the 'view full script' button."""
    version = detail.version
    if version is None:
        return "Kịch bản chưa có nội dung."
    parts = [f"📄 *{version.title}* (v{version.version_number})"]
    if version.hook:
        parts.append(f"\n🪝 {version.hook}")
    parts.append(f"\n{version.script_body[:3000]}")
    if version.production_notes:
        parts.append(f"\n📌 Ghi chú: {version.production_notes[:400]}")
    return "\n".join(parts)


def format_review_result(detail: ScriptDetail, review: ScriptReview) -> str:
    """Render the review message pushed to Telegram after a review run."""
    script = detail.script
    version = detail.version
    lines = [
        f"🤖 *Kết quả review* — {review.overall_score}/100 ({review.verdict.value})",
        f"Kịch bản: `{short_id(script.id)}` · {version.title if version else '—'}",
        f"Tác giả: {script.author or '—'} · Thể loại: "
        f"{detail.script_type.name if detail.script_type else '—'} · v{detail.version_number}",
        "",
        review.summary,
    ]

    strengths = _as_list(review.strengths)
    if strengths:
        lines.append("\n✅ *Điểm mạnh:*")
        lines.extend(f"• {item}" for item in strengths[:3])

    issues = _as_list(review.critical_issues)
    if issues:
        lines.append("\n⚠️ *Vấn đề nghiêm trọng:*")
        lines.extend(f"• {item}" for item in issues[:3])

    recommendations = _as_list(review.recommendations)
    if recommendations:
        lines.append("\n💡 *Đề xuất:*")
        lines.extend(f"• {item}" for item in recommendations[:3])

    if review.revised_hook_suggestion:
        lines.append(f"\n🪝 *Hook gợi ý:* {review.revised_hook_suggestion[:300]}")

    if detail.profile is not None:
        source = f"\n📊 Nguồn: {detail.profile.name} · dòng {script.source_row_number or '—'}"
        if detail.profile.spreadsheet_url:
            source += f"\n{detail.profile.spreadsheet_url}"
        lines.append(source)

    lines.append("\n_Duyệt ở đây chỉ có nghĩa: được phép sản xuất. Không phải được phép đăng._")
    return "\n".join(lines)


def build_review_keyboard(
    script_id: uuid.UUID,
    version_number: int,
    *,
    secret: str,
) -> dict[str, Any]:
    """Inline keyboard for a review message, as Telegram's JSON shape.

    The version number travels in the callback data so a decision taken on a
    stale message is refused rather than applied to newer text.
    """
    argument = str(version_number)

    def button(text: str, action: CallbackAction) -> dict[str, str]:
        return {
            "text": text,
            "callback_data": build_callback(
                action, secret=secret, entity_id=script_id, argument=argument
            ),
        }

    return {
        "inline_keyboard": [
            [
                button("✅ Duyệt sản xuất", CallbackAction.APPROVE_PRODUCTION),
                button("✏️ Yêu cầu sửa", CallbackAction.REQUEST_REVISION),
            ],
            [
                button("🔁 Review lại", CallbackAction.REVIEW_AGAIN),
                button("📄 Xem full", CallbackAction.VIEW_SCRIPT),
            ],
            [button("⏭ Bỏ qua", CallbackAction.SKIP)],
        ]
    }


def _as_list(value: Any) -> list[str]:
    """JSON columns come back as lists, but defensively accept anything."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []
