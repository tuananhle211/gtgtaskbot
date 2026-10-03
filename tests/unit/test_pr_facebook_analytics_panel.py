"""Step 1F.2.5 - the typed analytics a Facebook channel panel reads.

**No test in this file contacts Meta.** The domain half needs nothing but
readings; the end-to-end half drives the real provider and the real HTTP API
over an ``httpx.MockTransport``, reusing the CH-0004 Graph fixture from
``test_pr_facebook_post_permissions`` so all three steps model one Graph rather
than three that can drift.

What this file is really testing
---------------------------------

The panel asks eight questions and the server has to answer all of them from one
response. Six are numbers. The last two are the ones this milestone is about:

* **which metrics are blank because Meta stopped serving them**, and
* **which are blank because this connection's grant does not cover them.**

Both are blank columns. Neither is a bug. And they lead to completely different
actions - one is "nothing will ever fix this", the other is "somebody has to
reauthorize" - so a dashboard that renders them identically generates the same
support ticket every month.

The rule the whole file turns on
---------------------------------

**A number that arrived is available, whatever the metadata says.** That is what
makes the capability layer safe to add to a product with a year of history: a
snapshot written before any connector recorded per-field metadata, but carrying
900 reactions, reports its reactions available and keeps its best-post card.
Only a *missing* number ever gets a reason attached, and only when the reading
recorded one.

What is deliberately not here
------------------------------

No new column, no migration, and no OAuth scope. ``page_views_*`` is projected
out of ``extra_metrics`` at read time; the reason reactions are unavailable is
read from what the connector already writes. This milestone adds a way to *say*
what production already knows.
"""

from __future__ import annotations

# The ``world`` fixture and the Graph fake are imported from sibling modules
# rather than rebuilt, for the reason ``test_pr_meta_connector`` gives.
# ruff: noqa: F811
from datetime import timedelta
from typing import Any

import pytest

from meobot.api.schemas.pr import ChannelAnalyticsResponse
from meobot.domain.pr.channel_analytics import (
    FACEBOOK_TOP_POST_KEY,
    ChannelAnalytics,
    MetricAvailability,
    MetricObservation,
    availability_of,
    capabilities_from,
    compute_channel_analytics,
    page_views_from,
    top_post_is_rankable,
)
from tests.unit.test_pr_facebook_metrics_expansion import sync
from tests.unit.test_pr_facebook_post_permissions import production_graph
from tests.unit.test_pr_meta_connector import (  # noqa: F401 - `world` is a fixture
    NOW,
    World,
    _configured,
    world,
)

#: CH-0004's canonical columns on 2026-08-22, exactly as production reports them.
CH_0004: dict[str, int | None] = {
    "followers": 7880,
    "fans": 7880,
    "engagements_7d": 153,
    "engagements_30d": 553,
    "posts_count_7d": 5,
    "posts_count_30d": 14,
    # Not zero. Graph refused the summaries; nobody counted.
    "reactions_30d": None,
    "comments_30d": None,
    # Zero, and measured: the listing was read and nothing had been shared.
    "shares_30d": 0,
    "video_views_7d": 415,
    "video_views_30d": 2479,
    # Retired by Graph v23 and never coming back.
    "reach_7d": None,
    "reach_30d": None,
    "impressions_7d": None,
    "impressions_30d": None,
    "views_7d": None,
    "views_30d": None,
}

#: And its ``extra_metrics``, likewise verbatim.
CH_0004_EXTRA: dict[str, Any] = {
    "meta_provider": "FACEBOOK",
    "facebook_page_views_7d": 2604,
    "facebook_page_views_30d": 11168,
    "facebook_page_fan_count": 7880,
    "facebook_posts_read": 14,
    "facebook_posts_truncated": False,
    "meta_insight_metrics_available": [
        "page_post_engagements",
        "page_video_views",
        "page_views_total",
    ],
    "facebook_post_fields": {
        "shares": "empty",
        "comments": "not_permitted",
        "reactions": "not_permitted",
    },
    "meta_window_30d_end": "2026-08-20",
    # Recorded by the connector, because it still has to pick a post. Whether
    # it may be *called* the best one is this milestone's question.
    FACEBOOK_TOP_POST_KEY: {
        "post_id": "111_9",
        "engagements": 0,
        "permalink_url": "https://www.facebook.com/111_9",
        "reactions": None,
        "comments": None,
        "shares": 0,
    },
}


def reading(
    days_ago: int = 0, *, metrics: dict[str, int | None] | None = None, extra: Any = None
) -> MetricObservation:
    return MetricObservation(
        observed_at=NOW - timedelta(days=days_ago),
        metrics=CH_0004 if metrics is None else metrics,
        extra_metrics=CH_0004_EXTRA if extra is None else extra,
    )


def analytics_for(*observations: MetricObservation) -> ChannelAnalytics:
    result = compute_channel_analytics(list(observations))
    assert result is not None
    return result


# ===========================================================================
# 1-3: THE NUMBERS THE PANEL NEEDS, INCLUDING THE ONE IN extra_metrics
# ===========================================================================


def test_01_page_views_are_projected_out_of_extra_metrics() -> None:
    """Requirement 10. Typed server-side, so no browser learns a JSON key.

    ``page_views_total`` is a real number a PR manager asks for and it is
    deliberately **not** a canonical column: it counts views of the Page's own
    profile, which is not what "Views" means on a YouTube card sitting in the
    same channel list. One column meaning two things is how a dashboard starts
    lying quietly. So it travels under its own name, projected here.
    """
    analytics = analytics_for(reading())
    assert analytics.page_views_7d == 2604
    assert analytics.page_views_30d == 11168
    # And it stays out of the column that means something else.
    assert analytics.views_7d is None
    assert analytics.views_30d is None


@pytest.mark.parametrize(
    "extra",
    [
        None,
        {},
        {"facebook_page_views_30d": "eleven thousand"},
        {"facebook_page_views_30d": -5},
        {"facebook_page_views_30d": True},
        {"facebook_page_views_30d": 1.5},
    ],
)
def test_02_unreadable_page_views_are_blank_rather_than_an_exception(extra: Any) -> None:
    """``extra_metrics`` is untrusted input by the time anybody reads it back.

    Free-form JSON written by one version of a connector and read months later
    by another. A half-parsed number on a card would be worse than a missing
    one, and an exception on a page load would be worse than both. ``True`` is
    in the list because ``isinstance(True, int)`` is the bug this rules out.
    """
    assert page_views_from(extra) == (None, None)


def test_03_the_management_ratio_is_computed_from_the_production_figures() -> None:
    """Requirement 9. 553 / 7.880, and it is MeoBot's ratio rather than Meta's.

    A ratio and not a percentage, so exactly one place - the screen - decides
    how many decimals to show. ``0.0702`` renders as 7,02%.
    """
    analytics = analytics_for(reading())
    assert analytics.engagement_per_follower_30d == pytest.approx(0.0702, abs=0.0001)


# ===========================================================================
# 4-6: WHY A CARD IS BLANK, WHICH IS NOT THE SAME QUESTION AS WHETHER IT IS
# ===========================================================================


def test_04_reach_and_impressions_are_unsupported_rather_than_unmeasured() -> None:
    """Requirement 6. Meta retired them; time will not fix it.

    ``NOT_RECORDED`` would be the wrong word and a worse screen: it invites
    "wait for the next sync", and no sync will ever fill these again. The claim
    is keyed by **provider** rather than inferred from the null, because it is a
    fact about Graph v23 and not about this reading.
    """
    capabilities = analytics_for(reading()).capabilities
    assert capabilities.reach_available is False
    assert capabilities.impressions_available is False

    reasons = {entry.metric: entry for entry in capabilities.unavailable}
    for metric in ("reach_30d", "impressions_30d"):
        assert reasons[metric].availability is MetricAvailability.UNSUPPORTED
        assert reasons[metric].availability_label == "Không còn được Meta cung cấp"
        assert "không còn cung cấp" in reasons[metric].note


def test_05_reactions_and_comments_are_not_permitted_rather_than_zero() -> None:
    """Requirement 7. A different blank, with a different fix.

    Somebody reauthorizing with a wider consent fills these; waiting does not.
    That distinction is the whole capability layer, and it is read from what the
    connector already records rather than guessed from the null.
    """
    capabilities = analytics_for(reading()).capabilities
    assert capabilities.reactions_available is False
    assert capabilities.comments_available is False
    # Read successfully, and there was nothing to count. A measurement.
    assert capabilities.shares_available is True

    reasons = {entry.metric: entry for entry in capabilities.unavailable}
    for metric in ("reactions_30d", "comments_30d"):
        assert reasons[metric].availability is MetricAvailability.NOT_PERMITTED
        assert reasons[metric].availability_label == "Chưa có quyền đọc"
    assert "shares_30d" not in reasons, "0 is an answer, not an absence"


def test_06_nothing_provider_shaped_travels_in_the_explanation() -> None:
    """The rule the whole panel follows, asserted where new prose was added.

    Every sentence is one MeoBot wrote, in Vietnamese, chosen from a fixed set.
    A Graph error string is somebody else's writing about somebody else's system
    and would end up on a Vietnamese screen if it were allowed to travel.
    """
    capabilities = analytics_for(reading()).capabilities
    prose = " ".join(
        f"{entry.label} {entry.availability_label} {entry.note}"
        for entry in capabilities.unavailable
    )
    for leak in ("OAuthException", "#10", "page_impressions", "access_token", "EAA"):
        assert leak not in prose, leak


# ===========================================================================
# 7-9: THE HISTORICAL COMPARISON, AND REFUSING TO INVENT ONE
# ===========================================================================


def test_07_follower_growth_uses_a_baseline_near_the_window_and_names_it() -> None:
    """Requirement 2. "30 ngày" is what was asked for, not what was compared.

    Production's own numbers: 7.888 twenty-nine days ago, 7.880 now. The card
    must print -8 **and** "so với 29 ngày trước", because a card headed 30 over
    a 29-day gap is a small lie nobody can detect from the screen.
    """
    analytics = analytics_for(
        reading(),
        reading(29, metrics={"followers": 7888}, extra=None),
    )
    growth = analytics.follower_growth_30d
    assert growth is not None
    assert growth.delta == -8
    assert growth.direction.value == "DOWN"
    assert growth.baseline_age_days == 29
    assert analytics.follower_growth_rate_30d == pytest.approx(-0.1)


def test_08_no_baseline_near_enough_is_null_and_never_zero() -> None:
    """Requirement 2's other half, and requirement 5's.

    A channel connected last Tuesday has not been flat for a month - nobody
    knows what it did. ``0`` there is a measurement nobody took, and the panel
    renders this ``None`` as "Chưa đủ dữ liệu" rather than as no change.
    """
    analytics = analytics_for(reading(), reading(2, metrics={"followers": 7881}))
    assert analytics.follower_growth_30d is None
    assert analytics.follower_growth_rate_30d is None
    # The 7-day comparison is a different question and still has an answer.
    assert analytics.follower_growth_7d is None, "two days is not seven either"


def test_09_a_single_reading_yields_counts_but_no_comparisons() -> None:
    """Every current number, and nothing derived from a history that is one row."""
    analytics = analytics_for(reading())
    assert analytics.followers == 7880
    assert analytics.engagements_30d == 553
    assert analytics.posts_count_30d == 14
    assert analytics.follower_growth_7d is None
    assert analytics.follower_growth_30d is None
    assert analytics.engagement_change_30d is None


# ===========================================================================
# 10-12: THE BEST POST, WHICH IS A RANKING OR IT IS NOTHING
# ===========================================================================


def test_10_the_best_post_is_withheld_when_its_ranking_is_partial() -> None:
    """Requirement 8. The connector still ranks; the panel refuses the label.

    With the summaries refused, the "best" post is the **most-shared** post
    wearing words the data does not support. A manager quoting that to a client
    would have been misled by MeoBot rather than by Meta, so the card is hidden
    and the absence is accounted for in the same list as every other one.
    """
    analytics = analytics_for(reading())
    assert analytics.capabilities.top_post_rankable is False
    assert analytics.top_post_30d is None

    reasons = {entry.metric: entry for entry in analytics.capabilities.unavailable}
    assert reasons["top_post_30d"].availability is MetricAvailability.NOT_PERMITTED
    assert reasons["top_post_30d"].label == "Bài tốt nhất 30 ngày"


def test_11_the_best_post_returns_the_moment_the_ranking_is_sound() -> None:
    """And nothing has to change for it to. The gate is the data, not a flag."""
    permitted = dict(CH_0004, reactions_30d=400, comments_30d=60)
    extra = dict(
        CH_0004_EXTRA,
        facebook_post_fields={
            "shares": "available",
            "comments": "available",
            "reactions": "available",
        },
    )
    extra[FACEBOOK_TOP_POST_KEY] = {"post_id": "111_1", "engagements": 480}
    analytics = analytics_for(reading(metrics=permitted, extra=extra))

    assert analytics.capabilities.top_post_rankable is True
    assert analytics.top_post_30d is not None
    assert analytics.top_post_30d.post_id == "111_1"
    assert not analytics.capabilities.unavailable or all(
        entry.metric != "top_post_30d" for entry in analytics.capabilities.unavailable
    )


@pytest.mark.parametrize(
    ("reactions", "comments", "rankable"),
    [
        (MetricAvailability.AVAILABLE, MetricAvailability.AVAILABLE, True),
        (MetricAvailability.AVAILABLE, MetricAvailability.NOT_PERMITTED, False),
        (MetricAvailability.NOT_PERMITTED, MetricAvailability.AVAILABLE, False),
        (MetricAvailability.NOT_RECORDED, MetricAvailability.NOT_RECORDED, False),
    ],
)
def test_12_a_ranking_needs_both_halves_of_what_it_ranks_on(
    reactions: MetricAvailability, comments: MetricAvailability, rankable: bool
) -> None:
    """One missing summary is enough. A partial ranking is not a smaller ranking."""
    assert top_post_is_rankable(reactions, comments) is rankable


# ===========================================================================
# 13-15: OLDER READINGS, WHICH HAVE NONE OF THIS METADATA
# ===========================================================================


def test_13_a_reading_from_before_the_expansion_still_produces_analytics() -> None:
    """Requirement 14. Production's 2026-08-21 shape: followers and engagements.

    No ``fans``, no post counts, no video, no page views, no capability
    metadata. Every card the reading cannot fill is blank, nothing is invented,
    and - the part that matters - **no reason is attached to a blank whose cause
    was never recorded**. "Chưa có quyền đọc" over a metric nobody ever asked
    about would be a fabricated diagnosis.
    """
    bare: dict[str, int | None] = {"followers": 7880, "engagements_30d": 553}
    analytics = analytics_for(reading(metrics=bare, extra={"meta_provider": "FACEBOOK"}))

    assert analytics.followers == 7880
    assert analytics.engagements_30d == 553
    assert analytics.fans is None
    assert analytics.posts_count_30d is None
    assert analytics.page_views_30d is None
    assert analytics.capabilities.post_fields == {}
    assert analytics.capabilities.window_30d_end is None

    reasons = {entry.metric: entry.availability for entry in analytics.capabilities.unavailable}
    # Reach is still Meta's decision - that is a fact about the platform and
    # does not depend on this reading having recorded anything.
    assert reasons["reach_30d"] is MetricAvailability.UNSUPPORTED
    # The interaction summaries are not. Nobody said they were refused.
    assert "reactions_30d" not in reasons
    assert "comments_30d" not in reasons


def test_14_a_number_that_arrived_is_available_whatever_the_metadata_says() -> None:
    """The precedence rule, at the unit. Evidence beats absence of evidence.

    A snapshot from before any connector wrote per-field words, carrying 900
    reactions, must not be told its reactions were unavailable. This is what
    keeps a year of history readable.
    """
    assert (
        availability_of("reactions_30d", value=900, provider="FACEBOOK", post_fields={})
        is MetricAvailability.AVAILABLE
    )
    # Even against metadata that disagrees: the number is on the row.
    assert (
        availability_of(
            "reactions_30d",
            value=900,
            provider="FACEBOOK",
            post_fields={"reactions": "not_permitted"},
        )
        is MetricAvailability.AVAILABLE
    )
    # And a manual reading for a platform with no connector explains nothing.
    assert (
        availability_of("reach_30d", value=None, provider=None, post_fields={})
        is MetricAvailability.NOT_RECORDED
    )


def test_15_a_reading_with_no_extra_metrics_at_all_is_safe_to_describe() -> None:
    """A manual reading somebody typed. No provider, no metadata, no crash."""
    capabilities = capabilities_from(None, values={"followers": 100})
    assert capabilities.unavailable == ()
    assert capabilities.post_fields == {}
    assert capabilities.insight_metrics_available == ()
    assert capabilities.top_post_rankable is False


# ===========================================================================
# 16-18: THE WHOLE CHAIN, FROM GRAPH TO THE JSON THE PANEL RECEIVES
# ===========================================================================


def test_16_the_response_carries_every_field_the_panel_reads() -> None:
    """The typed projection, so no client has to interpret raw JSON.

    Requirement 1's list, asserted as a list. A field quietly dropped from the
    response is a card that quietly stops being drawn, and neither the API tests
    nor the panel tests would necessarily notice on their own.
    """
    payload = ChannelAnalyticsResponse.from_domain(
        analytics_for(reading(), reading(29, metrics={"followers": 7888}))
    ).model_dump()

    assert payload["followers"] == 7880
    assert payload["fans"] == 7880
    assert payload["engagements_7d"] == 153
    assert payload["engagements_30d"] == 553
    assert payload["posts_count_7d"] == 5
    assert payload["posts_count_30d"] == 14
    assert payload["video_views_7d"] == 415
    assert payload["video_views_30d"] == 2479
    assert payload["page_views_7d"] == 2604
    assert payload["page_views_30d"] == 11168
    assert payload["shares_30d"] == 0
    assert payload["reactions_30d"] is None
    assert payload["comments_30d"] is None
    assert payload["follower_growth_rate_30d"] == pytest.approx(-0.1)
    assert payload["engagement_per_follower_30d"] == pytest.approx(0.0702, abs=0.0001)
    assert payload["capabilities"]["window_30d_end"] == "2026-08-20"
    assert payload["capabilities"]["insight_metrics_available"] == [
        "page_post_engagements",
        "page_video_views",
        "page_views_total",
    ]
    assert payload["capabilities"]["post_fields"]["reactions"] == "not_permitted"


async def test_17_a_real_sync_produces_a_panel_response_over_http(world: World) -> None:
    """Graph refuses the summaries, the sync succeeds, the panel gets its JSON.

    The whole chain in one test, because the interesting failures live between
    the layers: a connector that records a word the domain does not understand,
    or a projection that drops a field, passes every test on either side of it.
    """
    graph = production_graph()
    row, result = await sync(world, graph)
    assert result.ok is True

    world.act_as(world.owner)
    response = world.client.get(f"/api/pr/channels/{row.id}/metrics")
    assert response.status_code == 200, response.text
    analytics = response.json()["analytics"]

    assert analytics["posts_count_30d"] == 6
    assert analytics["reactions_30d"] is None
    assert analytics["comments_30d"] is None
    assert analytics["shares_30d"] == 27
    assert analytics["page_views_7d"] == 35
    assert analytics["page_views_30d"] == 35

    capabilities = analytics["capabilities"]
    assert capabilities["reactions_available"] is False
    assert capabilities["comments_available"] is False
    assert capabilities["shares_available"] is True
    assert capabilities["top_post_rankable"] is False
    assert analytics["top_post_30d"] is None

    reasons = {entry["metric"]: entry["availability"] for entry in capabilities["unavailable"]}
    assert reasons["reactions_30d"] == "NOT_PERMITTED"
    assert reasons["comments_30d"] == "NOT_PERMITTED"
    assert reasons["reach_30d"] == "UNSUPPORTED"


async def test_18_no_secret_reaches_the_panel_response(world: World) -> None:
    """The containment rule, re-asserted over the fields this step added.

    ``capabilities`` is the newest thing on this response and it is built from
    ``extra_metrics``, which is where a connector writes. A test that only
    checked the numbers would not have noticed a token arriving beside them.
    """
    graph = production_graph()
    row, _ = await sync(world, graph)
    world.act_as(world.owner)
    body = world.client.get(f"/api/pr/channels/{row.id}/metrics").text

    for leak in ("EAApage-a-token", "app-secret", "EAAuser-long-lived"):
        assert leak not in body, leak
