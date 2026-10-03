"""The PR reporting foundation, on a database built the way production builds one.

Every rule Step 1B promises is a *PostgreSQL* rule: a partial unique index, a
``CHECK`` spanning two nullable columns, an ``ON DELETE RESTRICT``, a server
default. None of them can be proved by the offline SQLite fixture, and the
0.6.0a3 incident is the standing reminder of what happens when a schema is only
ever checked against the models that generated it. So this file does not use
``Base.metadata.create_all`` at all: it creates an empty database, runs the real
Alembic chain through head, and writes rows the way an application would.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and the row builders ``tests/integration/test_pr_core_migrations`` added, so the
Step 1A tables these nine hang off are built exactly once, in one place.

Run it against a PostgreSQL you are willing to have scratch databases created
in and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_reporting_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_prrep_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, NamedTuple

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from meobot.db.base import Base
from meobot.db.models.pr_reporting import (
    CHANNEL_METRIC_COLUMNS,
    POST_METRIC_COLUMNS,
    WEEKLY_INPUT_SIGNED_COLUMNS,
    WEEKLY_INPUT_VALUE_COLUMNS,
    PrAction,
    PrChannelMetricSnapshot,
    PrIssue,
    PrPostMetricSnapshot,
    PrPublication,
    PrReportArtifact,
    PrReportingPeriod,
    PrReportRun,
    PrWeeklyManualInput,
)
from meobot.db.session import Database
from meobot.domain.pr.reporting import (
    PrActionStatus,
    PrArtifactFormat,
    PrIssueSeverity,
    PrIssueStatus,
    PrMetricSource,
    PrPeriodType,
    PrReportRunStatus,
    PrReportTriggerType,
    PrReportType,
)
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    downgrade_to,
    upgrade_to,
)
from tests.integration.test_pr_core_migrations import (
    _make_channel,
    _make_content,
    _make_user,
    _scratch_database,
)

# The migrated database is built once for the whole module - thirteen revisions
# is too much to pay per test - so every coroutine here has to run on the same
# event loop the fixture was created on. ``loop_scope="module"`` is what pins
# that; without it pytest-asyncio gives each test a fresh loop and the shared
# connection pool belongs to a loop that has already closed.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

#: The nine tables revision 0013 creates, in dependency order.
REPORTING_TABLES: tuple[str, ...] = (
    "pr_reporting_periods",
    "pr_publications",
    "pr_post_metric_snapshots",
    "pr_channel_metric_snapshots",
    "pr_weekly_manual_inputs",
    "pr_issues",
    "pr_actions",
    "pr_report_runs",
    "pr_report_artifacts",
)

#: Step 1A's eleven, which the 0013 downgrade must leave standing.
STEP_1A_TABLES: frozenset[str] = frozenset(
    {
        "pr_brands",
        "pr_platforms",
        "pr_content_formats",
        "pr_content_pillars",
        "pr_channels",
        "pr_channel_assignments",
        "pr_content_items",
        "pr_content_targets",
        "pr_tasks",
        "pr_task_assignments",
        "pr_approval_events",
    }
)

WEEK_START = date(2026, 8, 3)
WEEK_END = date(2026, 8, 9)
OBSERVED = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 8, 3, 19, 4, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def reporting_database() -> AsyncIterator[Database]:
    """One migrated database shared by the constraint tests.

    Module-scoped because migrating a blank database runs thirteen revisions,
    and the constraint tests are independent: each uses its own codes and its
    own rows, and the failures they provoke are rolled back by
    :meth:`Database.transaction`.
    """
    async for database in _scratch_database("meobot_prrep"):
        yield database


@pytest_asyncio.fixture(loop_scope="module")
async def fresh_reporting_database() -> AsyncIterator[Database]:
    """A migrated database of its own, for tests that move the schema."""
    async for database in _scratch_database("meobot_prrepmig"):
        yield database


# --- Row builders -----------------------------------------------------------


class Sample(NamedTuple):
    """One of everything the reporting tables hang off, built once."""

    user_id: uuid.UUID
    channel_id: uuid.UUID
    brand_id: uuid.UUID
    content_id: uuid.UUID
    period_id: uuid.UUID
    publication_id: uuid.UUID


async def _make_period(
    database: Database,
    code: str,
    *,
    period_type: PrPeriodType = PrPeriodType.WEEK,
    start: date = WEEK_START,
    end: date = WEEK_END,
    previous_period_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with database.transaction() as session:
        period = PrReportingPeriod(
            code=code,
            period_type=period_type,
            date_start=start,
            date_end=end,
            previous_period_id=previous_period_id,
        )
        session.add(period)
        await session.flush()
        return period.id


async def _make_publication(
    database: Database,
    code: str,
    content_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    platform_post_id: str | None = None,
    published_at: datetime = PUBLISHED,
) -> uuid.UUID:
    async with database.transaction() as session:
        publication = PrPublication(
            code=code,
            content_id=content_id,
            channel_id=channel_id,
            platform_post_id=platform_post_id,
            published_at=published_at,
        )
        session.add(publication)
        await session.flush()
        return publication.id


async def _make_issue(database: Database, code: str) -> uuid.UUID:
    async with database.transaction() as session:
        issue = PrIssue(
            code=code,
            title=f"Issue {code}",
            severity=PrIssueSeverity.MEDIUM,
            status=PrIssueStatus.OPEN,
        )
        session.add(issue)
        await session.flush()
        return issue.id


async def _make_run(
    database: Database,
    period_id: uuid.UUID,
    key: str,
    *,
    report_type: PrReportType = PrReportType.WEEKLY_MANAGEMENT,
) -> uuid.UUID:
    async with database.transaction() as session:
        run = PrReportRun(
            report_type=report_type,
            period_id=period_id,
            idempotency_key=key,
            template_version="v1",
            trigger_type=PrReportTriggerType.MANUAL,
            status=PrReportRunStatus.PENDING,
            source_cutoff_at=OBSERVED,
        )
        session.add(run)
        await session.flush()
        return run.id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sample(reporting_database: Database) -> Sample:
    """A channel, a content item, a period and a publication, built once."""
    user_id = await _make_user(reporting_database, "Reporting person")
    channel_id, brand_id = await _make_channel(reporting_database, "CH-REP")
    content_id = await _make_content(reporting_database, "CNT-REP-0001", brand_id)
    period_id = await _make_period(reporting_database, "2026-W32")
    publication_id = await _make_publication(
        reporting_database, "PUB-REP-0001", content_id, channel_id, platform_post_id="post-rep-1"
    )
    return Sample(user_id, channel_id, brand_id, content_id, period_id, publication_id)


# --- 1, 2, 3, 4: the migration goes up, comes back down, and matches --------


async def test_the_migration_chain_reaches_head_with_every_reporting_table(
    reporting_database: Database,
) -> None:
    """Requirement 1."""
    async with reporting_database.session() as session:
        stamped = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        present = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name LIKE 'pr\\_%'"
                    )
                )
            ).all()
        }
    # The chain runs to head, not to 0013: later revisions sit on top of this
    # one - Step 1A1's 0014 is the first - and pinning the number here would
    # fail every time the module grows, which says nothing about Step 1B.
    assert stamped >= "0013"
    assert set(REPORTING_TABLES) <= present
    assert present >= STEP_1A_TABLES


async def test_downgrading_0013_removes_exactly_the_nine_tables(
    fresh_reporting_database: Database,
) -> None:
    """Requirements 2 and 4.

    Also checks what the downgrade must *not* do: Step 1A's eleven tables,
    ``users`` and the dispatch tables are untouched, because a reporting
    rollback that took the content module or the identity table with it would
    be catastrophic and silent.
    """
    dsn = str(fresh_reporting_database._settings.database_url)

    async def table_names() -> set[str]:
        async with fresh_reporting_database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            )
            return {row[0] for row in rows.all()}

    # Step back to 0013 first, so that what this test measures is what *0013's*
    # downgrade removes rather than everything later revisions piled on top.
    # Without this the scratch database is at head, and Step 1A1's
    # ``pr_ai_reviews`` would be counted against 0013.
    await downgrade_to(dsn, "0013")

    before = await table_names()
    assert set(REPORTING_TABLES) <= before

    await downgrade_to(dsn, "0012")
    after = await table_names()

    # Exactly nine tables disappeared, and they are the nine this step added.
    assert before - after == set(REPORTING_TABLES)
    assert len(before - after) == 9
    assert after & set(REPORTING_TABLES) == set()
    assert after >= STEP_1A_TABLES, "the downgrade removed a Step 1A table"
    assert "users" in after
    assert "message_dispatches" in after

    # And it goes back up cleanly, so 0013 is not a one-way door.
    await upgrade_to(dsn, "head")
    assert set(REPORTING_TABLES) <= await table_names()


async def test_the_models_and_the_migration_describe_the_same_schema(
    reporting_database: Database,
) -> None:
    """Requirement 3, using Alembic's own comparator rather than a hand list.

    Restricted to the PR tables: this repository has pre-existing drift between
    older models and older migrations, and widening this assertion would make it
    fail for reasons Step 1B did not cause and must not fix.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(connection: Any) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with reporting_database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    pr_differences = [difference for difference in differences if "pr_" in str(difference)]
    assert pr_differences == [], pr_differences


# --- 5 and 6: reporting periods --------------------------------------------


async def test_a_period_cannot_end_before_it_starts(reporting_database: Database) -> None:
    """Requirement 5."""
    with pytest.raises(IntegrityError):
        await _make_period(
            reporting_database,
            "2026-W99-BACKWARDS",
            start=date(2026, 8, 9),
            end=date(2026, 8, 3),
        )


async def test_a_single_day_period_is_accepted(reporting_database: Database) -> None:
    """Requirement 5, the accepting half: ``date_end >= date_start``, not ``>``."""
    period_id = await _make_period(
        reporting_database, "2026-D001", start=date(2026, 8, 3), end=date(2026, 8, 3)
    )
    assert period_id is not None


async def test_a_month_period_is_accepted(reporting_database: Database) -> None:
    period_id = await _make_period(
        reporting_database,
        "2026-08",
        period_type=PrPeriodType.MONTH,
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
    )
    assert period_id is not None


async def test_a_period_cannot_be_its_own_predecessor(reporting_database: Database) -> None:
    """Requirement 6, at insert time.

    Written as raw SQL because the id is generated client-side, which is the
    only way a caller could set ``previous_period_id`` to it in one statement.
    """
    period_id = uuid.uuid4()
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_reporting_periods "
                    "(id, code, period_type, date_start, date_end, previous_period_id) "
                    "VALUES (:id, :code, 'WEEK', :start, :end, :id)"
                ),
                {"id": period_id, "code": "2026-W-SELF", "start": WEEK_START, "end": WEEK_END},
            )


async def test_a_period_cannot_be_updated_to_point_at_itself(
    reporting_database: Database,
) -> None:
    """Requirement 6, at update time - the way it would actually happen."""
    period_id = await _make_period(reporting_database, "2026-W-SELFUPD")
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(
                text("UPDATE pr_reporting_periods SET previous_period_id = id WHERE id = :id"),
                {"id": period_id},
            )


async def test_a_period_may_point_at_the_one_before_it(reporting_database: Database) -> None:
    """The chain a week-on-week comparison walks."""
    first = await _make_period(reporting_database, "2026-W30")
    second = await _make_period(reporting_database, "2026-W31", previous_period_id=first)
    async with reporting_database.session() as session:
        stored = (
            await session.execute(
                text("SELECT previous_period_id FROM pr_reporting_periods WHERE id = :id"),
                {"id": second},
            )
        ).scalar_one()
    assert stored == first


async def test_a_duplicate_period_code_is_refused(reporting_database: Database) -> None:
    await _make_period(reporting_database, "2026-W40")
    with pytest.raises(IntegrityError):
        await _make_period(reporting_database, "2026-W40")


async def test_a_blank_period_code_is_refused(reporting_database: Database) -> None:
    """Whitespace is exactly as useless as an empty string."""
    with pytest.raises(IntegrityError):
        await _make_period(reporting_database, "   ")


# --- 7, 8, 9: publications --------------------------------------------------


async def test_one_content_item_may_have_many_publications(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 7.

    One script becomes a TikTok cut and a YouTube cut, and each is published on
    its own schedule with its own metrics. Nothing is unique on ``content_id``.
    """
    other_channel, _ = await _make_channel(reporting_database, "CH-REP-MANY")
    first = await _make_publication(
        reporting_database, "PUB-MANY-1", sample.content_id, sample.channel_id
    )
    second = await _make_publication(
        reporting_database, "PUB-MANY-2", sample.content_id, other_channel
    )
    async with reporting_database.session() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_publications WHERE content_id = :id"),
                {"id": sample.content_id},
            )
        ).scalar_one()
    assert first != second
    assert count >= 2


async def test_the_same_content_may_be_published_twice_to_one_channel(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 7, the case a ``(content, channel)`` unique key would break.

    A repost, a corrected re-upload and a scheduled repeat are all real, and
    each has its own metrics. What may only exist once is a *post on a
    platform*, not an appearance of an idea.
    """
    await _make_publication(
        reporting_database,
        "PUB-REPOST-1",
        sample.content_id,
        sample.channel_id,
        platform_post_id="repost-a",
        published_at=PUBLISHED,
    )
    await _make_publication(
        reporting_database,
        "PUB-REPOST-2",
        sample.content_id,
        sample.channel_id,
        platform_post_id="repost-b",
        published_at=PUBLISHED + timedelta(days=7),
    )
    async with reporting_database.session() as session:
        count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM pr_publications "
                    "WHERE content_id = :content AND channel_id = :channel"
                ),
                {"content": sample.content_id, "channel": sample.channel_id},
            )
        ).scalar_one()
    assert count >= 2


async def test_a_duplicate_platform_post_id_on_one_channel_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 8. One post on one platform is one row."""
    await _make_publication(
        reporting_database,
        "PUB-DUP-1",
        sample.content_id,
        sample.channel_id,
        platform_post_id="platform-post-dup",
    )
    with pytest.raises(IntegrityError):
        await _make_publication(
            reporting_database,
            "PUB-DUP-2",
            sample.content_id,
            sample.channel_id,
            platform_post_id="platform-post-dup",
        )


async def test_the_same_platform_post_id_on_two_channels_is_accepted(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 9.

    Platforms do not coordinate their id spaces. A TikTok id and a Facebook id
    that happen to be the same string name two unrelated posts, and a unique
    index on the id alone would refuse the second one.
    """
    other_channel, _ = await _make_channel(reporting_database, "CH-REP-OTHER")
    await _make_publication(
        reporting_database,
        "PUB-SHARED-1",
        sample.content_id,
        sample.channel_id,
        platform_post_id="shared-id-42",
    )
    await _make_publication(
        reporting_database,
        "PUB-SHARED-2",
        sample.content_id,
        other_channel,
        platform_post_id="shared-id-42",
    )
    async with reporting_database.session() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_publications WHERE platform_post_id = :pid"),
                {"pid": "shared-id-42"},
            )
        ).scalar_one()
    assert count == 2


async def test_many_publications_without_a_platform_post_id_coexist(
    reporting_database: Database, sample: Sample
) -> None:
    """Why the index is partial.

    A publication is recorded long before anybody fetches its platform id back.
    A plain unique index on ``(channel_id, platform_post_id)`` would let exactly
    one such row exist per channel, because in PostgreSQL two NULLs do not
    collide - but ``NULLS NOT DISTINCT`` and a future change of mind would, and
    the partial predicate says what is meant rather than relying on that.
    """
    for suffix in range(3):
        await _make_publication(
            reporting_database,
            f"PUB-NOPID-{suffix}",
            sample.content_id,
            sample.channel_id,
            platform_post_id=None,
        )
    async with reporting_database.session() as session:
        count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM pr_publications "
                    "WHERE channel_id = :channel AND platform_post_id IS NULL"
                ),
                {"channel": sample.channel_id},
            )
        ).scalar_one()
    assert count >= 3


async def test_a_duplicate_publication_code_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    await _make_publication(
        reporting_database, "PUB-CODE-DUP", sample.content_id, sample.channel_id
    )
    with pytest.raises(IntegrityError):
        await _make_publication(
            reporting_database, "PUB-CODE-DUP", sample.content_id, sample.channel_id
        )


# --- 10, 11, 12, 13: metric snapshots ---------------------------------------


async def test_views_and_reach_are_stored_and_read_back_independently(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 10.

    Plays and people. Written as two different numbers and read back as two
    different numbers, on both snapshot tables.
    """
    async with reporting_database.transaction() as session:
        session.add(
            PrPostMetricSnapshot(
                publication_id=sample.publication_id,
                observed_at=OBSERVED,
                source=PrMetricSource.API,
                views=12_431,
                reach=3_102,
                impressions=15_998,
                extra_metrics={"completion_rate": 0.42},
            )
        )
        session.add(
            PrChannelMetricSnapshot(
                channel_id=sample.channel_id,
                observed_at=OBSERVED,
                source=PrMetricSource.API,
                views=98_120,
                reach=41_007,
                followers=5_512,
                members=None,
            )
        )

    async with reporting_database.session() as session:
        post = (
            await session.execute(
                text(
                    "SELECT views, reach, impressions, extra_metrics "
                    "FROM pr_post_metric_snapshots WHERE publication_id = :id"
                ),
                {"id": sample.publication_id},
            )
        ).one()
        channel = (
            await session.execute(
                text(
                    "SELECT views, reach, followers, members "
                    "FROM pr_channel_metric_snapshots WHERE channel_id = :id"
                ),
                {"id": sample.channel_id},
            )
        ).one()

    assert post.views == 12_431
    assert post.reach == 3_102
    assert post.views != post.reach
    assert post.impressions == 15_998
    assert post.extra_metrics == {"completion_rate": 0.42}

    assert channel.views == 98_120
    assert channel.reach == 41_007
    assert channel.views != channel.reach
    assert channel.followers == 5_512
    assert channel.members is None


@pytest.mark.parametrize("metric", POST_METRIC_COLUMNS)
async def test_a_negative_post_metric_is_refused(
    reporting_database: Database, sample: Sample, metric: str
) -> None:
    """Requirement 11, for every measured column on the post table."""
    observed = OBSERVED + timedelta(minutes=POST_METRIC_COLUMNS.index(metric) + 1)
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrPostMetricSnapshot(
                    publication_id=sample.publication_id,
                    observed_at=observed,
                    source=PrMetricSource.MANUAL,
                    **{metric: Decimal("-1") if "average" in metric else -1},
                )
            )
            await session.flush()


@pytest.mark.parametrize("metric", CHANNEL_METRIC_COLUMNS)
async def test_a_negative_channel_metric_is_refused(
    reporting_database: Database, sample: Sample, metric: str
) -> None:
    """Requirement 11, for every measured column on the channel table."""
    observed = OBSERVED + timedelta(minutes=CHANNEL_METRIC_COLUMNS.index(metric) + 1)
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrChannelMetricSnapshot(
                    channel_id=sample.channel_id,
                    observed_at=observed,
                    source=PrMetricSource.MANUAL,
                    **{metric: -1},
                )
            )
            await session.flush()


async def test_zero_is_a_legitimate_metric(reporting_database: Database, sample: Sample) -> None:
    """A post that nobody saw is a fact, and the constraint is ``>= 0``."""
    async with reporting_database.transaction() as session:
        session.add(
            PrPostMetricSnapshot(
                publication_id=sample.publication_id,
                observed_at=OBSERVED + timedelta(hours=5),
                source=PrMetricSource.MANUAL,
                views=0,
                reach=0,
                average_view_duration_seconds=Decimal("0.000"),
            )
        )
        await session.flush()


async def test_an_absent_metric_is_accepted(reporting_database: Database, sample: Sample) -> None:
    """A platform that exposes nothing leaves everything null, which is honest."""
    async with reporting_database.transaction() as session:
        session.add(
            PrPostMetricSnapshot(
                publication_id=sample.publication_id,
                observed_at=OBSERVED + timedelta(hours=6),
                source=PrMetricSource.IMPORT,
            )
        )
        await session.flush()


async def test_a_duplicate_post_snapshot_from_one_source_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 12."""
    observed = OBSERVED + timedelta(hours=10)
    async with reporting_database.transaction() as session:
        session.add(
            PrPostMetricSnapshot(
                publication_id=sample.publication_id,
                observed_at=observed,
                source=PrMetricSource.API,
                views=10,
            )
        )
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrPostMetricSnapshot(
                    publication_id=sample.publication_id,
                    observed_at=observed,
                    source=PrMetricSource.API,
                    views=11,
                )
            )
            await session.flush()


async def test_two_sources_may_disagree_about_the_same_instant(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 12, and the reason ``source`` is in the key.

    An API reading and a hand-typed reading for the same moment can differ.
    Storing both is what makes the disagreement visible; a key without
    ``source`` would make the second write a silent overwrite of the first.
    """
    observed = OBSERVED + timedelta(hours=11)
    async with reporting_database.transaction() as session:
        session.add(
            PrPostMetricSnapshot(
                publication_id=sample.publication_id,
                observed_at=observed,
                source=PrMetricSource.API,
                views=1_000,
            )
        )
        session.add(
            PrPostMetricSnapshot(
                publication_id=sample.publication_id,
                observed_at=observed,
                source=PrMetricSource.MANUAL,
                views=1_050,
            )
        )
        await session.flush()

    async with reporting_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT source, views FROM pr_post_metric_snapshots "
                    "WHERE publication_id = :id AND observed_at = :at ORDER BY source"
                ),
                {"id": sample.publication_id, "at": observed},
            )
        ).all()
    assert [(row.source, row.views) for row in rows] == [("API", 1_000), ("MANUAL", 1_050)]


async def test_a_duplicate_channel_snapshot_from_one_source_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 12, channel side."""
    observed = OBSERVED + timedelta(hours=12)
    async with reporting_database.transaction() as session:
        session.add(
            PrChannelMetricSnapshot(
                channel_id=sample.channel_id,
                observed_at=observed,
                source=PrMetricSource.IMPORT,
                followers=1,
            )
        )
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrChannelMetricSnapshot(
                    channel_id=sample.channel_id,
                    observed_at=observed,
                    source=PrMetricSource.IMPORT,
                    followers=2,
                )
            )
            await session.flush()


async def test_the_snapshot_tables_have_no_updated_at_column(
    reporting_database: Database,
) -> None:
    """Requirement 13, read from the catalog rather than from the models.

    An observation that can be edited is not an observation. Asserted against
    the migrated schema so that adding the column to a model without noticing
    fails here too.
    """
    async with reporting_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name IN "
                    "('pr_post_metric_snapshots', 'pr_channel_metric_snapshots', "
                    "'pr_report_artifacts')"
                )
            )
        ).all()
    by_table: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        by_table.setdefault(table_name, set()).add(column_name)

    for table_name in (
        "pr_post_metric_snapshots",
        "pr_channel_metric_snapshots",
        "pr_report_artifacts",
    ):
        assert "updated_at" not in by_table[table_name], table_name
        assert "created_at" in by_table[table_name], table_name


# --- 14, 15, 16: weekly manual input ----------------------------------------


async def _add_weekly(
    database: Database,
    channel_id: uuid.UUID,
    period_id: uuid.UUID,
    user_id: uuid.UUID,
    version_no: int,
    **values: Any,
) -> None:
    async with database.transaction() as session:
        session.add(
            PrWeeklyManualInput(
                channel_id=channel_id,
                period_id=period_id,
                version_no=version_no,
                entered_by_user_id=user_id,
                **values,
            )
        )
        await session.flush()


async def test_a_duplicate_weekly_version_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 14."""
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-DUP")
    await _add_weekly(reporting_database, channel_id, sample.period_id, sample.user_id, 1)
    with pytest.raises(IntegrityError):
        await _add_weekly(reporting_database, channel_id, sample.period_id, sample.user_id, 1)


async def test_successive_versions_are_accepted(
    reporting_database: Database, sample: Sample
) -> None:
    """A correction is a new version, and the earlier one stays readable."""
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-VERS")
    await _add_weekly(
        reporting_database, channel_id, sample.period_id, sample.user_id, 1, manual_views=100
    )
    await _add_weekly(
        reporting_database, channel_id, sample.period_id, sample.user_id, 2, manual_views=140
    )
    async with reporting_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT version_no, manual_views FROM pr_weekly_manual_inputs "
                    "WHERE channel_id = :id ORDER BY version_no"
                ),
                {"id": channel_id},
            )
        ).all()
    assert [(row.version_no, row.manual_views) for row in rows] == [(1, 100), (2, 140)]


@pytest.mark.parametrize("version", [0, -1])
async def test_a_weekly_version_below_one_is_refused(
    reporting_database: Database, sample: Sample, version: int
) -> None:
    channel_id, _ = await _make_channel(reporting_database, f"CH-WK-V{version}")
    with pytest.raises(IntegrityError):
        await _add_weekly(reporting_database, channel_id, sample.period_id, sample.user_id, version)


@pytest.mark.parametrize("column", WEEKLY_INPUT_VALUE_COLUMNS)
async def test_a_negative_weekly_value_is_refused(
    reporting_database: Database, sample: Sample, column: str
) -> None:
    """Requirement 15, for every column defined as non-negative.

    Parametrised over ``WEEKLY_INPUT_VALUE_COLUMNS``, so the day a column moves
    into ``WEEKLY_INPUT_SIGNED_COLUMNS`` it leaves this test automatically and
    has to earn its own acceptance test instead.
    """
    assert column not in WEEKLY_INPUT_SIGNED_COLUMNS
    channel_id, _ = await _make_channel(reporting_database, f"CH-WK-NEG-{column}")
    value: Any = Decimal("-1") if column == "cost_vnd" else -1
    with pytest.raises(IntegrityError):
        await _add_weekly(
            reporting_database,
            channel_id,
            sample.period_id,
            sample.user_id,
            1,
            **{column: value},
        )


async def test_a_negative_members_gain_is_accepted(
    reporting_database: Database, sample: Sample
) -> None:
    """``members_gain`` is a signed period delta.

    A group that lost members over the week has a negative gain. That is a fact
    the team needs recorded, and a database that refused it would make the week
    unrecordable - so the value is stored and read back unchanged.
    """
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-LOSTMEMBERS")
    await _add_weekly(
        reporting_database,
        channel_id,
        sample.period_id,
        sample.user_id,
        1,
        members_end=980,
        members_gain=-42,
    )
    async with reporting_database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT members_end, members_gain FROM pr_weekly_manual_inputs "
                    "WHERE channel_id = :id"
                ),
                {"id": channel_id},
            )
        ).one()
    assert row.members_gain == -42
    assert row.members_end == 980


async def test_a_negative_members_end_is_rejected(
    reporting_database: Database, sample: Sample
) -> None:
    """The count beside the delta is still a count.

    ``members_end`` is how many members the channel had at the close of the
    period. There is no such thing as minus one member, and letting the signed
    delta relax its neighbour would have been the easy mistake.
    """
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-NEGEND")
    with pytest.raises(IntegrityError):
        await _add_weekly(
            reporting_database,
            channel_id,
            sample.period_id,
            sample.user_id,
            1,
            members_end=-1,
        )


async def test_a_negative_gain_beside_a_negative_count_is_still_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """The relaxation is scoped to one column, not to the row."""
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-MIXEDNEG")
    with pytest.raises(IntegrityError):
        await _add_weekly(
            reporting_database,
            channel_id,
            sample.period_id,
            sample.user_id,
            1,
            members_gain=-5,
            leads=-1,
        )


async def test_an_approval_without_an_approver_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 16. "Approved, by nobody" is not an approval."""
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-NOAPPROVER")
    with pytest.raises(IntegrityError):
        await _add_weekly(
            reporting_database,
            channel_id,
            sample.period_id,
            sample.user_id,
            1,
            approved_at=OBSERVED,
        )


async def test_an_approver_without_a_time_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 16, the other half. "Approved by Anh, at no point" either."""
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-NOTIME")
    with pytest.raises(IntegrityError):
        await _add_weekly(
            reporting_database,
            channel_id,
            sample.period_id,
            sample.user_id,
            1,
            approved_by_user_id=sample.user_id,
        )


async def test_an_approval_with_both_halves_is_accepted(
    reporting_database: Database, sample: Sample
) -> None:
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-APPROVED")
    approver = await _make_user(reporting_database, "Weekly approver")
    await _add_weekly(
        reporting_database,
        channel_id,
        sample.period_id,
        sample.user_id,
        1,
        approved_by_user_id=approver,
        approved_at=OBSERVED,
    )


async def test_an_unapproved_row_needs_neither_half(
    reporting_database: Database, sample: Sample
) -> None:
    """A draft has no approver and no approval time, and that is consistent."""
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-DRAFT")
    await _add_weekly(reporting_database, channel_id, sample.period_id, sample.user_id, 1)


async def test_a_weekly_row_may_reference_a_monthly_period(
    reporting_database: Database, sample: Sample
) -> None:
    """A documented deferral, pinned so it stays a decision.

    The database does **not** check that ``period_id`` names a ``WEEK`` - that
    needs a lookup into another row, which no ``CHECK`` constraint can do.
    Step 1C services must validate it. If a future revision adds a trigger or a
    denormalised column to enforce it, this test has to change in the same
    commit, which is the point.
    """
    channel_id, _ = await _make_channel(reporting_database, "CH-WK-MONTHLY")
    monthly = await _make_period(
        reporting_database,
        "2026-09",
        period_type=PrPeriodType.MONTH,
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
    )
    await _add_weekly(reporting_database, channel_id, monthly, sample.user_id, 1)


# --- 17: issues and actions -------------------------------------------------


async def test_one_issue_may_have_many_actions(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 17.

    An issue routinely has several actions with different owners and different
    deadlines, which is why they are a table and not an ``action_taken`` column.
    """
    issue_id = await _make_issue(reporting_database, "ISS-0001")
    second_owner = await _make_user(reporting_database, "Second action owner")
    async with reporting_database.transaction() as session:
        for index, owner in enumerate((sample.user_id, second_owner, sample.user_id)):
            session.add(
                PrAction(
                    issue_id=issue_id,
                    description=f"Action {index}",
                    owner_user_id=owner,
                    status=PrActionStatus.TODO,
                    deadline=OBSERVED + timedelta(days=index),
                )
            )
        await session.flush()

    async with reporting_database.session() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_actions WHERE issue_id = :id"), {"id": issue_id}
            )
        ).scalar_one()
    assert count == 3


async def test_an_issue_may_belong_to_no_period_channel_or_content(
    reporting_database: Database,
) -> None:
    """ "We still have no analytics access on TikTok" belongs to none of them."""
    issue_id = await _make_issue(reporting_database, "ISS-UNATTACHED")
    async with reporting_database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT period_id, channel_id, content_id, needs_management_decision "
                    "FROM pr_issues WHERE id = :id"
                ),
                {"id": issue_id},
            )
        ).one()
    assert row.period_id is None
    assert row.channel_id is None
    assert row.content_id is None
    assert row.needs_management_decision is False


async def test_a_duplicate_issue_code_is_refused(reporting_database: Database) -> None:
    await _make_issue(reporting_database, "ISS-DUP")
    with pytest.raises(IntegrityError):
        await _make_issue(reporting_database, "ISS-DUP")


async def test_a_blank_action_description_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    issue_id = await _make_issue(reporting_database, "ISS-BLANKDESC")
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrAction(
                    issue_id=issue_id,
                    description="   ",
                    owner_user_id=sample.user_id,
                    status=PrActionStatus.TODO,
                )
            )
            await session.flush()


# --- 18 and 19: report runs and their artifacts -----------------------------


async def test_a_duplicate_idempotency_key_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 18.

    What stops a retry, a double press and a scheduler firing twice from
    producing three reports of the same week.
    """
    await _make_run(reporting_database, sample.period_id, "weekly:2026-W32:v1")
    with pytest.raises(IntegrityError):
        await _make_run(reporting_database, sample.period_id, "weekly:2026-W32:v1")


async def test_a_blank_idempotency_key_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    with pytest.raises(IntegrityError):
        await _make_run(reporting_database, sample.period_id, "  ")


async def test_a_run_that_finished_before_it_started_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrReportRun(
                    report_type=PrReportType.WEEKLY_MANAGEMENT,
                    period_id=sample.period_id,
                    idempotency_key="weekly:backwards",
                    template_version="v1",
                    trigger_type=PrReportTriggerType.SCHEDULED,
                    status=PrReportRunStatus.FAILED,
                    source_cutoff_at=OBSERVED,
                    started_at=OBSERVED,
                    finished_at=OBSERVED - timedelta(minutes=1),
                )
            )
            await session.flush()


async def test_a_run_may_finish_without_a_recorded_start(
    reporting_database: Database, sample: Sample
) -> None:
    """What a crashed-and-reconciled run looks like, and it must be storable."""
    async with reporting_database.transaction() as session:
        session.add(
            PrReportRun(
                report_type=PrReportType.MONTHLY_MANAGEMENT,
                period_id=sample.period_id,
                idempotency_key="monthly:reconciled",
                template_version="v1",
                trigger_type=PrReportTriggerType.RETRY,
                status=PrReportRunStatus.FAILED,
                source_cutoff_at=OBSERVED,
                finished_at=OBSERVED,
            )
        )
        await session.flush()


async def test_a_negative_missing_data_count_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrReportRun(
                    report_type=PrReportType.WEEKLY_MANAGEMENT,
                    period_id=sample.period_id,
                    idempotency_key="weekly:negative-missing",
                    template_version="v1",
                    trigger_type=PrReportTriggerType.MANUAL,
                    status=PrReportRunStatus.VALIDATION_FAILED,
                    source_cutoff_at=OBSERVED,
                    missing_data_count=-1,
                )
            )
            await session.flush()


async def test_a_run_stores_its_validation_summary_as_json(
    reporting_database: Database, sample: Sample
) -> None:
    run_id = uuid.uuid4()
    async with reporting_database.transaction() as session:
        session.add(
            PrReportRun(
                id=run_id,
                report_type=PrReportType.WEEKLY_MANAGEMENT,
                period_id=sample.period_id,
                idempotency_key="weekly:with-summary",
                template_version="v2",
                trigger_type=PrReportTriggerType.SCHEDULED,
                status=PrReportRunStatus.VALIDATION_FAILED,
                source_cutoff_at=OBSERVED,
                validation_summary={"missing": ["CH-REP"], "checked": 12},
                missing_data_count=1,
            )
        )
    async with reporting_database.session() as session:
        stored = (
            await session.execute(
                text("SELECT validation_summary FROM pr_report_runs WHERE id = :id"),
                {"id": run_id},
            )
        ).scalar_one()
    assert stored == {"missing": ["CH-REP"], "checked": 12}


async def _add_artifact(
    database: Database,
    run_id: uuid.UUID,
    artifact_format: PrArtifactFormat,
    version_no: int,
) -> None:
    async with database.transaction() as session:
        session.add(
            PrReportArtifact(
                report_run_id=run_id,
                artifact_format=artifact_format,
                version_no=version_no,
                file_name=f"report-v{version_no}.{artifact_format.value.lower()}",
                storage_path=f"/srv/reports/{run_id}/v{version_no}",
                mime_type="application/octet-stream",
                file_size_bytes=1024,
            )
        )
        await session.flush()


async def test_a_duplicate_artifact_version_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Requirement 19."""
    run_id = await _make_run(reporting_database, sample.period_id, "weekly:artifact-dup")
    await _add_artifact(reporting_database, run_id, PrArtifactFormat.XLSX, 1)
    with pytest.raises(IntegrityError):
        await _add_artifact(reporting_database, run_id, PrArtifactFormat.XLSX, 1)


async def test_one_run_may_produce_two_formats_of_the_same_version(
    reporting_database: Database, sample: Sample
) -> None:
    """An XLSX for the analyst and a PDF for the meeting, both version 1."""
    run_id = await _make_run(reporting_database, sample.period_id, "weekly:two-formats")
    await _add_artifact(reporting_database, run_id, PrArtifactFormat.XLSX, 1)
    await _add_artifact(reporting_database, run_id, PrArtifactFormat.PDF, 1)
    async with reporting_database.session() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_report_artifacts WHERE report_run_id = :id"),
                {"id": run_id},
            )
        ).scalar_one()
    assert count == 2


async def test_a_regenerated_artifact_is_a_new_version(
    reporting_database: Database, sample: Sample
) -> None:
    """The workbook somebody was sent on Monday is still there on Tuesday."""
    run_id = await _make_run(reporting_database, sample.period_id, "weekly:regenerated")
    await _add_artifact(reporting_database, run_id, PrArtifactFormat.XLSX, 1)
    await _add_artifact(reporting_database, run_id, PrArtifactFormat.XLSX, 2)
    async with reporting_database.session() as session:
        versions = [
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT version_no FROM pr_report_artifacts "
                        "WHERE report_run_id = :id ORDER BY version_no"
                    ),
                    {"id": run_id},
                )
            ).all()
        ]
    assert versions == [1, 2]


@pytest.mark.parametrize("version", [0, -1])
async def test_an_artifact_version_below_one_is_refused(
    reporting_database: Database, sample: Sample, version: int
) -> None:
    run_id = await _make_run(reporting_database, sample.period_id, f"weekly:badversion{version}")
    with pytest.raises(IntegrityError):
        await _add_artifact(reporting_database, run_id, PrArtifactFormat.PDF, version)


async def test_a_negative_file_size_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    run_id = await _make_run(reporting_database, sample.period_id, "weekly:negativesize")
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            session.add(
                PrReportArtifact(
                    report_run_id=run_id,
                    artifact_format=PrArtifactFormat.XLSX,
                    version_no=1,
                    file_name="report.xlsx",
                    storage_path="/srv/reports/negative",
                    mime_type="application/octet-stream",
                    file_size_bytes=-1,
                )
            )
            await session.flush()


# --- 20 and 21: identity and delete behaviour, read from the catalog --------


async def test_every_reporting_person_column_references_the_users_table(
    reporting_database: Database,
) -> None:
    """Requirement 20, against the migrated schema rather than the models."""
    async with reporting_database.session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT tc.table_name, kcu.column_name, ccu.table_name AS referenced,
                           rc.delete_rule
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON tc.constraint_name = ccu.constraint_name
                    JOIN information_schema.referential_constraints AS rc
                      ON tc.constraint_name = rc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = ANY(:tables)
                    """
                ),
                {"tables": list(REPORTING_TABLES)},
            )
        ).all()

    assert rows, "no foreign keys were reflected"
    person = {
        (row.table_name, row.column_name, row.referenced)
        for row in rows
        if row.column_name.endswith("user_id")
    }
    assert {referenced for _, _, referenced in person} == {"users"}
    assert {(table, column) for table, column, _ in person} == {
        ("pr_publications", "publisher_user_id"),
        ("pr_weekly_manual_inputs", "entered_by_user_id"),
        ("pr_weekly_manual_inputs", "approved_by_user_id"),
        ("pr_issues", "owner_user_id"),
        ("pr_actions", "owner_user_id"),
        ("pr_report_runs", "requested_by_user_id"),
        # Step 1F.2.4a (migration 0027). The seventh, and the first on a
        # snapshot table: a hand-entered reading records who entered it in the
        # row itself, so "ai nhập chỉ số này?" is answered by the history rather
        # than by searching an audit log.
        ("pr_channel_metric_snapshots", "recorded_by_user_id"),
    }


async def test_every_reporting_foreign_key_restricts_deletion(
    reporting_database: Database,
) -> None:
    """Requirement 21, against the migrated schema."""
    async with reporting_database.session() as session:
        rules = (
            await session.execute(
                text(
                    """
                    SELECT tc.table_name, tc.constraint_name, rc.delete_rule
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.referential_constraints AS rc
                      ON tc.constraint_name = rc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = ANY(:tables)
                    """
                ),
                {"tables": list(REPORTING_TABLES)},
            )
        ).all()

    # Twenty-one since Step 1F.2.3f, which added ``production_submission_id`` and
    # ``derivative_id`` to ``pr_publications`` - both ``RESTRICT``, which is what
    # stops a produced file being deleted out from under the record of where it
    # was posted. Twenty-two since Step 1F.2.4a (0027) added
    # ``pr_channel_metric_snapshots.recorded_by_user_id`` - also ``RESTRICT``, so
    # removing the person who typed the numbers in cannot quietly remove that
    # they typed them. The count is asserted as well as the rule so a key added
    # with the wrong ``ondelete`` cannot hide behind a passing "all of them
    # restrict".
    assert len(rules) == 22, len(rules)
    offenders = [
        (row.table_name, row.constraint_name, row.delete_rule)
        for row in rules
        if row.delete_rule != "RESTRICT"
    ]
    assert offenders == [], offenders


async def test_deleting_a_user_who_entered_a_week_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    channel_id, _ = await _make_channel(reporting_database, "CH-DEL-USER")
    entered_by = await _make_user(reporting_database, "Week typist")
    await _add_weekly(reporting_database, channel_id, sample.period_id, entered_by, 1)
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": entered_by})


async def test_deleting_a_channel_that_has_been_measured_is_refused(
    reporting_database: Database,
) -> None:
    channel_id, _ = await _make_channel(reporting_database, "CH-DEL-MEASURED")
    async with reporting_database.transaction() as session:
        session.add(
            PrChannelMetricSnapshot(
                channel_id=channel_id,
                observed_at=OBSERVED,
                source=PrMetricSource.API,
                followers=10,
            )
        )
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(
                text("DELETE FROM pr_channels WHERE id = :id"), {"id": channel_id}
            )


async def test_deleting_a_period_that_has_been_reported_is_refused(
    reporting_database: Database,
) -> None:
    period_id = await _make_period(reporting_database, "2026-W41")
    await _make_run(reporting_database, period_id, "weekly:2026-W41")
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(
                text("DELETE FROM pr_reporting_periods WHERE id = :id"), {"id": period_id}
            )


async def test_deleting_a_publication_that_has_metrics_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    publication_id = await _make_publication(
        reporting_database, "PUB-DEL-METRIC", sample.content_id, sample.channel_id
    )
    async with reporting_database.transaction() as session:
        session.add(
            PrPostMetricSnapshot(
                publication_id=publication_id,
                observed_at=OBSERVED,
                source=PrMetricSource.API,
                views=1,
            )
        )
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(
                text("DELETE FROM pr_publications WHERE id = :id"), {"id": publication_id}
            )


async def test_deleting_a_run_that_produced_a_file_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    """Report history never disappears as a side effect of a cleanup."""
    run_id = await _make_run(reporting_database, sample.period_id, "weekly:has-artifact")
    await _add_artifact(reporting_database, run_id, PrArtifactFormat.PDF, 1)
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(text("DELETE FROM pr_report_runs WHERE id = :id"), {"id": run_id})


async def test_deleting_an_issue_that_has_actions_is_refused(
    reporting_database: Database, sample: Sample
) -> None:
    issue_id = await _make_issue(reporting_database, "ISS-DEL-ACTIONS")
    async with reporting_database.transaction() as session:
        session.add(
            PrAction(
                issue_id=issue_id,
                description="Chase the platform",
                owner_user_id=sample.user_id,
                status=PrActionStatus.IN_PROGRESS,
            )
        )
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(text("DELETE FROM pr_issues WHERE id = :id"), {"id": issue_id})


# --- The 0011 lesson: the database fills what the ORM omits ----------------


async def test_the_database_fills_every_default_the_orm_omits(
    reporting_database: Database, sample: Sample
) -> None:
    """Raw ``INSERT``s that mention no defaulted column at all.

    This is the shape of the write that failed in production at 0.6.0a3: the
    ORM omits a server-defaulted column from its ``INSERT``, and a migration
    that created the column ``NOT NULL`` with no default turns that into a
    ``NULL`` violation on the first real write.
    """
    period_id = uuid.uuid4()
    publication_id = uuid.uuid4()
    weekly_id = uuid.uuid4()
    issue_id = uuid.uuid4()
    action_id = uuid.uuid4()
    run_id = uuid.uuid4()
    channel_id, _ = await _make_channel(reporting_database, "CH-DEFAULTS")

    async with reporting_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_reporting_periods (id, code, period_type, date_start, date_end) "
                "VALUES (:id, '2026-W50', 'WEEK', :start, :end)"
            ),
            {"id": period_id, "start": WEEK_START, "end": WEEK_END},
        )
        await session.execute(
            text(
                "INSERT INTO pr_publications (id, code, content_id, channel_id, published_at) "
                "VALUES (:id, 'PUB-DEFAULTS', :content, :channel, :at)"
            ),
            {
                "id": publication_id,
                "content": sample.content_id,
                "channel": channel_id,
                "at": PUBLISHED,
            },
        )
        await session.execute(
            text(
                "INSERT INTO pr_weekly_manual_inputs "
                "(id, channel_id, period_id, version_no, entered_by_user_id) "
                "VALUES (:id, :channel, :period, 1, :user)"
            ),
            {
                "id": weekly_id,
                "channel": channel_id,
                "period": period_id,
                "user": sample.user_id,
            },
        )
        # ``severity`` and ``status`` are omitted: the database supplies them.
        await session.execute(
            text(
                "INSERT INTO pr_issues (id, code, title) VALUES (:id, 'ISS-DEFAULTS', 'Defaults')"
            ),
            {"id": issue_id},
        )
        await session.execute(
            text(
                "INSERT INTO pr_actions (id, issue_id, description, owner_user_id) "
                "VALUES (:id, :issue, 'Defaulted action', :user)"
            ),
            {"id": action_id, "issue": issue_id, "user": sample.user_id},
        )
        # ``report_type`` and ``trigger_type`` are stated because they have no
        # default and must not gain one; ``status`` is omitted because it does.
        await session.execute(
            text(
                "INSERT INTO pr_report_runs (id, report_type, period_id, idempotency_key, "
                "template_version, trigger_type, source_cutoff_at) "
                "VALUES (:id, 'WEEKLY_MANAGEMENT', :period, 'weekly:defaults', 'v1', "
                "'MANUAL', :at)"
            ),
            {"id": run_id, "period": period_id, "at": OBSERVED},
        )

    async with reporting_database.session() as session:
        period = (
            await session.execute(
                text(
                    "SELECT status, created_at, updated_at FROM pr_reporting_periods WHERE id = :id"
                ),
                {"id": period_id},
            )
        ).one()
        publication = (
            await session.execute(
                text("SELECT status, created_at FROM pr_publications WHERE id = :id"),
                {"id": publication_id},
            )
        ).one()
        weekly = (
            await session.execute(
                text("SELECT status, created_at FROM pr_weekly_manual_inputs WHERE id = :id"),
                {"id": weekly_id},
            )
        ).one()
        issue = (
            await session.execute(
                text(
                    "SELECT severity, status, needs_management_decision, created_at "
                    "FROM pr_issues WHERE id = :id"
                ),
                {"id": issue_id},
            )
        ).one()
        action = (
            await session.execute(
                text("SELECT status, created_at FROM pr_actions WHERE id = :id"),
                {"id": action_id},
            )
        ).one()
        run = (
            await session.execute(
                text(
                    "SELECT status, missing_data_count, created_at "
                    "FROM pr_report_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).one()

    assert period.status == "OPEN"
    assert period.created_at is not None
    assert period.updated_at is not None
    assert publication.status == "PUBLISHED"
    assert publication.created_at is not None
    assert weekly.status == "DRAFT"
    assert weekly.created_at is not None
    assert issue.severity == "MEDIUM"
    assert issue.status == "OPEN"
    assert issue.needs_management_decision is False
    assert issue.created_at is not None
    assert action.status == "TODO"
    assert action.created_at is not None
    assert run.status == "PENDING"
    assert run.missing_data_count == 0
    assert run.created_at is not None


@pytest.mark.parametrize(
    ("omitted", "statement"),
    [
        (
            "report_type",
            "INSERT INTO pr_report_runs (id, period_id, idempotency_key, template_version, "
            "source_cutoff_at, trigger_type) "
            "VALUES (:id, :period, :key, 'v1', :at, 'MANUAL')",
        ),
        (
            "trigger_type",
            "INSERT INTO pr_report_runs (id, period_id, idempotency_key, template_version, "
            "source_cutoff_at, report_type) "
            "VALUES (:id, :period, :key, 'v1', :at, 'WEEKLY_MANAGEMENT')",
        ),
    ],
)
async def test_a_run_without_its_type_or_trigger_is_refused(
    reporting_database: Database, sample: Sample, omitted: str, statement: str
) -> None:
    """The two columns that deliberately have no default.

    Proving the *absence* of a default, not just the value of the ones that
    have one: a raw ``INSERT`` omitting either fails on the ``NOT NULL``, which
    is what forces a caller to say whether a run is weekly or monthly and why
    it started. Each statement is written out in full rather than assembled,
    so what is being inserted is readable at the point of the assertion.
    """
    with pytest.raises(IntegrityError):
        async with reporting_database.transaction() as session:
            await session.execute(
                text(statement),
                {
                    "id": uuid.uuid4(),
                    "period": sample.period_id,
                    "key": f"weekly:missing-{omitted}",
                    "at": OBSERVED,
                },
            )


@pytest.mark.parametrize(
    ("table_name", "removed", "kept"),
    [
        (
            "pr_post_metric_snapshots",
            "ix_pr_post_metric_snapshots_publication_observed",
            "uq_pr_post_metric_snapshots_publication_observed_source",
        ),
        (
            "pr_channel_metric_snapshots",
            "ix_pr_channel_metric_snapshots_channel_observed",
            "uq_pr_channel_metric_snapshots_channel_observed_source",
        ),
    ],
)
async def test_the_redundant_snapshot_index_is_absent_but_the_unique_one_is_not(
    reporting_database: Database, table_name: str, removed: str, kept: str
) -> None:
    """The duplicate index was omitted on purpose, in the built schema.

    The unique index is a B-tree, so a query constraining its leftmost prefix -
    the subject alone, or the subject plus an ``observed_at`` range - already
    uses it. A standalone two-column index would answer the same queries while
    costing a second write on every append.
    """
    async with reporting_database.session() as session:
        names = {
            row[0]
            for row in (
                await session.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                    {"table": table_name},
                )
            ).all()
        }
    assert kept in names
    assert removed not in names, f"{removed} was re-added"


@pytest.mark.parametrize(
    ("expected_index", "statement"),
    [
        (
            "uq_pr_post_metric_snapshots_publication_observed_source",
            "EXPLAIN SELECT 1 FROM pr_post_metric_snapshots WHERE publication_id = :value",
        ),
        (
            "uq_pr_channel_metric_snapshots_channel_observed_source",
            "EXPLAIN SELECT 1 FROM pr_channel_metric_snapshots WHERE channel_id = :value",
        ),
    ],
)
async def test_the_unique_index_serves_its_leftmost_prefix(
    reporting_database: Database, expected_index: str, statement: str
) -> None:
    """The claim the index removal rests on, checked against the planner.

    ``EXPLAIN`` a lookup on the subject column alone and confirm PostgreSQL
    reaches for the three-column unique index. Without this, "the leftmost
    prefix is enough" would be an assertion about B-trees in general rather
    than about this schema in particular.
    """
    async with reporting_database.session() as session:
        # A sequential scan is cheaper on a tiny table, so ask the planner to
        # cost the index path the way it would on a real one.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in (await session.execute(text(statement), {"value": uuid.uuid4()})).all()
        )
    assert expected_index in plan, plan


async def test_a_snapshot_created_at_is_filled_without_being_mentioned(
    reporting_database: Database, sample: Sample
) -> None:
    """The append-only tables have only ``created_at``, and it must default."""
    snapshot_id = uuid.uuid4()
    async with reporting_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_post_metric_snapshots (id, publication_id, observed_at, source) "
                "VALUES (:id, :publication, :at, 'IMPORT')"
            ),
            {
                "id": snapshot_id,
                "publication": sample.publication_id,
                "at": OBSERVED + timedelta(days=3),
            },
        )
    async with reporting_database.session() as session:
        created = (
            await session.execute(
                text("SELECT created_at FROM pr_post_metric_snapshots WHERE id = :id"),
                {"id": snapshot_id},
            )
        ).scalar_one()
    assert created is not None
