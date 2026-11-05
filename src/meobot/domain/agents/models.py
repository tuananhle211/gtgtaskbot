"""Đội agent (0091, plan ``docs/ai-assistant/AGENT_TEAM_CHAT_PLAN.md``).

The company's Hermes agents (Chủ tịch, CEO, CMO, Marketing Ads, Pháp chế...)
run on their own VPS; APM lets people chat with them like on Telegram.

**Who sees which agent** is decided on the agent itself: its departments, its
people, or "everyone". On top of that the permission module ``AGENTS``:

* ``CHAT`` (NONE / ALL): chat with **every** agent, whatever the lists say -
  ticked per person or per department by the OWNER;
* ``MANAGE`` (``owner_only``): edit the agents and their lists.

The OWNER and an ADMIN chat with every agent; only the OWNER manages them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

MODULE_AGENTS = "AGENTS"


class AgentPermission(StrEnum):
    #: Chat with every agent (not only those whose lists name the person).
    CHAT = "CHAT"
    #: Edit the agents, who may chat with each.
    MANAGE = "MANAGE"


AGENT_PERMISSION_LABELS: Mapping[AgentPermission, str] = MappingProxyType(
    {
        AgentPermission.CHAT: "Chat với mọi agent",
        AgentPermission.MANAGE: "Quản lý đội agent",
    }
)

AGENTS_OWNER_ONLY: frozenset[str] = frozenset({AgentPermission.MANAGE.value})


def agent_allowed_scopes(_permission: AgentPermission) -> tuple[str, ...]:
    return ("NONE", "ALL")


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class MessageStatus(StrEnum):
    #: The answer is still coming.
    STREAMING = "STREAMING"
    DONE = "DONE"
    #: The agent or the link failed; what came before is kept.
    FAILED = "FAILED"
    #: The person pressed "Dừng".
    CANCELLED = "CANCELLED"


class AttachmentKind(StrEnum):
    """What a person attached to a question (0092)."""

    #: Sent to the agent as a picture (it sees it).
    IMAGE = "IMAGE"
    #: PDF / Word / Excel / PowerPoint / text: APM reads the words out and
    #: sends them with the question (Hermes takes no files).
    DOCUMENT = "DOCUMENT"
    #: A skill (``SKILL.md``, or a ``.zip`` holding one): the agent uses it in
    #: this conversation; keeping it is the usual proposal Emma approves.
    SKILL = "SKILL"


class AgentGroup(StrEnum):
    BOARD = "BOARD"
    MARKETING = "MARKETING"
    SALES = "SALES"
    SUPPORT = "SUPPORT"


AGENT_GROUP_LABELS: Mapping[AgentGroup, str] = MappingProxyType(
    {
        AgentGroup.BOARD: "Ban Giám đốc",
        AgentGroup.MARKETING: "Marketing",
        AgentGroup.SALES: "Kinh doanh - CSKH",
        AgentGroup.SUPPORT: "Hỗ trợ",
    }
)

#: A message the person types: at most this many characters.
MAX_MESSAGE_CHARS = 8000
#: At most this many messages per person in :data:`RATE_WINDOW_SECONDS`.
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 300
#: An answer longer than this is cut (status FAILED, reason timeout).
ANSWER_TIMEOUT_SECONDS = 300
#: Attachments (0092). A picture at most this big (the model's own limit is 5 MB).
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: Any other file at most this big.
MAX_FILE_BYTES = 20 * 1024 * 1024
#: At most this many files on one question.
MAX_ATTACHMENTS = 6
#: The pictures of one question together (Hermes takes a 10 MB request, base64 grows 4/3).
MAX_IMAGE_BYTES_PER_MESSAGE = 6 * 1024 * 1024
#: The words read out of one file; longer is cut (``truncated``).
MAX_FILE_TEXT_CHARS = 80_000
#: The words of every file of one question together.
MAX_ATTACHED_TEXT_CHARS = 200_000
#: At most this many uploads per person in :data:`RATE_WINDOW_SECONDS`.
UPLOAD_RATE_LIMIT = 40
#: An upload never sent is dropped after this long.
UNSENT_UPLOAD_HOURS = 24

#: A conversation title: the first words of the first question.
TITLE_CHARS = 60
#: A STREAMING answer older than this is stale (the API restarted mid-answer).
STALE_STREAM_SECONDS = ANSWER_TIMEOUT_SECONDS + 60


def conversation_title(text: str) -> str:
    """The first line of the first question, cut at a word."""
    line = " ".join(text.strip().split())
    if len(line) <= TITLE_CHARS:
        return line or "Cuộc trò chuyện mới"
    cut = line[:TITLE_CHARS].rsplit(" ", 1)[0]
    return (cut or line[:TITLE_CHARS]) + "…"


def hermes_session_id(conversation_id: object) -> str:
    """The transcript a conversation keeps on the Hermes side."""
    return f"apm-{conversation_id}"


def hermes_session_key(user_id: object, slug: str) -> str:
    """Long-term memory scope: one per person and agent."""
    return f"apm:{user_id}:{slug}"


@dataclass(frozen=True, slots=True)
class AgentSeed:
    slug: str
    name: str
    title: str
    badge: str
    group: AgentGroup
    description: str
    #: Department codes whose people chat with it.
    departments: tuple[str, ...] = ()
    everyone: bool = False


#: The 15 Hermes profiles on the VPS (04/11/2026), as 0091 seeds them.
DEFAULT_AGENTS: tuple[AgentSeed, ...] = (
    AgentSeed(
        "chutich",
        "Chủ tịch",
        "Tầm nhìn, chiến lược, phản biện CEO",
        "CT",
        AgentGroup.BOARD,
        "Mục tiêu năm/quý, bản ghi nhớ chiến lược, họp tháng.",
        ("BGD",),
    ),
    AgentSeed(
        "ceo",
        "CEO",
        "Điều hành, họp giao ban, theo dõi quyết định",
        "CEO",
        AgentGroup.BOARD,
        "Chia mục tiêu cho các giám đốc, gom biên bản họp tuần.",
        ("BGD",),
    ),
    AgentSeed(
        "cfo",
        "CFO",
        "Tài chính, chi phí quảng cáo, ngân sách",
        "CFO",
        AgentGroup.BOARD,
        "Thực thu, Ads/DT, ngân sách so với thực chi, cảnh báo.",
        ("BGD", "TCKT"),
    ),
    AgentSeed(
        "cco",
        "CCO",
        "Kinh doanh & khách hàng",
        "CCO",
        AgentGroup.SALES,
        "Doanh thu thực thu, tỉ lệ chốt, Telesale, CSKH, Chat page.",
        ("BGD", "SALE"),
    ),
    AgentSeed(
        "cmo",
        "CMO",
        "Chiến lược marketing, điều phối 5 agent MKT",
        "CMO",
        AgentGroup.MARKETING,
        "Kế hoạch kênh, BM Hà Nội / Sài Gòn, soát nội dung.",
        ("BGD", "ADS", "BT", "TK", "D", "PR", "MEDIA"),
    ),
    AgentSeed(
        "mktads",
        "Marketing Ads",
        "Phân tích chiến dịch, đề xuất Tăng / Giữ / Tắt",
        "ADS",
        AgentGroup.MARKETING,
        "Báo cáo chiến dịch, cảnh báo chi bất thường.",
        ("BGD", "ADS"),
    ),
    AgentSeed(
        "mktnghiencuu",
        "Nghiên cứu thị trường",
        "Đối thủ, giá, xu hướng",
        "NC",
        AgentGroup.MARKETING,
        "Hồ sơ đối thủ có link nguồn, insight khách.",
        ("BGD", "ADS", "BT", "TK", "D", "PR", "MEDIA"),
    ),
    AgentSeed(
        "mktnoidung",
        "Nội dung",
        "Kịch bản video, bài Fanpage",
        "ND",
        AgentGroup.MARKETING,
        "Kịch bản đúng 8 mục cấm, lịch nội dung tuần.",
        ("BGD", "BT", "PR"),
    ),
    AgentSeed(
        "mktthietke",
        "Thiết kế",
        "Brief hình ảnh, banner, mã task T",
        "TK",
        AgentGroup.MARKETING,
        "Brief thiết kế, ý tưởng hình, mã task design.",
        ("BGD", "TK"),
    ),
    AgentSeed(
        "mktdung",
        "Dựng video",
        "Kịch bản dựng, shot list, mã task D",
        "DG",
        AgentGroup.MARKETING,
        "Shot list, định dạng theo kênh, hook 3 giây.",
        ("BGD", "D", "MEDIA"),
    ),
    AgentSeed(
        "telesale",
        "Telesale",
        "Kịch bản gọi, xử lý từ chối",
        "TS",
        AgentGroup.SALES,
        "Soạn kịch bản, phân loại lead - chỉ soạn nháp.",
        ("BGD", "SALE"),
    ),
    AgentSeed(
        "cskh",
        "Chăm sóc khách hàng",
        "Hỏi thăm sau dịch vụ, khiếu nại",
        "CS",
        AgentGroup.SALES,
        "Soạn tin hỏi thăm, ghi nhận khiếu nại - chỉ soạn nháp.",
        ("BGD", "SALE"),
    ),
    AgentSeed(
        "chatpage",
        "Chat page",
        "Trả lời inbox, bình luận Fanpage",
        "CP",
        AgentGroup.SALES,
        "Soạn trả lời inbox, xin SĐT - chỉ soạn nháp.",
        ("BGD", "SALE"),
    ),
    AgentSeed(
        "nhansu",
        "Nhân sự",
        "Tuyển dụng, onboarding, đào tạo",
        "NS",
        AgentGroup.SUPPORT,
        "JD, câu hỏi phỏng vấn, kế hoạch onboarding.",
        ("BGD",),
    ),
    AgentSeed(
        "phapche",
        "Pháp chế",
        "Soát quảng cáo, hợp đồng, dữ liệu cá nhân",
        "PC",
        AgentGroup.SUPPORT,
        "Soát nội dung và hợp đồng - không thay ý kiến luật sư.",
        everyone=True,
    ),
)


__all__ = [
    "AGENTS_OWNER_ONLY",
    "AGENT_GROUP_LABELS",
    "AGENT_PERMISSION_LABELS",
    "ANSWER_TIMEOUT_SECONDS",
    "DEFAULT_AGENTS",
    "MAX_ATTACHED_TEXT_CHARS",
    "MAX_ATTACHMENTS",
    "MAX_FILE_BYTES",
    "MAX_FILE_TEXT_CHARS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_BYTES_PER_MESSAGE",
    "MAX_MESSAGE_CHARS",
    "MODULE_AGENTS",
    "RATE_LIMIT",
    "RATE_WINDOW_SECONDS",
    "STALE_STREAM_SECONDS",
    "UNSENT_UPLOAD_HOURS",
    "UPLOAD_RATE_LIMIT",
    "AgentGroup",
    "AgentPermission",
    "AgentSeed",
    "AttachmentKind",
    "MessageRole",
    "MessageStatus",
    "agent_allowed_scopes",
    "conversation_title",
    "hermes_session_id",
    "hermes_session_key",
]
