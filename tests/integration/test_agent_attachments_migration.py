"""0092: Đội agent - ``agent_attachments`` (files sent with a question).

Runs the real migration on a scratch PostgreSQL database: the table matches
the model, deleting a conversation deletes its files, downgrading drops the
table and keeps the messages.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0092_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0092")
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
            item
            for item in compare_metadata(context, Base.metadata)
            if "agent_attachments" in repr(item)
        ]

    engine, _ = database
    async with engine.connect() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_a_conversation_takes_its_files_with_it(
    database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = database
    user, conversation, message = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO users (id, full_name, role, status, active)"
                " VALUES (:id, 'Hảo', 'EMPLOYEE', 'active', true)"
            ),
            {"id": user},
        )
        agent = (
            await c.execute(text("SELECT id FROM agent_profiles WHERE slug = 'phapche'"))
        ).scalar_one()
        await c.execute(
            text(
                "INSERT INTO agent_conversations (id, agent_id, user_id, title, hermes_session_id)"
                " VALUES (:id, :agent, :user, 'Hỏi', :sid)"
            ),
            {"id": conversation, "agent": agent, "user": user, "sid": f"apm-{conversation}"},
        )
        await c.execute(
            text(
                "INSERT INTO agent_messages (id, conversation_id, role, content, status,"
                " tools_used, created_at) VALUES (:id, :c, 'USER', 'Hỏi', 'DONE', '[]', now())"
            ),
            {"id": message, "c": conversation},
        )
        for message_id in (message, None):
            await c.execute(
                text(
                    "INSERT INTO agent_attachments (id, conversation_id, message_id, user_id,"
                    " name, content_type, kind, size_bytes, data, created_at)"
                    " VALUES (:id, :c, :m, :u, 'a.png', 'image/png', 'IMAGE', 3, :data, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "c": conversation,
                    "m": message_id,
                    "u": user,
                    "data": b"abc",
                },
            )
        truncated = (
            await c.execute(text("SELECT bool_and(NOT truncated) FROM agent_attachments"))
        ).scalar_one()
        assert truncated is True  # server default false
        await c.execute(
            text("DELETE FROM agent_conversations WHERE id = :id"), {"id": conversation}
        )
        left = (await c.execute(text("SELECT count(*) FROM agent_attachments"))).scalar_one()
    assert left == 0


async def test_03_downgrade_drops_the_table(database: tuple[AsyncEngine, str]) -> None:
    engine, url = database
    await downgrade_to(url, "0091")
    async with engine.connect() as c:
        tables = (
            await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_name = 'agent_attachments'"
                )
            )
        ).all()
    assert tables == []
    await upgrade_to(url, "0092")
