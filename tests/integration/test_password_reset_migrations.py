"""``0046`` against a real PostgreSQL: the temporary-password columns, the roundtrip.

* **the models describe the migration** - ``compare_metadata`` finds nothing
  about the two new ``users`` columns at head;
* **existing rows are untouched** - a user from before ``0046`` (even one with
  a chosen password) is not on a temporary password and was never reset;
* **the migration goes both ways** - down to ``0045`` drops exactly the two
  columns and keeps every row and its password; up again restores them.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_password_reset_migrations.py -m integration
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
from meobot.application.account.passwords import hash_password
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
USER_COLUMNS = ("password_temporary", "password_reset_at")

OLD_USER = uuid.uuid4()
OLD_HASH = hash_password("Mèo con 2026")


async def _scalar(dsn: str, sql: str, **params: object) -> object:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql), params)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0045`` with one user who chose a password, then head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0046_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0045")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, telegram_user_id, full_name, role, active, "
                        " status, password_hash, password_changed_at, created_at, updated_at) "
                        "VALUES (:id, 434343, 'Old', 'EMPLOYEE', true, 'active', :hash, now(), "
                        " now(), now())"
                    ),
                    {"id": OLD_USER, "hash": OLD_HASH},
                )
        finally:
            await engine.dispose()
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    db = Database(Settings(**SETTINGS_KWARGS, database_url=dsn))  # type: ignore[arg-type]
    try:
        yield db
    finally:
        await db.dispose()


async def test_01_models_and_migration_agree(database: Database) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)
    # ``users`` carries older server-default drift that predates this revision;
    # what 0046 owns is its two columns.
    ours = [one for one in differences if any(name in str(one) for name in USER_COLUMNS)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_existing_rows_are_not_temporary(database: Database) -> None:
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT password_temporary, password_reset_at, password_hash "
                    "FROM users WHERE id = :id"
                ),
                {"id": OLD_USER},
            )
        ).one()
    assert tuple(row) == (False, None, OLD_HASH)


async def test_04_the_flag_is_never_null(database: Database) -> None:
    async with database.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE users SET password_temporary = NULL WHERE id = :id"), {"id": OLD_USER}
            )
        await session.rollback()


async def test_05_the_migration_goes_both_ways(dsn: str) -> None:
    await downgrade_to(dsn, "0045")
    assert await _scalar(dsn, "SELECT version_num FROM alembic_version") == "0045"
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            columns = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'users' AND column_name = ANY(:users)"
                        ),
                        {"users": list(USER_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert columns == []
            # The row and its password survived.
            assert (
                await connection.scalar(
                    text("SELECT password_hash FROM users WHERE id = :id"), {"id": OLD_USER}
                )
                == OLD_HASH
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)
    assert await _scalar(dsn, "SELECT version_num FROM alembic_version") == alembic_head()
    assert (
        await _scalar(dsn, "SELECT password_temporary FROM users WHERE id = :id", id=OLD_USER)
        is False
    )
