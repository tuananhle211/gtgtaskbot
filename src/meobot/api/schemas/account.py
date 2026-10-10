"""Wire shapes for password login and the account screen (``/api/account``).

No ``data`` envelope, like ``/api/orders``. Labels come from the server.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meobot.application.account.account_service import MemberView
from meobot.application.account.stats_service import MemberStats
from meobot.db.models.user import User
from meobot.domain.identity.labels import RoleInput, role_label
from meobot.domain.units.labels import (
    function_tag,
    unit_label,
    unit_role_label,
    unit_short_label,
)
from meobot.domain.units.models import UnitCode, UnitMemberRole, UnitMembership

#: Password fields carry **no** schema constraint on purpose: FastAPI's 422 for
#: a failed constraint echoes the offending ``input`` back in the body, and a
#: password should not travel back over the wire. Length and strength are the
#: service's rules (401 for a login, 422 with a ``reason`` for a change).


class PasswordLoginRequest(BaseModel):
    """``username`` is the Telegram numeric id; surrounding spaces are ignored."""

    username: str = Field(min_length=1, max_length=40)
    password: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return "PasswordLoginRequest(username=..., password=***)"


class PasswordLoginResponse(BaseModel):
    must_change_password: bool


class PasswordResetRequest(BaseModel):
    """ "Quên mật khẩu?": the Telegram numeric id, nothing else."""

    username: str = Field(min_length=1, max_length=40)


class PasswordResetResponse(BaseModel):
    """The same sentence for every request - see ``PasswordResetService``."""

    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return "ChangePasswordRequest(***)"


class UpdateProfileRequest(BaseModel):
    full_name: str = Field(max_length=200)


class ChangeRoleRequest(BaseModel):
    """The member's system role. "ADMIN" (Quản trị viên) sees both streams."""

    role: RoleInput


class AvatarUploadRequest(BaseModel):
    """A cropped, resized picture (0047): base64 without a ``data:`` prefix.

    No schema constraints on purpose: a failed constraint's 422 echoes the
    ``input`` back, which here would be up to 400 KB of base64. Type, length
    and content are checked by ``AvatarService`` (422 ``avatar_too_large`` /
    ``avatar_invalid_image``).
    """

    content_type: str
    data: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"AvatarUploadRequest(content_type={self.content_type!r}, data=<{len(self.data)}>)"


class AvatarResponse(BaseModel):
    avatar_url: str


class MemberStatsResponse(BaseModel):
    month: str
    points: float
    nodes_done: int
    nodes_in_progress: int
    revisions: int
    orders_created: int
    orders_completed: int
    pr_contents_owned: int
    pr_productions_done: int
    pr_approvals: int
    work_items_counted: int
    #: 0053: deadline-based now ("Đúng hạn"); the old meaning is first_pass_rate.
    on_time_rate: float | None
    first_pass_rate: float | None = None
    late_count: int = 0
    tokens_used: float = 0.0
    tokens_budget: float = 0.0
    effort_rate: float | None = None
    performance_score: float | None = None
    output_target: int | None = None

    @classmethod
    def from_domain(cls, stats: MemberStats) -> MemberStatsResponse:
        return cls(
            month=stats.month,
            points=stats.points,
            nodes_done=stats.nodes_done,
            nodes_in_progress=stats.nodes_in_progress,
            revisions=stats.revisions,
            orders_created=stats.orders_created,
            orders_completed=stats.orders_completed,
            pr_contents_owned=stats.pr_contents_owned,
            pr_productions_done=stats.pr_productions_done,
            pr_approvals=stats.pr_approvals,
            work_items_counted=stats.work_items_counted,
            on_time_rate=stats.on_time_rate,
            first_pass_rate=stats.first_pass_rate,
            late_count=stats.late_count,
            tokens_used=stats.tokens_used,
            tokens_budget=stats.tokens_budget,
            effort_rate=stats.effort_rate,
            performance_score=stats.performance_score,
            output_target=stats.output_target,
        )


class AccountUnitResponse(BaseModel):
    code: str
    label: str
    #: Additive: the chip tag, "PR" / "ORD".
    short_label: str = ""
    #: Additive: the role in the stream (``UnitMemberRole``) and its lead flag.
    role: str = ""
    role_label: str
    is_lead: bool = False
    #: Additive: ORD function roles only, "BT" / "TK" / "D"; null otherwise.
    function_tag: str | None = None
    member_code: str | None

    @classmethod
    def build(
        cls,
        code: UnitCode,
        role: UnitMemberRole,
        *,
        is_lead: bool,
        member_code: str | None,
    ) -> AccountUnitResponse:
        return cls(
            code=code.value,
            label=unit_label(code),
            short_label=unit_short_label(code),
            role=role.value,
            role_label=unit_role_label(role, is_lead),
            is_lead=is_lead,
            function_tag=function_tag(code, role),
            member_code=member_code,
        )


class AccountMeResponse(BaseModel):
    user_id: uuid.UUID
    telegram_user_id: int | None
    telegram_username: str | None
    full_name: str
    role: str
    role_label: str
    units: list[AccountUnitResponse]
    must_change_password: bool
    #: Additive: whether the account has chosen its own password at all. A
    #: Telegram-link session on the default password is not forced to change
    #: it, so ``must_change_password`` alone cannot say this.
    has_custom_password: bool
    #: Additive (0046): the account is on a temporary password MeoBot sent by
    #: Telegram (a reset). Not "custom" - nobody chose it.
    password_temporary: bool = False
    password_changed_at: datetime | None
    stats: MemberStatsResponse
    #: Additive (0047): ``/api/account/avatar/<id>?v=<n>``, null without a picture.
    avatar_url: str | None = None

    @classmethod
    def build(
        cls,
        *,
        user: User,
        membership: UnitMembership,
        must_change_password: bool,
        stats: MemberStats,
        avatar_url: str | None = None,
    ) -> AccountMeResponse:
        return cls(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            telegram_username=user.telegram_username,
            full_name=user.full_name,
            role=user.role.value,
            role_label=role_label(user.role),
            units=[
                AccountUnitResponse.build(
                    entry.unit_code,
                    entry.role,
                    is_lead=entry.is_lead,
                    member_code=entry.member_code,
                )
                for entry in membership.entries
            ],
            must_change_password=must_change_password,
            has_custom_password=user.password_hash is not None and not user.password_temporary,
            password_temporary=bool(user.password_temporary),
            password_changed_at=user.password_changed_at,
            stats=MemberStatsResponse.from_domain(stats),
            avatar_url=avatar_url,
        )


class MemberRowResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    telegram_user_id: int | None
    units: list[str]
    #: Additive: the base role code (``OWNER`` / ``ADMIN`` / ``TEAM_LEAD`` / ``EMPLOYEE``).
    role: str = ""
    role_label: str
    last_login_at: datetime | None
    has_custom_password: bool
    #: Additive (0046): on a temporary password sent by a reset.
    password_temporary: bool = False
    locked: bool
    stats: MemberStatsResponse
    #: Additive (0047): null without a picture.
    avatar_url: str | None = None
    #: Additive: false for a deactivated account (listed with ``include_inactive``).
    active: bool = True
    #: Additive: each open tag in full (``units`` keeps the bare codes).
    unit_tags: list[AccountUnitResponse] = []
    #: Additive: the ORD department tag ("BT" / "TK" / "D") and whether the
    #: person leads it; null / false outside an ORD function.
    function_tag: str | None = None
    is_lead: bool = False

    @classmethod
    def from_view(cls, view: MemberView) -> MemberRowResponse:
        tags = [
            AccountUnitResponse.build(
                unit.code, unit.role, is_lead=unit.is_lead, member_code=unit.member_code
            )
            for unit in view.units
        ]
        function = next((tag for tag in tags if tag.function_tag is not None), None)
        return cls(
            role=view.user.role.value,
            active=bool(view.user.active),
            unit_tags=tags,
            function_tag=None if function is None else function.function_tag,
            is_lead=False if function is None else function.is_lead,
            user_id=view.user.id,
            full_name=view.user.full_name,
            telegram_user_id=view.user.telegram_user_id,
            units=[unit.code.value for unit in view.units],
            role_label=view.role_label,
            last_login_at=view.last_login_at,
            has_custom_password=view.has_custom_password,
            password_temporary=view.password_temporary,
            locked=view.locked,
            stats=MemberStatsResponse.from_domain(view.stats),
            avatar_url=view.avatar_url,
        )


class MemberListResponse(BaseModel):
    month: str
    members: list[MemberRowResponse]


__all__ = [
    "AccountMeResponse",
    "AccountUnitResponse",
    "AvatarResponse",
    "AvatarUploadRequest",
    "ChangePasswordRequest",
    "MemberListResponse",
    "MemberRowResponse",
    "MemberStatsResponse",
    "PasswordLoginRequest",
    "PasswordLoginResponse",
    "PasswordResetRequest",
    "PasswordResetResponse",
    "UpdateProfileRequest",
]
