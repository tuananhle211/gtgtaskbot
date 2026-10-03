"""Facebook Page and Instagram professional-account connectors.

Step 1F.2.4c. Two providers, one transport
(:class:`~meobot.integrations.meta.client.MetaGraphClient`), because Facebook
and Instagram are one API with two vocabularies.

The token lifecycle, which is not Google's
-------------------------------------------

Meta has **no refresh token**. The chain is:

``code`` → short-lived user token (~1h) → long-lived user token (~60d) →
**Page access token**, which when derived from a long-lived user token does not
expire at all.

That last one is the durable credential MeoBot stores, and it is used *directly*
as the bearer credential - never exchanged for anything. Which is why
:meth:`acquire_access` here makes no request: it returns what it was given. The
port names the outcome rather than the mechanism precisely so that both this and
Google's exchange can be honest implementations of it, and migration 0029
renamed the column for the same reason.

An Instagram professional account uses **the Page's token too**. That is Meta's
model: the account is reached through the Facebook Page it is linked to. It does
not make the MeoBot channel a Facebook channel - the platform identity is the
account being measured, and the discovery relationship is an implementation
detail of Meta's graph.

What Facebook cannot currently report
--------------------------------------

``reach_7d``, ``reach_30d``, ``impressions_7d`` and ``impressions_30d`` are all
``None`` for a Facebook Page, and that is Meta's decision rather than a mapping
MeoBot declined to make. Graph v23 removed ``page_impressions`` and
``page_impressions_unique`` from Page Insights; asking for either returns
``(#100) The value must be a valid insights metric``.

Two consequences worth stating, because they shaped the code:

* **Graph rejects the entire request** when one metric name in a comma-separated
  list is unknown. That is why a single retired metric took a production sync
  down with it rather than simply going missing, and why optional insight
  fetches now degrade through :meth:`_MetaProvider._optional_insights` instead of
  trusting the response to be partial;
* **``views_*`` stays unmapped for a different reason** - Meta's account-level
  view metrics are *profile* views, which is not what "views" means on a YouTube
  or TikTok card sitting in the same channel list.

Instagram is unaffected: its ``metric_type=total_value`` form returns a
deduplicated total over an arbitrary window, so ``reach_7d`` and ``reach_30d``
are both exact there.

What Facebook cannot currently report *because of a permission*
----------------------------------------------------------------

A separate thing from the paragraph above, and it is separate because the fix is
different: ``reactions_30d`` and ``comments_30d`` are ``None`` for a Page whose
grant does not cover the post interaction summaries. Meta gates other people's
activity on a Page's posts behind a permission this app does not hold, and Graph
refuses the *whole* ``published_posts`` request over it rather than answering
with the fields it will serve - which is how two optional counts once failed an
entire channel's sync with ``INSUFFICIENT_SCOPE``.

:meth:`FacebookChannelMetricsProvider._fetch_post_window` now drops the
summaries and asks the same window again with core fields alone, once. A refused
*core* listing still fails the sync: that is a connection somebody has to fix,
where two uncountable interaction totals are two ``NULL`` columns. See
:data:`POST_FIELD_REFUSALS` for where that line is drawn and
:data:`~meobot.integrations.meta.constants.FACEBOOK_SCOPES` for why the scope has
not simply been added.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from meobot.core.logging import get_logger
from meobot.domain.pr.channel_analytics import FACEBOOK_TOP_POST_KEY
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.domain.pr.channel_metrics import (
    BoundProviderAccount,
    ChannelMetricsReading,
    DiscoveredProviderAccount,
    FetchedChannelIdentity,
    PrChannelPlatform,
    ProviderAccountChoices,
    ProviderTokens,
    normalize_capture_time,
)
from meobot.integrations.meta.client import (
    InsightWindow,
    MetaGraphClient,
    MetaPage,
    insight_window,
    parse_count,
)
from meobot.integrations.meta.constants import (
    FACEBOOK_SCOPES,
    INSTAGRAM_SCOPES,
    MAX_POST_PAGES,
    POST_PAGE_SIZE,
)
from meobot.integrations.meta.errors import MetaApiError
from meobot.integrations.meta.posts import (
    CORE_POST_FIELD_SPEC,
    POST_FIELDS,
    FacebookPost,
    PostTotals,
    PostWindow,
    field_availability,
    parse_post,
    posts_within,
    summarize,
    top_post_payload,
)

logger = get_logger(__name__)

#: Page Insights metrics MeoBot asks for as a daily series, then sums.
#:
#: **One metric, since Graph v23 retired the others.** ``page_impressions`` and
#: ``page_impressions_unique`` were requested here until a production sync
#: failed with ``(#100) The value must be a valid insights metric`` - Meta had
#: removed both from Page Insights, and because Graph rejects a *whole* request
#: containing one unknown metric name, the perfectly valid
#: ``page_post_engagements`` in the same call went down with them.
#:
#: ``page_post_engagements`` is an **additive event count**, which is what makes
#: summing the daily series correct: an engagement on Monday and one on Tuesday
#: really are two engagements.
FACEBOOK_DAILY_METRICS: tuple[str, ...] = ("page_post_engagements",)

#: Page Insights metrics Step 1F.2.4d asks for **hopefully**, in their own
#: request.
#:
#: Separate from :data:`FACEBOOK_DAILY_METRICS` rather than appended to it, and
#: the separation is the whole point. Graph rejects an entire ``metric=`` list
#: containing one name it does not recognise, so putting an unconfirmed metric
#: in the same call as the confirmed one would put ``page_post_engagements`` -
#: the metric a production sync depends on - behind a name Meta might retire
#: next quarter. Two calls cost one extra request a day and cost nothing when a
#: name goes away.
#:
#: Both are documented Page Insights metrics rather than guesses, and both are
#: **verified at runtime rather than asserted here**: whatever this deployment's
#: Graph version answers is used, whatever it refuses is left ``None``, and
#: which was which is written into every snapshot under
#: ``meta_insight_metrics_available``. ``meobot-meta-probe`` exists to answer the
#: same question against a real Page before anybody has to read a snapshot.
#:
#: * ``page_video_views`` - plays of the Page's videos and Reels. Additive, so
#:   summing the daily series is correct;
#: * ``page_views_total`` - views of the Page's own profile. Recorded in
#:   ``extra_metrics`` and **never** written to ``views_7d``/``views_30d``: a
#:   profile view is not what "views" means on a YouTube or TikTok card sitting
#:   in the same channel list, and one column meaning two things is how a
#:   dashboard starts lying quietly.
FACEBOOK_OPTIONAL_DAILY_METRICS: tuple[str, ...] = ("page_video_views", "page_views_total")

#: Instagram insights, asked for as deduplicated period totals.
#:
#: ``views`` replaced ``impressions`` for Instagram accounts in recent Graph
#: versions. Both are requested; whichever the deployment's version answers is
#: used, and the other is simply absent - which is the API-drift handling this
#: connector is built around rather than a special case.
INSTAGRAM_TOTAL_METRICS: tuple[str, ...] = ("reach", "views", "total_interactions")

#: The two ways Graph says "not this **field list**" rather than "not you".
#:
#: Both are answers about the request rather than about the connection, and both
#: are survivable by asking for less:
#:
#: * ``INSUFFICIENT_SCOPE`` - the grant does not cover one of the fields asked
#:   for. This is the production case: a Page token that reads every core post
#:   field is refused ``reactions.summary`` and ``comments.summary``, because
#:   those are other people's activity and Meta gates them behind a permission
#:   this app does not hold;
#: * ``BAD_RESPONSE`` - Graph would not parse the list at all, which is what a
#:   field it has retired looks like. The same retry finds out whether the core
#:   fields still work.
#:
#: Everything else - ``AUTH_REQUIRED``, ``INVALID_ACCOUNT``, ``RATE_LIMITED``,
#: ``PROVIDER_UNAVAILABLE`` - is about the caller or the service and asking for
#: fewer fields cannot help, so it is never retried and never swallowed.
#:
#: The retry is what classifies the failure, and that matters: the connector
#: cannot tell from the error alone whether Graph refused the summaries or the
#: listing. Asking again without the summaries answers it. If the core listing
#: comes back, it was the summaries and the sync continues with two ``NULL``
#: counts; if it is refused too, that refusal propagates and the channel fails
#: with ``INSUFFICIENT_SCOPE`` exactly as before.
POST_FIELD_REFUSALS: frozenset[PrChannelSyncErrorCode] = frozenset(
    {
        PrChannelSyncErrorCode.INSUFFICIENT_SCOPE,
        PrChannelSyncErrorCode.BAD_RESPONSE,
    }
)


class _MetaProvider:
    """What the two Meta providers share: OAuth, and a credential that is a token.

    Not a public abstraction and not a second port - the providers implement
    :class:`~meobot.domain.pr.channel_metrics.ChannelMetricsProvider` like every
    other provider does. This is ordinary code reuse between two classes that
    genuinely do the same four things.
    """

    platform: PrChannelPlatform
    scopes: tuple[str, ...]
    provider = "meta"

    def __init__(self, client: MetaGraphClient) -> None:
        self._client = client

    async def aclose(self) -> None:
        await self._client.aclose()

    def build_authorization_url(self, *, state: str) -> str:
        return self._client.authorization_url(state=state, scopes=self.scopes)

    async def exchange_authorization_code(self, *, code: str) -> ProviderTokens:
        """Code → short-lived user token → **long-lived** user token.

        The long-lived token is returned as ``access_token`` and *not* as the
        durable credential: it is what discovery is performed with, and it is
        not what gets stored. The credential MeoBot keeps is the Page token that
        the selected Page hands over, which is why
        :class:`~meobot.application.pr_channel_connection_service.PrChannelConnectionService`
        swaps it in at selection time rather than at callback time.
        """
        short_lived = await self._client.exchange_code(code=code)
        long_lived = await self._client.exchange_for_long_lived(short_lived_token=short_lived)
        return ProviderTokens(
            access_token=long_lived,
            # Deliberately the *user* token: it is durable enough to survive the
            # selection step, and the Page token replaces it once a Page exists.
            durable_credential=long_lived,
            granted_scopes=self.scopes,
        )

    async def acquire_access(self, *, durable_credential: str) -> ProviderTokens:
        """Return the stored credential. **Meta makes no request here.**

        The Page access token is the access token. There is nothing to exchange,
        no endpoint to call, and no refresh to perform - which is precisely why
        the port method is called ``acquire_access`` rather than
        ``refresh_access``, and why migration 0029 renamed the column that holds
        it. A network call here would be an invented one.

        No durable credential is returned, which the caller correctly reads as
        "keep the one you have".
        """
        return ProviderTokens(access_token=durable_credential)

    async def revoke(self, *, durable_credential: str) -> None:
        """Meta offers no server-side revoke MeoBot can safely call here.

        ``DELETE /{user-id}/permissions`` revokes the **whole app grant** for
        that person - every Page, on every MeoBot channel they ever connected,
        and on any other integration sharing the app. Disconnecting one channel
        must not do that.

        So this is a no-op by design, and disconnect remains complete on
        MeoBot's side: the stored credential is dropped and the connection is
        marked disconnected. A person who wants the grant itself withdrawn does
        it in Facebook's own Business Integrations settings, which is the only
        place with the right granularity.
        """
        logger.info("meta_revoke_skipped", extra={"provider": self.provider})

    # --- Account selection ------------------------------------------------
    async def _owner(self, access_token: str) -> tuple[str, str | None]:
        """The Meta user who granted consent. One cheap field read.

        Its id is what a half-finished connection records in
        ``provider_account_id`` while somebody is still choosing a Page, so that
        column never has to hold a placeholder: the connection really does hold
        a credential for *that* Meta user, and nothing is bound yet.
        """
        payload = await self._client.fields("me", token=access_token, fields="id,name")
        owner_id = _str(payload.get("id"))
        if owner_id is None:
            raise MetaApiError(
                "the authorization did not resolve to a Meta account",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        return owner_id, _str(payload.get("name"))

    async def _pages(self, access_token: str) -> list[MetaPage]:
        return await self._client.list_pages(user_token=access_token)

    # --- Optional insights, which Meta retires without warning ------------
    async def _optional_series(
        self,
        node_id: str,
        *,
        token: str,
        metrics: tuple[str, ...],
        window: InsightWindow,
        period: str = "day",
    ) -> dict[str, list[int]]:
        """An insight series, degrading to "unavailable" rather than failing.

        See :meth:`_degrade` for why this exists.
        """
        return await self._degrade(
            metrics,
            lambda group: self._client.insights_series(
                node_id, token=token, metrics=group, window=window, period=period
            ),
            node_id=node_id,
        )

    async def _optional_totals(
        self,
        node_id: str,
        *,
        token: str,
        metrics: tuple[str, ...],
        window: InsightWindow,
    ) -> dict[str, int]:
        """Deduplicated period totals, degrading the same way."""
        return await self._degrade(
            metrics,
            lambda group: self._client.insights_total(
                node_id, token=token, metrics=group, window=window
            ),
            node_id=node_id,
        )

    async def _degrade[T](
        self,
        metrics: tuple[str, ...],
        fetch: Callable[[tuple[str, ...]], Awaitable[dict[str, T]]],
        *,
        node_id: str,
    ) -> dict[str, T]:
        """Ask for a group of optional metrics; keep whatever Meta will answer.

        This exists because of a production incident, and the mechanism is worth
        stating exactly.

        Graph does **not** return a partial answer when a metric name is no
        longer valid. It rejects the whole request with ``(#100) The value must
        be a valid insights metric`` - so a single retired metric took
        ``page_post_engagements`` down with it in the same comma-separated call,
        and a Facebook channel whose followers, fans and engagements were all
        readable failed its entire sync with ``BAD_RESPONSE``.

        The original code assumed the missing metric would simply be absent from
        a 200 response. That is true when Meta *permission-gates* a metric and
        false when it *retires* one, and only the second had happened.

        So: try the group; if Graph rejects it as malformed, retry the metrics
        **one at a time** and keep the ones that answer. The retry costs extra
        calls only on the day something is deprecated, and it is what stops one
        retired name from silently costing the other two.

        What is deliberately **not** degraded is everything that is not a
        malformed request. ``AUTH_REQUIRED``, ``INSUFFICIENT_SCOPE``,
        ``INVALID_ACCOUNT``, ``RATE_LIMITED`` and ``PROVIDER_UNAVAILABLE`` all
        propagate untouched - a revoked token, a missing consent, a wrong Page
        binding or a quota limit must still fail the sync loudly, because
        swallowing those would write a snapshot full of nulls and record it as a
        success.
        """
        try:
            answered_group: dict[str, T] = await fetch(metrics)
        except MetaApiError as exc:
            if exc.error_code is not PrChannelSyncErrorCode.BAD_RESPONSE:
                raise
            if len(metrics) <= 1:
                logger.warning(
                    "meta_insight_metric_unavailable",
                    extra={
                        "provider": self.provider,
                        "node_id": node_id,
                        "metrics": list(metrics),
                    },
                )
                return {}
        else:
            return answered_group

        # The group was refused. Find out which members still work rather than
        # losing all of them to one retired name.
        answered: dict[str, T] = {}
        unavailable: list[str] = []
        for metric in metrics:
            try:
                answered.update(await fetch((metric,)))
            except MetaApiError as exc:
                if exc.error_code is not PrChannelSyncErrorCode.BAD_RESPONSE:
                    raise
                unavailable.append(metric)
        if unavailable:
            logger.warning(
                "meta_insight_metrics_unavailable",
                extra={
                    "provider": self.provider,
                    "node_id": node_id,
                    "metrics": unavailable,
                },
            )
        return answered

    # --- Shared measurement helpers ---------------------------------------
    @staticmethod
    def _windows(now: datetime) -> tuple[InsightWindow, InsightWindow]:
        return insight_window(now, days=7), insight_window(now, days=30)

    @staticmethod
    def _window_metadata(seven: InsightWindow, thirty: InsightWindow) -> dict[str, Any]:
        return {
            "meta_window_7d_start": seven.start_iso,
            "meta_window_7d_end": seven.end_iso,
            "meta_window_30d_start": thirty.start_iso,
            "meta_window_30d_end": thirty.end_iso,
        }


class FacebookChannelMetricsProvider(_MetaProvider):
    """A Facebook **Page**. Not a profile, not a Group, not an ad account.

    ``followers`` comes from ``followers_count`` and never from ``fan_count``.
    They are different numbers - fans are people who liked the Page, followers
    are people who receive its posts, and the two diverged permanently when Meta
    split them. ``fan_count`` is preserved in ``extra_metrics`` because it is
    still the number some reports quote, but the canonical follower column means
    followers.
    """

    platform = PrChannelPlatform.FACEBOOK
    scopes = FACEBOOK_SCOPES

    async def discover_accounts(self, *, access_token: str) -> ProviderAccountChoices:
        """Every Facebook Page this person manages.

        Two Graph calls for the whole step, and the Page tokens that came back
        with them never leave this method - :class:`DiscoveredProviderAccount`
        has nowhere to put one, which is the point.
        """
        owner_id, owner_name = await self._owner(access_token)
        pages = eligible_pages(await self._pages(access_token))
        return ProviderAccountChoices(
            owner_account_id=owner_id,
            owner_name=owner_name,
            accounts=tuple(
                DiscoveredProviderAccount(account_id=page.id, name=page.name) for page in pages
            ),
        )

    async def bind_account(self, *, access_token: str, account_id: str) -> BoundProviderAccount:
        """Bind one Page and hand back **that Page's own** access token.

        This is where the durable credential is decided. The user token this
        method was called with is discovery scaffolding; what gets stored is the
        Page token, which does not expire and grants nothing beyond that Page.
        """
        for page in await self._pages(access_token):
            if page.id == account_id:
                return BoundProviderAccount(
                    identity=FetchedChannelIdentity(
                        account_id=page.id,
                        title=page.name,
                        profile_url=f"https://www.facebook.com/{page.id}",
                    ),
                    durable_credential=page.access_token,
                )
        raise MetaApiError(
            "that Page is not one this authorization can reach",
            error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
        )

    async def fetch_channel_identity(self, *, access_token: str) -> FetchedChannelIdentity:
        """The Page this Page token belongs to.

        ``/me`` with a Page token resolves to the Page itself, which is what
        makes this answer authoritative rather than a repetition of whatever id
        the caller believed.
        """
        payload = await self._client.fields(
            "me", token=access_token, fields="id,name,username,link"
        )
        page_id = payload.get("id")
        if not isinstance(page_id, str) or not page_id.strip():
            raise MetaApiError(
                "the Page token did not resolve to a Page",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        username = payload.get("username")
        return FetchedChannelIdentity(
            account_id=page_id.strip(),
            title=_str(payload.get("name")),
            handle=f"@{username}" if isinstance(username, str) and username else None,
            profile_url=_str(payload.get("link")),
        )

    async def fetch_channel_metrics(
        self, *, access_token: str, account_id: str, now: datetime
    ) -> ChannelMetricsReading:
        """Identity, Page fields, three insight requests and one post window.

        Step 1F.2.4c fetched four things. Step 1F.2.4d fetches, at most:

        =========================================  ==================
        Request                                    Cost
        =========================================  ==================
        ``/me`` - identity                         1
        ``/{page}?fields=followers_count,fan_count``  1
        confirmed insights, 30d                    1
        optional insights, 30d                     1 (+2 if refused)
        insights for whatever answered, 7d         0 or 1
        ``/{page}/published_posts``                1-4 (+1 if the
                                                   summaries are refused)
        =========================================  ==================

        Eight or nine on an ordinary day. Three things keep it there:

        * the 7-day insight request asks only for the metrics the 30-day one
          just answered, so a retired name costs its one-at-a-time retry **once**
          per sync rather than twice;
        * the 7-day *post* figures are filtered out of the 30-day fetch rather
          than fetched again;
        * every interaction count arrives as a summary on the post listing, so
          the post half of this method costs the same for a hundred posts as for
          one.
        """
        identity = await self.fetch_channel_identity(access_token=access_token)
        if identity.account_id != account_id:
            raise MetaApiError(
                "the authorized Page is not the one this connection is bound to",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )

        profile = await self._client.fields(
            account_id, token=access_token, fields="followers_count,fan_count"
        )
        seven, thirty = self._windows(now)

        # The confirmed metric and the hopeful ones travel in separate requests.
        # See FACEBOOK_OPTIONAL_DAILY_METRICS for why that separation is not
        # tidiness: it is what keeps a metric Meta may retire away from the one
        # production depends on.
        confirmed_30d = await self._optional_series(
            account_id, token=access_token, metrics=FACEBOOK_DAILY_METRICS, window=thirty
        )
        optional_30d = await self._optional_series(
            account_id,
            token=access_token,
            metrics=FACEBOOK_OPTIONAL_DAILY_METRICS,
            window=thirty,
        )
        # Ask the shorter window only for what the longer one just answered, and
        # keep the two groups apart there as well. A name this Graph version has
        # retired is then requested once per sync instead of twice, a Page whose
        # optional metrics are all gone makes no second weekly request at all,
        # and the confirmed metric never shares a ``metric=`` list with a
        # hopeful one at any point in the sync.
        daily_7d = await self._answered_again(
            account_id, token=access_token, answered=confirmed_30d, window=seven
        )
        daily_7d.update(
            await self._answered_again(
                account_id, token=access_token, answered=optional_30d, window=seven
            )
        )
        daily_30d = {**confirmed_30d, **optional_30d}

        window_30d = await self._optional_post_window(account_id, token=access_token, window=thirty)
        totals_30d, totals_7d = _post_totals(window_30d, seven=seven)

        metrics: dict[str, int | None] = {
            "followers": parse_count(profile.get("followers_count")),
            # Step 1F.2.4d. Page likes, in their own column at last. A different
            # number from followers ever since Meta split the two, and quoted by
            # every report the department writes.
            "fans": parse_count(profile.get("fan_count")),
            # Still empty: Graph offers a Page no lifetime post count, and
            # deriving one by paginating the whole feed is the unbounded crawl
            # this step exists to avoid. The windowed counts below are the ones
            # worth having anyway.
            "posts_count": None,
            # No account-level "views" on a Page that means what the other
            # platforms' cards mean. See the module docstring. ``page_views_total``
            # is fetched and kept in ``extra_metrics``, where a profile view can
            # be read as a profile view.
            "views_7d": None,
            "views_30d": None,
            # Graph v23 retired ``page_impressions_unique``, so there is no
            # deduplicated reach to read at any period. Empty rather than
            # approximated: summing daily uniques would count one person several
            # times, which is the mistake this connector refuses to make even
            # when it leaves a card blank.
            "reach_7d": None,
            "reach_30d": None,
            # And ``page_impressions`` with it.
            "impressions_7d": None,
            "impressions_30d": None,
            "engagements_7d": _sum(daily_7d.get("page_post_engagements")),
            "engagements_30d": _sum(daily_30d.get("page_post_engagements")),
            "following": None,
            # ``likes_30d`` stays empty on purpose even though reactions are
            # now read. A Facebook reaction is a like, a love, a haha, a wow, a
            # sad or an angry; the total is not a like count, and it goes in the
            # column that says reactions.
            "likes_30d": None,
            "comments_30d": totals_30d.comments,
            "shares_30d": totals_30d.shares,
            # --- Step 1F.2.4d ---------------------------------------------
            "posts_count_7d": totals_7d.posts_count,
            "posts_count_30d": totals_30d.posts_count,
            "reactions_30d": totals_30d.reactions,
            "video_views_7d": _sum(daily_7d.get("page_video_views")),
            "video_views_30d": _sum(daily_30d.get("page_video_views")),
        }

        extra: dict[str, Any] = {
            "meta_provider": "FACEBOOK",
            "meta_page_id": account_id,
            # Page likes. Now also in the canonical ``fans`` column; kept here
            # too because snapshots taken before 1F.2.4d have it only here, and
            # a reader comparing a March row with an August one should not find
            # the field simply gone.
            "facebook_page_fan_count": parse_count(profile.get("fan_count")),
            # Profile views. Deliberately not a canonical column - see the
            # ``views_*`` comment above.
            "facebook_page_views_7d": _sum(daily_7d.get("page_views_total")),
            "facebook_page_views_30d": _sum(daily_30d.get("page_views_total")),
            # Which insight metrics this sync actually got an answer for. Cheap
            # to record and the only way to tell "the Page had no engagement" from
            # "Meta stopped serving the metric" when reading a snapshot months
            # later - which is exactly the question this incident raised.
            "meta_insight_metrics_available": sorted(daily_30d),
            # Step 1F.2.4d. Whether the post half of this reading is a window or
            # a prefix of one, and why every count over it may be ``None``.
            "facebook_posts_read": (None if window_30d is None else len(window_30d.posts)),
            "facebook_posts_truncated": (None if window_30d is None else window_30d.truncated),
            # Which post fields this reading actually got, and - when it did not
            # get one - which of the five reasons applied. The same job
            # ``meta_insight_metrics_available`` does for Page Insights, and it
            # exists for the same question: months later, is this blank
            # ``reactions_30d`` a Page nobody reacted to or a permission this app
            # was never granted? Safe words only; no Graph payload, no token.
            "facebook_post_fields": field_availability(window_30d),
            "facebook_post_window_start": thirty.start_iso,
            "facebook_post_window_end": thirty.end_iso,
            **self._window_metadata(seven, thirty),
        }
        if totals_30d.top_post is not None:
            extra[FACEBOOK_TOP_POST_KEY] = top_post_payload(totals_30d.top_post)

        return ChannelMetricsReading(
            observed_at=normalize_capture_time(now),
            metrics=metrics,
            extra_metrics=extra,
            period_start=thirty.start_iso,
            period_end=thirty.end_iso,
        )

    async def _answered_again(
        self,
        node_id: str,
        *,
        token: str,
        answered: dict[str, list[int]],
        window: InsightWindow,
    ) -> dict[str, list[int]]:
        """Re-ask, for a shorter window, exactly the metrics that just answered.

        No request at all when nothing did, which is the whole saving: a Page
        whose Page Insights have been retired out from under it costs one
        request per group rather than two.
        """
        metrics = tuple(sorted(answered))
        if not metrics:
            return {}
        return await self._optional_series(node_id, token=token, metrics=metrics, window=window)

    # --- The post window, which must never fail a sync on its own ---------
    async def _optional_post_window(
        self, page_id: str, *, token: str, window: InsightWindow
    ) -> PostWindow | None:
        """The window's posts, or ``None`` when Graph would not list them.

        Degraded on exactly the same terms as an optional insight metric, and
        for the same reason: a Page whose followers and engagements read
        perfectly must not fail its whole sync because ``published_posts``
        answered with something unreadable. ``None`` means *not read*, the four
        post-derived columns stay ``NULL``, and the snapshot is still written.

        Everything that is **not** a malformed request propagates untouched -
        ``AUTH_REQUIRED``, ``INSUFFICIENT_SCOPE``, ``INVALID_ACCOUNT``,
        ``RATE_LIMITED``, ``PROVIDER_UNAVAILABLE``. A revoked token or a
        withdrawn consent must still fail loudly; swallowing it here would write
        a snapshot of nulls and record it as a success.

        An ``INSUFFICIENT_SCOPE`` reaching this method therefore means the
        **core** listing was refused, not the optional summaries:
        :meth:`_fetch_post_window` has already retried without them and only
        re-raises what survived that. Refusing to list a Page's own posts at all
        is a broken connection and still fails the sync; refusing to count other
        people's reactions to them is two ``NULL`` columns.
        """
        try:
            return await self._fetch_post_window(page_id, token=token, window=window)
        except MetaApiError as exc:
            if exc.error_code is not PrChannelSyncErrorCode.BAD_RESPONSE:
                raise
            logger.warning(
                "meta_post_window_unavailable",
                extra={"provider": self.provider, "node_id": page_id},
            )
            return None

    async def _fetch_post_window(
        self, page_id: str, *, token: str, window: InsightWindow
    ) -> PostWindow:
        """Walk ``published_posts`` for one window, bounded three ways.

        Bounded by the window Graph filters on, by
        :data:`~meobot.integrations.meta.constants.POST_PAGE_SIZE`, and by
        :data:`~meobot.integrations.meta.constants.MAX_POST_PAGES`. There is no
        path through this loop that makes an unbounded number of requests, which
        is the property that matters more than any of the individual numbers.

        Posts are deduplicated by id. Cursor pagination over a feed that is
        being written to can hand back a row twice, and a double-counted post
        would inflate both the post count and every interaction total derived
        from it.

        Optional fields cost one request, once
        ---------------------------------------

        The walk starts asking for :data:`~meobot.integrations.meta.posts.POST_FIELDS`
        - core fields *and* the two interaction summaries - because when the
        grant covers them a hundred posts cost the same four requests as one.
        When Graph refuses that list on :data:`POST_FIELD_REFUSALS` terms, the
        summaries are dropped and **the same cursor is asked for again** with
        :data:`~meobot.integrations.meta.posts.CORE_POST_FIELD_SPEC`.

        Three properties of that retry are the ones to check when reading it:

        * it happens **at most once** per walk - ``summaries_permitted`` is the
          guard, so the whole method is bounded by ``MAX_POST_PAGES + 1``
          requests and there is still no per-post call anywhere;
        * it does not restart the walk. Pages already read are kept, and the
          retry resumes from the cursor the refused request was going to use, so
          a refusal on page three costs one request rather than three;
        * it is **never** a per-post fallback. If the core listing is refused as
          well, that exception propagates and fails the sync.
        """
        seen: dict[str, FacebookPost] = {}
        cursor: str | None = None
        fields = POST_FIELDS
        summaries_permitted = True
        pages_read = 0
        while pages_read < MAX_POST_PAGES:
            try:
                rows, cursor = await self._client.edge_page(
                    page_id,
                    "published_posts",
                    token=token,
                    fields=fields,
                    limit=POST_PAGE_SIZE,
                    since=window.since,
                    until=window.until,
                    after=cursor,
                )
            except MetaApiError as exc:
                if not summaries_permitted or exc.error_code not in POST_FIELD_REFUSALS:
                    raise
                # Graph refused this *field list*. Drop the optional half and
                # ask again from the same cursor - once, which is what keeps the
                # cost of this at one extra request rather than a second walk.
                # If the core listing is refused too, the exception raised on the
                # next pass through here is the one that reaches the sync, and it
                # is then a real permission failure rather than this one.
                summaries_permitted = False
                fields = CORE_POST_FIELD_SPEC
                logger.warning(
                    "meta_post_summaries_unavailable",
                    extra={
                        "provider": self.provider,
                        "node_id": page_id,
                        "error_code": exc.error_code.value,
                    },
                )
                continue
            pages_read += 1
            for row in rows:
                post = parse_post(row)
                if post is not None:
                    seen.setdefault(post.post_id, post)
            if cursor is None:
                break
        # The cap was reached and Graph still had more. What MeoBot holds is the
        # newest end of the window, not the window.
        truncated = cursor is not None
        if truncated:
            logger.warning(
                "meta_post_window_truncated",
                extra={
                    "provider": self.provider,
                    "node_id": page_id,
                    "posts_read": len(seen),
                    "window_start": window.start_iso,
                    "window_end": window.end_iso,
                },
            )
        return PostWindow(
            posts=tuple(seen.values()),
            truncated=truncated,
            summaries_permitted=summaries_permitted,
        )


class InstagramChannelMetricsProvider(_MetaProvider):
    """An Instagram **professional** account - Business or Creator.

    Consumer and private accounts are not reachable through this API at all, and
    the connector says so rather than binding something that can never sync. An
    account with no linked Facebook Page is equally unreachable, because Meta's
    model routes Instagram access through the Page.
    """

    platform = PrChannelPlatform.INSTAGRAM
    scopes = INSTAGRAM_SCOPES

    async def discover_accounts(self, *, access_token: str) -> ProviderAccountChoices:
        """Every Instagram professional account reachable through a managed Page.

        Pages **without** a linked professional account are excluded rather than
        offered and then refused: a chooser should show what will work, not what
        exists. A person whose Pages have no Instagram accounts sees an empty
        list and a clear message, not a row that fails on click.

        Consumer and private Instagram accounts never appear here at all -
        Meta's API does not expose them, which is why MeoBot does not claim to
        support them.
        """
        owner_id, owner_name = await self._owner(access_token)
        pages = eligible_instagram_pages(await self._pages(access_token))
        return ProviderAccountChoices(
            owner_account_id=owner_id,
            owner_name=owner_name,
            accounts=tuple(
                DiscoveredProviderAccount(
                    account_id=str(page.instagram_account_id),
                    name=page.instagram_username or str(page.instagram_account_id),
                    handle=f"@{page.instagram_username}" if page.instagram_username else None,
                    via=page.name,
                )
                for page in pages
            ),
        )

    async def bind_account(self, *, access_token: str, account_id: str) -> BoundProviderAccount:
        """Bind one Instagram account, credentialed by its Page's token.

        The stored credential is the **Page** token, because that is what Meta
        authorizes Instagram reads with. It does not make this a Facebook
        channel: the account being measured is the Instagram one, and the Page
        is how Meta's graph routes to it.
        """
        for page in eligible_instagram_pages(await self._pages(access_token)):
            if page.instagram_account_id == account_id:
                username = page.instagram_username
                return BoundProviderAccount(
                    identity=FetchedChannelIdentity(
                        account_id=account_id,
                        title=username or account_id,
                        handle=f"@{username}" if username else None,
                        profile_url=(
                            f"https://www.instagram.com/{username}/" if username else None
                        ),
                    ),
                    durable_credential=page.access_token,
                )
        raise MetaApiError(
            "Tài khoản Instagram này chưa hỗ trợ kết nối API.",
            error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
        )

    async def fetch_channel_identity(self, *, access_token: str) -> FetchedChannelIdentity:
        """Not resolvable from a token alone - see :meth:`fetch_channel_metrics`.

        A Page token identifies the *Page*, not the Instagram account hanging
        off it, so ``/me`` would answer the wrong question. The bound account id
        is what identifies this connection, and the sync path reads its identity
        directly.
        """
        raise MetaApiError(
            "an Instagram account cannot be identified from a Page token alone",
            error_code=PrChannelSyncErrorCode.BAD_RESPONSE,
        )

    async def identity_for(self, *, access_token: str, account_id: str) -> FetchedChannelIdentity:
        """The Instagram account this connection is bound to."""
        payload = await self._client.fields(
            account_id, token=access_token, fields="id,username,name"
        )
        resolved = payload.get("id")
        if not isinstance(resolved, str) or resolved.strip() != account_id:
            raise MetaApiError(
                "the Instagram account did not resolve to the bound id",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        username = _str(payload.get("username"))
        return FetchedChannelIdentity(
            account_id=account_id,
            title=_str(payload.get("name")) or username,
            handle=f"@{username}" if username else None,
            profile_url=f"https://www.instagram.com/{username}/" if username else None,
        )

    async def fetch_channel_metrics(
        self, *, access_token: str, account_id: str, now: datetime
    ) -> ChannelMetricsReading:
        """Account fields plus two total-value insight requests. Three calls."""
        profile = await self._client.fields(
            account_id,
            token=access_token,
            fields="id,username,followers_count,media_count",
        )
        resolved = profile.get("id")
        if not isinstance(resolved, str) or resolved.strip() != account_id:
            raise MetaApiError(
                "the Instagram account did not resolve to the bound id",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )

        seven, thirty = self._windows(now)
        totals_7d = await self._optional_totals(
            account_id, token=access_token, metrics=INSTAGRAM_TOTAL_METRICS, window=seven
        )
        totals_30d = await self._optional_totals(
            account_id, token=access_token, metrics=INSTAGRAM_TOTAL_METRICS, window=thirty
        )

        metrics: dict[str, int | None] = {
            "followers": parse_count(profile.get("followers_count")),
            # Cheap: Meta puts it on the account object, so no pagination.
            "posts_count": parse_count(profile.get("media_count")),
            "views_7d": totals_7d.get("views"),
            "views_30d": totals_30d.get("views"),
            # Deduplicated by Meta over the exact window. Exact, unlike Facebook.
            "reach_7d": totals_7d.get("reach"),
            "reach_30d": totals_30d.get("reach"),
            # ``impressions`` was retired for Instagram accounts; ``views`` is
            # its replacement and is mapped above. Nothing compatible remains.
            "impressions_7d": None,
            "impressions_30d": None,
            # Meta's own defined engagement metric for an account over a period:
            # likes, comments, saves and shares on the account's content. A
            # provider definition, not one MeoBot invented by summing.
            "engagements_7d": totals_7d.get("total_interactions"),
            "engagements_30d": totals_30d.get("total_interactions"),
            "following": None,
            "likes_30d": None,
            "comments_30d": None,
            "shares_30d": None,
        }

        extra: dict[str, Any] = {
            "meta_provider": "INSTAGRAM",
            "instagram_account_id": account_id,
            "instagram_username": _str(profile.get("username")),
            "meta_insight_metrics_available": sorted(totals_30d),
            **self._window_metadata(seven, thirty),
        }

        return ChannelMetricsReading(
            observed_at=normalize_capture_time(now),
            metrics=metrics,
            extra_metrics=extra,
            period_start=thirty.start_iso,
            period_end=thirty.end_iso,
        )


def eligible_pages(pages: list[MetaPage]) -> list[MetaPage]:
    """Every discovered Page. All of them can be a Facebook channel."""
    return list(pages)


def eligible_instagram_pages(pages: list[MetaPage]) -> list[MetaPage]:
    """Only the Pages with a linked Instagram professional account.

    A Page without one is not an Instagram option and is excluded rather than
    offered and then refused - which is the difference between a chooser that
    shows what will work and one that shows what exists.
    """
    return [page for page in pages if page.instagram_account_id]


def _post_totals(
    window: PostWindow | None, *, seven: InsightWindow
) -> tuple[PostTotals, PostTotals]:
    """The month's totals and the week's, from one fetch.

    The weekly figures are a filter over the monthly fetch rather than a second
    request - see :func:`~meobot.integrations.meta.posts.posts_within`, which
    also decides whether a truncated month still covers the whole week.

    A window that was not read at all yields two empty
    :class:`~meobot.integrations.meta.posts.PostTotals`, every field of which is
    ``None``. Not zero: nobody counted.
    """
    if window is None:
        return PostTotals(), PostTotals()
    weekly = posts_within(window, start=seven.start_utc, end=seven.end_utc_exclusive)
    return summarize(window), summarize(weekly)


def _sum(values: list[int] | None) -> int | None:
    """Total an additive daily series, or ``None`` when Meta returned nothing.

    ``None`` rather than ``0`` for an absent series: a metric the API declined
    to answer is not a month in which nothing happened.
    """
    if not values:
        return None
    return sum(values)


def _last(values: list[int] | None) -> int | None:
    """The final point of a trailing-window series.

    For ``period=week``/``days_28`` each point is *already* the deduplicated
    figure for the window ending on that day, so the last point is the answer
    and summing them would be nonsense.
    """
    if not values:
        return None
    return values[-1]


def _str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__: list[str] = [
    "FACEBOOK_DAILY_METRICS",
    "FACEBOOK_OPTIONAL_DAILY_METRICS",
    "FACEBOOK_TOP_POST_KEY",
    "INSTAGRAM_TOTAL_METRICS",
    "POST_FIELD_REFUSALS",
    "FacebookChannelMetricsProvider",
    "InstagramChannelMetricsProvider",
    "eligible_instagram_pages",
    "eligible_pages",
]
