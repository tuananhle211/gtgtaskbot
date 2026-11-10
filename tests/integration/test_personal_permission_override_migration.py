"""0098: a personal permission row may say NONE (it overrides the seats).

Runs the real migration on a scratch PostgreSQL database: ``NONE`` is
accepted, a downgrade revokes the ``NONE`` rows, and it goes up again.

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
    scratch = f"meobot_0098_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0098")
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


INSERT = (
    "INSERT INTO user_permissions (id, user_id, module, permission, scope, created_at) "
    "VALUES (gen_random_uuid(), :user, 'ORDER', 'ORDER_APPROVE', :scope, now())"
)


async def _user(connection: object) -> object:
    return await connection.scalar(  # type: ignore[attr-defined]
        text(
            "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'X', 'EMPLOYEE', true, now(), now()) RETURNING id"
        )
    )


async def test_01_none_is_accepted_and_down_revokes_it(dsn: str, database: Database) -> None:
    async with database.engine.begin() as connection:
        user = await _user(connection)
        await connection.execute(text(INSERT), {"user": user, "scope": "NONE"})
    await database.engine.dispose()
    await downgrade_to(dsn, "0097")
    async with database.engine.begin() as connection:
        revoked = await connection.scalar(
            text(
                "SELECT count(*) FROM user_permissions "
                "WHERE user_id = :user AND revoked_at IS NOT NULL"
            ),
            {"user": user},
        )
        assert revoked == 1
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(text(INSERT), {"user": user, "scope": "NONE"})
    await database.engine.dispose()
    await upgrade_to(dsn, "0098")
    async with database.engine.begin() as connection:
        await connection.execute(text(INSERT), {"user": user, "scope": "NONE"})
