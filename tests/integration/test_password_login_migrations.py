"""``0045`` against a real PostgreSQL: password columns, the session's method, the roundtrip.

* **the models describe the migration** - ``compare_metadata`` over ``users``
  and ``web_sessions`` is empty at head;
* **existing rows are untouched** - a user from before ``0045`` is on the
  default password (``password_hash IS NULL``) with no failures; an existing
  session is a Telegram-link session;
* **the database refuses plain text** - only a ``scrypt$`` value fits
  ``password_hash``; the failure count is never negative; the method is one of
  the two known ones;
* **the migration goes both ways** - down to ``0044`` drops exactly the five
  columns (and their checks) and keeps every row; up again restores them.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_password_login_migrations.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
USER_COLUMNS = ("password_hash", "password_changed_at", "failed_login_count", "locked_until")
CHECKS = (
    "ck_users_password_hash_is_scrypt",
    "ck_users_failed_login_count_not_negative",
    "ck_web_sessions_auth_method_known",
)

OLD_USER = uuid.uuid4()
OLD_SESSION = uuid.uuid4()


async def _scalar(dsn: str, sql: str, **params: object) -> object:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql), params)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0044`` with one user and one session, then taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0045_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0044")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, telegram_user_id, full_name, role, active, "
                        " status, created_at, updated_at) "
                        "VALUES (:id, 424242, 'Old', 'EMPLOYEE', true, 'active', now(), now())"
                    ),
                    {"id": OLD_USER},
                )
                await connection.execute(
                    text(
                        "INSERT INTO web_sessions (id, user_id, kind, token_hash, expires_at) "
                        "VALUES (:id, :user, 'SESSION', :hash, :expires)"
                    ),
                    {
                        "id": OLD_SESSION,
                        "user": OLD_USER,
                        "hash": "a" * 64,
                        "expires": datetime.now(UTC) + timedelta(hours=1),
                    },
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
    # ``users`` carries older server-default drift (``status``,
    # ``private_chat_available``) that predates this revision; what 0045 owns is
    # its five columns, its three checks and all of ``web_sessions``.
    owned = (*USER_COLUMNS, "auth_method", *CHECKS, "'web_sessions'")
    ours = [one for one in differences if any(name in str(one) for name in owned)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_existing_rows_are_on_the_default_and_the_telegram_link(
    database: Database,
) -> None:
    async with database.session() as session:
        user = (
            await session.execute(
                text(
                    "SELECT password_hash, password_changed_at, failed_login_count, locked_until "
                    "FROM users WHERE id = :id"
                ),
                {"id": OLD_USER},
            )
        ).one()
        method = await session.scalar(
            text("SELECT auth_method FROM web_sessions WHERE id = :id"), {"id": OLD_SESSION}
        )
    assert tuple(user) == (None, None, 0, None)
    assert method == "TELEGRAM_LINK"


async def test_04_the_database_refuses_plain_text_and_nonsense(database: Database) -> None:
    for sql, params, check in (
        (
            "UPDATE users SET password_hash = :value WHERE id = :id",
            {"value": "Apm@2026", "id": OLD_USER},
            "password_hash_is_scrypt",
        ),
        (
            "UPDATE users SET failed_login_count = -1 WHERE id = :id",
            {"id": OLD_USER},
            "failed_login_count_not_negative",
        ),
        (
            "UPDATE web_sessions SET auth_method = 'MAGIC' WHERE id = :id",
            {"id": OLD_SESSION},
            "auth_method_known",
        ),
    ):
        async with database.session() as session:
            with pytest.raises(IntegrityError) as caught:
                await session.execute(text(sql), params)
            assert check in str(caught.value)
            await session.rollback()
    # A real hash fits.
    async with database.session() as session:
        await session.execute(
            text("UPDATE users SET password_hash = :value WHERE id = :id"),
            {"value": hash_password("Mèo con 2026"), "id": OLD_USER},
        )
        await session.rollback()


async def test_05_the_migration_goes_both_ways(dsn: str) -> None:
    await downgrade_to(dsn, "0044")
    assert await _scalar(dsn, "SELECT version_num FROM alembic_version") == "0044"
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            columns = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE (table_name = 'users' AND column_name = ANY(:users)) "
                            "   OR (table_name = 'web_sessions' AND column_name = 'auth_method')"
                        ),
                        {"users": list(USER_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert columns == []
            checks = (
                (
                    await connection.execute(
                        text("SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"),
                        {"names": list(CHECKS)},
                    )
                )
                .scalars()
                .all()
            )
            assert checks == []
            # Every row survived.
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM users WHERE id = :id"), {"id": OLD_USER}
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM web_sessions WHERE id = :id"), {"id": OLD_SESSION}
                )
                == 1
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)
    assert await _scalar(dsn, "SELECT version_num FROM alembic_version") == alembic_head()
    assert (
        await _scalar(dsn, "SELECT auth_method FROM web_sessions WHERE id = :id", id=OLD_SESSION)
        == "TELEGRAM_LINK"
    )
    assert (
        await _scalar(dsn, "SELECT failed_login_count FROM users WHERE id = :id", id=OLD_USER) == 0
    )
