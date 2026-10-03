"""The CH-0004 regression: two optional counts must not fail a channel's sync.

**No test in this file contacts Meta.** Every one drives the real provider and
the real services through an ``httpx.MockTransport``, reusing the ``FakeGraph``
two files over so that all three steps' behaviour is asserted against one model
of Graph rather than three that can drift.

What happened
--------------

A Facebook Page connection holding ``pages_show_list``, ``pages_read_engagement``
and ``read_insights`` synced perfectly for weeks and then failed every attempt
with ``INSUFFICIENT_SCOPE``. A capability probe against the live Page found no
missing Page data at all - ``followers_count``, ``fan_count``,
``page_post_engagements``, ``page_video_views`` and ``page_views_total`` all
answered, and so did ``id``, ``created_time``, ``permalink_url``, ``message``
and ``shares`` on the posts. Exactly two candidates came back ``NOT_PERMITTED``:

* ``reactions.summary(total_count).limit(0)``
* ``comments.summary(total_count).limit(0)``

Those are other people's activity on the Page's posts, which Meta gates behind a
permission this app does not hold. And because Graph refuses a *whole request*
containing one field it will not serve - the same behaviour that took a retired
insight metric's neighbours down with it in 1F.2.4d - the two optional counts
took every readable post field with them, and the post listing's refusal took the
channel's followers, fans and engagements with *it*.

What these tests hold
----------------------

The split is between **what a connection is** and **what it can measure**. A
Page whose posts cannot be listed at all is a broken connection and still fails
loudly. A Page whose posts list fine but whose reactions may not be counted is a
working connection with two ``NULL`` columns, and the difference between ``NULL``
and ``0`` is preserved all the way to the snapshot, because "nobody reacted" and
"we are not allowed to know" are different facts.

The cost rule from 1F.2.4d is held too: the fallback is **one extra request per
sync**, never one per post.
"""

from __future__ import annotations

# The ``world`` fixture and the Graph fake are imported from sibling modules
# rather than rebuilt, for the reason ``test_pr_meta_connector`` gives.
# ruff: noqa: F811
from datetime import timedelta
from typing import Any

import pytest

from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.domain.pr.channel_analytics import FACEBOOK_TOP_POST_KEY
from meobot.domain.pr.channel_connections import PrChannelSyncErrorCode
from meobot.integrations.meta.constants import MAX_POST_PAGES
from meobot.integrations.meta.errors import MetaApiError
from meobot.integrations.meta.posts import (
    CORE_POST_FIELDS,
    INTERACTION_SUMMARY_FIELDS,
    POST_FIELDS,
    FacebookPost,
    PostWindow,
    field_availability,
    posts_within,
    summarize,
)
from tests.unit.test_pr_facebook_metrics_expansion import (
    graph_with_posts,
    sync,
)
from tests.unit.test_pr_meta_connector import (  # noqa: F401 - `world` is a fixture
    NOW,
    PAGE_A,
    PAGE_A_TOKEN,
    FakeGraph,
    World,
    _configured,
    post_row,
    world,
)

#: The exact refusal CH-0004 hit: core post fields readable, both interaction
#: summaries not permitted, everything above the post edge healthy.
DENIED_SUMMARIES = frozenset({"reactions", "comments"})

#: What ``month_of_posts`` shares add up to. Two of its six posts carry no
#: ``shares`` field at all, which is how Graph reports a post nobody shared -
#: so this total is also the assertion that absence still reads as zero.
MONTH_SHARES = 27


def production_graph(**overrides: Any) -> FakeGraph:
    """A Page in the CH-0004 shape."""
    values: dict[str, Any] = {"denied_post_fields": DENIED_SUMMARIES}
    values.update(overrides)
    return graph_with_posts(**values)


def post_requests(graph: FakeGraph) -> list[str]:
    return [str(request.url) for request in graph.requests if "published_posts" in str(request.url)]


# ===========================================================================
# 1-4: THE SNAPSHOT STILL HAPPENS
# ===========================================================================


async def test_01_the_production_refusal_no_longer_fails_the_reading() -> None:
    """The regression itself, at the provider.

    Page fields answered, Page Insights answered, core post fields answered,
    the two summaries were refused. Before this fix the whole reading raised
    ``INSUFFICIENT_SCOPE`` and nothing was recorded; now every number that
    survived the refusal is here and only the two that did not are ``NULL``.
    """
    graph = production_graph()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()

    assert metrics["followers"] == 124812
    assert metrics["fans"] == 130500
    assert metrics["engagements_30d"] == 70
    assert metrics["engagements_7d"] == 70
    assert metrics["video_views_30d"] == 700
    assert metrics["posts_count_30d"] == 6
    assert metrics["posts_count_7d"] == 3
    assert metrics["shares_30d"] == MONTH_SHARES

    assert metrics["reactions_30d"] is None, "unknown, and unknown is not zero"
    assert metrics["comments_30d"] is None


async def test_02_the_whole_sync_succeeds_and_writes_a_snapshot(world: World) -> None:
    """``pr.sync_channel_metrics`` returns ``ok=True`` and no error code.

    The task is a projection of this outcome - it returns ``outcome.ok``,
    ``outcome.snapshot_id`` and ``outcome.error_code`` and nothing else - so the
    two fields asserted here are the two the scheduler acts on, and an
    ``INSUFFICIENT_SCOPE`` reaching either would be the production failure.
    """
    graph = production_graph()
    _, result = await sync(world, graph)

    assert result.ok is True
    assert result.error_code is None, "no INSUFFICIENT_SCOPE from optional summaries"
    assert result.snapshot_id is not None

    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.followers == 124812
    assert snapshot.fans == 130500
    assert snapshot.engagements_30d == 70
    assert snapshot.posts_count_30d == 6
    assert snapshot.posts_count_7d == 3
    assert snapshot.shares_30d == MONTH_SHARES
    assert snapshot.reactions_30d is None
    assert snapshot.comments_30d is None


async def test_03_the_snapshot_records_why_the_two_columns_are_null(world: World) -> None:
    """Requirement 6. Enough metadata to tell a refusal from a quiet month.

    Safe words MeoBot chose, never Graph's own prose: a provider's error text is
    somebody else's writing about somebody else's system and it would end up on
    a Vietnamese screen if it were allowed to travel.
    """
    graph = production_graph()
    _, result = await sync(world, graph)
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)

    assert snapshot is not None and snapshot.extra_metrics is not None
    assert snapshot.extra_metrics["facebook_post_fields"] == {
        "reactions": "not_permitted",
        "comments": "not_permitted",
        "shares": "available",
    }
    assert snapshot.extra_metrics["facebook_posts_read"] == 6
    assert snapshot.extra_metrics["facebook_posts_truncated"] is False

    blob = str(snapshot.extra_metrics)
    assert PAGE_A_TOKEN not in blob
    assert "OAuthException" not in blob and "permission the token" not in blob


async def test_04_a_permitted_page_still_reports_its_summaries(world: World) -> None:
    """The control. Nothing about the fix changes a Page that answers.

    Kept beside the regression rather than trusted to the suite next door: the
    two behaviours are one decision and reading them together is how the
    difference stays visible.
    """
    _, result = await sync(world, graph_with_posts())
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)

    assert snapshot is not None and snapshot.extra_metrics is not None
    assert snapshot.reactions_30d == 840
    assert snapshot.comments_30d == 113
    assert snapshot.extra_metrics["facebook_post_fields"]["reactions"] == "available"
    assert snapshot.extra_metrics["facebook_post_fields"]["comments"] == "available"


# ===========================================================================
# 5-8: NULL, ZERO AND EMPTY STAY THREE DIFFERENT THINGS
# ===========================================================================


async def test_05_a_refused_summary_is_null_where_a_quiet_month_is_zero() -> None:
    """Requirement 4, stated as the comparison that makes it matter.

    The same Page, the same posts, the same window. The only difference is
    whether Graph would serve the summaries, and the two readings must not agree
    - a dashboard showing ``0`` reactions for a Page nobody is allowed to count
    is not cautious, it is wrong.
    """
    end = NOW - timedelta(days=2)
    quiet = [post_row("111_1", created=end - timedelta(days=1), reactions=0, comments=0)]

    permitted = (
        await graph_with_posts(posts=quiet)
        .facebook()
        .fetch_channel_metrics(access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW)
    ).canonical()
    refused = (
        await production_graph(posts=quiet)
        .facebook()
        .fetch_channel_metrics(access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW)
    ).canonical()

    assert permitted["reactions_30d"] == 0 and permitted["comments_30d"] == 0
    assert refused["reactions_30d"] is None and refused["comments_30d"] is None
    assert permitted["posts_count_30d"] == refused["posts_count_30d"] == 1


async def test_06_shares_keep_their_own_absence_rule() -> None:
    """Requirement 4's other half. ``shares`` is the Page's own content.

    Graph omits ``shares`` entirely for a post nobody shared rather than sending
    a zero, and that absence is unambiguous where a summary's is not - so a
    month of unshared posts is ``shares_30d = 0`` even here, and the metadata
    says ``empty`` rather than ``not_permitted``.
    """
    end = NOW - timedelta(days=2)
    graph = production_graph(
        posts=[
            post_row("111_1", created=end - timedelta(days=1)),
            post_row("111_2", created=end - timedelta(days=4)),
        ]
    )
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )

    assert reading.canonical()["shares_30d"] == 0, "nobody shared, and that is a measurement"
    assert reading.extra_metrics["facebook_post_fields"] == {
        "reactions": "not_permitted",
        "comments": "not_permitted",
        "shares": "empty",
    }


async def test_07_the_best_post_is_still_recorded_from_what_arrived() -> None:
    """A ranking on shares alone is a worse ranking, not a missing one.

    ``reactions`` and ``comments`` travel as ``null`` in the stored payload
    rather than as zeros, so a reader of this row can tell that the ranking was
    made on partial information.
    """
    graph = production_graph()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    top = reading.extra_metrics[FACEBOOK_TOP_POST_KEY]

    assert top["post_id"] == "111_1", "the most-shared post of the month"
    assert top["shares"] == 20
    assert top["reactions"] is None and top["comments"] is None


def test_08_the_availability_report_names_five_distinct_outcomes() -> None:
    """:func:`field_availability` at the unit, including the two edges.

    A listing nobody could read reports ``not_read`` rather than
    ``not_permitted``: no refusal was ever heard, and inventing a cause is how a
    diagnostic starts misleading the person reading it.
    """
    assert field_availability(None) == {
        "reactions": "not_read",
        "comments": "not_read",
        "shares": "not_read",
    }
    assert field_availability(PostWindow()) == {
        "reactions": "empty",
        "comments": "empty",
        "shares": "empty",
    }
    assert field_availability(PostWindow(summaries_permitted=False)) == {
        "reactions": "not_permitted",
        "comments": "not_permitted",
        "shares": "empty",
    }
    # Graph served the listing, said nothing about the summaries, and never
    # refused anything. ``absent`` rather than ``not_permitted``, because
    # naming a cause nobody heard is how a diagnostic starts misleading its
    # reader - and rather than ``empty``, because there were posts to count.
    served_without = PostWindow(posts=(FacebookPost(post_id="111_1", shares=3),))
    assert field_availability(served_without) == {
        "reactions": "absent",
        "comments": "absent",
        "shares": "available",
    }


def test_09_a_window_read_without_summaries_totals_nothing_it_did_not_read() -> None:
    """The propagation rule, unchanged, now reached by a second route.

    ``summarize`` needed no change for any of this: a post whose summary never
    arrived already carried ``None``, and a sum missing one term already refused
    to be a sum. The core fields are all there, so the counts built from them
    are real numbers.
    """
    end = NOW - timedelta(days=2)
    window = PostWindow(
        posts=(
            FacebookPost(post_id="111_1", created_time=end - timedelta(days=1), shares=7),
            FacebookPost(post_id="111_2", created_time=end - timedelta(days=3)),
        ),
        summaries_permitted=False,
    )
    totals = summarize(window)

    assert totals.posts_count == 2
    assert totals.shares == 7
    assert totals.reactions is None and totals.comments is None
    assert totals.top_post is not None and totals.top_post.post_id == "111_1"

    weekly = posts_within(window, start=end - timedelta(days=6), end=end + timedelta(days=1))
    assert weekly.summaries_permitted is False, "a slice inherits what its parent could read"


# ===========================================================================
# 10-13: THE COST, AND THE FAILURES THAT MUST STILL FAIL
# ===========================================================================


async def test_10_the_fallback_costs_one_request_and_never_one_per_post() -> None:
    """Requirement 7. The bound is ``MAX_POST_PAGES + 1``, whatever happens.

    Three pages of posts, the first request refused. Four requests reach the
    edge: the refused one and the three that read the window. Nothing is asked
    about an individual post, which is the rule 1F.2.4d was built on and the one
    a naive "then fetch each post's reactions" fix would have broken.
    """
    graph = production_graph(posts_page_size=2)
    await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )

    sent = post_requests(graph)
    assert len(sent) == 4
    assert len(sent) <= MAX_POST_PAGES + 1

    everything = graph.sent()
    for post_id in ("111_1", "111_2", "111_3", "111_4", "111_5", "111_6"):
        assert f"/{post_id}" not in everything, "no per-post request, ever"


async def test_11_the_fallback_asks_for_the_core_fields_and_nothing_else() -> None:
    """The retry drops exactly the optional half and keeps the required one."""
    graph = production_graph()
    await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    first, second = post_requests(graph)

    for summary in INTERACTION_SUMMARY_FIELDS:
        assert summary.split(".")[0] in first, "the first attempt still hopes for them"
        assert summary.split(".")[0] not in second, "the retry does not"
    for core in CORE_POST_FIELDS:
        assert core in second, core
    assert "shares" in second, "a share count is the Page's own content"


async def test_12_a_refused_core_listing_still_fails_the_sync(world: World) -> None:
    """Requirement 3. Degradation is scoped to the optional summaries.

    The whole edge is refused here, not just two fields, so the retry without
    the summaries is refused too - and that second refusal is the one that
    reaches the sync. A Page whose own posts cannot be listed is a connection
    somebody has to fix, and it still says so.
    """
    graph = production_graph(
        denied_post_fields=frozenset(),
        posts_status=403,
        posts_error={"error": {"message": "(#10) permission", "code": 10}},
    )
    with pytest.raises(MetaApiError) as failure:
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
    assert len(post_requests(graph)) == 2, "one retry, then the refusal stands"

    _, result = await sync(world, graph)
    assert result.ok is False
    assert result.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
    assert result.snapshot_id is None


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"error": {"code": 190, "error_subcode": 458}}, PrChannelSyncErrorCode.AUTH_REQUIRED),
        (400, {"error": {"code": 4}}, PrChannelSyncErrorCode.RATE_LIMITED),
        (500, {"error": {"code": 2}}, PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
        (
            404,
            {"error": {"code": 100, "error_subcode": 33}},
            PrChannelSyncErrorCode.INVALID_ACCOUNT,
        ),
    ],
)
async def test_13_the_retry_never_swallows_a_failure_about_the_caller(
    status: int, payload: dict[str, Any], expected: PrChannelSyncErrorCode
) -> None:
    """A revoked token, a quota and an outage are not field problems.

    Asking for fewer fields cannot help any of them, so none is retried - the
    request count proves it - and all four still fail the sync loudly.
    """
    graph = production_graph(
        denied_post_fields=frozenset(), posts_status=status, posts_error=payload
    )
    with pytest.raises(MetaApiError) as failure:
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    assert failure.value.error_code is expected
    assert len(post_requests(graph)) == 1, "not retried"


async def test_14_a_page_level_permission_failure_is_still_insufficient_scope(
    world: World,
) -> None:
    """Requirement 3, at the top of the call chain rather than the bottom.

    Identity, the Page fields and Page Insights are core: a grant that cannot
    read them cannot establish a valid connection, and no amount of post-field
    degradation applies. The sync fails and writes nothing.
    """
    graph = production_graph()
    graph.status = 403
    graph.error_payload = {"error": {"message": "(#10) permission", "code": 10}}

    with pytest.raises(MetaApiError) as failure:
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
    assert not post_requests(graph), "it never reached the posts"


def test_15_the_two_field_lists_together_are_what_one_request_asks_for() -> None:
    """The split is a partition, not a rewrite.

    ``POST_FIELDS`` is still one ``fields=`` parameter carrying everything;
    what changed is that the connector now knows which half it can lose.
    """
    assert ",".join((*CORE_POST_FIELDS, *INTERACTION_SUMMARY_FIELDS)) == POST_FIELDS
    assert "shares" in CORE_POST_FIELDS, "the Page's own content is never optional"
    for required in ("id", "created_time", "permalink_url", "message"):
        assert required in CORE_POST_FIELDS, required
    assert not any("summary" in field for field in CORE_POST_FIELDS)
