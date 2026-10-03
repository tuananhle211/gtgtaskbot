"""Step 1F.2.4d - growth, rates and averages, derived from our own snapshots.

Pure domain tests. **Nothing in this file has a database or a network**, which
is the point: every number a manager reads off the analytics cards is arithmetic
over readings MeoBot already stored, so it has to be checkable without either.

The three rules under test, stated once
----------------------------------------

**Absent is not zero.** Every derivation returns ``None`` the moment an operand
is missing. A channel that has been measured once has no growth; a Page whose
post window could not be read has no average engagement per post. Neither has a
value of zero, and a test asserting ``is None`` rather than ``== 0`` is asserting
the difference a card renders as "—" rather than "0".

**A comparison names what it compared.** Snapshots land whenever a sync ran or
somebody typed one in, so a "30-day" baseline is routinely 27 or 33 days old.
:class:`MetricChange` carries the real age and the panel prints it.

**Near enough, or nothing.** Outside
:data:`~meobot.domain.pr.channel_analytics.BASELINE_TOLERANCE_DAYS` there is no
baseline. A channel connected last Tuesday shows a blank 30-day growth card
rather than a comparison against its own first reading dressed up as a month.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meobot.domain.pr.channel_analytics import (
    ANALYTICS_LOOKBACK_DAYS,
    FACEBOOK_TOP_POST_KEY,
    MetricObservation,
    TrendDirection,
    average_engagement_per_post,
    baseline_for,
    change_over,
    compute_channel_analytics,
    engagement_per_follower,
    top_post_from,
)

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


def reading(
    days_ago: float,
    *,
    extra: dict[str, Any] | None = None,
    **metrics: int | None,
) -> MetricObservation:
    """One reading, ``days_ago`` before :data:`NOW`."""
    return MetricObservation(
        observed_at=NOW - timedelta(days=days_ago),
        metrics=metrics,
        extra_metrics=extra,
    )


# ===========================================================================
# Baseline selection
# ===========================================================================


def test_the_baseline_is_the_reading_nearest_the_target_not_the_newest() -> None:
    """Nearest, because the panel reports how old the winner actually was.

    A sync six hours past the 30-day mark is a better baseline than one four
    days before it, and choosing it is safe precisely because
    ``baseline_age_days`` travels with the answer.
    """
    latest = reading(0, followers=130_000)
    history = [
        reading(2, followers=129_900),
        reading(29.75, followers=124_000),
        reading(33, followers=123_000),
    ]
    chosen = baseline_for(history, metric="followers", latest=latest, window_days=30)
    assert chosen is not None
    assert chosen.value("followers") == 124_000


def test_a_baseline_outside_the_tolerance_is_no_baseline() -> None:
    """Requirement: no comparison rather than a mislabelled one.

    The only reading behind this channel is 20 days old. Comparing it and
    calling the result "30 ngày" would be a lie the screen cannot expose, so the
    30-day growth is simply unknown.
    """
    latest = reading(0, followers=130_000)
    history = [reading(20, followers=120_000)]
    assert baseline_for(history, metric="followers", latest=latest, window_days=30) is None
    assert change_over(history, metric="followers", latest=latest, window_days=30) is None


def test_a_reading_that_left_the_metric_blank_is_skipped_not_treated_as_zero() -> None:
    """A blank is *not recorded*, so it cannot be subtracted from.

    The nearest reading to the 7-day mark has no follower count. Reading it as
    zero would report a gain of 130.000 followers in a week; the honest answer
    is to keep looking and use the next reading that actually carries one.
    """
    latest = reading(0, followers=130_000)
    history = [
        reading(7, followers=None, engagements_30d=10),
        reading(8, followers=128_000),
    ]
    change = change_over(history, metric="followers", latest=latest, window_days=7)
    assert change is not None
    assert change.baseline == 128_000
    assert change.delta == 2_000


def test_a_reading_at_or_after_the_latest_is_never_a_baseline() -> None:
    """Comparing a reading with itself is not a comparison."""
    latest = reading(0, followers=130_000)
    assert baseline_for([latest], metric="followers", latest=latest, window_days=7) is None


def test_ties_are_broken_deterministically() -> None:
    """Two equally close readings must not swap places between two page loads."""
    latest = reading(0, followers=130_000)
    history = [reading(6, followers=129_000), reading(8, followers=128_000)]
    first = baseline_for(history, metric="followers", latest=latest, window_days=7)
    second = baseline_for(list(reversed(history)), metric="followers", latest=latest, window_days=7)
    assert first is not None and second is not None
    assert first.observed_at == second.observed_at


# ===========================================================================
# Growth, rate and direction
# ===========================================================================


def test_follower_growth_reports_the_delta_the_rate_and_the_real_gap() -> None:
    """The whole management card, in one object.

    ``window_days`` is 30 because that is what was asked for. ``baseline_age_days``
    is 28 because that is what was found, and 28 is what the screen prints.
    """
    latest = reading(0, followers=130_000)
    history = [reading(28, followers=124_000)]
    change = change_over(history, metric="followers", latest=latest, window_days=30)

    assert change is not None
    assert change.delta == 6_000
    assert change.delta_pct == pytest.approx(4.8, abs=0.05)
    assert change.direction is TrendDirection.UP
    assert change.window_days == 30
    assert change.baseline_age_days == 28


def test_a_channel_that_lost_followers_says_so() -> None:
    """Negative growth is a real answer and must not be clamped."""
    latest = reading(0, followers=118_000)
    change = change_over(
        [reading(30, followers=124_000)], metric="followers", latest=latest, window_days=30
    )
    assert change is not None
    assert change.delta == -6_000
    assert change.direction is TrendDirection.DOWN
    assert change.delta_pct is not None and change.delta_pct < 0


def test_no_change_is_flat_and_not_growth() -> None:
    """Zero is ``FLAT``. A small Page really does stand still for a week."""
    latest = reading(0, followers=124_000)
    change = change_over(
        [reading(7, followers=124_000)], metric="followers", latest=latest, window_days=7
    )
    assert change is not None
    assert change.delta == 0
    assert change.direction is TrendDirection.FLAT
    assert change.delta_pct == 0


def test_growth_from_zero_has_a_delta_but_no_percentage() -> None:
    """The percentage change from nothing is not a large number - it is not one.

    The absolute delta is still true and is still reported, which is why this
    returns an object rather than ``None``.
    """
    latest = reading(0, followers=500)
    change = change_over(
        [reading(7, followers=0)], metric="followers", latest=latest, window_days=7
    )
    assert change is not None
    assert change.delta == 500
    assert change.delta_pct is None


# ===========================================================================
# Rates and averages
# ===========================================================================


def test_engagement_per_follower_is_a_ratio_and_refuses_a_zero_denominator() -> None:
    """A Page with no audience has no engagement rate.

    Not infinity, not 100%, not zero - dividing by no followers produces no
    number, and the card shows "—".
    """
    assert engagement_per_follower(3_100, 124_000) == pytest.approx(0.025, abs=0.0001)
    assert engagement_per_follower(3_100, 0) is None
    assert engagement_per_follower(3_100, None) is None
    assert engagement_per_follower(None, 124_000) is None


def test_engagement_per_follower_keeps_a_real_zero() -> None:
    """A month with no engagement has a rate of zero, which is a measurement."""
    assert engagement_per_follower(0, 124_000) == 0


def test_average_engagement_per_post_comes_from_the_post_sums() -> None:
    """Reactions + comments + shares, over the posts those counts were taken from.

    Deliberately **not** ``engagements_30d / posts_count_30d``:
    ``page_post_engagements`` counts engagement on posts published months
    earlier too, so dividing it by this month's post count mixes two
    populations and inflates the average for any Page with a back catalogue.
    """
    assert average_engagement_per_post(
        reactions=900, comments=120, shares=30, posts=15
    ) == pytest.approx(70.0)


def test_a_month_with_no_posts_has_no_average() -> None:
    """Not an average of zero. Nothing was divided, because nothing was posted."""
    assert average_engagement_per_post(reactions=0, comments=0, shares=0, posts=0) is None


@pytest.mark.parametrize(
    "missing", [{"reactions": None}, {"comments": None}, {"shares": None}, {"posts": None}]
)
def test_one_missing_term_makes_the_average_unknown(missing: dict[str, Any]) -> None:
    """A sum missing one term is not a smaller sum. It is not a sum."""
    args: dict[str, int | None] = {
        "reactions": 900,
        "comments": 120,
        "shares": 30,
        "posts": 15,
    }
    args.update(missing)
    assert average_engagement_per_post(**args) is None  # type: ignore[arg-type]


# ===========================================================================
# The best post, read back out of stored JSON
# ===========================================================================


def test_the_top_post_is_read_from_extra_metrics() -> None:
    """It is an object, not a metric, which is why it has no column."""
    post = top_post_from(
        {
            FACEBOOK_TOP_POST_KEY: {
                "post_id": "111_222",
                "engagements": 480,
                "permalink_url": "https://www.facebook.com/111_222",
                "created_time": "2026-08-04T02:15:00+00:00",
                "excerpt": "Khai trương chi nhánh mới",
                "reactions": 400,
                "comments": 60,
                "shares": 20,
            }
        }
    )
    assert post is not None
    assert post.post_id == "111_222"
    assert post.engagements == 480
    assert post.created_time is not None and post.created_time.tzinfo is not None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {FACEBOOK_TOP_POST_KEY: "not an object"},
        {FACEBOOK_TOP_POST_KEY: {"engagements": 12}},
        {FACEBOOK_TOP_POST_KEY: {"post_id": "1", "engagements": "many"}},
        {FACEBOOK_TOP_POST_KEY: {"post_id": "1", "engagements": -4}},
    ],
)
def test_unreadable_stored_json_yields_no_post_rather_than_an_exception(
    payload: dict[str, Any] | None,
) -> None:
    """``extra_metrics`` is untrusted input by the time anybody reads it back.

    It is free-form JSON written by one version of a connector and read months
    later by another. A half-built object on a card would be worse than a
    missing one, and an exception on a page load would be worse than both.
    """
    assert top_post_from(payload) is None


def test_an_unparseable_timestamp_does_not_lose_the_post() -> None:
    """Degrading field by field: the post is still the best post."""
    post = top_post_from(
        {FACEBOOK_TOP_POST_KEY: {"post_id": "1", "engagements": 9, "created_time": "yesterday"}}
    )
    assert post is not None
    assert post.created_time is None


# ===========================================================================
# The whole computation
# ===========================================================================


def test_a_channel_with_no_readings_has_no_analytics() -> None:
    """``None``, not an object full of zeros. There is nothing to describe."""
    assert compute_channel_analytics([]) is None


def test_a_channel_with_one_reading_has_counts_but_no_comparisons() -> None:
    """The distinction the API response depends on.

    An object whose derived half is empty says *"we measured this once"*.
    ``None`` would have said *"we have never measured this"*, and the panel
    renders those two very differently - cards with "—" versus an empty state.
    """
    analytics = compute_channel_analytics([reading(0, followers=124_000, engagements_30d=3_100)])
    assert analytics is not None
    assert analytics.followers == 124_000
    assert analytics.follower_growth_7d is None
    assert analytics.follower_growth_30d is None
    assert analytics.engagement_change_30d is None
    # The single-reading derivations still work.
    assert analytics.engagement_per_follower_30d == pytest.approx(0.025, abs=0.0001)


def test_the_full_management_view_is_computed_from_history() -> None:
    """Everything the six primary and six secondary cards need, in one object."""
    observations = [
        reading(
            0,
            followers=130_000,
            fans=134_000,
            engagements_7d=900,
            engagements_30d=3_600,
            posts_count_7d=4,
            posts_count_30d=15,
            reactions_30d=900,
            comments_30d=120,
            shares_30d=30,
            video_views_7d=1_200,
            video_views_30d=5_400,
            extra={
                FACEBOOK_TOP_POST_KEY: {"post_id": "111_222", "engagements": 480},
            },
        ),
        reading(7, followers=129_000, engagements_7d=800),
        reading(30, followers=124_000, engagements_30d=3_000),
    ]
    analytics = compute_channel_analytics(observations)

    assert analytics is not None
    assert analytics.fans == 134_000
    assert analytics.follower_growth_7d is not None
    assert analytics.follower_growth_7d.delta == 1_000
    assert analytics.follower_growth_30d is not None
    assert analytics.follower_growth_30d.delta == 6_000
    assert analytics.engagement_change_7d is not None
    assert analytics.engagement_change_7d.delta == 100
    assert analytics.engagement_change_30d is not None
    assert analytics.engagement_change_30d.delta == 600
    assert analytics.engagement_per_follower_30d == pytest.approx(0.0277, abs=0.0002)
    assert analytics.average_engagement_per_post_30d == pytest.approx(70.0)
    assert analytics.top_post_30d is not None
    assert analytics.top_post_30d.post_id == "111_222"


def test_zero_survives_every_derivation_as_zero() -> None:
    """The whole null-versus-zero rule, asserted where it is easiest to break.

    A Page that posted nothing, got no reactions and gained no followers has
    zeros. A Page nobody could measure has blanks. Both are rendered, and they
    must not render the same.
    """
    analytics = compute_channel_analytics(
        [
            reading(
                0,
                followers=124_000,
                fans=0,
                engagements_30d=0,
                posts_count_30d=0,
                reactions_30d=0,
                comments_30d=0,
                shares_30d=0,
            ),
            reading(30, followers=124_000),
        ]
    )
    assert analytics is not None
    assert analytics.fans == 0
    assert analytics.engagements_30d == 0
    assert analytics.engagement_per_follower_30d == 0
    assert analytics.posts_count_30d == 0
    # And the one that is genuinely undefined stays undefined.
    assert analytics.average_engagement_per_post_30d is None


def test_unmeasured_metrics_stay_none_and_never_become_zero() -> None:
    """The other half of the same rule, on the same shape of reading."""
    analytics = compute_channel_analytics([reading(0, followers=124_000)])
    assert analytics is not None
    for blank in (
        analytics.fans,
        analytics.engagements_7d,
        analytics.engagements_30d,
        analytics.posts_count_30d,
        analytics.reactions_30d,
        analytics.video_views_30d,
        analytics.engagement_per_follower_30d,
        analytics.average_engagement_per_post_30d,
    ):
        assert blank is None


def test_the_lookback_covers_the_widest_window_and_its_tolerance() -> None:
    """The bound the query uses has to be able to reach the furthest baseline.

    If this ever shrank below 37 days the 30-day card would go blank on every
    channel whose monthly reading happened to land a few days late - a silent
    regression with no error anywhere.
    """
    assert ANALYTICS_LOOKBACK_DAYS >= 37
    latest = reading(0, followers=130_000)
    furthest = reading(36, followers=120_000)
    assert change_over([furthest], metric="followers", latest=latest, window_days=30) is not None
