"""Schemas for the user-lifecycle, quota and group-policy endpoints.

Every response carries the authoritative enum *and* its display label side by
side (``role``/``role_label``, ``status``/``status_label``). A caller that wants
to key off something stable uses the enum; a caller rendering a screen uses the
label. Neither has to guess, and the label never becomes the identifier.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.user_service import status_label
from meobot.db.models.access import GroupMemberResponsePolicy, PendingGuestAccessRequest
from meobot.db.models.user import User
from meobot.domain.access.models import GroupPolicyMode, UserStatus
from meobot.domain.access.quota import MAX_DAILY_LIMIT
from meobot.domain.identity.labels import RoleInput, role_label
from meobot.domain.identity.models import Role


class UserResponse(BaseModel):
    """One registered user, as the API exposes them."""

    id: uuid.UUID
    telegram_user_id: int | None
    telegram_username: str | None
    full_name: str
    role: str
    role_label: str
    status: str
    status_label: str
    active: bool
    status_reason: str | None
    suspended_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, model: User) -> UserResponse:
        return cls(
            id=model.id,
            telegram_user_id=model.telegram_user_id,
            telegram_username=model.telegram_username,
            full_name=model.full_name,
            role=model.role.value,
            role_label=role_label(model.role),
            status=model.status.value,
            status_label=status_label(model.status),
            active=model.active,
            status_reason=model.status_reason,
            suspended_at=model.suspended_at,
            revoked_at=model.revoked_at,
            created_at=model.created_at,
        )


class UserListResponse(BaseModel):
    """Envelope for user listings."""

    items: list[UserResponse]
    total: int


class AddUserRequest(BaseModel):
    """Body for ``POST /api/v1/users``."""

    model_config = ConfigDict(extra="forbid")

    telegram_user_id: int = Field(gt=0)
    #: Accepts every alias ("MEMBER", "trưởng nhóm", ...); normalised to the
    #: authoritative enum before the service or the policy engine sees it.
    role: RoleInput = Role.EMPLOYEE
    full_name: str = Field(default="", max_length=200)
    telegram_username: str | None = Field(default=None, max_length=100)


class StatusChangeRequest(BaseModel):
    """Body for suspend and revoke."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class RoleChangeRequest(BaseModel):
    """Body for ``PATCH /api/v1/users/{id}/role``."""

    model_config = ConfigDict(extra="forbid")

    role: RoleInput


class QuotaResponse(BaseModel):
    """One member's chat allowance for the current local day."""

    user_id: uuid.UUID
    quota_date: date
    limit: int
    used: int
    reserved: int
    remaining: int
    unlimited: bool


class QuotaOverrideRequest(BaseModel):
    """Body for ``PUT /api/v1/users/{id}/quota``."""

    model_config = ConfigDict(extra="forbid")

    #: ``None`` clears a standing override and returns the member to the
    #: default. That is why this is a ``PUT`` on the override, not a ``DELETE``
    #: on the user.
    daily_limit: int | None = Field(default=None, ge=0, le=MAX_DAILY_LIMIT)
    bonus_today: int | None = Field(default=None, ge=0, le=MAX_DAILY_LIMIT)
    reset_today: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class GroupPolicyResponse(BaseModel):
    """One person's response policy in one chat."""

    id: uuid.UUID
    telegram_chat_id: int
    telegram_user_id: int
    mode: str
    effective_mode: str
    muted_until: datetime | None
    guest_granted_at: datetime | None
    guest_expires_at: datetime | None
    guest_question_limit: int
    guest_questions_used: int
    guest_reserved_count: int
    revoked_at: datetime | None

    @classmethod
    def from_model(
        cls, model: GroupMemberResponsePolicy, *, effective: GroupPolicyMode
    ) -> GroupPolicyResponse:
        return cls(
            id=model.id,
            telegram_chat_id=model.telegram_chat_id,
            telegram_user_id=model.telegram_user_id,
            mode=model.mode.value,
            effective_mode=effective.value,
            muted_until=model.muted_until,
            guest_granted_at=model.guest_granted_at,
            guest_expires_at=model.guest_expires_at,
            guest_question_limit=model.guest_question_limit,
            guest_questions_used=model.guest_questions_used,
            guest_reserved_count=model.guest_reserved_count,
            revoked_at=model.revoked_at,
        )


class GroupPolicyListResponse(BaseModel):
    """Envelope for policy listings."""

    items: list[GroupPolicyResponse]
    total: int


class SetGroupPolicyRequest(BaseModel):
    """Body for ``PUT /api/v1/group-policies/{chat_id}/{telegram_user_id}``."""

    model_config = ConfigDict(extra="forbid")

    bot_id: int = 0
    mode: GroupPolicyMode
    muted_until: datetime | None = None
    guest_question_limit: int | None = Field(default=None, ge=1, le=100)
    guest_duration_hours: int | None = Field(default=None, ge=1, le=720)
    reason: str | None = Field(default=None, max_length=1000)


class PendingAccessResponse(BaseModel):
    """One stranger waiting for a decision."""

    id: uuid.UUID
    telegram_chat_id: int
    chat_title: str | None
    requester_telegram_id: int
    requester_username: str | None
    requester_display_name: str | None
    question_preview: str
    status: str
    mention_count: int
    expires_at: datetime
    created_at: datetime

    @classmethod
    def from_model(cls, model: PendingGuestAccessRequest) -> PendingAccessResponse:
        return cls(
            id=model.id,
            telegram_chat_id=model.telegram_chat_id,
            chat_title=model.chat_title,
            requester_telegram_id=model.requester_telegram_id,
            requester_username=model.requester_username,
            requester_display_name=model.requester_display_name,
            question_preview=model.question_preview,
            status=model.status.value,
            mention_count=model.mention_count,
            expires_at=model.expires_at,
            created_at=model.created_at,
        )


class PendingAccessListResponse(BaseModel):
    """Envelope for pending-request listings."""

    items: list[PendingAccessResponse]
    total: int


__all__ = [
    "AddUserRequest",
    "GroupPolicyListResponse",
    "GroupPolicyResponse",
    "PendingAccessListResponse",
    "PendingAccessResponse",
    "QuotaOverrideRequest",
    "QuotaResponse",
    "RoleChangeRequest",
    "SetGroupPolicyRequest",
    "StatusChangeRequest",
    "UserListResponse",
    "UserResponse",
    "UserStatus",
]
