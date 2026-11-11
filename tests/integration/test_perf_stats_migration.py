"""0103: ``perf_stats_hourly`` (plan docs/ops/SCALING_PLAN.md D).

Runs the real migrations on a scratch PostgreSQL database: the table matches
the model, the summing upsert works on PostgreSQL, and a downgrade drops it.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.application.ops.perf_stats import PerfCollector, save_tallies
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.session import Database
from meobot.domain.ops.perf_stats import PerfKind
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, downgrade_to, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
AT = datetime(2026, 11, 11, 3, 15, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0103_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0103")
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


async def test_01_the_table_matches_the_model_and_sums_on_conflict(
    dsn: str, database: Database
) -> None:
    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item
            for item in compare_metadata(context, Base.metadata)
            if "perf_stats_hourly" in repr(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []

    for ms in (40, 900):
        collector = PerfCollector()
        collector.record(PerfKind.HTTP, "/api/tasks/{ref}", ms, method="GET", at=AT)
        async with database.transaction() as session:
            await save_tallies(session, collector.take())
    async with database.engine.begin() as connection:
        row = (
            await connection.execute(
                text("SELECT count, total_ms, max_ms, b0, b5 FROM perf_stats_hourly")
            )
        ).one()
    assert tuple(row) == (2, 940, 900, 1, 1)

    await database.engine.dispose()
    await downgrade_to(dsn, "0102")
    async with database.engine.begin() as connection:
        gone = await connection.scalar(text("SELECT to_regclass('perf_stats_hourly')"))
    assert gone is None
    await database.engine.dispose()
    await upgrade_to(dsn, "0103")
