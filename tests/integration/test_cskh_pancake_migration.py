"""0094: Chăm sóc khách hàng - ``pancake_pages`` + conversations + replies.

Runs the real migration on a scratch PostgreSQL database: the tables match the
models, a customer message is kept once per page, deleting a page takes its
conversations and replies, downgrading drops the three tables.

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
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.db.base import Base
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, downgrade_to, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

TABLES = ("pancake_pages", "pancake_conversations", "pancake_replies")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0094_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0094")
        found = create_async_engine(url)
        try:
            yield found, url
        finally:
            await found.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def test_01_models_and_migration_agree(database: tuple[AsyncEngine, str]) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _diff(connection: object) -> list[object]:
        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item for item in compare_metadata(context, Base.metadata) if "pancake" in repr(item)
        ]

    engine, _ = database
    async with engine.connect() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_one_row_per_message_and_a_page_takes_everything(
    database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = database
    page, conv = uuid.uuid4(), uuid.uuid4()
    reply = (
        "INSERT INTO pancake_replies (id, page_row_id, conversation_row_id,"
        " customer_message_id, status, violations) VALUES (:id, :p, :c, 'm1', 'WAITING', '[]')"
    )
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO pancake_pages (id, page_id, name, mode, agent_slug,"
                " access_token_hint, webhook_key, handoff_tag_ids)"
                " VALUES (:id, '123', 'Apexmed', 'SUGGEST', 'chatpage', '', 'k1', '[]')"
            ),
            {"id": page},
        )
        await c.execute(
            text(
                "INSERT INTO pancake_conversations (id, page_row_id, conversation_id, tag_ids,"
                " assignee_ids, sent_message_ids) VALUES (:id, :p, 'psid', '[]', '[]', '[]')"
            ),
            {"id": conv, "p": page},
        )
        await c.execute(text(reply), {"id": uuid.uuid4(), "p": page, "c": conv})
    with pytest.raises(IntegrityError):
        async with engine.begin() as c:
            await c.execute(text(reply), {"id": uuid.uuid4(), "p": page, "c": conv})
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM pancake_pages WHERE id = :id"), {"id": page})
        for table in TABLES[1:]:
            left = (await c.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608 - fixed names
            assert left == 0, table


async def test_03_downgrade_drops_the_tables(database: tuple[AsyncEngine, str]) -> None:
    engine, url = database
    await downgrade_to(url, "0093")
    async with engine.connect() as c:
        for table in TABLES:
            found = (await c.execute(text("SELECT to_regclass(:t)"), {"t": table})).scalar_one()
            assert found is None, table
