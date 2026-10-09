"""Invite schemas.

The plaintext code appears in exactly one place: the response to the request
that created it. It is never readable afterwards, because only its hash is
stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.invite_service import DEFAULT_EXPIRY_DAYS, MAX_EXPIRY_DAYS, MAX_USES
from meobot.db.models.invite import InviteCode
from meobot.domain.identity.labels import RoleInput, role_label
from meobot.domain.identity.models import Role


class InviteResponse(BaseModel):
    """An invite as exposed by the API. Never contains the code itself."""

    id: uuid.UUID
    #: The authoritative enum value, unchanged. ``role_label`` carries the
    #: Vietnamese wording for anything that renders this to a person.
    role: str
    role_label: str
    scope: str | None
    note: str | None
    expires_at: datetime | None
    max_uses: int
    use_count: int
    active: bool
    created_at: datetime

    @classmethod
    def from_model(cls, model: InviteCode) -> InviteResponse:
        return cls(
            id=model.id,
            role=model.role.value,
            role_label=role_label(model.role),
            scope=model.scope,
            note=model.note,
            expires_at=model.expires_at,
            max_uses=model.max_uses,
            use_count=model.use_count,
            active=model.active,
            created_at=model.created_at,
        )


class CreatedInviteResponse(InviteResponse):
    """The creation response, which is the only place the code is readable."""

    code: str = Field(description="Shown once. Store it now or create a new invite.")
    #: The bot's Telegram username for a ``t.me/<bot>?start=<code>`` link, when
    #: the deployment knows it; null otherwise (``/api/invites`` only).
    bot_username: str | None = None

    @classmethod
    def from_created(cls, model: InviteCode, code: str) -> CreatedInviteResponse:
        base = InviteResponse.from_model(model)
        return cls(**base.model_dump(), code=code)


class CreateInviteRequest(BaseModel):
    """Body for ``POST /api/v1/invites``."""

    model_config = ConfigDict(extra="forbid")

    #: Accepts every alias ("MEMBER", "trưởng nhóm", ...); normalised to the
    #: authoritative enum before the service or the policy engine sees it.
    role: RoleInput = Role.EMPLOYEE
    scope: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    expires_in_days: int = Field(default=DEFAULT_EXPIRY_DAYS, ge=1, le=MAX_EXPIRY_DAYS)
    max_uses: int = Field(default=1, ge=1, le=MAX_USES)


class InviteListResponse(BaseModel):
    """Envelope for invite listings."""

    items: list[InviteResponse]
    total: int
    #: As on :class:`CreatedInviteResponse`: null when not configured.
    bot_username: str | None = None
