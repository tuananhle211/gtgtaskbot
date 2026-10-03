"""Shared integration plumbing: timeouts, retries, idempotency, safe logging.

Rules every integration must follow:

* **Timeout** - no unbounded awaits; the caller supplies :class:`IntegrationConfig`.
* **Retry boundary** - retries live here, not scattered in call sites, and only
  transient failures (timeout, rate limit) are retried.
* **Typed exceptions** - vendor errors are translated into
  :mod:`meobot.core.errors` types at the client boundary.
* **Redacted logging** - never log tokens, DSNs or full payloads.
* **Idempotency** - any write must carry a key so a retry cannot double-post.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from meobot.core.errors import (
    IntegrationError,
    IntegrationRateLimitError,
    IntegrationTimeoutError,
)
from meobot.core.logging import get_logger

logger = get_logger(__name__)

#: Failures worth retrying. Auth and validation errors are never retried.
TRANSIENT_ERRORS: tuple[type[IntegrationError], ...] = (
    IntegrationTimeoutError,
    IntegrationRateLimitError,
)


@dataclass(frozen=True, slots=True)
class IntegrationConfig:
    """Per-integration timeout and retry budget."""

    provider: str
    timeout_seconds: float = 15.0
    max_retries: int = 2
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8.0


def build_idempotency_key(*parts: str | int | None) -> str:
    """Deterministic key for a write operation.

    The same logical action (same entity, same version, same target) always
    produces the same key, so a retried publish cannot create a duplicate post.
    """
    material = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


async def call_with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    config: IntegrationConfig,
    operation_name: str,
    jitter: bool = True,
) -> T:
    """Run ``operation`` with a timeout and bounded exponential backoff.

    Args:
        operation: Zero-argument coroutine factory performing one attempt.
        config: Timeout and retry budget.
        operation_name: Short label used in logs (never contains payloads).
        jitter: Add randomised jitter to the backoff to avoid thundering herds.

    Returns:
        Whatever ``operation`` returns.

    Raises:
        IntegrationTimeoutError: When every attempt timed out.
        IntegrationError: Any non-transient integration failure, re-raised
            unchanged after being logged.
    """
    last_error: IntegrationError | None = None

    for attempt in range(config.max_retries + 1):
        try:
            async with asyncio.timeout(config.timeout_seconds):
                return await operation()
        except TimeoutError as exc:
            last_error = IntegrationTimeoutError(
                f"{operation_name} timed out after {config.timeout_seconds}s",
                provider=config.provider,
                details={"attempt": attempt + 1},
            )
            logger.warning(
                "integration_timeout",
                extra={
                    "provider": config.provider,
                    "operation": operation_name,
                    "attempt": attempt + 1,
                },
            )
            if attempt >= config.max_retries:
                raise last_error from exc
        except TRANSIENT_ERRORS as exc:
            last_error = exc
            logger.warning(
                "integration_transient_error",
                extra={
                    "provider": config.provider,
                    "operation": operation_name,
                    "attempt": attempt + 1,
                    "error_code": exc.code,
                },
            )
            if attempt >= config.max_retries:
                raise
        except IntegrationError:
            # Permanent failure (auth, validation, not configured): do not retry.
            logger.error(
                "integration_permanent_error",
                extra={"provider": config.provider, "operation": operation_name},
            )
            raise

        await asyncio.sleep(_backoff_delay(config, attempt, jitter=jitter))

    # Unreachable: the loop either returns or raises.
    raise last_error or IntegrationError(  # pragma: no cover - defensive
        f"{operation_name} failed", provider=config.provider
    )


def _backoff_delay(config: IntegrationConfig, attempt: int, *, jitter: bool) -> float:
    """Exponential backoff capped at ``backoff_max_seconds``."""
    delay: float = min(config.backoff_base_seconds * (2**attempt), config.backoff_max_seconds)
    if jitter:
        delay *= 0.5 + random.random() / 2  # noqa: S311 - jitter, not cryptography
    return delay
