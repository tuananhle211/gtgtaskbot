"""Every word a Member reads, in one place.

**Why a registry rather than strings in handlers.** Wording that lives next to
the code that sends it drifts: the button says one thing, the confirmation card
says another, and the error says it in English. Worse, a rule like "never show a
Member an enum" cannot be *checked* when the text is scattered - there is
nowhere to look. Here there is: the copy-quality test scans this module, and a
forbidden English interface word fails the build.

**What may stay English.** Platform names people actually say out loud -
Facebook, TikTok, YouTube, Google Drive, Telegram - and the word "MeoBot". Every
other word a Member sees is Vietnamese.

**What may never appear** in anything here: an enum value, a database id, a
UUID, a permission name, a tool name, a callback payload, a provider error, a
model name, or an English status word.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# --- Proper nouns that are allowed to stay as they are -----------------------
#: Checked by the copy-quality test, which otherwise rejects Latin-script words
#: that look like interface English.
ALLOWED_PROPER_NOUNS: Final[frozenset[str]] = frozenset(
    {
        "TasksBot",
        "Telegram",
        "Facebook",
        "TikTok",
        "YouTube",
        "Google",
        "Drive",
        "Sheet",
        "Sheets",
        "Member",
        "Guest",
        "AI",
    }
)


class WorkStatusLabel(StrEnum):
    """Vietnamese wording for the work states this deployment will use.

    The internal names stay English wherever they are stored; these are what a
    person reads. Defined here rather than next to a workflow enum because the
    work module does not exist yet - when it lands, it maps onto these words
    instead of inventing a second vocabulary.
    """

    ASSIGNED = "Chưa làm"
    ACCEPTED = "Đã nhận việc"
    IN_PROGRESS = "Đang làm"
    PENDING_GROUP_APPROVAL = "Chờ admin group duyệt"
    PUBLISHED = "Đã đăng"
    REJECTED = "Bị từ chối"
    REMOVED = "Bị gỡ"
    COMPLETED = "Đã hoàn thành"
    OVERDUE = "Quá hạn"
    CANCELLED = "Đã huỷ"


#: Domain vocabulary, so no handler ever writes "task" or "quota" at a person.
TERMS: Final[dict[str, str]] = {
    "quota": "Lượt trò chuyện AI",
    "task": "Việc",
    "submit": "Nộp kết quả",
    "proof": "Bằng chứng",
    "progress": "Tiến độ",
    "dashboard": "Tổng quan",
    "access_request": "Yêu cầu sử dụng TasksBot",
    "seed_order": "Yêu cầu seeding",
    "seed_task": "Việc seeding",
    "social_report": "Báo cáo kênh",
    "channel_registry": "Danh sách kênh",
}


# --- Buttons ----------------------------------------------------------------
class Button(StrEnum):
    """Inline-button labels.

    Short and concrete on purpose: a button is read in a fraction of a second on
    a phone, and "Xác nhận" tells somebody what will happen where "OK" does not.
    """

    # Home
    MY_WORK = "📋 Việc của tôi"
    SUBMIT_RESULT = "📤 Nộp kết quả"
    REPORT_ISSUE = "⚠️ Báo vấn đề"
    VIEW_PROGRESS = "📊 Xem tiến độ"
    ASK_MEOBOT = "💬 Hỏi TasksBot"

    # Work cards
    START_WORK = "▶️ Bắt đầu làm"
    ACCEPT_WORK = "✅ Nhận việc"
    MARK_DONE = "✅ Đã hoàn thành"
    SUBMIT_PROOF = "📤 Nộp bằng chứng"
    HAS_PROBLEM = "⚠️ Có vấn đề"
    VIEW_DETAIL = "📄 Xem chi tiết"

    # Generic
    CONFIRM = "✅ Xác nhận"
    EDIT_AGAIN = "✏️ Sửa lại"
    DISCARD = "❌ Huỷ"
    GO_BACK = "⬅️ Quay lại"

    # Issue kinds
    ISSUE_WAITING_APPROVAL = "⏳ Đang chờ duyệt"
    ISSUE_REFUSED = "🚫 Bị từ chối"
    ISSUE_TAKEN_DOWN = "🗑 Bị gỡ sau khi đăng"
    ISSUE_BROKEN_LINK = "🔗 Link bị lỗi"
    ISSUE_BLOCKED = "🚧 Không thể thực hiện"
    ISSUE_OTHER = "📝 Lý do khác"

    # HR - Member
    ASK_LEAVE = "📝 Xin nghỉ phép"
    ASK_LATE = "⏰ Xin đi muộn"
    MY_REQUESTS = "📋 Yêu cầu của tôi"
    MY_HR_STATS = "📊 Thống kê của tôi"
    SEND_REQUEST = "✅ Gửi yêu cầu"
    WITHDRAW_REQUEST = "↩️ Rút yêu cầu"

    # HR - leave periods
    MORNING = "🌅 Buổi sáng"
    AFTERNOON = "🌇 Buổi chiều"
    FULL_DAY = "📅 Cả ngày"
    PICK_HOURS = "⏰ Chọn giờ cụ thể"
    NO_REASON = "⏭ Không có lý do cụ thể"

    # HR - approver
    APPROVE = "✅ Duyệt"
    REFUSE = "❌ Từ chối"
    ASK_FOR_MORE = "💬 Yêu cầu bổ sung"
    VIEW_HISTORY = "📄 Xem lịch sử"
    PENDING_REQUESTS = "📥 Đơn chờ duyệt"
    OFF_TODAY = "🏖 Nghỉ hôm nay"
    LATE_TODAY = "⏰ Đi muộn hôm nay"
    WEEKLY_REPORT = "📊 Báo cáo tuần"
    MONTHLY_REPORT = "📅 Báo cáo tháng"
    BY_PERSON = "👤 Xem theo nhân sự"

    # AI allowance
    MY_AI_ALLOWANCE = "💬 Lượt trò chuyện AI"


# --- Cards and sentences ----------------------------------------------------
HOME_GREETING: Final[str] = "Chào {name} 👋"
HOME_QUESTION: Final[str] = "Bạn muốn làm gì?"
HOME_NOTHING_TODAY: Final[str] = "Hôm nay bạn chưa có việc nào được giao."

HELP_MEMBER: Final[str] = """TASKSBOT CÓ THỂ GIÚP BẠN

📋 Công việc
• Xem việc hôm nay
• Nhận việc
• Báo tiến độ
• Xem việc quá hạn

📤 Nộp kết quả
• Gửi link bài
• Gửi ảnh bằng chứng
• Báo số bình luận đã làm

⚠️ Báo vấn đề
• Bài chờ duyệt
• Bài bị từ chối
• Bài bị gỡ
• Không truy cập được link

📝 Nghỉ phép và đi muộn
• Xin nghỉ cả ngày, buổi sáng hoặc buổi chiều
• Xin đi muộn và báo giờ có mặt
• Xem yêu cầu đang chờ duyệt
• Xem thống kê nghỉ phép của bạn

📊 Kết quả cá nhân
• Xem tỷ lệ hoàn thành
• Xem các kênh mình phụ trách

Bạn không cần nhớ câu lệnh.
Hãy nhắn cho mình như nói chuyện bình thường."""

AI_ALLOWANCE: Final[str] = """Bạn còn {remaining}/{limit} lượt trò chuyện AI hôm nay.

Nhận việc, cập nhật tiến độ, nộp bài và báo vấn đề không bị trừ lượt."""

AI_ALLOWANCE_UNLIMITED: Final[str] = "Vai trò của bạn không giới hạn lượt trò chuyện AI."

AI_ALLOWANCE_LOW: Final[str] = """Bạn còn {remaining} lượt trò chuyện AI hôm nay.
Nhận việc, nộp kết quả và báo tiến độ vẫn không bị giới hạn."""

#: How many are left before MeoBot volunteers the warning above.
LOW_ALLOWANCE_THRESHOLD: Final[int] = 3


# --- Clarification and ambiguity --------------------------------------------
ASK_WHICH_WORK: Final[str] = "Bạn đang nói tới việc nào?"
ASK_HOW_MUCH_DONE: Final[str] = "Bạn đã hoàn thành bao nhiêu?"
ASK_FOR_PROOF: Final[str] = "Bạn gửi giúp mình link hoặc ảnh bằng chứng nhé."
ASK_WHICH_TASK_FOR_PHOTO: Final[str] = "Ảnh này thuộc việc nào vậy bạn?"

FLOW_EXPIRED: Final[str] = """Phiên cập nhật vừa rồi đã hết thời gian.
Bạn hãy chọn lại việc cần cập nhật nhé."""

NOTHING_TO_CONTINUE: Final[str] = "Hiện bạn không có thao tác nào đang dở."

CANCELLED: Final[str] = "Mình đã huỷ thao tác vừa rồi."

NOT_UNDERSTOOD: Final[str] = """Mình chưa hiểu ý bạn.
Bạn thử nói lại theo cách khác, hoặc bấm một trong các nút bên dưới nhé."""


# --- Errors, in words somebody can act on ------------------------------------
class Problem(StrEnum):
    """Failures translated into something a Member can do something about.

    Each of these replaces a technical message that would otherwise reach a
    person: a provider error code, a permission identifier, a stack trace. The
    original stays in the log, where an operator can find it.
    """

    NO_PERMISSION = "Bạn chưa được cấp quyền xem dữ liệu này."
    WORK_NOT_FOUND = (
        "Mình không còn tìm thấy việc này. "
        "Có thể việc đã được huỷ hoặc danh sách vừa được cập nhật."
    )
    REQUEST_NOT_FOUND = (
        "Mình không còn tìm thấy yêu cầu này. Có thể yêu cầu đã được xử lý hoặc đã bị rút."
    )
    AI_UNAVAILABLE = (
        "TasksBot chưa thể tạo nội dung lúc này.\nLượt trò chuyện AI của bạn chưa bị trừ."
    )
    FACEBOOK_DISCONNECTED = (
        "TasksBot hiện chưa lấy được số liệu của Page này vì kết nối Facebook đã hết hạn.\n\n"
        "Quản trị viên đã được thông báo để kết nối lại.\nBạn không cần thao tác gì."
    )
    BUTTON_EXPIRED = "Nút này đã hết hiệu lực. Bạn mở lại danh sách rồi thử lại nhé."
    BUTTON_NOT_YOURS = "Nút này không dành cho bạn."
    LIST_TOO_OLD = (
        "Danh sách bạn đang nhắc tới đã cũ. Bạn mở lại danh sách rồi chọn lại giúp mình nhé."
    )
    SOMETHING_WENT_WRONG = "Mình gặp trục trặc khi xử lý yêu cầu này. Bạn thử lại sau một chút nhé."


# --- Truthful "not built yet" ------------------------------------------------
#: Used wherever an intent is understood but the business module behind it does
#: not exist. Saying so plainly is the only honest option: returning a fake
#: success would be worse than returning nothing.
NOT_BUILT_YET: Final[dict[str, str]] = {
    "work": (
        "Tính năng giao việc đang được hoàn thiện.\n"
        "Hiện tại TasksBot chưa quản lý danh sách việc của bạn."
    ),
    "seeding": (
        "Tính năng giao việc seeding đang được hoàn thiện.\n"
        "Hiện tại bạn chưa thể nộp kết quả seeding qua TasksBot."
    ),
    "proof": (
        "Tính năng nộp bằng chứng đang được hoàn thiện.\n"
        "Hiện tại TasksBot chưa lưu được ảnh hay link kết quả."
    ),
    "channels": (
        "Danh sách kênh bạn phụ trách đang được hoàn thiện.\n"
        "Hiện tại TasksBot chưa quản lý phân công kênh."
    ),
    "schedule_gaps": (
        "Lịch đăng bài đang được hoàn thiện.\n"
        "Hiện tại TasksBot chưa theo dõi được kênh nào còn thiếu bài."
    ),
    "personal_report": (
        "Báo cáo kết quả cá nhân đang được hoàn thiện.\n"
        "Hiện tại TasksBot chưa tổng hợp được số liệu của bạn."
    ),
    "issue": (
        "Tính năng báo vấn đề bài đăng đang được hoàn thiện.\n"
        "Hiện tại bạn hãy báo trực tiếp cho Trưởng phòng giúp mình nhé."
    ),
}


def not_built_yet(area: str) -> str:
    """The honest answer for an intent whose module does not exist yet."""
    return NOT_BUILT_YET.get(area, NOT_BUILT_YET["work"])


def home_card(
    *,
    name: str,
    lines: list[str],
    question: str = HOME_QUESTION,
) -> str:
    """Assemble the Member home card.

    ``lines`` are already-rendered bullet points, so the caller decides what is
    worth telling this person today and this function decides how it reads.
    """
    body = [HOME_GREETING.format(name=name), ""]
    if lines:
        body.append("Hôm nay bạn có:")
        body.extend(f"• {line}" for line in lines)
    else:
        body.append(HOME_NOTHING_TODAY)
    body.extend(["", question])
    return "\n".join(body)
