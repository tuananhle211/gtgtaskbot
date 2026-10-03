"""Outbound integrations.

Every integration is defined as a :class:`typing.Protocol` plus at least one
fake implementation. Application code depends on the protocol only, so tests
run with no credentials and no network. Real clients arrive in later
milestones; see the README for the current status of each.
"""

from meobot.integrations.base import (
    IntegrationConfig,
    build_idempotency_key,
    call_with_retries,
)

__all__ = ["IntegrationConfig", "build_idempotency_key", "call_with_retries"]
