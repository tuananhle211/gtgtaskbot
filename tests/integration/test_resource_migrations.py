"""``0065`` against a real PostgreSQL: the tables, the seed, the triggers, the roundtrip.

* **the models describe the migration** - ``compare_metadata`` over the six
  tables and the ``users`` column is empty at head;
* **the pick-lists are seeded** - every list of the requirement, with each
  status in its group;
* **every department gets the module's default grants** by its function;
* **the histories are append-only in the database** - an ``UPDATE`` and a
  ``DELETE`` on each are refused by the trigger, whoever runs them;
* **the migration goes both ways** - ``0065 -> 0064 -> 0065``.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_resource_migrations.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.session import Database
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    alembic_head,
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

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
NEW_TABLES = (
    "resource_options",
    "resources",
    "resource_code_counters",
    "resource_handovers",
    "resource_status_history",
    "resource_changes",
    # 0066 rides the same chain: the personal permission layer.
    "user_permissions",
)
APPEND_ONLY = ("resource_handovers", "resource_status_history", "resource_changes")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0065_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    db = Database(Settings(**SETTINGS_KWARGS, database_url=dsn))
    try:
        yield db
    finally:
        await db.dispose()


async def test_01_models_and_migration_agree(database: Database) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _diff(connection: object) -> list[object]:
        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [item for item in compare_metadata(context, Base.metadata) if _mentions(item)]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
        columns = {
            row[0]: row[1]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name, column_default FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name = 'employment_status'"
                    )
                )
            ).all()
        }
    assert diff == [], diff
    assert columns and "WORKING" in str(columns["employment_status"])
    assert alembic_head() == "0072"


def _mentions(item: object) -> bool:
    """Whether one autogenerate diff is about the module's own tables (the
    older drift elsewhere in the schema is not this migration's)."""
    rendered = repr(item)
    return any(f"'{table}'" in rendered for table in NEW_TABLES)


async def test_02_the_pick_lists_and_the_grants_are_seeded(database: Database) -> None:
    async with database.engine.connect() as connection:
        kinds = (
            await connection.execute(
                text("SELECT kind, count(*) FROM resource_options GROUP BY kind ORDER BY kind")
            )
        ).all()
        assert dict(kinds) == {
            "HANDOVER": 3,
            "ORIGIN": 6,
            "ROLE": 9,
            # 0072: "Die / Mất" became "Die" + "Mất".
            "STATUS": 7,
            "TWO_FACTOR": 3,
            "TYPE": 7,
        }
        groups = (
            await connection.execute(
                text(
                    "SELECT code, status_group FROM resource_options "
                    "WHERE kind = 'STATUS' ORDER BY sort_order"
                )
            )
        ).all()
        assert dict(groups) == {
            "ACTIVE": "NORMAL",
            "RESTRICTED": "ATTENTION",
            "CHECKPOINT": "ATTENTION",
            "DIE": "LOST",
            "LOST": "LOST",
            "RETURNED": "CLOSED",
            "STOPPED": "CLOSED",
        }
        grants = (
            await connection.execute(
                text(
                    "SELECT d.code, p.position, p.permission, p.scope "
                    "FROM department_permissions p JOIN departments d ON d.id = p.department_id "
                    "WHERE p.module = 'RESOURCES'"
                )
            )
        ).all()
        cells = {
            (code, position, permission): scope for code, position, permission, scope in grants
        }
        assert cells[("TN", "HEAD", "IMPORT")] == "ALL"
        assert cells[("TN", "MEMBER", "EDIT")] == "ALL"
        assert ("TN", "MEMBER", "IMPORT") not in cells
        assert cells[("BGD", "MEMBER", "REPORT")] == "ALL"
        assert cells[("ADS", "HEAD", "VIEW")] == "OWN"
        assert cells[("ADS", "MEMBER", "VIEW")] == "OWN"
        status = (await connection.execute(text("SELECT employment_status FROM users"))).all()
        assert all(row[0] == "WORKING" for row in status)


async def test_03_the_histories_refuse_updates_and_deletes(database: Database) -> None:
    department_id = uuid.uuid4()
    option_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO departments (id, code, name, function, active, sort_order) "
                "VALUES (:id, 'X', 'X', 'NONE', true, 99)"
            ),
            {"id": department_id},
        )
        await connection.execute(
            text(
                "INSERT INTO resource_options (id, kind, code, name, sort_order, active) "
                "VALUES (:id, 'TYPE', 'TEST', 'Test', 99, true)"
            ),
            {"id": option_id},
        )
        handover = (
            await connection.execute(
                text("SELECT id FROM resource_options WHERE kind = 'HANDOVER' LIMIT 1")
            )
        ).scalar_one()
        status = (
            await connection.execute(
                text("SELECT id FROM resource_options WHERE kind = 'STATUS' LIMIT 1")
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO resources (id, code, department_id, type_id, external_id, name, "
                "handover_id, status_id) VALUES (:id, 'X-001', :department, :type, '1', 'x', "
                ":handover, :status)"
            ),
            {
                "id": resource_id,
                "department": department_id,
                "type": option_id,
                "handover": handover,
                "status": status,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO resource_handovers (id, resource_id, reason) "
                "VALUES (:id, :resource, 'NEW')"
            ),
            {"id": uuid.uuid4(), "resource": resource_id},
        )
        await connection.execute(
            text(
                "INSERT INTO resource_status_history (id, resource_id, to_status_id) "
                "VALUES (:id, :resource, :status)"
            ),
            {"id": uuid.uuid4(), "resource": resource_id, "status": status},
        )
        await connection.execute(
            text(
                "INSERT INTO resource_changes (id, resource_id, field) "
                "VALUES (:id, :resource, 'created')"
            ),
            {"id": uuid.uuid4(), "resource": resource_id},
        )
    for table in APPEND_ONLY:
        # Table names from this file's own tuple, not from input.
        column = "field" if table == "resource_changes" else "note"
        for statement in (
            f"UPDATE {table} SET {column} = 'x'",  # noqa: S608
            f"DELETE FROM {table}",  # noqa: S608
        ):
            async with database.engine.connect() as connection:
                with pytest.raises(DBAPIError, match="append-only"):
                    await connection.execute(text(statement))
                await connection.rollback()


async def test_04_the_migration_goes_both_ways(dsn: str) -> None:
    await downgrade_to(dsn, "0064")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            tables = {
                row[0]
                for row in (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).all()
            }
            assert not (set(NEW_TABLES) - {"user_permissions"}) & tables
            columns = {
                row[0]
                for row in (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'users'"
                        )
                    )
                ).all()
            }
            assert "employment_status" not in columns
            grants = (
                await connection.execute(
                    text("SELECT count(*) FROM department_permissions WHERE module = 'RESOURCES'")
                )
            ).scalar_one()
            assert grants == 0
    finally:
        await engine.dispose()
    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            count = (
                await connection.execute(text("SELECT count(*) FROM resource_options"))
            ).scalar_one()
            assert count == 35  # 0072 adds "Mất"
    finally:
        await engine.dispose()
