"""0102: Hộp chat CSKH replaces the 0094 agent replies.

Runs the real migrations on a scratch PostgreSQL database: the 17 ``cskh_*``
tables match the models and the 0094 tables are gone, an old ``CSKH REPLY``
grant becomes ``INBOX``, Tư vấn online / Telesales get their cells, and a
downgrade brings the 0094 tables back (``INBOX`` -> ``REPLY``, ``ZALO`` gone).

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


def added_later(item: object) -> bool:
    """What 0104 adds (notes, codes, stage, a page's look) is checked by its own test."""
    text_ = repr(item)
    return "cskh_conversation_codes" in text_ or any(
        f"'{column}'" in text_ and table in text_
        for table, column in (
            ("cskh_conversations", "notes"),
            ("cskh_conversations", "stage"),
            ("cskh_pages", "short_name"),
            ("cskh_pages", "color"),
        )
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0102_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0101")
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


async def test_01_tables_grants_and_downgrade(dsn: str, database: Database) -> None:
    async with database.engine.begin() as connection:
        for code in ("TVOL", "TELE", "QHKH"):
            await connection.execute(
                text(
                    "INSERT INTO departments (id, code, name, function, active, created_at, "
                    "updated_at) VALUES (:id, :code, :code, 'NONE', true, now(), now()) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                {"id": uuid.uuid4(), "code": code},
            )
        qhkh = await connection.scalar(text("SELECT id FROM departments WHERE code = 'QHKH'"))
        await connection.execute(
            text(
                "INSERT INTO department_permissions (id, department_id, position, module, "
                "permission, scope, created_at, updated_at) VALUES "
                "(:id, :d, 'MEMBER', 'CSKH', 'REPLY', 'ALL', now(), now())"
            ),
            {"id": uuid.uuid4(), "d": qhkh},
        )
    await database.engine.dispose()
    await upgrade_to(dsn, "0102")

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
            if ("cskh_" in repr(item) or "pancake_" in repr(item)) and not added_later(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
        cells = {
            (code, position, permission, scope)
            for code, position, permission, scope in (
                await connection.execute(
                    text(
                        "SELECT d.code, p.position, p.permission, p.scope "
                        "FROM department_permissions p JOIN departments d "
                        "ON d.id = p.department_id WHERE p.module = 'CSKH'"
                    )
                )
            ).all()
        }
    assert ("QHKH", "MEMBER", "INBOX", "ALL") in cells
    assert ("TVOL", "HEAD", "INBOX", "ALL") in cells and ("TVOL", "MEMBER", "INBOX", "OWN") in cells
    assert ("TELE", "MEMBER", "ZALO", "OWN") in cells
    assert not any(permission == "REPLY" for _, _, permission, _ in cells)
    await database.engine.dispose()

    await downgrade_to(dsn, "0101")
    async with database.engine.begin() as connection:
        assert await connection.scalar(text("SELECT to_regclass('cskh_conversations')")) is None
        assert await connection.scalar(text("SELECT to_regclass('pancake_replies')")) is not None
        permissions = set(
            (
                await connection.scalars(
                    text("SELECT permission FROM department_permissions WHERE module = 'CSKH'")
                )
            ).all()
        )
    assert permissions == {"REPLY"}
    await database.engine.dispose()
    await upgrade_to(dsn, "0102")
