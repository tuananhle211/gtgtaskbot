"""Turning a Graph error into one of the seven things MeoBot can act on.

The mapping lives here, at the transport boundary, so that nothing above it ever
matches on a Graph error code. What a channel manager needs to know is which of
three things to do - reconnect, wait, or tell an engineer - and that is what
:class:`~meobot.domain.pr.channel_connections.PrChannelSyncErrorCode`
distinguishes.

Meta's error codes, and why the subcodes matter
------------------------------------------------

Graph reports almost every authorization problem as ``code 190``
(``OAuthException``) and then says what *kind* in ``error_subcode``. Those
subcodes are not decoration: 458 means the person removed the app, 460 means
they changed their password, 463 means the token simply aged out. All three are
``AUTH_REQUIRED`` - somebody must reauthorize - but 190 with no subcode can also
be a malformed request from MeoBot's own side, which is not the user's problem
and must not tell them to reconnect.

The rate-limit codes are similarly spread out: 4 is app-level, 17 is user-level,
32 and 613 are Page-level, and the 80xxx family is per-product. All of them mean
the same thing to a scheduler - come back later - which is exactly why they are
collapsed here rather than in a service.

Nothing carries a Graph payload forward. A provider's own error prose is
somebody else's text about somebody else's system, it changes without notice,
and it ends up on a Vietnamese screen if it is allowed to travel.
"""

from __future__ import annotations

from typing import Any

from meobot.core.errors import IntegrationError
from meobot.domain.pr.channel_connections import (
    ChannelSyncProviderError,
    PrChannelSyncErrorCode,
)


class MetaApiError(IntegrationError, ChannelSyncProviderError):
    """A Graph call failed, already classified.

    Both an :class:`~meobot.core.errors.IntegrationError` - so the existing
    integration logging conventions apply unchanged - and a
    :class:`~meobot.domain.pr.channel_connections.ChannelSyncProviderError`, so
    the sync orchestration catches it without importing anything from this
    package. That second base is what keeps ``PrChannelSyncService`` free of the
    words "Meta" and "Graph", exactly as it is free of "YouTube".
    """

    code = "integration.meta"

    def __init__(
        self,
        message: str,
        *,
        error_code: PrChannelSyncErrorCode,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, provider="meta", details={"error_code": error_code.value})
        self.error_code = error_code
        self.status_code = status_code


#: ``OAuthException`` subcodes that mean the grant is genuinely gone and only a
#: person can restore it. Listed explicitly rather than "any subcode", because a
#: 190 with no subcode is usually a malformed request from this repository and
#: telling somebody to reauthorize over MeoBot's own bug wastes their time and
#: rotates a working credential for nothing.
DEAD_GRANT_SUBCODES: frozenset[int] = frozenset(
    {
        458,  # the person removed the app
        459,  # checkpointed account
        460,  # password changed, session invalidated
        463,  # token expired
        464,  # unconfirmed user
        467,  # invalid access token
        492,  # the person no longer has a role on the Page
    }
)

#: Every code Meta uses for "too many requests", across app, user, Page and
#: product-specific limits. They all mean the same thing to a scheduler.
RATE_LIMIT_CODES: frozenset[int] = frozenset({4, 17, 32, 613, 80001, 80002, 80003, 80004})

#: Permission problems: the token is valid but was not granted what this call
#: needs. Distinct from a rate limit because the fix is a person reauthorizing
#: with a wider consent, not waiting.
PERMISSION_CODES: frozenset[int] = frozenset({10, 200, 210, 230, 299})

#: Meta's own "we had a problem" codes.
TRANSIENT_CODES: frozenset[int] = frozenset({1, 2, 341})


def classify_graph_error(status_code: int, payload: Any = None) -> PrChannelSyncErrorCode:
    """Map a Graph failure onto a safe error class.

    Reads ``error.code`` and ``error.error_subcode`` when the body is shaped the
    way Graph shapes one, and falls back to the HTTP status when it is not - a
    proxy returning an HTML 502 is a real thing that happens and must classify
    as ``PROVIDER_UNAVAILABLE`` rather than crash the classifier.
    """
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    subcode = error.get("error_subcode") if isinstance(error, dict) else None

    if isinstance(code, int):
        if code in RATE_LIMIT_CODES:
            return PrChannelSyncErrorCode.RATE_LIMITED
        if code in PERMISSION_CODES:
            return PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
        if code in TRANSIENT_CODES:
            return PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE
        if code == 190:
            if isinstance(subcode, int) and subcode in DEAD_GRANT_SUBCODES:
                return PrChannelSyncErrorCode.AUTH_REQUIRED
            # A bare 190 on a 401 is still "this token does not work".
            return (
                PrChannelSyncErrorCode.AUTH_REQUIRED
                if status_code == 401
                else PrChannelSyncErrorCode.BAD_RESPONSE
            )
        if code == 100:
            # "Unsupported get request" / object not accessible. Subcode 33 is
            # specifically "the object does not exist or you cannot see it",
            # which for a bound Page means the binding is wrong.
            return (
                PrChannelSyncErrorCode.INVALID_ACCOUNT
                if subcode == 33
                else PrChannelSyncErrorCode.BAD_RESPONSE
            )

    if status_code == 401:
        return PrChannelSyncErrorCode.AUTH_REQUIRED
    if status_code == 403:
        return PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
    if status_code == 404:
        return PrChannelSyncErrorCode.INVALID_ACCOUNT
    if status_code == 429:
        return PrChannelSyncErrorCode.RATE_LIMITED
    if 500 <= status_code < 600:
        return PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE
    if 400 <= status_code < 500:
        return PrChannelSyncErrorCode.BAD_RESPONSE
    return PrChannelSyncErrorCode.UNKNOWN


__all__: list[str] = [
    "DEAD_GRANT_SUBCODES",
    "PERMISSION_CODES",
    "RATE_LIMIT_CODES",
    "TRANSIENT_CODES",
    "MetaApiError",
    "classify_graph_error",
]
