"""Migration 0024, on a database built the way production builds one.

Three claims, and only PostgreSQL can settle them:

* **the column is nullable and nothing was backfilled.** A row written at 0023
  is still ``NULL`` at 0024 - not ``'UNCLASSIFIED'``, not a format guessed from
  its title. This is the decision 0024 rests on, and a well-meaning
  ``op.execute`` added later would break it silently;
* **the resources table has the constraints it claims**: two ``RESTRICT``
  foreign keys, two non-empty checks, and the one composite index;
* **the downgrade actually works.** Index and constraint names that do not match
  what was created are the classic Alembic failure - revision 0021 exists to
  repair exactly that - so this upgrades, downgrades and upgrades again, and
  checks the schema is gone in between and back afterwards.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and every PR migration test follows.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_content_type_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_ctype_*`` database and drops
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
from meobot.domain.pr.models import PrContentResourceType, PrContentType
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

RESOURCES = "pr_content_resources"
CONTENT_ITEMS = "pr_content_items"
RESOURCES_INDEX = "ix_pr_content_resources_content_required"


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0023() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0023**, with its DSN so a test can go further.

    One short of head, so a row can be written *before* the column exists and
    observed afterwards - which is the only way to prove nothing backfilled it.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_ctype_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0023")

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database, dsn
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def _seed_content(database: Database, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    """One user, one brand and one content item, as raw SQL.

    Raw because the ORM now knows about ``content_type`` and this has to write a
    row the way an *older deployment* would have - without the column at all.
    """
    async with database.transaction() as session:
        user_id = uuid.uuid4()
        brand_id = uuid.uuid4()
        content_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
                "VALUES (:id, :name, 'EMPLOYEE', true, now(), now())"
            ),
            {"id": user_id, "name": "Nguyễn A"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_brands (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, :name, 'ACTIVE', now(), now())"
            ),
            {"id": brand_id, "code": f"BR-{uuid.uuid4().hex[:6]}", "name": "Apexmed"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_content_items "
                "(id, code, title, brand_id, priority, workflow_stage, owner_user_id, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (:id, :code, :title, :brand, 'NORMAL', 'IDEA', :user, :user, "
                " now(), now())"
            ),
            {
                "id": content_id,
                "code": f"CNT-2026-{uuid.uuid4().int % 1000000:06d}",
                "title": title,
                "brand": brand_id,
                "user": user_id,
            },
        )
        return user_id, content_id


async def test_0023_has_neither_the_column_nor_the_table(
    stopped_at_0023: tuple[Database, str],
) -> None:
    """The starting state, asserted so the tests below mean something."""
    database, _ = stopped_at_0023
    async with database.transaction() as session:
        column = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = 'content_type'"
            ),
            {"table": CONTENT_ITEMS},
        )
        table = await session.scalar(text("SELECT to_regclass(:name)"), {"name": RESOURCES})
    assert column == 0
    assert table is None


async def test_0024_leaves_historical_rows_unclassified(
    stopped_at_0023: tuple[Database, str],
) -> None:
    """**The decision this revision rests on.** No backfill, and no guess.

    The title says "Video ngắn" and would be perfectly good evidence for one.
    Nothing guesses: the row comes out the other side ``NULL``, which is what
    *Chưa phân loại* means.
    """
    database, dsn = stopped_at_0023
    _, content_id = await _seed_content(database, "Video ngắn về chăm sóc da")

    await upgrade_to(dsn, "0024")

    async with database.transaction() as session:
        stored = await session.scalar(
            text("SELECT content_type FROM pr_content_items WHERE id = :id"), {"id": content_id}
        )
        classified = await session.scalar(
            text("SELECT count(*) FROM pr_content_items WHERE content_type IS NOT NULL")
        )
    assert stored is None
    assert classified == 0, "0024 must not classify anything"


async def test_0024_stores_every_content_type(stopped_at_0023: tuple[Database, str]) -> None:
    """The column accepts the six, and the vocabulary matches the enum."""
    database, dsn = stopped_at_0023
    await upgrade_to(dsn, "0024")
    _, content_id = await _seed_content(database, "Bài để phân loại")

    for content_type in PrContentType:
        async with database.transaction() as session:
            await session.execute(
                text("UPDATE pr_content_items SET content_type = :value WHERE id = :id"),
                {"value": content_type.value, "id": content_id},
            )
            stored = await session.scalar(
                text("SELECT content_type FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
        assert PrContentType(stored) is content_type


async def test_0024_creates_the_resource_table_with_its_guards(
    stopped_at_0023: tuple[Database, str],
) -> None:
    """The table, its one index, its two RESTRICT keys and its two checks."""
    database, dsn = stopped_at_0023
    await upgrade_to(dsn, "0024")

    async with database.transaction() as session:
        indexes = set(
            (
                await session.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                    {"table": RESOURCES},
                )
            )
            .scalars()
            .all()
        )
        assert {"pk_pr_content_resources", RESOURCES_INDEX} <= indexes
        # Deliberately no index on ``resource_type``: nothing filters by it, and
        # this asserts the decision rather than leaving it to drift.
        assert not any("resource_type" in name for name in indexes)

        # ``RESTRICT`` on both, which is the backstop that turns a table missing
        # from the explicit delete plan into a failed transaction.
        rules = dict(
            (
                await session.execute(
                    text(
                        "SELECT conname, confdeltype::text FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:table) AND contype = 'f'"
                    ),
                    {"table": RESOURCES},
                )
            ).all()  # type: ignore[arg-type]
        )
        assert rules == {
            "fk_content_resource_content": "r",
            "fk_content_resource_added_by": "r",
        }

        checks = set(
            (
                await session.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:table) AND contype = 'c'"
                    ),
                    {"table": RESOURCES},
                )
            )
            .scalars()
            .all()
        )
        assert {
            "ck_pr_content_resources_label_not_empty",
            "ck_pr_content_resources_location_not_empty",
        } <= checks


async def test_the_resource_table_refuses_a_blank_label(
    stopped_at_0023: tuple[Database, str],
) -> None:
    """The check constraint, exercised. A row titled by its own URL is the thing
    the label exists to prevent, and a blank one is that with extra steps."""
    database, dsn = stopped_at_0023
    await upgrade_to(dsn, "0024")
    user_id, content_id = await _seed_content(database, "Bài có tài nguyên")

    insert = text(
        "INSERT INTO pr_content_resources "
        "(id, content_id, resource_type, label, location, required_for_review, "
        " added_by_user_id, created_at, updated_at) "
        "VALUES (:id, :content, :type, :label, :location, false, :user, now(), now())"
    )
    payload = {
        "content": content_id,
        "type": PrContentResourceType.REFERENCE.value,
        "location": "https://drive.google.com/file/d/1AbCdEf/view",
        "user": user_id,
    }

    async with database.transaction() as session:
        await session.execute(insert, {**payload, "id": uuid.uuid4(), "label": "Brief"})

    with pytest.raises(Exception, match="label_not_empty"):
        async with database.transaction() as session:
            await session.execute(insert, {**payload, "id": uuid.uuid4(), "label": "   "})


async def test_0024_downgrades_and_upgrades_again(
    stopped_at_0023: tuple[Database, str],
) -> None:
    """The round trip, which is where constraint-name mistakes surface.

    An index dropped by a name that does not exist fails here and nowhere else -
    the upgrade path never touches it. Revision 0021 was written to repair
    exactly this class of defect, so it is asserted rather than assumed.
    """
    database, dsn = stopped_at_0023
    await upgrade_to(dsn, "0024")
    _, content_id = await _seed_content(database, "Bài sẽ mất tài nguyên")

    await downgrade_to(dsn, "0023")
    async with database.transaction() as session:
        assert await session.scalar(text("SELECT to_regclass(:n)"), {"n": RESOURCES}) is None
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'content_type'"
                ),
                {"t": CONTENT_ITEMS},
            )
            == 0
        )
        # The content itself is untouched by either direction.
        assert (
            await session.scalar(
                text("SELECT count(*) FROM pr_content_items WHERE id = :id"), {"id": content_id}
            )
            == 1
        )

    await upgrade_to(dsn, "0024")
    async with database.transaction() as session:
        assert await session.scalar(text("SELECT to_regclass(:n)"), {"n": RESOURCES}) is not None
        assert (
            await session.scalar(
                text("SELECT content_type FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
            is None
        )
