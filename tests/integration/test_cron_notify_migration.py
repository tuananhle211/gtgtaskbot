"""0080: per-job alert settings for "Tác vụ định kỳ".

Runs the real migration on a scratch PostgreSQL database: the new columns match
the model, the Meta pulls come out with alerts on and the ADS heads seated now
as recipients, every other job off, and the migration goes down and up again.

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
HEAD = uuid.uuid4()
MEMBER = uuid.uuid4()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0080_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0079")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                department = (
                    await connection.execute(
                        text(
                            "INSERT INTO departments (id, code, name, function, active)"
                            " VALUES (:id, 'ADS', 'Phòng Ads', 'NONE', true)"
                            " ON CONFLICT (code) DO UPDATE SET active = true RETURNING id"
                        ),
                        {"id": uuid.uuid4()},
                    )
                ).scalar_one()
                for user_id, name, position in (
                    (HEAD, "Trưởng phòng Ads", "HEAD"),
                    (MEMBER, "Marketing", "MEMBER"),
                ):
                    await connection.execute(
                        text(
                            "INSERT INTO users (id, full_name, role, status, active)"
                            " VALUES (:id, :name, 'employee', 'active', true)"
                        ),
                        {"id": user_id, "name": name},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO department_members (id, department_id, user_id, position)"
                            " VALUES (:id, :department, :user, :position)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "department": department,
                            "user": user_id,
                            "position": position,
                        },
                    )
                for key in ("ads.sync_spend", "ads.sync_charges", "notifications.drain_outbox"):
                    await connection.execute(
                        text(
                            "INSERT INTO cron_jobs (key, enabled, retries, retry_delay_seconds,"
                            " alert_after, keep_successes, created_at, updated_at)"
                            " VALUES (:key, true, 2, 1800, 1, true, now(), now())"
                        ),
                        {"key": key},
                    )
        finally:
            await engine.dispose()
        await upgrade_to(url, "0080")
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
            item for item in compare_metadata(context, Base.metadata) if "'cron_jobs'" in repr(item)
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_the_meta_pulls_alert_the_ads_heads(database: Database) -> None:
    async with database.engine.connect() as connection:
        rows = {
            key: (notify, recipients)
            for key, notify, recipients in await connection.execute(
                text("SELECT key, notify, notify_user_ids FROM cron_jobs")
            )
        }
    assert rows["ads.sync_spend"] == (True, [str(HEAD)])
    assert rows["ads.sync_charges"] == (True, [str(HEAD)])
    assert rows["notifications.drain_outbox"] == (False, [])


async def test_03_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0079")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            columns = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns"
                        " WHERE table_name = 'cron_jobs' AND column_name LIKE 'notify%'"
                    )
                )
            ).scalar_one()
        assert columns == 0
    finally:
        await engine.dispose()
    await upgrade_to(dsn, "0080")
