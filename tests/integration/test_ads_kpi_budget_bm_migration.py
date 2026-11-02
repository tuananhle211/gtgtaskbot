"""0087: KPI doanh thu và ngân sách tuần theo BM.

Runs the real migration on a scratch PostgreSQL database: the rows already
there become BM Hà Nội's, the tables match the models, one cell per BM, and
the migration goes down (keeping BM Hà Nội's rows) and up again.

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
USER = uuid.uuid4()
KPI = (
    "INSERT INTO ads_kpis (id, month, subject_key, amount{extra})"
    " VALUES (:id, '2026-10-01', 'team', 100{value})"
)
BUDGET = (
    "INSERT INTO ads_budgets (id, month, user_id, week, amount{extra})"
    " VALUES (:id, '2026-10-01', :user, 1, 50{value})"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0087_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        # Rows from before: a team KPI and a budget cell.
        await upgrade_to(url, "0086")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, full_name, role, active)"
                        " VALUES (:id, 'MKT', 'EMPLOYEE', true)"
                    ),
                    {"id": USER},
                )
                await connection.execute(text(KPI.format(extra="", value="")), {"id": uuid.uuid4()})
                await connection.execute(
                    text(BUDGET.format(extra="", value="")), {"id": uuid.uuid4(), "user": USER}
                )
        finally:
            await engine.dispose()
        await upgrade_to(url, "0087")
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
            if "'ads_kpis'" in repr(item) or "'ads_budgets'" in repr(item)
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_old_rows_are_bm_ha_nois_and_each_bm_has_its_cell(database: Database) -> None:
    async with database.engine.begin() as connection:
        regions = (
            await connection.execute(
                text("SELECT region FROM ads_kpis UNION ALL SELECT region FROM ads_budgets")
            )
        ).scalars()
        assert list(regions) == ["HN", "HN"]
        # BM Sài Gòn's cell next to BM Hà Nội's.
        await connection.execute(
            text(KPI.format(extra=", region", value=", 'HCM'")), {"id": uuid.uuid4()}
        )
        await connection.execute(
            text(BUDGET.format(extra=", region", value=", 'HCM'")),
            {"id": uuid.uuid4(), "user": USER},
        )
    with pytest.raises(IntegrityError):
        async with database.engine.begin() as connection:
            await connection.execute(
                text(KPI.format(extra=", region", value=", 'HN'")), {"id": uuid.uuid4()}
            )
    with pytest.raises(IntegrityError):
        # No default: every writer names the BM.
        async with database.engine.begin() as connection:
            await connection.execute(
                text(BUDGET.format(extra="", value="").replace("1, 50", "2, 50")),
                {"id": uuid.uuid4(), "user": USER},
            )


async def test_03_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0086")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            # BM Hà Nội's rows are kept, BM Sài Gòn's dropped.
            assert (
                await connection.execute(text("SELECT count(*) FROM ads_kpis"))
            ).scalar_one() == 1
            assert (
                await connection.execute(text("SELECT count(*) FROM ads_budgets"))
            ).scalar_one() == 1
    finally:
        await engine.dispose()
    await upgrade_to(dsn, "0087")
