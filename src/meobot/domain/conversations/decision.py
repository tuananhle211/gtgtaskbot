"""The one thing a provider may return for a free-text message.

Before 0.3.0 the provider's entire output surface was an
:class:`~meobot.domain.policy.models.ActionPlan`. That made every message an
attempted tool call, so "Bạn làm được gì?" came back as *unknown request*:
there is no tool for having a conversation, and there should not be.

:class:`ConversationDecision` widens the surface to exactly three shapes and
not one more:

``chat``
    Talk. No tool runs, no business state changes, nothing is written to
    Google. The reply is text the user reads.

``tool``
    Do something. Carries an ``ActionPlan``, which still goes through the
    policy engine, the permission check, the confirmation flow and the audit
    log exactly as before. Nothing about the execution path is relaxed.

``clarify``
    Ask. One question, plus the structured context needed to continue. Used
    when a target is ambiguous - "duyệt hết đi" must never be resolved by
    guessing which scripts were meant.

The invariants below are enforced by the model, not by the caller, so a
provider cannot return a "chat" that quietly carries an executable plan.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from meobot.domain.policy.models import ActionPlan


class ConversationMode(StrEnum):
    """What the provider decided this message is."""

    CHAT = "chat"
    TOOL = "tool"
    CLARIFY = "clarify"


class ConversationDecision(BaseModel):
    """A validated decision about one user message.

    Example::

        {
          "mode": "chat",
          "reply_text": "Mình có thể quản lý các Sheet kịch bản...",
          "confidence": 0.9
        }
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: ConversationMode
    reply_text: str | None = Field(
        default=None,
        max_length=8000,
        description="Required for mode=chat. Sent to the user as-is.",
    )
    action_plan: ActionPlan | None = Field(
        default=None,
        description="Required for mode=tool. Forbidden otherwise.",
    )
    clarification_question: str | None = Field(
        default=None,
        max_length=1000,
        description="Required for mode=clarify. Exactly one question.",
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    referenced_context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured facts to carry into the next message (the entity being "
            "discussed, a pending target). Never free-form reasoning."
        ),
    )
    internal_summary: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Short diagnostic label for logs. NEVER sent to a user and never "
            "persisted as conversation content. This is not chain-of-thought: "
            "providers are asked for a one-line label, not for their reasoning."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> ConversationDecision:
        """Enforce that the mode and the payload agree."""
        if self.mode is ConversationMode.CHAT:
            if not (self.reply_text or "").strip():
                raise ValueError("mode=chat requires reply_text")
            if self.action_plan is not None:
                raise ValueError("mode=chat must not carry an action_plan")
            if self.clarification_question is not None:
                raise ValueError("mode=chat must not carry a clarification_question")
        elif self.mode is ConversationMode.TOOL:
            if self.action_plan is None:
                raise ValueError("mode=tool requires an action_plan")
            if self.clarification_question is not None:
                raise ValueError("mode=tool must not carry a clarification_question")
        else:  # CLARIFY
            if not (self.clarification_question or "").strip():
                raise ValueError("mode=clarify requires a clarification_question")
            if self.action_plan is not None:
                raise ValueError("mode=clarify must not carry an action_plan")
        return self

    # --- Constructors -----------------------------------------------------
    @classmethod
    def chat(
        cls,
        reply_text: str,
        *,
        confidence: float | None = None,
        context: dict[str, Any] | None = None,
        internal_summary: str | None = None,
    ) -> ConversationDecision:
        """A conversational reply that executes nothing."""
        return cls(
            mode=ConversationMode.CHAT,
            reply_text=reply_text,
            confidence=confidence,
            referenced_context=context or {},
            internal_summary=internal_summary,
        )

    @classmethod
    def tool(
        cls,
        plan: ActionPlan,
        *,
        reply_text: str | None = None,
        confidence: float | None = None,
        context: dict[str, Any] | None = None,
        internal_summary: str | None = None,
    ) -> ConversationDecision:
        """An operational request. ``reply_text`` is an optional preamble."""
        return cls(
            mode=ConversationMode.TOOL,
            action_plan=plan,
            reply_text=reply_text,
            confidence=confidence,
            referenced_context=context or {},
            internal_summary=internal_summary,
        )

    @classmethod
    def clarify(
        cls,
        question: str,
        *,
        confidence: float | None = None,
        context: dict[str, Any] | None = None,
        internal_summary: str | None = None,
    ) -> ConversationDecision:
        """One question back to the user, with the context to resume."""
        return cls(
            mode=ConversationMode.CLARIFY,
            clarification_question=question,
            confidence=confidence,
            referenced_context=context or {},
            internal_summary=internal_summary,
        )

    # --- Views ------------------------------------------------------------
    @property
    def executes_a_tool(self) -> bool:
        """True only for ``mode=tool``. Read this instead of comparing modes."""
        return self.mode is ConversationMode.TOOL

    def user_visible_text(self) -> str | None:
        """The text a user may see. Never includes :attr:`internal_summary`."""
        if self.mode is ConversationMode.CHAT:
            return self.reply_text
        if self.mode is ConversationMode.CLARIFY:
            return self.clarification_question
        return self.reply_text


#: JSON schema handed to a real provider. Deliberately narrower than the model:
#: the provider may not set an idempotency key, and ``internal_summary`` is
#: capped hard so it cannot become a channel for hidden reasoning.
DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mode"],
    "properties": {
        "mode": {"type": "string", "enum": ["chat", "tool", "clarify"]},
        "reply_text": {"type": ["string", "null"], "maxLength": 8000},
        "clarification_question": {"type": ["string", "null"], "maxLength": 1000},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "internal_summary": {"type": ["string", "null"], "maxLength": 200},
        "referenced_context": {"type": "object", "additionalProperties": True},
        "action_plan": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["intent", "tool_name", "arguments"],
            "properties": {
                "intent": {"type": "string", "maxLength": 100},
                "tool_name": {"type": ["string", "null"], "maxLength": 100},
                "arguments": {"type": "object", "additionalProperties": True},
                "risk_level": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
                "reasoning": {"type": ["string", "null"], "maxLength": 2000},
            },
        },
    },
}
