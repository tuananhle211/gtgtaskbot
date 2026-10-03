"""The YouTube connector: consent, identity, and two APIs' worth of numbers.

Step 1F.2.4b. This is the only concrete
:class:`~meobot.domain.pr.channel_metrics.ChannelMetricsProvider` in MeoBot.

Two APIs, two different questions
----------------------------------

They are not interchangeable and the whole normalization rests on keeping them
apart:

* **YouTube Data API v3** (``channels.list?part=snippet,statistics``) answers
  *"what is this channel and what are its totals right now"* - title, handle,
  subscriber count, video count, and a **cumulative lifetime** view count;
* **YouTube Analytics API v2** (``reports.query``) answers *"what happened
  between these two dates"* - and is the only one of the two that can. It needs
  owner authorization, which is what the second scope is for.

The trap this module exists to avoid is treating the Data API's ``viewCount``
as "views this month". It is every view the channel has ever had. It goes to
``extra_metrics`` under ``youtube_total_view_count`` and **never** to
``views_30d``; a test asserts that specifically, because it is the single
easiest way to put a number on a dashboard that is wrong by three orders of
magnitude and looks plausible.

One report, not six
-------------------

Analytics is quota-metered, so the two ``reports.query`` calls ask for every
compatible metric at once - ``views,likes,comments,shares,estimatedMinutesWatched``
in one request per window - rather than one request per metric. Two windows, two
calls, plus one Data API call: **three requests per channel per day**.

What is deliberately not mapped
-------------------------------

``reach_7d``, ``reach_30d``, ``impressions_7d`` and ``impressions_30d`` stay
``None``. YouTube Analytics does expose ``impressions`` on some report
dimensions, but it means *thumbnails shown in browse surfaces*, which is not the
same quantity as a Facebook or TikTok impression and would not be comparable
across the channel list where those cards sit side by side. Publishing a number
under a label it does not match is worse than an empty card, which at least says
"chưa có dữ liệu" honestly.

``engagements_7d`` and ``engagements_30d`` are also ``None``: there is no
YouTube metric of that name, and summing likes and comments into one would be
MeoBot inventing a definition and then presenting it as measured. Step 1F.2.4a
made the same refusal about engagement *rate* for the same reason.

Everything this module does not map is visible as an empty card rather than as a
confident wrong number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.domain.pr.channel_metrics import (
    ChannelMetricsReading,
    FetchedChannelIdentity,
    PrChannelPlatform,
    ProviderTokens,
    normalize_capture_time,
)
from meobot.integrations.youtube.constants import (
    ANALYTICS_LAG_DAYS,
    DEFAULT_ENDPOINTS,
    DEFAULT_TIMEOUT_SECONDS,
    YOUTUBE_SCOPES,
    YouTubeOAuthEndpoints,
)
from meobot.integrations.youtube.errors import (
    YouTubeApiError,
    classify_oauth_error,
    classify_status,
)

logger = get_logger(__name__)

#: The Analytics metrics one report can return together. Requested in a single
#: call per window - see the module docstring on quota.
ANALYTICS_METRICS = ("views", "likes", "comments", "shares", "estimatedMinutesWatched")

#: The largest count worth believing from a remote system. Same bound the manual
#: form applies, applied again here because a provider response is untrusted
#: input and a 10^18 subscriber count is a parsing bug, not a channel.
MAX_REMOTE_VALUE = 10**15


@dataclass(frozen=True, slots=True)
class AnalyticsWindow:
    """One closed, settled reporting window, as ISO dates.

    Closed and settled on purpose: the window ends
    :data:`~meobot.integrations.youtube.constants.ANALYTICS_LAG_DAYS` days before
    today, so the same window queried twice returns the same numbers. That is
    what makes the idempotency fingerprint meaningful - a window whose last day
    is still moving would produce a different key on every retry.
    """

    start: date
    end: date

    @property
    def start_iso(self) -> str:
        return self.start.isoformat()

    @property
    def end_iso(self) -> str:
        return self.end.isoformat()


def analytics_window(observed_at: datetime, *, days: int) -> AnalyticsWindow:
    """The settled ``days``-long window ending before ``observed_at``.

    Args:
        observed_at: When MeoBot is looking. Only its date is used.
        days: 7 or 30. Inclusive of both bounds, so a 7-day window spans seven
            dates rather than eight.
    """
    end = normalize_capture_time(observed_at).date() - timedelta(days=ANALYTICS_LAG_DAYS)
    return AnalyticsWindow(start=end - timedelta(days=days - 1), end=end)


class YouTubeChannelMetricsProvider:
    """Talks to Google. Knows nothing about PR channels, sync or scheduling.

    Args:
        client_id: Google OAuth client id. Deployment configuration.
        client_secret: Google OAuth client secret. **Never** leaves this object,
            and never appears in a log, an audit row or a response.
        redirect_uri: Must match a URI registered on the OAuth client exactly.
        client: Injected ``httpx.AsyncClient``. Tests pass a ``MockTransport``,
            which is how the whole connector is exercised without a network.
        endpoints: Google's hosts. Substitutable for tests; never configurable
            in production - see :mod:`meobot.integrations.youtube.constants`.
    """

    platform = PrChannelPlatform.YOUTUBE
    provider = "youtube"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        client: httpx.AsyncClient | None = None,
        endpoints: YouTubeOAuthEndpoints = DEFAULT_ENDPOINTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._endpoints = endpoints
        self._timeout = timeout_seconds
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client when this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Authorization ----------------------------------------------------
    def build_authorization_url(self, *, state: str) -> str:
        """Google's consent screen, carrying our opaque ``state``.

        ``access_type=offline`` with ``prompt=consent`` is what makes unattended
        sync possible at all: without the first there is no refresh token, and
        without the second Google **omits** the refresh token on every
        authorization after the first - which is exactly the reconnect case, and
        would leave a reconnected channel unable to sync in the background.

        ``include_granted_scopes`` is deliberately absent: incremental
        authorization would let a grant quietly accumulate scopes from other
        Google integrations on the same client.
        """
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": " ".join(YOUTUBE_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
        return f"{self._endpoints.authorize}?{query}"

    async def exchange_authorization_code(self, *, code: str) -> ProviderTokens:
        """Trade a one-time code for tokens.

        The code is never logged. It is a bearer credential for the few seconds
        it lives, and a log line containing one is a log line that can be
        replayed into an account.
        """
        return await self._token_request(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self._redirect_uri,
            },
            operation="exchange_authorization_code",
        )

    async def acquire_access(self, *, durable_credential: str) -> ProviderTokens:
        """Exchange the stored refresh token for a fresh access token.

        Google's half of the provider-neutral contract: here the durable
        credential really is a refresh token and really is exchanged over the
        network. Meta's implementation of the same method makes no request at
        all - which is why the port names the outcome rather than the mechanism.

        The response normally carries **no** refresh token, and that is not a
        failure - it means "keep using the one you have".
        """
        return await self._token_request(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": durable_credential,
                "grant_type": "refresh_token",
            },
            operation="acquire_access",
        )

    async def revoke(self, *, durable_credential: str) -> None:
        """Ask Google to forget this grant.

        Best effort by contract. A revoke that fails must never stop MeoBot from
        disconnecting locally - the alternative is a channel nobody can
        disconnect because Google is having an afternoon.
        """
        try:
            response = await self._send(
                "POST",
                self._endpoints.revoke,
                data={"token": durable_credential},
                operation="revoke",
            )
        except YouTubeApiError:
            logger.warning("youtube_revoke_failed", extra={"provider": self.provider})
            return
        if response.status_code >= 400:
            logger.warning(
                "youtube_revoke_rejected",
                extra={"provider": self.provider, "status_code": response.status_code},
            )

    # --- Measurement ------------------------------------------------------
    async def fetch_channel_identity(self, *, access_token: str) -> FetchedChannelIdentity:
        """Who this token actually belongs to.

        ``mine=true``, so the answer is the authorized account rather than
        whatever id the caller believed. That is the whole point: the difference
        between what somebody clicked and what they consented to is the
        wrong-account mistake, and it is only visible if this call is the one
        that decides.
        """
        payload = await self._get_json(
            f"{self._endpoints.data_api}/channels",
            params={"part": "snippet,statistics", "mine": "true"},
            access_token=access_token,
            operation="fetch_channel_identity",
        )
        item = self._first_item(payload, operation="fetch_channel_identity")
        raw_snippet = item.get("snippet")
        snippet: dict[str, Any] = raw_snippet if isinstance(raw_snippet, dict) else {}
        account_id = item.get("id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise YouTubeApiError(
                "channels.list returned an item without an id",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        return FetchedChannelIdentity(
            account_id=account_id.strip(),
            title=_text(snippet.get("title")),
            handle=_text(snippet.get("customUrl")),
            profile_url=f"https://www.youtube.com/channel/{account_id.strip()}",
        )

    async def fetch_channel_metrics(
        self, *, access_token: str, account_id: str, now: datetime
    ) -> ChannelMetricsReading:
        """One normalized reading: Data API totals plus two Analytics windows.

        Three HTTP calls. The account id is re-verified against what the token
        actually owns, so a connection whose bound account has changed hands
        fails as ``INVALID_ACCOUNT`` rather than quietly recording somebody
        else's numbers under this channel's name.
        """
        statistics = await self._fetch_statistics(access_token=access_token)
        if statistics.account_id != account_id:
            raise YouTubeApiError(
                "the authorized account is not the one this connection is bound to",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )

        window_7d = analytics_window(now, days=7)
        window_30d = analytics_window(now, days=30)
        report_7d = await self._fetch_report(
            access_token=access_token, account_id=account_id, window=window_7d
        )
        report_30d = await self._fetch_report(
            access_token=access_token, account_id=account_id, window=window_30d
        )

        metrics: dict[str, int | None] = {
            # Point-in-time counts, from the Data API.
            "followers": statistics.subscribers,
            "posts_count": statistics.videos,
            # Windowed counts, from Analytics. Nothing else can produce these.
            "views_7d": report_7d.get("views"),
            "views_30d": report_30d.get("views"),
            "likes_30d": report_30d.get("likes"),
            "comments_30d": report_30d.get("comments"),
            "shares_30d": report_30d.get("shares"),
            # Deliberately absent - see the module docstring:
            # reach_*, impressions_*, engagements_*, following.
        }

        extra: dict[str, Any] = {
            # Cumulative and lifetime. Emphatically not ``views_30d``.
            "youtube_total_view_count": statistics.total_views,
            "youtube_hidden_subscriber_count": statistics.hidden_subscribers,
            # YouTube rounds public subscriber counts. Recorded so nobody later
            # reads a stored figure as exact to the person.
            "youtube_subscriber_count_is_provider_rounded": True,
            "youtube_analytics_start_7d": window_7d.start_iso,
            "youtube_analytics_end_7d": window_7d.end_iso,
            "youtube_analytics_start_30d": window_30d.start_iso,
            "youtube_analytics_end_30d": window_30d.end_iso,
        }
        watch_minutes = report_30d.get("estimatedMinutesWatched")
        if watch_minutes is not None:
            extra["youtube_estimated_minutes_watched_30d"] = watch_minutes

        return ChannelMetricsReading(
            observed_at=normalize_capture_time(now),
            metrics=metrics,
            extra_metrics=extra,
            # The 30-day window is the reading's identity: it is the widest one
            # fetched, and two syncs covering it are the same reading.
            period_start=window_30d.start_iso,
            period_end=window_30d.end_iso,
        )

    # --- Internals --------------------------------------------------------
    @dataclass(frozen=True, slots=True)
    class _Statistics:
        account_id: str
        subscribers: int | None
        videos: int | None
        total_views: int | None
        hidden_subscribers: bool

    async def _fetch_statistics(self, *, access_token: str) -> _Statistics:
        payload = await self._get_json(
            f"{self._endpoints.data_api}/channels",
            params={"part": "snippet,statistics", "mine": "true"},
            access_token=access_token,
            operation="fetch_statistics",
        )
        item = self._first_item(payload, operation="fetch_statistics")
        account_id = item.get("id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise YouTubeApiError(
                "channels.list returned an item without an id",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        raw_stats = item.get("statistics")
        stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        hidden = bool(stats.get("hiddenSubscriberCount"))
        return self._Statistics(
            account_id=account_id.strip(),
            # Hidden means the platform declines to say, which is *not* zero.
            # Storing a zero here would report a channel losing every
            # subscriber it had the day its owner ticked a privacy box.
            subscribers=None if hidden else _count(stats.get("subscriberCount")),
            videos=_count(stats.get("videoCount")),
            total_views=_count(stats.get("viewCount")),
            hidden_subscribers=hidden,
        )

    async def _fetch_report(
        self, *, access_token: str, account_id: str, window: AnalyticsWindow
    ) -> dict[str, int | None]:
        """One Analytics report, as ``metric name -> value``.

        A window with no rows is normal - a channel with no activity, or one
        whose data has not settled - and returns all-``None`` rather than
        all-zero. "Nothing happened" and "the platform has not said yet" are
        different facts and only one of them is a measurement.
        """
        payload = await self._get_json(
            f"{self._endpoints.analytics_api}/reports",
            params={
                "ids": f"channel=={account_id}",
                "startDate": window.start_iso,
                "endDate": window.end_iso,
                "metrics": ",".join(ANALYTICS_METRICS),
            },
            access_token=access_token,
            operation="fetch_report",
        )
        headers = payload.get("columnHeaders")
        rows = payload.get("rows")
        if not isinstance(headers, list) or not isinstance(rows, list) or not rows:
            return dict.fromkeys(ANALYTICS_METRICS, None)

        names = [str(header.get("name")) if isinstance(header, dict) else "" for header in headers]
        first = rows[0]
        if not isinstance(first, list):
            raise YouTubeApiError(
                "reports.query returned a row that is not a list",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        report: dict[str, int | None] = dict.fromkeys(ANALYTICS_METRICS, None)
        for index, name in enumerate(names):
            if name in report and index < len(first):
                report[name] = _count(first[index])
        return report

    @staticmethod
    def _first_item(payload: Mapping[str, Any], *, operation: str) -> dict[str, Any]:
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise YouTubeApiError(
                f"{operation}: no channel is accessible with this authorization",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        first = items[0]
        if not isinstance(first, dict):
            raise YouTubeApiError(
                f"{operation}: items[0] is not an object",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        return first

    async def _token_request(self, form: dict[str, str], *, operation: str) -> ProviderTokens:
        response = await self._send("POST", self._endpoints.token, data=form, operation=operation)
        payload = _json(response, operation=operation)
        if response.status_code >= 400:
            error = payload.get("error") if isinstance(payload, dict) else None
            raise YouTubeApiError(
                f"{operation} was refused by Google",
                error_code=classify_oauth_error(error if isinstance(error, str) else None),
                status_code=response.status_code,
            )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise YouTubeApiError(
                f"{operation} returned no access token",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
                status_code=response.status_code,
            )
        expires_in = payload.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int | float) and expires_in > 0:
            expires_at = utcnow() + timedelta(seconds=int(expires_in))
        scope = payload.get("scope")
        refresh = payload.get("refresh_token")
        return ProviderTokens(
            access_token=access_token,
            expires_at=expires_at,
            # Absent is normal on every authorization after the first.
            durable_credential=refresh if isinstance(refresh, str) and refresh else None,
            granted_scopes=tuple(scope.split()) if isinstance(scope, str) else (),
        )

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str],
        access_token: str,
        operation: str,
    ) -> dict[str, Any]:
        response = await self._send(
            "GET",
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            operation=operation,
        )
        payload = _json(response, operation=operation)
        if response.status_code >= 400:
            raise YouTubeApiError(
                f"{operation} failed",
                error_code=classify_status(response.status_code, payload=payload),
                status_code=response.status_code,
            )
        if not isinstance(payload, dict):
            raise YouTubeApiError(
                f"{operation} returned a body that is not an object",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
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
        headers: dict[str, str] | None = None,
        operation: str,
    ) -> httpx.Response:
        """One bounded request to a hard-coded Google host.

        ``url`` is always a literal from
        :mod:`meobot.integrations.youtube.constants`; there is no path by which
        a channel row, a request body or a redirect can decide where this goes.

        The log line names the operation and the status and nothing else -
        never the params (which carry a channel id), never the headers (which
        carry a bearer token), never the body.
        """
        client = self._ensure_client()
        try:
            response = await client.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=self._timeout,
                # Never follow a redirect. Google's APIs do not need one, and
                # an unfollowed redirect is one fewer way for a bearer token to
                # be sent somewhere it was not meant for.
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise YouTubeApiError(
                f"{operation} timed out",
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise YouTubeApiError(
                f"{operation} could not reach Google",
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc
        logger.info(
            "youtube_request",
            extra={
                "provider": self.provider,
                "operation": operation,
                "status_code": response.status_code,
            },
        )
        return response

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client


def _json(response: httpx.Response, *, operation: str) -> Any:
    """Parse a response body, or fail as ``BAD_RESPONSE``.

    An empty body is ``{}`` rather than an error: the revoke endpoint returns
    one on success, and a parser that threw there would turn a working revoke
    into a failure.
    """
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise YouTubeApiError(
            f"{operation} returned a body that is not JSON",
            error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            status_code=response.status_code,
        ) from exc


def _count(value: Any) -> int | None:
    """A non-negative count from an untrusted field, or ``None``.

    YouTube sends counts as **strings** (``"124812"``) in the Data API and as
    numbers in Analytics, so both are accepted. Everything else - a float with a
    fraction, a bool, a negative, an absurd magnitude, a word - is ``None``
    rather than an exception: one malformed field must not lose the whole
    reading, and a metric MeoBot could not read is exactly what "not recorded"
    means.
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


def _text(value: Any) -> str | None:
    """A non-empty display string, or ``None``."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__: list[str] = [
    "ANALYTICS_METRICS",
    "MAX_REMOTE_VALUE",
    "AnalyticsWindow",
    "YouTubeChannelMetricsProvider",
    "analytics_window",
]
