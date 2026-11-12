"""0104: Pancake notes + customer codes, a conversation's stage, a page's look.

Runs the real migrations on a scratch PostgreSQL database: the new columns and the
codes table match the models, existing conversations get their stage (callback ->
CALLBACK, a confirmed order -> CLOSED, answered -> CONSULTING, else NEW) and empty
notes, and a downgrade removes it all.

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
    scratch = f"meobot_0104_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0103")
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


async def _conversation(connection: object, psid: str, *, callback: bool = False) -> uuid.UUID:
    conv_id = uuid.uuid4()
    await connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO cskh_conversations (id, page_id, psid, customer_name, customer_key, "
            "last_text, last_out, unread, callback, pancake_tags, assignee_ids, phones, "
            "phone_digits, ads, qrs, qr_texts, created_at, updated_at) VALUES (:id, 'p1', "
            ":psid, '', '', '', false, 0, :cb, '[]', '[]', '[]', '', '[]', '[]', '', now(), now())"
        ),
        {"id": conv_id, "psid": psid, "cb": callback},
    )
    return conv_id


async def test_01_columns_stage_backfill_and_downgrade(dsn: str, database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO cskh_pages (id, name, username, token_hint, active, created_at, "
                "updated_at) VALUES ('p1', 'Page', '', '', true, now(), now())"
            )
        )
        waiting = await _conversation(connection, "w", callback=True)
        closed = await _conversation(connection, "c")
        answered = await _conversation(connection, "a")
        fresh = await _conversation(connection, "n")
        await connection.execute(
            text(
                "INSERT INTO cskh_orders (id, conversation_id, page_id, action, confirm_type, "
                "phone, phone_key, customer_name, address, content_name, order_status, "
                "platform, marketer_name, creator_name, telesale_name, customer_code, note, "
                "dup_info, created_at) VALUES (:id, :conv, 'p1', 'create', 'phone', '', '', "
                "'', '', '', '', 'Facebook', '', '', '', '', '', '', now())"
            ),
            {"id": uuid.uuid4(), "conv": closed},
        )
        await connection.execute(
            text(
                "INSERT INTO cskh_messages (id, conversation_id, mid, direction, kind, text, "
                "text_key, at, status, attachments, sender_name, created_at) VALUES "
                "(:id, :conv, 'm1', 'out', 'staff', 'Dạ', 'da', now(), 'ok', '[]', '', now())"
            ),
            {"id": uuid.uuid4(), "conv": answered},
        )
    await database.engine.dispose()
    await upgrade_to(dsn, "0104")

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [item for item in compare_metadata(context, Base.metadata) if "cskh_" in repr(item)]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
        stages = dict(
            (await connection.execute(text("SELECT id, stage FROM cskh_conversations"))).all()
        )
        assert stages == {
            waiting: "CALLBACK",
            closed: "CLOSED",
            answered: "CONSULTING",
            fresh: "NEW",
        }
        notes = await connection.scalar(
            text("SELECT notes FROM cskh_conversations WHERE id = :id"), {"id": fresh}
        )
        assert notes == []
        look = (await connection.execute(text("SELECT short_name, color FROM cskh_pages"))).one()
        assert tuple(look) == ("", "")
    await database.engine.dispose()
    await downgrade_to(dsn, "0103")
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(text("SELECT to_regclass('cskh_conversation_codes')")) is None
        )
        columns = set(
            (
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'cskh_conversations'"
                    )
                )
            ).all()
        )
        assert "stage" not in columns and "notes" not in columns
    await database.engine.dispose()
