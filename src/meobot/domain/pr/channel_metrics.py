"""What a channel *is*, and what its numbers were at a stated moment.

Step 1F.2.4a. Until now a PR channel was a name, a URL and a foreign key to
``pr_platforms``. That is enough to plan a post against and not enough to answer
the first question anybody asks about a channel - *"bao nhiêu followers, và lần
cập nhật gần nhất là khi nào?"*

Three vocabularies live here, and one port.

Platform is derived, never a second column
-------------------------------------------

:class:`PrChannelPlatform` is the canonical network vocabulary - the six
top-level services the department actually publishes on. It is deliberately
**not** a new column on ``pr_channels``: that table already carries
``platform_id``, and ``pr_platforms.code`` is already the canonical token Step
1F.1 matches policy packs against. A second platform field on the channel would
be two answers to one question, and the day they disagreed - channel says
``TIKTOK``, platform row says ``FACEBOOK`` - there would be no way to tell which
one the policy engine had believed.

So :func:`platform_from_code` maps the existing code onto the canonical
vocabulary, exactly, with no guessing. A platform registered as ``FB_VN`` or
``ZALO`` maps to nothing and the channel is shown as *Chưa xác định nền tảng* -
which is the truth, and is what a manager needs to see in order to fix it. What
this function must never do is inspect a name or a URL and conclude "Facebook":
that would write a legal-obligation-bearing classification from a display
string, which is the failure ``PrPlatformService`` already exists to avoid.

Data status is derived too
---------------------------

:class:`PrChannelMetricsStatus` says where a channel's numbers come from. It is
computed from facts that already exist - is there a connection, is there a
snapshot - and stored nowhere. A stored status would be a mutable copy of two
things that can be looked up, and the failure mode is specific and bad: a row
saying ``CONNECTED_API`` for a connector that was never built.

Step 1F.2.4a built no connector, so ``has_api_connection`` was ``False`` at
every call site and only ``MANUAL`` and ``DISCONNECTED`` were reachable. Step
1F.2.4b added one, and the promise held: it added a provider and a connection
row, and this vocabulary grew exactly one member - ``ACTION_REQUIRED``, for the
state nobody could reach before, where a credential has stopped working and the
API history it collected is still real.

Trend is computed, never persisted
-----------------------------------

:func:`follower_trend` compares the latest reading with the one before it. That
is **all** it claims - see :data:`TREND_COMPARISON_LABEL`. It is not "growth
over 30 days" unless the two ``observed_at`` values happen to be 30 days apart,
and since a manual snapshot is taken whenever somebody remembers, they usually
are not. A stored delta would additionally be wrong the moment a backdated
correction was appended behind it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Canonical vocabularies
# ---------------------------------------------------------------------------


class PrChannelPlatform(StrEnum):
    """The network a channel lives on, at the top level and no finer.

    Six members, on purpose. ``FACEBOOK_PAGE`` and ``FACEBOOK_GROUP`` would be
    two names for one API, one policy pack and one set of credentials, and the
    difference between them - which the team does care about - is already
    carried by ``pr_channels.category``. ``YOUTUBE_SHORTS`` would be worse: that
    is a *format*, it lives on a YouTube channel, and a channel that posts both
    would have to be registered twice.

    The values are exactly the ``pr_platforms.code`` tokens they map from, so
    the two vocabularies are the same strings rather than a translation table
    somebody has to keep in step.
    """

    FACEBOOK = "FACEBOOK"
    INSTAGRAM = "INSTAGRAM"
    TIKTOK = "TIKTOK"
    YOUTUBE = "YOUTUBE"
    WEBSITE = "WEBSITE"
    OTHER = "OTHER"


class PrChannelMetricsStatus(StrEnum):
    """Where this channel's numbers come from right now.

    Named for what it is: the **data** badge. Step 1F.2.4b introduced a separate
    :class:`~meobot.domain.pr.channel_connections.PrChannelConnectionState` for
    the OAuth credential and a
    :class:`~meobot.domain.pr.channel_connections.PrChannelSyncStatus` for the
    last run, and one string meaning all three would have had to choose which
    truth to tell whenever they disagreed - which they do, constantly.

    Step 1F.2.4a could only produce the first two values, because no connector
    existed. Step 1F.2.4b makes the last two reachable; the derivation moved to
    :func:`~meobot.domain.pr.channel_connections.metrics_status_for`, which is
    where the connection state is known.
    """

    #: No connector and no reading anybody has recorded.
    DISCONNECTED = "DISCONNECTED"
    #: Somebody has typed numbers in. The most recent of them is the current
    #: figure, and it is as old as its ``observed_at`` says.
    MANUAL = "MANUAL"
    #: A platform connector is fetching numbers and the credential is healthy.
    CONNECTED_API = "CONNECTED_API"
    #: Step 1F.2.4b. A connector exists and its credential has stopped working.
    #: Its own state rather than a fall back to ``MANUAL`` or a hopeful
    #: ``CONNECTED_API``: the history is real API data and stays, and the badge
    #: has to say that it stopped moving and why.
    ACTION_REQUIRED = "ACTION_REQUIRED"


def platform_from_code(code: str | None) -> PrChannelPlatform | None:
    """The canonical platform behind a ``pr_platforms.code``, or ``None``.

    An exact match on the code, case-folded to upper because that is the only
    normalization ``PrPlatformService`` itself performs. Anything else - a
    regional variant, a platform the department invented, a code with a suffix -
    returns ``None``, which the UI renders as *Chưa xác định*.

    Args:
        code: The registered platform's code, or ``None`` for a channel whose
            platform row could not be read.

    Returns:
        The canonical platform, or ``None`` when this code is not one of the
        six. Never a guess.
    """
    if not code:
        return None
    try:
        return PrChannelPlatform(code.strip().upper())
    except ValueError:
        return None


def connection_status(*, has_api_connection: bool, has_snapshot: bool) -> PrChannelMetricsStatus:
    """Derive the data status when there is no connection row to consult.

    Step 1F.2.4a's two-input form, kept for the callers that genuinely have only
    those two facts. Anything that can see a connection should call
    :func:`~meobot.domain.pr.channel_connections.metrics_status_for` instead,
    which can also produce ``ACTION_REQUIRED``.

    Args:
        has_api_connection: Whether a healthy connector is bound to this channel.
        has_snapshot: Whether any metric snapshot has ever been recorded.
    """
    if has_api_connection:
        return PrChannelMetricsStatus.CONNECTED_API
    if has_snapshot:
        return PrChannelMetricsStatus.MANUAL
    return PrChannelMetricsStatus.DISCONNECTED


# ---------------------------------------------------------------------------
# The metrics a manual reading may carry
# ---------------------------------------------------------------------------

#: The point-in-time counts. A follower total is a *stock*: it is true at the
#: instant it was read and needs no window to mean something.
#:
#: Step 1F.2.4d added ``fans``. It is **not** a synonym for ``followers`` and is
#: not stored as one: on a Facebook Page ``fan_count`` counts the people who
#: liked the Page and ``followers_count`` counts the people who receive its
#: posts, and Meta let the two diverge permanently. Every report the department
#: writes quotes one or the other, so both get a column and neither is derived
#: from the other. Platforms with no such number leave it ``None``.
STOCK_METRIC_FIELDS: tuple[str, ...] = ("followers", "fans", "following", "posts_count")

#: The counts that only mean something with a window attached, which is why each
#: of them carries the window in its name. ``views`` with no window is a number
#: nobody can reconcile against anything, and Step 1B's windowless columns on
#: this table are left unwritten by this step for exactly that reason.
WINDOWED_METRIC_FIELDS: tuple[str, ...] = (
    "views_7d",
    "views_30d",
    "reach_7d",
    "reach_30d",
    "impressions_7d",
    "impressions_30d",
    "engagements_7d",
    "engagements_30d",
    "likes_30d",
    "comments_30d",
    "shares_30d",
    # --- Step 1F.2.4d ----------------------------------------------------
    #: How many posts the account published *inside the window*. Different from
    #: the ``posts_count`` stock above, which is everything it has ever
    #: published, and it is the windowed one that "trung bình mỗi bài" can be
    #: divided by.
    "posts_count_7d",
    "posts_count_30d",
    #: Reactions on the window's posts. Kept apart from ``likes_30d`` because a
    #: Facebook reaction is a like, a love, a haha or an angry, and labelling
    #: the total "Likes" would say something the number does not.
    "reactions_30d",
    #: Plays of video and Reels, over the window. Not ``views_*``: on a Meta
    #: Page an account-level "view" is a *profile* view, which is a different
    #: thing entirely and stays out of the canonical view columns for the reason
    #: :mod:`meobot.integrations.meta.provider` gives.
    "video_views_7d",
    "video_views_30d",
)

#: Everything a person may type into the manual form, in the order the form
#: offers it. One tuple, used by the command, the validator and the response, so
#: a metric cannot be accepted and then silently dropped on the way out.
MANUAL_METRIC_FIELDS: tuple[str, ...] = STOCK_METRIC_FIELDS + WINDOWED_METRIC_FIELDS

#: The largest count worth believing. Not a platform limit - it is a typo
#: filter: ``124800000000000`` is somebody's finger on a key, and storing it
#: would blow out every axis on every chart that ever reads this row. Well
#: inside ``BIGINT``.
MAX_METRIC_VALUE = 10**15

#: How far ahead of the server's clock a capture time may be. A phone or laptop
#: a couple of minutes fast must not make an honest reading unrecordable; a
#: reading dated next week is a mistake, because nobody has read next week's
#: follower count.
MAX_FUTURE_CAPTURE_SKEW = timedelta(minutes=5)

#: What the trend actually compares, in the words the UI must use. Not
#: "+2,3% / 30 ngày": two manual readings are however far apart somebody's
#: memory put them, and labelling that as a monthly rate invents a denominator.
TREND_COMPARISON_LABEL = "so với lần ghi trước"


def normalize_capture_time(moment: datetime) -> datetime:
    """A capture time in UTC, treating a naive value as already UTC.

    A browser that posts ``2026-08-20T09:30`` with no offset means "half past
    nine as I read it", and refusing that outright would make the obvious form
    input unusable. The alternative - attaching the *server's* local zone -
    would move the reading by seven hours without saying so.
    """
    return moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def is_future_capture(moment: datetime, *, now: datetime) -> bool:
    """Whether this capture time is far enough ahead to be a mistake."""
    return normalize_capture_time(moment) > now + MAX_FUTURE_CAPTURE_SKEW


def stale_days(captured_at: datetime | None, *, now: datetime) -> int | None:
    """Whole days between the latest reading and now, or ``None`` if never read.

    Computed on the server because the browser must not do date arithmetic on
    these values - the same rule the assignment intervals follow, and for the
    same reason: two implementations of "how many days" eventually disagree
    about the boundary.
    """
    if captured_at is None:
        return None
    elapsed = now - normalize_capture_time(captured_at)
    return max(0, elapsed.days)


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelFollowerTrend:
    """The change in followers between two consecutive readings.

    ``delta_pct`` is ``None`` whenever the previous reading was zero followers,
    because the percentage change from nothing is not a large number - it is not
    a number. Showing "+∞%" or silently substituting 100% would both be
    inventions, so the panel shows the absolute delta alone.
    """

    #: ``latest - previous``. Negative when a channel lost followers.
    delta: int
    #: Percent change, to one decimal place, or ``None`` when undefined.
    delta_pct: float | None = None


def follower_trend(latest: int | None, previous: int | None) -> ChannelFollowerTrend | None:
    """Compare two follower readings, or refuse to.

    Returns ``None`` - not a zero trend - when there is no previous snapshot or
    when either reading left ``followers`` blank. A missing metric means *not
    recorded*, never *zero*, so treating a blank as 0 would report a collapse
    that never happened.
    """
    if latest is None or previous is None:
        return None
    delta = latest - previous
    if previous <= 0:
        return ChannelFollowerTrend(delta=delta)
    return ChannelFollowerTrend(delta=delta, delta_pct=round(delta * 100 / previous, 1))


# ---------------------------------------------------------------------------
# The port the connector step will implement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchedChannelIdentity:
    """A channel's identity as the platform itself reports it.

    Fetched immediately after consent and again on every sync. Its job is to
    answer *"whose account did this person actually authorize?"* - which is not
    the same question as *"which channel did they click connect on"*, and the
    gap between the two is the wrong-account mistake this shape exists to make
    visible.

    ``account_id`` is the platform's own stable identifier and is what a
    connection binds to. A title changes; a handle changes; the id does not.
    """

    account_id: str
    title: str | None = None
    handle: str | None = None
    profile_url: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelMetricsReading:
    """One normalized reading, in canonical field names, ready to store.

    The boundary between "a provider's JSON" and "a row in the channel metric
    snapshot history". Every provider returns this; nothing downstream of it
    knows what YouTube is.

    **Absent stays absent.** Every metric is ``int | None`` and a provider that
    does not report something leaves it ``None`` - never ``0``. That rule
    survives from Step 1F.2.4a for the same reason it was written there: a zero
    is a measurement and a blank is not, and a connector that filled blanks with
    zeros would silently manufacture a collapse in every chart.

    ``period_start``/``period_end`` are the **effective reporting window** the
    provider actually covered - ISO dates, not timestamps - and they are what
    the idempotency fingerprint is computed over. They are not ``observed_at``:
    that is when MeoBot looked, which is a different fact and is stored
    separately. See ``docs/pr/STEP_1F24B_YOUTUBE_CHANNEL_CONNECTOR.md``.
    """

    observed_at: datetime
    #: Canonical metric name -> value, keyed by :data:`MANUAL_METRIC_FIELDS`.
    #: A name outside that tuple is a programming error and is rejected on the
    #: way in rather than silently dropped on the way out.
    metrics: Mapping[str, int | None] = field(default_factory=dict)
    #: Platform-specific numbers with no canonical column - a cumulative
    #: lifetime view count, a hidden-subscriber flag, the exact Analytics window
    #: the figures came from. Stored, never parsed, never read by a summary card.
    extra_metrics: Mapping[str, Any] | None = None
    #: The effective reporting window, as ``YYYY-MM-DD``. ``None`` when the
    #: reading carries only point-in-time counts.
    period_start: str | None = None
    period_end: str | None = None

    def canonical(self) -> dict[str, int | None]:
        """The metrics, as every canonical column including the blanks."""
        return {name: self.metrics.get(name) for name in MANUAL_METRIC_FIELDS}


@dataclass(frozen=True, slots=True)
class ProviderTokens:
    """What an OAuth exchange, or a credential redemption, handed back.

    ``durable_credential`` is *whatever this provider needs in order to obtain a
    usable access token without a person present* - deliberately not
    "refresh token", which is one provider's word for it:

    * **Google** issues a refresh token, which is exchanged on every sync;
    * **Meta** issues a long-lived Page access token, which is used directly and
      is never exchanged for anything. It has no refresh token at all.

    It is ``None`` far more often than it is set, and that is normal rather than
    a failure: Google issues one on first consent and usually omits it on
    subsequent authorizations. The caller's job is to keep the one it already
    has - see
    :meth:`~meobot.application.pr_channel_connection_service.PrChannelConnectionService.finish_authorization`,
    where treating an omitted credential as "revoke the stored one" would break
    unattended sync on every reconnect.

    This object is short-lived and never stored as it stands. It is never
    logged, never audited and never serialised into a response.
    """

    access_token: str
    expires_at: datetime | None = None
    durable_credential: str | None = None
    granted_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveredProviderAccount:
    """One platform account a channel could be bound to.

    **Carries no credential.** Whatever token the provider obtained alongside
    this account stays inside the provider; what travels is an id, a name and a
    handle. That is the difference between an account chooser and a token leak,
    and it is why this type exists instead of passing provider objects around.
    """

    account_id: str
    name: str
    handle: str | None = None
    #: How this account was reached, when that is not obvious - an Instagram
    #: account is found through a Facebook Page, and somebody managing several
    #: Pages needs that to tell two similarly-named accounts apart.
    via: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderAccountChoices:
    """Everything an authorization discovered.

    ``owner_account_id`` is the platform account that actually granted consent -
    a Meta user, for instance. It is a real provider id and it is what a
    half-finished connection records while a person is still choosing, so that
    ``provider_account_id`` never has to hold a placeholder or a lie.
    """

    owner_account_id: str
    owner_name: str | None = None
    accounts: tuple[DiscoveredProviderAccount, ...] = ()


@dataclass(frozen=True, slots=True)
class BoundProviderAccount:
    """The result of binding one discovered account.

    ``durable_credential`` is the credential *for that account specifically* -
    for Meta, the chosen Page's own access token rather than the user token
    discovery was performed with. Swapping it in is the last step of the flow.
    """

    identity: FetchedChannelIdentity
    durable_credential: str


@runtime_checkable
class AccountSelectingProvider(Protocol):
    """A provider whose authorization grants access to **several** accounts.

    Google does not need this: consent identifies one YouTube channel and there
    is nothing to choose. Meta does - one person routinely manages a dozen Pages
    - and the existing port genuinely cannot express it, since it has no notion
    of "which of these did you mean".

    So it is a second, *optional* Protocol rather than a widened one: providers
    that bind immediately stay exactly as they were, and
    :class:`~meobot.application.pr_channel_connection_service.PrChannelConnectionService`
    asks ``isinstance(client, AccountSelectingProvider)`` - a question about
    capability, not about which platform this is. Adding a provider that needs
    selection therefore requires no change to the application layer at all.

    ``runtime_checkable`` so that test is possible; the check is structural and
    matches on the two method names below.
    """

    async def discover_accounts(self, *, access_token: str) -> ProviderAccountChoices:
        """Every account this authorization could bind, and who granted it."""
        ...

    async def bind_account(self, *, access_token: str, account_id: str) -> BoundProviderAccount:
        """Bind one discovered account and return its own durable credential.

        The caller has already verified ``account_id`` against a freshly
        computed discovery result, so an implementation may assume it is one of
        its own - but should still fail loudly rather than guess if it is not.
        """
        ...


class ChannelMetricsProvider(Protocol):
    """What a platform connector is, as far as the application layer is concerned.

    A ``Protocol`` so a test can pass a fake without inheriting anything, which
    is the whole reason no test in this repository has ever contacted Google.

    Step 1F.2.4b implements exactly one of these,
    :class:`~meobot.integrations.youtube.provider.YouTubeChannelMetricsProvider`.
    There is **no** ``FacebookChannelMetricsProvider``, ``TikTokChannelMetricsProvider``
    or ``InstagramChannelMetricsProvider`` anywhere in the tree, not even as a
    stub that raises: an empty class with a real-sounding name reads, to
    somebody scanning the codebase, exactly like an integration that exists. The
    registry says those platforms are unsupported and the UI says so in words.

    The split below is deliberate. Authorization and measurement are separate
    concerns with separate failure modes, and keeping them on one protocol - as
    opposed to two - is only justified because a real provider needs both and
    the registry hands out one object.
    """

    #: The canonical platform this provider speaks for. The registry keys on it.
    platform: PrChannelPlatform

    # --- Authorization ----------------------------------------------------
    def build_authorization_url(self, *, state: str) -> str:
        """Where to send the browser for consent, carrying an opaque ``state``.

        Synchronous because it is string construction: no request is made, and
        a provider that needed one here would be doing something surprising.
        """
        ...

    async def exchange_authorization_code(self, *, code: str) -> ProviderTokens:
        """Trade a one-time authorization code for tokens."""
        ...

    async def acquire_access(self, *, durable_credential: str) -> ProviderTokens:
        """Turn the stored durable credential into a usable access token.

        Named for the outcome rather than the mechanism, because the mechanism
        differs by provider: Google exchanges a refresh token over the network,
        Meta hands back the long-lived Page token it was given. A method called
        ``refresh_access`` would have described a request Meta never makes.

        The response normally carries **no** durable credential, and that is not
        a failure - it means "keep using the one you have". The caller must not
        read the ``None`` as a revocation; see
        ``PrChannelConnectionService.finish_authorization``.
        """
        ...

    async def revoke(self, *, durable_credential: str) -> None:
        """Tell the platform to forget this grant.

        Best effort by contract: a provider that cannot revoke, or whose revoke
        endpoint is down, must not prevent MeoBot from disconnecting locally.
        """
        ...

    # --- Measurement ------------------------------------------------------
    async def fetch_channel_identity(self, *, access_token: str) -> FetchedChannelIdentity:
        """Who the access token actually belongs to."""
        ...

    async def fetch_channel_metrics(
        self, *, access_token: str, account_id: str, now: datetime
    ) -> ChannelMetricsReading:
        """One normalized reading for ``account_id``."""
        ...


__all__: list[str] = [
    "MANUAL_METRIC_FIELDS",
    "MAX_FUTURE_CAPTURE_SKEW",
    "MAX_METRIC_VALUE",
    "STOCK_METRIC_FIELDS",
    "TREND_COMPARISON_LABEL",
    "WINDOWED_METRIC_FIELDS",
    "AccountSelectingProvider",
    "BoundProviderAccount",
    "ChannelFollowerTrend",
    "ChannelMetricsProvider",
    "ChannelMetricsReading",
    "DiscoveredProviderAccount",
    "FetchedChannelIdentity",
    "PrChannelMetricsStatus",
    "PrChannelPlatform",
    "ProviderAccountChoices",
    "ProviderTokens",
    "connection_status",
    "follower_trend",
    "is_future_capture",
    "normalize_capture_time",
    "platform_from_code",
    "stale_days",
]
