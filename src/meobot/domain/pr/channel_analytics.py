"""What the stored readings say that no single reading does.

Step 1F.2.4d. Growth, growth rate, engagement rate, engagement per post and the
month's best post - every one of them derived, here, from the channel metric
snapshot history, and **none of them asked of Meta**.

Why MeoBot computes growth rather than fetching it
---------------------------------------------------

Meta will happily answer "how many followers does this Page have". It will not
reliably answer "how many did it gain in the last 30 days" - the Page Insights
metrics that used to say so are the same family Graph v23 retired, and the ones
that remain are day-partitioned series whose definition Meta changes without
telling anybody. MeoBot already stores a dated follower count for every sync it
has ever run. Subtracting two numbers it wrote down itself is exact, is
reproducible, survives a Graph version bump, and keeps working for the manual
readings a person typed for a platform with no connector at all.

So the rule this module encodes is: **anything derivable from our own snapshots
is derived from our own snapshots.** What is fetched is what only the platform
knows - the current counts.

A comparison names what it compared
-------------------------------------

:class:`MetricChange` carries ``baseline_observed_at`` and ``baseline_age_days``
beside the delta, and the panel prints them. That is not decoration. Snapshots
land whenever a sync ran or somebody typed one in, so "30 ngày" is a request,
not a fact: the nearest reading may be 27 days old or 33. A card reading
"+2.480 trong 30 ngày" over a comparison against a 33-day-old snapshot is a
small lie that nobody can detect from the screen. A card reading "+2.480 · so
với 33 ngày trước" is the truth and costs one line.

And when there is no reading near enough, the answer is ``None`` - not zero, not
the oldest row we happen to hold. :data:`BASELINE_TOLERANCE_DAYS` says how near
is near enough; outside it a channel connected last Tuesday shows "—" for its
30-day growth, which is exactly what is known about it.

Absent is not zero, one layer up
---------------------------------

Every input is ``int | None`` and every derivation returns ``None`` the moment
one of its operands is missing. There is no ``or 0`` anywhere in this file. A
Page whose ``posts_count_30d`` is ``None`` because the post window could not be
read has *no* average engagement per post; it does not have an average of zero,
and it does not have an average computed as if it had posted nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from meobot.domain.pr.channel_metrics import normalize_capture_time

# ---------------------------------------------------------------------------
# Windows and how near a baseline has to be
# ---------------------------------------------------------------------------

#: The comparison windows the panel offers, in days.
ANALYTICS_WINDOWS: tuple[int, ...] = (7, 30)

#: How far a baseline reading may sit from the instant a window asks for, per
#: window, in days.
#:
#: Chosen from how often readings actually arrive rather than from arithmetic
#: neatness. A connected channel syncs about daily, so a 7-day comparison finds
#: a reading within hours; a channel somebody records by hand once a fortnight
#: will not have one within three days of the 7-day mark, and its weekly growth
#: card stays blank rather than quietly comparing against a 14-day gap.
#:
#: The 30-day tolerance is wider because a monthly rhythm is what the manual
#: readings actually have, and because a week of drift on a month-long window
#: is a much smaller distortion than three days of drift on a week-long one.
BASELINE_TOLERANCE_DAYS: Mapping[int, int] = {7: 3, 30: 7}

#: The longest history any derivation here needs, in days. The caller reads this
#: to bound its query: nothing in this module can use a reading older than the
#: widest window plus its own tolerance, so loading more would be work thrown
#: away.
ANALYTICS_LOOKBACK_DAYS = max(ANALYTICS_WINDOWS) + max(BASELINE_TOLERANCE_DAYS.values())

#: Longest excerpt kept from a post's own text. Enough to recognise which post
#: it was; not so much that a summary card becomes the post.
TOP_POST_EXCERPT_LIMIT = 200

#: Where the Facebook connector records the window's best post inside
#: ``extra_metrics``.
#:
#: Defined **here**, in the domain, and imported by the connector that writes it
#: - not the other way round. ``extra_metrics`` is a JSON column with no schema,
#: so the only thing keeping a writer and a reader in step is that they name the
#: same constant; two string literals in two packages would have drifted the
#: first time somebody renamed one of them.
FACEBOOK_TOP_POST_KEY = "facebook_top_post_30d"

#: Every ``extra_metrics`` key that may hold a best-of-window post, newest
#: convention first. A provider that starts recording one adds its key here and
#: the panel reads it with no further change; a key absent from this tuple is
#: simply never read, which is the safe direction.
TOP_POST_EXTRA_KEYS: tuple[str, ...] = (FACEBOOK_TOP_POST_KEY,)

#: Which connector wrote a reading, as recorded in ``extra_metrics``.
PROVIDER_EXTRA_KEY = "meta_provider"

#: Where the Facebook connector records **profile views** inside ``extra_metrics``.
#:
#: Not a canonical column, and deliberately: ``page_views_total`` counts views of
#: the Page's own profile, which is not what "Views" means on a YouTube or TikTok
#: card sitting in the same channel list. One column meaning two things is how a
#: dashboard starts lying quietly.
#:
#: But it is a real number a PR manager asks for, so the panel shows it - under
#: its own name, projected here into a typed field. The browser never reads these
#: keys: a screen that knew the string ``facebook_page_views_30d`` would be
#: holding connector vocabulary, and would break silently the day a connector
#: renamed it. See :func:`page_views_from`.
FACEBOOK_PAGE_VIEWS_7D_KEY = "facebook_page_views_7d"
FACEBOOK_PAGE_VIEWS_30D_KEY = "facebook_page_views_30d"

#: Where the Facebook connector records which post fields it actually got.
#:
#: A mapping of ``reactions`` / ``comments`` / ``shares`` to one of the five
#: words :mod:`meobot.integrations.meta.posts` defines. This is the only
#: evidence anybody has, months later, for why ``reactions_30d`` is ``NULL`` -
#: a Page nobody reacted to and a permission the app was never granted produce
#: the same blank column and want different sentences on the screen.
POST_FIELDS_EXTRA_KEY = "facebook_post_fields"

#: Where the Meta connector records which Page Insights metrics answered.
INSIGHT_METRICS_EXTRA_KEY = "meta_insight_metrics_available"

#: The settled 30-day window's last day, as ``YYYY-MM-DD``.
#:
#: Read so the panel can say what the monthly figures actually cover. Meta
#: Insights settles up to about 48 hours behind, so a card headed "30 ngày"
#: beside a sync timestamp from this morning implies a window that reaches the
#: current moment, and it does not.
WINDOW_30D_END_EXTRA_KEY = "meta_window_30d_end"

#: What a person is told about the cards a platform has stopped filling in.
#:
#: This sentence exists because of a support question, not a design idea. A
#: manager who sees blank reach cards concludes that MeoBot is broken - which is
#: the reasonable conclusion, and the wrong one - and asks. Saying *why* costs
#: one line and answers it before it is asked.
#:
#: Composed here rather than in the browser, for the reason the whole panel
#: follows: the screen holds no platform vocabulary and makes no judgement about
#: what a platform reports. It renders the sentence the server sent, or none.
PROVIDER_LIMITATION_NOTES: Mapping[str, str] = {
    "FACEBOOK": (
        "Facebook không còn cung cấp reach/impressions ở cấp Trang (Graph v23), "
        "nên MeoBot để trống thay vì ước lượng."
    ),
}


def limitation_note(extra_metrics: Mapping[str, Any] | None) -> str | None:
    """What to say about this reading's blank cards, or nothing.

    ``None`` for every platform that has not lost a metric, and for a manual
    reading, which has no provider and no such story to tell.
    """
    if not extra_metrics:
        return None
    provider = extra_metrics.get(PROVIDER_EXTRA_KEY)
    if not isinstance(provider, str):
        return None
    return PROVIDER_LIMITATION_NOTES.get(provider.strip().upper())


# ---------------------------------------------------------------------------
# Why a card is blank, which is a different question from whether it is
# ---------------------------------------------------------------------------


class MetricAvailability(StrEnum):
    """Why a number is or is not on the screen.

    Four answers, because a blank card provokes exactly one question - *is
    MeoBot broken?* - and three of these four say no in different ways that lead
    to different actions.
    """

    #: Read successfully. The value may still be ``0``; a measured zero is an
    #: answer and is displayed as one.
    AVAILABLE = "AVAILABLE"
    #: Nobody measured it. An older snapshot taken before a metric existed, a
    #: platform with no connector, a sync that has not run yet. Time fixes it.
    NOT_RECORDED = "NOT_RECORDED"
    #: The platform served the request and refused *this* field for want of a
    #: permission. A person reauthorizing with a wider consent fixes it; waiting
    #: does not.
    NOT_PERMITTED = "NOT_PERMITTED"
    #: The platform no longer offers the metric at all on the API version this
    #: deployment uses. Nothing fixes it, and saying so is the whole point -
    #: otherwise somebody re-opens the same support ticket every quarter.
    UNSUPPORTED = "UNSUPPORTED"


#: What each availability is called on a card, in Vietnamese.
#:
#: Composed here rather than in the browser, like every other sentence this
#: module produces. A screen that decided which platforms retired reach would be
#: holding platform knowledge the server is the authority on.
AVAILABILITY_LABELS: Mapping[MetricAvailability, str] = {
    MetricAvailability.AVAILABLE: "Có dữ liệu",
    MetricAvailability.NOT_RECORDED: "Chưa có dữ liệu",
    MetricAvailability.NOT_PERMITTED: "Chưa có quyền đọc",
    MetricAvailability.UNSUPPORTED: "Không còn được Meta cung cấp",
}

#: The longer explanation, for the information area rather than the card.
AVAILABILITY_NOTES: Mapping[MetricAvailability, str] = {
    MetricAvailability.NOT_RECORDED: "Chưa có lần đo nào ghi lại chỉ số này.",
    MetricAvailability.NOT_PERMITTED: (
        "Quyền hiện tại của kết nối Facebook không đọc được chỉ số này. "
        "Cần cấp lại quyền cho ứng dụng mới có số liệu."
    ),
    MetricAvailability.UNSUPPORTED: (
        "Meta hiện không còn cung cấp chỉ số này qua API đang dùng, "
        "nên MeoBot để trống thay vì ước lượng."
    ),
}

#: Canonical metric name -> the Vietnamese heading a card gives it.
#:
#: Only the metrics that can appear in :attr:`MetricCapabilities.unavailable`
#: need one; the panel's own cards carry their headings in the markup. Keeping
#: the two lists separate rather than building one giant label table is
#: deliberate: this one is about *explaining an absence*, and the day somebody
#: renames a card the explanation should not silently follow it.
#: The **30-day** variants only, because those are the cards the panel offers.
#: Explaining the absence of a "Reach 7 ngày" card that was never drawn would
#: double the length of an information area nobody would then read.
UNAVAILABLE_METRIC_LABELS: Mapping[str, str] = {
    "reach_30d": "Reach 30 ngày",
    "impressions_30d": "Impressions 30 ngày",
    "reactions_30d": "Reactions 30 ngày",
    "comments_30d": "Comments 30 ngày",
    "shares_30d": "Shares 30 ngày",
    "top_post_30d": "Bài tốt nhất 30 ngày",
}

#: Metrics a provider is known never to serve on the API version MeoBot uses.
#:
#: This is a **claim about the platform**, not about the reading, which is why it
#: is keyed by provider rather than inferred from a null. A Facebook Page's
#: ``reach_30d`` is not "not measured yet" and never will be: Graph v23 removed
#: ``page_impressions`` and ``page_impressions_unique`` from Page Insights, and
#: no sync will ever fill those cards again.
#:
#: Deliberately **not** listing ``views_7d``/``views_30d``. Meta still serves a
#: Page view metric; MeoBot declines to file profile views in a column that means
#: video/watch views everywhere else in the product. That is MeoBot's decision
#: rather than Meta's, and reporting it as "Meta stopped providing this" would be
#: blaming somebody else for our own mapping. Profile views appear under their
#: own name instead - see :data:`FACEBOOK_PAGE_VIEWS_30D_KEY`.
PROVIDER_UNSUPPORTED_METRICS: Mapping[str, tuple[str, ...]] = {
    "FACEBOOK": ("reach_7d", "reach_30d", "impressions_7d", "impressions_30d"),
}

#: How the connector's post-field words map onto an availability.
#:
#: The words themselves are defined by
#: :mod:`meobot.integrations.meta.posts`; this is the domain's reading of them,
#: and the two ``AVAILABLE`` entries are the ones worth pausing on. ``empty``
#: means Graph answered and there was nothing in it - a window with no posts, or
#: no post that was ever shared - which is a **measurement**, so the column is a
#: real ``0`` and the metric counts as available.
POST_FIELD_AVAILABILITY: Mapping[str, MetricAvailability] = {
    "available": MetricAvailability.AVAILABLE,
    "empty": MetricAvailability.AVAILABLE,
    "not_permitted": MetricAvailability.NOT_PERMITTED,
    "absent": MetricAvailability.NOT_RECORDED,
    "not_read": MetricAvailability.NOT_RECORDED,
}


class TrendDirection(StrEnum):
    """Which way a number moved between two readings.

    Three members and no ``UNKNOWN``: not knowing is expressed by the whole
    :class:`MetricChange` being ``None``, because a direction with no delta
    behind it is a badge with nothing under it.
    """

    UP = "UP"
    DOWN = "DOWN"
    #: Genuinely unchanged. A real and common answer for a small Page over a
    #: week, and not the same as "we could not tell".
    FLAT = "FLAT"


def direction_of(delta: int) -> TrendDirection:
    """The arrow for a delta. Zero is ``FLAT``, never ``UP``."""
    if delta > 0:
        return TrendDirection.UP
    if delta < 0:
        return TrendDirection.DOWN
    return TrendDirection.FLAT


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricObservation:
    """One stored reading, reduced to what a derivation needs.

    Deliberately not the ORM row. This module is domain code and must stay
    computable in a test that has no database - and keeping the metrics in a
    plain mapping rather than twenty attributes is what lets :func:`change_over`
    work for any metric name instead of needing a branch per column.
    """

    observed_at: datetime
    #: Canonical metric name -> value, keyed by
    #: :data:`~meobot.domain.pr.channel_metrics.MANUAL_METRIC_FIELDS`. A name
    #: that is absent and a name mapped to ``None`` mean the same thing here:
    #: not recorded.
    metrics: Mapping[str, int | None]
    #: The reading's platform-specific half. Read for exactly one thing - the
    #: month's best post - and never for a canonical figure.
    extra_metrics: Mapping[str, Any] | None = None

    def value(self, metric: str) -> int | None:
        """This reading's figure for ``metric``, or ``None``."""
        return self.metrics.get(metric)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricChange:
    """One metric, then and now, and how far apart "then" really was.

    ``delta_pct`` is ``None`` whenever the baseline was zero, for the reason
    :class:`~meobot.domain.pr.channel_metrics.ChannelFollowerTrend` gives: the
    percentage change from nothing is not a large number, it is not a number.
    """

    metric: str
    #: The window this comparison was *asked* for, in days. What was actually
    #: compared is ``baseline_age_days``, and the two differ routinely.
    window_days: int
    latest: int
    baseline: int
    delta: int
    direction: TrendDirection
    delta_pct: float | None = None
    baseline_observed_at: datetime | None = None
    #: Whole days between the baseline reading and the latest one. The number
    #: the panel prints beside the delta so that "30 ngày" is never a claim the
    #: data does not support.
    baseline_age_days: int | None = None


@dataclass(frozen=True, slots=True)
class TopPost:
    """The window's best-performing post, as the connector recorded it.

    Read out of ``extra_metrics`` rather than a column: a post is an id, a link,
    a time, three counts and some text, which is not a metric and would be a
    foreign key to a table MeoBot does not keep post rows in.
    """

    post_id: str
    engagements: int
    permalink_url: str | None = None
    created_time: datetime | None = None
    #: The first :data:`TOP_POST_EXCERPT_LIMIT` characters of the post's own
    #: text, so a manager can tell which post this was without leaving MeoBot.
    excerpt: str | None = None
    reactions: int | None = None
    comments: int | None = None
    shares: int | None = None


@dataclass(frozen=True, slots=True)
class UnavailableMetric:
    """One card that will not fill, and why not.

    Sent so the panel can group these somewhere other than the main grid. A
    "Reach — " card sitting beside six live numbers reads as a broken sync to
    every manager who sees it, and they are right to read it that way: a blank
    where a number belongs is a defect unless something says otherwise.
    """

    metric: str
    #: The Vietnamese heading, from :data:`UNAVAILABLE_METRIC_LABELS`.
    label: str
    availability: MetricAvailability
    #: The short chip - "Chưa có quyền đọc", "Không còn được Meta cung cấp".
    availability_label: str
    #: The full sentence, for a tooltip or an information area.
    note: str


@dataclass(frozen=True, slots=True)
class MetricCapabilities:
    """What this reading's platform and this connection's grant could answer.

    Derived from what the connector recorded beside the numbers, never guessed
    from the numbers themselves. That distinction is the whole file: ``NULL``
    says a card is blank, and only this says whether waiting, reauthorizing, or
    nothing at all is the fix.

    Every flag defaults to ``False``. A reading with no capability metadata -
    every snapshot written before this milestone - therefore reports "we do not
    know that this is available", which is the safe direction: the panel shows
    the number if there is one and explains nothing it cannot justify.
    """

    reach_available: bool = False
    impressions_available: bool = False
    reactions_available: bool = False
    comments_available: bool = False
    shares_available: bool = False
    video_views_available: bool = False
    page_views_available: bool = False
    #: Whether the month's best post can honestly be called the *best* one. See
    #: :func:`top_post_is_rankable`.
    top_post_rankable: bool = False
    #: The connector's own per-field words, passed through for an operator
    #: reading the panel with a probe run open beside it. The panel never
    #: branches on these strings - the flags above are what it reads - but a
    #: support conversation goes much faster when the screen and the probe use
    #: the same vocabulary.
    post_fields: Mapping[str, str] = field(default_factory=dict)
    #: Which Page Insights metrics answered on the sync that wrote this reading.
    insight_metrics_available: tuple[str, ...] = ()
    #: The settled window's last day, ``YYYY-MM-DD``, or ``None``.
    window_30d_end: str | None = None
    #: Every card that will not fill, with its reason. Only ``UNSUPPORTED`` and
    #: ``NOT_PERMITTED`` appear: those are the two that will not fix themselves,
    #: and listing every not-yet-measured metric would bury them.
    unavailable: tuple[UnavailableMetric, ...] = ()


@dataclass(frozen=True, slots=True)
class ChannelAnalytics:
    """Everything the management view of a channel is made of.

    The current counts are the latest reading's, unchanged. Everything below
    them is arithmetic over readings MeoBot stored, and every field of it is
    optional because every input is.
    """

    #: When the reading these figures come from was taken.
    observed_at: datetime

    # --- Current counts, straight off the latest reading ------------------
    #
    # The **whole** canonical vocabulary, not just what Step 1F.2.4d added. The
    # panel reads one object; a screen that took its Facebook cards from here
    # and its YouTube cards from the raw snapshot beside it would be two ways of
    # saying the same thing, and the day they disagreed about what "absent"
    # meant would be a bug nobody could see.
    followers: int | None = None
    fans: int | None = None
    following: int | None = None
    posts_count: int | None = None
    engagements_7d: int | None = None
    engagements_30d: int | None = None
    posts_count_7d: int | None = None
    posts_count_30d: int | None = None
    reactions_30d: int | None = None
    likes_30d: int | None = None
    comments_30d: int | None = None
    shares_30d: int | None = None
    video_views_7d: int | None = None
    video_views_30d: int | None = None
    #: Reported by the platforms that have them. A Facebook Page reports none of
    #: these - Graph v23 retired reach and impressions, and its account-level
    #: "views" are profile views - so they stay ``None`` there and the cards are
    #: simply not drawn. See :mod:`meobot.integrations.meta.provider`.
    views_7d: int | None = None
    views_30d: int | None = None
    reach_7d: int | None = None
    reach_30d: int | None = None
    impressions_7d: int | None = None
    impressions_30d: int | None = None

    #: Views of the Page's own profile, projected out of ``extra_metrics``.
    #:
    #: Under its own name and never in ``views_*``: a profile view is not what
    #: "Views" means on a YouTube card in the same channel list. Typed here so
    #: no browser has to know the connector's JSON key - see
    #: :data:`FACEBOOK_PAGE_VIEWS_30D_KEY`.
    page_views_7d: int | None = None
    page_views_30d: int | None = None

    # --- Derived from history ---------------------------------------------
    follower_growth_7d: MetricChange | None = None
    follower_growth_30d: MetricChange | None = None
    engagement_change_7d: MetricChange | None = None
    engagement_change_30d: MetricChange | None = None

    # --- Derived from the latest reading alone ----------------------------
    #: Engagements over the month divided by followers, as a ratio - ``0.0247``
    #: for 2,47%. A ratio rather than a percentage so that one place decides how
    #: many decimals a screen shows.
    engagement_per_follower_30d: float | None = None
    #: Interactions on the month's posts divided by how many posts there were.
    #: Computed from the post-level sums rather than by dividing
    #: ``engagements_30d``, which is a broader, page-level figure - see
    #: :func:`average_engagement_per_post`.
    average_engagement_per_post_30d: float | None = None

    top_post_30d: TopPost | None = None

    #: One Vietnamese sentence about metrics this platform no longer reports, or
    #: ``None``. Composed by :func:`limitation_note` so the browser holds no
    #: platform vocabulary at all.
    limitation_note: str | None = None

    #: What this platform and this grant could answer, and what they could not.
    #: Always present - a reading with no capability metadata gets an object of
    #: ``False`` flags and an empty ``unavailable``, which says "we cannot
    #: explain any of these blanks" rather than inventing an explanation.
    capabilities: MetricCapabilities = field(default_factory=MetricCapabilities)

    @property
    def follower_growth_rate_7d(self) -> float | None:
        """The weekly follower change as a percentage, or ``None``.

        A property rather than a stored field because it is already inside
        :attr:`follower_growth_7d` - copying it would be two places to keep in
        step, and the second one would be wrong first. ``None`` both when there
        is no baseline near enough and when the baseline was zero, because the
        percentage change from nothing is not a small number, it is not a
        number.
        """
        return None if self.follower_growth_7d is None else self.follower_growth_7d.delta_pct

    @property
    def follower_growth_rate_30d(self) -> float | None:
        """The monthly follower change as a percentage, or ``None``."""
        return None if self.follower_growth_30d is None else self.follower_growth_30d.delta_pct


# ---------------------------------------------------------------------------
# Derivations
# ---------------------------------------------------------------------------


def baseline_for(
    observations: Sequence[MetricObservation],
    *,
    metric: str,
    latest: MetricObservation,
    window_days: int,
) -> MetricObservation | None:
    """The reading nearest to ``window_days`` before ``latest`` that reports ``metric``.

    Nearest rather than "the newest one at or before the target". A sync that
    ran six hours after the 30-day mark is a better baseline than one that ran
    four days before it, and the reason the choice is safe to make is that
    :class:`MetricChange` reports how old the winner actually was.

    Candidates must:

    * be older than ``latest`` - comparing a reading with itself, or with one
      taken after it, is not a comparison;
    * report ``metric`` as something other than ``None``. A reading that left
      followers blank cannot be a follower baseline, and skipping to the next
      one is right: a blank is *not recorded*, so there is nothing to subtract;
    * sit within :data:`BASELINE_TOLERANCE_DAYS` of the target instant.

    Ties go to the **older** candidate, so the answer does not depend on the
    order rows came back in.

    Returns:
        The chosen reading, or ``None`` when nothing qualifies - which the
        caller renders as "—" and never as a growth of zero.
    """
    if latest.value(metric) is None:
        return None
    anchor = normalize_capture_time(latest.observed_at)
    target = anchor - timedelta(days=window_days)
    tolerance = timedelta(days=BASELINE_TOLERANCE_DAYS.get(window_days, window_days))

    best: MetricObservation | None = None
    best_distance: timedelta | None = None
    for candidate in observations:
        moment = normalize_capture_time(candidate.observed_at)
        if moment >= anchor or candidate.value(metric) is None:
            continue
        distance = abs(moment - target)
        if distance > tolerance:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = candidate, distance
        # Deterministic tie-break: the older of two equally close readings, so
        # the answer does not depend on the order rows came back in.
        elif (
            distance == best_distance
            and best is not None
            and moment < normalize_capture_time(best.observed_at)
        ):
            best = candidate
    return best


def change_over(
    observations: Sequence[MetricObservation],
    *,
    metric: str,
    latest: MetricObservation,
    window_days: int,
) -> MetricChange | None:
    """How ``metric`` moved over roughly ``window_days``, or ``None``.

    ``None`` in three cases, all of which mean *we do not know*: the latest
    reading does not carry the metric, no baseline is near enough, or the
    baseline does not carry it either. None of the three is a change of zero.
    """
    current = latest.value(metric)
    if current is None:
        return None
    baseline = baseline_for(observations, metric=metric, latest=latest, window_days=window_days)
    if baseline is None:
        return None
    before = baseline.value(metric)
    if before is None:  # pragma: no cover - baseline_for already required it
        return None

    delta = current - before
    anchor = normalize_capture_time(latest.observed_at)
    moment = normalize_capture_time(baseline.observed_at)
    return MetricChange(
        metric=metric,
        window_days=window_days,
        latest=current,
        baseline=before,
        delta=delta,
        direction=direction_of(delta),
        # From nothing to something is not a percentage. The absolute delta is
        # still true and is still shown.
        delta_pct=round(delta * 100 / before, 1) if before > 0 else None,
        baseline_observed_at=baseline.observed_at,
        baseline_age_days=max(0, (anchor - moment).days),
    )


def engagement_per_follower(engagements: int | None, followers: int | None) -> float | None:
    """Engagements over the month per follower, as a ratio.

    ``None`` when either input is missing **or when there are no followers**.
    Dividing by zero followers is not "infinite engagement", and a Page with no
    audience has no engagement rate to report.
    """
    if engagements is None or followers is None or followers <= 0:
        return None
    return round(engagements / followers, 4)


def average_engagement_per_post(
    *,
    reactions: int | None,
    comments: int | None,
    shares: int | None,
    posts: int | None,
) -> float | None:
    """Interactions per post over the window, from the post-level sums.

    **Not** ``engagements_30d / posts_count_30d``, and the difference matters.
    ``page_post_engagements`` is a page-level metric counting every engagement
    that happened during the window - including engagements on posts published
    months earlier - while ``posts_count_30d`` counts only what was published
    inside it. Dividing one by the other mixes two populations and inflates the
    average for any account with a back catalogue.

    The three post-level sums, by contrast, were computed over exactly the posts
    being counted, so this division is over one population.

    ``None`` unless all four inputs are present and at least one post exists. A
    month with no posts has no average - not an average of zero.
    """
    if reactions is None or comments is None or shares is None:
        return None
    if posts is None or posts <= 0:
        return None
    return round((reactions + comments + shares) / posts, 1)


def top_post_from(
    extra_metrics: Mapping[str, Any] | None,
    *,
    keys: Sequence[str] = TOP_POST_EXTRA_KEYS,
) -> TopPost | None:
    """Read the connector's recorded best post, defensively.

    ``extra_metrics`` is free-form JSON written by a connector and read back
    months later, possibly by a version of this code that ships after the
    connector changed shape. So every field is checked and anything unreadable
    yields ``None`` rather than a half-built object or an exception on a page
    load - the same rule the transport layer applies to Graph's own JSON.
    """
    if not extra_metrics:
        return None
    payload = next(
        (
            candidate
            for candidate in (extra_metrics.get(key) for key in keys)
            if isinstance(candidate, dict)
        ),
        None,
    )
    if payload is None:
        return None
    post_id = _text(payload.get("post_id"))
    engagements = _count(payload.get("engagements"))
    if post_id is None or engagements is None:
        return None
    return TopPost(
        post_id=post_id,
        engagements=engagements,
        permalink_url=_text(payload.get("permalink_url")),
        created_time=_moment(payload.get("created_time")),
        excerpt=_text(payload.get("excerpt")),
        reactions=_count(payload.get("reactions")),
        comments=_count(payload.get("comments")),
        shares=_count(payload.get("shares")),
    )


def page_views_from(extra_metrics: Mapping[str, Any] | None) -> tuple[int | None, int | None]:
    """Profile views for the 7- and 30-day windows, out of ``extra_metrics``.

    The one place in the codebase that knows those two JSON keys. Everything
    above this - the API response, the panel, the tests - works in terms of
    ``page_views_7d`` and ``page_views_30d``, so a connector renaming its key is
    a one-line change here rather than a silent blank on a screen.

    Defensive for the same reason :func:`top_post_from` is: ``extra_metrics`` is
    schemaless JSON written by a connector and read back by a version of this
    code that ships later. Anything that is not a plain non-negative integer is
    ``None``, never an exception on a page load.
    """
    if not extra_metrics:
        return None, None
    return (
        _count(extra_metrics.get(FACEBOOK_PAGE_VIEWS_7D_KEY)),
        _count(extra_metrics.get(FACEBOOK_PAGE_VIEWS_30D_KEY)),
    )


def post_field_states(extra_metrics: Mapping[str, Any] | None) -> dict[str, str]:
    """The connector's per-field words, validated into plain strings.

    An empty mapping when the reading predates the connector recording them,
    which is the honest answer for every snapshot written before this milestone:
    not "everything was fine", just nothing recorded either way.
    """
    if not extra_metrics:
        return {}
    payload = extra_metrics.get(POST_FIELDS_EXTRA_KEY)
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        # Only words this domain understands. A connector that starts writing
        # something new is ignored here rather than passed to a screen that
        # would render it raw.
        if isinstance(key, str) and isinstance(value, str) and value in POST_FIELD_AVAILABILITY
    }


def insight_metrics_from(extra_metrics: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Which Page Insights metrics the sync that wrote this reading got."""
    if not extra_metrics:
        return ()
    payload = extra_metrics.get(INSIGHT_METRICS_EXTRA_KEY)
    if not isinstance(payload, list):
        return ()
    return tuple(item for item in payload if isinstance(item, str) and item)


def provider_of(extra_metrics: Mapping[str, Any] | None) -> str | None:
    """Which connector wrote this reading - ``"FACEBOOK"``, ``"INSTAGRAM"``, ``None``."""
    if not extra_metrics:
        return None
    provider = extra_metrics.get(PROVIDER_EXTRA_KEY)
    return provider.strip().upper() or None if isinstance(provider, str) else None


def availability_of(
    metric: str,
    *,
    value: int | None,
    provider: str | None,
    post_fields: Mapping[str, str],
) -> MetricAvailability:
    """Why this metric is or is not on the screen, in the order causes rule out.

    The order is the argument. A value that arrived settles it - a number is a
    number whatever the metadata says. After that, a metric the platform is
    known never to serve is ``UNSUPPORTED``, because that answer is about the
    platform rather than this reading and must not be downgraded to "not
    measured yet" by a sync that happens to be young. Only then does the
    connector's own per-field word get a say, and if it has nothing to say the
    answer is ``NOT_RECORDED`` - which is the truthful "we do not know".
    """
    if value is not None:
        return MetricAvailability.AVAILABLE
    if provider is not None and metric in PROVIDER_UNSUPPORTED_METRICS.get(provider, ()):
        return MetricAvailability.UNSUPPORTED
    word = post_fields.get(_POST_FIELD_FOR.get(metric, ""))
    if word is not None:
        return POST_FIELD_AVAILABILITY[word]
    return MetricAvailability.NOT_RECORDED


#: Which post-level field backs which canonical column, for
#: :func:`availability_of`. Three entries, because three columns are summed from
#: the post listing and nothing else in the vocabulary is.
_POST_FIELD_FOR: Mapping[str, str] = {
    "reactions_30d": "reactions",
    "comments_30d": "comments",
    "shares_30d": "shares",
}


def top_post_is_rankable(reactions: MetricAvailability, comments: MetricAvailability) -> bool:
    """Whether "the month's best post" is a claim the data supports.

    It is not, when the reaction or comment summaries could not be read. The
    connector still ranks the window's posts - it has to pick one, and ranking
    on what arrived beats ranking on nothing - but the result is then the
    **most-shared** post wearing the words "best performing", and a manager who
    reports that to a client has been misled by MeoBot rather than by Meta.

    So the panel hides the card entirely in that case and says why in the
    unavailable list, which is the honest version of the same screen. It comes
    back by itself the day the grant covers the summaries; nothing here needs
    changing then.

    Takes the two **availabilities** rather than the connector's raw words, and
    that is what makes it work on older readings: a snapshot from before the
    connector recorded any per-field metadata, but carrying 900 reactions and
    120 comments, is rankable - the numbers arrived, which is the strongest
    evidence there is that they could be read. See :func:`availability_of` for
    the order those causes are ruled out in.
    """
    return reactions is MetricAvailability.AVAILABLE and comments is MetricAvailability.AVAILABLE


def capabilities_from(
    extra_metrics: Mapping[str, Any] | None,
    *,
    values: Mapping[str, int | None],
) -> MetricCapabilities:
    """What this reading's platform and grant could answer.

    Args:
        extra_metrics: the reading's platform-specific half, as stored.
        values: the reading's canonical figures, so a metric that *arrived* is
            reported available whatever the metadata says. A number on the
            screen is the strongest evidence there is that it could be read.
    """
    provider = provider_of(extra_metrics)
    post_fields = post_field_states(extra_metrics)

    def state(metric: str) -> MetricAvailability:
        return availability_of(
            metric, value=values.get(metric), provider=provider, post_fields=post_fields
        )

    states = {
        metric: state(metric) for metric in UNAVAILABLE_METRIC_LABELS if metric != "top_post_30d"
    }
    rankable = top_post_is_rankable(states["reactions_30d"], states["comments_30d"])
    if not rankable:
        # Why the best-post card is absent, in the same list as every other
        # absence. The reason follows the summaries: refused is refused, and
        # never recorded is never recorded.
        states["top_post_30d"] = (
            MetricAvailability.NOT_PERMITTED
            if states["reactions_30d"] is MetricAvailability.NOT_PERMITTED
            or states["comments_30d"] is MetricAvailability.NOT_PERMITTED
            else MetricAvailability.NOT_RECORDED
        )

    page_views_7d, page_views_30d = page_views_from(extra_metrics)
    return MetricCapabilities(
        reach_available=states["reach_30d"] is MetricAvailability.AVAILABLE,
        impressions_available=states["impressions_30d"] is MetricAvailability.AVAILABLE,
        reactions_available=states["reactions_30d"] is MetricAvailability.AVAILABLE,
        comments_available=states["comments_30d"] is MetricAvailability.AVAILABLE,
        shares_available=states["shares_30d"] is MetricAvailability.AVAILABLE,
        video_views_available=values.get("video_views_30d") is not None,
        page_views_available=page_views_30d is not None or page_views_7d is not None,
        top_post_rankable=rankable,
        post_fields=post_fields,
        insight_metrics_available=insight_metrics_from(extra_metrics),
        window_30d_end=_day(extra_metrics),
        unavailable=tuple(
            UnavailableMetric(
                metric=metric,
                label=UNAVAILABLE_METRIC_LABELS[metric],
                availability=availability,
                availability_label=AVAILABILITY_LABELS[availability],
                note=AVAILABILITY_NOTES[availability],
            )
            for metric, availability in states.items()
            # Only the two that will not fix themselves. Listing every
            # not-yet-measured metric would bury them in a list nobody reads,
            # and "chưa có dữ liệu" is already what the card itself shows.
            if availability in (MetricAvailability.UNSUPPORTED, MetricAvailability.NOT_PERMITTED)
        ),
    )


def compute_channel_analytics(
    observations: Sequence[MetricObservation],
) -> ChannelAnalytics | None:
    """The whole management view, from a channel's readings.

    Args:
        observations: This channel's readings, newest first. The head is "now";
            everything behind it is available as a baseline. Readings older than
            :data:`ANALYTICS_LOOKBACK_DAYS` are harmless and simply never
            selected, so the caller may bound its query however it likes.

    Returns:
        ``None`` when there is nothing to describe. A channel with no readings
        has no analytics - not analytics full of zeros.
    """
    if not observations:
        return None
    latest = observations[0]
    history = observations[1:]

    followers = latest.value("followers")
    engagements_30d = latest.value("engagements_30d")
    reactions_30d = latest.value("reactions_30d")
    comments_30d = latest.value("comments_30d")
    shares_30d = latest.value("shares_30d")
    posts_count_30d = latest.value("posts_count_30d")
    page_views_7d, page_views_30d = page_views_from(latest.extra_metrics)
    capabilities = capabilities_from(latest.extra_metrics, values=latest.metrics)

    return ChannelAnalytics(
        observed_at=latest.observed_at,
        followers=followers,
        fans=latest.value("fans"),
        following=latest.value("following"),
        posts_count=latest.value("posts_count"),
        engagements_7d=latest.value("engagements_7d"),
        engagements_30d=engagements_30d,
        posts_count_7d=latest.value("posts_count_7d"),
        posts_count_30d=posts_count_30d,
        reactions_30d=reactions_30d,
        likes_30d=latest.value("likes_30d"),
        comments_30d=comments_30d,
        shares_30d=shares_30d,
        video_views_7d=latest.value("video_views_7d"),
        video_views_30d=latest.value("video_views_30d"),
        views_7d=latest.value("views_7d"),
        views_30d=latest.value("views_30d"),
        reach_7d=latest.value("reach_7d"),
        reach_30d=latest.value("reach_30d"),
        impressions_7d=latest.value("impressions_7d"),
        impressions_30d=latest.value("impressions_30d"),
        follower_growth_7d=change_over(history, metric="followers", latest=latest, window_days=7),
        follower_growth_30d=change_over(history, metric="followers", latest=latest, window_days=30),
        engagement_change_7d=change_over(
            history, metric="engagements_7d", latest=latest, window_days=7
        ),
        engagement_change_30d=change_over(
            history, metric="engagements_30d", latest=latest, window_days=30
        ),
        engagement_per_follower_30d=engagement_per_follower(engagements_30d, followers),
        average_engagement_per_post_30d=average_engagement_per_post(
            reactions=reactions_30d,
            comments=comments_30d,
            shares=shares_30d,
            posts=posts_count_30d,
        ),
        page_views_7d=page_views_7d,
        page_views_30d=page_views_30d,
        # Hidden, not shown with a caveat. See :func:`top_post_is_rankable`:
        # when the interaction summaries were refused, "the best post" is the
        # most-shared post wearing a label the data does not support, and the
        # unavailable list says so in words instead.
        top_post_30d=(
            top_post_from(latest.extra_metrics) if capabilities.top_post_rankable else None
        ),
        limitation_note=limitation_note(latest.extra_metrics),
        capabilities=capabilities,
    )


# ---------------------------------------------------------------------------
# Reading untrusted stored JSON
# ---------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _count(value: Any) -> int | None:
    """A non-negative count, or ``None``. Bools are not counts."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _day(extra_metrics: Mapping[str, Any] | None) -> str | None:
    """The settled window's last day as ``YYYY-MM-DD``, or ``None``.

    Validated rather than passed through: this string is rendered on a screen,
    and a connector writing something else there must produce a blank rather
    than whatever it wrote.
    """
    if not extra_metrics:
        return None
    value = extra_metrics.get(WINDOW_30D_END_EXTRA_KEY)
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _moment(value: Any) -> datetime | None:
    """An ISO instant stored by a connector, or ``None`` if it will not parse."""
    if not isinstance(value, str):
        return None
    try:
        return normalize_capture_time(datetime.fromisoformat(value))
    except ValueError:
        return None


__all__: list[str] = [
    "ANALYTICS_LOOKBACK_DAYS",
    "ANALYTICS_WINDOWS",
    "AVAILABILITY_LABELS",
    "AVAILABILITY_NOTES",
    "BASELINE_TOLERANCE_DAYS",
    "FACEBOOK_PAGE_VIEWS_7D_KEY",
    "FACEBOOK_PAGE_VIEWS_30D_KEY",
    "FACEBOOK_TOP_POST_KEY",
    "INSIGHT_METRICS_EXTRA_KEY",
    "POST_FIELDS_EXTRA_KEY",
    "POST_FIELD_AVAILABILITY",
    "PROVIDER_EXTRA_KEY",
    "PROVIDER_LIMITATION_NOTES",
    "PROVIDER_UNSUPPORTED_METRICS",
    "TOP_POST_EXCERPT_LIMIT",
    "TOP_POST_EXTRA_KEYS",
    "UNAVAILABLE_METRIC_LABELS",
    "WINDOW_30D_END_EXTRA_KEY",
    "ChannelAnalytics",
    "MetricAvailability",
    "MetricCapabilities",
    "MetricChange",
    "MetricObservation",
    "TopPost",
    "TrendDirection",
    "UnavailableMetric",
    "availability_of",
    "average_engagement_per_post",
    "baseline_for",
    "capabilities_from",
    "change_over",
    "compute_channel_analytics",
    "direction_of",
    "engagement_per_follower",
    "insight_metrics_from",
    "limitation_note",
    "page_views_from",
    "post_field_states",
    "provider_of",
    "top_post_from",
    "top_post_is_rankable",
]
