"""TikTok's hosts, the API version, and the scopes MeoBot asks for.

Step 1F.2.6. Every URL this connector will ever contact is built from the
literals in this module, which is the same SSRF answer
:mod:`meobot.integrations.meta.constants` and
:mod:`meobot.integrations.youtube.constants` give: there is no code path where a
channel row, a request body, or a field inside a TikTok response can decide
where a request goes. In particular the connector **never fetches
``pr_channels.url``** - a person typed that.

Which TikTok product this is
-----------------------------

**Login Kit for Web, plus the Display API**, on ``open.tiktokapis.com/v2``. That
is a decision rather than a default, so it is written down here where somebody
reading the connector finds it first.

Deliberately **not** the TikTok API for Business (``business.tiktokapis.com``)
and **not** the Marketing API. Both would serve more - profile views, watch
time, audience demographics, traffic sources - and both cost something this
milestone declined to spend: a separate app with its own approval, and a hard
requirement that every connected account be a TikTok *Business* account, which
returns nothing at all for a Creator or personal account. The Display API works
for every account type MeoBot's channel list actually contains.

The consequence is stated rather than hidden: the metrics only the Business API
serves are **not available** through this connector, and the capability probe
prints that as evidence rather than leaving somebody to guess.

What the Display API is not
----------------------------

It is not an analytics API. It serves *lifetime* account counters and *per-video
lifetime* counters, and it has no notion of a reporting window at all - no
``since``/``until``, no daily series, no "views in the last 30 days". Anything
windowed has to be derived by MeoBot from the video list, and whether that is
sound is exactly what the probe exists to answer before any of it is built.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The TikTok Open API version this connector was written against.
#:
#: In the path of every endpoint below rather than assumed, for the reason the
#: Meta connector pins a Graph version: an unversioned endpoint means "whatever
#: TikTok considers current today", which turns a TikTok release into an
#: unannounced change in MeoBot's behaviour.
API_VERSION = "v2"

#: The scopes MeoBot asks for, and nothing else.
#:
#: Four, all read-only, and each earns its place:
#:
#: * ``user.info.basic`` - ``open_id``, ``union_id``, ``avatar_url`` and
#:   ``display_name``. The minimum Login Kit will issue a token for, and where
#:   the account identity a connection binds to comes from;
#: * ``user.info.profile`` - ``profile_deep_link``, ``bio_description``,
#:   ``is_verified`` and ``username``. What lets the panel show a person *which*
#:   TikTok account they authorized, which is the wrong-account check;
#: * ``user.info.stats`` - ``follower_count``, ``following_count``,
#:   ``likes_count`` and ``video_count``. **Nothing else grants these**, and
#:   without it a TikTok channel has no follower number at all;
#: * ``video.list`` - the account's own videos and their public counters.
#:
#: Deliberately **not** requested: ``video.upload``, ``video.publish``,
#: ``artist.certification.read``, ``research.data.basic``, and every advertising
#: or marketing scope. MeoBot reads numbers. A consent screen asking a marketing
#: manager to grant publishing rights so a dashboard can show a follower count
#: is asking for the wrong thing, and the blast radius of a leaked grant is the
#: difference between these four lines and those.
#:
#: TikTok gates ``user.info.stats`` and ``video.list`` behind app review. A
#: deployment whose app has not been approved for them gets a token carrying
#: fewer scopes than were asked for - which is why
#: :meth:`~meobot.integrations.tiktok.client.TikTokApiClient.exchange_code`
#: reads the granted set back off the token response and stores it, rather than
#: assuming this tuple was honoured.
TIKTOK_SCOPES: tuple[str, ...] = (
    "user.info.basic",
    "user.info.profile",
    "user.info.stats",
    "video.list",
)


@dataclass(frozen=True, slots=True)
class TikTokEndpoints:
    """TikTok's authorization and Open API hosts, for one API version.

    A frozen dataclass rather than loose module constants so a test can point
    the whole connector at a local base URL without monkey-patching a module,
    while production has no configuration surface that could redirect it:
    nothing here reads a host from the environment.

    Note that the authorization dialog lives on ``www.tiktok.com`` and every
    other call on ``open.tiktokapis.com``. Two hosts, because TikTok says so.
    """

    version: str = API_VERSION
    authorize_base: str = "https://www.tiktok.com"
    api_base: str = "https://open.tiktokapis.com"

    @property
    def authorize(self) -> str:
        """The consent dialog. Trailing slash included - TikTok requires it."""
        return f"{self.authorize_base}/{self.version}/auth/authorize/"

    @property
    def api(self) -> str:
        """The versioned Open API root every other call hangs off."""
        return f"{self.api_base}/{self.version}"

    @property
    def token(self) -> str:
        return f"{self.api}/oauth/token/"

    @property
    def revoke(self) -> str:
        return f"{self.api}/oauth/revoke/"

    @property
    def user_info(self) -> str:
        return f"{self.api}/user/info/"

    @property
    def video_list(self) -> str:
        return f"{self.api}/video/list/"

    @property
    def video_query(self) -> str:
        return f"{self.api}/video/query/"


#: Seconds. Bounded because a hung socket must not hold a worker slot until
#: Celery's task time limit, and generous because ``video/list`` is slower than
#: a field read.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: How many videos one ``video/list`` request asks for.
#:
#: Twenty is **TikTok's documented maximum** for ``max_count`` on this endpoint,
#: not a number MeoBot chose. Asking for more is rejected, so this is a ceiling
#: rather than a tuning knob, and the thing that actually bounds cost is
#: :data:`MAX_VIDEO_PAGES`.
VIDEO_PAGE_SIZE = 20

#: How many such pages any single walk will ever ask for.
#:
#: Five - a hundred videos - and the cap exists because an account's video list
#: has no end and a walk must have one. There is no path through this connector
#: that makes an unbounded number of requests, which is the property that
#: matters more than the number itself.
MAX_VIDEO_PAGES = 5

#: Fields on the ``user/info`` node, grouped by the scope that grants them.
#:
#: Grouped rather than flat because TikTok refuses the **whole request** when a
#: field's scope is missing - the same failure mode Graph has with insight
#: metric names - so a caller that wants to degrade around a missing scope has
#: to know which fields travel together. See
#: :meth:`~meobot.integrations.tiktok.client.TikTokApiClient.user_info`.
USER_FIELDS_BASIC: tuple[str, ...] = ("open_id", "union_id", "avatar_url", "display_name")
USER_FIELDS_PROFILE: tuple[str, ...] = ("profile_deep_link", "bio_description", "is_verified")
USER_FIELDS_STATS: tuple[str, ...] = (
    "follower_count",
    "following_count",
    "likes_count",
    "video_count",
)

#: Fields a ``video/list`` row is asked for, grouped by what a refusal means.
#:
#: Grouped for the same reason the user fields are: TikTok refuses the **whole**
#: ``fields=`` list over one name it will not serve, so a flat list would mean an
#: app not yet approved for a counter gets no cover image and no share link
#: either - and a "Video gần đây" section that could have shown eight cards shows
#: a refusal instead.
#:
#: * :data:`VIDEO_FIELDS_CORE` - what a video *is*. ``id`` and ``create_time``
#:   identify and order it, ``cover_image_url`` is the card, ``share_url`` is the
#:   only honest way to open it on TikTok, and the two text fields are what a
#:   person recognises it by. All of them are ``video.list``;
#: * :data:`VIDEO_FIELDS_COUNTERS` - the four public per-video counters. Also
#:   ``video.list``, but probed apart because
#:   :class:`~meobot.integrations.tiktok.probe.TikTokCapabilityProbe` exists
#:   precisely because whether TikTok serves them to a given app is not knowable
#:   from the documentation.
VIDEO_FIELDS_CORE: tuple[str, ...] = (
    "id",
    "create_time",
    "title",
    "video_description",
    "duration",
    "cover_image_url",
    "embed_link",
    "share_url",
)
VIDEO_FIELDS_COUNTERS: tuple[str, ...] = (
    "view_count",
    "like_count",
    "comment_count",
    "share_count",
)

#: How many videos one page of the account overview shows.
#:
#: Six rather than :data:`VIDEO_PAGE_SIZE`, because this is a *panel* on a
#: channel page and not a video library: six cards fit above the fold beside the
#: account stats, and the seventh is a scroll nobody asked for. "Xem thêm" asks
#: for another six with TikTok's own cursor, and :data:`MAX_VIDEO_PAGES` is
#: still the ceiling on how many times that can happen - the bound is the
#: connector's, not this constant's.
RECENT_VIDEO_PAGE = 6

#: ``username`` is listed apart from the rest of ``user.info.profile`` on
#: purpose.
#:
#: TikTok added it to that scope later than the other three, and a deployment
#: whose app was approved before it existed gets the whole profile group refused
#: for asking. Keeping it in its own group means the cost of that is one
#: nullable handle rather than the deep link and the verification flag as well.
#: The probe reports which of the two groups answered, which is the only way to
#: know without asking.
USER_FIELDS_USERNAME: tuple[str, ...] = ("username",)

__all__: list[str] = [
    "API_VERSION",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_VIDEO_PAGES",
    "RECENT_VIDEO_PAGE",
    "TIKTOK_SCOPES",
    "USER_FIELDS_BASIC",
    "USER_FIELDS_PROFILE",
    "USER_FIELDS_STATS",
    "USER_FIELDS_USERNAME",
    "VIDEO_FIELDS_CORE",
    "VIDEO_FIELDS_COUNTERS",
    "VIDEO_PAGE_SIZE",
    "TikTokEndpoints",
]
