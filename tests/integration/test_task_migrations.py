"""``0043`` against a real PostgreSQL: the backfill, the model, the roundtrip.

* **every existing source gets one task** - PR content in the PR unit, an
  order in its own unit - with the board's phase for its stage, the priority
  flag, and ``finished_at`` only for the finished phases;
* **the model describes the migration** - ``compare_metadata`` over
  ``tasks`` is empty at head;
* **a task follows its source** - the flush hook writes it, and a plain
  ``DELETE`` of the source takes it (``ON DELETE CASCADE``);
* **the migration goes both ways** - ``0043 -> 0042 -> 0043`` drops the table
  and backfills it again.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_task_migrations.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.session import Database
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    alembic_head,
    downgrade_to,
    upgrade_to,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}

OWNER = uuid.uuid4()
BRAND = uuid.uuid4()
IDEA = uuid.uuid4()
IN_PRODUCTION = uuid.uuid4()
PUBLISHED = uuid.uuid4()
PENDING = uuid.uuid4()
COMPLETED = uuid.uuid4()
CANCELLED = uuid.uuid4()
WHEN = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


async def _seed(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, full_name, role, active, status, created_at, "
                    "updated_at) VALUES (:id, 'Chủ', 'EMPLOYEE', true, 'active', now(), now())"
                ),
                {"id": OWNER},
            )
            await connection.execute(
                text(
                    "INSERT INTO pr_brands (id, code, name, created_at, updated_at) "
                    "VALUES (:id, 'BRND-T', 'Apexmed', now(), now())"
                ),
                {"id": BRAND},
            )
            content = text(
                "INSERT INTO pr_content_items (id, code, title, brand_id, priority, content_type,"
                " workflow_stage, owner_user_id, created_by_user_id, created_at, updated_at) "
                "VALUES (:id, :code, 'Bài', :brand, :priority, :type, :stage, :owner, :owner,"
                " :at, :at)"
            )
            for content_id, code, priority, content_type, stage in (
                (IDEA, "CNT-2026-900001", "NORMAL", None, "IDEA"),
                (IN_PRODUCTION, "CNT-2026-900002", "URGENT", "SHORT_VIDEO_SCRIPT", "PRODUCTION"),
                (PUBLISHED, "CNT-2026-900003", "NORMAL", "FACEBOOK_POST", "PUBLISHED"),
            ):
                await connection.execute(
                    content,
                    {
                        "id": content_id,
                        "code": code,
                        "brand": BRAND,
                        "priority": priority,
                        "type": content_type,
                        "stage": stage,
                        "owner": OWNER,
                        "at": WHEN,
                    },
                )
            ads = await connection.scalar(text("SELECT id FROM org_units WHERE code = 'ADS'"))
            order = text(
                "INSERT INTO orders (id, unit_id, code, title, video_type, order_content,"
                " owner_user_id, stage, submitted_at, is_priority, completed_at, created_at,"
                " updated_at) VALUES (:id, :unit, :code, 'Video', 'BTD', 'brief', :owner,"
                " :stage, :at, :priority, :completed, :at, :at)"
            )
            for order_id, code, stage, priority, completed in (
                (PENDING, "TEST-BTD-261001-01", "ORDER_PENDING", True, None),
                (COMPLETED, "TEST-BTD-261001-02", "COMPLETED", False, WHEN),
                (CANCELLED, "TEST-BTD-261001-03", "CANCELLED", False, None),
            ):
                await connection.execute(
                    order,
                    {
                        "id": order_id,
                        "unit": ads,
                        "code": code,
                        "owner": OWNER,
                        "stage": stage,
                        "at": WHEN,
                        "priority": priority,
                        "completed": completed,
                    },
                )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0042`` with content and orders, then taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0043_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0042")
        await _seed(url)
        await upgrade_to(url)
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


async def _tasks(database: Database) -> dict[str, tuple]:  # type: ignore[type-arg]
    async with database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT t.code, t.source_type, u.code AS unit, t.kind, t.is_priority, "
                    "t.stage, t.phase, t.finished_at IS NOT NULL AS finished, t.stage_since, "
                    "t.owner_user_id FROM tasks t JOIN org_units u ON u.id = t.unit_id"
                )
            )
        ).all()
    return {row.code: tuple(row)[1:] for row in rows}


async def test_01_models_and_migration_agree(database: Database) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)
    ours = [one for one in differences if "tasks" in str(one)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_every_source_was_backfilled(database: Database) -> None:
    rows = await _tasks(database)
    assert {code: row[:7] for code, row in rows.items()} == {
        "CNT-2026-900001": ("PR_CONTENT", "PR", "", False, "IDEA", "ORDER", False),
        "CNT-2026-900002": (
            "PR_CONTENT",
            "PR",
            "SHORT_VIDEO_SCRIPT",
            True,
            "PRODUCTION",
            "PRODUCTION",
            False,
        ),
        "CNT-2026-900003": ("PR_CONTENT", "PR", "FACEBOOK_POST", False, "PUBLISHED", "DONE", True),
        "TEST-BTD-261001-01": ("ORDER", "ADS", "BTD", True, "ORDER_PENDING", "REVIEW", False),
        "TEST-BTD-261001-02": ("ORDER", "ADS", "BTD", False, "COMPLETED", "DONE", True),
        "TEST-BTD-261001-03": ("ORDER", "ADS", "BTD", False, "CANCELLED", "CANCELLED", True),
    }
    assert all(row[7] == WHEN and row[8] == OWNER for row in rows.values())


async def test_04_a_task_follows_its_source(database: Database) -> None:
    from meobot.db.models.pr import PrContentItem
    from meobot.domain.pr.models import PrWorkflowStage

    async with database.transaction() as session:
        item = await session.get(PrContentItem, IDEA)
        assert item is not None
        item.workflow_stage = PrWorkflowStage.BRIEFING
    rows = await _tasks(database)
    assert rows["CNT-2026-900001"][4:6] == ("BRIEFING", "ORDER")
    assert rows["CNT-2026-900001"][7] > WHEN

    async with database.transaction() as session:
        await session.execute(text("DELETE FROM pr_content_items WHERE id = :id"), {"id": IDEA})
    assert "CNT-2026-900001" not in await _tasks(database)


async def test_05_the_migration_goes_both_ways(dsn: str) -> None:
    await downgrade_to(dsn, "0042")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0042"
            )
            present = await connection.scalar(text("SELECT to_regclass('public.tasks')"))
            assert present is None
    finally:
        await engine.dispose()
    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                alembic_head()
            )
            # Five sources remain after test 04 deleted one; each has its task again.
            assert await connection.scalar(text("SELECT count(*) FROM tasks")) == 5
    finally:
        await engine.dispose()
