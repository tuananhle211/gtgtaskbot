"""LLM providers.

The abstraction is deliberately narrow: a provider receives a message plus the
catalogue of tools the actor is allowed to see and returns one
:class:`~meobot.domain.conversations.decision.ConversationDecision`, one
:class:`~meobot.domain.policy.models.ActionPlan`, or schema-bound JSON. It
cannot execute anything, it never sees a database session, and it never
receives credentials other than its own API key.

This is also the seam where an OpenClaw (or any other agent runtime) adapter
would plug in later: it would implement :class:`LLMProvider` and still be
subject to the policy engine. See ADR-001 in the README.
"""

from meobot.integrations.llm.base import (
    ChatTurn,
    DecisionRequest,
    LLMProvider,
    PlanningRequest,
    StructuredRequest,
    StructuredResponse,
    SummaryRequest,
    ToolSummary,
)
from meobot.integrations.llm.factory import build_llm_provider
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.integrations.llm.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "ChatTurn",
    "DecisionRequest",
    "FakeLLMProvider",
    "LLMProvider",
    "OpenAICompatibleProvider",
    "PlanningRequest",
    "StructuredRequest",
    "StructuredResponse",
    "SummaryRequest",
    "ToolSummary",
    "build_llm_provider",
]
