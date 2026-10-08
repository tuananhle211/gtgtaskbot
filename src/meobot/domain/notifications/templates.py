"""Every cross-chat message body, in one registry.

Two reasons this is not written inline where each message is sent.

**Privacy is checkable.** A template declares its own classification and the
exact fields it accepts. So "the attendance group message cannot contain a
leave reason" is not a habit somebody has to maintain - the template has no
``reason`` field, and a test asserts that no group-bound template does.

**Rendering is not delivery.** A template turns validated structured data into
Vietnamese text. It never touches Telegram, never opens a database, and cannot
be given a value the payload did not carry - so a worker rendering a stored
payload months later produces the same words the person confirmed.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from meobot.domain.notifications.models import (
    NotificationEvent,
    PrivacyClassification,
)


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    """One renderable notification.

    Args:
        key: Stable identifier stored on the outbox row.
        version: Bumped when the wording changes, so an old row still renders
            the text it was written for.
        classification: How far this template's content may travel.
        required: Fields the payload must supply.
        optional: Fields that may be absent.
    """

    key: str
    version: int
    classification: PrivacyClassification
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    renderer: Any = field(default=None, compare=False, repr=False)
    #: Telegram ``parse_mode`` for the rendered text (``"HTML"``), or ``None``
    #: for plain text - every template but the few that need markup. A
    #: template that sets it escapes every payload value it prints.
    parse_mode: str | None = None
    #: Payload fields that must not outlive the message: the outbox blanks them
    #: once the row is settled (delivered or given up). The renderer must cope
    #: with a blanked value - a hand-made retry of a settled row then sends a
    #: harmless sentence instead of the secret.
    secret_fields: tuple[str, ...] = ()

    def render(self, payload: dict[str, Any]) -> str:
        """Render the message, refusing anything the template did not declare.

        Raises:
            KeyError: A required field is missing.
            ValueError: The payload carries a field this template never
                declared - which is how an accidental private value gets
                stopped rather than silently printed.
        """
        missing = [name for name in self.required if name not in payload]
        if missing:
            raise KeyError(f"{self.key}: missing {', '.join(sorted(missing))}")
        allowed = set(self.required) | set(self.optional)
        extra = sorted(set(payload) - allowed)
        if extra:
            raise ValueError(f"{self.key}: payload carries undeclared fields {extra}")
        return str(self.renderer(payload)).strip()


def _announcement_group(payload: dict[str, Any]) -> str:
    # "on behalf of" and never "from": MeoBot does not impersonate anybody.
    return f"""📢 THÔNG BÁO TỪ TRƯỞNG PHÒNG

{payload["content"]}

— TasksBot gửi thay Trưởng phòng"""


def _hr_request_to_approver(payload: dict[str, Any]) -> str:
    lines = [
        payload["heading"],
        "",
        f"Người gửi: {payload['requester_name']}",
        f"Thời gian: {payload['period']}",
    ]
    if payload.get("arrival"):
        lines.append(f"Dự kiến có mặt: {payload['arrival']}")
    if payload.get("late_minutes") is not None:
        lines.append(f"Đi muộn: {payload['late_minutes']} phút")
    lines.append(f"Lý do: {payload.get('reason') or 'Không có lý do cụ thể'}")
    lines.append(f"Gửi lúc: {payload['submitted_at']}")
    return "\n".join(lines)


def _hr_approved_to_member(payload: dict[str, Any]) -> str:
    return f"✅ Yêu cầu {payload['summary']} của bạn đã được duyệt."


def _hr_rejected_to_member(payload: dict[str, Any]) -> str:
    lines = [f"❌ Yêu cầu {payload['summary']} của bạn chưa được duyệt."]
    if payload.get("reason"):
        lines.append(f"Lý do: {payload['reason']}")
    return "\n".join(lines)


def _hr_more_info_to_member(payload: dict[str, Any]) -> str:
    lines = [f"💬 Trưởng phòng cần thêm thông tin cho yêu cầu {payload['summary']}."]
    if payload.get("note"):
        lines.append(payload["note"])
    lines.append("Bạn trả lời giúp mình nhé.")
    return "\n".join(lines)


def _attendance_leave(payload: dict[str, Any]) -> str:
    # No reason field exists here, and none can be added: render() rejects any
    # payload key the template did not declare.
    return f"""👥 CẬP NHẬT NHÂN SỰ

{payload["person"]} {payload["period"]}.
Yêu cầu đã được Trưởng phòng duyệt."""


def _attendance_late(payload: dict[str, Any]) -> str:
    return f"""⏰ CẬP NHẬT NHÂN SỰ

{payload["person"]} được duyệt có mặt lúc {payload["arrival"]} ngày {payload["work_date"]}."""


def _announcement_reminder(payload: dict[str, Any]) -> str:
    return f"""🔔 NHẮC XÁC NHẬN

Bạn chưa xác nhận đã đọc thông báo:
{payload["excerpt"]}"""


# --- 0.6.0a2 ---------------------------------------------------------------
def _access_request_to_owner(payload: dict[str, Any]) -> str:
    lines = [
        "🔔 CÓ NGƯỜI MUỐN DÙNG TASKSBOT",
        "",
        f"Người gửi: {payload['requester_name']}",
        f"Nơi nhắn: {payload['chat_label']}",
    ]
    if payload.get("question_preview"):
        lines.append(f"Nội dung: {payload['question_preview']}")
    lines.append("")
    lines.append("Bạn mở thẻ duyệt trong chat riêng để quyết định giúp mình nhé.")
    return "\n".join(lines)


def _access_decision_to_requester(payload: dict[str, Any]) -> str:
    return str(payload["outcome"])


def _quota_request_to_owner(payload: dict[str, Any]) -> str:
    return f"""📨 XIN THÊM LƯỢT TRÒ CHUYỆN

Thành viên: {payload["requester_name"]}
Hạn mức hiện tại: {payload["current_limit"]} lượt/ngày"""


def _quota_decision_to_member(payload: dict[str, Any]) -> str:
    return str(payload["outcome"])


def _guest_reply(payload: dict[str, Any]) -> str:
    # The answer, and nothing about the approval that made it possible: the
    # group does not need to know somebody was vetted.
    return str(payload["answer"])


def _reminder_personal(payload: dict[str, Any]) -> str:
    return f"""⏰ NHẮC VIỆC

{payload["content"]}"""


def _reminder_group(payload: dict[str, Any]) -> str:
    return f"""⏰ NHẮC VIỆC

{payload["content"]}

— TasksBot nhắc theo lịch đã đặt"""


def _delivery_failed_alert(payload: dict[str, Any]) -> str:
    # No provider body, no numeric chat id, no exception name. Only what the
    # person can act on: what did not arrive, where, why, and what still holds.
    return f"""⚠️ THÔNG BÁO CHƯA GỬI ĐƯỢC

Nội dung:
{payload["business_summary"]}

Nơi nhận:
{payload["destination_label"]}

Lý do:
{payload["reason_label"]}

Nội dung nghiệp vụ vẫn đã được lưu."""


def _announcement_delivered(payload: dict[str, Any]) -> str:
    """Told to the person who sent it, once Telegram has actually accepted it.

    The counterpart to "đã được xếp hàng gửi" in the source chat. Two messages
    rather than one because they answer two different questions, and the release
    exists because those two questions had been given one answer.
    """
    return f"""✅ Đã gửi thông báo tới {payload["destination_label"]}.

Nội dung:
{payload["business_summary"]}"""


def _delivery_recovered_alert(payload: dict[str, Any]) -> str:
    return f"""✅ ĐÃ GỬI ĐƯỢC

Nội dung:
{payload["business_summary"]}

Nơi nhận:
{payload["destination_label"]}"""


def _destination_unhealthy(payload: dict[str, Any]) -> str:
    return f"""⚠️ MỘT NƠI NHẬN ĐANG CÓ VẤN ĐỀ

Nơi nhận:
{payload["destination_label"]}

Tình trạng:
{payload["health_label"]}

TasksBot tạm thời chưa gửi được tin vào đây."""


def _destination_recovered(payload: dict[str, Any]) -> str:
    return f"""✅ NƠI NHẬN ĐÃ HOẠT ĐỘNG TRỞ LẠI

Nơi nhận:
{payload["destination_label"]}

TasksBot gửi tin vào đây bình thường trở lại."""


def _dispatch_group_part(payload: dict[str, Any]) -> str:
    """One group's copy of one part of a multi-group announcement.

    The "(2/3)" marker is added here rather than stored with the part, so what
    is kept in ``message_dispatch_parts`` is exactly the words the sender
    confirmed and the numbering is presentation. A single-part announcement
    carries no marker at all: numbering one message is noise.
    """
    marker = str(payload.get("part_marker") or "").strip()
    heading = "📢 THÔNG BÁO TỪ TRƯỞNG PHÒNG"
    if marker:
        heading = f"{heading} {marker}"
    body = [heading, "", payload["content"]]
    if not marker or payload.get("is_last_part"):
        # The signature closes the announcement, so it belongs on the last part
        # only - repeating it three times would read as three announcements.
        body.extend(["", "— TasksBot gửi thay Trưởng phòng"])
    return "\n".join(body)


def _dispatch_summary(payload: dict[str, Any]) -> str:
    """What the sender is told once every destination has settled.

    Per destination, because that is the only honest shape: "đã gửi" for a
    dispatch where one group refused the bot would be false for that group, and
    "chưa gửi được" would be false for the other two.
    """
    lines = [
        "📬 KẾT QUẢ GỬI THÔNG BÁO",
        "",
        f"Đã gửi thành công: {payload['delivered']}/{payload['total']}",
    ]
    if payload.get("delivered_lines"):
        lines.extend(["", str(payload["delivered_lines"])])
    if payload.get("failed_lines"):
        lines.extend(["", "Chưa gửi được:", str(payload["failed_lines"])])
    return "\n".join(lines)


def _order_update(payload: dict[str, Any]) -> str:
    """One shape for every order hand-off: what happened, which order, a link.

    The web inbox already carries the same heading and body; Telegram repeats
    them for the units that opted in, so a person reads one sentence in both.
    """
    lines = [
        str(payload["heading"]),
        "",
        f"{payload['title']} ({payload['code']})",
        str(payload["body"]),
    ]
    note = str(payload.get("note", "")).strip()
    if note:
        lines.extend(["", f"Ghi chú: {note}"])
    lines.extend(["", str(payload["link"])])
    return "\n".join(lines)


def _account_temporary_password(payload: dict[str, Any]) -> str:
    """The temporary password, in ``<code>`` so one tap copies it. HTML mode.

    A blanked payload (the outbox scrubs the password once the message is
    settled) renders a sentence without one, so a manual retry of an old row
    can never resend - or invent - a credential.
    """
    login_url = html.escape(str(payload["login_url"]))
    password = str(payload["password"])
    if not password:
        return (
            "Mật khẩu tạm TasksBot này đã hết hiệu lực. "
            f"Cần mật khẩu mới thì bấm “Quên mật khẩu?” tại {login_url}"
        )
    return (
        f"Mật khẩu tạm TasksBot của bạn: <code>{html.escape(password)}</code>. "
        f"Đăng nhập tại {login_url} rồi đổi mật khẩu."
    )


def _pr_content_approved(payload: dict[str, Any]) -> str:
    """ "Duyệt xong - giờ ai làm?"

    The instruction is the message. A bare "đã được duyệt" leaves the reader
    with nothing to do and a piece that waits; naming the next step - take the
    production or arrange somebody who will - is what turns the notification
    into the handoff it is announcing.
    """
    return "\n".join(
        [
            "✅ NỘI DUNG ĐÃ ĐƯỢC DUYỆT",
            "",
            f"{payload['title']} ({payload['content_code']}) đã được Trưởng phòng duyệt.",
            "Hãy nhận sản xuất hoặc phối hợp phân công người sản xuất để bắt đầu sản xuất.",
            "",
            str(payload["link"]),
        ]
    )


def _pr_production_assigned(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🎬 BẠN ĐƯỢC PHÂN CÔNG SẢN XUẤT",
            "",
            f"Bạn được phân công sản xuất nội dung {payload['title']} ({payload['content_code']}).",
            "",
            str(payload["link"]),
        ]
    )


def _pr_team_lead_approved(payload: dict[str, Any]) -> str:
    """Status, and it says so by naming what happens next without asking for it.

    The reader has nothing to do here - the piece is now somebody else's
    decision - so this deliberately does *not* end with an instruction, unlike
    :func:`_pr_content_approved`. A message that reads like a task when there is
    no task is how people learn to skim the ones that are.
    """
    return "\n".join(
        [
            "👍 TRƯỞNG NHÓM ĐÃ DUYỆT",
            "",
            f"{payload['title']} ({payload['content_code']}) đã được Trưởng nhóm duyệt "
            "và đang chờ Trưởng phòng duyệt.",
            "",
            str(payload["link"]),
        ]
    )


def _pr_production_revision_required(payload: dict[str, Any]) -> str:
    """The cut came back. Names the reviewer's note when there is one.

    "Cần sửa" without saying what is a message that costs the reader a trip to
    the panel to learn anything at all, so the note travels with it. It is
    optional because a reviewer is not forced to write one.
    """
    lines = [
        "✏️ SẢN PHẨM CẦN CHỈNH SỬA",
        "",
        f"{payload['title']} ({payload['content_code']}) cần chỉnh sửa sản phẩm "
        "trước khi duyệt nội bộ.",
    ]
    note = payload.get("note")
    if note:
        lines.append(f"Ghi chú của người duyệt: {note}")
    lines.extend(["", str(payload["link"])])
    return "\n".join(lines)


def _pr_internal_review_approved(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "✅ ĐÃ DUYỆT NỘI BỘ",
            "",
            f"{payload['title']} ({payload['content_code']}) đã được duyệt nội bộ "
            "và sẵn sàng cho bước tiếp theo.",
            "",
            str(payload["link"]),
        ]
    )


def _pr_workflow_undone(payload: dict[str, Any]) -> str:
    """The correction. Deliberately explicit about what is no longer true.

    Somebody has already been told "đã duyệt, hãy nhận sản xuất". Saying only
    "có thay đổi" would leave them believing production may proceed, which is
    the exact misunderstanding an undo has to close.
    """
    return "\n".join(
        [
            "↩️ MỘT BƯỚC DUYỆT ĐÃ ĐƯỢC HOÀN TÁC",
            "",
            f"{payload['title']} ({payload['content_code']}): {payload['what']}.",
            f"Nội dung quay lại bước {payload['stage_label']}.",
            "",
            str(payload["link"]),
        ]
    )


TEMPLATES: dict[str, MessageTemplate] = {
    template.key: template
    for template in (
        MessageTemplate(
            key="announcement.group",
            version=1,
            classification=PrivacyClassification.PUBLIC_OPERATIONAL,
            required=("content",),
            # A flag, not content: it tells the delivery worker whether to
            # attach "✅ Đã đọc" and is never rendered into the message.
            optional=("request_read_receipt",),
            renderer=_announcement_group,
        ),
        MessageTemplate(
            key="hr.request_to_approver",
            version=1,
            # The reason lives here, so this template may only ever go to a
            # private chat. ``may_route`` enforces that.
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("heading", "requester_name", "period", "submitted_at"),
            optional=("reason", "arrival", "late_minutes"),
            renderer=_hr_request_to_approver,
        ),
        MessageTemplate(
            key="hr.approved_to_member",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("summary",),
            renderer=_hr_approved_to_member,
        ),
        MessageTemplate(
            key="hr.rejected_to_member",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("summary",),
            optional=("reason",),
            renderer=_hr_rejected_to_member,
        ),
        MessageTemplate(
            key="hr.more_info_to_member",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("summary",),
            optional=("note",),
            renderer=_hr_more_info_to_member,
        ),
        MessageTemplate(
            key="hr.attendance_leave",
            version=1,
            # Neutral by construction: a name, a period, and nothing else.
            classification=PrivacyClassification.PUBLIC_OPERATIONAL,
            required=("person", "period"),
            renderer=_attendance_leave,
        ),
        MessageTemplate(
            key="hr.attendance_late",
            version=1,
            classification=PrivacyClassification.PUBLIC_OPERATIONAL,
            required=("person", "arrival", "work_date"),
            renderer=_attendance_late,
        ),
        MessageTemplate(
            key="announcement.reminder",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("excerpt",),
            renderer=_announcement_reminder,
        ),
        # --- 0.6.0a2: the two flows that used to send directly -----------
        MessageTemplate(
            key="access.request_to_owner",
            version=1,
            # Carries a stranger's words. Private chat only, and never a group
            # - "somebody outside asked us something" is a management matter.
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("requester_name", "chat_label"),
            optional=("question_preview",),
            renderer=_access_request_to_owner,
        ),
        MessageTemplate(
            key="access.decision_to_requester",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("outcome",),
            renderer=_access_decision_to_requester,
        ),
        MessageTemplate(
            key="quota.request_to_owner",
            version=1,
            # How much allowance one named person has left is about that
            # person. It goes to the owner privately, never to a group.
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("requester_name", "current_limit"),
            renderer=_quota_request_to_owner,
        ),
        MessageTemplate(
            key="quota.decision_to_member",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("outcome",),
            renderer=_quota_decision_to_member,
        ),
        # --- 0.6.0a2: Guest replay --------------------------------------
        MessageTemplate(
            key="guest.reply",
            version=1,
            # Goes back into the group the question was asked in, so it must be
            # group-safe. It carries only the generated answer: no reason, no
            # note, no internal identifier, and nothing about the approval.
            classification=PrivacyClassification.PUBLIC_OPERATIONAL,
            required=("answer",),
            renderer=_guest_reply,
        ),
        # --- 0.6.0a2: reminders ------------------------------------------
        MessageTemplate(
            key="reminder.personal",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("content",),
            renderer=_reminder_personal,
        ),
        MessageTemplate(
            key="reminder.group",
            version=1,
            # Only ever created for a destination the author was authorised to
            # address, and it carries only what they typed.
            classification=PrivacyClassification.TEAM_OPERATIONAL,
            required=("content",),
            renderer=_reminder_group,
        ),
        # --- 0.6.0a2: delivery and destination health ---------------------
        MessageTemplate(
            key="delivery.failed_alert",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("business_summary", "destination_label", "reason_label"),
            renderer=_delivery_failed_alert,
        ),
        MessageTemplate(
            key="announcement.delivered",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("business_summary", "destination_label"),
            renderer=_announcement_delivered,
        ),
        MessageTemplate(
            key="delivery.recovered_alert",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("business_summary", "destination_label"),
            renderer=_delivery_recovered_alert,
        ),
        MessageTemplate(
            key="destination.unhealthy",
            version=1,
            classification=PrivacyClassification.MANAGEMENT_ONLY,
            required=("destination_label", "health_label"),
            renderer=_destination_unhealthy,
        ),
        MessageTemplate(
            key="destination.recovered",
            version=1,
            classification=PrivacyClassification.MANAGEMENT_ONLY,
            required=("destination_label",),
            renderer=_destination_recovered,
        ),
        # --- 0.6.0a3: one announcement, several groups --------------------
        MessageTemplate(
            key="dispatch.group_part",
            version=1,
            # Same ceiling as a single-destination announcement, and for the
            # same reason: this is what a room full of colleagues will read.
            classification=PrivacyClassification.PUBLIC_OPERATIONAL,
            required=("content",),
            optional=("part_marker", "is_last_part", "request_read_receipt"),
            renderer=_dispatch_group_part,
        ),
        # --- PR workflow, Step 1F.2.3b -----------------------------------
        # All three are PERSONAL_PRIVATE: they name a piece of content, who is
        # expected to act on it and what a reviewer decided, which is team
        # information about a person's work and belongs in their own chat rather
        # than in a group where the routing rules would have to judge it.
        MessageTemplate(
            key="pr.content_approved",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("title", "content_code", "link"),
            renderer=_pr_content_approved,
        ),
        MessageTemplate(
            key="pr.production_assigned",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("title", "content_code", "link"),
            renderer=_pr_production_assigned,
        ),
        MessageTemplate(
            key="pr.workflow_undone",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("title", "content_code", "what", "stage_label", "link"),
            renderer=_pr_workflow_undone,
        ),
        # --- PR workflow, Step 1F.2.3d -----------------------------------
        # PERSONAL_PRIVATE for the same reason as the three above: each names a
        # person's work and a reviewer's decision about it.
        MessageTemplate(
            key="pr.team_lead_approved",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("title", "content_code", "link"),
            renderer=_pr_team_lead_approved,
        ),
        MessageTemplate(
            key="pr.production_revision_required",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("title", "content_code", "link"),
            # The reviewer's note. Optional because a reviewer may leave none,
            # and declared rather than smuggled in: ``MessageTemplate.render``
            # refuses a payload field no template named.
            optional=("note",),
            renderer=_pr_production_revision_required,
        ),
        MessageTemplate(
            key="pr.internal_review_approved",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("title", "content_code", "link"),
            renderer=_pr_internal_review_approved,
        ),
        # --- Ads order engine --------------------------------------------
        # PERSONAL_PRIVATE like the PR ones: every hand-off names a person's
        # work and somebody's decision on it.
        MessageTemplate(
            key="orders.update",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("heading", "title", "code", "body", "link"),
            optional=("note",),
            renderer=_order_update,
        ),
        # --- Account (0046) ---------------------------------------------
        # A credential: only ever the owner's private chat, and the outbox
        # blanks it once the row is settled.
        MessageTemplate(
            key="account.temporary_password",
            version=1,
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("password", "login_url"),
            renderer=_account_temporary_password,
            parse_mode="HTML",
            secret_fields=("password",),
        ),
        MessageTemplate(
            key="dispatch.summary",
            version=1,
            # Names which groups did and did not receive an announcement, which
            # is the sender's business and nobody else's - so it may only ever
            # reach a private chat.
            classification=PrivacyClassification.PERSONAL_PRIVATE,
            required=("delivered", "total"),
            optional=("delivered_lines", "failed_lines"),
            renderer=_dispatch_summary,
        ),
    )
}

#: Which template each event uses when it is aimed at a group.
GROUP_TEMPLATES: dict[NotificationEvent, str] = {
    NotificationEvent.ANNOUNCEMENT_PUBLISHED: "announcement.group",
    NotificationEvent.HR_ATTENDANCE_UPDATE: "hr.attendance_leave",
}


def template_for(key: str) -> MessageTemplate:
    """Look one up, failing loudly for an unknown key.

    Raises:
        KeyError: The key is not registered. A caller inventing template names
            is a bug, and one that would otherwise surface as an unsendable row
            hours later in a worker.
    """
    if key not in TEMPLATES:
        raise KeyError(f"Unknown notification template: {key!r}")
    return TEMPLATES[key]


def render(key: str, payload: dict[str, Any]) -> str:
    """Render one template against a payload."""
    return template_for(key).render(payload)


def group_safe_templates() -> list[MessageTemplate]:
    """Every template that is allowed to reach a group.

    Used by the privacy test, which asserts none of them declares a field that
    could carry a personal reason.
    """
    return [
        template for template in TEMPLATES.values() if template.classification.may_reach_a_group
    ]
