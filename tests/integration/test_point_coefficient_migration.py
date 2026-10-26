"""0076: members' points coefficients by month.

Runs the real migration on a scratch PostgreSQL database: the table matches
the model, refuses a coefficient outside 0-10, and goes down and up again.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
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
    scratch = f"meobot_0076_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0076")
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
            if "'user_point_coefficients'" in repr(item)
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_a_coefficient_stays_within_0_and_10(database: Database) -> None:
    user_id = uuid.uuid4()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, full_name, role, status, active) "
                "VALUES (:id, 'Hệ số', 'EMPLOYEE', 'active', true)"
            ),
            {"id": user_id},
        )
    insert = text(
        "INSERT INTO user_point_coefficients (id, user_id, effective_month, coefficient) "
        "VALUES (:id, :user, DATE '2026-10-01', :value)"
    )
    async with database.engine.begin() as connection:
        await connection.execute(insert, {"id": uuid.uuid4(), "user": user_id, "value": 1.25})
    for value in (0, 10.5):
        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    insert, {"id": uuid.uuid4(), "user": user_id, "value": value}
                )


async def test_03_it_goes_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0075")
    await upgrade_to(dsn, "0076")
