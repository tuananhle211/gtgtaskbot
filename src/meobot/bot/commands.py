"""The command registry: one definition per Telegram command.

Before this existed there were three lists that had to agree and did not - the
handlers, the ``HELP`` string, and nothing at all for Telegram's own command
menu. ``/help`` drifted from reality the moment a command was added.

Now every command is declared once here, and everything else is derived:

* :func:`render_help` builds the ``/help`` text;
* :func:`telegram_commands` builds the ``setMyCommands`` menu;
* ``/capabilities`` reads roles and permissions off these specs;
* the test-suite asserts that every spec has a handler and every handler has a
  spec, so the next command cannot be added to only one of them.

This registry describes *availability*, not authority. The policy engine and
the per-handler permission checks remain the enforcement points; a spec's
``permission`` only decides whether a command is worth showing to somebody.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from meobot.domain.identity.models import Role
from meobot.domain.permissions.matrix import Permission, has_permission


class CommandCategory(StrEnum):
    """Grouping used to lay out ``/help``."""

    SYSTEM = "Hệ thống"
    CHAT = "Trò chuyện"
    SHEETS = "Google Sheet"
    DRIVE = "Tạo Sheet trên Google Drive"
    SCRIPTS = "Kịch bản"
    PEOPLE = "Nhân sự"
    ACCESS = "Quyền truy cập trong group"
    QUOTA = "Hạn mức trò chuyện"
    CONFIRMATION = "Xác nhận"


#: Order categories appear in ``/help``.
CATEGORY_ORDER: tuple[CommandCategory, ...] = (
    CommandCategory.SYSTEM,
    CommandCategory.CHAT,
    CommandCategory.SHEETS,
    CommandCategory.DRIVE,
    CommandCategory.SCRIPTS,
    CommandCategory.PEOPLE,
    CommandCategory.ACCESS,
    CommandCategory.QUOTA,
    CommandCategory.CONFIRMATION,
)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One slash command.

    Args:
        command: Name without the leading slash.
        description: One line, short enough for Telegram's menu (256 chars).
        usage: Exact syntax, shown when required arguments are missing.
        category: Where it appears in ``/help``.
        permission: Permission a role must hold for the command to be listed.
        min_role: Minimum role for the command to be listed.
        available_to_unregistered: True for commands an unknown Telegram
            account may run - only ``/start`` and ``/join``.
        example: Optional concrete example appended to the usage hint.
    """

    command: str
    description: str
    usage: str
    category: CommandCategory
    permission: Permission | None = None
    min_role: Role | None = None
    available_to_unregistered: bool = False
    example: str | None = None

    @property
    def slash(self) -> str:
        return f"/{self.command}"

    def visible_to(self, role: Role) -> bool:
        """True when ``role`` should see this command in ``/help``."""
        if self.min_role is not None and role.rank < self.min_role.rank:
            return False
        return self.permission is None or has_permission(role, self.permission)

    def usage_text(self) -> str:
        """Friendly reply for a command invoked without its arguments."""
        text = f"Cú pháp: {self.usage}"
        if self.example:
            text += f"\nVí dụ: {self.example}"
        return text


COMMANDS: tuple[CommandSpec, ...] = (
    # --- System -----------------------------------------------------------
    CommandSpec(
        command="start",
        description="Kiểm tra đăng nhập và vai trò",
        usage="/start",
        category=CommandCategory.SYSTEM,
        available_to_unregistered=True,
    ),
    CommandSpec(
        command="help",
        description="Xem danh sách lệnh",
        usage="/help",
        category=CommandCategory.SYSTEM,
        available_to_unregistered=True,
    ),
    CommandSpec(
        command="health",
        description="Kiểm tra tình trạng hệ thống",
        usage="/health",
        category=CommandCategory.SYSTEM,
        permission=Permission.SYSTEM_HEALTH,
    ),
    CommandSpec(
        command="capabilities",
        description="TasksBot hiện làm được gì",
        usage="/capabilities",
        category=CommandCategory.SYSTEM,
    ),
    CommandSpec(
        command="whoami",
        description="Xem hồ sơ TasksBot đang dùng cho bạn",
        usage="/whoami",
        category=CommandCategory.SYSTEM,
    ),
    CommandSpec(
        command="assistant_profile",
        description="Xem danh tính và nhiệm vụ của TasksBot",
        usage="/assistant_profile",
        category=CommandCategory.SYSTEM,
    ),
    CommandSpec(
        command="script_types",
        description="Liệt kê thể loại kịch bản",
        usage="/script_types",
        category=CommandCategory.SYSTEM,
        permission=Permission.SCRIPT_TYPE_READ,
    ),
    # --- Chat -------------------------------------------------------------
    CommandSpec(
        command="new_chat",
        description="Bắt đầu một cuộc trò chuyện mới",
        usage="/new_chat",
        category=CommandCategory.CHAT,
        permission=Permission.CONVERSATION_USE,
    ),
    CommandSpec(
        command="clear_chat",
        description="Lưu trữ cuộc trò chuyện hiện tại",
        usage="/clear_chat",
        category=CommandCategory.CHAT,
        permission=Permission.CONVERSATION_USE,
    ),
    CommandSpec(
        command="chat_status",
        description="Xem trạng thái trò chuyện và mô hình đang dùng",
        usage="/chat_status",
        category=CommandCategory.CHAT,
        permission=Permission.CONVERSATION_USE,
    ),
    CommandSpec(
        command="chat_test",
        description="Kiểm tra trợ lý AI (văn bản và định tuyến)",
        usage="/chat_test",
        category=CommandCategory.CHAT,
        # OWNER-only: the probes spend real tokens against the configured model.
        min_role=Role.OWNER,
    ),
    CommandSpec(
        command="set_preferred_address",
        description="Đổi cách TasksBot xưng hô với bạn",
        usage="/set_preferred_address <cách bạn muốn được gọi>",
        category=CommandCategory.CHAT,
        permission=Permission.CONVERSATION_USE,
        example="/set_preferred_address anh",
    ),
    # --- Sheets -----------------------------------------------------------
    CommandSpec(
        command="sheets",
        description="Danh sách Google Sheet đã cấu hình",
        usage="/sheets",
        category=CommandCategory.SHEETS,
        permission=Permission.SHEET_PROFILE_READ,
    ),
    CommandSpec(
        command="add_sheet",
        description="Thêm Google Sheet mới (từng bước)",
        usage="/add_sheet",
        category=CommandCategory.SHEETS,
        permission=Permission.SHEET_PROFILE_WRITE,
    ),
    CommandSpec(
        command="sync_sheets",
        description="Đồng bộ kịch bản từ Sheet",
        usage="/sync_sheets [tên hoặc mã Sheet]",
        category=CommandCategory.SHEETS,
        permission=Permission.SHEET_PROFILE_WRITE,
        example="/sync_sheets TikTok",
    ),
    CommandSpec(
        command="cancel_flow",
        description="Huỷ thao tác nhiều bước đang dở",
        usage="/cancel_flow",
        category=CommandCategory.SHEETS,
    ),
    # --- Drive ------------------------------------------------------------
    CommandSpec(
        command="drive_status",
        description="Kiểm tra cấu hình Google Drive",
        usage="/drive_status",
        category=CommandCategory.DRIVE,
        permission=Permission.DRIVE_FOLDER_READ,
    ),
    CommandSpec(
        command="sheet_templates",
        description="Danh sách mẫu Sheet TasksBot tạo được",
        usage="/sheet_templates",
        category=CommandCategory.DRIVE,
        permission=Permission.SHEET_TEMPLATE_READ,
    ),
    CommandSpec(
        command="drive_folders",
        description="Thư mục Drive TasksBot được phép tạo file",
        usage="/drive_folders",
        category=CommandCategory.DRIVE,
        permission=Permission.DRIVE_FOLDER_READ,
    ),
    CommandSpec(
        command="add_drive_folder",
        description="Đăng ký một thư mục Drive cho phép",
        usage="/add_drive_folder",
        category=CommandCategory.DRIVE,
        permission=Permission.DRIVE_FOLDER_MANAGE,
    ),
    CommandSpec(
        command="create_work_sheet",
        description="Tạo Sheet quản lý công việc",
        usage="/create_work_sheet",
        category=CommandCategory.DRIVE,
        permission=Permission.SPREADSHEET_CREATE,
    ),
    CommandSpec(
        command="create_script_sheet",
        description="Tạo Sheet quản lý kịch bản",
        usage="/create_script_sheet",
        category=CommandCategory.DRIVE,
        permission=Permission.SPREADSHEET_CREATE,
    ),
    CommandSpec(
        command="created_sheets",
        description="Các Sheet TasksBot đã tạo",
        usage="/created_sheets",
        category=CommandCategory.DRIVE,
        permission=Permission.SPREADSHEET_READ,
    ),
    # --- Scripts ----------------------------------------------------------
    CommandSpec(
        command="pending_scripts",
        description="Kịch bản đang chờ review hoặc duyệt",
        usage="/pending_scripts [vị trí]",
        category=CommandCategory.SCRIPTS,
        permission=Permission.SCRIPT_READ,
        example="/pending_scripts 2",
    ),
    CommandSpec(
        command="script",
        description="Xem chi tiết một kịch bản",
        usage="/script <mã kịch bản>",
        category=CommandCategory.SCRIPTS,
        permission=Permission.SCRIPT_READ,
        example="/script TT-0312",
    ),
    CommandSpec(
        command="review_script",
        description="Yêu cầu AI chấm điểm một kịch bản",
        usage="/review_script <mã kịch bản>",
        category=CommandCategory.SCRIPTS,
        permission=Permission.SCRIPT_REVIEW,
        example="/review_script TT-0312",
    ),
    # --- People -----------------------------------------------------------
    CommandSpec(
        command="create_invite",
        description="Tạo mã mời cho thành viên mới",
        usage="/create_invite [vai trò] [số lượt] [số ngày]",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_MANAGE,
        example="/create_invite Nhân viên 5 7",
    ),
    CommandSpec(
        command="add_user",
        description="Thêm thành viên bằng Telegram ID",
        usage="/add_user <telegram_id> <vai trò> [họ tên]",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_MANAGE,
        example="/add_user 123456789 Nhân viên Nguyễn Văn A",
    ),
    CommandSpec(
        command="join",
        description="Đăng ký bằng mã mời",
        usage="/join <mã mời>",
        category=CommandCategory.PEOPLE,
        available_to_unregistered=True,
        example="/join MEO-1A2B3C",
    ),
    # --- Access control in groups -----------------------------------------
    # Every one of these targets somebody else, so every one of them asks for a
    # reply rather than a name - see :mod:`meobot.bot.handlers.people`.
    CommandSpec(
        command="allow_user",
        description="Cho phép TasksBot trả lời người này trong group",
        usage="/allow_user (reply vào tin nhắn của họ)",
        category=CommandCategory.ACCESS,
        permission=Permission.GROUP_MEMBER_POLICY_MANAGE,
    ),
    CommandSpec(
        command="ignore_user",
        description="Ngừng trả lời người này trong group",
        usage="/ignore_user (reply vào tin nhắn của họ)",
        category=CommandCategory.ACCESS,
        permission=Permission.GROUP_MEMBER_POLICY_MANAGE,
    ),
    CommandSpec(
        command="mute_user",
        description="Im lặng với người này trong một khoảng thời gian",
        usage="/mute_user [1h|1d|7d|30d] (reply)",
        category=CommandCategory.ACCESS,
        permission=Permission.GROUP_MEMBER_POLICY_MANAGE,
        example="/mute_user 7d",
    ),
    CommandSpec(
        command="unmute_user",
        description="Bỏ hạn chế với người này trong group",
        usage="/unmute_user (reply vào tin nhắn của họ)",
        category=CommandCategory.ACCESS,
        permission=Permission.GROUP_MEMBER_POLICY_MANAGE,
    ),
    CommandSpec(
        command="guest_user",
        description="Cấp quyền Guest tạm thời trong group này",
        usage="/guest_user [số lượt] [1h|1d|7d|30d] (reply)",
        category=CommandCategory.ACCESS,
        permission=Permission.GUEST_ACCESS_MANAGE,
        example="/guest_user 10 1d",
    ),
    CommandSpec(
        command="extend_guest",
        description="Thêm lượt hoặc thời gian cho một Guest",
        usage="/extend_guest [số lượt] [1h|1d|7d|30d] (reply)",
        category=CommandCategory.ACCESS,
        permission=Permission.GUEST_ACCESS_MANAGE,
        example="/extend_guest 10",
    ),
    CommandSpec(
        command="reset_guest",
        description="Đặt lại lượt Guest về mặc định",
        usage="/reset_guest (reply vào tin nhắn của họ)",
        category=CommandCategory.ACCESS,
        permission=Permission.GUEST_ACCESS_MANAGE,
    ),
    CommandSpec(
        command="revoke_guest",
        description="Thu hồi quyền Guest ngay lập tức",
        usage="/revoke_guest (reply vào tin nhắn của họ)",
        category=CommandCategory.ACCESS,
        permission=Permission.GUEST_ACCESS_MANAGE,
    ),
    CommandSpec(
        command="guest_info",
        description="Xem lượt Guest còn lại của một người",
        usage="/guest_info (reply vào tin nhắn của họ)",
        category=CommandCategory.ACCESS,
        permission=Permission.GROUP_MEMBER_POLICY_READ,
    ),
    CommandSpec(
        command="group_guests",
        description="Danh sách chính sách trong group này",
        usage="/group_guests",
        category=CommandCategory.ACCESS,
        permission=Permission.GROUP_MEMBER_POLICY_READ,
    ),
    CommandSpec(
        command="pending_access",
        description="Yêu cầu truy cập đang chờ duyệt",
        usage="/pending_access",
        category=CommandCategory.ACCESS,
        permission=Permission.GUEST_ACCESS_MANAGE,
    ),
    CommandSpec(
        command="user_info",
        description="Xem vai trò và trạng thái của một thành viên",
        usage="/user_info (reply vào tin nhắn của họ)",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_READ,
    ),
    CommandSpec(
        command="suspend_user",
        description="Tạm khoá một thành viên",
        usage="/suspend_user [lý do] (reply)",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_STATUS_MANAGE,
        example="/suspend_user nghỉ phép dài ngày",
    ),
    CommandSpec(
        command="enable_user",
        description="Mở khoá một thành viên",
        usage="/enable_user (reply vào tin nhắn của họ)",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_STATUS_MANAGE,
    ),
    CommandSpec(
        command="revoke_user",
        description="Thu hồi quyền sử dụng (giữ nguyên lịch sử)",
        usage="/revoke_user [lý do] (reply)",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_STATUS_MANAGE,
        example="/revoke_user đã nghỉ việc",
    ),
    CommandSpec(
        command="change_user_role",
        description="Đổi vai trò của một thành viên",
        usage="/change_user_role <vai trò> (reply)",
        category=CommandCategory.PEOPLE,
        permission=Permission.USER_ROLE_MANAGE,
        example="/change_user_role Trưởng nhóm",
    ),
    # --- Chat quota -------------------------------------------------------
    CommandSpec(
        command="quota",
        description="Xem số lượt trò chuyện AI còn lại của bạn",
        usage="/quota",
        category=CommandCategory.QUOTA,
        permission=Permission.CONVERSATION_USE,
    ),
    CommandSpec(
        command="user_quota",
        description="Xem hạn mức của một thành viên",
        usage="/user_quota (reply vào tin nhắn của họ)",
        category=CommandCategory.QUOTA,
        permission=Permission.USER_READ,
    ),
    CommandSpec(
        command="add_user_quota",
        description="Thêm lượt cho hôm nay",
        usage="/add_user_quota <số lượt> (reply)",
        category=CommandCategory.QUOTA,
        permission=Permission.USER_QUOTA_MANAGE,
        example="/add_user_quota 10",
    ),
    CommandSpec(
        command="reset_user_quota",
        description="Đặt lại lượt đã dùng hôm nay",
        usage="/reset_user_quota (reply vào tin nhắn của họ)",
        category=CommandCategory.QUOTA,
        permission=Permission.USER_QUOTA_MANAGE,
    ),
    CommandSpec(
        command="set_user_daily_quota",
        description="Đặt hạn mức hằng ngày riêng",
        usage="/set_user_daily_quota <số lượt> (reply)",
        category=CommandCategory.QUOTA,
        permission=Permission.USER_QUOTA_MANAGE,
        example="/set_user_daily_quota 50",
    ),
    CommandSpec(
        command="clear_user_quota_override",
        description="Bỏ hạn mức riêng, quay lại mặc định",
        usage="/clear_user_quota_override (reply)",
        category=CommandCategory.QUOTA,
        permission=Permission.USER_QUOTA_MANAGE,
    ),
    # --- Confirmation -----------------------------------------------------
    CommandSpec(
        command="confirm",
        description="Xác nhận một hành động rủi ro cao",
        usage="/confirm <mã xác nhận>",
        category=CommandCategory.CONFIRMATION,
        example="/confirm 8f3a21",
    ),
    CommandSpec(
        command="cancel",
        description="Huỷ một hành động đang chờ xác nhận",
        usage="/cancel <mã xác nhận>",
        category=CommandCategory.CONFIRMATION,
        example="/cancel 8f3a21",
    ),
)

#: Fast lookup used by handlers that need their own usage text.
BY_NAME: dict[str, CommandSpec] = {spec.command: spec for spec in COMMANDS}


def spec_for(command: str) -> CommandSpec:
    """Return the spec for ``command`` (without the slash).

    Raises:
        KeyError: When the command has no spec. That is a programming error -
            a handler exists that the registry does not know about - and the
            test suite catches it before deployment.
    """
    return BY_NAME[command.lstrip("/")]


def commands_for(role: Role) -> list[CommandSpec]:
    """Commands ``role`` may see, in declaration order."""
    return [spec for spec in COMMANDS if spec.visible_to(role)]


def public_commands() -> frozenset[str]:
    """Slash commands an unregistered Telegram account may send."""
    return frozenset(spec.slash for spec in COMMANDS if spec.available_to_unregistered)


def telegram_commands(role: Role = Role.EMPLOYEE) -> list[tuple[str, str]]:
    """``[(command, description)]`` for ``setMyCommands``.

    Telegram's menu is global per scope, so the default menu is built for the
    least privileged role: it must not advertise ``/create_invite`` to an
    employee who would only be refused.
    """
    return [(spec.command, spec.description) for spec in commands_for(role)]


def render_help(role: Role, *, unregistered: bool = False) -> str:
    """Build the ``/help`` body for ``role``.

    Rendered in the HTML parse mode with every dynamic part escaped - see
    :mod:`meobot.bot.formatting` for why this is not Markdown.
    """
    from meobot.bot.formatting import bold, escape

    if unregistered:
        specs: Sequence[CommandSpec] = [spec for spec in COMMANDS if spec.available_to_unregistered]
    else:
        specs = commands_for(role)

    lines: list[str] = [
        "🤖 " + bold("TasksBot") + " — trợ lý vận hành nội dung",
        "",
    ]
    for category in CATEGORY_ORDER:
        in_category = [spec for spec in specs if spec.category is category]
        if not in_category:
            continue
        lines.append(bold(category.value))
        lines.extend(f"{escape(spec.usage)} — {escape(spec.description)}" for spec in in_category)
        lines.append("")

    if not unregistered:
        lines.extend(
            [
                bold("Nhắn tự nhiên"),
                escape(
                    "Bạn có thể nhắn bình thường: hỏi TasksBot làm được gì, "
                    "brainstorm ý tưởng nội dung, hoặc yêu cầu thao tác thật "
                    'như "đồng bộ các Sheet" hay "tạo Sheet kịch bản tháng 8".'
                ),
                "",
                escape(
                    "⚠️ Duyệt kịch bản = được phép sản xuất. "
                    "Đăng bài vẫn cần một lần duyệt video riêng."
                ),
                "",
                bold("Chưa khả dụng"),
                escape("• Theo dõi sản xuất video và duyệt video"),
                escape("• Tự động đăng Facebook / TikTok"),
                escape("• Thống kê mạng xã hội và báo cáo tự động"),
                escape("• Xin nghỉ, báo đi muộn, báo cáo nhân sự"),
                escape("• Xoá file trên Google Drive (TasksBot không có công cụ xoá)"),
            ]
        )
    return "\n".join(lines).strip()


def handler_commands(routers: Iterable[object]) -> set[str]:
    """Command names actually registered on ``routers``.

    Walks aiogram's message observers and reads the ``Command`` filters back
    out, so a test can compare the live dispatcher against :data:`COMMANDS`
    instead of against a hand-written list that would drift the same way the
    old help text did.
    """
    from aiogram.filters import Command

    found: set[str] = set()
    for router in routers:
        observer = getattr(router, "message", None)
        if observer is None:
            continue
        for handler in observer.handlers:
            for filter_object in handler.filters or ():
                callback = getattr(filter_object, "callback", filter_object)
                if isinstance(callback, Command):
                    for command in callback.commands:
                        found.add(str(getattr(command, "pattern", command)))
    return found
