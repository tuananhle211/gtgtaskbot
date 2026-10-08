"""``0042`` against a real PostgreSQL: the seed, the tag, the invariants, the roundtrip.

* **every existing user is tagged PR.** Accounts that exist at ``0041`` come
  out of the upgrade with one open ``MEMBER`` row in the PR unit - active,
  suspended and revoked alike;
* **the registry and the KPI mapping exist** - two units, three Ads work
  types, three default work rules;
* **the models describe the migration** - ``compare_metadata`` over the nine
  tables is empty at head;
* **one active node per order** is held by the database, not only the service;
* **the counter upsert is gap-free** under concurrent allocation;
* **the migration goes both ways** - ``0042 -> 0041 -> 0042``; the seeded
  work types stay through the downgrade and are reused by the upgrade.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_order_migrations.py -m integration
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

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

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
NEW_TABLES = (
    "org_units",
    "org_unit_members",
    "orders",
    "order_nodes",
    "order_submissions",
    "order_events",
    "order_approvals",
    "order_code_counters",
    "order_work_rules",
)
WORK_TYPE_CODES = ("ADS_BIEN_TAP", "ADS_THIET_KE", "ADS_DUNG")

ACTIVE_USER = uuid.uuid4()
SUSPENDED_USER = uuid.uuid4()
REVOKED_USER = uuid.uuid4()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0041`` with three users, then taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0042_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0041")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                for user_id, status, active in (
                    (ACTIVE_USER, "active", True),
                    (SUSPENDED_USER, "suspended", False),
                    (REVOKED_USER, "revoked", False),
                ):
                    await connection.execute(
                        text(
                            "INSERT INTO users (id, full_name, role, active, status, "
                            " created_at, updated_at) "
                            "VALUES (:id, :name, 'EMPLOYEE', :active, :status, now(), now())"
                        ),
                        {
                            "id": user_id,
                            "name": f"User {status}",
                            "active": active,
                            "status": status,
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
    ours = [one for one in differences if any(name in str(one) for name in NEW_TABLES)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_every_existing_user_is_tagged_pr(database: Database) -> None:
    async with database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT m.user_id, m.role, m.is_lead, m.left_at, u.code "
                    "FROM org_unit_members m JOIN org_units u ON u.id = m.unit_id"
                )
            )
        ).all()
    assert {row.user_id for row in rows} == {ACTIVE_USER, SUSPENDED_USER, REVOKED_USER}
    assert all(row.role == "MEMBER" and row.code == "PR" and not row.is_lead for row in rows)
    assert all(row.left_at is None for row in rows)


async def test_04_the_registry_and_the_kpi_mapping_are_seeded(database: Database) -> None:
    async with database.session() as session:
        units = (await session.execute(text("SELECT code, active FROM org_units"))).all()
        types = (
            await session.execute(
                text(
                    "SELECT code, category FROM pr_work_types WHERE code LIKE 'ADS_%' ORDER BY code"
                )
            )
        ).all()
        rules = (
            await session.execute(
                text(
                    "SELECT r.node_type, r.video_type, t.code, u.code AS unit "
                    "FROM order_work_rules r "
                    "JOIN pr_work_types t ON t.id = r.work_type_id "
                    "JOIN org_units u ON u.id = r.unit_id ORDER BY r.node_type"
                )
            )
        ).all()
    assert {(row.code, row.active) for row in units} == {("PR", True), ("ADS", True)}
    assert [row.code for row in types] == sorted(WORK_TYPE_CODES)
    assert all(row.category == "PRODUCTION" for row in types)
    assert {(row.node_type, row.video_type, row.code, row.unit) for row in rules} == {
        ("BIEN_TAP", None, "ADS_BIEN_TAP", "ADS"),
        ("THIET_KE", None, "ADS_THIET_KE", "ADS"),
        ("DUNG", None, "ADS_DUNG", "ADS"),
    }


async def _ads_unit_id(session) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return await session.scalar(text("SELECT id FROM org_units WHERE code = 'ADS'"))


async def _insert_order(session, *, unit_id: uuid.UUID, code: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    order_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO orders (id, unit_id, code, title, video_type, order_content, "
            " owner_user_id, stage, submitted_at, created_at, updated_at) "
            "VALUES (:id, :unit, :code, 'Kịch bản A', 'BTD', 'brief', :owner, "
            " 'BIEN_TAP', :at, now(), now())"
        ),
        {
            "id": order_id,
            "unit": unit_id,
            "code": code,
            "owner": ACTIVE_USER,
            "at": datetime.now(UTC),
        },
    )
    return order_id


async def test_05_the_database_holds_one_active_node_per_order(database: Database) -> None:
    async with database.session() as session:
        order_id = await _insert_order(
            session, unit_id=await _ads_unit_id(session), code="TEST-BTD-261007-01"
        )
        insert = text(
            "INSERT INTO order_nodes (id, order_id, node_type, status, created_at, updated_at) "
            "VALUES (:id, :order, :node, :status, now(), now())"
        )
        await session.execute(
            insert,
            {"id": uuid.uuid4(), "order": order_id, "node": "BIEN_TAP", "status": "DANG_LAM"},
        )
        # A finished and a not-yet-reached node beside the active one are fine.
        await session.execute(
            insert, {"id": uuid.uuid4(), "order": order_id, "node": "DUNG", "status": "CHUA_TOI"}
        )
        with pytest.raises(IntegrityError) as caught:
            await session.execute(
                insert,
                {"id": uuid.uuid4(), "order": order_id, "node": "THIET_KE", "status": "CHO_DUYET"},
            )
        assert "uq_order_nodes_active_per_order" in str(caught.value)
        await session.rollback()


async def test_06_the_design_link_is_required_for_a_quick_edit(database: Database) -> None:
    async with database.session() as session:
        unit_id = await _ads_unit_id(session)
        with pytest.raises(IntegrityError) as caught:
            await session.execute(
                text(
                    "INSERT INTO orders (id, unit_id, code, title, video_type, order_content, "
                    " owner_user_id, submitted_at, created_at, updated_at) "
                    "VALUES (:id, :unit, 'TEST-D-261007-01', 'Nhanh', 'D', 'brief', :owner, "
                    " now(), now(), now())"
                ),
                {"id": uuid.uuid4(), "unit": unit_id, "owner": ACTIVE_USER},
            )
        # 0044 generalised 0042's check to every edit without a design node.
        assert "design_link_required_without_design" in str(caught.value)
        await session.rollback()


async def test_07_the_code_counter_is_gap_free_under_concurrency(database: Database) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from meobot.db.models.order import OrderCodeCounter

    async with database.session() as session:
        unit_id = await _ads_unit_id(session)

    async def allocate() -> int:
        async with database.transaction() as session:
            statement = (
                pg_insert(OrderCodeCounter)
                .values(
                    id=uuid.uuid4(),
                    unit_id=unit_id,
                    member_code="TEST",
                    day=date(2026, 10, 7),
                    last_no=1,
                )
                .on_conflict_do_update(
                    index_elements=[
                        OrderCodeCounter.unit_id,
                        OrderCodeCounter.member_code,
                        OrderCodeCounter.day,
                    ],
                    set_={"last_no": OrderCodeCounter.last_no + 1},
                )
                .returning(OrderCodeCounter.last_no)
            )
            return int((await session.execute(statement)).scalar_one())

    numbers = await asyncio.gather(*(allocate() for _ in range(12)))
    assert sorted(numbers) == list(range(1, 13))


async def test_08_the_migration_goes_both_ways(dsn: str) -> None:
    await downgrade_to(dsn, "0041")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0041"
            present = (
                (
                    await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                        ),
                        {"names": list(NEW_TABLES)},
                    )
                )
                .scalars()
                .all()
            )
            assert present == []
            # The seeded work types are taxonomy and stay through a downgrade.
            kept = await connection.scalar(
                text("SELECT count(*) FROM pr_work_types WHERE code = ANY(:codes)"),
                {"codes": list(WORK_TYPE_CODES)},
            )
            assert kept == 3
    finally:
        await engine.dispose()
    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == alembic_head()
            tagged = await connection.scalar(text("SELECT count(*) FROM org_unit_members"))
            assert tagged == 3
    finally:
        await engine.dispose()
