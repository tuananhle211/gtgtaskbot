"""LLM provider protocol, split by task.

Hard boundary: a provider returns data - a route, a piece of text, an
:class:`ActionPlan`, a JSON payload - and nothing else. It never receives a
database session, an HTTP client for a vendor API, or the ability to call a
tool. Whatever it proposes is re-validated downstream.

**Why the protocol is task-shaped as of 0.4.0.** Before it, one method
(``decide``) answered every free-text message with a single large
structured-output request: route, reply text, clarification and a full action
plan in one JSON object. That coupled three unrelated failure modes. When the
configured model refused a request *parameter*, the one call that could produce
conversation failed, and the only possible outcome was the fallback sentence -
so "Hello" was unanswerable while every slash command still worked.

Now each question is its own call, with its own output mode and its own
fallback:

* :meth:`LLMProvider.route_message` - chat, tool or clarify. Small schema.
* :meth:`LLMProvider.generate_chat_reply` - **plain text**. No schema, no tool
  catalogue, no execution path. This is the call that must keep working when
  structured output does not.
* :meth:`LLMProvider.plan_tool_action` - a strict :class:`ActionPlan`, reached
  only after routing said "tool".
* :meth:`LLMProvider.generate_clarification` - exactly one question.
* :meth:`LLMProvider.summarize_conversation` - the rolling summary.
* :meth:`LLMProvider.review_script` / :meth:`LLMProvider.propose_sheet_mapping`
  - the two advisory structured tasks, unchanged in behaviour.

``decide`` survives as a thin composition of route + generate, because the
internal API and a number of tests describe a message in those terms. Nothing
in the conversational path calls it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from meobot.domain.conversations.decision import ConversationDecision
from meobot.domain.identity.models import Role
from meobot.domain.policy.models import ActionPlan, RiskLevel
from meobot.domain.pr.ai_review import PrFullReviewOutput
from meobot.domain.scripts.models import MappingProposal, ScriptReviewResult
from meobot.integrations.llm.diagnostics import ProviderDiagnostics


class ToolSummary(BaseModel):
    """What the model is told about one tool. No handler, no credentials."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    risk_level: RiskLevel
    arguments_schema: dict[str, Any] = Field(default_factory=dict)


class PlanningRequest(BaseModel):
    """Everything a provider is given to produce a plan."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1, max_length=8000)
    actor_role: Role = Role.EMPLOYEE
    locale: str = "vi"
    available_tools: list[ToolSummary] = Field(default_factory=list)
    conversation_hint: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional short context, e.g. the entity the chat is about.",
    )
    suggested_tool_name: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "What routing thought this was. A hint, not an instruction: the "
            "planner still has to fill valid arguments, and an unfillable plan "
            "must still come back as 'unknown' so the caller asks."
        ),
    )

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self.available_tools}


class ChatTurn(BaseModel):
    """One stored message replayed to the provider.

    ``content`` is already redacted: nothing reaches this model that was not
    first passed through
    :func:`meobot.domain.conversations.redaction.prepare_for_storage`.
    """

    model_config = ConfigDict(frozen=True)

    role: str = Field(pattern="^(user|assistant|tool)$")
    content: str = Field(max_length=8000)


class DecisionRequest(BaseModel):
    """Everything a provider is given to decide what a message means.

    The history is *bounded by construction*: the caller supplies at most
    ``CHAT_HISTORY_MAX_MESSAGES`` turns plus one rolling summary, so the prompt
    cannot grow with the age of the conversation.
    """

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1, max_length=8000)
    actor_role: Role = Role.EMPLOYEE
    actor_name: str = Field(default="", max_length=200)
    locale: str = "vi"
    available_tools: list[ToolSummary] = Field(default_factory=list)
    history: list[ChatTurn] = Field(default_factory=list)
    rolling_summary: str | None = Field(default=None, max_length=4000)
    capability_brief: str | None = Field(
        default=None,
        max_length=6000,
        description="What MeoBot can actually do for this actor, from configuration.",
    )
    workflow_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Active guided flow, if any, so chat can reference it.",
    )
    chat_enabled: bool = Field(
        default=True,
        description="When false the provider must not answer conversationally.",
    )
    max_output_tokens: int = Field(default=1200, ge=64, le=8000)

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self.available_tools}


# --- Routing ---------------------------------------------------------------
class RouteMode(StrEnum):
    """What kind of message this is. Mirrors ``ConversationMode``."""

    CHAT = "chat"
    TOOL = "tool"
    CLARIFY = "clarify"


class MessageRoute(BaseModel):
    """The routing answer. Deliberately tiny.

    A small schema is a schema a weak gateway can satisfy, and routing is the
    one structured call on the conversational path. Nothing here is text the
    user reads - the reply is generated separately, in whichever mode works.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: RouteMode
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    possible_tool_name: str | None = Field(default=None, max_length=100)
    missing_information: str | None = Field(
        default=None,
        max_length=300,
        description="For mode=clarify: which fact is missing. Not reasoning.",
    )
    short_reason_label: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "One diagnostic label for logs ('greeting', 'sheet_sync'). Never a "
            "chain of thought, never shown to a user."
        ),
    )

    @classmethod
    def chat(cls, *, confidence: float = 0.5, label: str | None = None) -> MessageRoute:
        return cls(mode=RouteMode.CHAT, confidence=confidence, short_reason_label=label)

    @classmethod
    def clarify(
        cls,
        *,
        missing: str | None = None,
        confidence: float = 0.5,
        label: str | None = None,
    ) -> MessageRoute:
        return cls(
            mode=RouteMode.CLARIFY,
            confidence=confidence,
            missing_information=missing,
            short_reason_label=label,
        )

    @classmethod
    def tool(
        cls,
        *,
        tool_name: str | None = None,
        confidence: float = 0.5,
        label: str | None = None,
    ) -> MessageRoute:
        return cls(
            mode=RouteMode.TOOL,
            confidence=confidence,
            possible_tool_name=tool_name,
            short_reason_label=label,
        )


#: Schema handed to a real provider for routing. Five scalar fields: this is
#: what a gateway with partial structured-output support can still answer.
ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mode"],
    "properties": {
        "mode": {"type": "string", "enum": ["chat", "tool", "clarify"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "possible_tool_name": {"type": ["string", "null"], "maxLength": 100},
        "missing_information": {"type": ["string", "null"], "maxLength": 300},
        "short_reason_label": {"type": ["string", "null"], "maxLength": 80},
    },
}


class RouteRequest(BaseModel):
    """Input to :meth:`LLMProvider.route_message`."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1, max_length=8000)
    #: Rendered ``PromptContext``. Already bounded and already redacted.
    prompt_context: str = Field(default="", max_length=24000)
    actor_role: Role = Role.EMPLOYEE
    #: Names only. Routing does not need argument schemas, and sending them is
    #: what made the old single call large enough to be fragile.
    tool_names: list[str] = Field(default_factory=list)
    max_output_tokens: int = Field(default=300, ge=64, le=2000)


# --- Chat generation -------------------------------------------------------
class ChatRequest(BaseModel):
    """Input to :meth:`LLMProvider.generate_chat_reply`.

    Carries no tool catalogue and no JSON schema *by construction*. A chat
    generation cannot propose an action because it is never told that actions
    exist in a machine-readable form.
    """

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1, max_length=8000)
    prompt_context: str = Field(default="", max_length=24000)
    history: list[ChatTurn] = Field(default_factory=list)
    max_output_tokens: int = Field(default=1200, ge=64, le=8000)
    #: Set on the retry after a failure: drop optional context, shorten the
    #: budget, ask for the simplest possible answer.
    minimal: bool = False


class ChatReply(BaseModel):
    """Plain conversational text."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=8000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ClarificationRequest(BaseModel):
    """Input to :meth:`LLMProvider.generate_clarification`."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1, max_length=8000)
    prompt_context: str = Field(default="", max_length=24000)
    missing_information: str | None = Field(default=None, max_length=300)
    max_output_tokens: int = Field(default=300, ge=64, le=2000)


class ClarificationReply(BaseModel):
    """Exactly one question back to the user."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, max_length=1000)
    #: Structured facts to carry into the next turn. Never free-form reasoning.
    referenced_context: dict[str, Any] = Field(default_factory=dict)


class SummaryRequest(BaseModel):
    """A request to compress a conversation into a rolling summary."""

    model_config = ConfigDict(frozen=True)

    turns: list[ChatTurn] = Field(default_factory=list)
    previous_summary: str | None = Field(default=None, max_length=4000)
    max_output_tokens: int = Field(default=600, ge=64, le=4000)


class ConversationSummaryResult(BaseModel):
    """The rolling summary, as facts rather than narrative."""

    model_config = ConfigDict(frozen=True)

    summary: str = Field(default="", max_length=4000)

    @property
    def is_empty(self) -> bool:
        return not self.summary.strip()


# --- Advisory structured tasks --------------------------------------------
class ScriptReviewRequest(BaseModel):
    """Input to :meth:`LLMProvider.review_script`."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str = Field(min_length=1, max_length=20000)
    payload: dict[str, Any] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)
    #: Facts the offline provider scores from, so tests exercise both verdicts.
    context: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int = Field(default=4000, ge=64, le=16000)


class PrFullReviewRequest(BaseModel):
    """Input to :meth:`LLMProvider.review_pr_content`.

    Step 1F. Separate from :class:`ScriptReviewRequest` because it is a
    different question with a different answer shape: a script review returns a
    score and a verdict, and a PR full review returns findings whose severities
    the *caller* turns into an outcome. Sharing one request type would have
    meant one of them carrying fields the other ignores.
    """

    model_config = ConfigDict(frozen=True)

    system_prompt: str = Field(min_length=1, max_length=20000)
    #: Authoritative content, assembled server-side. Treated as data by the
    #: system prompt - see ``meobot.integrations.llm.pr_review_prompt``.
    payload: dict[str, Any] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)
    #: Facts the offline provider answers from, so tests reach every outcome.
    context: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int = Field(default=3000, ge=64, le=16000)


class SheetMappingRequest(BaseModel):
    """Input to :meth:`LLMProvider.propose_sheet_mapping`."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str = Field(min_length=1, max_length=20000)
    headers: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, str]] = Field(default_factory=list)
    json_schema: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int = Field(default=1500, ge=64, le=8000)


class StructuredRequest(BaseModel):
    """A request for one schema-bound JSON answer.

    The generic escape hatch behind the typed task methods. It still returns
    data, never an action: mapping proposals and script reviews are both
    advisory input to code that decides.
    """

    model_config = ConfigDict(frozen=True)

    task: str = Field(
        min_length=1,
        max_length=64,
        description="Short task id ('sheet_mapping', 'script_review'); routes the fake provider.",
    )
    system_prompt: str = Field(min_length=1, max_length=20000)
    user_prompt: str = Field(min_length=1, max_length=200000)
    json_schema: dict[str, Any] = Field(default_factory=dict)
    schema_name: str = Field(default="response", max_length=64)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=4000, ge=64, le=32000)
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured facts the fake provider uses to answer deterministically.",
    )


class StructuredResponse(BaseModel):
    """A validated JSON answer plus the provenance needed to store it."""

    model_config = ConfigDict(frozen=True)

    payload: dict[str, Any]
    provider: str
    model: str
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token counts when the provider reports them; empty otherwise.",
    )


@runtime_checkable
class LLMProvider(Protocol):
    """Turns a natural-language message into data - one task at a time."""

    @property
    def name(self) -> str:
        """Provider identifier used in logs and ``/api/v1/system/info``."""
        ...

    @property
    def model(self) -> str:
        """Model identifier stored alongside anything the provider produced."""
        ...

    @property
    def diagnostics(self) -> ProviderDiagnostics:
        """Rolling view of recent calls, for ``/chat_status`` and ``/chat_test``."""
        ...

    # --- Conversational path ---------------------------------------------
    async def route_message(self, request: RouteRequest) -> MessageRoute:
        """Classify a message as chat, tool or clarify.

        Implementations MUST raise :class:`~meobot.core.errors.LLMError` when
        they cannot answer, rather than guessing a mode. The caller owns the
        chat-first fallback: a routing failure defaults to *chat* for an
        ordinary message and to *clarify* for one that looks operational, and
        never to *tool*.
        """
        ...

    async def generate_chat_reply(self, request: ChatRequest) -> ChatReply:
        """Answer conversationally, in plain text.

        Implementations MUST:

        * request ordinary text output - no JSON schema, no JSON mode;
        * never claim an operation was performed (nothing was: this call has no
          tool catalogue and no execution path);
        * raise :class:`~meobot.core.errors.LLMError` rather than return empty
          text, so the caller can retry and then fall back deterministically.
        """
        ...

    async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
        """Propose an action, reached only after routing said "tool".

        Implementations MUST:

        * return ``ActionPlan.unknown()`` rather than guessing when unsure;
        * never name a tool absent from ``request.available_tools``;
        * never raise on unparsable model output.
        """
        ...

    async def generate_clarification(self, request: ClarificationRequest) -> ClarificationReply:
        """Ask exactly one short question, preserving the unresolved reference."""
        ...

    async def summarize_conversation(self, request: SummaryRequest) -> ConversationSummaryResult:
        """Compress conversation turns into a rolling summary of *facts*.

        Summarisation is an optimisation: an empty result is acceptable, an
        exception is not.
        """
        ...

    # --- Advisory structured tasks ---------------------------------------
    async def review_pr_content(self, request: PrFullReviewRequest) -> PrFullReviewOutput:
        """Review one PR content draft and return findings.

        Implementations MUST raise :class:`~meobot.core.errors.LLMError` rather
        than return a partial answer: the caller stores what comes back and
        moves a workflow on it, and a half-parsed review is worse than none.

        Implementations MUST NOT return a verdict. There is no field for one -
        the outcome is derived from finding severities in
        :func:`~meobot.domain.pr.ai_review.derive_outcome`.
        """
        ...

    async def review_script(self, request: ScriptReviewRequest) -> ScriptReviewResult:
        """Score a script against a rubric. Advisory only - approves nothing."""
        ...

    async def propose_sheet_mapping(self, request: SheetMappingRequest) -> MappingProposal:
        """Propose a column mapping. Advisory only - writes nothing."""
        ...

    async def complete_structured(self, request: StructuredRequest) -> StructuredResponse:
        """Answer with JSON matching ``request.json_schema``.

        Implementations MUST raise :class:`~meobot.core.errors.LLMError` rather
        than return a half-parsed payload; the caller validates the payload
        against a Pydantic model before anything is stored.
        """
        ...

    # --- Compatibility ----------------------------------------------------
    async def plan(self, request: PlanningRequest) -> ActionPlan:
        """Alias of :meth:`plan_tool_action`, kept for the internal API."""
        ...

    async def decide(self, request: DecisionRequest) -> ConversationDecision:
        """Route and generate in one call.

        Composed from the task methods above. The Telegram path does not use
        this - it needs the stages separated so a routing failure cannot take
        conversation down with it - but the internal API and the offline
        provider's tests are written in terms of a single decision.
        """
        ...

    async def summarize(self, request: SummaryRequest) -> str:
        """Alias of :meth:`summarize_conversation` returning bare text."""
        ...
