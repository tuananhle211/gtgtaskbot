"""The one HTTP object that talks to Meta, for both Facebook and Instagram.

Step 1F.2.4c. Facebook Pages and Instagram professional accounts are two MeoBot
platforms with two providers, and they are **one API**: the same OAuth dialog,
the same token exchange, the same Graph host, the same error shapes, and - this
is Meta's model rather than a shortcut - the same Page discovery call, because
an Instagram professional account is reached through the Facebook Page it is
linked to.

Writing that plumbing twice would mean two places to fix a Graph version bump
and two chances to classify an error differently. So it lives here once and the
two providers are thin adapters over it.

What this module will not do
-----------------------------

* **contact a host it was not compiled with.** Every URL comes from
  :class:`~meobot.integrations.meta.constants.MetaEndpoints`; nothing reads a
  host from a request, a channel row, or a redirect field inside a Graph
  response;
* **follow a redirect.** Graph does not need one, and an unfollowed redirect is
  one fewer way for a bearer token to be sent somewhere it was not meant for;
* **log a secret.** The log line names the operation and the status. Never the
  params - which carry the access token for Graph's `access_token` query form -
  never the headers, never the body.

Tokens travel as a header, not a query parameter
-------------------------------------------------

Graph accepts ``?access_token=``, and MeoBot does not use it. A token in a query
string is a token in an access log, in a proxy's request line and in an error
report. ``Authorization: Bearer`` keeps it out of all three, and Graph has
accepted it for years.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.domain.pr.channel_metrics import normalize_capture_time
from meobot.integrations.meta.constants import (
    DEFAULT_TIMEOUT_SECONDS,
    INSIGHTS_LAG_DAYS,
    MetaEndpoints,
)
from meobot.integrations.meta.errors import MetaApiError, classify_graph_error

logger = get_logger(__name__)

#: The largest count worth believing from a remote system. The same bound the
#: manual form applies, applied again because a Graph response is untrusted
#: input and a 10^18 follower count is a parsing bug, not an audience.
MAX_REMOTE_VALUE = 10**15


@dataclass(frozen=True, slots=True)
class InsightWindow:
    """One closed, settled reporting window.

    Closed and settled on purpose: it ends
    :data:`~meobot.integrations.meta.constants.INSIGHTS_LAG_DAYS` days before
    today, so the same window queried twice returns the same numbers. That is
    what makes the idempotency fingerprint meaningful rather than noise.
    """

    start: date
    end: date

    @property
    def start_iso(self) -> str:
        return self.start.isoformat()

    @property
    def end_iso(self) -> str:
        return self.end.isoformat()

    @property
    def start_utc(self) -> datetime:
        """Midnight UTC on the window's first day, as an aware instant.

        For filtering values MeoBot has already fetched - a post's
        ``created_time``, for instance - rather than for talking to Graph, which
        wants the epochs below. Explicitly UTC so the boundary does not move
        with the container's ``TZ``.
        """
        return datetime.combine(self.start, datetime.min.time(), tzinfo=UTC)

    @property
    def end_utc_exclusive(self) -> datetime:
        """Midnight UTC after the window's last day. Exclusive, like ``until``."""
        return datetime.combine(self.end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    @property
    def since(self) -> int:
        """Unix seconds at the window's first midnight. Graph wants epochs."""
        return int(datetime.combine(self.start, datetime.min.time()).timestamp())

    @property
    def until(self) -> int:
        """Unix seconds one day past the last day, because Graph's ``until`` is
        the exclusive upper bound of the daily series."""
        return int(datetime.combine(self.end + timedelta(days=1), datetime.min.time()).timestamp())


def insight_window(observed_at: datetime, *, days: int) -> InsightWindow:
    """The settled ``days``-long window ending before ``observed_at``.

    Inclusive of both bounds, so a 7-day window spans seven dates rather than
    eight.
    """
    end = normalize_capture_time(observed_at).date() - timedelta(days=INSIGHTS_LAG_DAYS)
    return InsightWindow(start=end - timedelta(days=days - 1), end=end)


@dataclass(frozen=True, slots=True)
class MetaPage:
    """One Facebook Page the authorizing person manages.

    ``access_token`` is the Page access token Graph returned alongside the id.
    It is **never** persisted for a Page that was not chosen, never returned to
    a browser, and never logged - see
    :meth:`MetaGraphClient.list_pages`.
    """

    id: str
    name: str
    access_token: str
    #: The linked Instagram professional account, when Graph reported one.
    instagram_account_id: str | None = None
    instagram_username: str | None = None


class MetaGraphClient:
    """Talks to Graph. Knows nothing about PR channels, sync or scheduling.

    Args:
        app_id: Meta app id. Deployment configuration.
        app_secret: Meta app secret. **Never** leaves this object, and never
            appears in a log, an audit row or a response.
        redirect_uri: Must match a Valid OAuth Redirect URI on the app exactly.
        endpoints: Graph hosts and the API version.
        client: Injected ``httpx.AsyncClient``. Tests pass a ``MockTransport``,
            which is how the whole connector is exercised without a network.
    """

    provider = "meta"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        endpoints: MetaEndpoints | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._endpoints = endpoints or MetaEndpoints()
        self._timeout = timeout_seconds
        self._client = client
        self._owns_client = client is None

    @property
    def endpoints(self) -> MetaEndpoints:
        return self._endpoints

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- OAuth ------------------------------------------------------------
    def authorization_url(self, *, state: str, scopes: tuple[str, ...]) -> str:
        """Meta's consent dialog, carrying our opaque ``state``.

        ``auth_type=rerequest`` so that a person who previously declined one of
        the scopes is asked again rather than silently returned with a token
        that cannot read insights - which would otherwise surface days later as
        an ``INSUFFICIENT_SCOPE`` sync failure nobody could explain.
        """
        from urllib.parse import urlencode

        query = urlencode(
            {
                "client_id": self._app_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": ",".join(scopes),
                "auth_type": "rerequest",
                "state": state,
            }
        )
        return f"{self._endpoints.authorize}?{query}"

    async def exchange_code(self, *, code: str) -> str:
        """A one-time authorization code for a **short-lived** user token.

        The code is never logged. It is a bearer credential for the few seconds
        it lives, and a log line containing one can be replayed into an account.
        """
        payload = await self._get(
            self._endpoints.token,
            params={
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "redirect_uri": self._redirect_uri,
                "code": code,
            },
            operation="exchange_code",
        )
        return self._require_token(payload, operation="exchange_code")

    async def exchange_for_long_lived(self, *, short_lived_token: str) -> str:
        """A short-lived user token for a long-lived one (~60 days).

        The step that makes unattended sync possible at all. A Page access token
        read with a *short-lived* user token expires with it; one read with a
        long-lived user token does not expire, which is the credential this
        connector ultimately stores.
        """
        payload = await self._get(
            self._endpoints.token,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "fb_exchange_token": short_lived_token,
            },
            operation="exchange_for_long_lived",
        )
        return self._require_token(payload, operation="exchange_for_long_lived")

    async def debug_token(self, *, token: str) -> Mapping[str, Any]:
        """What Meta says about a token - validity, scopes, expiry.

        Used to confirm a grant carries the scopes the connector needs before a
        connection is finalised, so a missing consent is a clear message at
        connect time rather than a puzzling failure on tomorrow's sweep.
        """
        payload = await self._get(
            f"{self._endpoints.graph}/debug_token",
            params={"input_token": token, "access_token": f"{self._app_id}|{self._app_secret}"},
            operation="debug_token",
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    # --- Discovery --------------------------------------------------------
    async def list_pages(self, *, user_token: str) -> list[MetaPage]:
        """Every Page this person manages, with its linked Instagram account.

        **One request** for the whole discovery step: the ``instagram_business_account``
        field is asked for on the same edge, so finding twelve Pages and their
        Instagram accounts costs one call rather than thirteen. That is the
        difference between a connect flow that feels instant and one that eats
        the app's rate limit before anybody has synced anything.

        The Page access tokens in the result are held in memory only. Exactly
        one of them is ever encrypted and stored - the one belonging to the Page
        the person actually chooses.
        """
        payload = await self._get(
            self._endpoints.node("me", "accounts"),
            params={
                "fields": ("id,name,access_token,instagram_business_account{id,username}"),
                "limit": "100",
            },
            token=user_token,
            operation="list_pages",
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MetaApiError(
                "me/accounts did not return a list",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        pages: list[MetaPage] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            page_id = _text(row.get("id"))
            token = _text(row.get("access_token"))
            if page_id is None or token is None:
                # A Page whose token Graph withheld cannot be synced, so it is
                # not offered. Silently skipping it is right: the person sees
                # the Pages they can actually connect.
                continue
            linked = row.get("instagram_business_account")
            linked_id = _text(linked.get("id")) if isinstance(linked, dict) else None
            linked_name = _text(linked.get("username")) if isinstance(linked, dict) else None
            pages.append(
                MetaPage(
                    id=page_id,
                    name=_text(row.get("name")) or page_id,
                    access_token=token,
                    instagram_account_id=linked_id,
                    instagram_username=linked_name,
                )
            )
        return pages

    # --- Reading ----------------------------------------------------------
    async def edge_page(
        self,
        node_id: str,
        edge: str,
        *,
        token: str,
        fields: str,
        limit: int,
        since: int | None = None,
        until: int | None = None,
        after: str | None = None,
    ) -> tuple[list[Mapping[str, Any]], str | None]:
        """One explicitly-sized page of an edge, and the cursor after it.

        **The next page is requested by cursor, never by following
        ``paging.next``.** Graph puts a fully-formed URL in that field and
        following it would mean the client contacting an address that arrived in
        a response - the one thing the module docstring says this class will
        never do. So the cursor is extracted and a fresh URL is built from
        :class:`~meobot.integrations.meta.constants.MetaEndpoints` exactly as
        the first page was.

        ``limit`` has no default. A caller that forgets a page size gets a
        ``TypeError`` here rather than Graph's own default and an unbounded walk
        through a Page's entire history - which is the failure this signature
        exists to make impossible.

        Returns:
            The page's rows, and the cursor to pass as ``after`` for the next
            page - or ``None`` when Graph reported no further page. A caller
            must still bound how many times it asks; see
            :data:`~meobot.integrations.meta.constants.MAX_POST_PAGES`.
        """
        params: dict[str, str] = {"fields": fields, "limit": str(limit)}
        if since is not None:
            params["since"] = str(since)
        if until is not None:
            params["until"] = str(until)
        if after is not None:
            params["after"] = after
        payload = await self._get(
            self._endpoints.node(node_id, edge),
            params=params,
            token=token,
            operation="edge_page",
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MetaApiError(
                f"{node_id}/{edge} did not return a list",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        return [row for row in rows if isinstance(row, dict)], _next_cursor(payload)

    async def fields(
        self,
        node_id: str,
        *,
        token: str,
        fields: str,
        operation_hint: str = "fields",
    ) -> Mapping[str, Any]:
        """Read named fields off one Graph node. One request, many fields.

        ``operation_hint`` only names the call in the log line. It exists so an
        operator running the capability probe can tell its requests apart from a
        sync's in the same log stream; it reaches no URL and no parameter.
        """
        return await self._get(
            self._endpoints.node(node_id),
            params={"fields": fields},
            token=token,
            operation=operation_hint,
        )

    async def insights_series(
        self,
        node_id: str,
        *,
        token: str,
        metrics: tuple[str, ...],
        window: InsightWindow,
        period: str = "day",
    ) -> dict[str, list[int]]:
        """A daily (or weekly) insight series per metric, as plain integers.

        One request for every metric in ``metrics`` - Graph accepts a
        comma-separated list and returns one entry per metric, which is the
        difference between three requests a day and twelve.

        A metric Graph declines to return is simply absent from the result.
        That is deliberate and is what
        :data:`~meobot.domain.pr.channel_connections.PrChannelSyncErrorCode`
        never sees: Meta deprecates and permission-gates individual metrics
        constantly, and one unavailable optional metric must not fail a sync
        that successfully read the other five.
        """
        payload = await self._get(
            self._endpoints.node(node_id, "insights"),
            params={
                "metric": ",".join(metrics),
                "period": period,
                "since": str(window.since),
                "until": str(window.until),
            },
            token=token,
            operation="insights_series",
        )
        return _series(payload)

    async def insights_total(
        self,
        node_id: str,
        *,
        token: str,
        metrics: tuple[str, ...],
        window: InsightWindow,
    ) -> dict[str, int]:
        """One **deduplicated total per metric** over the window.

        Instagram's ``metric_type=total_value`` form, and the only correct way
        to ask for reach over a period: reach counts *people*, so summing seven
        daily reach figures counts somebody who visited on three days three
        times. Meta computes the deduplicated figure; MeoBot must not try.
        """
        payload = await self._get(
            self._endpoints.node(node_id, "insights"),
            params={
                "metric": ",".join(metrics),
                "metric_type": "total_value",
                "since": str(window.since),
                "until": str(window.until),
            },
            token=token,
            operation="insights_total",
        )
        totals: dict[str, int] = {}
        rows = payload.get("data")
        if not isinstance(rows, list):
            return totals
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = _text(row.get("name"))
            total = row.get("total_value")
            value = parse_count(total.get("value")) if isinstance(total, dict) else None
            if name is not None and value is not None:
                totals[name] = value
        return totals

    # --- Internals --------------------------------------------------------
    def _require_token(self, payload: Mapping[str, Any], *, operation: str) -> str:
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise MetaApiError(
                f"{operation} returned no access token",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
            )
        return token

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, str],
        token: str | None = None,
        operation: str,
    ) -> Mapping[str, Any]:
        """One bounded GET to a hard-coded Graph host.

        The token, when there is one, goes in an ``Authorization`` header rather
        than in ``params`` - see the module docstring on why a query-string
        token is a logged token. The app-secret form used by ``debug_token`` is
        the documented exception and is passed as a parameter because Graph
        accepts it in no other way there.
        """
        client = self._ensure_client()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        try:
            response = await client.get(
                url,
                params=params,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise MetaApiError(
                f"{operation} timed out",
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise MetaApiError(
                f"{operation} could not reach Meta",
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc

        logger.info(
            "meta_request",
            extra={
                "provider": self.provider,
                "operation": operation,
                "status_code": response.status_code,
            },
        )

        payload: Any = {}
        if response.content:
            try:
                payload = response.json()
            except ValueError as exc:
                raise MetaApiError(
                    f"{operation} returned a body that is not JSON",
                    error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
                    status_code=response.status_code,
                ) from exc

        if response.status_code >= 400:
            raise MetaApiError(
                f"{operation} failed",
                error_code=classify_graph_error(response.status_code, payload),
                status_code=response.status_code,
            )
        if not isinstance(payload, dict):
            raise MetaApiError(
                f"{operation} returned a body that is not an object",
                error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
                status_code=response.status_code,
            )
        return payload

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client


def _next_cursor(payload: Mapping[str, Any]) -> str | None:
    """The ``after`` cursor, but only when Graph says another page exists.

    Both halves are required. Graph returns cursors on the **last** page too, so
    a client that paged on the cursor alone would ask for one empty page every
    time - and, on some edges, would loop. ``paging.next`` is the flag; the
    cursor is what is actually reused. The URL in ``paging.next`` is read for
    its presence and never for its value.
    """
    paging = payload.get("paging")
    if not isinstance(paging, dict) or not paging.get("next"):
        return None
    cursors = paging.get("cursors")
    return _text(cursors.get("after")) if isinstance(cursors, dict) else None


def _series(payload: Mapping[str, Any]) -> dict[str, list[int]]:
    """Graph's ``data[].values[].value`` shape, flattened to plain integers."""
    result: dict[str, list[int]] = {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _text(row.get("name"))
        values = row.get("values")
        if name is None or not isinstance(values, list):
            continue
        points: list[int] = []
        for point in values:
            if not isinstance(point, dict):
                continue
            number = parse_count(point.get("value"))
            if number is not None:
                points.append(number)
        result[name] = points
    return result


def parse_count(value: Any) -> int | None:
    """A non-negative count from an untrusted field, or ``None``.

    Graph sends counts as numbers and occasionally as strings, so both are
    accepted. Everything else - a fractional float, a bool, a negative, an
    absurd magnitude, a nested object (which is what an unsupported insight
    breakdown returns) - is ``None`` rather than an exception: one malformed
    field must not lose the whole reading, and a metric MeoBot could not read is
    exactly what "not recorded" means.
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


def now_window(days: int) -> InsightWindow:
    """The settled window ending before now. A convenience for callers."""
    return insight_window(utcnow(), days=days)


__all__: list[str] = [
    "MAX_REMOTE_VALUE",
    "InsightWindow",
    "MetaGraphClient",
    "MetaPage",
    "insight_window",
    "now_window",
    "parse_count",
]
