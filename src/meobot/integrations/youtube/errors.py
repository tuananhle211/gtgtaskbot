"""Turning whatever Google said into one of seven things MeoBot can act on.

The mapping lives here, at the transport boundary, so that no service above it
ever pattern-matches on an HTTP status or a Google error string. What a channel
manager needs to know is which of three things to do - reconnect, wait, or tell
an engineer - and that is what
:class:`~meobot.domain.pr.channel_connections.PrChannelSyncErrorCode`
distinguishes.

Nothing here carries a response body forward. A provider's own error prose is
somebody else's text about somebody else's system, it changes without notice,
and it ends up on a Vietnamese screen if it is allowed to travel.
"""

from __future__ import annotations

from meobot.core.errors import IntegrationError
from meobot.domain.pr.channel_connections import (
    ChannelSyncProviderError,
    PrChannelSyncErrorCode,
)


class YouTubeApiError(IntegrationError, ChannelSyncProviderError):
    """A YouTube call failed, already classified.

    Both an :class:`~meobot.core.errors.IntegrationError` - so the existing
    integration retry and logging conventions apply unchanged - and a
    :class:`~meobot.domain.pr.channel_connections.ChannelSyncProviderError`, so
    the sync orchestration can catch it without importing anything from this
    package. That second base is what keeps ``PrChannelSyncService`` free of the
    word "YouTube".

    Args:
        message: For a log line. English, structural, never a provider payload.
        error_code: The safe class this failure belongs to.
        status_code: The HTTP status, for logs only.
    """

    code = "integration.youtube"

    def __init__(
        self,
        message: str,
        *,
        error_code: PrChannelSyncErrorCode,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, provider="youtube", details={"error_code": error_code.value})
        self.error_code = error_code
        self.status_code = status_code


#: Google's OAuth error identifiers that mean "this grant is gone".
#:
#: Matched exactly rather than by substring: ``invalid_grant`` is the one that
#: means a refresh token has been revoked or has expired through disuse, and it
#: is the only OAuth failure that should ever cost a user their connection.
#: Treating a generic ``invalid_request`` - a bug in MeoBot's own request - as
#: "your token is gone" would tell people to reauthorize over and over while the
#: real fault sat in this repository.
DEAD_GRANT_ERRORS: frozenset[str] = frozenset({"invalid_grant", "unauthorized_client"})


def classify_status(status_code: int, *, payload: object = None) -> PrChannelSyncErrorCode:
    """Map an HTTP status onto a safe error class.

    ``403`` is the interesting one and is why ``payload`` is inspected at all:
    Google returns it both for "you have run out of quota" and for "this token
    does not carry the scope you need", and those need opposite responses - one
    is transient and retried on the next cadence, the other needs a person to
    reauthorize with a wider consent. The reason string is read only to tell
    those apart, and is never stored.
    """
    if status_code in {401}:
        return PrChannelSyncErrorCode.AUTH_REQUIRED
    if status_code == 403:
        reason = _reason(payload)
        if reason in {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded"}:
            return PrChannelSyncErrorCode.RATE_LIMITED
        if reason in {"insufficientPermissions", "forbidden", "insufficientScope"}:
            return PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
        # An unrecognised 403 is treated as a scope problem rather than as a
        # quota one: resting the connection for a day would hide a consent
        # mistake behind an error that looks like it will fix itself.
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


def classify_oauth_error(error: str | None) -> PrChannelSyncErrorCode:
    """Map a Google OAuth ``error`` identifier onto a safe class."""
    if error and error.strip() in DEAD_GRANT_ERRORS:
        return PrChannelSyncErrorCode.AUTH_REQUIRED
    return PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE


def _reason(payload: object) -> str | None:
    """The first ``error.errors[].reason`` Google supplied, if it is shaped so.

    Defensive at every level: this reads an untrusted response, and a payload
    that is a list, a string or ``None`` must produce ``None`` rather than an
    exception inside error handling.
    """
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    status = error.get("status")
    if isinstance(status, str) and status:
        mapped = {"RESOURCE_EXHAUSTED": "quotaExceeded", "PERMISSION_DENIED": "forbidden"}
        if status in mapped:
            return mapped[status]
    errors = error.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict) and isinstance(item.get("reason"), str):
                return str(item["reason"])
    return None


__all__: list[str] = [
    "DEAD_GRANT_ERRORS",
    "YouTubeApiError",
    "classify_oauth_error",
    "classify_status",
]
