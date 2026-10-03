"""The one HTTP object that talks to TikTok.

Step 1F.2.6. Login Kit for the token lifecycle and the Display API for reading;
one class, because they are one host family, one credential and one error
vocabulary, and splitting them would mean two places to fix a version bump and
two chances to classify a failure differently.

What this module will not do
-----------------------------

* **contact a host it was not compiled with.** Every URL comes from
  :class:`~meobot.integrations.tiktok.constants.TikTokEndpoints`; nothing reads
  a host from a request, a channel row, or a field inside a TikTok response;
* **follow a redirect.** TikTok does not need one, and an unfollowed redirect is
  one fewer way for a bearer token to be sent somewhere it was not meant for;
* **log a secret.** The log line names the operation, the status, and TikTok's
  own ``log_id`` when there is one. Never the params, never the headers, never
  the body - the token, the client secret, the authorization code and the
  refresh token all live in exactly one local each and none of them reaches a
  log sink, an exception message or a returned object's ``repr``.

Tokens travel as a header
--------------------------

``Authorization: Bearer``, always, for every Display API call. The client secret
goes in a form body on the two OAuth endpoints because TikTok accepts it in no
other way there, and a form body is not a request line: it is not in an access
log, a proxy log or a browser history the way a query parameter is.

A 200 is not a success
-----------------------

Read :mod:`meobot.integrations.tiktok.errors` before changing anything here. The
Open API answers a refused field, a missing scope and a rate limit with HTTP
200 and an ``error`` object, so :meth:`TikTokApiClient._api` checks
:func:`~meobot.integrations.tiktok.errors.is_ok` on every response and raises on
a body that a status-code-only client would have accepted as data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.integrations.tiktok.constants import (
    DEFAULT_TIMEOUT_SECONDS,
    VIDEO_PAGE_SIZE,
    TikTokEndpoints,
)
from meobot.integrations.tiktok.errors import (
    TikTokApiError,
    classify_tiktok_error,
    is_ok,
    oauth_error_code,
)

logger = get_logger(__name__)

#: The largest count worth believing from a remote system. The same bound the
#: manual form applies, applied again because a TikTok response is untrusted
#: input and a 10^18 follower count is a parsing bug, not an audience.
MAX_REMOTE_VALUE = 10**15


@dataclass(frozen=True, slots=True)
class TikTokTokens:
    """What a Login Kit token exchange handed back.

    Deliberately **not**
    :class:`~meobot.domain.pr.channel_metrics.ProviderTokens`: TikTok returns
    two expiries and an ``open_id``, and flattening them into the port's shape
    at the transport boundary would throw away the two facts the provider needs
    in order to be honest about them. The provider does the mapping, once.

    This object holds two live credentials and is short-lived by contract. It is
    never stored as it stands, never logged, never audited and never serialised
    into a response - ``slots=True`` is a small extra guarantee that nothing can
    quietly attach a copy of one somewhere else.
    """

    access_token: str
    #: TikTok's ``open_id`` for the authorizing account. Returned by the token
    #: endpoint itself, which is why account identity does not need a second
    #: call to establish - though :meth:`user_info` is still asked, because a
    #: connection binds to what the *token* resolves to rather than to what an
    #: exchange claimed.
    open_id: str | None = None
    expires_at: datetime | None = None
    refresh_token: str | None = None
    #: When the refresh token itself dies. TikTok anchors this to the **first**
    #: authorization and does **not** extend it on refresh, so a connection has
    #: a hard ceiling - a year by default - after which a person must consent
    #: again no matter how healthy the syncs have been. Recorded so the reason
    #: is knowable; not stored, because there is no column for it and the
    #: failure it produces (``AUTH_REQUIRED`` → ``ACTION_REQUIRED``) is already
    #: handled and already says "reconnect".
    refresh_expires_at: datetime | None = None
    #: The scopes TikTok actually granted, which may be **fewer** than were
    #: asked for: a person can decline one at the consent screen, and an app not
    #: approved for ``user.info.stats`` gets a token without it. Read back and
    #: stored rather than assumed - see
    #: :data:`~meobot.integrations.tiktok.constants.TIKTOK_SCOPES`.
    granted_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VideoPage:
    """One bounded page of ``video/list``, and how to ask for the next.

    ``cursor`` is TikTok's own value and is **only ever passed back verbatim**.
    It is documented as a millisecond timestamp of the last video returned, and
    nothing in this connector does arithmetic on it or interprets it: a cursor
    is an opaque continuation token, and a client that started treating it as a
    date would break the day TikTok changed what it puts there.
    """

    videos: tuple[Mapping[str, Any], ...] = ()
    cursor: int | None = None
    #: TikTok's own flag. The **only** thing that decides whether another page
    #: is asked for - a cursor comes back on the last page too, so a client that
    #: paged on the cursor alone would ask for one empty page every time.
    has_more: bool = False


class TikTokApiClient:
    """Talks to TikTok. Knows nothing about PR channels, sync or scheduling.

    Args:
        client_key: TikTok app client key. Deployment configuration, and not a
            secret - it is in the authorization URL a browser is sent to.
        client_secret: TikTok app client secret. **Never** leaves this object,
            and never appears in a log, an audit row or a response.
        redirect_uri: Must match a Redirect URI registered on the TikTok app
            exactly. Never taken from a request.
        endpoints: Hosts and the API version.
        client: Injected ``httpx.AsyncClient``. Tests pass a ``MockTransport``,
            which is how this whole connector is exercised without a network.
    """

    provider = "tiktok"

    def __init__(
        self,
        *,
        client_key: str,
        client_secret: str,
        redirect_uri: str,
        endpoints: TikTokEndpoints | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client_key = client_key
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._endpoints = endpoints or TikTokEndpoints()
        self._timeout = timeout_seconds
        self._client = client
        self._owns_client = client is None

    @property
    def endpoints(self) -> TikTokEndpoints:
        return self._endpoints

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- OAuth ------------------------------------------------------------
    def authorization_url(self, *, state: str, scopes: tuple[str, ...]) -> str:
        """TikTok's consent dialog, carrying our opaque ``state``.

        Two details TikTok differs from every other provider on, both of which
        are silent failures if missed:

        * the app credential parameter is **``client_key``**, not
          ``client_id``. TikTok answers a request carrying ``client_id`` with a
          generic error that names nothing;
        * scopes are joined with **commas**, not spaces. A space-joined list is
          read as one long scope name that does not exist, and the token that
          comes back carries none of them.

        No PKCE. Login Kit requires ``code_challenge`` for desktop and mobile
        clients and accepts a confidential web client authenticating with its
        secret, which is what MeoBot is: the exchange happens server-side, the
        secret never reaches a browser, and the code is bound to a single-use
        state row that names the channel and the person. Adding PKCE would mean
        storing a ``code_verifier`` across the redirect, which is a column this
        milestone would have had to add for no gain in a server-side flow.
        """
        query = urlencode(
            {
                "client_key": self._client_key,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                # Commas. See the docstring - this is not a style choice.
                "scope": ",".join(scopes),
                "state": state,
            }
        )
        return f"{self._endpoints.authorize}?{query}"

    async def exchange_code(self, *, code: str) -> TikTokTokens:
        """A one-time authorization code for an access and a refresh token.

        The code is never logged. It is a bearer credential for the few seconds
        it lives, and a log line containing one can be replayed into an account.
        """
        return await self._token_request(
            {
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self._redirect_uri,
            },
            operation="exchange_code",
        )

    async def refresh(self, *, refresh_token: str) -> TikTokTokens:
        """Exchange a stored refresh token for a fresh access token.

        **TikTok rotates the refresh token on every refresh**, which is the one
        thing about this method that must not be got wrong: the response carries
        a *new* refresh token and the old one stops working. Dropping it would
        leave the stored credential dead at the next sync, so the provider
        returns it as a rotated durable credential and
        :meth:`~meobot.application.pr_channel_connection_service.PrChannelConnectionService.access_token_for`
        writes it back - the branch that already exists there for exactly this.

        Rotation does **not** extend the refresh token's own lifetime: TikTok
        anchors that to the first authorization. See
        :attr:`TikTokTokens.refresh_expires_at`.
        """
        return await self._token_request(
            {
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            operation="refresh",
        )

    async def revoke_token(self, *, token: str) -> bool:
        """Ask TikTok to forget this grant. Best effort by contract.

        Returns ``True`` when TikTok confirmed, ``False`` otherwise. A revoke
        that fails must never stop MeoBot from disconnecting locally - the
        alternative is a channel nobody can disconnect because TikTok is having
        an afternoon - so this reports rather than raises.

        Unlike Meta's, this really is scoped to one grant: TikTok's revoke takes
        a token and drops the authorization that issued it, which is this
        channel's and nothing else.
        """
        try:
            response = await self._send(
                "POST",
                self._endpoints.revoke,
                data={
                    "client_key": self._client_key,
                    "client_secret": self._client_secret,
                    "token": token,
                },
                operation="revoke",
            )
        except TikTokApiError:
            logger.warning("tiktok_revoke_failed", extra={"provider": self.provider})
            return False
        if response.status_code >= 400:
            logger.warning(
                "tiktok_revoke_rejected",
                extra={"provider": self.provider, "status_code": response.status_code},
            )
            return False
        return True

    # --- Reading ----------------------------------------------------------
    async def user_info(
        self, *, access_token: str, fields: Sequence[str], operation_hint: str = "user_info"
    ) -> Mapping[str, Any]:
        """Read named fields off the authorized account. One request.

        ``GET`` with the field list as a **query parameter**, which is TikTok's
        shape here and differs from ``video/list`` in the same API. The account
        is always "whoever this token belongs to" - there is no id parameter and
        no way to ask about somebody else, which is a useful property rather
        than a limitation: a connection's identity check cannot be fooled by a
        caller passing the id it hoped for.

        ``operation_hint`` only names the call in the log line. It exists so an
        operator running the capability probe can tell its requests apart from a
        sync's in the same log stream; it reaches no URL and no parameter.

        Returns:
            The ``data.user`` object, or an empty mapping when TikTok answered
            successfully with nothing in it.
        """
        payload = await self._api(
            "GET",
            self._endpoints.user_info,
            params={"fields": ",".join(fields)},
            access_token=access_token,
            operation=operation_hint,
        )
        data = payload.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        return user if isinstance(user, dict) else {}

    async def video_page(
        self,
        *,
        access_token: str,
        fields: Sequence[str],
        max_count: int = VIDEO_PAGE_SIZE,
        cursor: int | None = None,
        operation_hint: str = "video_list",
    ) -> VideoPage:
        """One explicitly-sized page of the account's own videos.

        ``POST``, with the field list in the **query string** and the paging
        parameters in a **JSON body**. That split is TikTok's, not a choice
        available here, and it is the shape most easily got wrong: fields in the
        body are ignored and the response comes back with ``id`` alone.

        ``max_count`` is capped at
        :data:`~meobot.integrations.tiktok.constants.VIDEO_PAGE_SIZE` rather
        than passed through, because TikTok rejects anything larger and a caller
        that asked for 100 should get 20 videos rather than an error it cannot
        act on.

        There is **no server-side date filter** on this endpoint. TikTok offers
        no ``since``/``until``, so a windowed count can only be produced by
        walking newest-first and stopping - which is a walk this milestone
        deliberately does not build. What it does build is a bounded page reader
        the probe can use to find out whether the per-video counters are even
        served, which is the question that decides whether such a walk is worth
        writing at all.

        Returns:
            The page's rows, TikTok's cursor, and TikTok's own ``has_more``. A
            caller must still bound how many times it asks; see
            :data:`~meobot.integrations.tiktok.constants.MAX_VIDEO_PAGES`.
        """
        body: dict[str, Any] = {"max_count": min(max_count, VIDEO_PAGE_SIZE)}
        if cursor is not None:
            body["cursor"] = cursor
        payload = await self._api(
            "POST",
            self._endpoints.video_list,
            params={"fields": ",".join(fields)},
            access_token=access_token,
            json_body=body,
            operation=operation_hint,
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TikTokApiError(
                "video/list returned no data object",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        rows = data.get("videos")
        videos = (
            tuple(row for row in rows if isinstance(row, dict)) if isinstance(rows, list) else ()
        )
        return VideoPage(
            videos=videos,
            cursor=parse_count(data.get("cursor")),
            has_more=data.get("has_more") is True,
        )

    async def video_query(
        self,
        *,
        access_token: str,
        fields: Sequence[str],
        video_ids: Sequence[str],
        operation_hint: str = "video_query",
    ) -> tuple[Mapping[str, Any], ...]:
        """Named fields for specific videos, by id. One request for the batch.

        Present because the milestone names it, and useful for exactly one
        thing: confirming whether a field ``video/list`` withheld is available
        when asked for directly. It is **not** a per-video fetch loop and must
        never become one - the whole point of reading counters off the list
        response is that a hundred videos cost the same as one.
        """
        payload = await self._api(
            "POST",
            self._endpoints.video_query,
            params={"fields": ",".join(fields)},
            access_token=access_token,
            json_body={"filters": {"video_ids": list(video_ids)}},
            operation=operation_hint,
        )
        data = payload.get("data")
        rows = data.get("videos") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return ()
        return tuple(row for row in rows if isinstance(row, dict))

    # --- Internals --------------------------------------------------------
    async def _token_request(self, form: dict[str, str], *, operation: str) -> TikTokTokens:
        """One call to ``/oauth/token/``, parsed into :class:`TikTokTokens`.

        The token endpoint speaks OAuth 2.0's error dialect rather than the Open
        API's ``error`` object - see :mod:`meobot.integrations.tiktok.errors` -
        so this reads a top-level ``error`` string and classifies it with
        :func:`~meobot.integrations.tiktok.errors.oauth_error_code`. It is also
        the one place a client secret is sent, and the one place an
        authorization code or a refresh token appears; none of the three is
        logged, and the exception raised on failure carries the error class and
        nothing else.
        """
        response = await self._send("POST", self._endpoints.token, data=form, operation=operation)
        payload = self._decode(response, operation=operation)

        error = payload.get("error")
        # ``error`` is present and empty on success for some TikTok builds, so
        # emptiness is treated as absence rather than as a failure with no name.
        failed = response.status_code >= 400 or (isinstance(error, str) and error.strip())
        if failed:
            self._log_failure(operation, response.status_code, payload)
            raise TikTokApiError(
                f"{operation} was refused by TikTok",
                error_code=oauth_error_code(
                    error if isinstance(error, str) else None, status_code=response.status_code
                ),
                status_code=response.status_code,
            )

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise TikTokApiError(
                f"{operation} returned no access token",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
                status_code=response.status_code,
            )
        refresh = payload.get("refresh_token")
        scope = payload.get("scope")
        return TikTokTokens(
            access_token=access_token,
            open_id=_text(payload.get("open_id")),
            expires_at=_deadline(payload.get("expires_in")),
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            refresh_expires_at=_deadline(payload.get("refresh_expires_in")),
            # Comma-separated on the way back too, and TikTok has been known to
            # use both separators. Split on either rather than picking one and
            # recording a single scope named "a,b,c".
            granted_scopes=_scopes(scope),
        )

    async def _api(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str],
        access_token: str,
        json_body: dict[str, Any] | None = None,
        operation: str,
    ) -> Mapping[str, Any]:
        """One Display API call, with TikTok's 200-means-nothing handled.

        The ordering here is the whole point and is not negotiable:

        1. decode the body;
        2. **check ``error.code`` before the status line.** A refused scope
           arrives as ``200 OK`` with an empty ``data``, and a client that
           returned it as data would write a snapshot of nulls and record it as
           a success;
        3. only then fall back to the status.
        """
        response = await self._send(
            method,
            url,
            params=params,
            json_body=json_body,
            headers={"Authorization": f"Bearer {access_token}"},
            operation=operation,
        )
        payload = self._decode(response, operation=operation)

        if not is_ok(payload) or response.status_code >= 400:
            self._log_failure(operation, response.status_code, payload)
            raise TikTokApiError(
                f"{operation} failed",
                error_code=classify_tiktok_error(response.status_code, payload),
                status_code=response.status_code,
            )
        return payload

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        operation: str,
    ) -> httpx.Response:
        """One bounded request to a hard-coded TikTok host.

        No redirects followed, and every transport failure classified rather
        than allowed to escape as an ``httpx`` exception - a bare
        ``ReadTimeout`` reaching the sync orchestration would be an unknown
        failure instead of "the provider is unavailable, try later".
        """
        client = self._ensure_client()
        try:
            response = await client.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise TikTokApiError(
                f"{operation} timed out",
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise TikTokApiError(
                f"{operation} could not reach TikTok",
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc

        logger.info(
            "tiktok_request",
            extra={
                "provider": self.provider,
                "operation": operation,
                "status_code": response.status_code,
            },
        )
        return response

    def _decode(self, response: httpx.Response, *, operation: str) -> Mapping[str, Any]:
        """The body as a JSON object, or a classified failure.

        An empty body is an empty object rather than an error: TikTok's revoke
        endpoint answers with one, and treating that as malformed would turn a
        successful disconnect into a warning.
        """
        if not response.content:
            return {}
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise TikTokApiError(
                f"{operation} returned a body that is not JSON",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise TikTokApiError(
                f"{operation} returned a body that is not an object",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
                status_code=response.status_code,
            )
        return payload

    def _log_failure(self, operation: str, status_code: int, payload: Mapping[str, Any]) -> None:
        """Log the failure by its **name and log id**, never its prose.

        ``log_id`` is the one thing TikTok support asks for and the one thing in
        an error body that is safe to keep: it identifies a request in TikTok's
        systems and says nothing about MeoBot's. The human-readable ``message``
        beside it is provider prose and is deliberately dropped here rather than
        carried into a log line somebody would later paste onto a screen.
        """
        error = payload.get("error")
        if isinstance(error, dict):
            name, log_id = error.get("code"), error.get("log_id")
        else:
            name, log_id = error if isinstance(error, str) else None, payload.get("log_id")
        logger.warning(
            "tiktok_request_failed",
            extra={
                "provider": self.provider,
                "operation": operation,
                "status_code": status_code,
                "tiktok_error": name if isinstance(name, str) else None,
                "tiktok_log_id": log_id if isinstance(log_id, str) else None,
            },
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client


def _deadline(seconds: Any) -> datetime | None:
    """An absolute expiry from TikTok's relative ``*_expires_in``, or ``None``."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    if seconds <= 0:
        return None
    return utcnow() + timedelta(seconds=int(seconds))


def _scopes(value: Any) -> tuple[str, ...]:
    """The granted scope list, however TikTok chose to separate it."""
    if not isinstance(value, str):
        return ()
    return tuple(part for part in value.replace(",", " ").split() if part)


def parse_count(value: Any) -> int | None:
    """A non-negative count from an untrusted field, or ``None``.

    TikTok sends counts as numbers and occasionally as strings, so both are
    accepted. Everything else - a fractional float, a bool, a negative, an
    absurd magnitude, a nested object - is ``None`` rather than an exception:
    one malformed field must not lose the whole reading, and a metric MeoBot
    could not read is exactly what "not recorded" means.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if value != int(value):
            return None
        number = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text.lstrip("-").isdigit():
            return None
        number = int(text)
    else:
        return None
    if number < 0 or number > MAX_REMOTE_VALUE:
        return None
    return number


def epoch_to_utc(value: Any) -> datetime | None:
    """A TikTok ``create_time`` (Unix seconds) as an aware instant, or ``None``."""
    seconds = parse_count(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _text(value: Any) -> str | None:
    """A non-empty display string, or ``None``."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__: list[str] = [
    "MAX_REMOTE_VALUE",
    "TikTokApiClient",
    "TikTokTokens",
    "VideoPage",
    "epoch_to_utc",
    "parse_count",
]
