"""0084: Hotline theo loại và nhân viên đã nghỉ trên CRM.

Runs the real migration on a scratch PostgreSQL database: the accounts table
matches the model, an account is one of person / source / former (or none),
and the migration goes down and up again.

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
TABLES = ("'ads_crm_accounts'", "'ads_crm_payments'")
USER = uuid.uuid4()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0084_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0084")
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
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_one_owner_at_most(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, full_name, role, active)"
                " VALUES (:id, 'MKT', 'EMPLOYEE', true)"
            ),
            {"id": USER},
        )
        await connection.execute(
            text(
                "INSERT INTO ads_crm_accounts (crm_id, name, active, in_directory, region,"
                " former) VALUES (10, 'Đã nghỉ - HN', false, true, 'HN', true)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO ads_crm_accounts (crm_id, name, active, in_directory, source,"
                " hotline_kind) VALUES (11, 'Hotline FB', true, true, 'HOTLINE', 'FB')"
            )
        )
        former = (
            await connection.execute(text("SELECT former FROM ads_crm_accounts WHERE crm_id = 11"))
        ).scalar_one()
        assert former is False
    with pytest.raises(IntegrityError):
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO ads_crm_accounts (crm_id, name, active, in_directory,"
                    " user_id, former) VALUES (12, 'x', true, true, :user, true)"
                ),
                {"user": USER},
            )


async def test_03_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0083")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            gone = (
                await connection.execute(
                    text(
                        "SELECT count(*) = 0 FROM information_schema.columns"
                        " WHERE table_name = 'ads_crm_accounts' AND column_name = 'former'"
                    )
                )
            ).scalar_one()
            assert gone is True
    finally:
        await engine.dispose()
    await upgrade_to(dsn, "0084")
