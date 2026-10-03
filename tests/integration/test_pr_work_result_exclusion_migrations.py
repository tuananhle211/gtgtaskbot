"""``0041`` against a real PostgreSQL: the roundtrip, the CHECK, no backfill.

* **existing excluded rows are not classified.** A row excluded under ``0040``
  comes out of the upgrade with ``exclusion_kind IS NULL``, whatever its free
  text said - the reason is not guessed;
* **the CHECK is the rule.** A kind on a row that is not ``EXCLUDED`` is
  refused by the database; a kind on an excluded row is accepted;
* **the models describe the migration** - ``compare_metadata`` over what
  ``0041`` adds is empty at head;
* **the migration goes both ways** - ``0041 -> 0040 -> 0041``.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_result_exclusion_migrations.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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
NEW_NAMES = ("exclusion_kind", "exclusion_kind_matches_status")

USER = uuid.uuid4()
TYPE = uuid.uuid4()
ITEM = uuid.uuid4()
#: Excluded under ``0040`` with the administrator's prefix, a validator's free
#: text and the projector's sentence respectively - and one counted row.
ADMIN_TEXT = uuid.uuid4()
VALIDATOR_TEXT = uuid.uuid4()
PROJECTOR_TEXT = uuid.uuid4()
COUNTED = uuid.uuid4()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database migrated to ``0040`` and seeded, then taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0041_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0040")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await _seed_under_0040(connection)
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
    handle = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))
    try:
        yield handle
    finally:
        await handle.dispose()


async def _seed_under_0040(connection) -> None:  # type: ignore[no-untyped-def]
    """Rows as a deployment on ``0040`` would hold them: no kind anywhere."""
    await connection.execute(
        text(
            "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
            "VALUES (:id, 'Người cũ', 'EMPLOYEE', true, now(), now())"
        ),
        {"id": USER},
    )
    await connection.execute(
        text(
            "INSERT INTO pr_work_types "
            "(id, code, name, category, default_unit, default_quota_basis, "
            " requires_evidence, is_active, display_order, created_at, updated_at) "
            "VALUES (:id, 'TIM_KHACH', 'Tìm khách hàng', 'COMMUNITY', 'CUSTOMER', "
            " 'QUANTITY', false, true, 0, now(), now())"
        ),
        {"id": TYPE},
    )
    await connection.execute(
        text(
            "INSERT INTO pr_work_items "
            "(id, code, title, work_type_id, source_type, status, priority, quantity, unit, "
            " created_by_user_id, created_at, updated_at) "
            "VALUES (:id, 'WRK-OLD', 'Việc cũ', :type_id, 'MANUAL', 'ACCEPTED', 'NORMAL', "
            " 4, 'CUSTOMER', :user, now(), now())"
        ),
        {"id": ITEM, "type_id": TYPE, "user": USER},
    )
    excluded = (
        (ADMIN_TEXT, "Quản trị viên gỡ kết quả: ghi nhầm"),
        (VALIDATOR_TEXT, "Không đủ minh chứng"),
        (PROJECTOR_TEXT, "CNT-2026-000001: nguồn không còn xác nhận công việc này"),
    )
    for result_id, reason in excluded:
        await connection.execute(
            text(
                "INSERT INTO pr_work_results "
                "(id, work_item_id, user_id, quantity, source_type, status, "
                " reported_by_user_id, reported_at, excluded_at, excluded_reason, "
                " created_at, updated_at) "
                "VALUES (:id, :item, :user, 1, 'MANUAL', 'EXCLUDED', :user, now(), now(), "
                " :reason, now(), now())"
            ),
            {"id": result_id, "item": ITEM, "user": USER, "reason": reason},
        )
    await connection.execute(
        text(
            "INSERT INTO pr_work_results "
            "(id, work_item_id, user_id, quantity, source_type, status, "
            " reported_by_user_id, reported_at, counted_at, created_at, updated_at) "
            "VALUES (:id, :item, :user, 1, 'MANUAL', 'COUNTED', :user, now(), now(), now(), now())"
        ),
        {"id": COUNTED, "item": ITEM, "user": USER},
    )


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
    ours = [one for one in differences if any(name in str(one) for name in NEW_NAMES)]
    assert ours == [], ours


async def test_02_the_head_is_0041(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head() == "0041"


async def test_03_legacy_excluded_rows_are_not_classified(database: Database) -> None:
    async with database.session() as session:
        rows = (
            await session.execute(
                text("SELECT id, status, exclusion_kind FROM pr_work_results ORDER BY id")
            )
        ).all()
    assert len(rows) == 4
    assert all(kind is None for _id, _status, kind in rows), rows
    assert {status for _id, status, _kind in rows} == {"EXCLUDED", "COUNTED"}


async def test_04_a_kind_belongs_only_to_an_excluded_row(database: Database) -> None:
    async with database.session() as session:
        with pytest.raises(IntegrityError) as caught:
            await session.execute(
                text("UPDATE pr_work_results SET exclusion_kind = 'ADMIN_REMOVED' WHERE id = :id"),
                {"id": COUNTED},
            )
        assert "exclusion_kind_matches_status" in str(caught.value)
        await session.rollback()
    async with database.session() as session:
        for result_id, kind in (
            (ADMIN_TEXT, "ADMIN_REMOVED"),
            (VALIDATOR_TEXT, "VALIDATOR_REJECTED"),
            (PROJECTOR_TEXT, "SOURCE_REVERSED"),
        ):
            await session.execute(
                text("UPDATE pr_work_results SET exclusion_kind = :kind WHERE id = :id"),
                {"id": result_id, "kind": kind},
            )
        await session.rollback()  # leave the seeded rows as the upgrade left them


async def test_05_downgrade_and_upgrade_again(dsn: str, database: Database) -> None:
    await database.dispose()
    await downgrade_to(dsn, "0040")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'pr_work_results'"
                        )
                    )
                ).scalars()
            )
            assert "exclusion_kind" not in columns
            left = await connection.scalar(text("SELECT count(*) FROM pr_work_results"))
            assert left == 4, "no row is lost on the way down"
    finally:
        await engine.dispose()
    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            kinds = list(
                (
                    await connection.execute(text("SELECT exclusion_kind FROM pr_work_results"))
                ).scalars()
            )
            assert kinds == [None] * 4
    finally:
        await engine.dispose()
