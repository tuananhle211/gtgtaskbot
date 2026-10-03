"""The TikTok channel connector: consent, identity, and the counts TikTok serves.

Step 1F.2.6. One provider over
:class:`~meobot.integrations.tiktok.client.TikTokApiClient`, implementing the
same :class:`~meobot.domain.pr.channel_metrics.ChannelMetricsProvider` port
YouTube and the two Meta connectors implement. Nothing in the application layer
changed to accommodate it, which was the promise the port made.

The token lifecycle, which is Google's rather than Meta's
----------------------------------------------------------

``code`` → access token (~24h) + **refresh token** (~365d). The refresh token is
the durable credential MeoBot stores, and :meth:`acquire_access` really does
make a network call - unlike Meta's, which hands back what it was given.

Two things about it are TikTok's own and are the parts worth checking:

* **the refresh token rotates.** Every refresh returns a *new* one and
  invalidates the old. :meth:`acquire_access` therefore returns it as
  ``durable_credential``, and
  :meth:`~meobot.application.pr_channel_connection_service.PrChannelConnectionService.access_token_for`
  writes it back through the branch that already existed for exactly this. A
  connector that dropped it would work perfectly for one day and then fail
  ``AUTH_REQUIRED`` forever;
* **rotation does not extend its life.** TikTok anchors the refresh token's
  expiry to the *first* authorization, so a connection has a hard ceiling - a
  year by default - after which somebody must consent again however healthy the
  syncs have been. There is no column for that date and this step adds none: the
  ceiling arrives as ``invalid_grant``, which classifies as ``AUTH_REQUIRED``,
  which moves the connection to ``ACTION_REQUIRED``, whose panel already says
  "kết nối lại". The right thing already happens; what was missing was anybody
  knowing why, which is what this paragraph is for.

Why there is no account chooser
--------------------------------

TikTok consent authorizes **one** account - the one whose session completed the
dialog - so this provider deliberately does **not** implement
:class:`~meobot.domain.pr.channel_metrics.AccountSelectingProvider`. There is
nothing to choose, and offering a list of one would be ceremony. A person who
connected the wrong TikTok account fixes it by logging out of TikTok and
pressing "Kết nối lại", and the rebind is announced by the existing flow because
``provider_account_id`` changed.

What this step deliberately does not measure
---------------------------------------------

**Nothing windowed.** No ``views_7d``, no ``posts_count_30d``, no engagement
totals, no top video, no video walk at all. Not because it would be hard, but
because the Display API has no reporting window of any kind - it serves lifetime
counters and nothing else - so every windowed figure would have to be derived by
MeoBot from a video crawl, and whether the per-video counters that crawl depends
on are even served is the open question. ``meobot-tiktok-probe`` answers it
against a real account. Deriving a month's views from counters that turn out to
be absent, or lifetime-to-date rather than in-window, would put a wrong number
on a management dashboard, which is worse than an empty card.

So :meth:`fetch_channel_metrics` writes exactly what ``/v2/user/info/`` returns:
three canonical stock counts and one provider-specific total. That is a small
reading and an honest one, and it is enough to make a TikTok connection a real
connection - the sweeper syncs it, the history accumulates, and the follower
growth the analytics domain derives from stored snapshots starts working on its
own, because that derivation was never TikTok-specific.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from meobot.core.logging import get_logger
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.domain.pr.channel_metrics import (
    ChannelMetricsReading,
    FetchedChannelIdentity,
    PrChannelPlatform,
    ProviderTokens,
    normalize_capture_time,
)
from meobot.integrations.tiktok.client import (
    TikTokApiClient,
    TikTokTokens,
    epoch_to_utc,
    parse_count,
)
from meobot.integrations.tiktok.constants import (
    RECENT_VIDEO_PAGE,
    TIKTOK_SCOPES,
    USER_FIELDS_BASIC,
    USER_FIELDS_PROFILE,
    USER_FIELDS_STATS,
    USER_FIELDS_USERNAME,
    VIDEO_FIELDS_CORE,
    VIDEO_FIELDS_COUNTERS,
)
from meobot.integrations.tiktok.errors import TikTokApiError

logger = get_logger(__name__)

#: ``extra_metrics`` keys this connector writes.
#:
#: Named here rather than as literals at the write site for the reason
#: :data:`~meobot.domain.pr.channel_analytics.FACEBOOK_TOP_POST_KEY` gives:
#: ``extra_metrics`` is a JSON column with no schema, and the only thing keeping
#: a writer and a reader in step is that they name the same constant.
TIKTOK_PROVIDER_KEY = "tiktok_provider"
TIKTOK_TOTAL_LIKES_KEY = "tiktok_total_likes"
TIKTOK_VIDEO_COUNT_KEY = "tiktok_video_count"
TIKTOK_API_PRODUCT_KEY = "tiktok_api_product"
TIKTOK_FIELDS_KEY = "tiktok_user_fields"

#: Which TikTok product wrote a reading, recorded in every snapshot.
#:
#: A string rather than a boolean because the question it answers is "could this
#: reading have carried profile views?", and the honest answer depends on which
#: product was used. A future Business API reading would say so here, and a
#: reader comparing two snapshots months apart would be able to tell them apart
#: instead of wondering why one has fields the other lacks.
TIKTOK_API_PRODUCT = "DISPLAY_API"

#: The failures that mean "not this **field list**" rather than "not you".
#:
#: The same distinction :data:`~meobot.integrations.meta.provider.POST_FIELD_REFUSALS`
#: draws, and it exists here for the same reason: TikTok refuses a *whole*
#: ``fields=`` list over one name it will not serve, so a group that fails may
#: contain three perfectly readable fields.
#:
#: * ``BAD_RESPONSE`` - ``invalid_params``. TikTok would not parse the field
#:   list, which is what a field this API version does not have looks like;
#: * ``INSUFFICIENT_SCOPE`` - ``scope_not_authorized`` or
#:   ``scope_permission_missed``. The grant, or the app's approval, does not
#:   cover the group.
#:
#: Everything else - ``AUTH_REQUIRED``, ``INVALID_ACCOUNT``, ``RATE_LIMITED``,
#: ``PROVIDER_UNAVAILABLE`` - is about the caller or the service, asking for
#: fewer fields cannot help, and it is never swallowed.
FIELD_REFUSALS: frozenset[PrChannelSyncErrorCode] = frozenset(
    {
        PrChannelSyncErrorCode.BAD_RESPONSE,
        PrChannelSyncErrorCode.INSUFFICIENT_SCOPE,
    }
)

#: The field groups a sync asks for, in the order they matter.
#:
#: Grouped by the scope that grants them, because TikTok refuses the whole
#: request when one field's scope is missing - so a flat list would mean an app
#: not yet approved for ``user.info.stats`` gets **no** identity either, and a
#: connection that could have shown a display name shows nothing.
#:
#: ``required`` marks the group whose failure is a real failure. Only
#: ``user.info.basic`` is required: without ``open_id`` there is no account
#: identity and no connection worth having. Everything else degrades to
#: ``None`` and is recorded as unavailable.
USER_FIELD_GROUPS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("basic", USER_FIELDS_BASIC, True),
    ("profile", USER_FIELDS_PROFILE, False),
    ("username", USER_FIELDS_USERNAME, False),
    ("stats", USER_FIELDS_STATS, False),
)


#: The words this connector uses for "why is this field not on the screen".
#:
#: The same five the probe reports and the same vocabulary
#: :mod:`meobot.integrations.meta.posts` uses, so a support conversation about a
#: TikTok card and one about a Facebook card do not need two glossaries:
#:
#: * ``available`` - asked for, answered, and there is something in it;
#: * ``empty`` - asked for and answered with nothing. A real field on a quiet
#:   account, and time fixes it;
#: * ``not_returned`` - accepted and then silently omitted. TikTok's own
#:   speciality, and the reason the probe needed a fifth verdict;
#: * ``not_permitted`` - the grant or the app's approval does not cover it.
#:   Only a person reconnecting, or an app review, fixes it;
#: * ``unsupported`` - not a field on this endpoint at this API version.
FIELD_AVAILABLE = "available"
FIELD_EMPTY = "empty"
FIELD_NOT_RETURNED = "not_returned"


@dataclass(frozen=True, slots=True)
class TikTokAccountOverview:
    """The authorized account as ``/v2/user/info/`` describes it.

    Deliberately **not** a
    :class:`~meobot.domain.pr.channel_metrics.ChannelMetricsReading`: this is
    what a person looks at, not what a sync writes down. A reading is stored,
    fingerprinted and compared against its predecessors; this is fetched, shown
    and thrown away, and giving the two one type would mean either storing a bio
    or putting a snapshot's period bounds on a profile card.

    **Carries no credential**, and structurally cannot: every field is copied by
    name from the ``data.user`` object, and the access token that fetched it is
    a local in the method above.

    ``likes_count`` is the **lifetime** total of likes across every video the
    account has posted - not a month's worth, not a week's. See
    :data:`TIKTOK_TOTAL_LIKES_KEY`, which says the same thing at the write site,
    and :class:`~meobot.api.schemas.pr.TikTokStatsResponse`, which says it on
    the screen.
    """

    open_id: str
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    profile_deep_link: str | None = None
    bio_description: str | None = None
    #: ``None`` rather than ``False`` when ``user.info.profile`` was refused.
    #: An unverified account and an unreadable one are different statements and
    #: the screen says so.
    is_verified: bool | None = None
    follower_count: int | None = None
    following_count: int | None = None
    likes_count: int | None = None
    video_count: int | None = None
    #: One word per field group - see :data:`FIELD_AVAILABLE` and friends. What
    #: lets a panel say *"chưa được cấp quyền"* over a blank stats block rather
    #: than printing four zeroes an account never had.
    availability: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TikTokVideoSummary:
    """One row of ``/v2/video/list/``, as much of it as TikTok served.

    Every counter is ``int | None`` and every text field is ``str | None``, and
    the absences are load-bearing: a video with no likes and a video whose
    ``like_count`` TikTok would not serve must not both render "0".

    ``cover_image_url`` is a **short-lived** TikTok CDN link - TikTok documents
    it as expiring within hours - so it is never stored, only passed through to
    the browser that is looking at the panel right now.
    """

    video_id: str
    title: str | None = None
    description: str | None = None
    created_at: datetime | None = None
    duration_seconds: int | None = None
    cover_image_url: str | None = None
    share_url: str | None = None
    embed_link: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None


@dataclass(frozen=True, slots=True)
class TikTokRecentVideos:
    """One bounded page of the account's own videos, and how it went.

    ``cursor`` is TikTok's own value, passed back verbatim and never
    interpreted - see :class:`~meobot.integrations.tiktok.client.VideoPage`.
    ``has_more`` is TikTok's flag and the **only** thing that decides whether
    another page exists; a caller that paged on the cursor alone would ask for
    one empty page every time.
    """

    videos: tuple[TikTokVideoSummary, ...] = ()
    cursor: int | None = None
    has_more: bool = False
    #: What happened to ``video/list`` itself.
    availability: str = FIELD_AVAILABLE
    #: What happened to the four per-video counters, which travel on the same
    #: scope but are the part TikTok is least reliable about serving. Separate
    #: because "there are videos and no numbers on them" is a sentence a panel
    #: has to be able to say.
    counters: str = FIELD_AVAILABLE


class TikTokChannelMetricsProvider:
    """A TikTok account, read through Login Kit and the Display API.

    Args:
        client_key: TikTok app client key. Deployment configuration.
        client_secret: TikTok app client secret. Never leaves this object.
        redirect_uri: Must match a Redirect URI registered on the TikTok app.
        client: Injected transport. Tests pass one built over a
            ``MockTransport``, which is how this connector is exercised without
            a network.
    """

    platform = PrChannelPlatform.TIKTOK
    provider = "tiktok"
    scopes = TIKTOK_SCOPES

    def __init__(
        self,
        *,
        client_key: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        client: TikTokApiClient | None = None,
    ) -> None:
        self._client = client or TikTokApiClient(
            client_key=client_key, client_secret=client_secret, redirect_uri=redirect_uri
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Authorization ----------------------------------------------------
    def build_authorization_url(self, *, state: str) -> str:
        return self._client.authorization_url(state=state, scopes=self.scopes)

    async def exchange_authorization_code(self, *, code: str) -> ProviderTokens:
        """Code → access token + refresh token.

        The refresh token is returned as the durable credential, which is what
        the connection stores encrypted. The access token is returned beside it
        for the identity call the caller makes next and is never written down.

        ``granted_scopes`` is whatever **TikTok** granted, read off the token
        response rather than copied from :data:`TIKTOK_SCOPES`. Those two
        disagree whenever a person declines a scope at the consent screen or the
        app has not been approved for one, and recording the request instead of
        the grant would make the connection panel claim a permission the token
        does not carry.
        """
        return self._as_provider_tokens(await self._client.exchange_code(code=code))

    async def acquire_access(self, *, durable_credential: str) -> ProviderTokens:
        """Exchange the stored refresh token for a fresh access token.

        Returns the **rotated** refresh token as the durable credential, always,
        because TikTok always issues a new one here. See the module docstring:
        this is the branch that must not be dropped.
        """
        tokens = await self._client.refresh(refresh_token=durable_credential)
        return self._as_provider_tokens(tokens)

    async def revoke(self, *, durable_credential: str) -> None:
        """Ask TikTok to forget this grant. Best effort, and genuinely scoped.

        Unlike Meta - where the only available revoke drops the whole app grant
        for that person, across every channel they ever connected - TikTok's
        revoke takes one token and drops the authorization behind it. So this
        one really is called, and a failure is logged rather than raised:
        disconnect must stay complete on MeoBot's side whatever TikTok answers.
        """
        if not await self._client.revoke_token(token=durable_credential):
            logger.info("tiktok_revoke_incomplete", extra={"provider": self.provider})

    # --- Measurement ------------------------------------------------------
    async def fetch_channel_identity(self, *, access_token: str) -> FetchedChannelIdentity:
        """Who this token actually belongs to.

        There is no id parameter on ``/v2/user/info/`` - the endpoint answers
        for the authorizing account and nothing else - which makes this answer
        authoritative by construction rather than by convention. That is the
        wrong-account check: the difference between what somebody clicked and
        what they consented to is only visible if this call is the one that
        decides.

        Degrades over the optional field groups, so an app approved for
        ``user.info.basic`` alone still produces a usable identity rather than a
        refusal. Only the basic group failing is fatal.
        """
        fields, _ = await self._user_fields(access_token=access_token)
        return self._identity_from(fields)

    async def fetch_channel_metrics(
        self, *, access_token: str, account_id: str, now: datetime
    ) -> ChannelMetricsReading:
        """One reading: the counts ``/v2/user/info/`` serves, and nothing else.

        **One request** on an ordinary day - every field the Display API offers
        an account comes off one node - or up to four when a group is refused
        and the rest are re-asked individually.

        No window, no ``period_start``, no ``period_end``. Those name the
        *effective reporting window* a reading covers, and a lifetime follower
        count covers no window at all; inventing a 30-day span for it would make
        the idempotency fingerprint claim something the numbers do not. The
        consequence is deliberate and worth stating: two syncs of an account
        whose counts have not moved produce the same fingerprint and the second
        is recorded as a duplicate rather than appended, which is correct - a
        reading that says the same thing about the same account is the same
        reading.

        Raises:
            TikTokApiError: The token, the account or the service failed. A
                refused *optional* field group does not raise; it leaves the
                affected counts ``None`` and is recorded in ``extra_metrics``.
        """
        fields, availability = await self._user_fields(access_token=access_token)
        identity = self._identity_from(fields)
        if identity.account_id != account_id:
            raise TikTokApiError(
                "the authorized TikTok account is not the one this connection is bound to",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )

        metrics: dict[str, int | None] = {
            "followers": parse_count(fields.get("follower_count")),
            "following": parse_count(fields.get("following_count")),
            # Lifetime videos published, as TikTok counts them. The canonical
            # stock column, and the same thing ``posts_count`` means on every
            # other platform - a video is what a TikTok account posts.
            "posts_count": parse_count(fields.get("video_count")),
        }

        extra: dict[str, Any] = {
            TIKTOK_PROVIDER_KEY: "TIKTOK",
            TIKTOK_API_PRODUCT_KEY: TIKTOK_API_PRODUCT,
            # Lifetime likes across every video the account has posted. **Not**
            # ``likes_30d``, which is a windowed count of likes received during
            # a month: filing a lifetime cumulative total in a windowed column
            # is how a dashboard starts lying quietly, and the number would be
            # thousands of times too large. It has no canonical column because
            # no other platform reports one, so it lives here under its own name.
            TIKTOK_TOTAL_LIKES_KEY: parse_count(fields.get("likes_count")),
            # Also in ``posts_count`` above. Kept here too so a reader can tell
            # a TikTok reading's lifetime video count from a Facebook reading's
            # lifetime post count without inferring it from the provider.
            TIKTOK_VIDEO_COUNT_KEY: parse_count(fields.get("video_count")),
            # Which field groups this sync actually got an answer for, and - when
            # it did not get one - which of the reasons applied. Cheap to record
            # and the only way to tell "this account has no followers" from
            # "the app was never approved for user.info.stats" when reading a
            # snapshot months later. Safe words only; no payload, no token.
            TIKTOK_FIELDS_KEY: availability,
        }

        return ChannelMetricsReading(
            observed_at=normalize_capture_time(now),
            metrics=metrics,
            extra_metrics=extra,
            # Deliberately absent. See the docstring: these name a reporting
            # window, and this reading covers none.
            period_start=None,
            period_end=None,
        )

    # --- Reading, for a person rather than for a time series ---------------
    async def fetch_account_overview(self, *, access_token: str) -> TikTokAccountOverview:
        """Everything ``/v2/user/info/`` will say about the authorized account.

        The **same** request :meth:`fetch_channel_metrics` makes, through the
        **same** :meth:`_user_fields` degradation, and that is the point: there
        is one place in this connector that knows how TikTok refuses a field
        list, and a panel that read the account a second way would eventually
        disagree with the sync about what the account looks like.

        What differs is only what is kept. A sync keeps the four numbers that
        have columns; this keeps the identity and the profile beside them,
        because a person confirming that they authorized the account they meant
        needs a face and a handle rather than a follower count.

        Nothing is derived, nothing is windowed and nothing is filled in. A
        field group TikTok refused leaves its fields ``None`` and says so in
        :attr:`~TikTokAccountOverview.availability`.

        Raises:
            TikTokApiError: The token, the account or the service failed, or the
                **required** ``user.info.basic`` group was refused. A refused
                *optional* group does not raise.
        """
        fields, availability = await self._user_fields(access_token=access_token)
        open_id = _text(fields.get("open_id"))
        if open_id is None:
            raise TikTokApiError(
                "the TikTok token did not resolve to an account",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        verified = fields.get("is_verified")
        return TikTokAccountOverview(
            open_id=open_id,
            display_name=_text(fields.get("display_name")),
            username=_text(fields.get("username")),
            avatar_url=_text(fields.get("avatar_url")),
            profile_deep_link=_text(fields.get("profile_deep_link")),
            bio_description=_text(fields.get("bio_description")),
            # Only a real boolean counts. TikTok omits the field entirely when
            # ``user.info.profile`` was not granted, and reading an absence as
            # ``False`` would print "chưa xác minh" over an account nobody asked
            # about.
            is_verified=verified if isinstance(verified, bool) else None,
            follower_count=parse_count(fields.get("follower_count")),
            following_count=parse_count(fields.get("following_count")),
            likes_count=parse_count(fields.get("likes_count")),
            video_count=parse_count(fields.get("video_count")),
            availability=availability,
        )

    async def fetch_recent_videos(
        self,
        *,
        access_token: str,
        limit: int = RECENT_VIDEO_PAGE,
        cursor: int | None = None,
    ) -> TikTokRecentVideos:
        """One page of the account's own public videos, newest first.

        **One request** on an ordinary day, or two when TikTok refuses the field
        list - the same shape :meth:`_user_fields` has, for the same reason:
        ``video/list`` refuses a whole ``fields=`` list over one name, so an app
        whose approval does not reach the per-video counters would otherwise get
        no cover images and no share links either.

        So the counters are asked for *with* the core fields, and only if that
        is refused are the core fields asked for alone. A page that comes back
        the second way carries videos and no numbers, which is a true thing the
        panel can say - and is a far better answer than an empty section.

        The bound is the connector's, not this method's: ``limit`` is capped at
        :data:`~meobot.integrations.tiktok.constants.VIDEO_PAGE_SIZE` inside
        :meth:`~meobot.integrations.tiktok.client.TikTokApiClient.video_page`,
        and how many times a caller may follow the cursor is
        :data:`~meobot.integrations.tiktok.constants.MAX_VIDEO_PAGES`. There is
        no path here that walks an account's whole history.

        Raises:
            TikTokApiError: The token, the account or the service failed. A
                refused **field list** does not raise - it comes back as an
                availability word, because "video.list is not approved yet" is
                something a reviewer needs to read rather than a stack trace.
        """
        counters = FIELD_AVAILABLE
        try:
            page = await self._client.video_page(
                access_token=access_token,
                fields=VIDEO_FIELDS_CORE + VIDEO_FIELDS_COUNTERS,
                max_count=limit,
                cursor=cursor,
                operation_hint="account_videos",
            )
        except TikTokApiError as exc:
            # TikTok files "this account has never posted" under an *account*
            # error - ``user_has_no_video``, which
            # :data:`~meobot.integrations.tiktok.errors.ACCOUNT_CODES` maps to
            # ``INVALID_ACCOUNT`` alongside a deleted account and a banned one.
            # Letting that escape would turn a brand-new creator's first visit
            # into a red error box under a perfectly healthy connection.
            #
            # It is read as "no videos" rather than "no account" because of the
            # one thing this method's caller guarantees: the account has already
            # been resolved by ``/v2/user/info/`` on this same token, moments
            # ago. A token that just answered for an account is not a token
            # whose account cannot be found. The cost of being wrong is a
            # suspended account showing an empty video section instead of an
            # error - and the connection panel above it still reports the sync
            # health either way.
            if exc.error_code is PrChannelSyncErrorCode.INVALID_ACCOUNT:
                return TikTokRecentVideos(availability=FIELD_EMPTY, counters=counters)
            if exc.error_code not in FIELD_REFUSALS:
                raise
            # Either the counters are not served, or ``video.list`` itself is
            # not granted. Asking for the core alone is what tells those apart,
            # and the difference decides whether the panel shows six cards
            # without numbers or one sentence explaining the missing scope.
            counters = _REFUSAL_WORDS[exc.error_code]
            logger.info(
                "tiktok_video_fields_regrouped",
                extra={"provider": self.provider, "error_code": exc.error_code.value},
            )
            try:
                page = await self._client.video_page(
                    access_token=access_token,
                    fields=VIDEO_FIELDS_CORE,
                    max_count=limit,
                    cursor=cursor,
                    operation_hint="account_videos_core",
                )
            except TikTokApiError as inner:
                if inner.error_code is PrChannelSyncErrorCode.INVALID_ACCOUNT:
                    return TikTokRecentVideos(availability=FIELD_EMPTY, counters=counters)
                if inner.error_code not in FIELD_REFUSALS:
                    raise
                logger.warning(
                    "tiktok_video_list_unavailable",
                    extra={"provider": self.provider, "error_code": inner.error_code.value},
                )
                return TikTokRecentVideos(
                    availability=_REFUSAL_WORDS[inner.error_code], counters=counters
                )

        # A row with no ``id`` is dropped rather than rendered: it cannot be
        # keyed, linked or opened, and a card for it would be a blank the panel
        # could not explain.
        videos = tuple(
            video for video in (_video_from(row) for row in page.videos) if video.video_id
        )
        return TikTokRecentVideos(
            videos=videos,
            cursor=page.cursor,
            has_more=page.has_more,
            availability=FIELD_AVAILABLE if videos else FIELD_EMPTY,
            counters=(
                counters
                if counters != FIELD_AVAILABLE
                else _counters_word(page.videos, videos=videos)
            ),
        )

    # --- Field groups, which TikTok refuses whole --------------------------
    async def _user_fields(self, *, access_token: str) -> tuple[dict[str, Any], dict[str, str]]:
        """Every user field this grant will answer for, group by group.

        One request when everything is permitted, which is the ordinary case and
        the one worth optimising: all four groups travel in a single ``fields=``
        list. When TikTok refuses that list - one unapproved scope is enough -
        the groups are re-asked **individually** and whatever answers is kept.

        This is the same degradation
        :meth:`~meobot.integrations.meta.provider._MetaProvider._degrade`
        performs against Graph, and it exists for the same reason: TikTok does
        not answer partially. A field whose scope is missing does not come back
        absent from a 200 - it takes the whole request down with it, including
        the three fields beside it that were perfectly readable.

        Returns:
            The merged field values, and one word per group saying what
            happened to it - ``available``, ``empty``, ``not_permitted``,
            ``unsupported`` or ``not_read``.

        Raises:
            TikTokApiError: The **required** group was refused, or the failure
                was about the caller rather than the field list. A revoked
                token, a wrong binding or a rate limit must still fail loudly;
                swallowing one here would write a snapshot of nulls and record
                it as a success.
        """
        every_field = tuple(name for _, names, _ in USER_FIELD_GROUPS for name in names)
        try:
            answered = await self._client.user_info(access_token=access_token, fields=every_field)
            merged = dict(answered)
        except TikTokApiError as exc:
            if exc.error_code not in FIELD_REFUSALS:
                raise
            logger.info(
                "tiktok_user_fields_regrouped",
                extra={"provider": self.provider, "error_code": exc.error_code.value},
            )
        else:
            return merged, {name: _word_for(merged, names) for name, names, _ in USER_FIELD_GROUPS}

        # The combined list was refused. Find out which groups still work rather
        # than losing all of them to one unapproved scope.
        values: dict[str, Any] = {}
        availability: dict[str, str] = {}
        for name, names, required in USER_FIELD_GROUPS:
            try:
                answered = await self._client.user_info(access_token=access_token, fields=names)
            except TikTokApiError as exc:
                if required or exc.error_code not in FIELD_REFUSALS:
                    raise
                availability[name] = _REFUSAL_WORDS[exc.error_code]
                logger.warning(
                    "tiktok_user_field_group_unavailable",
                    extra={
                        "provider": self.provider,
                        "group": name,
                        "error_code": exc.error_code.value,
                    },
                )
                continue
            values.update(answered)
            availability[name] = _word_for(answered, names)
        return values, availability

    def _identity_from(self, fields: dict[str, Any]) -> FetchedChannelIdentity:
        """The account identity, bound to ``open_id`` and nothing softer.

        ``open_id`` is TikTok's stable per-app identifier for an account and is
        what ``provider_account_id`` holds. Deliberately **not** ``username``,
        which a person can change at will, and not ``display_name``, which is
        not even unique - two clinics with the same name would bind to each
        other's numbers.

        ``union_id`` is read but not used as identity: it is stable across the
        apps of one TikTok developer account, which is a different guarantee and
        a wider identifier than this connection needs.
        """
        open_id = _text(fields.get("open_id"))
        if open_id is None:
            raise TikTokApiError(
                "the TikTok token did not resolve to an account",
                error_code=PrChannelSyncErrorCode.INVALID_ACCOUNT,
            )
        username = _text(fields.get("username"))
        return FetchedChannelIdentity(
            account_id=open_id,
            title=_text(fields.get("display_name")),
            handle=f"@{username}" if username else None,
            # TikTok's own canonical link to the profile. Preferred over one
            # composed from a username, which would be wrong the moment somebody
            # renamed themselves - and would be a URL MeoBot invented rather
            # than one the platform confirmed.
            profile_url=_text(fields.get("profile_deep_link")),
        )

    @staticmethod
    def _as_provider_tokens(tokens: TikTokTokens) -> ProviderTokens:
        """Flatten TikTok's two-expiry answer into the port's shape.

        The refresh token becomes the durable credential. ``refresh_expires_at``
        has nowhere to go and is dropped here rather than smuggled into
        ``expires_at``, which means the access token's expiry and would be read
        as one by everything downstream.
        """
        return ProviderTokens(
            access_token=tokens.access_token,
            expires_at=tokens.expires_at,
            durable_credential=tokens.refresh_token,
            granted_scopes=tokens.granted_scopes,
        )


#: The connector's own word for each way a field group can be missing.
#:
#: Deliberately the same vocabulary
#: :mod:`meobot.integrations.meta.posts` uses for post fields, so that a support
#: conversation about a TikTok card and one about a Facebook card do not need
#: two glossaries.
_REFUSAL_WORDS: dict[PrChannelSyncErrorCode, str] = {
    PrChannelSyncErrorCode.INSUFFICIENT_SCOPE: "not_permitted",
    PrChannelSyncErrorCode.BAD_RESPONSE: "unsupported",
}


def _word_for(payload: Mapping[str, Any], names: tuple[str, ...]) -> str:
    """Whether a group that *answered* actually carried anything.

    ``available`` when at least one of its fields is present, ``empty`` when
    TikTok accepted the request and returned none of them. The distinction
    matters for the same reason it does in the Meta probe: an account with no
    videos and an app with no permission produce the same blank column and want
    different sentences on a screen.
    """
    return "available" if any(name in payload for name in names) else "empty"


def _video_from(row: Mapping[str, Any]) -> TikTokVideoSummary:
    """One ``video/list`` row, read defensively.

    Every field is optional because TikTok genuinely omits them - a row it
    accepted the request for can still come back with ``id`` and little else -
    and because this is untrusted remote input either way. A malformed counter
    is ``None`` rather than an exception: one bad number must not cost the
    panel the other five videos.

    ``id`` is the exception. A row with no id is not a video anybody can link
    to, order or key a list by, so it is dropped by the caller rather than
    rendered with a blank identity.
    """
    return TikTokVideoSummary(
        video_id=_text(row.get("id")) or "",
        title=_text(row.get("title")),
        description=_text(row.get("video_description")),
        created_at=epoch_to_utc(row.get("create_time")),
        duration_seconds=parse_count(row.get("duration")),
        cover_image_url=_text(row.get("cover_image_url")),
        share_url=_text(row.get("share_url")),
        embed_link=_text(row.get("embed_link")),
        view_count=parse_count(row.get("view_count")),
        like_count=parse_count(row.get("like_count")),
        comment_count=parse_count(row.get("comment_count")),
        share_count=parse_count(row.get("share_count")),
    )


def _counters_word(
    rows: tuple[Mapping[str, Any], ...], *, videos: tuple[TikTokVideoSummary, ...]
) -> str:
    """Whether the per-video counters actually arrived on a page TikTok served.

    Three outcomes rather than two, and the middle one is TikTok's own: the
    request was accepted, ``error.code`` was ``ok``, and the counter fields are
    simply **not in the rows**. That is
    :data:`~meobot.integrations.tiktok.probe.ProbeVerdict.NOT_RETURNED` seen
    from production rather than from the probe, and it is the difference between
    "these videos have no views" and "this app is not served view counts".

    An account with no videos gets ``empty``: nothing was refused and nothing
    was omitted; there was simply nothing to carry a counter.
    """
    if not videos:
        return FIELD_EMPTY
    if any(name in row for row in rows for name in VIDEO_FIELDS_COUNTERS):
        return FIELD_AVAILABLE
    return FIELD_NOT_RETURNED


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__: list[str] = [
    "FIELD_AVAILABLE",
    "FIELD_EMPTY",
    "FIELD_NOT_RETURNED",
    "FIELD_REFUSALS",
    "TIKTOK_API_PRODUCT",
    "TIKTOK_API_PRODUCT_KEY",
    "TIKTOK_FIELDS_KEY",
    "TIKTOK_PROVIDER_KEY",
    "TIKTOK_TOTAL_LIKES_KEY",
    "TIKTOK_VIDEO_COUNT_KEY",
    "USER_FIELD_GROUPS",
    "TikTokAccountOverview",
    "TikTokChannelMetricsProvider",
    "TikTokRecentVideos",
    "TikTokVideoSummary",
]
