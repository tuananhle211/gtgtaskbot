"""MeoBot's own identity, as data.

Before this existed, "who MeoBot is" was a paragraph pasted into a system
prompt, and a second, subtly different paragraph pasted into the offline
provider. Changing the assistant's job description meant finding every copy.

:class:`AssistantProfile` is the single source of truth. Prompts are *built*
from it by
:class:`~meobot.application.assistant_context_service.AssistantContextService`;
no prompt hardcodes the organisation, the department or the mission.

Storage: the profile lives in ``system_settings`` under
:data:`ASSISTANT_PROFILE_SETTING_KEY` when an operator has customised it, and
otherwise comes from configuration via :func:`default_assistant_profile`.
There is no second table - the settings row *is* the override.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.core.time import utcnow

#: ``system_settings.key`` holding the operator's overrides, if any.
ASSISTANT_PROFILE_SETTING_KEY = "assistant.profile"

#: The domains MeoBot is expected to be useful in. Job description, not
#: private data - it shapes what the assistant volunteers, so it belongs with
#: the identity rather than in an environment variable.
DEFAULT_OPERATING_DOMAINS: tuple[str, ...] = (
    "chiến lược thương hiệu",
    "truyền thông",
    "content",
    "short-form video",
    "kịch bản",
    "sản xuất nội dung",
    "seeding",
    "PR và sự kiện",
    "quản lý đội nhóm",
    "Google Sheets workflow",
)

DEFAULT_CORE_RESPONSIBILITIES: tuple[str, ...] = (
    "hỗ trợ điều hành công việc hằng ngày",
    "quản lý hệ thống nội dung và kịch bản trên Google Sheets",
    "phản biện và góp ý ý tưởng, kịch bản, hook",
    "brainstorm hướng nội dung theo kênh và tệp khán giả",
    "tóm tắt bối cảnh và nhắc lại quyết định đã chốt",
    "thực hiện thao tác vận hành qua công cụ có kiểm duyệt quyền hạn",
)

DEFAULT_TONE: tuple[str, ...] = (
    "tự nhiên",
    "thông minh",
    "trực tiếp",
    "thực tế",
    "có chính kiến",
    "không máy móc",
    "không tâng bốc quá mức",
    "ngắn gọn mặc định",
    "phân tích sâu khi được yêu cầu",
)

DEFAULT_RESPONSE_PREFERENCES: tuple[str, ...] = (
    "mặc định 2-6 câu, có hành động tiếp theo rõ ràng",
    "chỉ hỏi lại một câu, và chỉ khi câu đó giúp công việc tiến lên",
    "không lặp lại thông tin đã có trong bối cảnh",
    "tiếng Việt tự nhiên, không chèn tiếng Anh nếu người dùng không dùng trước",
)

DEFAULT_IDENTITY = "Trợ lý điều hành và sáng tạo nội dung dành cho Trưởng phòng PR Truyền thông."

DEFAULT_MISSION = (
    "Giúp người dùng điều hành công việc, quản lý nội dung, đưa ra quyết định, "
    "brainstorm, review kịch bản, làm việc với Google Sheets và giảm khối lượng "
    "công việc lặp lại."
)


class AssistantProfile(BaseModel):
    """Structured description of the assistant itself.

    Every field is safe to render into a prompt and safe to show a user via
    ``/assistant_profile``: there is nothing secret here by construction.
    """

    model_config = ConfigDict(frozen=True)

    assistant_name: str = Field(default="TasksBot", max_length=80)
    identity: str = Field(default=DEFAULT_IDENTITY, max_length=500)
    mission: str = Field(default=DEFAULT_MISSION, max_length=1000)
    organization_name: str = Field(default="", max_length=200)
    department_name: str = Field(default="", max_length=200)
    department_size: str = Field(default="", max_length=100)
    operating_domains: tuple[str, ...] = DEFAULT_OPERATING_DOMAINS
    core_responsibilities: tuple[str, ...] = DEFAULT_CORE_RESPONSIBILITIES
    tone: tuple[str, ...] = DEFAULT_TONE
    response_preferences: tuple[str, ...] = DEFAULT_RESPONSE_PREFERENCES
    capability_summary: str = Field(
        default="",
        max_length=2000,
        description="Filled from the live capability report, never written by hand.",
    )
    limitation_summary: str = Field(
        default="",
        max_length=2000,
        description="Filled from configuration, never written by hand.",
    )
    updated_at: datetime = Field(default_factory=utcnow)

    def workspace_line(self) -> str:
        """One line naming the organisation and department, if configured."""
        parts = [part for part in (self.organization_name, self.department_name) if part]
        if not parts:
            return ""
        line = " · ".join(parts)
        return f"{line} ({self.department_size})" if self.department_size else line

    def render_identity_block(self) -> str:
        """The ``[ASSISTANT IDENTITY]`` section body.

        Ends with the non-impersonation rule. It is repeated here even though
        the system prompt also carries it, because this block sits immediately
        above ``[CURRENT USER]`` - the block MeoBot was reading its identity out
        of - and adjacency is what the model actually attends to.
        """
        from meobot.domain.assistant.identity import (
            NEVER_CLAIM_BUSINESS_RESULT_RULE,
            NEVER_IMPERSONATE_RULE,
        )

        lines = [
            f"Tên: {self.assistant_name}",
            f"Vai trò: {self.identity}",
            f"Nhiệm vụ: {self.mission}",
        ]
        if self.core_responsibilities:
            lines.append("Việc chính: " + ", ".join(self.core_responsibilities))
        if self.tone:
            lines.append("Giọng điệu: " + ", ".join(self.tone))
        lines.append(NEVER_IMPERSONATE_RULE.strip())
        lines.append(NEVER_CLAIM_BUSINESS_RESULT_RULE.strip())
        return "\n".join(lines)

    def render_workspace_block(self) -> str:
        """The ``[WORKSPACE]`` section body (integrations added by the caller)."""
        lines: list[str] = []
        if self.organization_name:
            lines.append(f"Tổ chức: {self.organization_name}")
        if self.department_name:
            lines.append(f"Phòng ban: {self.department_name}")
        if self.department_size:
            lines.append(f"Quy mô phòng: {self.department_size}")
        if self.operating_domains:
            lines.append("Lĩnh vực: " + ", ".join(self.operating_domains))
        return "\n".join(lines)

    def public_dict(self) -> dict[str, object]:
        """Serialisable view stored in ``system_settings``."""
        return {
            "assistant_name": self.assistant_name,
            "identity": self.identity,
            "mission": self.mission,
            "organization_name": self.organization_name,
            "department_name": self.department_name,
            "department_size": self.department_size,
            "operating_domains": list(self.operating_domains),
            "core_responsibilities": list(self.core_responsibilities),
            "tone": list(self.tone),
            "response_preferences": list(self.response_preferences),
        }


def default_assistant_profile(
    *,
    organization_name: str = "",
    department_name: str = "",
    department_size: str = "",
    assistant_name: str = "TasksBot",
) -> AssistantProfile:
    """Build the profile MeoBot ships with, filled in from configuration.

    The organisation and department are configuration
    (``MEOBOT_ORGANIZATION_NAME``, ``MEOBOT_DEPARTMENT_NAME``) rather than
    constants, so a deployment for a different team needs no code change and no
    private detail is committed.
    """
    return AssistantProfile(
        assistant_name=assistant_name,
        organization_name=organization_name,
        department_name=department_name,
        department_size=department_size,
    )
