"""Migration 0030, on a database built the way production builds one.

Step 1F.2.4d adds six columns and one check constraint to a table that already
holds every reading the department has taken. Three claims are worth a real
PostgreSQL rather than the offline SQLite schema:

* **nothing that already existed moves.** A channel, a connection and a
  snapshot written at 0029 come through byte for byte, with the six new columns
  ``NULL`` - not zero. A backfilled zero would have invented a measurement on
  every historical row and no report reading them could tell;
* **the constraint has the name the revision says it has.** Generated
  constraint names concatenate and run past PostgreSQL's 63-byte identifier
  limit, which is the defect revision 0021 exists to repair. ``expansion_metrics_not_negative``
  is short enough; this is where that is confirmed rather than assumed;
* **downgrade is exact.** Six columns and one constraint go, every column 0013
  and 0027 created stays, and every row survives.

Each fixture creates its own uniquely-named ``meobot_fbx_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.models.pr_reporting import (
    CHANNEL_EXPANSION_METRIC_COLUMNS,
    CHANNEL_WINDOW_METRIC_COLUMNS,
)
from meobot.db.session import Database
from meobot.domain.pr.reporting import PrMetricSource
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    downgrade_to,
    upgrade_to,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SNAPSHOTS = "pr_channel_metric_snapshots"
EXPANSION_CHECK = "ck_pr_channel_metric_snapshots_expansion_metrics_not_negative"
WINDOW_CHECK = "ck_pr_channel_metric_snapshots_window_metrics_not_negative"


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0029() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0029**, with its DSN so a test can go further.

    One short of this step's head, so a snapshot can be written *before* the six
    columns exist and observed afterwards - which is the only way to prove 0030
    touched nothing on its way past.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_fbx_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0029")

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database, dsn
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": scratch},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}"'))
        await admin.dispose()


async def _seed(database: Database) -> dict[str, uuid.UUID]:
    """One platform, one channel and one snapshot, written at 0029."""
    ids = {name: uuid.uuid4() for name in ("platform", "channel", "snapshot")}
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_platforms (id, code, name, api_available, status, "
                " created_at, updated_at) "
                "VALUES (:id, :code, 'Facebook', true, 'ACTIVE', now(), now())"
            ),
            {"id": ids["platform"], "code": f"FACEBOOK_{uuid.uuid4().hex[:4].upper()}"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_channels (id, code, name, platform_id, category, "
                " status, created_at, updated_at) "
                "VALUES (:id, :code, 'Apex Media', :platform, 'SCALE', "
                " 'ACTIVE', now(), now())"
            ),
            {
                "id": ids["channel"],
                "code": f"CH-{uuid.uuid4().int % 10000:04d}",
                "platform": ids["platform"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO pr_channel_metric_snapshots "
                "(id, channel_id, observed_at, source, followers, engagements_30d, "
                " extra_metrics, created_at) "
                "VALUES (:id, :channel, now(), 'API', 124812, 2100, "
                " '{\"facebook_page_fan_count\": 130500}'::json, now())"
            ),
            {"id": ids["snapshot"], "channel": ids["channel"]},
        )
    return ids


async def _columns(database: Database, table: str) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
        return set(found.scalars().all())


async def _indexes(database: Database, table: str) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = :t"), {"t": table}
        )
        return set(found.scalars().all())


@pytest.mark.asyncio(loop_scope="module")
async def test_0029_has_none_of_the_expansion_columns(
    stopped_at_0029: tuple[Database, str],
) -> None:
    """The starting point, so the rest of the file is measuring something."""
    database, _ = stopped_at_0029
    present = await _columns(database, SNAPSHOTS)
    assert set(CHANNEL_EXPANSION_METRIC_COLUMNS).isdisjoint(present)
    # And 0027's columns are already there, so this really is one revision on.
    assert set(CHANNEL_WINDOW_METRIC_COLUMNS) <= present


@pytest.mark.asyncio(loop_scope="module")
async def test_0030_adds_six_columns_and_moves_nothing_else(
    stopped_at_0029: tuple[Database, str],
) -> None:
    """Additive, and provably so.

    The snapshot written at 0029 keeps every value it had, including the
    ``extra_metrics`` copy of ``fan_count`` - which 0030 deliberately does
    **not** migrate into the new ``fans`` column. Backfilling it would look
    harmless and would be a guess: the JSON key was written by one connector for
    one platform, and a migration that read it would be inferring a canonical
    measurement from a free-form blob.
    """
    database, dsn = stopped_at_0029
    ids = await _seed(database)
    before = await _columns(database, SNAPSHOTS)

    await upgrade_to(dsn, "0030")

    after = await _columns(database, SNAPSHOTS)
    assert after == before | set(CHANNEL_EXPANSION_METRIC_COLUMNS)

    async with database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT followers, engagements_30d, extra_metrics, fans, "
                    " posts_count_30d, reactions_30d, video_views_30d "
                    "FROM pr_channel_metric_snapshots WHERE id = :id"
                ),
                {"id": ids["snapshot"]},
            )
        ).one()
    assert row.followers == 124812
    assert row.engagements_30d == 2100
    assert row.extra_metrics["facebook_page_fan_count"] == 130500
    # NULL is "not recorded". Zero would have been a measurement nobody took,
    # and no report reading these rows could have told the difference.
    assert row.fans is None
    assert row.posts_count_30d is None
    assert row.reactions_30d is None
    assert row.video_views_30d is None


@pytest.mark.asyncio(loop_scope="module")
async def test_the_constraint_is_what_the_revision_claims(
    stopped_at_0029: tuple[Database, str],
) -> None:
    """The name in the migration is the name in the database, and it fits.

    Sixty-two bytes, under PostgreSQL's limit by one. That is not luck worth
    relying on silently: revision 0021 exists because a generated name ran past
    the limit and was truncated, and this is where the next one would be caught.

    Every new column is ``BIGINT`` and nullable, and 0027's own constraint is
    still there beside the new one rather than widened - each revision owns the
    columns it created.
    """
    database, dsn = stopped_at_0029
    await upgrade_to(dsn, "0030")

    assert len(EXPANSION_CHECK) <= 63, EXPANSION_CHECK

    async with database.transaction() as session:
        checks = await session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conrelid::regclass::text = :t"
            ),
            {"t": SNAPSHOTS},
        )
        names = set(checks.scalars().all())
    assert EXPANSION_CHECK in names
    assert WINDOW_CHECK in names, "0027's constraint was not widened or replaced"

    async with database.transaction() as session:
        types = await session.execute(
            text(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_name = :t"
            ),
            {"t": SNAPSHOTS},
        )
        described = {row[0]: (row[1], row[2]) for row in types.all()}
    for column in CHANNEL_EXPANSION_METRIC_COLUMNS:
        assert described[column] == ("bigint", "YES"), column


@pytest.mark.asyncio(loop_scope="module")
async def test_no_index_was_added(stopped_at_0029: tuple[Database, str]) -> None:
    """Six more columns change nothing about which rows are read.

    The lookups are still "the latest readings for this channel" and "this
    channel's readings since a date", both served by 0013's unique B-tree
    through its leftmost prefix. A redundant index would cost a write on every
    append and buy nothing.
    """
    database, dsn = stopped_at_0029
    before = await _indexes(database, SNAPSHOTS)
    await upgrade_to(dsn, "0030")
    assert await _indexes(database, SNAPSHOTS) == before


@pytest.mark.asyncio(loop_scope="module")
async def test_the_database_refuses_a_negative_count_on_every_new_column(
    stopped_at_0029: tuple[Database, str],
) -> None:
    """All six, not only the ones a connector writes.

    The provider and the sync service both drop an implausible value long before
    it reaches here. This exists because a constraint is what makes the refusal
    true of *every* writer, including a repair script somebody runs against the
    database directly at two in the morning.
    """
    database, dsn = stopped_at_0029
    ids = await _seed(database)
    await upgrade_to(dsn, "0030")

    # A lightweight table expression rather than the ORM model, for the reason
    # the 0027 suite gives: the model describes the schema at *head*, and this
    # test runs at 0030.
    snapshots = sa.table(
        SNAPSHOTS,
        sa.column("id"),
        sa.column("channel_id"),
        sa.column("observed_at"),
        sa.column("source"),
        *(sa.column(name) for name in CHANNEL_EXPANSION_METRIC_COLUMNS),
    )
    base = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    for offset, column in enumerate(CHANNEL_EXPANSION_METRIC_COLUMNS, start=1):
        with pytest.raises(Exception, match="expansion_metrics_not_negative"):
            async with database.transaction() as session:
                await session.execute(
                    sa.insert(snapshots).values(
                        id=uuid.uuid4(),
                        channel_id=ids["channel"],
                        observed_at=base - timedelta(hours=offset),
                        source=PrMetricSource.API.value,
                        **{column: -1},
                    )
                )

    # And zero is accepted everywhere, because a month with no shares is a real
    # month. This is the assertion that stops somebody "fixing" the constraint
    # into `> 0` and quietly making an honest zero unrecordable.
    async with database.transaction() as session:
        await session.execute(
            sa.insert(snapshots).values(
                id=uuid.uuid4(),
                channel_id=ids["channel"],
                observed_at=base,
                source=PrMetricSource.API.value,
                **dict.fromkeys(CHANNEL_EXPANSION_METRIC_COLUMNS, 0),
            )
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_downgrade_removes_exactly_what_it_added(
    stopped_at_0029: tuple[Database, str],
) -> None:
    """0030 → 0029 loses the six columns and nothing else.

    The rows survive with every column 0013 and 0027 created. What is lost is
    the Step 1F.2.4d half of every reading, which is what the revision's own
    docstring says and is the only honest thing a downgrade of an additive
    revision can lose.
    """
    database, dsn = stopped_at_0029
    ids = await _seed(database)
    await upgrade_to(dsn, "0030")

    async with database.transaction() as session:
        await session.execute(
            text(
                "UPDATE pr_channel_metric_snapshots SET fans = 130500, "
                " posts_count_30d = 15 WHERE id = :id"
            ),
            {"id": ids["snapshot"]},
        )

    await downgrade_to(dsn, "0029")

    present = await _columns(database, SNAPSHOTS)
    assert set(CHANNEL_EXPANSION_METRIC_COLUMNS).isdisjoint(present)
    assert set(CHANNEL_WINDOW_METRIC_COLUMNS) <= present

    async with database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT followers, engagements_30d, extra_metrics "
                    "FROM pr_channel_metric_snapshots WHERE id = :id"
                ),
                {"id": ids["snapshot"]},
            )
        ).one()
    assert row.followers == 124812
    assert row.engagements_30d == 2100
    assert row.extra_metrics["facebook_page_fan_count"] == 130500

    async with database.transaction() as session:
        checks = await session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conrelid::regclass::text = :t"
            ),
            {"t": SNAPSHOTS},
        )
        names = set(checks.scalars().all())
    assert EXPANSION_CHECK not in names
    assert WINDOW_CHECK in names

    # And it goes back up cleanly, which is what makes the downgrade a real
    # escape route rather than a one-way door.
    await upgrade_to(dsn, "0030")
    assert set(CHANNEL_EXPANSION_METRIC_COLUMNS) <= await _columns(database, SNAPSHOTS)
