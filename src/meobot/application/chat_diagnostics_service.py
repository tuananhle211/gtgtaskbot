"""Answering "is chat actually working" without leaking anything.

During the outage this was written for, ``/chat_status`` reported the model
name and said nothing about whether that model had ever answered - so it looked
healthy while every message failed. The two probes here close that gap by doing
the only thing that settles it: making the calls.

They are separate on purpose. Plain-text generation and structured routing fail
for different reasons and have different consequences: a gateway with no
``response_format`` support breaks routing while conversation stays perfectly
usable, and reporting one number would hide that.

What a probe may report: pass/fail, latency, an error *category*, and the
provider's negotiated capabilities. What it may never report: the API key, any
value derived from it, the Authorization header, a base URL with its path or
query string, a prompt, or a provider response body.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from meobot.core.config import Settings
from meobot.core.errors import LLMError, MeoBotError
from meobot.core.logging import get_logger
from meobot.integrations.llm.base import ChatRequest, LLMProvider, RouteRequest
from meobot.integrations.llm.capabilities import ProviderCapabilities
from meobot.integrations.llm.diagnostics import ErrorCategory

logger = get_logger(__name__)

#: Deliberately trivial. The probe tests reachability and request shape, not
#: the model's ability to hold a conversation.
PROBE_MESSAGE = "Trả lời đúng một từ: OK"
PROBE_ROUTE_MESSAGE = "Chào bạn"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The outcome of one probe."""

    name: str
    passed: bool
    latency_ms: int
    #: Log-safe category on failure; ``None`` on success.
    error_category: str | None = None

    def render(self) -> str:
        """One line for Telegram."""
        seconds = self.latency_ms / 1000
        if self.passed:
            return f"{self.name}: ✅ {seconds:.1f}s"
        return f"{self.name}: ❌ {self.error_category or ErrorCategory.UNKNOWN}"


@dataclass(frozen=True, slots=True)
class ChatTestReport:
    """Both probes plus what to do about them."""

    provider: str
    model: str
    plain_text: ProbeResult
    structured_routing: ProbeResult
    capabilities: ProviderCapabilities

    @property
    def usable_for_chat(self) -> bool:
        """Conversation works when plain text works, whatever routing did."""
        return self.plain_text.passed

    def recommendation(self) -> str:
        """What an operator should conclude, in one sentence."""
        if self.plain_text.passed and self.structured_routing.passed:
            return "Trò chuyện và định tuyến đều hoạt động bình thường."
        if self.plain_text.passed:
            return (
                "Dùng trò chuyện văn bản bình thường; định tuyến sẽ chạy bằng "
                "quy tắc từ khoá và JSON fallback. Yêu cầu thao tác vẫn an toàn: "
                "khi không chắc, MeoBot hỏi lại thay vì đoán."
            )
        if self.structured_routing.passed:
            return (
                "Định tuyến chạy được nhưng sinh văn bản thì không — kiểm tra "
                "LLM_MODEL và giới hạn token."
            )
        return (
            f"Cả hai đều lỗi. {ErrorCategory.hint(self.plain_text.error_category)} "
            "Kiểm tra LLM_API_KEY, LLM_MODEL và LLM_BASE_URL."
        )


class ChatDiagnosticsService:
    """Runs the two probes against the configured provider.

    Args:
        llm: The live provider. The probes use the same client and the same
            configuration the bot uses, which is the point - a probe against a
            freshly built client would not reproduce the failure.
        settings: Used only to report configuration, never to bypass it.
    """

    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    async def run(self) -> ChatTestReport:
        """Probe plain-text generation and structured routing, in that order."""
        plain = await self._probe_plain_text()
        routing = await self._probe_routing()
        report = ChatTestReport(
            provider=self._llm.name,
            model=self._llm.model,
            plain_text=plain,
            structured_routing=routing,
            capabilities=getattr(self._llm, "capabilities", ProviderCapabilities()),
        )
        logger.info(
            "chat_test_completed",
            extra={
                "provider": report.provider,
                "model": report.model,
                "plain_text_passed": plain.passed,
                "plain_text_latency_ms": plain.latency_ms,
                "routing_passed": routing.passed,
                "routing_latency_ms": routing.latency_ms,
                "plain_text_error": plain.error_category,
                "routing_error": routing.error_category,
            },
        )
        return report

    async def _probe_plain_text(self) -> ProbeResult:
        """One minimal plain-text generation."""
        started = time.monotonic()
        try:
            reply = await self._llm.generate_chat_reply(
                ChatRequest(message=PROBE_MESSAGE, max_output_tokens=64, minimal=True)
            )
        except MeoBotError as exc:
            return self._failure("Sinh văn bản", started, exc)
        except Exception:
            logger.exception("chat_probe_plain_text_crashed")
            return ProbeResult(
                name="Sinh văn bản",
                passed=False,
                latency_ms=_elapsed(started),
                error_category=ErrorCategory.UNKNOWN,
            )
        passed = bool(reply.text.strip())
        return ProbeResult(
            name="Sinh văn bản",
            passed=passed,
            latency_ms=_elapsed(started),
            error_category=None if passed else ErrorCategory.EMPTY_RESPONSE,
        )

    async def _probe_routing(self) -> ProbeResult:
        """One structured routing call."""
        started = time.monotonic()
        try:
            await self._llm.route_message(
                RouteRequest(message=PROBE_ROUTE_MESSAGE, tool_names=[], max_output_tokens=128)
            )
        except MeoBotError as exc:
            return self._failure("Định tuyến có cấu trúc", started, exc)
        except Exception:
            logger.exception("chat_probe_routing_crashed")
            return ProbeResult(
                name="Định tuyến có cấu trúc",
                passed=False,
                latency_ms=_elapsed(started),
                error_category=ErrorCategory.UNKNOWN,
            )
        return ProbeResult(name="Định tuyến có cấu trúc", passed=True, latency_ms=_elapsed(started))

    @staticmethod
    def _failure(name: str, started: float, error: MeoBotError) -> ProbeResult:
        """Classify a typed failure without quoting the provider."""
        category = error.details.get("category")
        if not isinstance(category, str):
            category = (
                error.details.get("parser_failure")
                if isinstance(error.details.get("parser_failure"), str)
                else None
            )
        if category is None:
            category = (
                ErrorCategory.UNKNOWN
                if not isinstance(error, LLMError)
                else (ErrorCategory.BAD_REQUEST)
            )
        return ProbeResult(
            name=name, passed=False, latency_ms=_elapsed(started), error_category=category
        )


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
