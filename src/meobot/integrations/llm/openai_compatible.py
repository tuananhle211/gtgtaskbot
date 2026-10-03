"""OpenAI-compatible chat-completions provider.

Works against any endpoint implementing ``POST /v1/chat/completions`` - OpenAI
itself, but also OpenRouter, Together, vLLM and friends. The model name is
configuration (``LLM_MODEL``), never a constant in this file.

**The bug this file was rewritten to fix.** Every request sent ``max_tokens``
and an explicit ``temperature``. The configured model rejected both with HTTP
400 (``max_tokens`` is ``max_completion_tokens`` on that model; only the default
temperature is accepted). Every task went through one structured-output call, so
every free-text message died at the first request and the user got the same
"trợ lý AI đang trục trặc" sentence for "Hello" as for a malformed instruction.
Slash commands kept working because they never call a model.

Three things changed:

1. **Parameters are negotiated, not assumed.** A 400 naming a parameter is
   turned into a capability downgrade (see
   :mod:`meobot.integrations.llm.capabilities`) and the request is retried
   immediately. The downgrade sticks for the process.
2. **Output mode degrades.** ``json_schema`` -> ``json_object`` -> strict JSON
   in plain text with a local parser -> one repair attempt. A gateway without
   structured output can still route.
3. **Chat generation is plain text.** It carries no schema and no tool
   catalogue, so it stays available even when every structured mode is refused.

Boundaries this provider does not cross:

* it receives no database session and no credentials beyond its own API key;
* it returns data and never executes anything;
* a tool name it invents is dropped here, and would be rejected by the policy
  engine anyway.

Nothing here logs an API key, an Authorization header, a prompt, or a response
body. What is logged is the task, the provider, the model, the attempt, the
latency, the HTTP status and an error category.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from meobot.core.errors import (
    IntegrationAuthError,
    IntegrationRateLimitError,
    IntegrationTimeoutError,
    LLMError,
)
from meobot.core.logging import get_logger
from meobot.domain.conversations.decision import (
    DECISION_SCHEMA,
    ConversationDecision,
    ConversationMode,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.policy.models import ActionPlan, RiskLevel
from meobot.integrations.base import IntegrationConfig, call_with_retries
from meobot.integrations.llm.base import (
    ROUTE_SCHEMA,
    ChatReply,
    ChatRequest,
    ClarificationReply,
    ClarificationRequest,
    ConversationSummaryResult,
    DecisionRequest,
    MessageRoute,
    PlanningRequest,
    RouteMode,
    RouteRequest,
    StructuredRequest,
    StructuredResponse,
    SummaryRequest,
)
from meobot.integrations.llm.capabilities import OutputMode, ProviderCapabilities
from meobot.integrations.llm.diagnostics import ErrorCategory, ProviderDiagnostics
from meobot.integrations.llm.prompts import (
    CHAT_SYSTEM_PROMPT,
    CLARIFY_SYSTEM_PROMPT,
    DECISION_SYSTEM_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
)
from meobot.integrations.llm.structured_tasks import StructuredTaskMixin

logger = get_logger(__name__)

PROVIDER = "openai"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: How many times one request may be re-sent with adjusted parameters before we
#: give up. Bounded so a provider that rejects everything cannot loop.
MAX_CAPABILITY_ADJUSTMENTS = 4

#: How many times an empty, length-truncated answer may buy a bigger budget,
#: and the ceiling it may buy up to. Bounded so a model that always returns
#: nothing cannot spend an unbounded number of tokens proving it.
MAX_BUDGET_ESCALATIONS = 2
MAX_ESCALATED_TOKENS = 8000

#: Temperature MeoBot asks for when the provider accepts one. Not sent at all
#: once ``supports_temperature`` has been downgraded.
CHAT_TEMPERATURE = 0.7
DETERMINISTIC_TEMPERATURE = 0.0

#: The plan schema handed to the model. Deliberately narrower than ActionPlan:
#: the model may not set an idempotency key.
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "tool_name", "arguments", "risk_level", "confidence", "reasoning"],
    "properties": {
        "intent": {"type": "string", "maxLength": 100},
        "tool_name": {"type": ["string", "null"], "maxLength": 100},
        "arguments": {"type": "object", "additionalProperties": True},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": ["string", "null"], "maxLength": 2000},
    },
}

#: Finds a JSON object inside prose, for the plain-text structured fallback.
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class _ParameterRejectedError(Exception):
    """Internal: the provider refused a request parameter we can adjust."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label


class _ModeRejectedError(Exception):
    """Internal: the provider refused this ``response_format``."""


@dataclass(frozen=True, slots=True)
class _Completion:
    """One successful answer, plus what the provider reported about it."""

    content: str
    usage: dict[str, int]


class OpenAICompatibleProvider(StructuredTaskMixin):
    """Chat-completions provider with negotiated output modes.

    Args:
        api_key: Secret. Held in memory, sent only in the Authorization header,
            never logged.
        model: Model identifier from ``LLM_MODEL``.
        base_url: API root, for OpenAI-compatible gateways.
        config: Timeout and retry budget.
        client: Injected ``httpx.AsyncClient`` (tests pass a MockTransport).
        capabilities: Starting assumptions. Tests pin these; production learns.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        config: IntegrationConfig | None = None,
        client: httpx.AsyncClient | None = None,
        capabilities: ProviderCapabilities | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("LLM_API_KEY is required when LLM_PROVIDER is not 'fake'")
        if not model:
            raise LLMError("LLM_MODEL is required when LLM_PROVIDER is not 'fake'")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._config = config or IntegrationConfig(provider=PROVIDER)
        self._client = client
        self._owns_client = client is None
        self._capabilities = capabilities or ProviderCapabilities()
        self._diagnostics = ProviderDiagnostics()

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this endpoint has been observed to accept."""
        return self._capabilities

    @property
    def diagnostics(self) -> ProviderDiagnostics:
        return self._diagnostics

    async def aclose(self) -> None:
        """Close the HTTP client when this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Routing ----------------------------------------------------------
    async def route_message(self, request: RouteRequest) -> MessageRoute:
        """Classify a message as chat, tool or clarify.

        Raises:
            LLMError: When the provider fails or produces something unusable
                even after one repair attempt. The caller owns the chat-first
                fallback; guessing a mode here would let a parse failure reach
                the execution path.
        """
        user_prompt = json.dumps(
            {
                "message": request.message,
                "actor_role": request.actor_role.value,
                "available_tool_names": request.tool_names,
                "context": request.prompt_context,
            },
            ensure_ascii=False,
        )
        payload, _ = await self._structured_json(
            task="route_message",
            system_prompt=ROUTING_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_schema=ROUTE_SCHEMA,
            schema_name="message_route",
            temperature=DETERMINISTIC_TEMPERATURE,
            max_output_tokens=request.max_output_tokens,
            repair_hint='Trả lời lại đúng schema, ví dụ {"mode":"chat","confidence":0.8}.',
        )
        return self._to_route(payload, allowed=set(request.tool_names))

    def _to_route(self, payload: dict[str, Any], *, allowed: set[str]) -> MessageRoute:
        """Validate a routing payload, refusing an invented tool name.

        Raises:
            LLMError: When ``mode`` is missing or not one of the three.
        """
        raw_mode = str(payload.get("mode", "")).strip().lower()
        if raw_mode not in {mode.value for mode in RouteMode}:
            self._diagnostics.record_error(
                task="route_message", category=ErrorCategory.PARSE_FAILED
            )
            raise LLMError(
                "LLM trả về chế độ định tuyến không hợp lệ.",
                details={"task": "route_message", "parser_failure": "invalid_mode"},
            )

        tool_name = payload.get("possible_tool_name")
        if not isinstance(tool_name, str) or tool_name not in allowed:
            if tool_name:
                logger.warning("llm_route_unknown_tool", extra={"tool_name": str(tool_name)[:80]})
            tool_name = None

        return MessageRoute(
            mode=RouteMode(raw_mode),
            confidence=_clamped_float(payload.get("confidence")) or 0.0,
            possible_tool_name=tool_name,
            missing_information=_optional_str(payload.get("missing_information"), 300),
            short_reason_label=_optional_str(payload.get("short_reason_label"), 80),
        )

    # --- Chat generation ---------------------------------------------------
    async def generate_chat_reply(self, request: ChatRequest) -> ChatReply:
        """Answer conversationally, as plain text.

        This is the call that has to survive a gateway with no structured-output
        support at all, so it sends no ``response_format`` and no tool
        catalogue. There is no path from here to a tool.

        Raises:
            LLMError: On a transport failure or an empty answer. The caller
                retries once with ``minimal=True`` and then falls back to a
                deterministic reply.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        if request.prompt_context and not request.minimal:
            messages.append({"role": "system", "content": request.prompt_context})
        elif request.prompt_context:
            # The retry keeps identity and address rules, drops everything else.
            messages.append(
                {"role": "system", "content": _essential_context(request.prompt_context)}
            )
        if not request.minimal:
            messages.extend(
                {"role": _api_role(turn.role), "content": turn.content} for turn in request.history
            )
        messages.append({"role": "user", "content": request.message})

        text = await self._text_completion(
            task="generate_chat_reply" if not request.minimal else "generate_chat_reply_minimal",
            messages=messages,
            temperature=CHAT_TEMPERATURE,
            max_output_tokens=min(request.max_output_tokens, 600 if request.minimal else 8000),
        )
        return ChatReply(text=text)

    async def generate_clarification(self, request: ClarificationRequest) -> ClarificationReply:
        """Ask exactly one short question.

        Raises:
            LLMError: On a transport failure or an empty answer.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": CLARIFY_SYSTEM_PROMPT}]
        if request.prompt_context:
            messages.append({"role": "system", "content": request.prompt_context})
        missing = (
            f"\n\nThông tin còn thiếu: {request.missing_information}"
            if request.missing_information
            else ""
        )
        messages.append({"role": "user", "content": request.message + missing})

        text = await self._text_completion(
            task="generate_clarification",
            messages=messages,
            temperature=DETERMINISTIC_TEMPERATURE,
            max_output_tokens=request.max_output_tokens,
        )
        # One question, whatever the model wrote.
        question = text.strip().split("\n")[0][:1000] or text[:1000]
        return ClarificationReply(
            question=question,
            referenced_context=(
                {"missing_information": request.missing_information}
                if request.missing_information
                else {}
            ),
        )

    # --- Tool planning -----------------------------------------------------
    async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
        """Classify a message into a tool call, or into 'unknown'.

        A malformed answer, an unknown tool, or a network failure all become
        ``ActionPlan.unknown()``: reaching the execution path requires a plan
        this provider actually produced and the policy engine then approved.
        """
        catalogue = [
            {
                "name": tool.name,
                "description": tool.description,
                "risk_level": tool.risk_level.value,
                "arguments_schema": tool.arguments_schema,
            }
            for tool in request.available_tools
        ]
        user_prompt = json.dumps(
            {
                "message": request.message,
                "actor_role": request.actor_role.value,
                "context_hint": request.conversation_hint,
                # A hint from routing, not an instruction. The model still has
                # to fill valid arguments, and must still answer "unknown" when
                # a required one was never stated - guessing a Sheet id is the
                # exact failure this whole path is built to prevent.
                "suggested_tool_name": request.suggested_tool_name,
                "available_tools": catalogue,
            },
            ensure_ascii=False,
        )
        try:
            payload, _ = await self._structured_json(
                task="plan_tool_action",
                system_prompt=PLANNING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_schema=PLAN_SCHEMA,
                schema_name="action_plan",
                temperature=DETERMINISTIC_TEMPERATURE,
                max_output_tokens=1000,
            )
        except LLMError as exc:
            logger.warning("llm_plan_failed", extra={"error_code": exc.code})
            return ActionPlan.unknown(reasoning="Provider failed to produce a plan")
        return self._to_plan(payload, allowed=request.tool_names())

    async def plan(self, request: PlanningRequest) -> ActionPlan:
        """Alias kept for the internal API."""
        return await self.plan_tool_action(request)

    # --- Summarisation ----------------------------------------------------
    async def summarize_conversation(self, request: SummaryRequest) -> ConversationSummaryResult:
        """Compress a conversation into facts. Never raises."""
        if not request.turns:
            return ConversationSummaryResult(summary=request.previous_summary or "")
        transcript = "\n".join(f"{turn.role}: {turn.content}" for turn in request.turns)
        user_prompt = (
            f"Tóm tắt trước đó:\n{request.previous_summary}\n\n" if request.previous_summary else ""
        ) + f"Các lượt cần gộp vào tóm tắt:\n{transcript}"

        try:
            text = await self._text_completion(
                task="summarize_conversation",
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=DETERMINISTIC_TEMPERATURE,
                max_output_tokens=request.max_output_tokens,
            )
        except LLMError as exc:
            logger.warning("llm_summary_failed", extra={"error_code": exc.code})
            return ConversationSummaryResult(summary=request.previous_summary or "")
        return ConversationSummaryResult(summary=text[:4000])

    async def summarize(self, request: SummaryRequest) -> str:
        """Alias returning bare text."""
        return (await self.summarize_conversation(request)).summary

    # --- Compatibility: one-shot decision ---------------------------------
    async def decide(self, request: DecisionRequest) -> ConversationDecision:
        """Route and generate in one call, for the internal API.

        The Telegram path does not use this: it needs the stages separated so a
        routing failure cannot take conversation down with it.
        """
        user_prompt = self._decision_prompt(request)
        try:
            payload, _ = await self._structured_json(
                task="conversation_decision",
                system_prompt=DECISION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_schema=DECISION_SCHEMA,
                schema_name="conversation_decision",
                temperature=CHAT_TEMPERATURE,
                max_output_tokens=request.max_output_tokens,
                repair_hint="Hãy trả lời lại đúng schema, chỉ điền đúng một chế độ.",
            )
        except LLMError as exc:
            logger.warning("llm_decide_failed", extra={"error_code": exc.code})
            return self._decision_fallback("provider_error")

        decision, error = self._to_decision(payload, allowed=request.tool_names())
        if decision is not None:
            return decision
        logger.warning("llm_decision_unusable", extra={"reason": error[:120]})
        return self._decision_fallback("invalid_output")

    @staticmethod
    def _decision_prompt(request: DecisionRequest) -> str:
        """Serialise the bounded context handed to the model."""
        catalogue = [
            {
                "name": tool.name,
                "description": tool.description,
                "risk_level": tool.risk_level.value,
                "arguments_schema": tool.arguments_schema,
            }
            for tool in request.available_tools
        ]
        return json.dumps(
            {
                "message": request.message,
                # The enum is what the model reasons about; the label is what it
                # is allowed to say out loud (see prompts._ROLE_RULES).
                "actor": {
                    "name": request.actor_name,
                    "role": request.actor_role.value,
                    "role_label": role_label(request.actor_role),
                },
                "chat_enabled": request.chat_enabled,
                "capabilities": request.capability_brief,
                "rolling_summary": request.rolling_summary,
                "recent_messages": [
                    {"role": turn.role, "content": turn.content} for turn in request.history
                ],
                "active_workflow": request.workflow_context or None,
                "available_tools": catalogue,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _decision_fallback(reason: str) -> ConversationDecision:
        """What ``decide`` returns when the provider could not be understood."""
        return ConversationDecision.clarify(
            "Mình chưa xử lý được câu này. Bạn nói rõ hơn giúp mình, hoặc dùng "
            "/help để xem các lệnh nhé.",
            internal_summary=f"fallback:{reason}",
        )

    @classmethod
    def _to_decision(
        cls,
        payload: dict[str, Any],
        *,
        allowed: set[str],
    ) -> tuple[ConversationDecision | None, str]:
        """Validate a raw decision payload."""
        raw_mode = str(payload.get("mode", "")).strip().lower()
        if raw_mode not in {mode.value for mode in ConversationMode}:
            return None, f"mode không hợp lệ: {raw_mode!r}"

        mode = ConversationMode(raw_mode)
        plan: ActionPlan | None = None
        if mode is ConversationMode.TOOL:
            raw_plan = payload.get("action_plan")
            if not isinstance(raw_plan, dict):
                return None, "mode=tool nhưng thiếu action_plan"
            tool_name = raw_plan.get("tool_name")
            if not isinstance(tool_name, str) or tool_name not in allowed:
                # An invented tool never becomes an executable plan.
                logger.warning(
                    "llm_decision_unknown_tool", extra={"tool_name": str(tool_name)[:80]}
                )
                return None, "action_plan gọi một công cụ không tồn tại"
            plan = cls._to_plan(
                {
                    "intent": raw_plan.get("intent") or tool_name,
                    "tool_name": tool_name,
                    "arguments": raw_plan.get("arguments") or {},
                    "risk_level": raw_plan.get("risk_level") or "high",
                    "confidence": payload.get("confidence") or 0.0,
                    "reasoning": raw_plan.get("reasoning"),
                },
                allowed=allowed,
            )
            if plan.is_unknown:
                return None, "action_plan không dựng được"

        try:
            decision = ConversationDecision(
                mode=mode,
                reply_text=_optional_str(payload.get("reply_text"), 8000),
                action_plan=plan,
                clarification_question=_optional_str(payload.get("clarification_question"), 1000),
                confidence=_clamped_float(payload.get("confidence")),
                referenced_context=(
                    dict(payload["referenced_context"])
                    if isinstance(payload.get("referenced_context"), dict)
                    else {}
                ),
                internal_summary=_optional_str(payload.get("internal_summary"), 200),
            )
        except PydanticValidationError as exc:
            return None, "; ".join(error["msg"] for error in exc.errors())[:300]
        return decision, ""

    @staticmethod
    def _to_plan(payload: dict[str, Any], *, allowed: set[str]) -> ActionPlan:
        """Validate a raw plan payload, refusing anything outside the catalogue."""
        tool_name = payload.get("tool_name")
        if tool_name is not None and tool_name not in allowed:
            logger.warning("llm_proposed_unknown_tool", extra={"tool_name": str(tool_name)[:80]})
            return ActionPlan.unknown(reasoning="Provider named a tool that does not exist")
        try:
            return ActionPlan(
                intent=str(payload.get("intent") or "unknown")[:100],
                risk_level=RiskLevel(str(payload.get("risk_level", "high"))),
                requires_confirmation=True,
                tool_name=tool_name,
                arguments=dict(payload.get("arguments") or {}),
                reasoning=(str(payload["reasoning"])[:2000] if payload.get("reasoning") else None),
                confidence=float(payload.get("confidence") or 0.0),
            )
        except (PydanticValidationError, TypeError, ValueError) as exc:
            logger.warning("llm_plan_invalid", extra={"error": type(exc).__name__})
            return ActionPlan.unknown(reasoning="Provider returned an unusable plan")

    # --- Structured output ------------------------------------------------
    async def complete_structured(self, request: StructuredRequest) -> StructuredResponse:
        """Ask for one JSON object matching ``request.json_schema``.

        Raises:
            LLMError: On a transport failure, a non-JSON answer, or a refusal.
        """
        payload, usage = await self._structured_json(
            task=request.task,
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
            json_schema=request.json_schema,
            schema_name=request.schema_name,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
        )
        return StructuredResponse(
            payload=payload,
            provider=PROVIDER,
            model=self._model,
            usage=usage,
        )

    async def _structured_json(
        self,
        *,
        task: str,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        schema_name: str,
        temperature: float,
        max_output_tokens: int,
        repair_hint: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Get one JSON object back, degrading the output mode as needed.

        The order is: JSON schema when supported, JSON object mode, then plain
        text containing strict JSON parsed locally, then one repair attempt.

        Raises:
            LLMError: When every mode failed to yield a JSON object.
        """
        mode = self._capabilities.best_mode(OutputMode.JSON_SCHEMA)
        instruction = _json_instruction(json_schema)

        while True:
            prompt = (
                user_prompt if mode is not OutputMode.TEXT else f"{user_prompt}\n\n{instruction}"
            )
            try:
                completed = await self._completion(
                    task=task,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    mode=mode,
                    json_schema=json_schema,
                    schema_name=schema_name,
                )
            except _ModeRejectedError:
                weaker = self._capabilities.weaken(mode)
                self._diagnostics.record_downgrade(f"{task}:{mode.value}_unsupported")
                logger.info(
                    "llm_output_mode_downgraded",
                    extra={
                        "task": task,
                        "from_mode": mode.value,
                        "to_mode": getattr(weaker, "value", None),
                    },
                )
                if weaker is None:
                    raise LLMError(
                        "Provider không hỗ trợ bất kỳ chế độ đầu ra nào.",
                        details={"task": task, "parser_failure": "no_supported_mode"},
                    ) from None
                mode = weaker
                continue
            break

        parsed = _parse_json_object(completed.content)
        if parsed is not None:
            return parsed, completed.usage

        if repair_hint is None:
            self._diagnostics.record_error(task=task, category=ErrorCategory.PARSE_FAILED)
            raise LLMError(
                "LLM trả về dữ liệu không phải JSON hợp lệ.",
                details={"task": task, "parser_failure": "not_json"},
            )

        logger.info("llm_structured_repair_attempt", extra={"task": task, "mode": mode.value})
        repaired = await self._completion(
            task=f"{task}_repair",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "user", "content": f"{repair_hint}\n{_json_instruction(json_schema)}"},
            ],
            temperature=DETERMINISTIC_TEMPERATURE,
            max_output_tokens=max_output_tokens,
            mode=OutputMode.TEXT,
            json_schema=json_schema,
            schema_name=schema_name,
        )
        parsed = _parse_json_object(repaired.content)
        if parsed is None:
            self._diagnostics.record_error(task=task, category=ErrorCategory.PARSE_FAILED)
            raise LLMError(
                "LLM vẫn không trả về JSON hợp lệ sau khi sửa.",
                details={"task": task, "parser_failure": "not_json_after_repair"},
            )
        return parsed, repaired.usage

    async def _text_completion(
        self,
        *,
        task: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        """One plain-text completion.

        Raises:
            LLMError: On a transport failure or an empty answer.
        """
        completed = await self._completion(
            task=task,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            mode=OutputMode.TEXT,
            json_schema={},
            schema_name="",
        )
        text = completed.content
        if not text.strip():
            self._diagnostics.record_error(task=task, category=ErrorCategory.EMPTY_RESPONSE)
            raise LLMError("LLM không trả về nội dung.", details={"task": task})
        return text.strip()

    # --- Transport --------------------------------------------------------
    async def _completion(
        self,
        *,
        task: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_output_tokens: int,
        mode: OutputMode,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> _Completion:
        """Send one request, adjusting parameters the provider refuses.

        Raises:
            _ModeRejected: The provider refused this ``response_format``.
            LLMError: Anything the caller must surface.
        """
        started = time.monotonic()
        budget = max_output_tokens
        escalations = 0
        for adjustment in range(MAX_CAPABILITY_ADJUSTMENTS + 1):
            body = self._build_body(
                messages=messages,
                temperature=temperature,
                max_output_tokens=budget,
                mode=mode,
                json_schema=json_schema,
                schema_name=schema_name,
            )
            try:
                payload = await self._send(body, task=task, attempt=adjustment + 1)
            except _ParameterRejectedError as rejected:
                self._diagnostics.record_downgrade(rejected.label)
                logger.info(
                    "llm_parameter_adjusted",
                    extra={"task": task, "provider": PROVIDER, "adjustment": rejected.label},
                )
                continue

            content = _extract_content(payload, task=task)

            # A model that thinks before it answers can spend the whole output
            # budget on reasoning and return *nothing*, with
            # ``finish_reason="length"``. That is a budget problem, not an
            # outage: reporting it as an empty answer would send a perfectly
            # healthy provider down the fallback path. Observed live on the
            # configured model during a long conversation.
            if not content.strip() and _truncated(payload) and escalations < MAX_BUDGET_ESCALATIONS:
                escalations += 1
                budget = min(budget * 3, MAX_ESCALATED_TOKENS)
                logger.info(
                    "llm_output_budget_raised",
                    extra={
                        "task_type": task,
                        "provider": PROVIDER,
                        "escalation": escalations,
                        "max_output_tokens": budget,
                    },
                )
                continue

            usage = _extract_usage(payload)
            latency_ms = int((time.monotonic() - started) * 1000)
            self._diagnostics.record_success(task=task, latency_ms=latency_ms)
            logger.info(
                "llm_call_completed",
                extra={
                    "task_type": task,
                    "provider": PROVIDER,
                    "model": self._model,
                    "endpoint_category": "chat_completions",
                    "output_mode": mode.value,
                    "attempt": adjustment + 1,
                    "latency_ms": latency_ms,
                    "http_status": 200,
                    **usage,
                },
            )
            return _Completion(content=content, usage=usage)

        self._diagnostics.record_error(task=task, category=ErrorCategory.BAD_REQUEST)
        raise LLMError(
            "Provider từ chối các tham số của yêu cầu.",
            details={"task": task, "provider": PROVIDER, "adjustments": MAX_CAPABILITY_ADJUSTMENTS},
        )

    def _build_body(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        max_output_tokens: int,
        mode: OutputMode,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        """Assemble the request body from the negotiated capabilities."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            self._capabilities.max_tokens_parameter: max_output_tokens,
        }
        if self._capabilities.supports_temperature:
            body["temperature"] = temperature
        if mode is OutputMode.JSON_SCHEMA:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name or "response",
                    "strict": False,
                    "schema": json_schema,
                },
            }
        elif mode is OutputMode.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}
        return body

    async def _send(self, body: dict[str, Any], *, task: str, attempt: int) -> dict[str, Any]:
        """POST once, through the shared retry/timeout budget."""

        async def call() -> dict[str, Any]:
            client = self._ensure_client()
            response = await client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            return self._handle(response, task=task)

        try:
            return await call_with_retries(call, config=self._config, operation_name=f"llm.{task}")
        except IntegrationTimeoutError as exc:
            self._diagnostics.record_error(task=task, category=ErrorCategory.TIMEOUT)
            raise LLMError(
                "LLM không phản hồi kịp thời.",
                details={"task": task, "provider": PROVIDER, "attempt": attempt},
            ) from exc
        except IntegrationAuthError as exc:
            self._diagnostics.record_error(task=task, category=ErrorCategory.AUTH)
            raise LLMError(
                exc.message, details={"task": task, "category": ErrorCategory.AUTH}
            ) from exc
        except IntegrationRateLimitError as exc:
            self._diagnostics.record_error(task=task, category=ErrorCategory.RATE_LIMIT)
            raise LLMError(
                exc.message, details={"task": task, "category": ErrorCategory.RATE_LIMIT}
            ) from exc
        except httpx.HTTPError as exc:
            self._diagnostics.record_error(task=task, category=ErrorCategory.NETWORK)
            raise LLMError(
                "Không kết nối được tới LLM provider.",
                details={"task": task, "error": type(exc).__name__},
            ) from exc

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client

    def _handle(self, response: httpx.Response, *, task: str) -> dict[str, Any]:
        """Translate an HTTP response into a payload or a typed error.

        A 400 is inspected for the *name* of the refused parameter and nothing
        else - never the body, which can echo the prompt.
        """
        status = response.status_code
        if status == httpx.codes.OK:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

        details = {"task": task, "status": status}
        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise IntegrationAuthError(
                "LLM provider từ chối API key (kiểm tra LLM_API_KEY).",
                provider=PROVIDER,
                details=details,
            )
        if status == httpx.codes.TOO_MANY_REQUESTS or status >= 500:
            raise IntegrationRateLimitError(
                f"LLM provider tạm thời quá tải (HTTP {status}).",
                provider=PROVIDER,
                details=details,
            )

        code, param, message = _error_fields(response)
        if status == httpx.codes.NOT_FOUND:
            self._diagnostics.record_error(
                task=task, category=ErrorCategory.MODEL_OR_ENDPOINT_NOT_FOUND
            )
            logger.error(
                "llm_endpoint_or_model_not_found",
                extra={
                    "task_type": task,
                    "provider": PROVIDER,
                    "model": self._model,
                    "http_status": status,
                    "error_code": code,
                    "endpoint_category": "chat_completions",
                },
            )
            raise LLMError(
                "Không tìm thấy mô hình hoặc endpoint (kiểm tra LLM_MODEL và LLM_BASE_URL).",
                details={**details, "category": ErrorCategory.MODEL_OR_ENDPOINT_NOT_FOUND},
            )

        if status == httpx.codes.BAD_REQUEST:
            if _mentions_response_format(param, message):
                logger.info(
                    "llm_response_format_rejected",
                    extra={"task_type": task, "provider": PROVIDER, "error_code": code},
                )
                raise _ModeRejectedError
            label = self._capabilities.adjust_for(code=code, param=param, message=message)
            if label is not None:
                raise _ParameterRejectedError(label)

        self._diagnostics.record_error(task=task, category=ErrorCategory.BAD_REQUEST)
        logger.warning(
            "llm_http_error",
            extra={
                "task_type": task,
                "provider": PROVIDER,
                "model": self._model,
                "http_status": status,
                "error_code": code,
                "error_param": param,
                "endpoint_category": "chat_completions",
            },
        )
        raise LLMError(f"LLM provider trả về lỗi HTTP {status}.", details=details)


# --- Module helpers --------------------------------------------------------
def _api_role(role: str) -> str:
    """Map a stored turn role onto a chat-completions role.

    A ``tool`` row records that a tool ran; without a matching ``tool_call_id``
    the API would reject it, so it is replayed as an assistant turn.
    """
    return "assistant" if role in {"assistant", "tool"} else "user"


def _essential_context(context: str) -> str:
    """Keep only the sections a minimal retry still needs.

    Identity and the address rule stay because losing them changes *who is
    speaking*; everything else is an optimisation the retry can afford to drop.
    """
    keep = ("[ASSISTANT IDENTITY]", "[CURRENT USER]")
    blocks = [block for block in context.split("\n\n") if block.startswith(keep)]
    return "\n\n".join(blocks)[:4000]


def _json_instruction(json_schema: dict[str, Any]) -> str:
    """Instruction used when the provider has no structured-output mode."""
    if not json_schema:
        return "Chỉ trả về một JSON object hợp lệ, không kèm giải thích."
    return (
        "Chỉ trả về MỘT JSON object hợp lệ khớp schema sau, không kèm giải "
        "thích, không kèm dấu ```:\n" + json.dumps(json_schema, ensure_ascii=False)
    )


def _parse_json_object(content: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model answer, tolerating fences and prose."""
    if not content.strip():
        return None
    candidates = [_FENCE.sub("", content).strip(), content.strip()]
    match = _JSON_BLOCK.search(content)
    if match is not None:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_content(payload: dict[str, Any], *, task: str) -> str:
    """Pull the text out of the first choice.

    Raises:
        LLMError: When the model refused or returned no content.
    """
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError("LLM trả về phản hồi rỗng.", details={"task": task})
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise LLMError(
            "LLM từ chối thực hiện yêu cầu này.",
            details={"task": task, "category": ErrorCategory.REFUSAL},
        )
    content = message.get("content")
    if not isinstance(content, str):
        raise LLMError("LLM không trả về nội dung.", details={"task": task})
    return content


def _truncated(payload: dict[str, Any]) -> bool:
    """True when the answer was cut off by the output-token budget."""
    choices = payload.get("choices") or []
    if not choices:
        return False
    return bool(choices[0].get("finish_reason") == "length")


def _extract_usage(payload: dict[str, Any]) -> dict[str, int]:
    """Normalise the usage block when the provider reports one."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {key: int(usage[key]) for key in keys if isinstance(usage.get(key), int)}


def _error_fields(response: httpx.Response) -> tuple[str | None, str | None, str]:
    """``(code, param, message)`` from a provider error body.

    Only these three fields are read, and only ``code``/``param`` are ever
    logged. The message is used to recognise which knob was refused and is
    never logged or shown - a provider is free to echo the prompt in it.
    """
    try:
        body = response.json()
    except ValueError:
        return None, None, ""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None, ""
    code = error.get("code")
    param = error.get("param")
    message = error.get("message")
    return (
        str(code) if isinstance(code, str) else None,
        str(param) if isinstance(param, str) else None,
        str(message) if isinstance(message, str) else "",
    )


def _mentions_response_format(param: str | None, message: str) -> bool:
    """True when a 400 is about ``response_format`` rather than a parameter."""
    if param == "response_format":
        return True
    lowered = message.lower()
    return "response_format" in lowered or "json_schema" in lowered


def _optional_str(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _clamped_float(value: Any) -> float | None:
    try:
        return None if value is None else min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return None
