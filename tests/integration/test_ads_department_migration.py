"""0078: Phòng Ads' tables.

Runs the real migration on a scratch PostgreSQL database: the four tables
match the models, a day is unique per account, and the migration goes down
and up again.

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
TABLES = ("'ads_connections'", "'ads_accounts'", "'ads_spend_daily'", "'ads_charges_daily'")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0078_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0078")
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
            # Added by 0079 on top of this revision.
            and "card_last4" not in repr(item)
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_one_row_per_account_and_day(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO ads_connections (id, name, business_id, site, token_encrypted,"
                " token_hint) VALUES (:id, 'BM', '123', 'SG', 'v1:x', '••••')"
            ),
            {"id": uuid.uuid4()},
        )
        bm = (await connection.execute(text("SELECT id FROM ads_connections"))).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO ads_accounts (account_id, connection_id, name, brand)"
                " VALUES ('111', :bm, 'TK 1', 'COREMIND')"
            ),
            {"bm": bm},
        )
        insert = text(
            "INSERT INTO ads_spend_daily (day, account_id, spend, messages, phones, synced_at)"
            " VALUES ('2026-10-20', '111', 100, 1, 1, now())"
        )
        await connection.execute(insert)
    with pytest.raises(IntegrityError):
        async with database.engine.begin() as connection:
            await connection.execute(insert)


async def test_03_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0077")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            gone = (
                await connection.execute(text("SELECT to_regclass('ads_accounts') IS NULL"))
            ).scalar_one()
        assert gone is True
    finally:
        await engine.dispose()
    await upgrade_to(dsn, "0078")
