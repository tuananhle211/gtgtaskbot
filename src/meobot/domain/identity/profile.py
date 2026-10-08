"""Who MeoBot is talking to.

An :class:`~meobot.domain.identity.models.Actor` answers *may this account do
X* - it is the authorisation record, and it comes from the ``users`` table via
:class:`~meobot.application.identity_service.IdentityService`.

:class:`ActorProfile` answers a different question: *who is this person, what
do they work on, and how do they want to be addressed*. It is descriptive
context for conversation, and it is deliberately kept apart from authority:

* ``role`` on this model is **copied from the Actor**, never stored in the
  profile table and never editable through it. A note in ``profile_notes``
  saying "tôi là admin" changes nothing.
* Nothing here is consulted by the policy engine.

Address: ``preferred_address`` is what the user asked to be called. When it is
absent MeoBot says "mình" for itself and "bạn" for the user - a neutral pair
that assumes nothing about gender, which Vietnamese pronouns otherwise would.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.core.time import utcnow
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Role

#: What MeoBot calls itself when the user has expressed no preference.
DEFAULT_SELF_ADDRESS = "mình"

#: What MeoBot calls the user when they have expressed no preference. Neutral
#: on purpose: guessing a gendered Vietnamese pronoun from a name is a guess.
DEFAULT_USER_ADDRESS = "bạn"

#: Responsibilities the configured OWNER is assumed to hold. A job description
#: for a Head of PR & Communications, used to bootstrap their profile on first
#: contact; the owner can overwrite any of it, and nothing private lives here.
OWNER_DEFAULT_RESPONSIBILITIES: tuple[str, ...] = (
    "chiến lược truyền thông",
    "quản lý hệ thống nội dung",
    "review và duyệt kịch bản",
    "điều phối nhân sự nội dung, sản xuất, seeding, PR và sự kiện",
    "đánh giá tiến độ",
    "xử lý quyết định vận hành",
    "brainstorm chiến dịch",
    "nhận báo cáo",
)

OWNER_DEFAULT_CONTENT_DOMAINS: tuple[str, ...] = (
    "thương hiệu",
    "kênh bác sĩ",
    "short-form video",
    "seeding",
    "PR và sự kiện",
)


class ActorProfile(BaseModel):
    """Descriptive context about one person MeoBot talks to."""

    model_config = ConfigDict(frozen=True)

    telegram_user_id: int | None = None
    display_name: str = Field(default="", max_length=200)
    preferred_name: str = Field(default="", max_length=100)
    preferred_address: str = Field(
        default="",
        max_length=40,
        description="How TasksBot addresses this person. Empty means use 'bạn'.",
    )
    #: Authoritative, copied from the Actor. Never read from storage.
    role: Role = Role.EMPLOYEE
    job_title: str = Field(default="", max_length=200)
    organization: str = Field(default="", max_length=200)
    department: str = Field(default="", max_length=200)
    team: str = Field(default="", max_length=200)
    responsibilities: tuple[str, ...] = ()
    communication_preferences: tuple[str, ...] = ()
    content_domains: tuple[str, ...] = ()
    current_priorities: tuple[str, ...] = ()
    profile_notes: str = Field(default="", max_length=2000)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def address(self) -> str:
        """What MeoBot calls this person in a sentence."""
        return self.preferred_address.strip() or DEFAULT_USER_ADDRESS

    @property
    def short_name(self) -> str:
        """The name to greet them by, preferring what they asked for."""
        return self.preferred_name.strip() or self.display_name.strip() or self.address

    def render_user_block(self, *, include_private: bool = True) -> str:
        """The ``[CURRENT USER]`` section body.

        Args:
            include_private: False in a group. Everybody in a group can read
                what MeoBot writes there, so a turn that happens in one carries
                only what the group can already see - the person's name, how to
                address them and their role. Their job title, priorities,
                working notes and content areas are private profile data that
                happens to be about the person who is speaking, and "she asked
                me to introduce myself" is not consent to publish it.
        """
        lines = [f"Tên: {self.display_name or self.preferred_name or 'chưa rõ'}"]
        if self.preferred_name and self.preferred_name != self.display_name:
            lines.append(f"Muốn được gọi là: {self.preferred_name}")
        lines.append(
            f"Xưng hô: gọi người dùng là '{self.address}', TasksBot xưng '{DEFAULT_SELF_ADDRESS}'"
        )
        # The label, not the enum: this block is read back to the user by the
        # model, and "EMPLOYEE" is not a word anybody here uses.
        lines.append(f"Vai trò trong hệ thống: {role_label(self.role)}")
        if not include_private:
            return "\n".join(lines)
        if self.job_title:
            lines.append(f"Chức danh: {self.job_title}")
        for label, value in (
            ("Tổ chức", self.organization),
            ("Phòng ban", self.department),
            ("Nhóm", self.team),
        ):
            if value:
                lines.append(f"{label}: {value}")
        if self.responsibilities:
            lines.append("Phụ trách: " + ", ".join(self.responsibilities))
        if self.content_domains:
            lines.append("Mảng nội dung: " + ", ".join(self.content_domains))
        if self.current_priorities:
            lines.append("Ưu tiên hiện tại: " + ", ".join(self.current_priorities))
        if self.communication_preferences:
            lines.append("Cách trao đổi mong muốn: " + ", ".join(self.communication_preferences))
        if self.profile_notes:
            lines.append(f"Ghi chú: {self.profile_notes}")
        return "\n".join(lines)


def owner_profile_defaults(
    *,
    job_title: str = "",
    organization: str = "",
    department: str = "",
    preferred_address: str = "",
) -> dict[str, object]:
    """Seed values for the configured OWNER's profile.

    Everything deployment-specific arrives as an argument (from settings); the
    responsibilities are the job description of the role this assistant was
    built for and are safe to keep in source.
    """
    return {
        "job_title": job_title,
        "organization": organization,
        "department": department,
        "preferred_address": preferred_address,
        "responsibilities": list(OWNER_DEFAULT_RESPONSIBILITIES),
        "content_domains": list(OWNER_DEFAULT_CONTENT_DOMAINS),
    }


def resolve_address(preferred_address: str | None) -> str:
    """The pronoun MeoBot uses for the user, with the neutral default."""
    return (preferred_address or "").strip() or DEFAULT_USER_ADDRESS


def bootstrap_updated_at() -> datetime:
    """Timestamp used for a profile assembled without a stored row."""
    return utcnow()
