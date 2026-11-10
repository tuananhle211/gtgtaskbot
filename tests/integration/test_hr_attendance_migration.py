"""0100: đơn từ, phép năm, ca, chấm công.

Runs the real migrations on a scratch PostgreSQL database: the tables match
the models, the leave types and the default shift are seeded, the HCNS
department gets ``HR`` / ``TIMESHEET``, and a downgrade removes it all.

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
    scratch = f"meobot_0100_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0099")
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


TABLES = (
    "hr_leave_types",
    "hr_absence_requests",
    "hr_absence_steps",
    "hr_shifts",
    "hr_attendance_days",
    "hr_leave_adjustments",
)


async def test_01_tables_seeds_and_grants(dsn: str, database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO departments (id, code, name, function, active, sort_order, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'HCNS', 'Phòng HCNS', "
                "'NONE', true, 0, now(), now())"
            )
        )
    await database.engine.dispose()
    await upgrade_to(dsn, "0100")
    async with database.engine.begin() as connection:
        codes = (
            await connection.scalars(text("SELECT code FROM hr_leave_types ORDER BY sort_order"))
        ).all()
        shifts = (await connection.execute(text("SELECT name, is_default FROM hr_shifts"))).all()
        grants = (
            await connection.scalars(
                text(
                    "SELECT position FROM department_permissions "
                    "WHERE module = 'HR' AND permission = 'TIMESHEET' ORDER BY position"
                )
            )
        ).all()
    assert codes[0] == "ANNUAL" and "UNPAID" in codes and len(codes) == 7
    assert [tuple(row) for row in shifts] == [("Hành chính", True)]
    assert list(grants) == ["HEAD", "MEMBER"]

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

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
        assert await connection.run_sync(_diff) == []
    await database.engine.dispose()
    await downgrade_to(dsn, "0099")
    async with database.engine.begin() as connection:
        left = await connection.scalar(
            text("SELECT count(*) FROM department_permissions WHERE permission = 'TIMESHEET'")
        )
        exists = await connection.scalar(text("SELECT to_regclass('hr_shifts')"))
    assert left == 0 and exists is None
    await database.engine.dispose()
    await upgrade_to(dsn, "0100")
