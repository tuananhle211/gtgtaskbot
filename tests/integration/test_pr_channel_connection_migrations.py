"""Migration 0028, on a database built the way production builds one.

Five claims, and only PostgreSQL can settle them:

* **the tables and the constraints are what the revision claims.** Two tables,
  ``RESTRICT`` on all four foreign keys, the non-empty checks, and the two
  partial unique indexes that carry the actual rules;

* **one live connection per channel and provider, enforced by the database.**
  Two live OAuth grants racing to sync one channel is not a state anybody should
  have to debug, and a *partial* index is what makes it unrepresentable while
  still letting a channel be disconnected and reconnected;

* **one provider reading, one row.** The idempotency index is partial over rows
  that have a key, so many manual snapshots coexist on a shared ``NULL`` while a
  re-fetch of an already-recorded reading is refused. Both halves are asserted,
  because a non-partial index would pass the second and break the first;

* **Step 1F.2.4a's history is untouched.** A channel and its manual snapshots
  written at 0027 come through 0028, back down again and up again byte for byte.
  That is the claim the whole downgrade story rests on;

* **the downgrade works, and loses exactly what it says it loses.** Connections
  and tokens go - they cannot be recovered from anywhere, since the platform
  issued them once - and every metric row stays.

**No token material is printed here, on the way up or down.** The fixture writes
a recognisable marker into the credential column and the tests assert
about its presence and absence rather than echoing it, which is also what makes
a failure message safe to paste into a chat.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_channel_connection_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_conn_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.session import Database
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

CONNECTIONS = "pr_channel_connections"
OAUTH_STATES = "pr_channel_oauth_states"
SNAPSHOTS = "pr_channel_metric_snapshots"

LIVE_INDEX = "uq_pr_channel_connections_live"
READING_KEY_INDEX = "uq_pr_channel_metric_snapshots_reading_key"

#: A stand-in for ciphertext. Not a token, and never echoed by an assertion.
SEALED = "v1:primary:AAAAAAAAAAAAAAAA:ZmFrZS1jaXBoZXJ0ZXh0"

#: The columns as **0028** creates them. Migration 0029 renames one of these -
#: see :data:`EXPECTED_CONNECTION_COLUMNS_0029`.
EXPECTED_CONNECTION_COLUMNS = frozenset(
    {
        "id",
        "channel_id",
        "provider",
        "status",
        "provider_account_id",
        "provider_account_name",
        "provider_account_handle",
        "encrypted_refresh_token",
        "granted_scopes",
        "connected_by_user_id",
        "connected_at",
        "disconnected_at",
        "auto_sync_enabled",
        "sync_status",
        "last_sync_started_at",
        "last_sync_succeeded_at",
        "last_sync_failed_at",
        "last_sync_error_code",
        "last_sync_error_message",
        "consecutive_failures",
        "created_at",
        "updated_at",
    }
)

EXPECTED_STATE_COLUMNS = frozenset(
    {
        "id",
        "state_hash",
        "channel_id",
        "provider",
        "user_id",
        "is_reconnect",
        "expires_at",
        "consumed_at",
        "created_at",
    }
)


#: The same set after 0029, which renames the credential column and nothing
#: else. Step 1F.2.4c: Meta has no refresh token, so a column called
#: ``encrypted_refresh_token`` would have been a lie in the schema.
EXPECTED_CONNECTION_COLUMNS_0029 = (EXPECTED_CONNECTION_COLUMNS - {"encrypted_refresh_token"}) | {
    "encrypted_credential"
}


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0027() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0027**, with its DSN so a test can go further.

    One short of head, so a channel and its manual snapshots exist *before* the
    connector tables do - which is the only way to prove 0028 touched nothing on
    its way past.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_conn_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0027")

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
    """A user, a YouTube platform, a channel and two manual snapshots."""
    ids = {key: uuid.uuid4() for key in ("user", "platform", "channel", "snap_a", "snap_b")}
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
                "VALUES (:id, :code, 'YouTube', true, 'ACTIVE', now(), now())"
            ),
            {"id": ids["platform"], "code": f"YT_{uuid.uuid4().hex[:4].upper()}"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_channels (id, code, name, platform_id, category, status, "
                " created_at, updated_at) "
                "VALUES (:id, :code, 'Apex Media', :platform, 'SCALE', 'ACTIVE', now(), now())"
            ),
            {
                "id": ids["channel"],
                "code": f"CH-{uuid.uuid4().int % 10000:04d}",
                "platform": ids["platform"],
            },
        )
        for key, followers, offset in (("snap_a", 1000, 2), ("snap_b", 1200, 1)):
            await session.execute(
                text(
                    "INSERT INTO pr_channel_metric_snapshots "
                    "(id, channel_id, observed_at, source, followers, views_30d, "
                    " recorded_by_user_id, created_at) "
                    "VALUES (:id, :channel, now() - (:offset * interval '1 day'), 'MANUAL', "
                    " :followers, 5000, :user, now())"
                ),
                {
                    "id": ids[key],
                    "channel": ids["channel"],
                    "offset": offset,
                    "followers": followers,
                    "user": ids["user"],
                },
            )
    return ids


async def _tables(database: Database) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        return set(found.scalars().all())


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


async def _add_connection(
    database: Database,
    ids: dict[str, uuid.UUID],
    *,
    connection_id: uuid.UUID,
    status: str = "CONNECTED",
    account: str = "UCabcdefghijklmnopqrstuv",
) -> None:
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_channel_connections "
                "(id, channel_id, provider, status, provider_account_id, "
                " encrypted_refresh_token, connected_by_user_id, connected_at, "
                " auto_sync_enabled, sync_status, consecutive_failures, "
                " created_at, updated_at) "
                "VALUES (:id, :channel, 'YOUTUBE', :status, :account, :sealed, :user, now(), "
                " true, 'NEVER_SYNCED', 0, now(), now())"
            ),
            {
                "id": connection_id,
                "channel": ids["channel"],
                "status": status,
                "account": account,
                "sealed": SEALED,
                "user": ids["user"],
            },
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_0027_has_neither_connector_table(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """The starting point, so the rest of the file is measuring something."""
    database, _ = stopped_at_0027
    tables = await _tables(database)
    assert CONNECTIONS not in tables
    assert OAUTH_STATES not in tables
    assert "provider_reading_key" not in await _columns(database, SNAPSHOTS)


@pytest.mark.asyncio(loop_scope="module")
async def test_0028_adds_two_tables_and_moves_nothing_else(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """Additive, and provably so.

    Two manual snapshots written at 0027 come through with the same values and
    the same recorder. A well-meaning ``op.execute`` added later is caught here
    rather than on the NAS.
    """
    database, dsn = stopped_at_0027
    ids = await _seed(database)

    await upgrade_to(dsn, "0028")

    assert await _columns(database, CONNECTIONS) == EXPECTED_CONNECTION_COLUMNS
    assert await _columns(database, OAUTH_STATES) == EXPECTED_STATE_COLUMNS
    assert "provider_reading_key" in await _columns(database, SNAPSHOTS)
    assert READING_KEY_INDEX in await _indexes(database, SNAPSHOTS)

    async with database.transaction() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT followers, views_30d, source, recorded_by_user_id, "
                    " provider_reading_key FROM pr_channel_metric_snapshots "
                    "WHERE channel_id = :id ORDER BY followers"
                ),
                {"id": ids["channel"]},
            )
        ).all()
    assert [row.followers for row in rows] == [1000, 1200]
    assert all(row.source == "MANUAL" for row in rows)
    assert all(row.recorded_by_user_id == ids["user"] for row in rows)
    # Nothing is backfilled: a manual reading is not a repeatable fetch and has
    # nothing to deduplicate against.
    assert all(row.provider_reading_key is None for row in rows)


@pytest.mark.asyncio(loop_scope="module")
async def test_the_constraints_are_what_the_revision_claims(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """Names, delete rules and checks, read from the catalog."""
    database, dsn = stopped_at_0027
    await upgrade_to(dsn, "0028")

    async with database.transaction() as session:
        keys = await session.execute(
            text(
                "SELECT conname, confdeltype::text FROM pg_constraint "
                "WHERE contype = 'f' AND conrelid::regclass::text = ANY(:tables)"
            ),
            {"tables": [CONNECTIONS, OAUTH_STATES]},
        )
        found = dict(keys.all())  # type: ignore[arg-type]
    for name in (
        "fk_channel_connection_channel",
        "fk_channel_connection_connected_by",
        "fk_channel_oauth_state_channel",
        "fk_channel_oauth_state_user",
    ):
        assert found.get(name) == "r", name

    async with database.transaction() as session:
        checks = await session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conrelid::regclass::text = ANY(:tables)"
            ),
            {"tables": [CONNECTIONS, OAUTH_STATES]},
        )
        names = set(checks.scalars().all())
    assert "ck_pr_channel_connections_account_id_not_empty" in names
    assert "ck_pr_channel_connections_consecutive_failures_not_negative" in names
    assert "ck_pr_channel_oauth_states_state_hash_not_empty" in names

    assert LIVE_INDEX in await _indexes(database, CONNECTIONS)
    assert "ix_pr_channel_connections_due" in await _indexes(database, CONNECTIONS)


@pytest.mark.asyncio(loop_scope="module")
async def test_only_one_live_connection_per_channel_and_provider(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """The rule that stops two grants racing to sync one channel.

    Partial, so the *history* of connecting and disconnecting is representable:
    a channel taken down and reconnected has two rows and only one live one.
    """
    database, dsn = stopped_at_0027
    ids = await _seed(database)
    await upgrade_to(dsn, "0028")

    await _add_connection(database, ids, connection_id=uuid.uuid4())

    with pytest.raises(Exception, match=LIVE_INDEX):
        await _add_connection(database, ids, connection_id=uuid.uuid4())

    # A disconnected row does not collide - which is what makes reconnecting
    # possible without deleting the record that the channel was once connected.
    await _add_connection(database, ids, connection_id=uuid.uuid4(), status="DISCONNECTED")
    await _add_connection(database, ids, connection_id=uuid.uuid4(), status="DISCONNECTED")


@pytest.mark.asyncio(loop_scope="module")
async def test_one_provider_reading_one_row_and_manual_rows_do_not_collide(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """Both halves of the idempotency index, because a full index breaks one.

    A non-partial unique index over ``(channel_id, provider_reading_key)`` would
    also refuse the duplicate below - and would refuse the *second manual
    snapshot*, since every manual row shares a ``NULL`` key. The seed already
    wrote two of those, so this test would have failed at ``_seed``.
    """
    database, dsn = stopped_at_0027
    ids = await _seed(database)
    await upgrade_to(dsn, "0028")

    fingerprint = "a" * 64

    async def insert(snapshot_id: uuid.UUID, *, offset: int, key: str | None) -> None:
        async with database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_channel_metric_snapshots "
                    "(id, channel_id, observed_at, source, followers, "
                    " provider_reading_key, created_at) "
                    "VALUES (:id, :channel, now() - (:offset * interval '1 hour'), 'API', "
                    " 100, :key, now())"
                ),
                {
                    "id": snapshot_id,
                    "channel": ids["channel"],
                    "offset": offset,
                    "key": key,
                },
            )

    await insert(uuid.uuid4(), offset=1, key=fingerprint)
    with pytest.raises(Exception, match=READING_KEY_INDEX):
        await insert(uuid.uuid4(), offset=2, key=fingerprint)

    # A different reading is fine, and so are more keyless manual rows.
    await insert(uuid.uuid4(), offset=3, key="b" * 64)
    await insert(uuid.uuid4(), offset=4, key=None)
    await insert(uuid.uuid4(), offset=5, key=None)


@pytest.mark.asyncio(loop_scope="module")
async def test_the_downgrade_and_the_reupgrade_both_work(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """0027 -> 0028 -> 0027 -> 0028, with the seeded history watched throughout.

    What a downgrade loses is real and is stated in the README: every connection
    and every stored refresh token, which the platform issued once and will not
    reissue without a person consenting again. What it must **not** lose is a
    single metric row.
    """
    database, dsn = stopped_at_0027
    ids = await _seed(database)
    await upgrade_to(dsn, "0028")
    await _add_connection(database, ids, connection_id=uuid.uuid4())

    await downgrade_to(dsn, "0027")

    tables = await _tables(database)
    assert CONNECTIONS not in tables
    assert OAUTH_STATES not in tables
    assert "provider_reading_key" not in await _columns(database, SNAPSHOTS)

    async with database.transaction() as session:
        surviving = (
            await session.execute(
                text(
                    "SELECT followers, source, recorded_by_user_id "
                    "FROM pr_channel_metric_snapshots WHERE channel_id = :id "
                    "ORDER BY followers"
                ),
                {"id": ids["channel"]},
            )
        ).all()
    assert [row.followers for row in surviving] == [1000, 1200]
    assert all(row.source == "MANUAL" for row in surviving)
    assert all(row.recorded_by_user_id == ids["user"] for row in surviving)

    await upgrade_to(dsn, "0028")
    assert await _columns(database, CONNECTIONS) == EXPECTED_CONNECTION_COLUMNS
    assert "provider_reading_key" in await _columns(database, SNAPSHOTS)
    # And the connection really did go: the token is not recoverable, which is
    # exactly what the README warns about.
    async with database.transaction() as session:
        remaining = (
            await session.execute(text("SELECT count(*) FROM pr_channel_connections"))
        ).scalar()
    assert remaining == 0


# ---------------------------------------------------------------------------
# Step 1F.2.4c: 0029, the credential rename
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_0029_renames_the_credential_column_and_keeps_the_ciphertext(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """The whole of Step 1F.2.4c's schema change, and it is lossless.

    A connection written at 0028 comes through 0029 with the **same ciphertext**
    under the new name. That matters more than it looks: the AES-GCM envelope is
    bound to the connection's *id*, not to the column's name, so a rename cannot
    make a stored credential undecryptable. If it could, this migration would
    silently disconnect every channel on the deployment.
    """
    database, dsn = stopped_at_0027
    ids = await _seed(database)
    await upgrade_to(dsn, "0028")
    connection_id = uuid.uuid4()
    await _add_connection(database, ids, connection_id=connection_id)

    await upgrade_to(dsn, "0029")

    assert await _columns(database, CONNECTIONS) == EXPECTED_CONNECTION_COLUMNS_0029
    async with database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT encrypted_credential, provider_account_id, status "
                    "FROM pr_channel_connections WHERE id = :id"
                ),
                {"id": connection_id},
            )
        ).one()
    assert row.encrypted_credential == SEALED, "the ciphertext is untouched"
    assert row.provider_account_id == "UCabcdefghijklmnopqrstuv"
    assert row.status == "CONNECTED"


@pytest.mark.asyncio(loop_scope="module")
async def test_0029_touches_nothing_else(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """One column, one table. Everything Step 1F.2.4a and b built is unmoved."""
    database, dsn = stopped_at_0027
    ids = await _seed(database)
    await upgrade_to(dsn, "0029")

    assert await _columns(database, OAUTH_STATES) == EXPECTED_STATE_COLUMNS
    assert "provider_reading_key" in await _columns(database, SNAPSHOTS)
    assert LIVE_INDEX in await _indexes(database, CONNECTIONS)
    assert READING_KEY_INDEX in await _indexes(database, SNAPSHOTS)

    async with database.transaction() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT followers, source, recorded_by_user_id "
                    "FROM pr_channel_metric_snapshots WHERE channel_id = :id "
                    "ORDER BY followers"
                ),
                {"id": ids["channel"]},
            )
        ).all()
    assert [row.followers for row in rows] == [1000, 1200]
    assert all(row.source == "MANUAL" for row in rows)


@pytest.mark.asyncio(loop_scope="module")
async def test_0028_to_0029_and_back_and_forward_again(
    stopped_at_0027: tuple[Database, str],
) -> None:
    """0028 -> 0029 -> 0028 -> 0029, with a connection watched throughout.

    A pure rename, so unlike 0028's downgrade this one loses **nothing** - the
    row, the ciphertext and every metric snapshot survive the round trip. What a
    downgrade does reintroduce is the misleading name, which is a problem for a
    schema nobody should be running Meta on rather than a data loss.

    No token material is printed here in either direction: the fixture's marker
    is compared, never echoed.
    """
    database, dsn = stopped_at_0027
    ids = await _seed(database)
    # The fixture's database is shared across this module and earlier tests have
    # already carried it forward, so "go to 0028" has to be a *downgrade* here.
    # ``upgrade_to`` a revision already applied is a no-op, which would leave the
    # insert below writing to a column this revision renamed.
    await upgrade_to(dsn, "0029")
    await downgrade_to(dsn, "0028")
    connection_id = uuid.uuid4()
    await _add_connection(database, ids, connection_id=connection_id)

    await upgrade_to(dsn, "0029")
    assert "encrypted_credential" in await _columns(database, CONNECTIONS)

    await downgrade_to(dsn, "0028")
    columns = await _columns(database, CONNECTIONS)
    assert "encrypted_refresh_token" in columns
    assert "encrypted_credential" not in columns
    async with database.transaction() as session:
        survived = (
            await session.execute(
                text("SELECT encrypted_refresh_token FROM pr_channel_connections WHERE id = :id"),
                {"id": connection_id},
            )
        ).one()
    assert survived.encrypted_refresh_token == SEALED, "lossless in both directions"

    await upgrade_to(dsn, "0029")
    assert await _columns(database, CONNECTIONS) == EXPECTED_CONNECTION_COLUMNS_0029
    async with database.transaction() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_channel_metric_snapshots WHERE channel_id = :id"),
                {"id": ids["channel"]},
            )
        ).scalar()
    assert count == 2, "metric history is untouched by either direction"
