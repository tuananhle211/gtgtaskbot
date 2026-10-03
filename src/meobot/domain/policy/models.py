"""Value objects exchanged between the LLM, the policy engine and the tools.

The LLM never returns free-form commands. Its entire output surface is
:class:`ActionPlan` - a validated, schema-bound proposal that the policy engine
is free to reject.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from meobot.domain.identity.models import Role
from meobot.domain.permissions.matrix import Permission
from meobot.domain.scripts.workflow import ScriptStatus
from meobot.domain.videos.workflow import VideoStatus

#: Intent used when the LLM cannot map the message to a known capability.
UNKNOWN_INTENT = "unknown"


class RiskLevel(StrEnum):
    """How much damage a wrong execution could do."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 10, "medium": 20, "high": 30}[self.value]

    @classmethod
    def max_of(cls, first: RiskLevel, second: RiskLevel) -> RiskLevel:
        return first if first.rank >= second.rank else second


class DecisionCode(StrEnum):
    """Machine-readable reason for a policy decision."""

    ALLOWED = "allowed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    UNKNOWN_TOOL = "unknown_tool"
    UNKNOWN_INTENT = "unknown_intent"
    INACTIVE_ACTOR = "inactive_actor"
    NOT_REGISTERED = "not_registered"
    MISSING_PERMISSION = "missing_permission"
    DESTRUCTIVE_DENIED = "destructive_denied"
    INVALID_WORKFLOW_STATE = "invalid_workflow_state"
    NOT_RESOURCE_OWNER = "not_resource_owner"


class ConfirmationState(StrEnum):
    """Lifecycle of a :class:`ConfirmationRequest`."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class IntentEnvelope(BaseModel):
    """Raw classification result before it is bound to a concrete tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(min_length=1, max_length=100)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_text: str = Field(default="", max_length=8000)
    entities: dict[str, str] = Field(default_factory=dict)
    reasoning: str | None = Field(default=None, max_length=2000)

    @property
    def is_unknown(self) -> bool:
        return self.intent == UNKNOWN_INTENT


class ActionPlan(BaseModel):
    """The only thing an LLM is allowed to produce.

    Example::

        {
          "intent": "approve_script",
          "risk_level": "high",
          "requires_confirmation": true,
          "tool_name": "script.approve",
          "arguments": {"script_id": "TT-0312", "version": 3}
        }
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(min_length=1, max_length=100)
    risk_level: RiskLevel = RiskLevel.HIGH
    requires_confirmation: bool = True
    tool_name: str | None = Field(
        default=None,
        max_length=100,
        description="None when the intent maps to no tool (e.g. 'unknown').",
    )
    arguments: dict[str, Any] = Field(default_factory=dict)
    reasoning: str | None = Field(default=None, max_length=2000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    idempotency_key: str | None = Field(
        default=None,
        max_length=200,
        description="Set by the caller so a repeated confirmation cannot double-execute.",
    )

    @classmethod
    def unknown(cls, *, reasoning: str | None = None) -> ActionPlan:
        """Plan used when MeoBot did not understand the request."""
        return cls(
            intent=UNKNOWN_INTENT,
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            tool_name=None,
            reasoning=reasoning,
        )

    @property
    def is_unknown(self) -> bool:
        return self.intent == UNKNOWN_INTENT or self.tool_name is None


@runtime_checkable
class ToolPolicy(Protocol):
    """Policy-relevant metadata of a tool.

    ``ToolDefinition`` in :mod:`meobot.tools` satisfies this structurally, which
    keeps the domain layer free of any dependency on the tool implementations.
    """

    @property
    def name(self) -> str: ...

    @property
    def risk_level(self) -> RiskLevel: ...

    @property
    def required_permission(self) -> Permission | None: ...

    @property
    def destructive(self) -> bool: ...

    @property
    def requires_ownership(self) -> bool: ...

    @property
    def min_role(self) -> Role | None: ...


class PolicyContext(BaseModel):
    """Extra facts the engine needs that are not part of the plan itself."""

    model_config = ConfigDict(frozen=True)

    resource_owner_user_id: uuid.UUID | None = None
    script_status: ScriptStatus | None = None
    video_status: VideoStatus | None = None
    confirmed: bool = Field(
        default=False,
        description="True when a valid confirmation token was already redeemed.",
    )


class PolicyDecision(BaseModel):
    """The engine's verdict on an :class:`ActionPlan`."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    code: DecisionCode
    reason: str
    effective_risk: RiskLevel
    requires_confirmation: bool = False
    missing_permission: Permission | None = None

    @property
    def executable(self) -> bool:
        """True only when the tool may run right now, without further input."""
        return self.allowed and not self.requires_confirmation


class ConfirmationRequest(BaseModel):
    """Domain view of a pending high-risk action awaiting a human 'yes'."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    action_plan: ActionPlan
    confirmation_token: str
    status: ConfirmationState
    expires_at: datetime
    created_at: datetime
    confirmed_at: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        """True when the request can no longer be redeemed at ``now`` (UTC)."""
        return now >= self.expires_at

    def is_redeemable(self, now: datetime) -> bool:
        """True when the token is still pending and unexpired."""
        return self.status is ConfirmationState.PENDING and not self.is_expired(now)
