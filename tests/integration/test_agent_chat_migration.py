"""0091: Đội agent - the three tables, the 15 agents seeded by department code.

Runs the real migration on a scratch PostgreSQL database. A ``TCKT``
department is added before the upgrade: the CFO agent then lists Ban Giám đốc
and Tài Chính-Kế Toán, a code the database lacks (``SALE``) is skipped, Pháp
chế is open to everyone. Downgrading drops the tables.

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

TCKT = uuid.uuid4()
TABLES = ("agent_profiles", "agent_conversations", "agent_messages")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0091_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0090")
        found = create_async_engine(url)
        try:
            async with found.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO departments (id, code, name, function, active, sort_order)"
                        " VALUES (:id, 'TCKT', 'Tài Chính-Kế Toán', 'NONE', true, 20)"
                    ),
                    {"id": TCKT},
                )
            await upgrade_to(url, "0091")
            yield found, url
        finally:
            await found.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def test_01_fifteen_agents_seeded_by_department_code(
    database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = database
    async with engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT slug, everyone, allowed_department_ids, active FROM agent_profiles"
                    " ORDER BY sort_order"
                )
            )
        ).all()
        codes = {
            str(dept_id): code
            for dept_id, code in (await c.execute(text("SELECT id, code FROM departments"))).all()
        }
    assert len(rows) == 15 and all(active for *_, active in rows)
    by_slug = {slug: (everyone, [codes[d] for d in depts]) for slug, everyone, depts, _ in rows}
    assert by_slug["cfo"] == (False, ["BGD", "TCKT"])
    assert by_slug["phapche"] == (True, [])
    # No SALE department here: Telesale is the board's only.
    assert by_slug["telesale"] == (False, ["BGD"])
    assert by_slug["mktads"] == (False, ["BGD", "ADS"])


async def test_02_a_conversation_and_its_messages(database: tuple[AsyncEngine, str]) -> None:
    engine, _ = database
    user, conversation = uuid.uuid4(), uuid.uuid4()
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
                " VALUES (:id, :agent, :user, 'Hỏi', :session)"
            ),
            {"id": conversation, "agent": agent, "user": user, "session": f"apm-{conversation}"},
        )
        await c.execute(
            text(
                "INSERT INTO agent_messages (id, conversation_id, role, content, status,"
                " tools_used, created_at) VALUES (:id, :conv, 'USER', 'Xin chào', 'DONE',"
                " '[]', now())"
            ),
            {"id": uuid.uuid4(), "conv": conversation},
        )
    # Deleting the conversation takes its messages with it.
    async with engine.begin() as c:
        await c.execute(
            text("DELETE FROM agent_conversations WHERE id = :id"), {"id": conversation}
        )
        left = (
            await c.execute(
                text("SELECT count(*) FROM agent_messages WHERE conversation_id = :id"),
                {"id": conversation},
            )
        ).scalar_one()
    assert left == 0


async def test_03_models_and_migration_agree(database: tuple[AsyncEngine, str]) -> None:
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
            and "agent_attachments" not in repr(item)  # 0092's own test
        ]

    engine, _ = database
    async with engine.connect() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_04_downgrade_drops_the_tables(database: tuple[AsyncEngine, str]) -> None:
    engine, url = database
    await downgrade_to(url, "0090")
    async with engine.connect() as c:
        tables = (
            await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_name LIKE 'agent_%'"
                )
            )
        ).all()
    assert tables == []
    await upgrade_to(url, "0091")
