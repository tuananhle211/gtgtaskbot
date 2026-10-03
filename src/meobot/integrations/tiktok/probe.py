"""Ask TikTok what it will actually answer, one field at a time.

Step 1F.2.6. This module exists because nothing in this repository has ever made
a request to TikTok, and because the question that decides the *next* milestone
cannot be answered from documentation.

That question is: **which fields does the Display API serve, for this account,
under the scopes this app was actually approved for?** TikTok's reference
answers it for the product; only the account can answer it for the account,
because the answer also depends on the account's type, whether it has ever
posted, whether the app passed review for ``user.info.stats`` and ``video.list``,
and which scopes the person ticked at the consent screen.

In particular, whether ``view_count``, ``like_count``, ``comment_count`` and
``share_count`` come back on ``video/list`` rows decides whether MeoBot can
derive 7- and 30-day windows at all. Building that derivation first and finding
out afterwards would put invented numbers on a management dashboard.

One field per request, always
------------------------------

:meth:`TikTokCapabilityProbe.probe_account` never batches. Batching is what the
connector does to be cheap in production; batching here would reproduce the
exact failure being investigated - TikTok refuses a whole ``fields=`` list over
one name it will not serve, so one bad field in a group of four comes back as
four refusals with no way to tell which was which. An operator running this once
against one account can afford one request per candidate, and what they get back
is unambiguous.

The video fields are the exception that proves it: they are probed on **one**
page of videos per candidate, because a field's presence is a property of the
row rather than of the request, and asking for it alone is what proves TikTok
serves it rather than that some other field carried the request.

Nothing here is a guess
------------------------

Every candidate is a field TikTok documents for these endpoints. There is
deliberately **no** mechanism for trying variations to find a replacement -
``play_count``, ``video_views``, ``total_likes`` are not fields, and a tool that
hunted for them would eventually get a 200 from something with a similar name
and a different definition, which is worse than a blank card.

What this never does
---------------------

**Print, log, return or store a token.** :class:`FieldProbeResult` has nowhere
to put one, the probe takes the access token as an argument and never copies it
into a result, and the CLI that drives this reads a credential from the
database, hands it here, and prints only field names and counts. There is a test
that renders a full report over a fake TikTok and asserts neither the access
token nor the refresh token appears anywhere in the output.

**Write anything.** Read-only against TikTok and against MeoBot's own database.
It records nothing and changes no connection's health: a probe that failed must
not look like a sync that failed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from meobot.core.logging import get_logger
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.integrations.tiktok.client import TikTokApiClient
from meobot.integrations.tiktok.constants import (
    USER_FIELDS_BASIC,
    USER_FIELDS_PROFILE,
    USER_FIELDS_STATS,
    USER_FIELDS_USERNAME,
)
from meobot.integrations.tiktok.errors import TikTokApiError

logger = get_logger(__name__)


class ProbeKind(StrEnum):
    """What sort of thing was asked for. Different asks fail differently."""

    #: A field on ``/v2/user/info/`` - identity or profile.
    USER_FIELD = "USER_FIELD"
    #: A counter on ``/v2/user/info/`` gated behind ``user.info.stats``.
    USER_STAT = "USER_STAT"
    #: A field on a ``/v2/video/list/`` row.
    VIDEO_FIELD = "VIDEO_FIELD"


class ProbeVerdict(StrEnum):
    """What TikTok said, in the five categories worth acting on.

    Five rather than the Meta probe's four, and the extra one is
    :attr:`NOT_RETURNED`, which TikTok makes necessary: it accepts a field, says
    ``error.code == "ok"``, and simply omits it from the object. Graph does that
    too for a *field*, but TikTok does it for **counters on video rows**, where
    it is the difference between "this account's videos have no views" and "this
    API version does not serve view counts to this app" - and those want
    opposite decisions in the next milestone.
    """

    #: Answered, with a value. Safe to map.
    AVAILABLE = "AVAILABLE"
    #: Answered, and the value is genuinely empty or zero - an account with no
    #: videos probed for ``video_count``, say. The field exists; this account
    #: has no data for it. **Not** the same as unsupported.
    EMPTY = "EMPTY"
    #: Accepted the request and left the field out of the response entirely.
    #: The one TikTok makes necessary - see the class docstring.
    NOT_RETURNED = "NOT_RETURNED"
    #: TikTok refused the *caller* - the grant, or the app's approval, does not
    #: cover this. A different fix entirely: reconnect with wider consent, or
    #: get the app approved for the scope. Waiting does not help.
    NOT_PERMITTED = "NOT_PERMITTED"
    #: TikTok refused the *name*. Not a field on this endpoint at this API
    #: version. Nothing fixes it and nobody should keep asking.
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class FieldProbeResult:
    """One candidate, and what asking for it produced.

    **Carries no token and no raw TikTok payload.** ``sample`` is a short
    rendering MeoBot composed - a count, a ratio of rows - never the response
    body, which on the video endpoints contains somebody's video titles and on
    all of them is provider data that has no business on a terminal by accident.
    """

    field: str
    kind: ProbeKind
    verdict: ProbeVerdict
    #: MeoBot's own one-line explanation, in English, for an operator's terminal.
    detail: str | None = None
    #: A short, safe rendering of what came back: ``"124812"``, ``"18/20 rows"``.
    sample: str | None = None

    @property
    def usable(self) -> bool:
        """Whether this field could be mapped to a column today."""
        return self.verdict is ProbeVerdict.AVAILABLE


#: Identity fields worth confirming, one at a time.
#:
#: ``open_id`` is what a connection binds to and is here so a probe run is a
#: complete picture rather than a list of the doubtful half. The rest answer
#: real questions: ``username`` and ``profile_deep_link`` are what let a person
#: confirm they authorized the account they meant, and ``is_verified`` often
#: explains *why* another field is gated.
USER_FIELD_CANDIDATES: tuple[str, ...] = (
    *USER_FIELDS_BASIC,
    *USER_FIELDS_USERNAME,
    *USER_FIELDS_PROFILE,
)

#: The four counters behind ``user.info.stats``, probed separately from the
#: identity fields because they are behind a **different scope** and an app is
#: routinely approved for one and not the other. Printing them as their own
#: block is how an operator sees that distinction in one glance.
USER_STAT_CANDIDATES: tuple[str, ...] = USER_FIELDS_STATS

#: Video fields worth confirming, in the order a report reads best.
#:
#: The first block is what a video *is* and is expected to answer. The second is
#: the block this whole probe exists for: if ``view_count`` and its three
#: siblings do not come back, MeoBot cannot derive a 7- or 30-day window from
#: the Display API at all, and the next milestone is a different milestone.
VIDEO_FIELD_CANDIDATES: tuple[str, ...] = (
    "id",
    "create_time",
    "title",
    "video_description",
    "duration",
    "cover_image_url",
    "embed_link",
    "share_url",
    # The block that decides the next step.
    "view_count",
    "like_count",
    "comment_count",
    "share_count",
)

#: How many videos a probe reads per candidate. One page, small: the probe is
#: asking whether a field *answers*, and five rows prove that as well as a
#: hundred do - while keeping a probe run comfortably inside any rate limit.
PROBE_VIDEO_LIMIT = 5

#: How many pages the pagination check walks. Two is enough to prove the cursor
#: and ``has_more`` behave as documented, which is the only thing a bounded walk
#: in a later milestone would depend on, and it is bounded here for the same
#: reason it would be bounded there.
PROBE_MAX_PAGES = 2


@dataclass(frozen=True, slots=True)
class AccountProbeReport:
    """Everything one probe run found out about one TikTok account.

    ``open_id`` and ``display_name`` come from TikTok rather than from the
    caller, so a report cannot claim to be about an account the token does not
    actually resolve to.
    """

    open_id: str
    display_name: str | None = None
    #: The scopes stored on the connection, as TikTok granted them. Printed
    #: because a missing scope explains most of what a report will say, and
    #: scope names are not secrets - they are what somebody consented to.
    granted_scopes: tuple[str, ...] = ()
    identity: tuple[FieldProbeResult, ...] = ()
    stats: tuple[FieldProbeResult, ...] = ()
    videos: tuple[FieldProbeResult, ...] = ()
    #: How many videos the video probe actually read, or ``None`` when the
    #: endpoint could not be reached at all.
    videos_read: int | None = None
    #: How many pages the pagination check walked, and whether TikTok said there
    #: were more after them. Evidence that a bounded walk is possible, which is
    #: the precondition for every windowed metric the next milestone might add.
    pages_walked: int = 0
    more_pages_available: bool = False
    #: Why the video probe could not run, when it could not. ``None`` on a run
    #: that reached the endpoint, whatever the individual fields did.
    video_refusal: FieldProbeResult | None = None

    @property
    def results(self) -> tuple[FieldProbeResult, ...]:
        return self.identity + self.stats + self.videos

    def available(self) -> tuple[str, ...]:
        """Every candidate this account will answer for, in probe order."""
        return tuple(result.field for result in self.results if result.usable)

    def unavailable(self) -> tuple[str, ...]:
        """Every candidate it will not - refused, gated, omitted, or empty."""
        return tuple(result.field for result in self.results if not result.usable)

    def limitations(self) -> tuple[FieldProbeResult, ...]:
        """The refusals that will not fix themselves, for the report's tail.

        ``NOT_PERMITTED`` and ``UNSUPPORTED`` only. An ``EMPTY`` field is a
        working field on a quiet account and time fixes it; listing it beside a
        scope the app was never approved for would bury the one that matters.
        """
        return tuple(
            result
            for result in self.results
            if result.verdict in (ProbeVerdict.NOT_PERMITTED, ProbeVerdict.UNSUPPORTED)
        )


class TikTokCapabilityProbe:
    """Asks a live TikTok account what it will answer, without changing anything.

    Args:
        client: The same transport the connector uses, so a probe result is
            evidence about the code path production takes rather than about a
            second HTTP client that might differ.
    """

    provider = "tiktok"

    def __init__(self, client: TikTokApiClient) -> None:
        self._client = client

    async def probe_account(
        self,
        *,
        access_token: str,
        granted_scopes: Sequence[str] = (),
        identity_candidates: Sequence[str] = USER_FIELD_CANDIDATES,
        stat_candidates: Sequence[str] = USER_STAT_CANDIDATES,
        video_candidates: Sequence[str] = VIDEO_FIELD_CANDIDATES,
    ) -> AccountProbeReport:
        """Probe every candidate against whichever account this token belongs to.

        The account is resolved from the token rather than taken as an argument:
        a probe report has to be about the account the credential actually
        reaches, and an operator who passes the wrong channel code should not
        get a confident report about it.

        Nothing here raises for a field that fails - that *is* the result. A
        failure on the very first call does raise, because a probe that cannot
        identify the account has nothing to report.
        """
        identity = await self._identity(access_token=access_token)
        open_id = _text(identity.get("open_id"))
        if open_id is None:
            raise TikTokApiError(
                "the token did not resolve to a TikTok account",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )

        identity_results = tuple(
            [
                await self._probe_user_field(field, token=access_token, kind=ProbeKind.USER_FIELD)
                for field in identity_candidates
            ]
        )
        stat_results = tuple(
            [
                await self._probe_user_field(field, token=access_token, kind=ProbeKind.USER_STAT)
                for field in stat_candidates
            ]
        )
        videos, videos_read, refusal = await self._probe_videos(
            token=access_token, candidates=video_candidates
        )
        pages, more = await self._probe_pagination(token=access_token)

        return AccountProbeReport(
            open_id=open_id,
            display_name=_text(identity.get("display_name")),
            granted_scopes=tuple(granted_scopes),
            identity=identity_results,
            stats=stat_results,
            videos=videos,
            videos_read=videos_read,
            pages_walked=pages,
            more_pages_available=more,
            video_refusal=refusal,
        )

    # --- One candidate at a time ------------------------------------------
    async def _identity(self, *, access_token: str) -> dict[str, Any]:
        """Resolve the account, on the narrowest field set that can do it.

        ``open_id`` and ``display_name`` are both ``user.info.basic``, which is
        the scope Login Kit will not issue a token without - so if this call
        fails, nothing else in the report would have worked either and failing
        here is the honest outcome.
        """
        return dict(
            await self._client.user_info(
                access_token=access_token,
                fields=("open_id", "display_name"),
                operation_hint="probe_identity",
            )
        )

    async def _probe_user_field(
        self, field: str, *, token: str, kind: ProbeKind
    ) -> FieldProbeResult:
        try:
            payload = await self._client.user_info(
                access_token=token, fields=(field,), operation_hint="probe_user_field"
            )
        except TikTokApiError as exc:
            return _refusal(field, kind, exc)
        if field not in payload:
            return FieldProbeResult(
                field=field,
                kind=kind,
                verdict=ProbeVerdict.NOT_RETURNED,
                detail="accepted, but the account object carried no such field",
            )
        value = payload.get(field)
        return FieldProbeResult(
            field=field,
            kind=kind,
            verdict=ProbeVerdict.EMPTY if _is_empty(value) else ProbeVerdict.AVAILABLE,
            detail=None if not _is_empty(value) else "answered, with nothing in it",
            sample=_sample(value),
        )

    async def _probe_videos(
        self, *, token: str, candidates: Sequence[str]
    ) -> tuple[tuple[FieldProbeResult, ...], int | None, FieldProbeResult | None]:
        """Probe video fields one at a time, on one small page of videos.

        Individually here too, for the same reason: ``video/list`` refuses a
        whole ``fields=`` list containing one bad name, and a grouped probe
        would report twelve unsupported fields when one was.

        ``id`` is asked for alongside every candidate rather than alone, because
        TikTok requires at least one field and a row with only a counter on it
        cannot be matched back to anything - and because if ``id`` itself is
        refused there is no point continuing.

        Returns:
            The per-field results, how many rows were read, and - when the
            endpoint could not be reached at all - one result explaining why, so
            the report says "video.list was refused" rather than printing twelve
            identical refusals.
        """
        results: list[FieldProbeResult] = []
        videos_read: int | None = None
        for candidate in candidates:
            fields = ("id",) if candidate == "id" else ("id", candidate)
            try:
                page = await self._client.video_page(
                    access_token=token,
                    fields=fields,
                    max_count=PROBE_VIDEO_LIMIT,
                    operation_hint="probe_video_field",
                )
            except TikTokApiError as exc:
                refusal = _refusal(candidate, ProbeKind.VIDEO_FIELD, exc)
                if candidate == "id":
                    # The endpoint itself is unreachable. Probing eleven more
                    # fields would produce eleven copies of this one refusal.
                    return (), None, refusal
                results.append(refusal)
                continue
            videos_read = max(videos_read or 0, len(page.videos))
            if not page.videos:
                results.append(
                    FieldProbeResult(
                        field=candidate,
                        kind=ProbeKind.VIDEO_FIELD,
                        verdict=ProbeVerdict.EMPTY,
                        detail="the account has published no video this probe could read",
                    )
                )
                continue
            present = sum(1 for row in page.videos if candidate in row)
            filled = sum(
                1 for row in page.videos if candidate in row and not _is_empty(row.get(candidate))
            )
            results.append(
                FieldProbeResult(
                    field=candidate,
                    kind=ProbeKind.VIDEO_FIELD,
                    verdict=_video_verdict(present, filled),
                    detail=_video_detail(present, filled),
                    sample=f"{present}/{len(page.videos)} rows",
                )
            )
        return tuple(results), videos_read, None

    async def _probe_pagination(self, *, token: str) -> tuple[int, bool]:
        """Walk at most :data:`PROBE_MAX_PAGES` pages and report what happened.

        Not a field probe - a **mechanism** probe. Every windowed metric a later
        milestone might derive depends on being able to page backwards through
        the video list in bounded steps, and this is the cheapest possible proof
        that the cursor and ``has_more`` behave the way that would require.

        Failures are swallowed to a zero walk rather than raised: the field
        results above are the report's substance and a pagination hiccup must
        not cost them.
        """
        pages = 0
        cursor: int | None = None
        more = False
        while pages < PROBE_MAX_PAGES:
            try:
                page = await self._client.video_page(
                    access_token=token,
                    fields=("id", "create_time"),
                    max_count=PROBE_VIDEO_LIMIT,
                    cursor=cursor,
                    operation_hint="probe_pagination",
                )
            except TikTokApiError:
                logger.info(
                    "tiktok_probe_pagination_unavailable", extra={"provider": self.provider}
                )
                break
            pages += 1
            more = page.has_more
            if not page.has_more or page.cursor is None:
                break
            cursor = page.cursor
        return pages, more


def _refusal(field: str, kind: ProbeKind, exc: TikTokApiError) -> FieldProbeResult:
    """Classify one refusal, without carrying TikTok's own words forward.

    The distinction that matters is ``BAD_RESPONSE`` (TikTok rejected the
    *name* - ``invalid_params``, so not a field on this endpoint) versus
    ``INSUFFICIENT_SCOPE`` (it rejected the *caller* - the grant or the app's
    approval). They look identical on a terminal if collapsed and they need
    opposite responses: stop asking, versus get the app approved and reconnect.

    Everything else - a revoked token, a rate limit, an outage - is reported as
    inconclusive rather than as "unsupported", because a probe that recorded a
    five-minute TikTok outage as "this field does not exist" would be worse than
    no probe at all: somebody would rule out a working field on the strength of
    it.
    """
    if exc.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE:
        return FieldProbeResult(
            field=field,
            kind=kind,
            verdict=ProbeVerdict.NOT_PERMITTED,
            detail="the granted scopes, or the app's approved scopes, do not cover this",
        )
    if exc.error_code is PrChannelSyncErrorCode.BAD_RESPONSE:
        return FieldProbeResult(
            field=field,
            kind=kind,
            verdict=ProbeVerdict.UNSUPPORTED,
            detail="TikTok rejected the field name on this API version",
        )
    return FieldProbeResult(
        field=field,
        kind=kind,
        verdict=ProbeVerdict.UNSUPPORTED,
        detail=f"inconclusive - the call failed as {exc.error_code.value}, try again later",
    )


def _video_verdict(present: int, filled: int) -> ProbeVerdict:
    """Three outcomes for a video field, and the middle one is the point.

    A field that is on **no** row was accepted and omitted - ``NOT_RETURNED``,
    which for the four counters means the Display API will not serve them to
    this app and no amount of MeoBot code changes that. A field present but
    empty on every row is a real measurement of nothing. Anything with a value
    is available.
    """
    if present == 0:
        return ProbeVerdict.NOT_RETURNED
    return ProbeVerdict.AVAILABLE if filled else ProbeVerdict.EMPTY


def _video_detail(present: int, filled: int) -> str | None:
    if present == 0:
        return "accepted, but no video row carried it"
    if not filled:
        return "present on every row and empty on all of them"
    return None


def _is_empty(value: Any) -> bool:
    """Whether a returned value is genuinely nothing.

    ``0`` counts as empty **here and only here**, and the reason is worth
    stating: this is a capability probe, not a reading. An account whose
    ``follower_count`` is 0 tells an operator nothing about whether the field
    works, and calling it ``AVAILABLE`` beside a real number would blur the one
    distinction the report exists to draw. Everywhere else in MeoBot a measured
    zero is an answer and is displayed as one.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return value == 0
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict):
        return not value
    return False


def _sample(value: Any) -> str | None:
    """A short, safe rendering of a field value.

    Truncated hard. A probe report is a terminal table, and a bio or a signed
    CDN URL with a hundred characters of query string would wrap it into
    unreadability.
    """
    if value is None:
        return None
    text = str(value).strip().replace("\n", " ")
    if not text:
        return None
    return text if len(text) <= 60 else text[:57] + "..."


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__: list[str] = [
    "PROBE_MAX_PAGES",
    "PROBE_VIDEO_LIMIT",
    "USER_FIELD_CANDIDATES",
    "USER_STAT_CANDIDATES",
    "VIDEO_FIELD_CANDIDATES",
    "AccountProbeReport",
    "FieldProbeResult",
    "ProbeKind",
    "ProbeVerdict",
    "TikTokCapabilityProbe",
]
