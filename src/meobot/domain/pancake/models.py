"""Chăm sóc khách hàng - Pancake: agent trả lời khách (0094, 2026-11-09).

A customer writes to a Facebook page managed in Pancake; Pancake posts the
``messaging`` event to APM (``/api/cskh/pancake/webhook/<key>``); APM asks a
Hermes agent (``chatpage`` by default) through the relay and either keeps the
answer as a **suggestion** for a person to send (mode ``SUGGEST``, the default)
or sends it itself (``AUTO``) when nothing stops it.

What stops an automatic answer (the answer is then kept as a suggestion):

* the agent asks for a person (a line ``[CHUYEN_NGUOI] lý do``);
* the reply breaks the ORD script rules (the AI check, the same rules as the
  order form) or the check could not run - an automatic answer never "fails
  open";
* the conversation is paused: a staff member answered on Pancake themselves
  (``STAFF_PAUSE_HOURS``), it wears one of the page's hand-off tags, or it is
  assigned to somebody while the page says "assigned = a person handles it".

Only inbox messages are answered (comments are not). Nothing about the
customer is stored: no name, no phone, no message text - only Pancake's ids.
The words are read from Pancake when needed (to ask the agent, to show the
thread to the person deciding).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

#: Pancake's Page API (``/v1`` messages and sending, ``/v2`` conversations).
DEFAULT_API_BASE = "https://pages.fm/api/public_api"
#: The agent that answers a page unless the OWNER picks another.
DEFAULT_AGENT_SLUG = "chatpage"
#: A burst of customer messages is answered once: wait this long after the last.
DEBOUNCE_SECONDS = 20.0
#: How long the bot keeps quiet after a staff member answered on Pancake.
STAFF_PAUSE_HOURS = 12
#: The same after the agent itself asked for a person.
HANDOFF_PAUSE_HOURS = 12
#: Facebook takes at most 2000 characters in one message.
MAX_REPLY_CHARS = 2000
#: Messages shown to the agent on its first turn in a conversation.
FIRST_TURN_MESSAGES = 10
#: Messages shown to the person deciding on a suggestion.
THREAD_MESSAGES = 12
#: One answer may take this long (the agent may search the Second Brain).
ANSWER_TIMEOUT_SECONDS = 180.0
#: A row still waiting / answering after this long was cut by a restart.
STALE_MINUTES = 10
#: The marker the agent writes to ask for a person.
HANDOFF_MARKER = "[CHUYEN_NGUOI]"


class ReplyMode(StrEnum):
    #: The agent writes, a person reads and presses "Gửi".
    SUGGEST = "SUGGEST"
    #: The agent's answer goes out by itself when nothing stops it.
    AUTO = "AUTO"


REPLY_MODE_LABELS = {
    ReplyMode.SUGGEST: "Gợi ý - người duyệt rồi gửi",
    ReplyMode.AUTO: "Tự động gửi (câu hỏi đơn giản)",
}


class ReplyStatus(StrEnum):
    #: Queued; the agent is asked once the customer stops typing.
    WAITING = "WAITING"
    #: The agent is writing.
    ANSWERING = "ANSWERING"
    #: Waiting for a person: send (as is or edited) or dismiss.
    SUGGESTED = "SUGGESTED"
    SENT = "SENT"
    #: A person chose not to send it.
    DISMISSED = "DISMISSED"
    #: Not answered: folded into a later message, staff answered, paused...
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


REPLY_STATUS_LABELS = {
    ReplyStatus.WAITING: "Đang chờ khách nhắn xong",
    ReplyStatus.ANSWERING: "Agent đang soạn",
    ReplyStatus.SUGGESTED: "Chờ duyệt",
    ReplyStatus.SENT: "Đã gửi",
    ReplyStatus.DISMISSED: "Bỏ qua",
    ReplyStatus.SKIPPED: "Không trả lời",
    ReplyStatus.FAILED: "Lỗi",
}

OPEN_STATUSES = frozenset({ReplyStatus.WAITING, ReplyStatus.ANSWERING})

#: Why a row was skipped / failed, as the screen says it.
REASON_LABELS = {
    "merged": "Gộp vào tin sau của khách",
    "staff_replied": "Nhân viên đã trả lời trên Pancake",
    "paused": "Hội thoại đang tạm dừng bot",
    "handoff_tag": "Hội thoại có nhãn chuyển người",
    "assigned": "Hội thoại đã giao cho nhân viên",
    "page_off": "Page đang tắt trả lời",
    "outside_window": "Quá 24 giờ, Facebook không cho nhắn",
    "no_customer_message": "Không thấy tin mới của khách",
    "interrupted": "Bị ngắt (máy chủ khởi động lại)",
    "agents_not_configured": "Chưa kết nối được đội agent",
    "agent_failed": "Agent trả lời lỗi",
    "agent_timeout": "Agent trả lời quá lâu",
    "empty_answer": "Agent không trả lời gì",
    "pancake_failed": "Pancake từ chối",
    "token_missing": "Page chưa có token",
}


class PancakeError(Exception):
    """Pancake refused or could not be reached. ``code`` is kept on the row."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# --- the webhook ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WebhookMessage:
    """What APM takes from a ``messaging`` event (nothing about the customer)."""

    page_id: str
    conversation_id: str
    message_id: str
    #: ``INBOX`` / ``COMMENT`` / ``LIVESTREAM``.
    conversation_type: str
    #: Written by the page (a staff member, or APM itself), not the customer.
    from_page: bool
    at: datetime | None
    assignee_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WebhookConversation:
    """A ``conversation`` event: assignment / tags changed."""

    page_id: str
    conversation_id: str
    assignee_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()


def _text(value: Any, limit: int = 200) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item)[:80] for item in value if isinstance(item, str | int) and str(item))


def _tag_ids(value: Any) -> tuple[str, ...]:
    """Tags come as objects (``{"id": 3, ...}``), ids, or ``null`` for removed ones."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            if item.get("is_deactive"):
                continue
            raw = item.get("id")
        else:
            raw = item
        if isinstance(raw, str | int) and str(raw):
            out.append(str(raw)[:80])
    return tuple(out)


def parse_time(value: Any) -> datetime | None:
    """Pancake's ``2024-12-25T11:06:07.000000`` (UTC, no zone) or ISO with a zone."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _from_page(sender: Any, page_id: str) -> bool:
    if not isinstance(sender, dict):
        return False
    sender_id = str(sender.get("id") or "")
    return bool(sender.get("admin_id")) or (bool(sender_id) and sender_id == page_id)


def parse_webhook(payload: Any) -> WebhookMessage | WebhookConversation | None:
    """The event in Pancake's webhook body; None = nothing APM acts on."""
    if not isinstance(payload, dict):
        return None
    kind = payload.get("event_type")
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    page_id = _text(payload.get("page_id"), 80)
    conversation = data.get("conversation")
    conversation = conversation if isinstance(conversation, dict) else {}
    if kind == "conversation":
        conversation_id = _text(conversation.get("id"), 160)
        if not page_id or not conversation_id:
            return None
        return WebhookConversation(
            page_id=page_id,
            conversation_id=conversation_id,
            assignee_ids=_ids(conversation.get("assignee_ids")),
            tag_ids=_tag_ids(conversation.get("tags")),
        )
    if kind != "messaging":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    page_id = page_id or _text(message.get("page_id"), 80)
    conversation_id = _text(message.get("conversation_id") or conversation.get("id"), 160)
    message_id = _text(message.get("id"), 160)
    if not page_id or not conversation_id or not message_id:
        return None
    return WebhookMessage(
        page_id=page_id,
        conversation_id=conversation_id,
        message_id=message_id,
        conversation_type=_text(message.get("type") or conversation.get("type"), 20).upper(),
        from_page=_from_page(message.get("from"), page_id),
        at=parse_time(message.get("inserted_at")),
        assignee_ids=_ids(conversation.get("assignee_ids")),
        tag_ids=_tag_ids(conversation.get("tags")),
    )


# --- a thread as read from Pancake ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThreadMessage:
    id: str
    from_page: bool
    text: str
    at: datetime | None
    #: Pictures / stickers / videos sent (the agent is told, never shown them).
    attachments: int = 0


@dataclass(frozen=True, slots=True)
class Thread:
    messages: tuple[ThreadMessage, ...] = ()
    #: Facebook still lets the page write (inside the 24 h window).
    can_inbox: bool = True


_TAGS = re.compile(r"<[^>]+>")
_BREAKS = re.compile(r"<\s*(br|/p|/div)\s*/?>", re.IGNORECASE)


def plain_text(value: Any, limit: int = 4000) -> str:
    """A message's words: Pancake's ``message`` may carry HTML."""
    if not isinstance(value, str):
        return ""
    text = _BREAKS.sub("\n", value)
    text = html.unescape(_TAGS.sub("", text))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def parse_thread(payload: Any, page_id: str) -> Thread:
    """Pancake's "Get messages" answer, oldest first, text only."""
    if not isinstance(payload, dict):
        return Thread()
    out: list[ThreadMessage] = []
    for item in payload.get("messages") or []:
        if not isinstance(item, dict) or item.get("is_removed"):
            continue
        message_id = _text(item.get("id"), 160)
        if not message_id:
            continue
        text = plain_text(item.get("original_message")) or plain_text(item.get("message"))
        attachments = item.get("attachments")
        count = len(attachments) if isinstance(attachments, list) else 0
        if not text and not count:
            continue
        out.append(
            ThreadMessage(
                id=message_id,
                from_page=_from_page(item.get("from"), page_id),
                text=text,
                at=parse_time(item.get("inserted_at")),
                attachments=count,
            )
        )
    out.sort(key=lambda m: m.at.timestamp() if m.at else 0.0)
    can_inbox = payload.get("can_inbox")
    return Thread(messages=tuple(out), can_inbox=can_inbox is not False)


# --- what the agent is told -------------------------------------------------------------


def channel_briefing(page_name: str) -> str:
    """The first turn of a conversation: where the agent is and the rules."""
    return (
        f"[Kênh: Pancake · Page «{page_name}» · tin nhắn của khách hàng]\n"
        "Bạn đang trả lời khách nhắn tin vào fanpage của Apexmed. Câu bạn viết sẽ được gửi "
        "nguyên văn cho khách (có thể có nhân viên duyệt trước).\n"
        "Quy tắc:\n"
        "- Viết tiếng Việt, xưng em, gọi khách là anh/chị; ngắn gọn 1-4 câu, thân thiện, "
        "không dùng Markdown, không tiêu đề.\n"
        "- Chỉ báo giá niêm yết có trong bảng giá được phép đọc; không có thì mời khách để lại "
        "số điện thoại để tư vấn viên gọi lại.\n"
        "- Không hứa hẹn, cam kết kết quả, không so sánh với nơi khác, không nói về thuốc, "
        "biến chứng hay chẩn đoán.\n"
        "- Không hỏi, không nhắc lại, không ghi nhớ tên, số điện thoại hay ảnh của khách.\n"
        f"- Cần người thật (khách muốn đặt lịch, hỏi chuyên môn y khoa, khiếu nại, bức xúc, "
        f"hỏi điều bạn không chắc): viết dòng đầu tiên là {HANDOFF_MARKER} kèm lý do ngắn, "
        "rồi câu trả lời tạm cho khách (ví dụ: em chuyển tư vấn viên hỗ trợ mình ngay ạ).\n"
        "- Chỉ trả về đúng câu gửi khách, không giải thích thêm cho nhân viên."
    )


def customer_turn(messages: list[ThreadMessage], *, page_name: str, first: bool) -> str:
    """The question sent to the agent: the new messages, then what to do."""
    lines: list[str] = []
    if first:
        lines += [channel_briefing(page_name), "", "Đoạn hội thoại gần nhất:"]
    else:
        lines.append("Tin mới trong hội thoại:")
    for message in messages:
        who = "Nhân viên page" if message.from_page else "Khách"
        text = message.text or ""
        if message.attachments:
            text = (text + " " if text else "") + f"(gửi kèm {message.attachments} ảnh/tệp)"
        lines.append(f"- {who}: {text}")
    lines += ["", "Viết câu trả lời gửi khách."]
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    text: str
    handoff: bool = False
    handoff_reason: str = ""


_MEDIA = re.compile(r"MEDIA:\S+")


def read_answer(raw: str) -> AgentAnswer:
    """The agent's answer: the hand-off marker taken out, capped for Facebook."""
    text = _MEDIA.sub("", raw or "").strip()
    handoff, reason = False, ""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if HANDOFF_MARKER in stripped:
            handoff = True
            rest = stripped.replace(HANDOFF_MARKER, "").strip(" :-\u2013")
            if rest and not reason:
                reason = rest[:200]
            continue
        kept.append(line)
    body = "\n".join(kept).strip()
    # Markdown the agent may still write: bold / headings read badly in Messenger.
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
    body = re.sub(r"^#+\s*", "", body, flags=re.MULTILINE)
    return AgentAnswer(text=body[:MAX_REPLY_CHARS].strip(), handoff=handoff, handoff_reason=reason)


@dataclass(frozen=True, slots=True)
class PausedBy:
    reason: str
    until: datetime | None = None


@dataclass(slots=True)
class PageRules:
    """A page's switches that decide whether the bot keeps quiet."""

    handoff_tag_ids: frozenset[str] = field(default_factory=frozenset)
    stop_when_assigned: bool = False

    def paused_by(
        self,
        *,
        tag_ids: tuple[str, ...] | list[str],
        assignee_ids: tuple[str, ...] | list[str],
        paused_until: datetime | None,
        now: datetime,
    ) -> PausedBy | None:
        if paused_until is not None and paused_until > now:
            return PausedBy("paused", paused_until)
        if self.handoff_tag_ids and self.handoff_tag_ids.intersection(str(t) for t in tag_ids):
            return PausedBy("handoff_tag")
        if self.stop_when_assigned and assignee_ids:
            return PausedBy("assigned")
        return None


__all__ = [
    "ANSWER_TIMEOUT_SECONDS",
    "DEBOUNCE_SECONDS",
    "DEFAULT_AGENT_SLUG",
    "DEFAULT_API_BASE",
    "FIRST_TURN_MESSAGES",
    "HANDOFF_MARKER",
    "HANDOFF_PAUSE_HOURS",
    "MAX_REPLY_CHARS",
    "OPEN_STATUSES",
    "REASON_LABELS",
    "REPLY_MODE_LABELS",
    "REPLY_STATUS_LABELS",
    "STAFF_PAUSE_HOURS",
    "STALE_MINUTES",
    "THREAD_MESSAGES",
    "AgentAnswer",
    "PageRules",
    "PancakeError",
    "PausedBy",
    "ReplyMode",
    "ReplyStatus",
    "Thread",
    "ThreadMessage",
    "WebhookConversation",
    "WebhookMessage",
    "channel_briefing",
    "customer_turn",
    "parse_thread",
    "parse_time",
    "parse_webhook",
    "plain_text",
    "read_answer",
]
