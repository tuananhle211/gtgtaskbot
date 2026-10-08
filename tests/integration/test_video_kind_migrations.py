"""``0044`` against a real PostgreSQL: the catalogue, the seed, the process codes, the roundtrip.

* **the models describe the migration** - ``compare_metadata`` over
  ``unit_video_kinds`` and ``orders`` is empty at head;
* **the Ads catalogue is seeded** - eleven kinds, in the department's order,
  one point each, with the same ids on every database;
* **an order placed before 0044 survives** - no kind, nothing rewritten;
* **the design-link rule covers every process editing without a design** -
  ``D`` and ``BD``; one name per unit, whatever the case;
* **the migration goes both ways** - ``0044 -> 0043`` refuses while an order
  uses a new process code and changes nothing; once none does it restores
  ``0043`` exactly, and the upgrade seeds again.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_video_kind_migrations.py -m integration
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

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

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "0044_ads_process_and_video_kinds.py"
SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
TABLES = ("unit_video_kinds", "orders")
NEW_COLUMNS = ("video_kind_id", "video_kind_name", "video_kind_points")

OWNER = uuid.uuid4()
OLD_ORDER = uuid.uuid4()

EXPECTED_KINDS = [
    "Video full diễn hoạt",
    "Video live full diễn hoạt",
    "Video live 50% diễn hoạt",
    "Sửa khác (source, nhạc, khung,...)",
    "Short video",
    "Quay nửa ngày",
    "Quay cả ngày",
    "Quay khác",
    "Kịch bản quay mới",
    "Kịch bản khác",
    "Tổ chức sản xuất",
]


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0044", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSERT_ORDER = text(
    "INSERT INTO orders (id, unit_id, code, title, video_type, order_content, design_link, "
    " owner_user_id, stage, submitted_at, created_at, updated_at) "
    "VALUES (:id, (SELECT id FROM org_units WHERE code = 'ADS'), :code, 'Kịch bản', :type, "
    " 'brief', :design, :owner, 'ORDER_PENDING', :at, now(), now())"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0043`` with one user and one order, then taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0044_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0043")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, full_name, role, active, status, "
                        " created_at, updated_at) "
                        "VALUES (:id, 'Orderer', 'EMPLOYEE', true, 'active', now(), now())"
                    ),
                    {"id": OWNER},
                )
                await connection.execute(
                    INSERT_ORDER,
                    {
                        "id": OLD_ORDER,
                        "code": "OLD-D-261007-01",
                        "type": "D",
                        "design": "https://example.com/design",
                        "owner": OWNER,
                        "at": datetime.now(UTC),
                    },
                )
        finally:
            await engine.dispose()
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
    ours = [one for one in differences if any(name in str(one) for name in TABLES)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_the_ads_catalogue_is_seeded_in_order(database: Database) -> None:
    migration = _migration()
    async with database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT k.id, k.name, k.points, k.active, k.sort_order, u.code "
                    "FROM unit_video_kinds k JOIN org_units u ON u.id = k.unit_id "
                    "ORDER BY k.sort_order"
                )
            )
        ).all()
    assert [row.name for row in rows] == EXPECTED_KINDS
    assert list(migration.ADS_VIDEO_KINDS) == EXPECTED_KINDS
    assert all(row.code == "ADS" and row.active and float(row.points) == 1.0 for row in rows)
    assert [row.sort_order for row in rows] == list(range(len(EXPECTED_KINDS)))
    # The same ids on every database.
    assert [row.id for row in rows] == [migration.kind_seed_id(name) for name in EXPECTED_KINDS]


async def test_04_an_order_placed_before_0044_is_untouched(database: Database) -> None:
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT video_type, video_kind_id, video_kind_name, video_kind_points "
                    "FROM orders WHERE id = :id"
                ),
                {"id": OLD_ORDER},
            )
        ).one()
    assert row.video_type == "D"
    assert row.video_kind_id is None and row.video_kind_name is None
    assert row.video_kind_points is None


async def test_05_an_edit_without_a_design_node_needs_the_design_link(
    database: Database,
) -> None:
    for code in ("D", "BD"):
        async with database.session() as session:
            with pytest.raises(IntegrityError) as caught:
                await session.execute(
                    INSERT_ORDER,
                    {
                        "id": uuid.uuid4(),
                        "code": f"NEW-{code}-261008-01",
                        "type": code,
                        "design": None,
                        "owner": OWNER,
                        "at": datetime.now(UTC),
                    },
                )
            assert "design_link_required_without_design" in str(caught.value)
            await session.rollback()
    # Every other process may come without one.
    async with database.session() as session:
        for number, code in enumerate(("B", "T", "BT", "TD", "BTD"), start=1):
            await session.execute(
                INSERT_ORDER,
                {
                    "id": uuid.uuid4(),
                    "code": f"NEW-{code}-261008-{number:02d}",
                    "type": code,
                    "design": None,
                    "owner": OWNER,
                    "at": datetime.now(UTC),
                },
            )
        await session.rollback()


async def test_06_one_name_per_unit_whatever_the_case(database: Database) -> None:
    async with database.session() as session:
        with pytest.raises(IntegrityError) as caught:
            await session.execute(
                text(
                    "INSERT INTO unit_video_kinds (id, unit_id, name) "
                    "VALUES (:id, (SELECT id FROM org_units WHERE code = 'ADS'), 'SHORT VIDEO')"
                ),
                {"id": uuid.uuid4()},
            )
        assert "uq_unit_video_kinds_unit_name" in str(caught.value)
        await session.rollback()
    # The defaults: one point, active, first in the list.
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO unit_video_kinds (id, unit_id, name) "
                "VALUES (:id, (SELECT id FROM org_units WHERE code = 'PR'), 'Short video')"
            ),
            {"id": uuid.uuid4()},
        )
        row = (
            await session.execute(
                text(
                    "SELECT points, active, sort_order FROM unit_video_kinds "
                    "WHERE unit_id = (SELECT id FROM org_units WHERE code = 'PR')"
                )
            )
        ).one()
        assert float(row.points) == 1.0 and row.active is True and row.sort_order == 0
        await session.rollback()


async def test_07_the_migration_goes_both_ways(dsn: str) -> None:
    engine = create_async_engine(dsn)
    new_order = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                INSERT_ORDER,
                {
                    "id": new_order,
                    "code": "NEW-BD-261008-09",
                    "type": "BD",
                    "design": "https://example.com/design",
                    "owner": OWNER,
                    "at": datetime.now(UTC),
                },
            )
    finally:
        await engine.dispose()

    # An order on a new process code: the downgrade refuses and changes nothing.
    with pytest.raises(RuntimeError, match="0044 downgrade refused"):
        await downgrade_to(dsn, "0043")
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            # The refused step rolls the whole downgrade back: still at head.
            assert version == alembic_head()
            assert await connection.scalar(text("SELECT count(*) FROM unit_video_kinds")) == 11
            await connection.execute(text("DELETE FROM orders WHERE id = :id"), {"id": new_order})
    finally:
        await engine.dispose()

    await downgrade_to(dsn, "0043")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0043"
            table = await connection.scalar(text("SELECT to_regclass('unit_video_kinds')"))
            assert table is None
            columns = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'orders' AND column_name = ANY(:names)"
                        ),
                        {"names": list(NEW_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert columns == []
            checks = set(
                (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'orders'::regclass AND contype = 'c'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert "ck_orders_design_link_required_for_d" in checks
            assert "ck_orders_design_link_required_without_design" not in checks
            # The order from before 0044 is still there.
            assert (
                await connection.scalar(
                    text("SELECT video_type FROM orders WHERE id = :id"), {"id": OLD_ORDER}
                )
                == "D"
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == alembic_head()
            assert await connection.scalar(text("SELECT count(*) FROM unit_video_kinds")) == 11
    finally:
        await engine.dispose()
