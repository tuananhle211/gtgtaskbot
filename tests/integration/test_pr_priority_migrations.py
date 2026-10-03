"""Migration 0023, on a database built the way production builds one.

Two claims that can only be checked against a real PostgreSQL, and both of them
are claims 0023's docstring makes in prose:

* **the ``priority`` columns need no ``ALTER``**, because 0012 created them as
  bare ``VARCHAR(20)`` with no vocabulary ``CHECK``. That is an assertion about
  what SQLAlchemy's ``sa.Enum(native_enum=False)`` actually emitted on the day
  0012 ran, and reading it back off ``information_schema`` is the only way to
  know rather than believe. If it turns out a constraint *is* there, this fails
  and 0023 is wrong;
* **``LOW`` rows are moved rather than stranded.** The backfill is the reason
  this revision exists at all - an enum member removed without moving its rows
  makes every later read of those rows a ``LookupError`` - so the test writes
  ``'LOW'`` at 0022, migrates, and reads back ``'NORMAL'``.

That second one is why this file migrates in two steps instead of going straight
to head: rows written *before* the migration are the only ones the backfill can
be observed on, and a database created at head has none.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and every PR migration test follows.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_priority_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_priority_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
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
from meobot.domain.pr.models import PrPriority
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

NOTIFICATIONS = "user_notifications"


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0022() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0022**, with its DSN so a test can go further.

    Deliberately one short of head. Everything below is about the difference
    0023 makes, and a fixture that had already applied it could only assert the
    end state - not that the transition does what it says.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_priority_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0022")

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database, dsn
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def _seed_person_and_content(
    database: Database, priority: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """One user and one content item at ``priority``, written as raw SQL.

    Raw SQL on purpose: the ORM would refuse to write ``'LOW'`` now that
    :class:`~meobot.domain.pr.models.PrPriority` has no such member, which is
    precisely the state this test needs to create. A row written by an older
    deployment does not care what today's enum says.
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
                "VALUES (:id, :code, :title, :brand, :priority, 'IDEA', :user, :user, "
                " now(), now())"
            ),
            {
                "id": content_id,
                "code": f"CNT-2026-{uuid.uuid4().int % 1000000:06d}",
                "title": "Bài cũ",
                "brand": brand_id,
                "priority": priority,
                "user": user_id,
            },
        )
        return user_id, content_id


async def test_0012_left_no_vocabulary_check_on_priority(
    stopped_at_0022: tuple[Database, str],
) -> None:
    """The finding 0023 rests on, read off the live catalogue.

    ``sa.Enum(..., native_enum=False)`` defaults to ``create_constraint=False``
    since SQLAlchemy 1.4, so what 0012 emitted was a bare ``VARCHAR(20)``. If a
    future SQLAlchemy changed that default, 0023 would silently be a migration
    that adds a value the column rejects - and this is the assertion that would
    catch it.
    """
    database, _ = stopped_at_0022
    async with database.transaction() as session:
        for table in ("pr_content_items", "pr_tasks"):
            column = (
                await session.execute(
                    text(
                        "SELECT data_type, character_maximum_length, column_default, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = 'priority'"
                    ),
                    {"table": table},
                )
            ).one()
            assert column.data_type == "character varying", table
            assert column.character_maximum_length == 20, table
            assert column.is_nullable == "NO", table
            assert "NORMAL" in (column.column_default or ""), table

            # No CHECK mentioning the vocabulary. The two `*_not_empty` checks on
            # `pr_content_items` are about `code` and `title` and are unrelated.
            checks = (
                (
                    await session.execute(
                        text(
                            "SELECT cc.check_clause FROM information_schema.check_constraints cc "
                            "JOIN information_schema.constraint_column_usage ccu "
                            "  ON ccu.constraint_name = cc.constraint_name "
                            "WHERE ccu.table_name = :table AND ccu.column_name = 'priority'"
                        ),
                        {"table": table},
                    )
                )
                .scalars()
                .all()
            )
            assert not [clause for clause in checks if "NORMAL" in clause], (
                f"{table}.priority has a vocabulary CHECK; 0023 must widen it"
            )


async def test_0023_moves_low_rows_to_normal(stopped_at_0022: tuple[Database, str]) -> None:
    """The backfill, observed across the transition.

    Written at 0022 as ``'LOW'`` - a value no current enum member matches - and
    read back at 0023 as ``'NORMAL'``. Without this, the row survives the
    migration and every subsequent ORM read of it raises.
    """
    database, dsn = stopped_at_0022
    _, content_id = await _seed_person_and_content(database, "LOW")

    async with database.transaction() as session:
        before = await session.scalar(
            text("SELECT priority FROM pr_content_items WHERE id = :id"), {"id": content_id}
        )
    assert before == "LOW", "the fixture must create the state the backfill repairs"

    await upgrade_to(dsn, "0023")

    async with database.transaction() as session:
        after = await session.scalar(
            text("SELECT priority FROM pr_content_items WHERE id = :id"), {"id": content_id}
        )
        stragglers = await session.scalar(
            text("SELECT count(*) FROM pr_content_items WHERE priority = 'LOW'")
        )
    assert after == "NORMAL"
    assert stragglers == 0
    # And the value it landed on is one the enum can actually coerce.
    assert PrPriority(after) is PrPriority.NORMAL


async def test_0023_stores_critical_without_any_schema_change(
    stopped_at_0022: tuple[Database, str],
) -> None:
    """The other half: the new level fits the column 0012 created."""
    database, dsn = stopped_at_0022
    await upgrade_to(dsn, "0023")
    _, content_id = await _seed_person_and_content(database, PrPriority.CRITICAL.value)

    async with database.transaction() as session:
        stored = await session.scalar(
            text("SELECT priority FROM pr_content_items WHERE id = :id"), {"id": content_id}
        )
    assert PrPriority(stored) is PrPriority.CRITICAL


async def test_0023_creates_the_notification_inbox(
    stopped_at_0022: tuple[Database, str],
) -> None:
    """The table, its two read indexes, and the constraints that guard it."""
    database, dsn = stopped_at_0022
    await upgrade_to(dsn, "0023")

    async with database.transaction() as session:
        indexes = set(
            (
                await session.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                    {"table": NOTIFICATIONS},
                )
            )
            .scalars()
            .all()
        )
        assert {
            "pk_user_notifications",
            "ix_user_notifications_recipient_created",
            "ix_user_notifications_recipient_unread",
            "uq_user_notifications_idempotency_key",
        } <= indexes

        # A deleted person's inbox has no other owner, and nothing points at it.
        # ``confdeltype`` is a ``"char"``, which asyncpg hands back as bytes.
        rule = await session.scalar(
            text(
                "SELECT confdeltype::text FROM pg_constraint "
                "WHERE conname = 'fk_user_notifications_recipient'"
            )
        )
        assert rule == "c", "recipient_user_id must cascade on delete"


async def test_the_inbox_refuses_a_duplicate_event(
    stopped_at_0022: tuple[Database, str],
) -> None:
    """One business event, one inbox row - enforced by the database.

    The same guarantee ``outbound_messages.idempotency_key`` gives Telegram. A
    retried workflow transaction must not produce two identical notifications.
    """
    database, dsn = stopped_at_0022
    await upgrade_to(dsn, "0023")
    user_id, content_id = await _seed_person_and_content(database, PrPriority.NORMAL.value)

    insert = text(
        "INSERT INTO user_notifications "
        "(id, recipient_user_id, event_type, title, body, target_kind, target_id, "
        " idempotency_key, created_at, updated_at) "
        "VALUES (:id, :user, 'pr_content_approved', :title, :body, 'pr_content', :target, "
        " :key, now(), now())"
    )
    payload = {
        "user": user_id,
        "title": "Nội dung đã được duyệt",
        "body": "“Bài cũ” đã được duyệt.",
        "target": content_id,
        "key": "pr_content_approved:fixed",
    }

    async with database.transaction() as session:
        await session.execute(insert, {**payload, "id": uuid.uuid4()})

    with pytest.raises(Exception, match="uq_user_notifications_idempotency_key"):
        async with database.transaction() as session:
            await session.execute(insert, {**payload, "id": uuid.uuid4()})
