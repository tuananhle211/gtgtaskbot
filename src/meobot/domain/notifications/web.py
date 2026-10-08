"""The same events, in the words a notification panel uses.

Step 1F.2.3d. :mod:`meobot.domain.notifications.templates` writes for a Telegram
chat; this writes for a bell dropdown. They describe the same events and they
are deliberately not the same strings.

Why two sets of words rather than one
--------------------------------------

A Telegram message is the whole of what the reader gets, so it carries an emoji
heading, blank lines, the content code in brackets and the URL spelled out at the
end. A dropdown row is a **title, one line, and a timestamp**, and the link is
where the row goes when clicked rather than something to read.

Rendering the Telegram string into that row would put a bare
``https://…/pr/content/8f3e…`` in front of somebody as body text - a raw
identifier shown as content, which is the one thing a deep link must not become.
Truncating it instead would cut a message mid-sentence at whatever width the
panel happened to be.

So each event gets a short title and a body that reads as a sentence on its own,
and the target travels as structured columns. Neither module imports the other;
what they share is :class:`~meobot.domain.notifications.models.NotificationEvent`,
which is the actual contract.

No identifiers in the text
---------------------------

The bodies below name the **content title** and nothing else. The code
(``CNT-2026-000042``) is in the Telegram version because a chat has no other way
to disambiguate two pieces with similar names; the panel has the row, the link
and the page it lands on. A UUID never appears in either.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from meobot.domain.notifications.models import NotificationEvent

#: What a row's bold line says. One per PR event the panel can show.
#:
#: Phrased as *what happened*, not as *what to do*: the body carries the
#: instruction where there is one, and a title that gave orders would read as
#: shouting in a list of twenty.
WEB_TITLES: Mapping[NotificationEvent, str] = MappingProxyType(
    {
        NotificationEvent.PR_CONTENT_APPROVED: "Nội dung đã được duyệt",
        NotificationEvent.PR_CONTENT_TEAM_LEAD_APPROVED: "Trưởng nhóm đã duyệt",
        NotificationEvent.PR_PRODUCTION_ASSIGNED: "Bạn được phân công sản xuất",
        NotificationEvent.PR_PRODUCTION_REVISION_REQUIRED: "Sản phẩm cần chỉnh sửa",
        NotificationEvent.PR_INTERNAL_REVIEW_APPROVED: "Đã duyệt nội bộ",
        NotificationEvent.PR_WORKFLOW_UNDONE: "Một bước duyệt đã được hoàn tác",
        NotificationEvent.PR_WORK_ASSIGNED: "Bạn được giao một công việc",
        NotificationEvent.PR_WORK_PROPOSAL_DECIDED: "Đề xuất công việc đã được xử lý",
        NotificationEvent.PR_WORK_AWAITING_VALIDATION: "Có công việc chờ bạn xác nhận",
        NotificationEvent.PR_WORK_APPROVED: "Công việc đã được xác nhận",
        NotificationEvent.PR_KPI_PLAN_SUBMITTED: "Có kế hoạch KPI chờ bạn duyệt",
        NotificationEvent.PR_KPI_PLAN_APPROVED: "Kế hoạch KPI của bạn đã được duyệt",
        NotificationEvent.PR_KPI_PLAN_RETURNED: "Kế hoạch KPI của bạn cần chỉnh sửa",
        NotificationEvent.ORDER_SUBMITTED: "Có order mới cần duyệt",
        NotificationEvent.ORDER_APPROVED: "Có order mới",
        NotificationEvent.ORDER_RETURNED: "Order cần sửa, gửi lại",
        NotificationEvent.ORDER_NODE_TURN: "Có order tới lượt",
        NotificationEvent.ORDER_NODE_ASSIGNED: "Bạn được giao một công đoạn",
        NotificationEvent.ORDER_NODE_ACCEPTED: "Đã có người nhận việc",
        NotificationEvent.ORDER_SUBMISSION_READY: "Có bài chờ bạn duyệt",
        NotificationEvent.ORDER_NODE_RETURNED: "Bài của bạn cần sửa",
        NotificationEvent.ORDER_FINAL_RETURNED: "Sản phẩm cần sửa lại",
        NotificationEvent.ORDER_COMPLETED: "Order đã hoàn thành",
    }
)


def web_title(event: NotificationEvent) -> str:
    """The dropdown row's bold line.

    Falls back to a neutral sentence rather than the event code: an event added
    to the enum without a title here should look unremarkable in somebody's
    panel, not leak ``pr_content_measured`` at them.
    """
    return WEB_TITLES.get(event, "Cập nhật nội dung")


def web_body(event: NotificationEvent, *, title: str, what: str = "", note: str = "") -> str:
    """The line under it, as a sentence naming the content.

    Args:
        event: Which of the six this is.
        title: The content item's own title. Never a code and never an id.
        what: For :attr:`~NotificationEvent.PR_WORKFLOW_UNDONE`, the phrase from
            :data:`~meobot.application.pr_notifications.UNDO_DESCRIPTIONS`
            naming which decision was taken back. Ignored by every other event.
        note: For :attr:`~NotificationEvent.PR_PRODUCTION_REVISION_REQUIRED`, the
            reviewer's comment when they left one. Appended rather than
            substituted, so an empty note simply produces the shorter sentence.
    """
    if event is NotificationEvent.PR_CONTENT_APPROVED:
        return (
            f"“{title}” đã được Trưởng phòng duyệt. Hãy nhận sản xuất hoặc "
            "phân công người sản xuất để bắt đầu."
        )
    if event is NotificationEvent.PR_CONTENT_TEAM_LEAD_APPROVED:
        return f"“{title}” đã được Trưởng nhóm duyệt và đang chờ Trưởng phòng duyệt."
    if event is NotificationEvent.PR_PRODUCTION_ASSIGNED:
        return f"Bạn được phân công sản xuất nội dung “{title}”."
    if event is NotificationEvent.PR_PRODUCTION_REVISION_REQUIRED:
        body = f"“{title}” cần chỉnh sửa sản phẩm trước khi duyệt nội bộ."
        return f"{body} Ghi chú: {note}" if note else body
    if event is NotificationEvent.PR_INTERNAL_REVIEW_APPROVED:
        return f"“{title}” đã được duyệt nội bộ và sẵn sàng cho bước tiếp theo."
    if event is NotificationEvent.PR_WORKFLOW_UNDONE:
        return f"“{title}”: {what}." if what else f"“{title}”: một bước duyệt đã được hoàn tác."
    return f"“{title}” có cập nhật mới."


__all__: list[str] = ["WEB_TITLES", "web_body", "web_title"]
