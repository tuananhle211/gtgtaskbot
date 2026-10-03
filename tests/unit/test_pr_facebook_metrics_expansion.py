"""Step 1F.2.4d - the Facebook metrics expansion, end to end.

**No test in this file contacts Meta.** Every one drives the real provider, the
real probe and the real services through an ``httpx.MockTransport``, reusing the
``FakeGraph`` next door so that the two steps' behaviour is asserted against one
model of Graph rather than two that can drift.

What this file is really testing
---------------------------------

Step 1F.2.4c ended with a Facebook Page reporting three numbers - followers,
fans and engagements - and four permanently blank cards where Graph v23 had
retired the metrics behind them. This step adds what a Page *can* still answer
and refuses, again, to invent what it cannot.

So the assertions divide in two, and the second half is the important one:

**What was added.** Page likes in their own column, posts published in the
window, reactions/comments/shares from the posts themselves, video plays when
this Graph version serves them, and the month's best post.

**What is still refused.** No reach. No impressions. No count derived from a
truncated fetch. No zero standing in for a metric nobody could read. No
per-post request, and therefore no rate-limit spiral. And - the rule the whole
step is built on - **no optional metric can fail a channel's sync**, while every
credential, permission, quota and outage failure still fails it loudly.
"""

from __future__ import annotations

# The ``world`` fixture and the Graph fake are imported from sibling modules
# rather than rebuilt, for the reason ``test_pr_meta_connector`` gives.
# ruff: noqa: F811
import uuid
from datetime import timedelta
from typing import Any

import pytest

from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.domain.pr.channel_analytics import FACEBOOK_TOP_POST_KEY
from meobot.domain.pr.channel_connections import (
    PrChannelSyncErrorCode,
    PrChannelSyncTrigger,
)
from meobot.domain.pr.channel_metrics import MANUAL_METRIC_FIELDS, PrChannelPlatform
from meobot.integrations.meta.constants import MAX_POST_PAGES, POST_PAGE_SIZE
from meobot.integrations.meta.errors import MetaApiError
from meobot.integrations.meta.posts import PostWindow, parse_post, posts_within, summarize
from meobot.integrations.meta.probe import (
    PAGE_INSIGHT_CANDIDATES,
    MetaCapabilityProbe,
    ProbeVerdict,
)
from meobot.integrations.meta.provider import (
    FACEBOOK_DAILY_METRICS,
    FACEBOOK_OPTIONAL_DAILY_METRICS,
)
from tests.unit.test_pr_channel_metrics import describe
from tests.unit.test_pr_channel_metrics import record as record_manual
from tests.unit.test_pr_meta_connector import (  # noqa: F401 - `world` is a fixture
    NOW,
    PAGE_A,
    PAGE_A_TOKEN,
    FakeGraph,
    World,
    _configured,
    authorize_and_finish,
    fb_channel,
    page_row,
    post_row,
    world,
)


#: A month of posts, newest first, the way Graph orders ``published_posts``.
#:
#: Three of them fall inside the settled 7-day window and three do not, so a
#: single fixture exercises both the monthly totals and the weekly slice that is
#: filtered out of them rather than fetched again.
def month_of_posts() -> list[dict[str, Any]]:
    end = NOW - timedelta(days=2)
    return [
        post_row("111_1", created=end - timedelta(days=1), reactions=400, comments=60, shares=20),
        post_row("111_2", created=end - timedelta(days=3), reactions=100, comments=10),
        post_row("111_3", created=end - timedelta(days=5), reactions=50, comments=5, shares=1),
        post_row("111_4", created=end - timedelta(days=12), reactions=200, comments=30, shares=4),
        post_row("111_5", created=end - timedelta(days=20), reactions=80, comments=8),
        post_row("111_6", created=end - timedelta(days=27), reactions=10, comments=0, shares=2),
    ]


def graph_with_posts(**overrides: Any) -> FakeGraph:
    """A Page that answers every metric this step maps."""
    values: dict[str, Any] = {
        "pages": [page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)],
        "posts": month_of_posts(),
        "daily": {
            "page_post_engagements": [10] * 7,
            "page_video_views": [100] * 7,
            "page_views_total": [5] * 7,
        },
    }
    values.update(overrides)
    return FakeGraph(**values)


async def sync(world: World, graph: FakeGraph):
    """Connect a Facebook channel over this Graph and run one sync."""
    row = await fb_channel(world)
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    result = await world.services.channel_sync.run_sync(
        connection_id=outcome.connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    return row, result


# ===========================================================================
# 1-6: THE PAGE METRICS THAT WORK
# ===========================================================================


async def test_01_page_likes_land_in_their_own_column_beside_followers() -> None:
    """Requirement 1. ``fan_count`` is a metric now, not a footnote.

    Step 1F.2.4c kept it in ``extra_metrics`` because there was no column for
    it. There is one now, and both numbers are stored, because a fan liked the
    Page and a follower receives its posts and every report quotes one or the
    other.
    """
    graph = graph_with_posts()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()
    assert metrics["followers"] == 124812
    assert metrics["fans"] == 130500
    assert metrics["followers"] != metrics["fans"]
    # And the old location still carries it, so a March snapshot and an August
    # one can be read by the same code.
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["facebook_page_fan_count"] == 130500


async def test_02_engagements_still_map_from_the_confirmed_metric() -> None:
    """Requirement 2. The one metric production depends on is untouched."""
    graph = graph_with_posts()
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["engagements_7d"] == 70
    assert metrics["engagements_30d"] == 70


async def test_03_video_plays_map_when_this_graph_version_serves_them() -> None:
    """Requirement 3. Additive, so summing the daily series is correct."""
    graph = graph_with_posts()
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["video_views_7d"] == 700
    assert metrics["video_views_30d"] == 700


async def test_04_profile_views_are_recorded_but_never_called_views() -> None:
    """Requirement 4, and the refusal underneath it.

    ``page_views_total`` counts views of the **Page's profile**. Writing it into
    ``views_30d`` would make a Facebook card and a YouTube card in the same
    channel list mean two different things under one heading, so it goes where
    platform-specific numbers go and no summary card reads it.
    """
    graph = graph_with_posts()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    assert reading.canonical()["views_30d"] is None
    assert reading.canonical()["views_7d"] is None
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["facebook_page_views_30d"] == 35


async def test_05_reach_and_impressions_are_still_refused() -> None:
    """Requirement 5. Graph v23 retired them and nothing replaced them.

    The expansion adds what a Page can answer. It does **not** reach for
    something adjacent and call it reach: summing daily uniques would count one
    person on three days as three people, which is worse than a blank card
    because it is plausible.
    """
    graph = graph_with_posts()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()
    for refused in ("reach_7d", "reach_30d", "impressions_7d", "impressions_30d"):
        assert metrics[refused] is None, refused
    assert "page_impressions" not in graph.sent()
    assert "page_impressions_unique" not in graph.sent()


async def test_06_the_optional_metrics_travel_in_their_own_request() -> None:
    """Requirement 6, and the lesson of the outage this step inherited.

    A metric Meta may retire next quarter must not share a ``metric=`` list with
    the one production depends on, because Graph rejects the whole list over one
    unknown name. Two tuples, two requests.
    """
    assert FACEBOOK_DAILY_METRICS == ("page_post_engagements",)
    assert set(FACEBOOK_DAILY_METRICS).isdisjoint(FACEBOOK_OPTIONAL_DAILY_METRICS)

    graph = graph_with_posts()
    await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    insight_urls = [str(r.url) for r in graph.requests if "/insights" in str(r.url)]
    for url in insight_urls:
        confirmed = "page_post_engagements" in url
        optional = any(name in url for name in FACEBOOK_OPTIONAL_DAILY_METRICS)
        assert not (confirmed and optional), url


# ===========================================================================
# 7-12: THE METRICS META WILL NOT ANSWER
# ===========================================================================


async def test_07_a_retired_optional_metric_costs_nothing_but_itself() -> None:
    """Requirement 7. The confirmed metric survives its neighbour's retirement.

    This is the production incident, re-run against the metrics this step added:
    ``page_video_views`` is retired in this Graph, and followers, fans and
    engagements must all still arrive.
    """
    graph = graph_with_posts(retired_metrics=frozenset({"page_video_views"}))
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()

    assert metrics["video_views_30d"] is None, "the retired one is unavailable"
    assert metrics["engagements_30d"] == 70, "its neighbour survived"
    assert metrics["followers"] == 124812
    assert metrics["fans"] == 130500
    assert reading.extra_metrics is not None
    assert "page_video_views" not in reading.extra_metrics["meta_insight_metrics_available"]


async def test_08_a_page_whose_optional_metrics_are_all_retired_still_syncs(
    world: World,
) -> None:
    """Requirement 8, end to end. The snapshot is written regardless."""
    graph = graph_with_posts(retired_metrics=frozenset({"page_video_views", "page_views_total"}))
    _, result = await sync(world, graph)

    assert result.ok is True
    assert result.error_code is None
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.followers == 124812
    assert snapshot.fans == 130500
    assert snapshot.engagements_30d == 70
    assert snapshot.video_views_30d is None


async def test_09_the_weekly_request_asks_only_for_what_answered() -> None:
    """Requirement 9. A retired name is asked for once per sync, not twice.

    Cheap, and the reason it matters is that the one-at-a-time retry a rejection
    triggers is the expensive part. Doing it for both windows would double the
    cost of the day Meta deprecates something.
    """
    graph = graph_with_posts(retired_metrics=frozenset({"page_video_views"}))
    await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    asked = [str(r.url) for r in graph.requests if "page_video_views" in str(r.url)]
    # Once in the 30-day group, once in the one-at-a-time retry that identifies
    # it - and never again for the 7-day window.
    assert len(asked) == 2, asked


async def test_10_a_page_with_no_insights_at_all_still_records_its_counts(
    world: World,
) -> None:
    """Requirement 10. Followers and fans are worth a snapshot on their own."""
    graph = graph_with_posts(
        retired_metrics=frozenset({"page_post_engagements", "page_video_views", "page_views_total"})
    )
    _, result = await sync(world, graph)
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)

    assert result.ok is True
    assert snapshot is not None
    assert snapshot.followers == 124812 and snapshot.fans == 130500
    for empty in ("engagements_7d", "engagements_30d", "video_views_30d"):
        assert getattr(snapshot, empty) is None, empty
    assert snapshot.extra_metrics is not None
    assert snapshot.extra_metrics["meta_insight_metrics_available"] == []


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"error": {"code": 190, "error_subcode": 458}}, PrChannelSyncErrorCode.AUTH_REQUIRED),
        (403, {"error": {"code": 10}}, PrChannelSyncErrorCode.INSUFFICIENT_SCOPE),
        (400, {"error": {"code": 4}}, PrChannelSyncErrorCode.RATE_LIMITED),
        (500, {"error": {"code": 2}}, PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
        (
            404,
            {"error": {"code": 100, "error_subcode": 33}},
            PrChannelSyncErrorCode.INVALID_ACCOUNT,
        ),
    ],
)
async def test_11_degradation_never_swallows_a_real_failure_on_the_post_edge(
    status: int, payload: dict[str, Any], expected: PrChannelSyncErrorCode
) -> None:
    """Requirement 11, the resilience rule, asserted where it is newest.

    The post listing degrades on a **malformed request** and on nothing else. A
    revoked token, a withdrawn consent, a quota limit, a Meta outage and a wrong
    Page binding all still fail the sync loudly - swallowing any of them would
    write a snapshot of nulls and record it as a success, which is worse than
    failing because it is silent.

    The failure is injected on the post edge alone, leaving identity, profile
    and insights healthy: otherwise the request would die earlier and this would
    prove nothing.
    """
    graph = graph_with_posts(posts_status=status, posts_error=payload)
    with pytest.raises(MetaApiError) as failure:
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    assert failure.value.error_code is expected


async def test_12_an_unreadable_post_listing_does_not_fail_the_sync(world: World) -> None:
    """Requirement 12. The other half of requirement 11.

    Graph answered the post edge with something that is not a list - the
    malformed-request case. The four post-derived columns go ``NULL``, every
    Page-level number is still recorded, and the sync succeeds.
    """
    graph = graph_with_posts(
        posts_status=400,
        posts_error={"error": {"message": "(#100) unsupported get request", "code": 100}},
    )
    _, result = await sync(world, graph)
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)

    assert result.ok is True, "an unreadable post listing is not a failed sync"
    assert snapshot is not None
    assert snapshot.followers == 124812
    assert snapshot.engagements_30d == 70
    for blank in ("posts_count_30d", "reactions_30d", "comments_30d", "shares_30d"):
        assert getattr(snapshot, blank) is None, blank
    assert snapshot.extra_metrics is not None
    assert snapshot.extra_metrics["facebook_posts_truncated"] is None


# ===========================================================================
# 13-20: THE POST WINDOW
# ===========================================================================


async def test_13_posts_are_counted_for_both_windows_from_one_fetch() -> None:
    """Requirement 13. The weekly figure is a filter, not a second request."""
    graph = graph_with_posts()
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["posts_count_30d"] == 6
    assert metrics["posts_count_7d"] == 3

    post_requests = [r for r in graph.requests if "published_posts" in str(r.url)]
    assert len(post_requests) == 1, "one page held them all, and the week cost nothing"


async def test_14_reactions_comments_and_shares_come_from_the_posts() -> None:
    """Requirement 14. And reactions are not filed as likes.

    A Facebook reaction may be a like, a love, a haha, a wow, a sad or an angry.
    The total belongs in ``reactions_30d``; ``likes_30d`` stays empty rather than
    carrying a number that is not a like count.
    """
    graph = graph_with_posts()
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["reactions_30d"] == 840
    assert metrics["comments_30d"] == 113
    # 20 + 1 + 4 + 2; the two posts Graph omitted ``shares`` for really had none.
    assert metrics["shares_30d"] == 27
    assert metrics["likes_30d"] is None


async def test_15_the_best_post_is_recorded_with_enough_to_act_on() -> None:
    """Requirement 15. An object, in ``extra_metrics``, not a column."""
    graph = graph_with_posts()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    assert reading.extra_metrics is not None
    top = reading.extra_metrics[FACEBOOK_TOP_POST_KEY]
    assert top["post_id"] == "111_1"
    assert top["engagements"] == 480
    assert top["permalink_url"] == "https://www.facebook.com/111_1"
    assert top["reactions"] == 400 and top["comments"] == 60 and top["shares"] == 20


async def test_16_the_post_walk_is_bounded_and_says_when_it_stopped() -> None:
    """Requirement 16, and the reason the cap exists at all.

    A Page's feed has no end and a sync must have one. When the cap is reached
    the window is a **prefix**, so every count over it goes ``NULL`` rather than
    a floor that would read on a card as a total - a truncated count is not a
    smaller count, it is a different number wearing the right name.
    """
    end = NOW - timedelta(days=2)
    many = [
        post_row(f"111_{index}", created=end - timedelta(hours=index), reactions=1, comments=0)
        for index in range(MAX_POST_PAGES * POST_PAGE_SIZE + 10)
    ]
    graph = graph_with_posts(posts=many)
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()

    requests = [r for r in graph.requests if "published_posts" in str(r.url)]
    assert len(requests) == MAX_POST_PAGES, "bounded, whatever the Page does"
    assert metrics["posts_count_30d"] is None
    assert metrics["reactions_30d"] is None
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["facebook_posts_truncated"] is True
    assert reading.extra_metrics["facebook_posts_read"] == MAX_POST_PAGES * POST_PAGE_SIZE


async def test_17_a_truncated_month_can_still_have_a_complete_week() -> None:
    """Requirement 17. Truncation cuts the **old** end, so check rather than assume.

    ``published_posts`` is newest first. If the cut fell earlier than seven days
    back, the weekly slice is whole and blanking it would throw away a good card
    for no reason. If the cut fell *inside* the week, it is not - and that is the
    case the check exists for.
    """
    end = NOW - timedelta(days=2)
    # Every fetched post is older than the 7-day window, so the cut is too.
    old = [
        post_row(f"111_{i}", created=end - timedelta(days=10, hours=i), reactions=1, comments=0)
        for i in range(MAX_POST_PAGES * POST_PAGE_SIZE + 5)
    ]
    graph = graph_with_posts(posts=old)
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["posts_count_30d"] is None, "the month is a prefix"
    assert metrics["posts_count_7d"] == 0, "the week is whole, and really had none"


async def test_18_a_week_inside_the_cut_is_unknown_not_zero() -> None:
    """Requirement 18. The other side of the same check."""
    end = NOW - timedelta(days=2)
    busy = [
        post_row(f"111_{i}", created=end - timedelta(hours=i), reactions=1, comments=0)
        for i in range(MAX_POST_PAGES * POST_PAGE_SIZE + 5)
    ]
    window = PostWindow(
        posts=tuple(p for p in (parse_post(row) for row in busy[:20]) if p is not None),
        truncated=True,
    )
    weekly = posts_within(window, start=end - timedelta(days=6), end=end + timedelta(days=1))
    assert weekly.truncated is True
    assert summarize(weekly).posts_count is None


async def test_19_a_post_graph_declined_to_summarise_makes_the_total_unknown() -> None:
    """Requirement 19. A sum missing one term is not a sum.

    One post's reaction summary is absent. ``reactions_30d`` goes ``NULL``,
    because a total that silently skipped a post would be a smaller number with
    no way to tell - while ``comments_30d``, which is complete, is still stored.
    """
    end = NOW - timedelta(days=2)
    graph = graph_with_posts(
        posts=[
            post_row("111_1", created=end - timedelta(days=1), reactions=None, comments=60),
            post_row("111_2", created=end - timedelta(days=3), reactions=100, comments=10),
        ]
    )
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["reactions_30d"] is None
    assert metrics["comments_30d"] == 70
    assert metrics["posts_count_30d"] == 2, "the posts were still counted"


async def test_20_a_month_with_no_posts_is_zero_and_not_blank() -> None:
    """Requirement 20. The null-versus-zero rule at the post edge.

    Graph listed the window and it was empty. That is a measurement: this Page
    published nothing. It is not the same as a listing MeoBot could not read.
    """
    graph = graph_with_posts(posts=[])
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["posts_count_30d"] == 0
    assert metrics["reactions_30d"] == 0
    assert metrics["shares_30d"] == 0


async def test_20a_cursor_pagination_never_follows_a_url_from_a_response() -> None:
    """The SSRF rule, still held on a new edge.

    Graph puts a fully-formed URL in ``paging.next``. Following it would mean
    contacting an address that arrived in a response, so the cursor is extracted
    and the URL is rebuilt from the compiled endpoints - which is what makes
    every request here still go to the host this client was constructed with.
    """
    end = NOW - timedelta(days=2)
    graph = graph_with_posts(
        posts=[
            post_row(f"111_{i}", created=end - timedelta(hours=i), reactions=1, comments=0)
            for i in range(30)
        ],
        posts_page_size=10,
    )
    metrics = (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    ).canonical()
    assert metrics["posts_count_30d"] == 30
    for request in graph.requests:
        assert str(request.url).startswith("https://graph.facebook.com/v23.0/")


# ===========================================================================
# 21-26: WHAT THE PANEL COMPUTES FROM STORED SNAPSHOTS
# ===========================================================================


async def test_21_the_panel_reports_growth_from_readings_it_stored(world: World) -> None:
    """Requirement 21. Two readings a month apart, and the delta between them.

    Nothing was asked of Meta for this. Meta is not reliably able to answer
    "how many followers did this Page gain in 30 days", and MeoBot does not need
    it to: it wrote both numbers down itself.
    """
    row = await fb_channel(world)
    await record_manual(world, row.id, at=NOW - timedelta(days=30), followers=124_000)
    await record_manual(world, row.id, at=NOW, followers=130_000, engagements_30d=3_100)

    view = await describe(world, row.id)
    assert view.analytics is not None
    assert view.analytics.follower_growth_30d is not None
    assert view.analytics.follower_growth_30d.delta == 6_000
    assert view.analytics.follower_growth_30d.baseline_age_days == 30


async def test_22_the_engagement_rate_is_computed_server_side(world: World) -> None:
    """Requirement 22. The browser does no arithmetic on a metric."""
    row = await fb_channel(world)
    await record_manual(world, row.id, at=NOW, followers=124_000, engagements_30d=3_100)

    view = await describe(world, row.id)
    assert view.analytics is not None
    assert view.analytics.engagement_per_follower_30d == pytest.approx(0.025, abs=0.0001)


async def test_23_average_engagement_per_post_uses_the_post_sums(world: World) -> None:
    """Requirement 23, and the division it refuses to do.

    ``engagements_30d`` is a **page-level** figure counting engagement on posts
    published months ago too. Dividing it by this month's post count would
    inflate the average for any Page with a back catalogue, so the average is
    computed from the three post-level sums, which were taken over exactly the
    posts being counted.
    """
    row = await fb_channel(world)
    await record_manual(
        world,
        row.id,
        at=NOW,
        followers=124_000,
        # Deliberately large, and deliberately not what the average uses.
        engagements_30d=99_000,
        posts_count_30d=15,
        reactions_30d=900,
        comments_30d=120,
        shares_30d=30,
    )
    view = await describe(world, row.id)
    assert view.analytics is not None
    assert view.analytics.average_engagement_per_post_30d == pytest.approx(70.0)


async def test_24_a_channel_measured_once_has_cards_but_no_comparisons(
    world: World,
) -> None:
    """Requirement 24. An object with blanks, not a missing object.

    The panel renders "—" in the growth cards. It does not render its empty
    state, because the channel has been measured.
    """
    row = await fb_channel(world)
    await record_manual(world, row.id, at=NOW, followers=124_000)

    view = await describe(world, row.id)
    assert view.analytics is not None
    assert view.analytics.followers == 124_000
    assert view.analytics.follower_growth_30d is None


async def test_25_a_channel_never_measured_has_no_analytics_at_all(world: World) -> None:
    """Requirement 25. ``None``, which the panel renders as its empty state."""
    row = await fb_channel(world)
    view = await describe(world, row.id)
    assert view.analytics is None


async def test_26_a_sync_fills_the_analytics_the_panel_reads(world: World) -> None:
    """Requirement 26, end to end: fetch, store, derive, in one flow."""
    graph = graph_with_posts()
    row, result = await sync(world, graph)
    assert result.ok is True

    view = await describe(world, row.id)
    assert view.analytics is not None
    assert view.analytics.followers == 124812
    assert view.analytics.fans == 130500
    assert view.analytics.posts_count_30d == 6
    assert view.analytics.reactions_30d == 840
    assert view.analytics.average_engagement_per_post_30d == pytest.approx(163.3, abs=0.1)
    assert view.analytics.top_post_30d is not None
    assert view.analytics.top_post_30d.post_id == "111_1"


# ===========================================================================
# 27-32: THE CAPABILITY PROBE
# ===========================================================================


async def test_27_the_probe_reports_each_metric_separately() -> None:
    """Requirement 27. One request per candidate, so one refusal means one thing.

    Batching is what the connector does to be cheap. Batching here would
    reproduce the exact failure being investigated - five names refused because
    one was unknown - and leave the operator no wiser.
    """
    graph = graph_with_posts(retired_metrics=frozenset({"page_impressions", "page_views_total"}))
    report = await MetaCapabilityProbe(graph.client()).probe_page(
        access_token=PAGE_A_TOKEN, now=NOW
    )
    verdicts = {result.metric: result.verdict for result in report.insights}

    assert verdicts["page_post_engagements"] is ProbeVerdict.AVAILABLE
    assert verdicts["page_video_views"] is ProbeVerdict.AVAILABLE
    assert verdicts["page_impressions"] is ProbeVerdict.UNSUPPORTED
    assert verdicts["page_views_total"] is ProbeVerdict.UNSUPPORTED
    # One request per insight candidate, never a group.
    insight_requests = [str(r.url) for r in graph.requests if "/insights" in str(r.url)]
    assert len(insight_requests) == len(PAGE_INSIGHT_CANDIDATES)
    for url in insight_requests:
        assert "%2C" not in url and "," not in url.split("metric=")[1].split("&")[0]


async def test_28_the_probe_separates_retired_from_not_permitted() -> None:
    """Requirement 28. The two need opposite responses.

    "Graph rejected the name" means stop asking. "Graph rejected the caller"
    means reconnect with wider consent. Collapsing them into one word would send
    somebody to delete a working mapping over a missing permission.
    """
    graph = graph_with_posts(insights_status=403, insights_error={"error": {"code": 10}})
    report = await MetaCapabilityProbe(graph.client()).probe_page(
        access_token=PAGE_A_TOKEN, now=NOW
    )
    assert {result.verdict for result in report.insights} == {ProbeVerdict.NOT_PERMITTED}


async def test_29_a_metric_that_answers_with_nothing_is_empty_not_unsupported() -> None:
    """Requirement 29. A Page with no videos is not a Graph without the metric.

    The difference decides whether to map a metric or leave it alone, so the
    probe keeps them apart even though both leave the card blank today.
    """
    graph = graph_with_posts(daily={"page_post_engagements": [10]})
    report = await MetaCapabilityProbe(graph.client()).probe_page(
        access_token=PAGE_A_TOKEN, now=NOW
    )
    verdicts = {result.metric: result.verdict for result in report.insights}
    assert verdicts["page_post_engagements"] is ProbeVerdict.AVAILABLE
    assert verdicts["page_video_views"] is ProbeVerdict.EMPTY


async def test_30_the_probe_never_reveals_the_token() -> None:
    """Requirement 30, and the one this whole tool is judged on.

    The rendered report, the JSON payload and every result field are checked -
    an operator's terminal, a pasted bug report and a piped file are the three
    places a credential most easily escapes to.
    """
    from meobot.cli.meta_probe import _as_payload, render

    graph = graph_with_posts()
    report = await MetaCapabilityProbe(graph.client()).probe_page(
        access_token=PAGE_A_TOKEN, now=NOW
    )
    rendered = render("APEX-FB", report)
    payload = repr(_as_payload("APEX-FB", report))

    assert PAGE_A_TOKEN not in rendered
    assert PAGE_A_TOKEN not in payload
    assert "Authorization" not in rendered
    # And it says something useful.
    assert "page_post_engagements" in rendered
    assert report.page_id == PAGE_A


async def test_31_the_probe_writes_nothing(world: World) -> None:
    """Requirement 31. A probe that failed must not look like a sync that failed."""
    graph = graph_with_posts(insights_status=403, insights_error={"error": {"code": 10}})
    row = await fb_channel(world)
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    before = outcome.connection.sync_status

    await MetaCapabilityProbe(graph.client()).probe_page(access_token=PAGE_A_TOKEN, now=NOW)

    await world.session.refresh(outcome.connection)
    assert outcome.connection.sync_status == before
    assert outcome.connection.consecutive_failures == 0
    snapshots = await world.services.channel_metrics.has_history(row.id)
    assert snapshots is False


async def test_32_the_probe_invents_no_replacement_for_a_retired_metric() -> None:
    """Requirement 32. Every candidate is a metric Meta documents.

    A tool that tried variations of a retired name would eventually get a 200
    from something with a similar name and a different definition, and somebody
    would map it. The candidate list is fixed, and this is the guard.
    """
    for candidate in PAGE_INSIGHT_CANDIDATES:
        assert candidate.startswith("page_"), candidate
        assert "_v2" not in candidate and "_new" not in candidate, candidate


# ===========================================================================
# The vocabulary itself
# ===========================================================================


def test_the_new_metrics_are_offered_by_the_manual_form_too() -> None:
    """A column MeoBot can store but cannot be told is a column only Meta fills.

    Half the department's channels are on platforms with no connector, and they
    are measured by somebody reading numbers off a screen. Every canonical
    metric is therefore a field on the manual form.
    """
    for added in (
        "fans",
        "posts_count_7d",
        "posts_count_30d",
        "reactions_30d",
        "video_views_7d",
        "video_views_30d",
    ):
        assert added in MANUAL_METRIC_FIELDS, added


def test_the_snapshot_columns_and_the_vocabulary_are_the_same_list() -> None:
    """Every canonical metric has a column, and every column is canonical.

    A metric in the vocabulary with no column would be silently dropped on the
    way in; a column outside it would be unreachable from the form, the
    response and the fingerprint.
    """
    columns = set(PrChannelMetricSnapshot.__table__.columns.keys())
    assert set(MANUAL_METRIC_FIELDS) <= columns, set(MANUAL_METRIC_FIELDS) - columns


def test_a_reading_is_still_deduplicated_over_every_metric() -> None:
    """The fingerprint covers the new columns, so a changed one is a new reading.

    Step 1F.2.4b's idempotency key is computed over the whole canonical
    vocabulary. Adding six metrics without them entering the fingerprint would
    have meant a re-fetch that found new reactions being swallowed as a
    duplicate.
    """
    from meobot.domain.pr.channel_connections import reading_fingerprint

    base = dict.fromkeys(MANUAL_METRIC_FIELDS, None)
    first = reading_fingerprint(
        provider_account_id=PAGE_A,
        period_start="2026-07-20",
        period_end="2026-08-18",
        metrics={**base, "reactions_30d": 840},
    )
    second = reading_fingerprint(
        provider_account_id=PAGE_A,
        period_start="2026-07-20",
        period_end="2026-08-18",
        metrics={**base, "reactions_30d": 900},
    )
    assert first != second


async def test_a_second_identical_fetch_is_still_a_duplicate(world: World) -> None:
    """And the deduplication still works, with six more columns in the key."""
    graph = graph_with_posts()
    row, first = await sync(world, graph)
    assert first.ok is True and first.duplicate is False

    connection = await world.services.channel_connections.get_live_connection(
        row.id, provider=PrChannelPlatform.FACEBOOK
    )
    assert connection is not None
    second = await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=uuid.uuid4(),
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    assert second.ok is True
    assert second.duplicate is True
    assert second.snapshot_id is None
