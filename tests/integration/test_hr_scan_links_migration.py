"""0099: the scan folder becomes a list of Drive links.

Runs the real migrations on a scratch PostgreSQL database: a 0097 cell
holding words and two links becomes two ``hr_scan_links`` rows and a note,
the table matches the model, and a downgrade puts the links back.

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
    scratch = f"meobot_0099_{uuid.uuid4().hex[:8]}"
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


CELL = (
    "CV https://drive.google.com/a\nhttps://drive.google.com/file/d/1Ab\n"
    "Cd_28/view?usp=drive_link \nđã nhận bản gốc"
)
B = "https://drive.google.com/file/d/1AbCd_28/view?usp=drive_link"


async def test_01_a_cell_is_split_into_links(dsn: str, database: Database) -> None:
    async with database.engine.begin() as connection:
        user = await connection.scalar(
            text(
                "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'X', 'EMPLOYEE', true, now(), now()) RETURNING id"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO hr_profiles (user_id, scan_link, created_at, updated_at) "
                "VALUES (:user, :cell, now(), now())"
            ),
            {"user": user, "cell": CELL},
        )
    await database.engine.dispose()
    await upgrade_to(dsn, "0099")
    async with database.engine.begin() as connection:
        urls = [
            tuple(row)
            for row in (
                await connection.execute(
                    text(
                        "SELECT label, url FROM hr_scan_links "
                        "WHERE user_id = :user ORDER BY sort_order"
                    ),
                    {"user": user},
                )
            ).all()
        ]
        note = await connection.scalar(
            text("SELECT scan_note FROM hr_profiles WHERE user_id = :user"), {"user": user}
        )
    assert urls == [("CV", "https://drive.google.com/a"), (None, B)]
    assert note == "đã nhận bản gốc"

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
            if "hr_scan_links" in repr(item) or "scan_note" in repr(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
    await database.engine.dispose()
    await downgrade_to(dsn, "0098")
    async with database.engine.begin() as connection:
        back = await connection.scalar(
            text("SELECT scan_link FROM hr_profiles WHERE user_id = :user"), {"user": user}
        )
    assert back == f"đã nhận bản gốc\nCV https://drive.google.com/a\n{B}"
    await database.engine.dispose()
    await upgrade_to(dsn, "0099")
