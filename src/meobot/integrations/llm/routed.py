"""The provider the app actually holds: one per purpose, with fallbacks.

"Kết nối AI" (0064) lets the OWNER register several AI services and chain
them per purpose (:class:`~meobot.domain.ai.models.AiPurpose`). This class is
the :class:`~meobot.integrations.llm.base.LLMProvider` every caller already
receives; each call is sent to the first provider of its purpose's chain and,
when that one fails (:class:`~meobot.core.errors.LLMError` - a refused key, an
exhausted quota, a timeout, an unusable answer), to the next.

The chains come from a *loader* (the application layer reads the database and
decrypts the keys), cached for ``ttl_seconds`` so a change on the screen
reaches every process - API, bot, worker - within that time without a
restart. A loader failure keeps the last table; with no table at all the
environment provider (``LLM_*``) answers, exactly as before 0064. That
provider is also the last link of every chain, unless it is the offline
``fake`` one.

This module receives providers, never a database session or a key.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from meobot.core.errors import LLMError
from meobot.core.logging import get_logger
from meobot.domain.ai.models import AiPurpose, purpose_of_task
from meobot.domain.conversations.decision import ConversationDecision
from meobot.domain.policy.models import ActionPlan
from meobot.integrations.llm.base import (
    ChatReply,
    ChatRequest,
    ClarificationReply,
    ClarificationRequest,
    ConversationSummaryResult,
    DecisionRequest,
    LLMProvider,
    MessageRoute,
    PlanningRequest,
    RouteRequest,
    StructuredRequest,
    StructuredResponse,
    SummaryRequest,
)
from meobot.integrations.llm.capabilities import ProviderCapabilities
from meobot.integrations.llm.diagnostics import ProviderDiagnostics

logger = get_logger(__name__)

T = TypeVar("T")

#: How long a loaded table is used before the loader is asked again.
DEFAULT_TTL_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class RouteTable:
    """Per purpose, the providers to try, in order. A purpose missing here
    (or with an empty chain) uses ``DEFAULT``'s."""

    chains: Mapping[AiPurpose, tuple[LLMProvider, ...]] = field(default_factory=dict)


Loader = Callable[[], Awaitable[RouteTable]]


class RoutedLLMProvider:
    """An :class:`LLMProvider` that picks, per call, who answers."""

    def __init__(
        self,
        *,
        fallback: LLMProvider,
        loader: Loader | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fallback = fallback
        self._loader = loader
        self._ttl = ttl_seconds
        self._clock = clock
        self._table = RouteTable()
        self._loaded_at: float | None = None

    # --- the table ------------------------------------------------------------

    async def _current(self) -> RouteTable:
        if self._loader is None:
            return self._table
        now = self._clock()
        if self._loaded_at is not None and now - self._loaded_at < self._ttl:
            return self._table
        try:
            self._table = await self._loader()
        except Exception as exc:  # the last table keeps serving
            logger.warning("ai_routes_load_failed", extra={"error": type(exc).__name__})
        self._loaded_at = now
        return self._table

    def refresh(self) -> None:
        """Read the table again on the next call (after a change on the screen)."""
        self._loaded_at = None

    async def chain(self, purpose: AiPurpose) -> list[LLMProvider]:
        """Who is asked for ``purpose``, in order."""
        table = await self._current()
        chain = list(table.chains.get(purpose) or table.chains.get(AiPurpose.DEFAULT) or ())
        if not chain or self._fallback.name != "fake":
            chain.append(self._fallback)
        return chain

    async def _run(self, purpose: AiPurpose, task: str, call: Callable[[Any], Awaitable[T]]) -> T:
        chain = await self.chain(purpose)
        failure: LLMError | None = None
        for position, provider in enumerate(chain):
            try:
                return await call(provider)
            except LLMError as exc:
                failure = exc
                if position + 1 < len(chain):
                    logger.warning(
                        "ai_fallback",
                        extra={
                            "task_type": task,
                            "purpose": purpose.value,
                            "provider": provider.name,
                            "model": provider.model,
                            "category": (exc.details or {}).get("category"),
                        },
                    )
        assert failure is not None
        raise failure

    # --- identity -------------------------------------------------------------

    def _primary(self) -> LLMProvider:
        chain = self._table.chains.get(AiPurpose.DEFAULT) or ()
        return chain[0] if chain else self._fallback

    @property
    def name(self) -> str:
        return self._primary().name

    @property
    def model(self) -> str:
        return self._primary().model

    @property
    def diagnostics(self) -> ProviderDiagnostics:
        return self._primary().diagnostics

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What the default AI was seen to accept (for ``/chat_status``)."""
        found = getattr(self._primary(), "capabilities", None)
        return found if isinstance(found, ProviderCapabilities) else ProviderCapabilities()

    # --- the assistant ----------------------------------------------------------

    async def route_message(self, request: RouteRequest) -> MessageRoute:
        return await self._run(
            AiPurpose.ASSISTANT, "route_message", lambda p: p.route_message(request)
        )

    async def generate_chat_reply(self, request: ChatRequest) -> ChatReply:
        return await self._run(
            AiPurpose.ASSISTANT, "generate_chat_reply", lambda p: p.generate_chat_reply(request)
        )

    async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
        return await self._run(
            AiPurpose.ASSISTANT, "plan_tool_action", lambda p: p.plan_tool_action(request)
        )

    async def generate_clarification(self, request: ClarificationRequest) -> ClarificationReply:
        return await self._run(
            AiPurpose.ASSISTANT,
            "generate_clarification",
            lambda p: p.generate_clarification(request),
        )

    async def summarize_conversation(self, request: SummaryRequest) -> ConversationSummaryResult:
        return await self._run(
            AiPurpose.ASSISTANT,
            "summarize_conversation",
            lambda p: p.summarize_conversation(request),
        )

    async def plan(self, request: PlanningRequest) -> ActionPlan:
        return await self._run(AiPurpose.ASSISTANT, "plan", lambda p: p.plan(request))

    async def decide(self, request: DecisionRequest) -> ConversationDecision:
        return await self._run(AiPurpose.ASSISTANT, "decide", lambda p: p.decide(request))

    async def summarize(self, request: SummaryRequest) -> str:
        return await self._run(AiPurpose.ASSISTANT, "summarize", lambda p: p.summarize(request))

    # --- structured tasks -------------------------------------------------------

    async def review_pr_content(self, request: Any) -> Any:
        return await self._run(
            AiPurpose.PR_REVIEW, "pr_full_review", lambda p: p.review_pr_content(request)
        )

    async def review_script(self, request: Any) -> Any:
        return await self._run(
            AiPurpose.PR_REVIEW, "script_review", lambda p: p.review_script(request)
        )

    async def propose_sheet_mapping(self, request: Any) -> Any:
        return await self._run(
            AiPurpose.DEFAULT, "sheet_mapping", lambda p: p.propose_sheet_mapping(request)
        )

    async def complete_structured(self, request: StructuredRequest) -> StructuredResponse:
        return await self._run(
            purpose_of_task(request.task),
            request.task,
            lambda p: p.complete_structured(request),
        )

    # --- lifecycle --------------------------------------------------------------

    async def aclose(self) -> None:
        closer = getattr(self._fallback, "aclose", None)
        if callable(closer):
            await closer()


__all__ = ["DEFAULT_TTL_SECONDS", "Loader", "RouteTable", "RoutedLLMProvider"]
