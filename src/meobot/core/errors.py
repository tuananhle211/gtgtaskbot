"""Typed exception hierarchy.

Every layer raises these instead of bare :class:`Exception`, so boundaries
(API routes, bot handlers, Celery tasks) can map them to responses without
inspecting strings. Nothing in this module ever swallows an exception.
"""

from __future__ import annotations

from typing import Any


class MeoBotError(Exception):
    """Base class for every error raised by MeoBot itself."""

    code: str = "meobot_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def __str__(self) -> str:
        return self.message


# --- Configuration ---------------------------------------------------------
class ConfigurationError(MeoBotError):
    """Required configuration is missing or invalid."""

    code = "configuration_error"


# --- Domain ----------------------------------------------------------------
class DomainError(MeoBotError):
    """Base class for business-rule violations."""

    code = "domain_error"


class ValidationError(DomainError):
    """Input violates a domain invariant (e.g. rubric weights != 100)."""

    code = "validation_error"


class NotFoundError(DomainError):
    """A referenced entity does not exist."""

    code = "not_found"


class ConflictError(DomainError):
    """The operation conflicts with existing state (duplicate version, ...)."""

    code = "conflict"


class WorkflowStateError(DomainError):
    """The requested transition is not legal from the current workflow state."""

    code = "invalid_workflow_state"


# --- Authorisation ---------------------------------------------------------
class AuthorizationError(MeoBotError):
    """The actor is not allowed to perform the action."""

    code = "forbidden"


class NotRegisteredError(AuthorizationError):
    """The Telegram account is unknown to MeoBot."""

    code = "not_registered"


class ConfirmationRequiredError(MeoBotError):
    """A high-risk action needs explicit human confirmation first."""

    code = "confirmation_required"


class ConfirmationExpiredError(MeoBotError):
    """The confirmation token is expired or already used."""

    code = "confirmation_expired"


# --- Tools / LLM -----------------------------------------------------------
class ToolError(MeoBotError):
    """Base class for tool-layer failures."""

    code = "tool_error"


class ToolNotFoundError(ToolError):
    """The requested tool is not registered."""

    code = "tool_not_found"


class ToolArgumentError(ToolError):
    """Tool arguments failed schema validation."""

    code = "tool_argument_error"


class ToolExecutionError(ToolError):
    """The tool handler failed while executing."""

    code = "tool_execution_error"


class LLMError(MeoBotError):
    """The LLM provider failed or produced an unusable plan."""

    code = "llm_error"


# --- Integrations ----------------------------------------------------------
class IntegrationError(MeoBotError):
    """Base class for third-party integration failures."""

    code = "integration_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.provider = provider


class IntegrationTimeoutError(IntegrationError):
    """The provider did not answer within the configured timeout."""

    code = "integration_timeout"


class IntegrationAuthError(IntegrationError):
    """Credentials are missing, invalid or expired."""

    code = "integration_auth_error"


class IntegrationRateLimitError(IntegrationError):
    """The provider rate-limited us."""

    code = "integration_rate_limited"


class IntegrationNotConfiguredError(IntegrationError):
    """A real client was requested but no credentials are configured."""

    code = "integration_not_configured"
