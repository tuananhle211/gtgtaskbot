"""Migration 0025, on a database built the way production builds one.

Four claims, and only PostgreSQL can settle them:

* **the two new tables have the constraints they claim**: ``RESTRICT`` foreign
  keys throughout, the non-empty checks, and the two indexes;
* **the publication columns are added without touching what is already there.**
  A publication row written at 0024 still exists at 0025, still has its code, its
  channel and its instant, and carries ``NULL`` in both new output references -
  which is the load-bearing decision of the revision. A well-meaning
  ``op.execute`` added later to "fix" those nulls would be fabricating production
  lineage, and this is what would catch it;
* **the ``CHECK`` is "never both" and not XOR.** Asserted by inserting the three
  shapes that must be legal - neither, submission-only, derivative-only - and the
  one that must not;
* **the downgrade actually works.** Index and constraint names that do not match
  what was created are the classic Alembic failure - revision 0021 exists to
  repair exactly that - so this upgrades, downgrades and upgrades again, and
  checks the schema is gone in between and back afterwards.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and every PR migration test follows.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_derivative_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_deriv_*`` database and drops
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
from meobot.domain.pr.models import PrContentDerivativeType
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

DERIVATIVES = "pr_content_derivatives"
DESTINATIONS = "pr_content_destinations"
PUBLICATIONS = "pr_publications"

DERIVATIVES_INDEX = "ix_pr_content_derivatives_content_created"
DESTINATIONS_INDEX = "ix_pr_content_destinations_content"
PUBLICATIONS_INDEX = "ix_pr_publications_content_published"

OUTPUT_CHECK = "ck_pr_publications_output_not_both"

#: The columns 0025 adds to an existing table. Nothing else about
#: ``pr_publications`` may move.
ADDED_COLUMNS = frozenset({"production_submission_id", "derivative_id", "note"})


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0024() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0024**, with its DSN so a test can go further.

    One short of head, so a publication can be written *before* the output
    columns exist and observed afterwards - which is the only way to prove
    nothing backfilled them.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_deriv_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0024")

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
    """A user, a brand, a platform, a channel, a content item and one publication.

    Raw SQL, because the ORM now knows about the 0025 columns and this has to
    write rows the way an *older deployment* would have - without them.
    """
    ids = {key: uuid.uuid4() for key in ("user", "brand", "platform", "channel", "content", "pub")}
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
                "INSERT INTO pr_brands (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, 'Apexmed', 'ACTIVE', now(), now())"
            ),
            {"id": ids["brand"], "code": f"BR-{uuid.uuid4().hex[:6]}"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_platforms (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, 'Website', 'ACTIVE', now(), now())"
            ),
            {"id": ids["platform"], "code": f"PL{uuid.uuid4().hex[:6].upper()}"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_channels "
                "(id, code, name, platform_id, brand_id, category, status, created_at, updated_at) "
                "VALUES (:id, :code, 'Apexmed Website', :platform, :brand, 'SCALE', 'ACTIVE', "
                " now(), now())"
            ),
            {
                "id": ids["channel"],
                "code": f"CH-{uuid.uuid4().hex[:6]}",
                "platform": ids["platform"],
                "brand": ids["brand"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO pr_content_items "
                "(id, code, title, brand_id, priority, workflow_stage, owner_user_id, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (:id, :code, 'Bài cũ', :brand, 'NORMAL', 'PUBLISHED', :user, :user, "
                " now(), now())"
            ),
            {
                "id": ids["content"],
                "code": f"CNT-2026-{uuid.uuid4().int % 1000000:06d}",
                "brand": ids["brand"],
                "user": ids["user"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO pr_publications "
                "(id, code, content_id, channel_id, published_at, url, status, "
                " created_at, updated_at) "
                "VALUES (:id, :code, :content, :channel, now(), 'https://example.test/old', "
                " 'PUBLISHED', now(), now())"
            ),
            {
                "id": ids["pub"],
                "code": f"PUB-2026-{uuid.uuid4().int % 1000000:06d}",
                "content": ids["content"],
                "channel": ids["channel"],
            },
        )
    return ids


async def _columns(database: Database, table: str) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
        return set(found.scalars().all())


async def _tables(database: Database) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        return set(found.scalars().all())


async def _indexes(database: Database, table: str) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = :t"), {"t": table}
        )
        return set(found.scalars().all())


@pytest.mark.asyncio(loop_scope="module")
async def test_0024_has_neither_table_nor_the_output_columns(
    stopped_at_0024: tuple[Database, str],
) -> None:
    """The starting point, so the rest of the file is measuring something."""
    database, _ = stopped_at_0024
    tables = await _tables(database)
    assert DERIVATIVES not in tables
    assert DESTINATIONS not in tables
    assert ADDED_COLUMNS.isdisjoint(await _columns(database, PUBLICATIONS))


@pytest.mark.asyncio(loop_scope="module")
async def test_0025_adds_the_tables_and_leaves_the_old_row_alone(
    stopped_at_0024: tuple[Database, str],
) -> None:
    """The load-bearing claim: nothing is backfilled and nothing is lost.

    A publication written at 0024 survives 0025 with its code, its channel and
    its instant, and with ``NULL`` in both output references - which is a real,
    permanent state. Inventing a submission or a derivative for it to satisfy an
    XOR would be writing production history that never happened.
    """
    database, dsn = stopped_at_0024
    ids = await _seed(database)

    await upgrade_to(dsn, "0025")

    tables = await _tables(database)
    assert DERIVATIVES in tables
    assert DESTINATIONS in tables
    assert await _columns(database, PUBLICATIONS) >= ADDED_COLUMNS

    async with database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT code, channel_id, url, production_submission_id, derivative_id, note "
                    "FROM pr_publications WHERE id = :id"
                ),
                {"id": ids["pub"]},
            )
        ).one()
    assert row.code.startswith("PUB-")
    assert row.channel_id == ids["channel"]
    assert row.url == "https://example.test/old"
    # Both null, permanently and legitimately.
    assert row.production_submission_id is None
    assert row.derivative_id is None
    assert row.note is None


@pytest.mark.asyncio(loop_scope="module")
async def test_the_indexes_and_foreign_keys_are_what_the_revision_claims(
    stopped_at_0024: tuple[Database, str],
) -> None:
    """The names in the migration are the names in the database.

    Short and explicit, because the generated form concatenates two long table
    names and runs past PostgreSQL's 63-byte identifier limit - the defect 0021
    exists to repair. ``RESTRICT`` throughout, so a table forgotten from the
    lifecycle delete plan is a failed transaction rather than a half-deleted item.
    """
    database, dsn = stopped_at_0024
    await upgrade_to(dsn, "0025")

    assert DERIVATIVES_INDEX in await _indexes(database, DERIVATIVES)
    assert DESTINATIONS_INDEX in await _indexes(database, DESTINATIONS)
    assert PUBLICATIONS_INDEX in await _indexes(database, PUBLICATIONS)

    async with database.transaction() as session:
        found = await session.execute(
            text(
                "SELECT conname, confdeltype::text FROM pg_constraint "
                "WHERE contype = 'f' AND conrelid::regclass::text IN "
                "(:derivatives, :destinations, :publications)"
            ),
            {
                "derivatives": DERIVATIVES,
                "destinations": DESTINATIONS,
                "publications": PUBLICATIONS,
            },
        )
        keys = dict(found.all())  # type: ignore[arg-type]
    for name in (
        "fk_content_derivative_content",
        "fk_content_derivative_source",
        "fk_content_derivative_author",
        "fk_content_destination_content",
        "fk_content_destination_added_by",
        "fk_publication_submission",
        "fk_publication_derivative",
    ):
        assert keys.get(name) == "r", name

    async with database.transaction() as session:
        checks = await session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' AND "
                "conrelid::regclass::text IN (:derivatives, :destinations, :publications)"
            ),
            {
                "derivatives": DERIVATIVES,
                "destinations": DESTINATIONS,
                "publications": PUBLICATIONS,
            },
        )
        names = set(checks.scalars().all())
    assert OUTPUT_CHECK in names
    assert "ck_pr_content_derivatives_label_not_empty" in names
    assert "ck_pr_content_derivatives_location_not_empty" in names
    assert "ck_pr_content_destinations_label_not_empty" in names
    assert "ck_pr_content_destinations_url_not_empty" in names


@pytest.mark.asyncio(loop_scope="module")
async def test_the_check_forbids_both_and_permits_neither(
    stopped_at_0024: tuple[Database, str],
) -> None:
    """ "Never both", which is not the same constraint as XOR.

    The interesting property is what it does **not** forbid. Three shapes must be
    insertable - neither, submission-only, derivative-only - and only the fourth
    must fail, because "neither" is what every row written before this revision
    looks like.
    """
    database, dsn = stopped_at_0024
    ids = await _seed(database)
    await upgrade_to(dsn, "0025")

    async with database.transaction() as session:
        version = uuid.uuid4()
        submission = uuid.uuid4()
        derivative = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO pr_content_versions "
                "(id, content_id, version_no, title, created_by_user_id, created_at) "
                "VALUES (:id, :content, 1, 'Bài cũ', :user, now())"
            ),
            {"id": version, "content": ids["content"], "user": ids["user"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_production_submissions "
                "(id, content_id, content_version_id, submission_no, producer_user_id, "
                " submitted_by_user_id, artifact_type, location, created_at) "
                "VALUES (:id, :content, :version, 1, :user, :user, 'DRIVE_LINK', "
                " 'https://drive.google.com/file/d/1/view', now())"
            ),
            {
                "id": submission,
                "content": ids["content"],
                "version": version,
                "user": ids["user"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO pr_content_derivatives "
                "(id, content_id, source_submission_id, derivative_type, label, location, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (:id, :content, :source, :kind, 'TikTok cut 25s', "
                " 'https://drive.google.com/file/d/2/view', :user, now(), now())"
            ),
            {
                "id": derivative,
                "content": ids["content"],
                "source": submission,
                "kind": PrContentDerivativeType.CUTDOWN.value,
                "user": ids["user"],
            },
        )

    async def insert(submission_id: uuid.UUID | None, derivative_id: uuid.UUID | None) -> None:
        async with database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_publications "
                    "(id, code, content_id, channel_id, production_submission_id, derivative_id, "
                    " published_at, status, created_at, updated_at) "
                    "VALUES (:id, :code, :content, :channel, :submission, :derivative, now(), "
                    " 'PUBLISHED', now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "code": f"PUB-2026-{uuid.uuid4().int % 1000000:06d}",
                    "content": ids["content"],
                    "channel": ids["channel"],
                    "submission": submission_id,
                    "derivative": derivative_id,
                },
            )

    # Legal, all three - the third being the shape every legacy row has.
    await insert(submission, None)
    await insert(None, derivative)
    await insert(None, None)

    with pytest.raises(Exception, match=OUTPUT_CHECK):
        await insert(submission, derivative)


@pytest.mark.asyncio(loop_scope="module")
async def test_the_round_trip(stopped_at_0024: tuple[Database, str]) -> None:
    """0024 -> 0025 -> 0024 -> 0025, which is where name mistakes surface.

    An index dropped by a name that does not exist, or a constraint the migration
    named one way and drops another, fails here and nowhere else - and would fail
    on the NAS during a rollback, which is the worst possible moment.

    The publication row survives the whole trip. Its output references do not,
    and that is the honest cost written down: downgrading drops the columns, so
    what is lost is the record of *which file* went out, never the record that it
    did.
    """
    database, dsn = stopped_at_0024
    ids = await _seed(database)

    await upgrade_to(dsn, "0025")
    assert DERIVATIVES in await _tables(database)

    await downgrade_to(dsn, "0024")
    tables = await _tables(database)
    assert DERIVATIVES not in tables
    assert DESTINATIONS not in tables
    assert ADDED_COLUMNS.isdisjoint(await _columns(database, PUBLICATIONS))
    async with database.transaction() as session:
        survived = await session.execute(
            text("SELECT count(*) FROM pr_publications WHERE id = :id"), {"id": ids["pub"]}
        )
        assert survived.scalar() == 1

    await upgrade_to(dsn, "0025")
    tables = await _tables(database)
    assert DERIVATIVES in tables
    assert DESTINATIONS in tables
    assert await _columns(database, PUBLICATIONS) >= ADDED_COLUMNS
    assert DERIVATIVES_INDEX in await _indexes(database, DERIVATIVES)
    assert PUBLICATIONS_INDEX in await _indexes(database, PUBLICATIONS)
