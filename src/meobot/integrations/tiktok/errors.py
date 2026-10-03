"""Turning a TikTok failure into one of the seven things MeoBot can act on.

The mapping lives here, at the transport boundary, so that nothing above it ever
matches on a TikTok error string. What a channel manager needs to know is which
of three things to do - reconnect, wait, or tell an engineer - and that is what
:class:`~meobot.domain.pr.channel_connections.PrChannelSyncErrorCode`
distinguishes.

The thing that makes TikTok different from Graph
-------------------------------------------------

**TikTok answers a failed call with HTTP 200.**

Every Open API v2 response carries an ``error`` object, and a *successful* one
has ``error.code == "ok"``. A request for a field the grant does not cover comes
back ``200 OK`` with ``error.code == "scope_not_authorized"`` and an empty
``data``. A client that classified on the status line would record that as a
success carrying no numbers - which is the single worst outcome available here,
because a snapshot of nulls written as a success looks exactly like an account
that lost all its metrics.

So :func:`classify_tiktok_error` reads ``error.code`` **first** and the HTTP
status only as a fallback, and
:meth:`~meobot.integrations.tiktok.client.TikTokApiClient` treats any
``error.code`` other than ``ok`` as a failure regardless of status.

The OAuth endpoints are shaped differently again
-------------------------------------------------

``/oauth/token/`` and ``/oauth/revoke/`` do **not** use the ``error`` object.
They answer in the OAuth 2.0 style - a top-level ``error`` *string* and an
``error_description`` - which is a third shape to read. :func:`oauth_error_code`
handles that one, and the two are kept apart rather than merged into a
best-effort parser that would quietly mis-read both.

Nothing carries a TikTok payload forward. A provider's own error prose is
somebody else's text about somebody else's system, it changes without notice,
and it ends up on a Vietnamese screen if it is allowed to travel. In particular
``log_id`` - which TikTok puts in every error and which support asks for - is
logged and never returned.
"""

from __future__ import annotations

from typing import Any

from meobot.core.errors import IntegrationError
from meobot.domain.pr.channel_connections import (
    ChannelSyncProviderError,
    PrChannelSyncErrorCode,
)


class TikTokApiError(IntegrationError, ChannelSyncProviderError):
    """A TikTok call failed, already classified.

    Both an :class:`~meobot.core.errors.IntegrationError` - so the existing
    integration logging conventions apply unchanged - and a
    :class:`~meobot.domain.pr.channel_connections.ChannelSyncProviderError`, so
    the sync orchestration catches it without importing anything from this
    package. That second base is what keeps ``PrChannelSyncService`` free of the
    word "TikTok", exactly as it is free of "Meta" and "YouTube".

    Carries no token, no authorization code and no TikTok response body. The
    message is MeoBot's own English sentence for a log; what a person sees is
    composed from :attr:`error_code` by
    :func:`~meobot.application.pr_channel_sync_service.error_message`.
    """

    code = "integration.tiktok"

    def __init__(
        self,
        message: str,
        *,
        error_code: PrChannelSyncErrorCode,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, provider="tiktok", details={"error_code": error_code.value})
        self.error_code = error_code
        self.status_code = status_code


#: The one ``error.code`` that means nothing went wrong.
#:
#: TikTok also sends an empty string on some endpoints when there is genuinely
#: no error, so both spellings of "fine" are accepted. Anything else is a
#: failure even on a 200.
OK_CODES: frozenset[str] = frozenset({"ok", ""})

#: Open API error codes that mean **the grant is gone** and only a person can
#: restore it.
#:
#: ``access_token_invalid`` covers a revoked authorization, a token that aged
#: out, and an account that was deleted; ``access_token_expired`` is the
#: ordinary hourly expiry, which the caller resolves by refreshing rather than
#: by asking somebody to reconnect - but by the time it reaches this classifier
#: the refresh has already been attempted and failed, so it means the same
#: thing.
AUTH_CODES: frozenset[str] = frozenset(
    {
        "access_token_invalid",
        "access_token_expired",
        "invalid_access_token",
        "unauthorized",
    }
)

#: Codes that mean **the token is valid and was not granted what this call
#: needs**. A different fix entirely: a person reauthorizing with a wider
#: consent, or an app approved for a scope it was not approved for.
#:
#: ``scope_not_authorized`` is what a person declined at the consent screen;
#: ``scope_permission_missed`` is what TikTok returns when the *app* was never
#: approved for the scope, so nobody can consent to it. Both are
#: ``INSUFFICIENT_SCOPE`` because both are answered by reconnecting after
#: somebody has fixed the app, and neither is answered by waiting.
SCOPE_CODES: frozenset[str] = frozenset(
    {
        "scope_not_authorized",
        "scope_permission_missed",
        "insufficient_scope",
    }
)

#: Too many requests, across every level TikTok limits at. They all mean the
#: same thing to a scheduler: come back later.
RATE_LIMIT_CODES: frozenset[str] = frozenset(
    {
        "rate_limit_exceeded",
        "daily_quota_limit_exceeded",
        "spam_risk_too_many_requests",
    }
)

#: TikTok's own "we had a problem".
TRANSIENT_CODES: frozenset[str] = frozenset({"internal_error", "service_unavailable"})

#: The account cannot be reached as addressed - deleted, suspended, private, or
#: an ``open_id`` that does not belong to this app.
ACCOUNT_CODES: frozenset[str] = frozenset(
    {
        "user_not_found",
        "account_not_found",
        "user_has_no_video",
        "spam_risk_user_banned_from_posting",
    }
)

#: OAuth 2.0 ``error`` strings from ``/oauth/token/`` that mean the stored
#: refresh token will never work again.
#:
#: ``invalid_grant`` is the ordinary one: a refresh token that was revoked in
#: the TikTok app's settings, or that hit the 365-day ceiling TikTok anchors to
#: the *first* authorization and does not extend on refresh. Either way only a
#: person can fix it, and the connection has to say so rather than retrying it
#: hourly for a year.
OAUTH_DEAD_GRANT: frozenset[str] = frozenset(
    {"invalid_grant", "access_denied", "authorization_code_expired"}
)

#: OAuth ``error`` strings that are MeoBot's or the operator's fault rather than
#: the person's. Classified as ``BAD_RESPONSE`` so nobody is told to reconnect
#: over a misconfigured client key - which would waste their time and rotate a
#: working credential for nothing.
OAUTH_CLIENT_FAULT: frozenset[str] = frozenset(
    {"invalid_client", "invalid_request", "unsupported_grant_type", "invalid_scope"}
)


def classify_tiktok_error(
    status_code: int, payload: Any = None, *, error_code: str | None = None
) -> PrChannelSyncErrorCode:
    """Map an Open API failure onto a safe error class.

    Args:
        status_code: The HTTP status, used only as a fallback. TikTok answers
            most failures with 200, so this is rarely the deciding input.
        payload: The parsed response body, when it is shaped the way TikTok
            shapes one. A proxy returning an HTML 502 is a real thing that
            happens and must classify as ``PROVIDER_UNAVAILABLE`` rather than
            crash the classifier, so nothing here assumes a dict.
        error_code: The ``error.code`` string, when the caller has already
            pulled it out. Passed explicitly rather than re-parsed so that the
            client and this function cannot disagree about which field they read.

    Returns:
        One of the seven codes. Never ``ok``, and never a TikTok string.
    """
    code = (error_code or _error_code_of(payload) or "").strip().lower()

    if code:
        if code in AUTH_CODES:
            return PrChannelSyncErrorCode.AUTH_REQUIRED
        if code in SCOPE_CODES:
            return PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
        if code in RATE_LIMIT_CODES:
            return PrChannelSyncErrorCode.RATE_LIMITED
        if code in TRANSIENT_CODES:
            return PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE
        if code in ACCOUNT_CODES:
            return PrChannelSyncErrorCode.INVALID_ACCOUNT
        # ``invalid_params`` is the important one to land here rather than
        # anywhere softer: it is what TikTok says when a *field name* in the
        # query is not one it serves, which is precisely the "this metric does
        # not exist on this API version" case the probe exists to surface. It is
        # a malformed request, and asking for fewer fields can fix it - see
        # ``POST_FIELD_REFUSALS``' TikTok equivalent in the client.
        return PrChannelSyncErrorCode.BAD_RESPONSE

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


def oauth_error_code(error: str | None, *, status_code: int) -> PrChannelSyncErrorCode:
    """Map an OAuth 2.0 ``error`` string from the token endpoint.

    Kept apart from :func:`classify_tiktok_error` because the two endpoints use
    genuinely different vocabularies for genuinely different things, and one
    lenient parser reading both would mis-classify in whichever direction it
    guessed. ``invalid_grant`` here means "reconnect"; there is no
    ``invalid_grant`` in the Open API vocabulary at all.
    """
    name = (error or "").strip().lower()
    if name in OAUTH_DEAD_GRANT:
        return PrChannelSyncErrorCode.AUTH_REQUIRED
    if name in OAUTH_CLIENT_FAULT:
        return PrChannelSyncErrorCode.BAD_RESPONSE
    if name in RATE_LIMIT_CODES:
        return PrChannelSyncErrorCode.RATE_LIMITED
    if name in TRANSIENT_CODES:
        return PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE
    if status_code == 401:
        return PrChannelSyncErrorCode.AUTH_REQUIRED
    if 500 <= status_code < 600:
        return PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE
    if name or 400 <= status_code < 500:
        return PrChannelSyncErrorCode.BAD_RESPONSE
    return PrChannelSyncErrorCode.UNKNOWN


def _error_code_of(payload: Any) -> str | None:
    """The ``error.code`` string, if the body is shaped the way TikTok shapes one."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        return code if isinstance(code, str) else None
    # ``/oauth/*`` uses a top-level string here. Read defensively rather than
    # refused, so a token-endpoint body reaching this function by mistake
    # classifies as something rather than raising inside an error path.
    return error if isinstance(error, str) else None


def is_ok(payload: Any) -> bool:
    """Whether a 200 from the Open API actually means success.

    The load-bearing question of this module. A body with no ``error`` object at
    all counts as fine - some endpoints omit it - and anything with a code
    outside :data:`OK_CODES` does not, whatever the status line said.
    """
    code = _error_code_of(payload)
    return code is None or code.strip().lower() in OK_CODES


__all__: list[str] = [
    "ACCOUNT_CODES",
    "AUTH_CODES",
    "OAUTH_CLIENT_FAULT",
    "OAUTH_DEAD_GRANT",
    "OK_CODES",
    "RATE_LIMIT_CODES",
    "SCOPE_CODES",
    "TRANSIENT_CODES",
    "TikTokApiError",
    "classify_tiktok_error",
    "is_ok",
    "oauth_error_code",
]
