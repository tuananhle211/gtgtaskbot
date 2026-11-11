"""0101: đơn giải trình chấm công + ảnh xác minh.

Runs the real migrations on a scratch PostgreSQL database: the new columns
and table match the models, and a downgrade removes them.

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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0101_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0100")
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


async def test_01_columns_and_table(dsn: str, database: Database) -> None:
    await upgrade_to(dsn, "0101")

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item for item in compare_metadata(context, Base.metadata) if "hr_absence" in repr(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
    await database.engine.dispose()
    await downgrade_to(dsn, "0100")
    async with database.engine.begin() as connection:
        table = await connection.scalar(text("SELECT to_regclass('hr_absence_files')"))
        column = await connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'hr_absence_requests' AND column_name = 'check_in'"
            )
        )
    assert table is None and column == 0
    await database.engine.dispose()
    await upgrade_to(dsn, "0101")
