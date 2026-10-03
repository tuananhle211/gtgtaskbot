"""What an OpenAI-compatible endpoint actually accepts.

"OpenAI-compatible" is a family, not a contract. Gateways disagree about
``response_format``, and OpenAI's own newer models disagree with OpenAI's older
ones about the *parameter names*. The outage this module exists to prevent was
exactly that: the configured model rejected ``max_tokens`` (it wants
``max_completion_tokens``) and rejected any ``temperature`` other than the
default. Both are HTTP 400, both were raised as a generic ``LLMError``, and
because every task went through one structured-output call, every natural
message failed while every slash command kept working.

The fix is to treat capabilities as *learned*, not assumed:

* start optimistic (schema output, tunable temperature, ``max_tokens``);
* when the provider says "unsupported parameter X", record it and retry the
  same request with X dropped or renamed;
* keep the downgrade for the life of the process, so the cost is one wasted
  round-trip after start-up rather than one per message.

Nothing here hardcodes a model name. A capability is only ever downgraded
because the provider said so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Parameter name newer OpenAI models require in place of ``max_tokens``.
MAX_COMPLETION_TOKENS = "max_completion_tokens"
MAX_TOKENS = "max_tokens"


class OutputMode(StrEnum):
    """How strongly the answer's shape is constrained.

    Ordered strongest to weakest; :meth:`ProviderCapabilities.best_mode` walks
    down this order until it finds something the provider supports.
    """

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    TEXT = "text"


#: Fallback order for a task that needs a JSON object back.
STRUCTURED_MODE_ORDER: tuple[OutputMode, ...] = (
    OutputMode.JSON_SCHEMA,
    OutputMode.JSON_OBJECT,
    OutputMode.TEXT,
)


@dataclass(slots=True)
class ProviderCapabilities:
    """Mutable record of what this endpoint has been observed to accept.

    Args:
        supports_plain_text: Ordinary text completion. Assumed always true;
            if this is false the provider is unusable for conversation.
        supports_json_mode: ``response_format={"type": "json_object"}``.
        supports_json_schema: ``response_format={"type": "json_schema", ...}``.
        supports_tool_calling: Native ``tools``/``tool_choice``. MeoBot does not
            use it - planning goes through the policy engine, not through the
            provider - but it is reported by ``/chat_test`` so an operator can
            see what the gateway offers.
        supports_temperature: Whether a non-default ``temperature`` is accepted.
        max_tokens_parameter: Which output-length parameter this endpoint wants.
    """

    supports_plain_text: bool = True
    supports_json_mode: bool = True
    supports_json_schema: bool = True
    supports_tool_calling: bool = True
    supports_temperature: bool = True
    max_tokens_parameter: str = MAX_TOKENS
    #: Adjustments made so far, newest last. Log-safe labels only.
    downgrades: list[str] = field(default_factory=list)

    def best_mode(self, wanted: OutputMode) -> OutputMode:
        """The strongest supported mode no stronger than ``wanted``."""
        start = STRUCTURED_MODE_ORDER.index(wanted)
        for mode in STRUCTURED_MODE_ORDER[start:]:
            if self.supports(mode):
                return mode
        return OutputMode.TEXT

    def supports(self, mode: OutputMode) -> bool:
        """True when ``mode`` has not been ruled out by the provider."""
        if mode is OutputMode.JSON_SCHEMA:
            return self.supports_json_schema
        if mode is OutputMode.JSON_OBJECT:
            return self.supports_json_mode
        return self.supports_plain_text

    def weaken(self, mode: OutputMode) -> OutputMode | None:
        """Record that ``mode`` is unsupported and return the next one to try.

        Returns ``None`` when there is nothing weaker left, which only happens
        if plain text itself was refused.
        """
        if mode is OutputMode.JSON_SCHEMA:
            self.supports_json_schema = False
            self._note("response_format:json_schema")
            return self.best_mode(OutputMode.JSON_OBJECT)
        if mode is OutputMode.JSON_OBJECT:
            self.supports_json_mode = False
            self._note("response_format:json_object")
            return self.best_mode(OutputMode.TEXT)
        self.supports_plain_text = False
        self._note("response_format:text")
        return None

    def adjust_for(self, *, code: str | None, param: str | None, message: str) -> str | None:
        """React to a 400 that names an unsupported parameter.

        Args:
            code: The provider's ``error.code`` (``unsupported_parameter``,
                ``unsupported_value``, ...).
            param: The provider's ``error.param`` (``max_tokens``, ...).
            message: The provider's ``error.message``. Read only to recognise
                which knob is being refused; never logged and never shown.

        Returns:
            A short log-safe label for the adjustment made, or ``None`` when
            this error is not about a parameter we can drop - in which case the
            caller must surface it rather than retry.
        """
        haystack = f"{param or ''} {message}".lower()

        if self._is_max_tokens(param, haystack) and self.max_tokens_parameter == MAX_TOKENS:
            self.max_tokens_parameter = MAX_COMPLETION_TOKENS
            return self._note(f"max_tokens->{MAX_COMPLETION_TOKENS}")

        if self._is_temperature(param, haystack) and self.supports_temperature:
            self.supports_temperature = False
            return self._note("temperature:dropped")

        if code == "unsupported_parameter" and param == MAX_COMPLETION_TOKENS:
            # A gateway that wants the *old* name. Symmetry costs one line.
            self.max_tokens_parameter = MAX_TOKENS
            return self._note(f"{MAX_COMPLETION_TOKENS}->max_tokens")

        return None

    @staticmethod
    def _is_max_tokens(param: str | None, haystack: str) -> bool:
        if param == MAX_TOKENS:
            return True
        return MAX_COMPLETION_TOKENS in haystack and MAX_TOKENS in haystack

    @staticmethod
    def _is_temperature(param: str | None, haystack: str) -> bool:
        return param == "temperature" or "'temperature'" in haystack

    def _note(self, label: str) -> str:
        if label not in self.downgrades:
            self.downgrades.append(label)
        return label

    def as_dict(self) -> dict[str, object]:
        """Log-safe snapshot, used by ``/chat_test`` and structured logging."""
        return {
            "supports_plain_text": self.supports_plain_text,
            "supports_json_mode": self.supports_json_mode,
            "supports_json_schema": self.supports_json_schema,
            "supports_tool_calling": self.supports_tool_calling,
            "supports_temperature": self.supports_temperature,
            "max_tokens_parameter": self.max_tokens_parameter,
            "downgrades": list(self.downgrades),
        }
