"""Step 1F.2.4a - channel identity, and numbers a person can write down.

Numbered 1-41, following the requirement numbering the step was specified with:
1-8 platform identity, 9-18 recording a snapshot, 19-24 history, 25-30 trend,
31-33 derived status, 34-38 the security boundary, 39-41 the query shapes.

The world is ``test_pr_production_lifecycle``'s, imported rather than rebuilt,
because it already has the five people whose different capabilities are the
whole subject of the authorization half - and a second copy of it would
eventually disagree about who holds what.

The two claims this file exists to keep separate
-------------------------------------------------

**Who may read** and **who may write**. Step 1F.2.4a widened nobody's view of a
channel: whoever could see one can see what it measured. Recording a reading is
management work, and an ordinary member is refused - in the service and again
over HTTP, because a hidden button is not an authorization layer.

And one claim that runs through all of them: **nothing here talks to a
platform.** The last test in the file reads this module's own imports and says
so, which is the only form of that assertion that cannot rot.
"""

from __future__ import annotations

# The ``world`` fixture is imported from a sibling module rather than rebuilt -
# see the module docstring. pytest requires a fixture to be a module-level name
# and every test then takes a parameter of the same name, so ruff sees a
# redefinition on every signature in the file.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from meobot.application.pr_channel_metrics_service import (
    DEFAULT_SNAPSHOT_PAGE,
    ChannelMetricsView,
    RecordChannelMetricsCommand,
)
from meobot.application.pr_channel_service import CreateChannelCommand, UpdateChannelCommand
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrChannel, PrPlatform
from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.db.models.user import User
from meobot.domain.pr.channel_connections import metrics_status_for
from meobot.domain.pr.channel_metrics import (
    PrChannelMetricsStatus,
    PrChannelPlatform,
    follower_trend,
    platform_from_code,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.labels import channel_platform_label
from meobot.domain.pr.models import PrChannelCategory, PrEntityStatus
from meobot.domain.pr.reporting import PrMetricSource
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

#: A capture time comfortably in the past, relative to whenever the suite runs.
#:
#: Not a fixed calendar date: ``captured_at`` is bounded against the real clock -
#: a reading dated next week is refused - so a hard-coded 2026 timestamp would
#: pass today and start failing on its own the day the machine's date moves past
#: it. Every other moment in this file is derived from it.
NOW = utcnow().replace(microsecond=0) - timedelta(days=1)


# --- Helpers ----------------------------------------------------------------


async def platform(world: World, code: str, *, name: str | None = None) -> PrPlatform:
    """A registered platform, created the way the admin screen creates one."""
    row = PrPlatform(code=code, name=name or code.title())
    world.session.add(row)
    await world.session.flush()
    return row


async def channel(
    world: World,
    *,
    platform_code: str = "TIKTOK",
    name: str = "Dr Tiến - Tân trang cô bé",
    handle: str | None = None,
    actor: User | None = None,
) -> PrChannel:
    existing = await world.session.execute(
        select(PrPlatform).where(PrPlatform.code == platform_code).limit(1)
    )
    row = existing.scalar_one_or_none() or await platform(world, platform_code)
    return await world.services.channels.create_channel(
        actor=world.actor(actor or world.owner),
        request_id=world.request_id,
        command=CreateChannelCommand(
            name=name,
            platform_id=row.id,
            category=PrChannelCategory.SCALE,
            handle=handle,
        ),
    )


async def record(
    world: World,
    channel_id: uuid.UUID,
    *,
    at: datetime = NOW,
    actor: User | None = None,
    **metrics: Any,
) -> PrChannelMetricSnapshot:
    return await world.services.channel_metrics.record_manual_snapshot(
        actor=world.actor(actor or world.owner),
        request_id=world.request_id,
        command=RecordChannelMetricsCommand(channel_id=channel_id, captured_at=at, **metrics),
    )


async def describe(
    world: World, channel_id: uuid.UUID, *, actor: User | None = None, **kwargs: int
) -> ChannelMetricsView:
    return await world.services.channel_metrics.describe(
        actor=world.actor(actor or world.owner), channel_id=channel_id, **kwargs
    )


async def audit_actions(world: World) -> list[str]:
    result = await world.session.execute(select(AuditLog.action))
    return [str(row[0]) for row in result.all()]


def as_utc(moment: datetime) -> datetime:
    """One timestamp, comparable on both databases the suite runs against.

    PostgreSQL hands back an aware datetime and SQLite - which the offline suite
    builds its schema on - hands back a naive one for the same column. The rows
    are written in UTC either way, so this reattaches the zone rather than
    dropping it from the expected value.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# ===========================================================================
# 1-8: PLATFORM IDENTITY
# ===========================================================================


async def test_01_a_legacy_platform_code_has_no_canonical_platform(world: World) -> None:
    """Requirement 1, adapted to a schema where ``platform_id`` was never null.

    ``pr_channels.platform_id`` has always been ``NOT NULL``, so the thing a
    legacy channel can lack is not a platform *row* - it is a place in the
    canonical vocabulary. A channel registered on ``FB_VN`` maps to nothing, and
    *Chưa xác định* is the honest answer rather than a guess at Facebook.
    """
    row = await channel(world, platform_code="FB_VN", name="Trang cũ")
    detail = await world.services.queries.get_channel(
        actor=world.actor(world.owner), channel_id=row.id
    )

    assert detail.platform_code == "FB_VN"
    assert platform_from_code(detail.platform_code) is None
    assert channel_platform_label(None) == "Chưa xác định"
    # And the channel is entirely usable: nothing is blocked by not knowing.
    assert row.status.value == "ACTIVE"


async def test_02_a_new_channel_still_requires_a_platform(world: World) -> None:
    """Requirement 2. Creating one against an id nobody registered is refused."""
    with pytest.raises(PrNotFoundError):
        await world.services.channels.create_channel(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            command=CreateChannelCommand(
                name="Kênh mới",
                platform_id=uuid.uuid4(),
                category=PrChannelCategory.SCALE,
            ),
        )


@pytest.mark.parametrize(
    "code",
    ["FACEBOOK", "INSTAGRAM", "TIKTOK", "YOUTUBE", "WEBSITE", "OTHER"],
)
async def test_03_every_supported_platform_is_accepted(world: World, code: str) -> None:
    """Requirement 3. All six canonical codes resolve to their own member."""
    resolved = platform_from_code(code)
    assert resolved is not None
    assert resolved.value == code
    assert channel_platform_label(resolved) != "Chưa xác định"


@pytest.mark.parametrize("code", ["FACEBOOK_PAGE", "YOUTUBE_SHORTS", "ZALO", "", None, "  "])
async def test_04_an_unknown_platform_is_not_guessed_at(code: str | None) -> None:
    """Requirement 4. Never a near match, never an inference.

    ``FACEBOOK_PAGE`` is not Facebook here on purpose: the code is the token the
    policy system compares byte for byte, and deciding that a near miss counts
    would be inferring a legal obligation from a string somebody typed.
    """
    assert platform_from_code(code) is None


async def test_05_a_handle_is_optional_and_kept_exactly_as_written(world: World) -> None:
    """Requirement 5. No ``@`` added, none stripped, and blank means absent."""
    without = await channel(world, name="Không handle")
    assert without.handle is None

    with_at = await channel(world, name="Có handle", handle="@drtien")
    assert with_at.handle == "@drtien"

    without_at = await channel(world, name="Không @", handle="apexmed")
    assert without_at.handle == "apexmed"

    blank = await channel(world, name="Trắng", handle="   ")
    assert blank.handle is None, "whitespace is not a handle"


async def test_06_existing_url_and_external_id_rules_are_untouched(world: World) -> None:
    """Requirement 6. Step 1F.2.4a reused both columns and added neither.

    ``url`` is still the thing a person clicks and is still never a key;
    ``external_id`` is still the platform's own identifier. No second URL column
    and no ``external_account_id`` exist on the table.
    """
    columns = set(PrChannel.__table__.columns.keys())
    assert "url" in columns
    assert "external_id" in columns
    assert "handle" in columns
    assert "profile_url" not in columns
    assert "external_account_id" not in columns
    assert "platform" not in columns, "the canonical platform is derived, not stored twice"


async def test_07_a_manager_may_move_a_channel_to_another_platform(world: World) -> None:
    """Requirement 7. And the readings taken under the old one stay put."""
    row = await channel(world, platform_code="FB_VN", name="Trang cũ")
    await record(world, row.id, followers=1000)
    facebook = await platform(world, "FACEBOOK")

    await world.services.channels.update_channel(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=UpdateChannelCommand(channel_id=row.id, platform_id=facebook.id),
    )
    await world.session.refresh(row)
    assert row.platform_id == facebook.id

    # Requirement: changing the platform rewrites no history.
    view = await describe(world, row.id)
    assert view.total == 1
    assert view.latest is not None
    assert view.latest.snapshot.followers == 1000

    # And the change is in the audit trail on both sides.
    logs = await world.session.execute(
        select(AuditLog).where(AuditLog.action == "pr.channel.updated")
    )
    updated = list(logs.scalars().all())
    assert updated, "a platform change must be auditable"
    assert updated[-1].before_data is not None and updated[-1].after_data is not None
    assert updated[-1].before_data["platform_id"] != updated[-1].after_data["platform_id"]
    assert updated[-1].after_data["platform_id"] == str(facebook.id)


async def test_08_an_ordinary_member_cannot_change_a_platform(world: World) -> None:
    """Requirement 8. Editing a channel is management, and always was."""
    row = await channel(world)
    other = await platform(world, "YOUTUBE")
    with pytest.raises(PrPermissionDeniedError):
        await world.services.channels.update_channel(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=UpdateChannelCommand(channel_id=row.id, platform_id=other.id),
        )


async def test_08a_a_channel_cannot_be_moved_onto_a_retired_platform(world: World) -> None:
    """The Step 1F.2.1 rule, applied to the new path as well as to create.

    Retiring a platform and then finding channels being *moved* onto it would
    make the status a note rather than a decision, exactly as it would for a
    create - so both paths ask the same method.
    """
    row = await channel(world)
    retired = await platform(world, "YOUTUBE")
    retired.status = PrEntityStatus.INACTIVE
    await world.session.flush()

    with pytest.raises(PrValidationError) as failure:
        await world.services.channels.update_channel(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            command=UpdateChannelCommand(channel_id=row.id, platform_id=retired.id),
        )
    assert failure.value.details["reason"] == "platform_inactive"


# ===========================================================================
# 9-18: RECORDING A SNAPSHOT
# ===========================================================================


async def test_09_a_manager_may_record_a_manual_snapshot(world: World) -> None:
    """Requirement 9."""
    row = await channel(world)
    snapshot = await record(world, row.id, followers=124812, views_30d=3102441)

    assert snapshot.channel_id == row.id
    assert snapshot.followers == 124812
    assert snapshot.views_30d == 3102441
    assert snapshot.observed_at == NOW


async def test_10_an_ordinary_member_may_not(world: World) -> None:
    """Requirement 10. Reading is open; writing is ``PR_CHANNEL_MANAGE``."""
    row = await channel(world)
    with pytest.raises(PrPermissionDeniedError):
        await record(world, row.id, actor=world.member, followers=1)

    # And the same person may still *read* the channel's metrics.
    view = await describe(world, row.id, actor=world.member)
    assert view.status is PrChannelMetricsStatus.DISCONNECTED
    assert view.can_record_metrics is False


async def test_11_at_least_one_metric_is_required(world: World) -> None:
    """Requirement 11. An empty reading records that a form was opened."""
    row = await channel(world)
    with pytest.raises(PrValidationError) as failure:
        await record(world, row.id)
    assert "ít nhất một chỉ số" in failure.value.message


async def test_12_zero_is_a_measurement_and_is_accepted(world: World) -> None:
    """Requirement 12. And it satisfies "at least one" on its own.

    This is the test that makes ``all(value is None …)`` rather than
    ``any(value)`` the right check: a channel really can have zero shares in a
    month, and refusing to record that would make a real month unrecordable.
    """
    row = await channel(world)
    snapshot = await record(world, row.id, shares_30d=0)
    assert snapshot.shares_30d == 0
    assert snapshot.followers is None


async def test_13_a_negative_metric_is_refused(world: World) -> None:
    """Requirement 13."""
    row = await channel(world)
    with pytest.raises(PrValidationError) as failure:
        await record(world, row.id, followers=-1)
    assert failure.value.details["field"] == "followers"


async def test_14_a_future_capture_time_is_refused(world: World) -> None:
    """Requirement 14. Nobody has read next week's follower count."""
    row = await channel(world)
    with pytest.raises(PrValidationError) as failure:
        await record(world, row.id, at=datetime.now(UTC) + timedelta(days=2), followers=1)
    assert failure.value.details["field"] == "captured_at"

    # A clock a couple of minutes fast is not a mistake, and is accepted.
    snapshot = await record(world, row.id, at=datetime.now(UTC) + timedelta(minutes=2), followers=1)
    assert snapshot.followers == 1


async def test_15_a_historical_capture_time_is_accepted(world: World) -> None:
    """Requirement 15. Backdating is normal, not suspicious."""
    row = await channel(world)
    long_ago = NOW - timedelta(days=180)
    snapshot = await record(world, row.id, at=long_ago, followers=1000)
    assert snapshot.observed_at == long_ago


async def test_16_the_source_is_recorded_as_manual(world: World) -> None:
    """Requirement 16. Written by the service, never taken from the caller."""
    row = await channel(world)
    snapshot = await record(world, row.id, followers=10)
    assert snapshot.source is PrMetricSource.MANUAL


async def test_17_the_recorder_is_persisted(world: World) -> None:
    """Requirement 17. "Ai nhập chỉ số này?" is answered by the row itself."""
    row = await channel(world)
    snapshot = await record(world, row.id, actor=world.owner, followers=10)
    assert snapshot.recorded_by_user_id == world.owner.id

    view = await describe(world, row.id)
    assert view.latest is not None
    assert view.latest.recorded_by_name == world.owner.full_name


async def test_18_recording_writes_an_audit_event_naming_the_actor(world: World) -> None:
    """Requirement 18. The payload names the row, not the numbers.

    Copying the figures into the audit trail would store them twice and give
    somebody two places to read the same reading from - and the snapshot table
    is already append-only, so it *is* the record of what was entered.
    """
    row = await channel(world)
    snapshot = await record(world, row.id, followers=124812, extra_metrics={"profile_views": 9})

    assert "pr.channel.metrics.recorded" in await audit_actions(world)
    entry = (
        await world.session.execute(select(AuditLog).where(AuditLog.entity_id == str(snapshot.id)))
    ).scalar_one()
    assert entry.actor_user_id == world.owner.id
    assert entry.entity_type == "pr_channel_metric_snapshot"
    assert entry.after_data is not None
    assert entry.after_data["channel_id"] == str(row.id)
    assert entry.after_data["snapshot_id"] == str(snapshot.id)
    assert entry.after_data["captured_at"] == NOW.isoformat()
    assert entry.after_data["source"] == "MANUAL"
    # The numbers themselves are deliberately absent - the snapshot row is
    # already the append-only record of what was entered.
    assert "followers" not in entry.after_data
    assert "extra_metrics" not in entry.after_data


# ===========================================================================
# 19-24: HISTORY
# ===========================================================================


async def test_19_a_second_snapshot_appends_rather_than_replacing(world: World) -> None:
    """Requirement 19. This is the whole append-only decision, as a test."""
    row = await channel(world)
    first = await record(world, row.id, at=NOW - timedelta(days=1), followers=100)
    second = await record(world, row.id, at=NOW, followers=120)

    total = (
        await world.session.execute(
            select(func.count())
            .select_from(PrChannelMetricSnapshot)
            .where(PrChannelMetricSnapshot.channel_id == row.id)
        )
    ).scalar_one()
    assert total == 2

    await world.session.refresh(first)
    assert first.followers == 100, "the earlier reading must not be rewritten"
    assert second.followers == 120


async def test_19a_a_correction_is_a_new_reading_not_an_edit(world: World) -> None:
    """The correction path, stated. A typo at 09:00 is fixed by recording again.

    And the one thing that cannot be appended: a second manual reading at the
    exact same instant. 0013's unique index refuses it; the service turns that
    into a sentence somebody can act on.
    """
    row = await channel(world)
    await record(world, row.id, at=NOW, followers=1248120)

    with pytest.raises(PrConflictError) as failure:
        await record(world, row.id, at=NOW, followers=124812)
    assert "ghi thêm" in failure.value.message

    corrected = await record(world, row.id, at=NOW + timedelta(minutes=1), followers=124812)
    view = await describe(world, row.id)
    assert view.total == 2, "the mistake stays; the fix is a new row"
    assert view.latest is not None
    assert view.latest.snapshot.id == corrected.id


async def test_20_latest_is_chosen_by_captured_at_not_by_insertion(world: World) -> None:
    """Requirement 20. A backfilled reading must not become the present."""
    row = await channel(world)
    await record(world, row.id, at=NOW, followers=120)
    # Written second, observed months earlier. Insertion order says it is
    # newest; ``observed_at`` says it is not, and ``observed_at`` is right.
    await record(world, row.id, at=NOW - timedelta(days=180), followers=100)

    view = await describe(world, row.id)
    assert view.latest is not None
    assert as_utc(view.latest.snapshot.observed_at) == NOW
    assert view.latest.snapshot.followers == 120


async def test_21_previous_is_the_second_row_of_the_same_ordering(world: World) -> None:
    """Requirement 21. Deterministic, and the same order the history uses."""
    row = await channel(world)
    for day, followers in ((3, 100), (2, 110), (1, 120)):
        await record(world, row.id, at=NOW - timedelta(days=day), followers=followers)

    view = await describe(world, row.id)
    assert view.latest is not None and view.previous is not None
    assert view.latest.snapshot.followers == 120
    assert view.previous.snapshot.followers == 110


async def test_22_history_ordering_is_newest_first_and_stable(world: World) -> None:
    """Requirement 22."""
    row = await channel(world)
    for day in range(5):
        await record(world, row.id, at=NOW - timedelta(days=day), followers=100 + day)

    view = await describe(world, row.id)
    moments = [as_utc(entry.snapshot.observed_at) for entry in view.history]
    assert moments == sorted(moments, reverse=True)
    # And asking twice returns the same order.
    again = await describe(world, row.id)
    assert [entry.snapshot.id for entry in again.history] == [
        entry.snapshot.id for entry in view.history
    ]


async def test_23_history_is_paginated_and_bounded(world: World) -> None:
    """Requirement 23. And the current pair survives paging past it."""
    row = await channel(world)
    for day in range(7):
        await record(world, row.id, at=NOW - timedelta(days=day), followers=100 + day)

    first = await describe(world, row.id, limit=3, offset=0)
    assert len(first.history) == 3
    assert first.total == 7
    assert first.limit == 3

    second = await describe(world, row.id, limit=3, offset=3)
    assert len(second.history) == 3
    assert {entry.snapshot.id for entry in first.history}.isdisjoint(
        {entry.snapshot.id for entry in second.history}
    )
    # Page 2 still knows what "now" is - the cards do not go blank when a person
    # pages back through the history.
    assert second.latest is not None
    assert second.latest.snapshot.id == first.latest.snapshot.id  # type: ignore[union-attr]

    # An absurd page size is capped rather than honoured.
    huge = await describe(world, row.id, limit=10_000)
    assert huge.limit <= 100
    assert DEFAULT_SNAPSHOT_PAGE == 30

    # And a page of one still knows what it is comparing against. The head of
    # the ordering is not the head of the page, and reading the trend off a
    # one-row page would silently lose it.
    single = await describe(world, row.id, limit=1)
    assert len(single.history) == 1
    assert single.previous is not None
    assert single.trend is not None


async def test_24_a_null_metric_stays_null(world: World) -> None:
    """Requirement 24. Absent is not zero, all the way down to the column."""
    row = await channel(world)
    snapshot = await record(world, row.id, followers=100)

    assert snapshot.reach_30d is None
    assert snapshot.impressions_7d is None
    view = await describe(world, row.id)
    assert view.latest is not None
    assert view.latest.snapshot.reach_30d is None


# ===========================================================================
# 25-30: TREND
# ===========================================================================


async def test_25_a_follower_delta_is_computed(world: World) -> None:
    """Requirement 25."""
    row = await channel(world)
    await record(world, row.id, at=NOW - timedelta(days=1), followers=123392)
    await record(world, row.id, at=NOW, followers=124812)

    view = await describe(world, row.id)
    assert view.trend is not None
    assert view.trend.delta == 1420
    assert view.trend.delta_pct == pytest.approx(1.2, abs=0.05)


@pytest.mark.parametrize(
    ("before", "after", "delta"),
    [(100, 120, 20), (120, 100, -20), (100, 100, 0)],
)
async def test_26_27_28_positive_negative_and_zero_deltas(
    world: World, before: int, after: int, delta: int
) -> None:
    """Requirements 26, 27 and 28. A channel that lost followers lost them."""
    row = await channel(world, name=f"Kênh {before}-{after}")
    await record(world, row.id, at=NOW - timedelta(days=1), followers=before)
    await record(world, row.id, at=NOW, followers=after)

    view = await describe(world, row.id)
    assert view.trend is not None
    assert view.trend.delta == delta


async def test_29_the_percentage_is_omitted_when_the_previous_was_zero() -> None:
    """Requirement 29. The change from nothing is not a number.

    A unit of the domain function rather than a database round trip, because
    what is being asserted is arithmetic and nothing else.
    """
    trend = follower_trend(500, 0)
    assert trend is not None
    assert trend.delta == 500
    assert trend.delta_pct is None, "no +∞%, and no substituted 100%"


async def test_30_one_snapshot_produces_no_trend_at_all(world: World) -> None:
    """Requirement 30. Not a zero trend - no trend."""
    row = await channel(world)
    await record(world, row.id, followers=100)

    view = await describe(world, row.id)
    assert view.previous is None
    assert view.trend is None

    # And a reading that left ``followers`` blank cannot be compared either.
    assert follower_trend(None, 100) is None
    assert follower_trend(100, None) is None


# ===========================================================================
# 31-33: DERIVED STATUS
# ===========================================================================


async def test_31_no_snapshot_means_disconnected(world: World) -> None:
    """Requirement 31."""
    row = await channel(world)
    view = await describe(world, row.id)
    assert view.status is PrChannelMetricsStatus.DISCONNECTED
    assert view.latest is None
    assert view.has_history is False


async def test_32_a_snapshot_means_manual(world: World) -> None:
    """Requirement 32."""
    row = await channel(world)
    await record(world, row.id, followers=1)
    view = await describe(world, row.id)
    assert view.status is PrChannelMetricsStatus.MANUAL
    assert view.has_history is True


async def test_33_a_channel_with_no_connection_never_claims_one(world: World) -> None:
    """Requirement 33, as Step 1F.2.4b left it.

    Step 1F.2.4a asserted this by reading the source for a hard-coded ``False``,
    because no connector existed and the claim was that none could. Step
    1F.2.4b built one, so the constant is gone and the claim is now the sharper
    one: the badge is derived from the channel's **own** connection, so a
    channel that has never been connected cannot show ``CONNECTED_API`` however
    many connectors the deployment has.

    The connected case is covered in ``test_pr_youtube_connector``, where there
    is a connection to derive from.
    """
    row = await channel(world)
    await record(world, row.id, followers=1)

    view = await describe(world, row.id)
    assert view.status is PrChannelMetricsStatus.MANUAL
    assert view.status is not PrChannelMetricsStatus.CONNECTED_API
    assert view.status is not PrChannelMetricsStatus.ACTION_REQUIRED

    # And the derivation refuses to invent a connection out of a snapshot.
    assert (
        metrics_status_for(connection_state=None, has_snapshot=True)
        is PrChannelMetricsStatus.MANUAL
    )
    assert (
        metrics_status_for(connection_state=None, has_snapshot=False)
        is PrChannelMetricsStatus.DISCONNECTED
    )


# ===========================================================================
# 34-38: THE SECURITY BOUNDARY
# ===========================================================================


async def test_34_a_client_cannot_supply_the_recorder(world: World) -> None:
    """Requirement 34. The field is not on the command or on the body.

    Two assertions, because they fail differently: the command has no such
    field to fill in, and the HTTP body model refuses one outright rather than
    ignoring it.
    """
    assert "recorded_by_user_id" not in RecordChannelMetricsCommand.__dataclass_fields__

    row = await channel(world)
    world.act_as(world.owner)
    response = world.client.post(
        f"/api/pr/channels/{row.id}/metrics",
        json={
            "captured_at": NOW.isoformat(),
            "followers": 10,
            "recorded_by_user_id": str(world.member.id),
        },
    )
    assert response.status_code == 422, response.text


async def test_35_a_client_cannot_claim_its_typing_came_from_an_api(world: World) -> None:
    """Requirement 35. Manual-only means the source is not an input."""
    assert "source" not in RecordChannelMetricsCommand.__dataclass_fields__

    row = await channel(world)
    world.act_as(world.owner)
    response = world.client.post(
        f"/api/pr/channels/{row.id}/metrics",
        json={"captured_at": NOW.isoformat(), "followers": 10, "source": "API"},
    )
    assert response.status_code == 422, response.text

    accepted = world.client.post(
        f"/api/pr/channels/{row.id}/metrics",
        json={"captured_at": NOW.isoformat(), "followers": 10},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["latest"]["source"] == "MANUAL"


async def test_36_a_channel_id_that_does_not_exist_is_refused(world: World) -> None:
    """Requirement 36."""
    with pytest.raises(PrNotFoundError):
        await record(world, uuid.uuid4(), followers=1)
    with pytest.raises(PrNotFoundError):
        await describe(world, uuid.uuid4())


async def test_37_the_permission_boundary_holds_over_http(world: World) -> None:
    """Requirement 37. The refusal is the API's, not the panel's."""
    row = await channel(world)
    world.act_as(world.member)

    refused = world.client.post(
        f"/api/pr/channels/{row.id}/metrics",
        json={"captured_at": NOW.isoformat(), "followers": 10},
    )
    assert refused.status_code == 403, refused.text

    # Reading is open to the same person, and says they may not write.
    readable = world.client.get(f"/api/pr/channels/{row.id}/metrics")
    assert readable.status_code == 200
    assert readable.json()["can_record_metrics"] is False


async def test_38_a_hidden_button_is_not_the_authorization(world: World) -> None:
    """Requirement 38. The flag and the write agree, and the write decides.

    The flag going missing would hide a control somebody may use; the flag going
    wrong must not let somebody through. So both are asserted against the same
    capability, from the same session.
    """
    row = await channel(world)

    world.act_as(world.owner)
    manager = world.client.get(f"/api/pr/channels/{row.id}").json()
    assert manager["can_record_metrics"] is True
    assert manager["can_edit_channel"] is True
    assert manager["can_manage_assignments"] is True

    world.act_as(world.member)
    member = world.client.get(f"/api/pr/channels/{row.id}").json()
    assert member["can_record_metrics"] is False
    # And pressing the button they were not shown still fails.
    assert (
        world.client.post(
            f"/api/pr/channels/{row.id}/metrics",
            json={"captured_at": NOW.isoformat(), "followers": 1},
        ).status_code
        == 403
    )


# ===========================================================================
# 39-41: THE QUERY SHAPES
# ===========================================================================


async def test_39_the_channel_list_reads_every_badge_in_one_query(world: World) -> None:
    """Requirement 39. Not one query per card.

    Ten channels, five of them measured, and one statement answers all ten. The
    assertion is on the count of statements the projection issues, because "no
    N+1" is a claim about the number of round trips and nothing else.
    """
    rows = [await channel(world, name=f"Kênh {index}") for index in range(10)]
    for row in rows[:5]:
        await record(world, row.id, followers=100)

    statements: list[str] = []
    original = world.session.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        statements.append(str(statement))
        return await original(statement, *args, **kwargs)

    world.session.execute = counting  # type: ignore[method-assign]
    try:
        summaries = await world.services.channel_metrics.summaries_for_channels(
            [row.id for row in rows]
        )
    finally:
        world.session.execute = original  # type: ignore[method-assign]

    # Two statements for ten channels: one window function over the snapshots,
    # one ``IN`` over the connections. Step 1F.2.4b added the second and it is
    # still per **page** rather than per card - which is the claim. The number
    # is asserted as a constant, and the test below proves it does not grow
    # with the channel count.
    assert len(statements) == 2, statements
    assert len(summaries) == 10
    measured = [s for s in summaries.values() if s.status is PrChannelMetricsStatus.MANUAL]
    assert len(measured) == 5
    assert all(s.followers == 100 for s in measured)
    unmeasured = [s for s in summaries.values() if s.followers is None]
    assert len(unmeasured) == 5
    assert all(s.status is PrChannelMetricsStatus.DISCONNECTED for s in unmeasured)


async def test_39a_the_badge_query_cost_does_not_grow_with_the_page(world: World) -> None:
    """The other half of "no N+1", and the half that actually rots.

    A fixed count for ten channels proves nothing on its own - a per-card query
    would also be "constant" if somebody only ever tested with one card. What
    makes it a real claim is that three channels and thirty cost the same, which
    is only true of a projection that fans out in SQL rather than in Python.
    """

    async def statements_for(count: int) -> int:
        rows = [await channel(world, name=f"Đo {count}-{index}") for index in range(count)]
        seen: list[str] = []
        original = world.session.execute

        async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
            seen.append(str(statement))
            return await original(statement, *args, **kwargs)

        world.session.execute = counting  # type: ignore[method-assign]
        try:
            await world.services.channel_metrics.summaries_for_channels([r.id for r in rows])
        finally:
            world.session.execute = original  # type: ignore[method-assign]
        return len(seen)

    assert await statements_for(3) == await statements_for(30)


async def test_40_the_detail_history_is_bounded(world: World) -> None:
    """Requirement 40. A channel measured daily for a year is still one page."""
    row = await channel(world)
    for day in range(40):
        await record(world, row.id, at=NOW - timedelta(days=day), followers=100 + day)

    view = await describe(world, row.id)
    assert view.total == 40
    assert len(view.history) == DEFAULT_SNAPSHOT_PAGE


async def test_41_recorder_names_are_resolved_without_asking_per_row(world: World) -> None:
    """Requirement 41. One join for a whole page, whoever appears on it.

    And a person who has left is still named: the join has no status filter,
    because who typed it does not change when they leave.
    """
    row = await channel(world)
    # The two people who hold ``PR_CHANNEL_MANAGE`` by role. It is not a
    # grantable capability - it comes from ``settings.write``, which OWNER and
    # ADMIN hold and TEAM_LEAD does not - so the page is built from the people
    # who really may write, rather than from a grant the service would refuse.
    people = [world.owner, world.head]
    for index, person in enumerate(people):
        await record(world, row.id, at=NOW - timedelta(days=index), actor=person, followers=index)

    names = {entry.recorded_by_name for entry in (await describe(world, row.id)).history}
    assert names == {person.full_name for person in people}


async def test_42_this_module_reaches_no_platform(world: World) -> None:
    """The claim the whole step rests on, asserted where it cannot rot.

    Not "no connector was called during this test" - that would prove only that
    the tested paths do not call one. This reads the service's own imports and
    says there is no HTTP client in the file at all.
    """
    from pathlib import Path

    import meobot.application.pr_channel_metrics_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "aiohttp", "graph.facebook", "googleapis", "urlopen"):
        assert forbidden not in source, forbidden

    import meobot.domain.pr.channel_metrics as domain

    domain_source = Path(domain.__file__).read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "aiohttp"):
        assert forbidden not in domain_source, forbidden
    # The port is there, and nothing implements it.
    assert "class ChannelMetricsProvider(Protocol)" in domain_source
    for absent in ("FacebookProvider", "TikTokProvider", "YouTubeProvider", "InstagramProvider"):
        assert absent not in domain_source, absent


def test_43_the_platform_vocabulary_stays_top_level_only() -> None:
    """Six networks, and no subtype creeping in.

    A guard rather than a restatement: the failure this stops is somebody adding
    ``FACEBOOK_PAGE`` because one screen wanted it, at which point the code stops
    being the token the policy system matches on.
    """
    assert {member.value for member in PrChannelPlatform} == {
        "FACEBOOK",
        "INSTAGRAM",
        "TIKTOK",
        "YOUTUBE",
        "WEBSITE",
        "OTHER",
    }
    for member in PrChannelPlatform:
        assert member.name == member.value, "a rename must not be silent"


def test_44_the_source_vocabulary_is_step_1bs_own() -> None:
    """No second source enum was invented. ``MANUAL`` is the one that works."""
    assert {member.value for member in PrMetricSource} == {"API", "MANUAL", "IMPORT"}
