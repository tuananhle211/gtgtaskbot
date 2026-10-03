"""Migration 0027, on a database built the way production builds one.

Five claims, and only PostgreSQL can settle them:

* **the columns and the constraints are what the revision claims.** Thirteen
  metric columns, all ``BIGINT`` and all nullable, one ``recorded_by_user_id``
  with a ``RESTRICT`` foreign key, one non-negative check named for what it
  covers, and a ``handle`` on ``pr_channels`` with its own check;

* **nothing that already existed moves.** 0027 is additive, so a channel, a
  content item and a metric snapshot written at 0026 come through it byte for
  byte - including 0013's windowless columns, which this step leaves alone.
  A well-meaning ``op.execute`` added later is caught here rather than on the
  NAS;

* **no platform is guessed.** The most expensive mistake this revision could
  have made is reading a channel's ``url`` or ``name`` and writing a platform
  from it. A channel whose URL says ``tiktok.com`` is registered on a platform
  called *Facebook Việt Nam*, and it is still on that platform afterwards;

* **the database refuses a negative count**, for every one of the thirteen new
  columns and not only for the ones a service happens to write. That is what
  makes the rule true of a repair script as well as of the API;

* **the downgrade actually works.** Index and constraint names that do not match
  what was created are the classic Alembic failure - revision 0021 exists to
  repair exactly that, and 0027 creates its checks from bare names and drops
  them through ``op.f()`` for that reason. So this upgrades, downgrades and
  upgrades again, and asserts the snapshot *rows* survive the round trip with
  their 0013 columns intact.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and every PR migration test follows.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_channel_metrics_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_chm_*`` database and drops
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
from meobot.db.models.pr_reporting import CHANNEL_WINDOW_METRIC_COLUMNS
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

CHANNELS = "pr_channels"
SNAPSHOTS = "pr_channel_metric_snapshots"

HANDLE_CHECK = "ck_pr_channels_handle_not_empty"
WINDOW_CHECK = "ck_pr_channel_metric_snapshots_window_metrics_not_negative"
RECORDER_FK = "fk_pr_channel_metric_snapshots_recorded_by_user_id_users"

#: Everything 0013 put on the snapshot table. 0027 must leave every one of them
#: exactly where it is - see the module docstring on why they are kept.
COLUMNS_0013 = frozenset(
    {
        "id",
        "channel_id",
        "observed_at",
        "source",
        "followers",
        "members",
        "views",
        "reach",
        "impressions",
        "engagements",
        "messages",
        "extra_metrics",
        "created_at",
    }
)

#: Everything 0027 adds. Nothing else may appear.
COLUMNS_0027 = frozenset(CHANNEL_WINDOW_METRIC_COLUMNS) | {"recorded_by_user_id"}


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0026() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0026**, with its DSN so a test can go further.

    One short of head, so a channel and a snapshot can be written *before*
    Step 1F.2.4a's columns exist and observed afterwards - which is the only way
    to prove 0027 touched nothing on its way past.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_chm_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0026")

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database, dsn
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def _seed(database: Database) -> dict[str, uuid.UUID]:
    """A user, a platform, a channel and one snapshot, as 0026 would hold them.

    The platform is deliberately named *Facebook Việt Nam* while the channel's
    URL points at TikTok. Anything that inferred a platform from either string
    would have to pick one of them, and both would be wrong.
    """
    ids = {key: uuid.uuid4() for key in ("user", "platform", "channel", "snapshot")}
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
                "VALUES (:id, 'Nguyễn A', 'EMPLOYEE', true, now(), now())"
            ),
            {"id": ids["user"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_platforms (id, code, name, api_available, status, "
                " created_at, updated_at) "
                "VALUES (:id, :code, 'Facebook Việt Nam', false, 'ACTIVE', now(), now())"
            ),
            {"id": ids["platform"], "code": f"FB_VN_{uuid.uuid4().hex[:4].upper()}"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_channels (id, code, name, platform_id, category, url, "
                " status, created_at, updated_at) "
                "VALUES (:id, :code, 'Trang cũ', :platform, 'SCALE', "
                " 'https://www.tiktok.com/@drtien', 'ACTIVE', now(), now())"
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
                "(id, channel_id, observed_at, source, followers, views, created_at) "
                "VALUES (:id, :channel, now(), 'MANUAL', 1000, 5000, now())"
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
async def test_0026_has_none_of_step_1f24as_columns(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """The starting point, so the rest of the file is measuring something."""
    database, _ = stopped_at_0026
    assert await _columns(database, SNAPSHOTS) == COLUMNS_0013
    assert "handle" not in await _columns(database, CHANNELS)


@pytest.mark.asyncio(loop_scope="module")
async def test_0027_adds_its_columns_and_moves_nothing_else(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """Additive, and provably so.

    A channel and a snapshot written at 0026 come through with the same values -
    including 0013's ``views``, which Step 1F.2.4a deliberately stops writing but
    does not touch.
    """
    database, dsn = stopped_at_0026
    ids = await _seed(database)

    await upgrade_to(dsn, "0027")

    assert await _columns(database, SNAPSHOTS) == COLUMNS_0013 | COLUMNS_0027
    assert "handle" in await _columns(database, CHANNELS)

    async with database.transaction() as session:
        snapshot = (
            await session.execute(
                text(
                    "SELECT followers, views, source, following, views_30d, "
                    " recorded_by_user_id FROM pr_channel_metric_snapshots WHERE id = :id"
                ),
                {"id": ids["snapshot"]},
            )
        ).one()
    assert snapshot.followers == 1000
    assert snapshot.views == 5000, "0013's column is untouched, not migrated"
    assert snapshot.source == "MANUAL"
    # The new columns arrive empty. NULL is "not recorded", and backfilling a
    # zero would have invented a measurement for every historical row.
    assert snapshot.following is None
    assert snapshot.views_30d is None
    assert snapshot.recorded_by_user_id is None

    async with database.transaction() as session:
        channel = (
            await session.execute(
                text("SELECT name, url, platform_id, handle FROM pr_channels WHERE id = :id"),
                {"id": ids["channel"]},
            )
        ).one()
    assert channel.name == "Trang cũ"
    assert channel.url == "https://www.tiktok.com/@drtien"
    assert channel.handle is None


@pytest.mark.asyncio(loop_scope="module")
async def test_no_platform_is_guessed_from_a_url_or_a_name(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """The claim the whole legacy story rests on.

    The seeded channel's URL says TikTok and its platform is called *Facebook
    Việt Nam*. After 0027 it is still on that platform, and the code is still
    the one it was registered with. Nothing looked at either string.
    """
    database, dsn = stopped_at_0026
    ids = await _seed(database)
    await upgrade_to(dsn, "0027")

    async with database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT p.code, p.name FROM pr_channels c "
                    "JOIN pr_platforms p ON p.id = c.platform_id WHERE c.id = :id"
                ),
                {"id": ids["channel"]},
            )
        ).one()
    assert row.code.startswith("FB_VN_")
    assert row.name == "Facebook Việt Nam"
    assert row.code not in {"TIKTOK", "FACEBOOK"}


@pytest.mark.asyncio(loop_scope="module")
async def test_the_constraints_are_what_the_revision_claims(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """The names in the migration are the names in the database.

    Short and explicit, because the generated form concatenates long names and
    runs past PostgreSQL's 63-byte identifier limit - the defect 0021 exists to
    repair. Every new metric column is ``BIGINT`` and nullable, and the recorder
    key is ``RESTRICT``.
    """
    database, dsn = stopped_at_0026
    await upgrade_to(dsn, "0027")

    async with database.transaction() as session:
        checks = await session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conrelid::regclass::text = ANY(:tables)"
            ),
            {"tables": [SNAPSHOTS, CHANNELS]},
        )
        names = set(checks.scalars().all())
    assert WINDOW_CHECK in names
    assert HANDLE_CHECK in names
    # 0013's own check is still there beside the new one, and is *not* asserted
    # by name: that revision passed its already-prefixed name to
    # ``op.create_check_constraint``, so the naming convention prefixed it a
    # second time and PostgreSQL truncated the result to
    # ``ck_pr_channel_metric_snapshots_ck_pr_channel_metric_sna_<hash>``. That
    # is the same defect revision 0021 exists to repair, it is pre-existing and
    # harmless, and 0027 deliberately does not repeat it - which is what the two
    # assertions above are checking. What matters here is that widening the
    # table left the old constraint in place, so it is asserted by shape.
    doubled = {name for name in names if name.startswith("ck_pr_channel_metric_snapshots_ck_")}
    assert len(doubled) == 1, names

    async with database.transaction() as session:
        keys = await session.execute(
            text(
                "SELECT conname, confdeltype::text FROM pg_constraint "
                "WHERE contype = 'f' AND conrelid::regclass::text = :t"
            ),
            {"t": SNAPSHOTS},
        )
        found = dict(keys.all())  # type: ignore[arg-type]
    assert found.get(RECORDER_FK) == "r", found

    async with database.transaction() as session:
        types = await session.execute(
            text(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_name = :t"
            ),
            {"t": SNAPSHOTS},
        )
        described = {row[0]: (row[1], row[2]) for row in types.all()}
    for column in CHANNEL_WINDOW_METRIC_COLUMNS:
        assert described[column] == ("bigint", "YES"), column

    # No index was added. The lookup this step performs is served by 0013's
    # unique B-tree through its leftmost prefix, and a duplicate would cost a
    # write on every append and buy nothing.
    assert await _indexes(database, SNAPSHOTS) == {
        "pk_pr_channel_metric_snapshots",
        "ix_pr_channel_metric_snapshots_observed_at",
        "ix_pr_channel_metric_snapshots_source",
        "uq_pr_channel_metric_snapshots_channel_observed_source",
    }


@pytest.mark.asyncio(loop_scope="module")
async def test_the_database_refuses_a_negative_count_on_every_new_column(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """Every one of the thirteen, not only the ones a service writes.

    The service refuses these long before they reach here. This exists because a
    constraint is what makes the refusal true of *every* writer, including a
    repair script somebody runs against the database directly.
    """
    database, dsn = stopped_at_0026
    ids = await _seed(database)
    await upgrade_to(dsn, "0027")

    # Built as a lightweight table expression rather than through the ORM model
    # or a hand-built string. The model is the wrong tool here: it describes the
    # schema at **head**, and this test runs against 0027 - inserting through it
    # would name ``provider_reading_key``, which Step 1F.2.4b adds in 0028, and
    # the insert would fail on a missing column instead of on the constraint
    # under test. A literal string is the wrong tool too: the column name varies
    # per iteration and cannot be a bind parameter.
    snapshots = sa.table(
        SNAPSHOTS,
        sa.column("id"),
        sa.column("channel_id"),
        sa.column("observed_at"),
        sa.column("source"),
        *(sa.column(name) for name in CHANNEL_WINDOW_METRIC_COLUMNS),
    )
    base = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    for offset, column in enumerate(CHANNEL_WINDOW_METRIC_COLUMNS, start=1):
        with pytest.raises(Exception, match="window_metrics_not_negative"):
            async with database.transaction() as session:
                await session.execute(
                    sa.insert(snapshots).values(
                        id=uuid.uuid4(),
                        channel_id=ids["channel"],
                        observed_at=base - timedelta(hours=offset),
                        source=PrMetricSource.MANUAL.value,
                        **{column: -1},
                    )
                )

    # And a whitespace handle is refused on the channel.
    with pytest.raises(Exception, match="handle_not_empty"):
        async with database.transaction() as session:
            await session.execute(
                text("UPDATE pr_channels SET handle = '   ' WHERE id = :id"),
                {"id": ids["channel"]},
            )


@pytest.mark.asyncio(loop_scope="module")
async def test_the_history_stays_append_only_at_the_row_level(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """Two readings at one instant from one source are still refused.

    0013's unique index is what makes "record a corrected reading" the only
    correction path there is, and Step 1F.2.4a's whole append-only story rests
    on it - so it is asserted here rather than assumed.
    """
    database, dsn = stopped_at_0026
    ids = await _seed(database)
    await upgrade_to(dsn, "0027")

    moment = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_channel_metric_snapshots "
                "(id, channel_id, observed_at, source, followers, created_at) "
                "VALUES (:id, :channel, :at, 'MANUAL', 100, now())"
            ),
            {"id": uuid.uuid4(), "channel": ids["channel"], "at": moment},
        )

    with pytest.raises(Exception, match="uq_pr_channel_metric_snapshots_channel_observed_source"):
        async with database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_channel_metric_snapshots "
                    "(id, channel_id, observed_at, source, followers, created_at) "
                    "VALUES (:id, :channel, :at, 'MANUAL', 200, now())"
                ),
                {"id": uuid.uuid4(), "channel": ids["channel"], "at": moment},
            )

    # The same instant from a *different* source is kept, and the disagreement
    # is visible - which is 0013's design and is why the source is in the key.
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_channel_metric_snapshots "
                "(id, channel_id, observed_at, source, followers, created_at) "
                "VALUES (:id, :channel, :at, 'IMPORT', 200, now())"
            ),
            {"id": uuid.uuid4(), "channel": ids["channel"], "at": moment},
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_the_downgrade_and_the_reupgrade_both_work(
    stopped_at_0026: tuple[Database, str],
) -> None:
    """0026 -> 0027 -> 0026 -> 0027, with the seeded rows watched throughout.

    What a downgrade loses is real and is stated in the README: the thirteen
    metric columns, the recorder, and every handle. What it must **not** lose is
    the snapshot rows themselves or anything 0013 put on them.
    """
    database, dsn = stopped_at_0026
    ids = await _seed(database)
    await upgrade_to(dsn, "0027")

    async with database.transaction() as session:
        await session.execute(
            text(
                "UPDATE pr_channel_metric_snapshots "
                "SET views_30d = 3102441, recorded_by_user_id = :user WHERE id = :id"
            ),
            {"id": ids["snapshot"], "user": ids["user"]},
        )
        await session.execute(
            text("UPDATE pr_channels SET handle = '@drtien' WHERE id = :id"),
            {"id": ids["channel"]},
        )

    await downgrade_to(dsn, "0026")
    assert await _columns(database, SNAPSHOTS) == COLUMNS_0013
    assert "handle" not in await _columns(database, CHANNELS)

    async with database.transaction() as session:
        surviving = (
            await session.execute(
                text("SELECT followers, views FROM pr_channel_metric_snapshots WHERE id = :id"),
                {"id": ids["snapshot"]},
            )
        ).one()
    assert surviving.followers == 1000
    assert surviving.views == 5000, "the row survives; only 1F.2.4a's columns go"

    await upgrade_to(dsn, "0027")
    assert await _columns(database, SNAPSHOTS) == COLUMNS_0013 | COLUMNS_0027
    assert "handle" in await _columns(database, CHANNELS)
