"""0093: Phòng họp agent - ``agent_rooms`` + its agents, people, messages.

Runs the real migration on a scratch PostgreSQL database: the tables match the
models, a message has exactly one speaker, deleting a room takes everything in
it, downgrading drops the four tables.

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

TABLES = ("agent_rooms", "agent_room_agents", "agent_room_members", "agent_room_messages")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0093_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0093")
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
            item for item in compare_metadata(context, Base.metadata) if "agent_room" in repr(item)
        ]

    engine, _ = database
    async with engine.connect() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_one_speaker_and_a_room_takes_everything_with_it(
    database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = database
    user, room = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO users (id, full_name, role, status, active)"
                " VALUES (:id, 'Hảo', 'OWNER', 'active', true)"
            ),
            {"id": user},
        )
        agent = (
            await c.execute(text("SELECT id FROM agent_profiles WHERE slug = 'ceo'"))
        ).scalar_one()
        await c.execute(
            text(
                "INSERT INTO agent_rooms (id, owner_user_id, title, chair_agent_id, rounds)"
                " VALUES (:id, :u, 'Họp', :a, 0)"
            ),
            {"id": room, "u": user, "a": agent},
        )
        await c.execute(
            text(
                "INSERT INTO agent_room_agents (id, room_id, agent_id, position, added_at)"
                " VALUES (:id, :r, :a, 0, now())"
            ),
            {"id": uuid.uuid4(), "r": room, "a": agent},
        )
        await c.execute(
            text(
                "INSERT INTO agent_room_messages (id, room_id, speaker, agent_id, speaker_label,"
                " content, status, tools_used, created_at)"
                " VALUES (:id, :r, 'AGENT', :a, 'CEO', 'Chào', 'DONE', '[]', now())"
            ),
            {"id": uuid.uuid4(), "r": room, "a": agent},
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent_room_messages (id, room_id, speaker, user_id, agent_id,"
                    " speaker_label, content, status, tools_used, created_at)"
                    " VALUES (:id, :r, 'AGENT', :u, :a, 'Hai người', 'x', 'DONE', '[]', now())"
                ),
                {"id": uuid.uuid4(), "r": room, "u": user, "a": agent},
            )
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM agent_rooms WHERE id = :id"), {"id": room})
        for table in TABLES[1:]:
            left = (await c.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608 - fixed names
            assert left == 0, table


async def test_03_downgrade_drops_the_tables(database: tuple[AsyncEngine, str]) -> None:
    engine, url = database
    await downgrade_to(url, "0092")
    async with engine.connect() as c:
        tables = (
            await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_name LIKE 'agent_room%'"
                )
            )
        ).all()
    assert tables == []
    await upgrade_to(url, "0093")
