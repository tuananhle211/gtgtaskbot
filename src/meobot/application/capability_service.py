"""What MeoBot can actually do, right now, for one specific actor.

Assembled from live facts rather than from a written list, because a written
list is exactly what drifted: the old ``/help`` promised natural conversation
that did not exist and stayed silent about Drive, which by then did.

Four buckets, and the distinction between them is the whole point:

``available_now``
    A registered tool the actor's role permits, whose integration is
    configured. Saying this is a promise MeoBot can keep.

``needs_configuration``
    Implemented, permitted, but missing a credential or an id. The user should
    be told *what* to configure, not that the feature does not exist.

``not_permitted``
    Implemented and configured, but this actor's role does not allow it.

``not_implemented``
    Honest list of things people keep asking for. Kept short and specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from meobot.bot.commands import COMMANDS, CommandSpec
from meobot.core.config import Settings
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.tools.base import ToolDefinition, ToolRegistry

#: Things MeoBot genuinely does not do. Listed so a chat answer can decline
#: specifically instead of vaguely.
#:
#: Kept honest in both directions. "Xin nghỉ, báo đi muộn" was in this list
#: until 0.6.0a2 while the HR module had already shipped in 0.6.0A, so MeoBot
#: was telling people a feature they were using did not exist. Removing an
#: entry from here is part of shipping the thing it describes.
NOT_IMPLEMENTED: tuple[str, ...] = (
    "Giao việc và nhận việc",
    "Việc seeding và nộp bằng chứng",
    "Quản lý kênh và lịch đăng bài",
    "Báo cáo công việc hằng ngày",
    "Theo dõi sản xuất video và duyệt video",
    "Tự động đăng bài Facebook hoặc TikTok",
    "Thống kê mạng xã hội và báo cáo tự động",
    "Lịch nhắc lặp theo tháng (hiện có một lần, hằng ngày và hằng tuần)",
    "Xoá hoặc di chuyển file trên Google Drive (MeoBot không có công cụ xoá)",
)

#: Things that work for every registered user, whatever their role, and are not
#: backed by a tool in the registry. Listed here so the private-chat answer and
#: the group answer come from one place - a hardcoded list in each would be two
#: lists, and two lists drift.
ALWAYS_AVAILABLE: tuple[str, ...] = (
    "Đặt lịch nhắc một lần, hằng ngày và hằng tuần",
    "Xin nghỉ phép và báo đi muộn",
    "Xem tình trạng đơn nghỉ và đơn đi muộn của mình",
)

#: Available only to roles that may broadcast. Kept beside the list above so
#: the difference between what a Member sees and what an owner sees is always
#: a permission, never a different hardcoded paragraph.
BROADCAST_CAPABILITIES: tuple[str, ...] = (
    "Đăng ký group nhận thông báo (nhắn trong chính group đó)",
    "Gửi thông báo tới group đã đăng ký",
    "Xem ai đã xác nhận đã đọc thông báo",
    "Duyệt đơn nghỉ và đơn đi muộn",
)

#: Tool-name prefix -> the configuration it depends on. A tool whose
#: requirement is unmet is reported as "needs configuration", not as missing.
_REQUIREMENTS: tuple[tuple[str, str, str], ...] = (
    ("sheet_profile.", "google", "GOOGLE_SERVICE_ACCOUNT_FILE"),
    ("drive.", "google", "GOOGLE_SERVICE_ACCOUNT_FILE"),
    ("spreadsheet.", "google", "GOOGLE_SERVICE_ACCOUNT_FILE"),
)


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """A role-aware snapshot of what MeoBot can do."""

    available_now: list[str] = field(default_factory=list)
    needs_configuration: list[str] = field(default_factory=list)
    not_permitted: list[str] = field(default_factory=list)
    not_implemented: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "available_now": list(self.available_now),
            "needs_configuration": list(self.needs_configuration),
            "not_permitted": list(self.not_permitted),
            "not_implemented": list(self.not_implemented),
        }


class CapabilityService:
    """Builds :class:`CapabilityReport` from the registry and configuration.

    Args:
        registry: The live tool registry - the only authority on what exists.
        settings: Configuration, which decides what is usable.
    """

    def __init__(self, registry: ToolRegistry, settings: Settings) -> None:
        self._registry = registry
        self._settings = settings

    def report_for(self, actor: Actor) -> CapabilityReport:
        """Describe MeoBot's capabilities from ``actor``'s point of view."""
        available: list[str] = []
        needs_config: list[str] = []
        not_permitted: list[str] = []

        for tool in self._registry.policies().values():
            if not isinstance(tool, ToolDefinition):  # pragma: no cover - defensive
                continue
            label = f"{tool.description} ({tool.name})"
            if not self._permitted(actor, tool):
                not_permitted.append(label)
                continue
            missing = self._missing_configuration(tool.name)
            if missing is not None:
                needs_config.append(f"{tool.description} — cần {missing}")
                continue
            available.append(tool.description)

        # One registry, two contexts. A private chat and a group ask the same
        # service, so MeoBot cannot tell somebody a feature is unavailable in
        # one and available in the other unless a permission actually differs.
        if self._settings.reminder_enabled:
            available.extend(ALWAYS_AVAILABLE)
        else:
            needs_config.append("Đặt lịch nhắc — cần bật REMINDER_ENABLED")
            available.extend(ALWAYS_AVAILABLE[1:])

        if has_permission(actor.role, Permission.ANNOUNCEMENT_BROADCAST):
            available.extend(BROADCAST_CAPABILITIES)
        else:
            not_permitted.extend(BROADCAST_CAPABILITIES)

        if not self._settings.chat_enabled:
            needs_config.append("Trò chuyện tự nhiên — cần bật CHAT_ENABLED")
        elif self._settings.llm_is_fake:
            needs_config.append(
                "Trò chuyện tự nhiên đầy đủ — hiện chạy ngoại tuyến, cần "
                "LLM_PROVIDER=openai kèm LLM_API_KEY và LLM_MODEL"
            )
        else:
            available.append("Trò chuyện, brainstorm và góp ý nội dung")

        if self._settings.google_enabled and not self._settings.google_script_sheet_template_id:
            needs_config.append(
                "Sao chép Sheet kịch bản từ mẫu có sẵn định dạng — cần "
                "GOOGLE_SCRIPT_SHEET_TEMPLATE_ID (hiện tạo Sheet trắng chuẩn)"
            )
        if self._settings.google_enabled and not self._settings.google_work_sheet_template_id:
            needs_config.append(
                "Sao chép Sheet công việc từ mẫu có sẵn định dạng — cần "
                "GOOGLE_WORK_SHEET_TEMPLATE_ID (hiện tạo Sheet trắng chuẩn)"
            )

        return CapabilityReport(
            available_now=sorted(set(available)),
            needs_configuration=sorted(set(needs_config)),
            not_permitted=sorted(set(not_permitted)),
            not_implemented=list(NOT_IMPLEMENTED),
        )

    def commands_for(self, actor: Actor) -> list[CommandSpec]:
        """Slash commands this actor may use."""
        return [spec for spec in COMMANDS if spec.visible_to(actor.role)]

    def brief_for(self, actor: Actor) -> str:
        """Compact capability text embedded in the provider's prompt.

        ``not_permitted`` is deliberately omitted: telling the model about
        capabilities this actor cannot use invites it to offer them.
        """
        from meobot.integrations.llm.prompts import capability_brief

        report = self.report_for(actor)
        return capability_brief(
            available_now=report.available_now,
            needs_configuration=report.needs_configuration,
            not_implemented=report.not_implemented,
        )

    def _permitted(self, actor: Actor, tool: ToolDefinition) -> bool:
        """Same two checks the policy engine makes, for display purposes only."""
        if tool.min_role is not None and actor.role.rank < tool.min_role.rank:
            return False
        return tool.required_permission is None or has_permission(
            actor.role, tool.required_permission
        )

    def _missing_configuration(self, tool_name: str) -> str | None:
        """The configuration key a tool needs but does not have."""
        for prefix, requirement, key in _REQUIREMENTS:
            if not tool_name.startswith(prefix):
                continue
            if requirement == "google" and not self._settings.google_enabled:
                return key
        return None
