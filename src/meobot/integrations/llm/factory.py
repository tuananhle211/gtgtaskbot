"""Provider selection from configuration."""

from __future__ import annotations

from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.llm.base import LLMProvider
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.integrations.llm.openai_compatible import (
    DEFAULT_BASE_URL,
    OpenAICompatibleProvider,
)
from meobot.integrations.llm.openai_compatible import (
    PROVIDER as OPENAI_PROVIDER,
)

#: Providers implemented today. ``openai`` also covers OpenAI-compatible
#: gateways (OpenRouter, Together, a self-hosted vLLM) via ``LLM_BASE_URL``.
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"fake", OPENAI_PROVIDER})


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Return the provider named by ``LLM_PROVIDER``.

    Raises:
        ConfigurationError: When an unimplemented provider is requested, or
            when the real provider is requested without a key or a model. The
            application refuses to start rather than silently downgrading to
            the fake provider in production.
    """
    provider = settings.llm_provider.strip().lower()
    if provider == "fake":
        return FakeLLMProvider()

    if provider == OPENAI_PROVIDER:
        if settings.llm_api_key is None:
            raise ConfigurationError(
                "LLM_API_KEY is required when LLM_PROVIDER=openai.",
                details={"provider": provider},
            )
        if not settings.llm_model:
            raise ConfigurationError(
                "LLM_MODEL is required when LLM_PROVIDER=openai (no model is hardcoded).",
                details={"provider": provider},
            )
        return OpenAICompatibleProvider(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            base_url=settings.llm_base_url or DEFAULT_BASE_URL,
            config=IntegrationConfig(
                provider=OPENAI_PROVIDER,
                timeout_seconds=settings.llm_timeout_seconds,
                max_retries=settings.http_max_retries,
            ),
        )

    raise ConfigurationError(
        f"LLM_PROVIDER={provider!r} is not implemented. Supported: {sorted(SUPPORTED_PROVIDERS)}.",
        details={"requested": provider},
    )
