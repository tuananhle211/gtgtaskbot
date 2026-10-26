"""0075: points carry the department they count for.

Runs the real migration on a scratch PostgreSQL database: points recorded at
0074 come out of 0075 counted for the department of the step's phase (a
person seated in two departments), else their first department, and the
model agrees with the new column.

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
TWO_SEATS = uuid.uuid4()
ONE_SEAT = uuid.uuid4()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at 0074 with two people's points, then 0075."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0075_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0074")
        await _seed_points(url)
        await upgrade_to(url, "0075")
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def _department(connection: object, function: str) -> uuid.UUID:
    return (  # type: ignore[no-any-return]
        await connection.execute(  # type: ignore[attr-defined]
            text("SELECT id FROM departments WHERE function = :f ORDER BY sort_order LIMIT 1"),
            {"f": function},
        )
    ).scalar_one()


async def _seed_points(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            # A department of another function, first in the order, and Dựng.
            other = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO departments (id, code, name, function, active, sort_order) "
                    "VALUES (:id, 'ZZMKT', 'MKT thử', 'NONE', true, -1)"
                ),
                {"id": other},
            )
            dung = await _department(connection, "DUNG")
            unit_id = (
                await connection.execute(text("SELECT id FROM org_units WHERE code = 'ADS'"))
            ).scalar_one()
            for user_id, name in ((TWO_SEATS, "Hai phòng"), (ONE_SEAT, "Một phòng")):
                await connection.execute(
                    text(
                        "INSERT INTO users (id, full_name, role, status, active) "
                        "VALUES (:id, :name, 'EMPLOYEE', 'active', true)"
                    ),
                    {"id": user_id, "name": name},
                )
            seats = ((TWO_SEATS, other), (TWO_SEATS, dung), (ONE_SEAT, other))
            for user_id, department_id in seats:
                await connection.execute(
                    text(
                        "INSERT INTO department_members (id, department_id, user_id, position) "
                        "VALUES (:id, :d, :u, 'MEMBER')"
                    ),
                    {"id": uuid.uuid4(), "d": department_id, "u": user_id},
                )
            order_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO orders (id, unit_id, code, title, video_type, order_content, "
                    "owner_user_id, stage, submitted_at) VALUES "
                    "(:id, :unit, 'PTS01', 'Điểm', 'D', 'brief', :owner, 'COMPLETED', now())"
                ),
                {"id": order_id, "unit": unit_id, "owner": TWO_SEATS},
            )
            node_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO order_nodes (id, order_id, node_type, status) "
                    "VALUES (:id, :order, 'DUNG', 'HOAN_THANH')"
                ),
                {"id": node_id, "order": order_id},
            )
            for user_id in (TWO_SEATS, ONE_SEAT):
                await connection.execute(
                    text(
                        "INSERT INTO order_node_points (id, node_id, order_id, user_id, phase, "
                        "items, points, first_completed_at) VALUES "
                        "(:id, :node, :order, :user, 'DUNG', CAST('[]' AS jsonb), 240, now())"
                    ),
                    {"id": uuid.uuid4(), "node": node_id, "order": order_id, "user": user_id},
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
            if "'order_node_points'" in repr(item)
        ]

    async with database.engine.begin() as connection:
        diff = await connection.run_sync(_diff)
    assert diff == [], diff


async def test_02_existing_points_count_for_the_phase_department(database: Database) -> None:
    async with database.engine.connect() as connection:
        rows = dict(
            (
                await connection.execute(
                    text(
                        "SELECT p.user_id, d.function FROM order_node_points p "
                        "JOIN departments d ON d.id = p.department_id"
                    )
                )
            ).all()
        )
    # Seated in MKT (first) and Dựng: a Dựng step counts for Dựng.
    assert rows[TWO_SEATS] == "DUNG"
    # Seated only in MKT: there.
    assert rows[ONE_SEAT] == "NONE"


async def test_03_it_goes_down_and_up_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0074")
    await upgrade_to(dsn, "0075")
