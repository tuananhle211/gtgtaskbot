"""Ask Meta what it will actually answer, one metric at a time.

Step 1F.2.4d. This module exists because of a production incident and a wrong
assumption, and it is the tool that stops both recurring.

The assumption was that a metric Graph no longer serves would simply be absent
from a 200 response. That is true when Meta **permission-gates** a metric and
false when it **retires** one: a retired name makes Graph reject the entire
comma-separated request, which is how ``page_impressions`` took the perfectly
valid ``page_post_engagements`` down with it and failed a whole channel sync.

The connector now degrades around that at runtime. What it could not do is
answer the question *before* anybody writes code: **which metrics does this
Graph version serve, for this Page, under the scopes this app was granted?**
Meta's changelog answers it for the version; only the Page can answer it for the
Page, because the answer also depends on the Page's own kind, its age, whether
it has ever posted a video, and which permissions the person who connected it
actually granted.

So the probe asks. Individually.

One metric per request, always
-------------------------------

:meth:`MetaCapabilityProbe.probe_page` never batches. Batching is what the connector
does to be cheap in production; batching here would reproduce the exact failure
being investigated - one bad name in a group of five and all five come back
refused, with no way to tell which was which. An operator running this once
against one Page can afford one request per candidate, and what they get back is
unambiguous.

Nothing here is a guess
------------------------

Every candidate in :data:`PAGE_INSIGHT_CANDIDATES` and the field lists is a
metric Meta documents, or one MeoBot already reads. There is deliberately **no**
mechanism for trying variations of a retired name to find a replacement:
``page_impressions_v2``, ``page_reach``, ``page_unique_users`` are not metrics,
and a tool that hunted for them would eventually get a 200 from something with a
similar name and a different definition, which is worse than a blank card.

What this never does
---------------------

**Print, log, return or store an access token.** :class:`MetricProbeResult`
has nowhere to put one, the probe takes the token as an argument and never
copies it into a result, and the CLI that drives this reads a credential from
the database, hands it here, and prints only metric names and counts. There is a
test asserting the token does not appear in the rendered report.

**Write anything.** The probe is read-only, against Graph and against MeoBot's
own database. It records nothing and changes no connection's health: a probe
that failed must not look like a sync that failed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from meobot.core.logging import get_logger
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.integrations.meta.client import InsightWindow, MetaGraphClient, insight_window
from meobot.integrations.meta.errors import MetaApiError

logger = get_logger(__name__)


class ProbeKind(StrEnum):
    """What sort of thing was asked for. Different asks fail differently."""

    #: A field on the Page node - ``followers_count``, ``fan_count``.
    PAGE_FIELD = "PAGE_FIELD"
    #: A Page Insights metric, asked for as a daily series over a window.
    PAGE_INSIGHT = "PAGE_INSIGHT"
    #: A field or summary on the ``published_posts`` edge.
    POST_FIELD = "POST_FIELD"


class ProbeVerdict(StrEnum):
    """What Graph said, in the only four categories worth acting on."""

    #: Answered, with a value. Safe to map.
    AVAILABLE = "AVAILABLE"
    #: Answered, but with nothing in it - a Page with no videos probed for
    #: ``page_video_views``, say. The metric exists; this Page has no data for
    #: it. **Not** the same as unsupported, and the difference decides whether
    #: to map the metric or leave it alone.
    EMPTY = "EMPTY"
    #: Graph refused the name. Retired, or never existed on this node type.
    UNSUPPORTED = "UNSUPPORTED"
    #: Graph refused the *caller* - the grant does not cover this. A different
    #: fix entirely: reconnect with wider consent, not "stop asking".
    NOT_PERMITTED = "NOT_PERMITTED"


@dataclass(frozen=True, slots=True)
class MetricProbeResult:
    """One candidate, and what asking for it produced.

    **Carries no token and no raw Graph payload.** ``sample`` is a short
    rendering MeoBot composed - a count, a series length - never the response
    body, which on some edges contains other people's writing and on all of them
    is provider prose that has no business on a Vietnamese screen.
    """

    metric: str
    kind: ProbeKind
    verdict: ProbeVerdict
    #: MeoBot's own one-line explanation, in English, for an operator's terminal.
    detail: str | None = None
    #: A short, safe rendering of what came back: ``"124812"``, ``"30 điểm"``.
    sample: str | None = None

    @property
    def usable(self) -> bool:
        """Whether this metric could be mapped to a column today."""
        return self.verdict is ProbeVerdict.AVAILABLE


#: Page node fields worth confirming.
#:
#: ``followers_count`` and ``fan_count`` are what the connector already reads
#: and are here so a probe run is a complete picture rather than a list of the
#: doubtful half. The rest are cheap to ask and answer real questions:
#: ``name``/``username``/``link`` prove the token resolves to the Page, and
#: ``category`` and ``verification_status`` often explain *why* a metric is
#: gated - an unverified Page and a verified one do not get the same answers.
PAGE_FIELD_CANDIDATES: tuple[str, ...] = (
    "followers_count",
    "fan_count",
    "name",
    "username",
    "link",
    "category",
    "verification_status",
)

#: Page Insights metrics worth asking about, in the order a report reads best.
#:
#: The first is confirmed working in production. The two after it are what Step
#: 1F.2.4d hopes for and maps when available. The last group is here **precisely
#: because it is expected to fail**: printing ``page_impressions UNSUPPORTED``
#: beside the others is how an operator confirms, in ten seconds, that the blank
#: reach cards are Meta's decision and not a MeoBot bug - which is the single
#: most frequently asked question about this dashboard.
PAGE_INSIGHT_CANDIDATES: tuple[str, ...] = (
    # Confirmed.
    "page_post_engagements",
    # Mapped when available.
    "page_video_views",
    "page_views_total",
    # Expected to be refused on v23. Probed so the refusal is visible evidence
    # rather than folklore.
    "page_impressions",
    "page_impressions_unique",
    "page_fans",
    "page_fan_adds",
    "page_fan_removes",
)

#: Post-edge fields worth confirming, asked for one at a time.
#:
#: ``shares`` is listed even though it is not a summary, because its absence on
#: a post with no shares is the behaviour every count in this file depends on
#: reading correctly.
POST_FIELD_CANDIDATES: tuple[str, ...] = (
    "id",
    "created_time",
    "permalink_url",
    "message",
    "reactions.summary(total_count).limit(0)",
    "comments.summary(total_count).limit(0)",
    "shares",
    "status_type",
    "attachments{media_type}",
)

#: How many posts a probe reads. One page, small: the probe is asking whether a
#: field *answers*, and one row proves that as well as a hundred do.
PROBE_POST_LIMIT = 5


@dataclass(frozen=True, slots=True)
class PageProbeReport:
    """Everything one probe run found out about one Page.

    ``page_id`` and ``page_name`` come from Graph rather than from the caller,
    so a report cannot claim to be about a Page the token does not actually
    resolve to.
    """

    page_id: str
    page_name: str | None
    window: InsightWindow
    fields: tuple[MetricProbeResult, ...] = ()
    insights: tuple[MetricProbeResult, ...] = ()
    posts: tuple[MetricProbeResult, ...] = ()
    #: How many posts the post probe actually read, or ``None`` when the edge
    #: could not be listed at all.
    posts_read: int | None = None

    @property
    def results(self) -> tuple[MetricProbeResult, ...]:
        return self.fields + self.insights + self.posts

    def available(self) -> tuple[str, ...]:
        """Every candidate this Page will answer for, in probe order."""
        return tuple(result.metric for result in self.results if result.usable)

    def unavailable(self) -> tuple[str, ...]:
        """Every candidate it will not - refused, gated, or genuinely empty."""
        return tuple(result.metric for result in self.results if not result.usable)


class MetaCapabilityProbe:
    """Asks a live Page what it will answer, without changing anything.

    Args:
        client: The same Graph transport the connector uses, so a probe result
            is evidence about the code path production takes rather than about a
            second HTTP client that might differ.
    """

    provider = "meta"

    def __init__(self, client: MetaGraphClient) -> None:
        self._client = client

    async def probe_page(
        self,
        *,
        access_token: str,
        now: datetime,
        window_days: int = 30,
        insight_candidates: Sequence[str] = PAGE_INSIGHT_CANDIDATES,
        field_candidates: Sequence[str] = PAGE_FIELD_CANDIDATES,
        post_candidates: Sequence[str] = POST_FIELD_CANDIDATES,
    ) -> PageProbeReport:
        """Probe every candidate against whichever Page this token belongs to.

        The Page is resolved from the token via ``/me`` rather than taken as an
        argument: a probe report has to be about the Page the credential
        actually reaches, and an operator who passes the wrong id should not get
        a confident report about it.

        Nothing here raises for a metric that fails - that *is* the result. An
        authorization failure on the very first call does raise, because a probe
        that cannot identify the Page has nothing to report.
        """
        identity = await self._client.fields(
            "me", token=access_token, fields="id,name", operation_hint="probe_identity"
        )
        page_id = _text(identity.get("id"))
        if page_id is None:
            raise MetaApiError(
                "the token did not resolve to a Page",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        window = insight_window(now, days=window_days)

        fields = tuple(
            [
                await self._probe_field(page_id, token=access_token, field=candidate)
                for candidate in field_candidates
            ]
        )
        insights = tuple(
            [
                await self._probe_insight(
                    page_id, token=access_token, metric=candidate, window=window
                )
                for candidate in insight_candidates
            ]
        )
        posts, posts_read = await self._probe_posts(
            page_id, token=access_token, window=window, candidates=post_candidates
        )
        return PageProbeReport(
            page_id=page_id,
            page_name=_text(identity.get("name")),
            window=window,
            fields=fields,
            insights=insights,
            posts=posts,
            posts_read=posts_read,
        )

    # --- One candidate at a time ------------------------------------------
    async def _probe_field(self, page_id: str, *, token: str, field: str) -> MetricProbeResult:
        try:
            payload = await self._client.fields(
                page_id, token=token, fields=field, operation_hint="probe_field"
            )
        except MetaApiError as exc:
            return _refusal(field, ProbeKind.PAGE_FIELD, exc)
        # Graph answers a field request with the node's id plus whatever it
        # could read. A field it declined to read is simply not in the object,
        # which for a *field* - unlike an insight metric - is the normal shape.
        name = field.split("{", 1)[0].split(".", 1)[0]
        if name not in payload:
            return MetricProbeResult(
                metric=field,
                kind=ProbeKind.PAGE_FIELD,
                verdict=ProbeVerdict.EMPTY,
                detail="the Page node carried no such field",
            )
        return MetricProbeResult(
            metric=field,
            kind=ProbeKind.PAGE_FIELD,
            verdict=ProbeVerdict.AVAILABLE,
            sample=_sample(payload.get(name)),
        )

    async def _probe_insight(
        self, page_id: str, *, token: str, metric: str, window: InsightWindow
    ) -> MetricProbeResult:
        try:
            series = await self._client.insights_series(
                page_id, token=token, metrics=(metric,), window=window
            )
        except MetaApiError as exc:
            return _refusal(metric, ProbeKind.PAGE_INSIGHT, exc)
        points = series.get(metric)
        if not points:
            return MetricProbeResult(
                metric=metric,
                kind=ProbeKind.PAGE_INSIGHT,
                verdict=ProbeVerdict.EMPTY,
                detail="accepted, but returned no data points for this window",
            )
        return MetricProbeResult(
            metric=metric,
            kind=ProbeKind.PAGE_INSIGHT,
            verdict=ProbeVerdict.AVAILABLE,
            sample=f"{len(points)} point(s), total {sum(points)}",
        )

    async def _probe_posts(
        self,
        page_id: str,
        *,
        token: str,
        window: InsightWindow,
        candidates: Sequence[str],
    ) -> tuple[tuple[MetricProbeResult, ...], int | None]:
        """Probe post fields one at a time, on one small page of posts.

        Individually here too, for the same reason: ``published_posts`` refuses
        a whole ``fields=`` list containing one bad expansion, and a grouped
        probe would report nine unsupported fields when one was.
        """
        results: list[MetricProbeResult] = []
        posts_read: int | None = None
        for candidate in candidates:
            try:
                rows, _ = await self._client.edge_page(
                    page_id,
                    "published_posts",
                    token=token,
                    fields=candidate,
                    limit=PROBE_POST_LIMIT,
                    since=window.since,
                    until=window.until,
                )
            except MetaApiError as exc:
                results.append(_refusal(candidate, ProbeKind.POST_FIELD, exc))
                continue
            posts_read = max(posts_read or 0, len(rows))
            if not rows:
                results.append(
                    MetricProbeResult(
                        metric=candidate,
                        kind=ProbeKind.POST_FIELD,
                        verdict=ProbeVerdict.EMPTY,
                        detail="the Page published nothing in this window",
                    )
                )
                continue
            name = candidate.split("{", 1)[0].split(".", 1)[0]
            present = sum(1 for row in rows if name in row)
            results.append(
                MetricProbeResult(
                    metric=candidate,
                    kind=ProbeKind.POST_FIELD,
                    verdict=(ProbeVerdict.AVAILABLE if present else ProbeVerdict.EMPTY),
                    detail=(None if present else "accepted, but no post in this window carried it"),
                    sample=f"{present}/{len(rows)} post(s)",
                )
            )
        return tuple(results), posts_read


def _refusal(metric: str, kind: ProbeKind, exc: MetaApiError) -> MetricProbeResult:
    """Classify one refusal, without carrying Meta's own words forward.

    The distinction that matters is ``BAD_RESPONSE`` (Graph rejected the
    *name* - retired, or wrong node type) versus ``INSUFFICIENT_SCOPE`` (Graph
    rejected the *caller*). They look identical on a terminal if collapsed and
    they need opposite responses: stop asking, versus reconnect with wider
    consent.

    Everything else - a revoked token, a rate limit, an outage - is reported as
    inconclusive rather than as "unsupported", because a probe that recorded a
    five-minute Meta outage as "this metric does not exist" would be worse than
    no probe at all: somebody would delete a working mapping on the strength of
    it.
    """
    if exc.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE:
        return MetricProbeResult(
            metric=metric,
            kind=kind,
            verdict=ProbeVerdict.NOT_PERMITTED,
            detail="the granted permissions do not cover this - reconnect with wider consent",
        )
    if exc.error_code is PrChannelSyncErrorCode.BAD_RESPONSE:
        return MetricProbeResult(
            metric=metric,
            kind=kind,
            verdict=ProbeVerdict.UNSUPPORTED,
            detail="Graph rejected the name on this API version",
        )
    return MetricProbeResult(
        metric=metric,
        kind=kind,
        verdict=ProbeVerdict.UNSUPPORTED,
        detail=f"inconclusive - the call failed as {exc.error_code.value}, try again later",
    )


def _sample(value: Any) -> str | None:
    """A short, safe rendering of a field value.

    Truncated hard. A probe report is a terminal table, and a Page description
    or a link with tracking parameters would wrap it into unreadability.
    """
    if value is None:
        return None
    text = str(value).strip().replace("\\n", " ")
    if not text:
        return None
    return text if len(text) <= 60 else text[:57] + "..."


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__: list[str] = [
    "PAGE_FIELD_CANDIDATES",
    "PAGE_INSIGHT_CANDIDATES",
    "POST_FIELD_CANDIDATES",
    "PROBE_POST_LIMIT",
    "MetaCapabilityProbe",
    "MetricProbeResult",
    "PageProbeReport",
    "ProbeKind",
    "ProbeVerdict",
]
