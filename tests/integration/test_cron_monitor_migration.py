"""0077: the job monitor's tables.

Runs the real migration on a scratch PostgreSQL database: the three tables
match the models, a job's runs and incidents go with it, and the migration
goes down and up again.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.session import Database
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
TABLES = ("'cron_jobs'", "'cron_job_runs'", "'cron_job_incidents'")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0077_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0077")
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
        return [
            item
            for item in compare_metadata(context, Base.metadata)
            if any(table in repr(item) for table in TABLES)
            # 0080 adds the per-job alert columns (test_cron_notify_migration).
            and not ("Column('notify" in repr(item) and item[0] == "add_column")
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_a_job_takes_its_history_with_it(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO cron_jobs (key, retries, retry_delay_seconds, alert_after,"
                " keep_successes) VALUES ('t.job', 2, 1800, 1, true)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO cron_job_runs (id, job_key, trigger, attempt, status)"
                " VALUES (:id, 't.job', 'SCHEDULE', 1, 'FAILED')"
            ),
            {"id": uuid.uuid4()},
        )
        await connection.execute(
            text(
                "INSERT INTO cron_job_incidents (id, job_key, kind, failures)"
                " VALUES (:id, 't.job', 'FAILED', 1)"
            ),
            {"id": uuid.uuid4()},
        )
        await connection.execute(text("DELETE FROM cron_jobs WHERE key = 't.job'"))
        left = (
            await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM cron_job_runs)"
                    " + (SELECT count(*) FROM cron_job_incidents)"
                )
            )
        ).scalar_one()
    assert left == 0


async def test_03_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0076")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            gone = (
                await connection.execute(text("SELECT to_regclass('cron_jobs') IS NULL"))
            ).scalar_one()
        assert gone is True
    finally:
        await engine.dispose()
    await upgrade_to(dsn, "0077")
