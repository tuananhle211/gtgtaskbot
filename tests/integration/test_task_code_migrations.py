"""0074: task codes with points - the catalogue and the old design codes.

Runs the real migration on a scratch PostgreSQL database: an order carrying
the 0058 design codes at 0073 comes out of 0074 carrying T1-T5 (two old codes
landing on one new code add up), the ADS unit has the 20 new codes and the old
ones are switched off, and the models agree with the new columns.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import json
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
NEW_TABLES = ("order_node_points", "unit_design_codes")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at 0073 with an order on the old codes, then 0074."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0074_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0073")
        await _seed_old_order(url)
        await upgrade_to(url, "0074")
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def _seed_old_order(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            unit_id = (
                await connection.execute(text("SELECT id FROM org_units WHERE code = 'ADS'"))
            ).scalar_one()
            codes = {
                row[0]: row[1]
                for row in (
                    await connection.execute(
                        text("SELECT code, id FROM unit_design_codes WHERE unit_id = :u"),
                        {"u": unit_id},
                    )
                ).all()
            }
            user_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO users (id, full_name, role, status, active) "
                    "VALUES (:id, 'Người order', 'EMPLOYEE', 'active', true)"
                ),
                {"id": user_id},
            )
            items = [
                {"code_id": str(codes["RBTS"]), "code": "RBTS", "name": "x", "quantity": 2},
                {"code_id": str(codes["TKBTS"]), "code": "TKBTS", "name": "y", "quantity": 1},
                {"code_id": str(codes["TKV"]), "code": "TKV", "name": "z", "quantity": 1},
            ]
            await connection.execute(
                text(
                    "INSERT INTO orders (id, unit_id, code, title, video_type, order_content, "
                    "owner_user_id, stage, submitted_at, design_items) VALUES "
                    "(:id, :unit, 'OLD01', 'Cũ', 'T', 'brief', :owner, 'THIET_KE', now(), "
                    "CAST(:items AS jsonb))"
                ),
                {"id": uuid.uuid4(), "unit": unit_id, "owner": user_id, "items": json.dumps(items)},
            )
    finally:
        await engine.dispose()


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

    def _diff(connection: object) -> list[object]:
        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item
            for item in compare_metadata(context, Base.metadata)
            if any(f"'{table}'" in repr(item) for table in NEW_TABLES)
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_the_new_codes_are_seeded_and_the_old_ones_retired(database: Database) -> None:
    async with database.engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT phase, code, points, active, free_points FROM unit_design_codes")
            )
        ).all()
    active = {(row[0], row[1]): row[2] for row in rows if row[3]}
    assert len(active) == 20
    assert active[("BIEN_TAP", "N5")] == 480
    assert active[("THIET_KE", "T1")] == 90
    assert active[("DUNG", "D7")] == 30
    assert {row[1] for row in rows if row[4]} == {"N0", "T0", "D0"}
    retired = {row[1] for row in rows if not row[3]}
    assert {"RBTS", "TKBTS", "TKV", "O-TD"} <= retired


async def test_03_old_lists_carry_the_new_codes(database: Database) -> None:
    async with database.engine.connect() as connection:
        stored = (
            await connection.execute(text("SELECT design_items FROM orders WHERE code = 'OLD01'"))
        ).scalar_one()
    by_code = {item["code"]: item for item in stored}
    # RBTS x2 + TKBTS x1 -> T1 x3; TKV -> T3. Only the code changes.
    assert set(by_code) == {"T1", "T3"}
    assert by_code["T1"]["quantity"] == 3 and by_code["T1"]["points"] == 270
    assert by_code["T3"]["quantity"] == 1 and by_code["T3"]["points"] == 480
    assert by_code["T1"]["form"] == "NEW"


async def test_04_it_goes_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0073")
    await upgrade_to(dsn, "0074")
