"""Step 1F.2.9 - the TikTok account panel a reviewer actually looks at.

Numbered 63-90, continuing the numbering ``test_pr_tiktok_connector`` started:
63-68 the demo's preconditions, 69-77 the account overview, 78-83 recent videos
and their degradation, 84-87 the refresh, 88-90 what must never reach a browser.

**No test in this file contacts TikTok.** Every one drives the real service, the
real provider and the real client through ``FakeTikTok`` - the ``MockTransport``
harness Step 1F.2.6 built - which is imported rather than rebuilt, so a panel
test and a connector test cannot disagree about what TikTok does.

What this file is really testing
---------------------------------

**That an unavailable field is never a zero.** TikTok gates ``user.info.stats``
and ``video.list`` behind app review, so the reviewer's own account is quite
likely to be missing one while they are looking at the screen. A panel that
printed ``0`` followers over a refused scope would be showing a TikTok reviewer
a fabricated number about their own account, which is the worst possible thing
this screen could do. Tests 74, 75, 80 and 81.

**That the panel and the sync are one mechanism.** "Đồng bộ lại" goes through
the same capability check, the same audit action and the same conditional-UPDATE
claim as "Đồng bộ ngay", and a press that lands on a sync already in flight is
not an error. Tests 84-87.

**That nothing renders a credential.** The access token, the refresh token and
the client secret are distinctive strings in the fixture, and test 88 serialises
a full panel and asserts none of them appears anywhere in it. Test 89 does the
same for the connection state endpoint the panel sits beside.
"""

from __future__ import annotations

# The ``world`` fixture, the channel helper and the whole ``FakeTikTok`` harness
# come from sibling modules rather than being rebuilt. ruff sees a redefinition
# on every signature that takes the fixture.
# ruff: noqa: F811
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from meobot.api.schemas.pr import ChannelConnectionStateResponse, TikTokOverviewResponse
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.domain.pr.channel_connections import (
    PrChannelConnectionState,
    PrChannelSyncStatus,
)
from meobot.domain.pr.channel_metrics import PrChannelPlatform
from meobot.domain.pr.errors import PrPermissionDeniedError, PrValidationError
from meobot.domain.pr.labels import TIKTOK_SCOPE_LABELS
from meobot.integrations.tiktok.constants import (
    MAX_VIDEO_PAGES,
    RECENT_VIDEO_PAGE,
    TIKTOK_SCOPES,
    VIDEO_PAGE_SIZE,
)
from tests.unit.test_pr_channel_metrics import channel as make_channel
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)
from tests.unit.test_pr_tiktok_connector import (  # noqa: F401 - `tiktok` is a fixture
    ACCESS_TOKEN,
    CLIENT_SECRET,
    OPEN_ID,
    REFRESH_TOKEN,
    ROTATED_REFRESH,
    FakeTikTok,
    authorize_and_finish,
    connector_settings,
    tiktok,
    tiktok_channel,
    video_row,
)

pytestmark = pytest.mark.asyncio


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def _configured(world: World) -> None:
    """A deployment with a real TikTok app, for every test in this file.

    The same settings the connector tests use. Applied to the service bundle
    *and* to ``app.state.settings``, because the HTTP tests build their own
    services per request and would otherwise see a deployment with no TikTok
    app at all.
    """
    settings = connector_settings()
    world.services.channel_connections._settings = settings
    world.services.channel_sync._settings = settings
    world.services.tiktok_account._settings = settings
    world.services.settings = settings
    world.client.app.state.settings = settings


def with_videos(count: int = 3, **extra: Any) -> FakeTikTok:
    """A TikTok whose account has ``count`` videos with full counters."""
    fake = FakeTikTok(**extra)
    fake.videos = [
        video_row(
            f"v{index}",
            created=1_760_000_000 + index * 86_400,
            video_description=f"Mô tả video {index}",
            duration=32 + index,
            cover_image_url=f"https://p16-sign.tiktokcdn.com/cover-{index}.jpeg",
            share_url=f"https://www.tiktok.com/@bsvutrongtien/video/{index}",
            embed_link=f"https://www.tiktok.com/embed/v2/{index}",
            view_count=125_400 + index,
            like_count=8_200 + index,
            comment_count=320 + index,
            share_count=56 + index,
        )
        for index in range(count)
    ]
    return fake


async def connected_channel(world: World, fake: FakeTikTok):
    """A TikTok channel with a live, healthy connection over ``fake``."""
    channel = await tiktok_channel(world)
    outcome = await authorize_and_finish(world, channel.id, tiktok=fake)
    return channel, outcome.connection


async def overview(
    world: World, channel_id: uuid.UUID, fake: FakeTikTok, **kwargs: Any
) -> TikTokOverviewResponse:
    """One panel, through the real service over ``fake``, as the API renders it."""
    return TikTokOverviewResponse.from_view(
        await world.services.tiktok_account.describe(
            actor=world.actor(world.owner),
            channel_id=channel_id,
            provider_client=fake.provider(),
            **kwargs,
        )
    )


# ===========================================================================
# 63-68: THE DEMO'S PRECONDITIONS
# ===========================================================================


async def test_63_a_configured_deployment_reports_tiktok_as_configured(
    world: World, tiktok: FakeTikTok
) -> None:
    """The bug that hid the whole connector behind a disabled button.

    ``_connector_configured`` had no TikTok branch and fell through to
    ``False``, and the panel disables "Kết nối TikTok" on exactly that flag. So
    a deployment with a working TikTok app told everybody its configuration was
    missing and refused to let them press the one control that mattered.
    """
    from meobot.api.routers.pr import _connector_configured

    assert _connector_configured(connector_settings(), PrChannelPlatform.TIKTOK) is True


async def test_64_an_unconfigured_deployment_still_says_so(world: World) -> None:
    """And the fix did not turn the warning into a permanent lie."""
    from meobot.api.routers.pr import _connector_configured

    bare = connector_settings(tiktok_client_key=None, tiktok_client_secret=None)
    assert _connector_configured(bare, PrChannelPlatform.TIKTOK) is False


async def test_65_a_platform_with_no_connector_is_still_unconfigured() -> None:
    """The fall-through tail must not become True by default."""
    from meobot.api.routers.pr import _connector_configured

    assert _connector_configured(connector_settings(), PrChannelPlatform.WEBSITE) is False
    assert _connector_configured(connector_settings(), None) is False


async def test_66_the_connection_endpoint_offers_a_tiktok_connect_control(
    world: World,
) -> None:
    """What the panel reads before anybody has connected anything.

    ``supported`` and ``configured`` both true, and no connection - which is the
    "Chưa kết nối / [Kết nối TikTok]" screen the demo opens on.
    """
    channel = await tiktok_channel(world)
    await world.session.flush()
    response = world.client.get(f"/api/pr/channels/{channel.id}/connection")
    assert response.status_code == 200
    body = ChannelConnectionStateResponse.model_validate(response.json())
    assert body.supported is True
    assert body.configured is True
    assert body.provider == "TIKTOK"
    assert body.provider_label == "TikTok"
    assert body.connection is None


async def test_67_the_oauth_callback_returns_to_the_selected_channel(
    world: World, tiktok: FakeTikTok
) -> None:
    """The redirect names *this* channel, so the panel can reopen on it.

    The whole reviewer-visible loop depends on it: consent leaves MeoChat, and
    what comes back has to land on the same channel detail rather than on a
    channel list with nothing selected.

    The destination is built from configuration, never from the request - there
    is no ``next`` parameter in this flow and there must never be one.
    """
    from meobot.api.routers.pr import _connection_redirect

    channel = await tiktok_channel(world)
    outcome = await authorize_and_finish(world, channel.id, tiktok=tiktok)
    target = _connection_redirect(
        connector_settings(), status="connected", channel_id=str(outcome.connection.channel_id)
    )
    assert target.startswith("https://pr.example.com/pr/channels?")
    assert "connection=connected" in target
    assert f"channel={channel.id}" in target


async def test_68_the_scope_label_table_covers_exactly_what_is_requested() -> None:
    """A fifth scope must not reach a consent screen with no Vietnamese name.

    The labels live in the domain layer and the scope tuple lives in the
    integration, so nothing but this assertion keeps them in step - which is
    fine, because this is the only property that matters and it fails loudly.
    """
    assert set(TIKTOK_SCOPE_LABELS) == set(TIKTOK_SCOPES)
    for label, description in TIKTOK_SCOPE_LABELS.values():
        assert label and description


# ===========================================================================
# 69-77: THE ACCOUNT OVERVIEW
# ===========================================================================


async def test_69_a_disconnected_channel_has_no_panel_to_draw(
    world: World, tiktok: FakeTikTok
) -> None:
    """ "Chưa kết nối" is a refusal with a reason, not an empty overview."""
    channel = await tiktok_channel(world)
    with pytest.raises(PrValidationError) as caught:
        await overview(world, channel.id, tiktok)
    assert caught.value.details["reason"] == "not_connected"


async def test_70_a_channel_on_another_platform_is_refused(world: World) -> None:
    """A Facebook channel cannot be read through TikTok's panel.

    Server-side, rather than by a component that was not rendered: a hidden
    control is not authorization.
    """
    channel = await make_channel(world, platform_code="FACEBOOK", name="Apexmed FB")
    with pytest.raises(PrValidationError) as caught:
        await overview(world, channel.id, FakeTikTok())
    assert caught.value.details["reason"] == "not_tiktok"


async def test_71_the_identity_shown_is_the_one_tiktok_confirmed(
    world: World, tiktok: FakeTikTok
) -> None:
    """``user.info.basic``: the avatar, the display name and the account id.

    ``open_id`` rather than a username, because that is what the connection
    binds to and what somebody compares against TikTok's own console.
    """
    channel, _ = await connected_channel(world, tiktok)
    panel = await overview(world, channel.id, tiktok)

    assert panel.account.open_id == OPEN_ID
    assert panel.account.display_name == "Tâm sự cùng bs Vũ Trọng Tiến"
    assert panel.account.avatar_url == "https://p16.tiktokcdn.com/avatar.jpeg"
    assert panel.state == PrChannelConnectionState.CONNECTED.value
    assert panel.state_label == "Đã kết nối"


async def test_72_the_profile_half_carries_the_handle_bio_and_deep_link(
    world: World, tiktok: FakeTikTok
) -> None:
    """``user.info.profile``: what lets somebody confirm *which* account this is.

    The profile URL is TikTok's own ``profile_deep_link`` and never one composed
    from a username - a composed link would be wrong the moment somebody renamed
    themselves.
    """
    channel, _ = await connected_channel(world, tiktok)
    panel = await overview(world, channel.id, tiktok)

    assert panel.account.username == "bsvutrongtien"
    assert panel.account.handle == "@bsvutrongtien"
    assert panel.account.profile_url == "https://www.tiktok.com/@bsvutrongtien"
    assert panel.account.bio == "Bác sĩ thẩm mỹ"
    assert panel.account.is_verified is False
    assert panel.profile_availability == "available"


async def test_73_the_stats_half_carries_the_four_lifetime_counters(
    world: World, tiktok: FakeTikTok
) -> None:
    """``user.info.stats``: followers, following, lifetime likes, video count."""
    channel, _ = await connected_channel(world, tiktok)
    panel = await overview(world, channel.id, tiktok)

    assert panel.stats.follower_count == 128_400
    assert panel.stats.following_count == 312
    assert panel.stats.likes_count == 4_500_000
    assert panel.stats.video_count == 382
    assert panel.stats.availability == "available"


async def test_74_a_refused_stats_scope_is_blank_and_explained_never_zero(
    world: World,
) -> None:
    """The most expensive mistake this panel could make, and the test for it.

    An app awaiting review for ``user.info.stats`` gets the whole group refused.
    Four ``None``s and a word saying why - never four zeroes, which would show a
    TikTok reviewer a fabricated follower count for their own account.
    """
    fake = FakeTikTok(denied_fields=frozenset({"follower_count"}))
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert panel.stats.follower_count is None
    assert panel.stats.following_count is None
    assert panel.stats.likes_count is None
    assert panel.stats.video_count is None
    assert panel.stats.availability == "not_permitted"
    assert panel.stats.availability_label == "Chưa được cấp quyền"
    # And the identity beside it still arrived: one refused group must not cost
    # the panel the account it was drawing.
    assert panel.account.display_name == "Tâm sự cùng bs Vũ Trọng Tiến"


async def test_75_a_refused_profile_scope_leaves_the_identity_standing(
    world: World,
) -> None:
    """``user.info.basic`` alone still produces a usable panel."""
    fake = FakeTikTok(denied_fields=frozenset({"bio_description", "username"}))
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert panel.account.open_id == OPEN_ID
    assert panel.account.display_name == "Tâm sự cùng bs Vũ Trọng Tiến"
    assert panel.account.bio is None
    assert panel.account.handle is None
    # Not False. An unverified account and an unreadable one are different
    # statements, and only one of them may be printed as "chưa xác minh".
    assert panel.account.is_verified is None
    assert panel.profile_availability == "not_permitted"


async def test_76_every_requested_scope_is_listed_granted_or_not(
    world: World, tiktok: FakeTikTok
) -> None:
    """The permissions block is evidence, so it lists refusals too.

    A list that silently omitted an ungranted scope would make an app awaiting
    review look identical to one fully approved.
    """
    tiktok.granted_scope = "user.info.basic,user.info.profile"
    channel, _ = await connected_channel(world, tiktok)
    panel = await overview(world, channel.id, tiktok)

    assert [row.scope for row in panel.scopes] == list(TIKTOK_SCOPES)
    granted = {row.scope: row.granted for row in panel.scopes}
    assert granted == {
        "user.info.basic": True,
        "user.info.profile": True,
        "user.info.stats": False,
        "video.list": False,
    }
    assert panel.granted_scopes == ["user.info.basic", "user.info.profile"]
    stats_row = next(row for row in panel.scopes if row.scope == "user.info.stats")
    assert stats_row.label == "Thống kê tài khoản"
    assert "người theo dõi" in stats_row.description.lower()


async def test_77_a_token_bound_to_another_account_is_refused(
    world: World, tiktok: FakeTikTok
) -> None:
    """The wrong-account check, on the read path as well as the sync path.

    ``/v2/user/info/`` answers for whoever the token belongs to, so the
    ``open_id`` that comes back is authoritative. Showing one account's
    followers under another account's channel name is worse than showing none.
    """
    channel, connection = await connected_channel(world, tiktok)
    connection.provider_account_id = "_someone-else-entirely"
    await world.session.flush()

    with pytest.raises(PrValidationError) as caught:
        await overview(world, channel.id, tiktok)
    assert caught.value.details["error_code"] == "INVALID_ACCOUNT"
    # MeoBot's own sentence, never TikTok's prose.
    assert "TikTok" in str(caught.value)


# ===========================================================================
# 78-83: RECENT PUBLIC VIDEOS
# ===========================================================================


async def test_78_recent_videos_carry_their_metadata_and_counters(
    world: World,
) -> None:
    """``video.list``: the cover, the text, the time and the four counters."""
    fake = with_videos(3)
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert len(panel.videos) == 3
    first = panel.videos[0]
    assert first.video_id == "v0"
    assert first.title == "Video v0"
    assert first.description == "Mô tả video 0"
    assert first.cover_image_url == "https://p16-sign.tiktokcdn.com/cover-0.jpeg"
    assert first.share_url == "https://www.tiktok.com/@bsvutrongtien/video/0"
    assert first.embed_link == "https://www.tiktok.com/embed/v2/0"
    assert first.created_at is not None
    assert first.duration_seconds == 32
    assert first.view_count == 125_400
    assert first.like_count == 8_200
    assert first.comment_count == 320
    assert first.share_count == 56
    assert panel.videos_availability == "available"
    assert panel.video_counters_availability == "available"


async def test_79_the_video_list_is_bounded_and_pages_on_tiktoks_own_cursor(
    world: World,
) -> None:
    """A bounded page, and "Xem thêm" continues it with TikTok's own token.

    The cursor is passed back verbatim and never interpreted - it is documented
    as a millisecond timestamp and a client doing arithmetic on it would break
    the day TikTok changed what it puts there. ``has_more`` is TikTok's own flag
    and the only thing that decides whether another page exists.
    """
    fake = with_videos(14)
    channel, _ = await connected_channel(world, fake)

    first = await overview(world, channel.id, fake, video_limit=RECENT_VIDEO_PAGE)
    assert len(first.videos) == RECENT_VIDEO_PAGE
    assert first.videos_has_more is True
    assert first.videos_cursor == RECENT_VIDEO_PAGE
    # The ceiling on "Xem thêm" is the connector's, sent to the browser rather
    # than picked by it.
    assert first.max_video_pages == MAX_VIDEO_PAGES

    second = await overview(
        world, channel.id, fake, video_limit=RECENT_VIDEO_PAGE, cursor=first.videos_cursor
    )
    assert [row.video_id for row in second.videos] == [f"v{i}" for i in range(6, 12)]
    assert second.videos_has_more is True

    last = await overview(
        world, channel.id, fake, video_limit=RECENT_VIDEO_PAGE, cursor=second.videos_cursor
    )
    assert [row.video_id for row in last.videos] == ["v12", "v13"]
    assert last.videos_has_more is False


async def test_80_a_refused_video_list_is_explained_not_hidden(world: World) -> None:
    """An app not yet approved for ``video.list`` still gets the whole account.

    The section says why it is empty. Everything above it - identity, profile,
    stats - is untouched, because a refused endpoint is not a refused panel.
    """
    fake = FakeTikTok(video_error="scope_not_authorized")
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert panel.videos == []
    assert panel.videos_availability == "not_permitted"
    assert panel.videos_availability_label == "Chưa được cấp quyền"
    assert panel.stats.follower_count == 128_400
    assert panel.account.display_name == "Tâm sự cùng bs Vũ Trọng Tiến"


async def test_81_counters_silently_omitted_still_leave_the_videos(
    world: World,
) -> None:
    """TikTok's own speciality: a 200, ``error.code == "ok"``, and no counters.

    The cards still draw - cover, title, date, link - and the numbers show as
    unavailable rather than as zero. "This video has no views" and "this app is
    not served view counts" are different sentences.
    """
    fake = with_videos(2, silent_fields=frozenset({"view_count", "like_count"}))
    # The other two counters are omitted from the rows as well, so the page
    # carries none at all - which is the case the ``not_returned`` word exists
    # for.
    fake.silent_fields = frozenset({"view_count", "like_count", "comment_count", "share_count"})
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert len(panel.videos) == 2
    assert panel.videos[0].cover_image_url is not None
    assert panel.videos[0].view_count is None
    assert panel.videos[0].like_count is None
    assert panel.videos_availability == "available"
    assert panel.video_counters_availability == "not_returned"
    assert panel.video_counters_availability_label == "TikTok không trả về trường này"


async def test_82_a_refused_counter_field_costs_only_the_counters(
    world: World,
) -> None:
    """The degradation that earns its keep.

    TikTok refuses a whole ``fields=`` list over one name, so asking for the
    counters and the cover together would lose the cover too. The core fields
    are re-asked alone, and the panel shows six cards without numbers instead of
    an empty section.
    """
    fake = with_videos(3, unknown_fields=frozenset({"share_count"}))
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert len(panel.videos) == 3
    assert panel.videos[0].cover_image_url is not None
    assert panel.videos[0].share_url is not None
    assert panel.videos[0].view_count is None
    assert panel.videos_availability == "available"
    assert panel.video_counters_availability == "unsupported"


async def test_83_an_account_with_no_videos_says_empty_not_refused(
    world: World, tiktok: FakeTikTok
) -> None:
    """A quiet account and a missing scope must not read the same."""
    channel, _ = await connected_channel(world, tiktok)
    panel = await overview(world, channel.id, tiktok)

    assert panel.videos == []
    assert panel.videos_availability == "empty"
    assert panel.videos_availability_label == "TikTok trả về rỗng"


async def test_83b_an_account_that_has_never_posted_is_not_an_error(
    world: World,
) -> None:
    """TikTok files "never posted" under an **account** error, and it must not escape.

    ``user_has_no_video`` classifies as ``INVALID_ACCOUNT`` - the same class as
    a deleted account and a banned one - so letting it through would turn a
    brand-new creator's first visit into a red error box under a perfectly
    healthy connection. It is read as "no videos" because the account was
    resolved by ``/v2/user/info/`` on this same token moments earlier.
    """
    fake = FakeTikTok(video_error="user_has_no_video")
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    assert panel.videos == []
    assert panel.videos_availability == "empty"
    # Everything else on the panel is untouched.
    assert panel.account.display_name == "Tâm sự cùng bs Vũ Trọng Tiến"
    assert panel.stats.follower_count == 128_400


# ===========================================================================
# 84-87: "ĐỒNG BỘ LẠI"
# ===========================================================================


async def test_84_a_refresh_uses_the_existing_sync_claim(world: World, tiktok: FakeTikTok) -> None:
    """One press, two mechanisms, neither of them reimplemented.

    The snapshot half goes through ``request_sync`` and ``claim`` - the same
    capability check, the same audit action and the same conditional UPDATE that
    "Đồng bộ ngay" has used since Step 1F.2.4b. The evidence is the connection
    row: it comes back ``SYNCING``, which only that claim sets.
    """
    fake = with_videos(2)
    channel, connection = await connected_channel(world, fake)

    view = await world.services.tiktok_account.refresh(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=channel.id,
        provider_client=fake.provider(),
    )

    assert view.sync_requested is True
    row = await world.session.get(PrChannelConnection, connection.id)
    assert row is not None
    assert row.sync_status is PrChannelSyncStatus.SYNCING
    # And the live half really did happen inside the same call.
    assert view.account.follower_count == 128_400
    assert len(view.videos.videos) == 2


async def test_85_a_refresh_over_a_running_sync_still_refreshes_the_screen(
    world: World,
) -> None:
    """A double-click is not a breakage.

    The claim is already held, so no second snapshot is requested - and that is
    reported rather than raised, because the picture on screen *was* refreshed
    and refusing the whole call would make a harmless press look like a failure.
    """
    fake = with_videos(2)
    channel, connection = await connected_channel(world, fake)
    assert await world.services.channel_sync.claim(connection.id) is True

    view = await world.services.tiktok_account.refresh(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=channel.id,
        provider_client=fake.provider(),
    )
    assert view.sync_requested is False
    assert view.account.display_name == "Tâm sự cùng bs Vũ Trọng Tiến"


async def test_86_refreshing_is_a_management_capability(world: World, tiktok: FakeTikTok) -> None:
    """Reading is open; asking TikTok for fresh numbers is ``PR_CHANNEL_MANAGE``.

    The same split ``/metrics/sync`` has had since Step 1F.2.4b. A colleague who
    may see the channel may see the panel, and cannot spend its quota.
    """
    channel, _ = await connected_channel(world, tiktok)

    # Reading is fine for an ordinary employee.
    await world.services.tiktok_account.describe(
        actor=world.actor(world.member),
        channel_id=channel.id,
        provider_client=tiktok.provider(),
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.tiktok_account.refresh(
            actor=world.actor(world.member),
            request_id=world.request_id,
            channel_id=channel.id,
            provider_client=tiktok.provider(),
        )


async def test_87_the_rotated_refresh_token_is_still_persisted(
    world: World, tiktok: FakeTikTok
) -> None:
    """Reading the panel goes through ``access_token_for``, rotation and all.

    TikTok issues a **new** refresh token on every refresh and retires the old
    one. The panel gets its access token from the service that owns that
    branch, so opening the panel keeps the stored credential alive rather than
    quietly invalidating it - which is what a second token path would have done.
    """
    channel, connection = await connected_channel(world, tiktok)
    assert world.services.channel_connections.read_credential(connection) == ROTATED_REFRESH

    tiktok.rotate_to = "rft.SECONDROTATION0003"
    await overview(world, channel.id, tiktok)

    row = await world.session.get(PrChannelConnection, connection.id)
    assert row is not None
    assert world.services.channel_connections.read_credential(row) == "rft.SECONDROTATION0003"


# ===========================================================================
# 88-90: WHAT MUST NEVER REACH A BROWSER
# ===========================================================================


async def test_88_no_credential_appears_anywhere_in_the_panel(
    world: World,
) -> None:
    """The regression test the whole schema design exists to make passable.

    A full panel - identity, profile, stats, videos, permissions - serialised
    exactly as the API sends it, searched for all three secrets in the fixture.
    Scope *names* are expected to be there; they are what somebody consented to.
    """
    fake = with_videos(4)
    channel, _ = await connected_channel(world, fake)
    panel = await overview(world, channel.id, fake)

    rendered = json.dumps(panel.model_dump(mode="json"), ensure_ascii=False)
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, ROTATED_REFRESH, CLIENT_SECRET):
        assert secret not in rendered
    for word in ("access_token", "refresh_token", "client_secret", "encrypted_credential"):
        assert word not in rendered
    # The evidence that is meant to be there.
    assert "user.info.stats" in rendered
    assert OPEN_ID in rendered


async def test_89_the_connection_state_endpoint_leaks_nothing_either(
    world: World, tiktok: FakeTikTok
) -> None:
    """The panel the account overview sits beside, checked for the same thing."""
    channel, _ = await connected_channel(world, tiktok)
    await world.session.flush()

    response = world.client.get(f"/api/pr/channels/{channel.id}/connection")
    assert response.status_code == 200
    rendered = json.dumps(response.json(), ensure_ascii=False)
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, ROTATED_REFRESH, CLIENT_SECRET):
        assert secret not in rendered


async def test_90_a_provider_failure_never_carries_tiktoks_own_words(
    world: World,
) -> None:
    """A rate limit becomes a Vietnamese sentence MeoBot wrote, and nothing else.

    Not TikTok's ``message``, not its ``log_id``, not the status line. The safe
    error class travels in ``details`` for a client that wants to branch on it.
    """
    fake = FakeTikTok(video_error="rate_limit_exceeded", denied_fields=frozenset())
    # Scope the failure to the whole account read, not only the videos.
    fake.token_error = None
    channel, _ = await connected_channel(world, fake)
    fake.videos = []
    fake.video_error = "rate_limit_exceeded"

    with pytest.raises(PrValidationError) as caught:
        await overview(world, channel.id, fake)
    message = str(caught.value)
    assert caught.value.details["error_code"] == "RATE_LIMITED"
    assert "TikTok" in message
    assert "rate_limit_exceeded" not in message
    assert "L-ERR" not in message


async def test_91_the_overview_route_answers_over_http(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route is wired, bounded, and rendered from the real schema.

    The provider is faked one layer down - at the registry the service asks -
    so what is exercised here is the whole request path: dependency, capability,
    credential, provider, schema.
    """
    fake = with_videos(8)
    channel, _ = await connected_channel(world, fake)
    await world.session.flush()
    monkeypatch.setattr(
        "meobot.application.pr_tiktok_account_service.build_provider",
        lambda platform, settings: fake.provider(),
    )

    response = world.client.get(
        f"/api/pr/channels/{channel.id}/connections/tiktok/overview?videos=3"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["videos"]) == 3
    assert body["account"]["open_id"] == OPEN_ID
    assert body["stats"]["follower_count"] == 128_400
    assert body["videos_has_more"] is True

    # Bounded by the connector, refused by the route rather than clamped
    # silently: a caller asking for a hundred videos is asking for something
    # this endpoint does not do.
    assert (
        world.client.get(
            f"/api/pr/channels/{channel.id}/connections/tiktok/overview"
            f"?videos={VIDEO_PAGE_SIZE + 1}"
        ).status_code
        == 422
    )


async def test_92_the_refresh_route_answers_and_claims(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Đồng bộ lại" over HTTP: ``200``, fresh data, and the existing claim taken.

    ``200`` rather than ``202`` because the body is complete and current. The
    snapshot the worker appends is the asynchronous half, and ``sync_requested``
    reports whether it was handed over.
    """
    fake = with_videos(2)
    channel, connection = await connected_channel(world, fake)
    await world.session.flush()
    monkeypatch.setattr(
        "meobot.application.pr_tiktok_account_service.build_provider",
        lambda platform, settings: fake.provider(),
    )
    enqueued: list[str] = []
    monkeypatch.setattr("meobot.api.routers.pr._enqueue_channel_sync", enqueued.append)

    response = world.client.post(f"/api/pr/channels/{channel.id}/connections/tiktok/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["sync_requested"] is True
    assert body["account"]["display_name"] == "Tâm sự cùng bs Vũ Trọng Tiến"
    assert enqueued == [str(connection.id)]

    row = (
        await world.session.execute(
            select(PrChannelConnection).where(PrChannelConnection.id == connection.id)
        )
    ).scalar_one()
    assert row.sync_status is PrChannelSyncStatus.SYNCING
