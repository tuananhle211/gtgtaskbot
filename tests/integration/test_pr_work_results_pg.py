"""``0039`` against a real PostgreSQL: the roundtrip, the drift check, the data step.

What only a real database answers here:

* **the models describe the migration.** ``compare_metadata`` over what
  ``0039`` adds - the container columns and index on ``pr_work_items``, the
  results table, the template flag, the score allocation column;
* **the migration goes both ways.** ``0039 -> 0038 -> 0039``, including the
  CHECK it replaces and the units it folds back;
* **the partial unique index is the rule.** Two containers for one employee,
  work type and month are refused by the database, and two results with one
  source key likewise;
* **the data step is bounded.** *Tìm khách hàng* reads in customers afterwards;
  a type that is recognisably something else keeps its unit; a work item filed
  under the old unit keeps it;
* **the backfill on score allocations is a copy.** ``counted_amount`` equals
  the ``eligible_amount`` those rows priced under ``0035``.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_results_pg.py -m integration
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

#: Everything ``0039`` adds, and the filter every drift assertion uses.
NEW_NAMES = (
    "reporting_period_id",
    "subject_user_id",
    "pr_work_results",
    "accumulate_by_period",
    "counted_amount",
    "uq_pr_work_items_period_container",
    "period_container_has_subject",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database migrated to ``0038`` and seeded, then taken to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0039_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0038")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await _seed_under_0038(connection)
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


async def _seed_under_0038(connection) -> None:  # type: ignore[no-untyped-def]
    """Rows as a deployment on ``0038`` would hold them."""
    await connection.execute(
        text(
            "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
            "VALUES (:id, 'Người cũ', 'EMPLOYEE', true, now(), now())"
        ),
        {"id": USER},
    )
    for type_id, code, name in (
        (CUSTOMERS, "TIM_KHACH", "Tìm khách hàng"),
        (SCRIPTS, "SHORT_SCRIPT", "Kịch bản ngắn"),
    ):
        await connection.execute(
            text(
                "INSERT INTO pr_work_types "
                "(id, code, name, category, default_unit, default_quota_basis, "
                " requires_evidence, is_active, display_order, created_at, updated_at) "
                "VALUES (:id, :code, :name, 'COMMUNITY', 'ITEM', 'QUANTITY', "
                " false, true, 0, now(), now())"
            ),
            {"id": type_id, "code": code, "name": name},
        )
    # A job filed under the old unit, before the rename.
    await connection.execute(
        text(
            "INSERT INTO pr_work_items "
            "(id, code, title, work_type_id, source_type, status, priority, quantity, unit, "
            " created_by_user_id, created_at, updated_at) "
            "VALUES (:id, 'WRK-OLD', 'Việc cũ', :type_id, 'MANUAL', 'ACCEPTED', 'NORMAL', "
            " 4, 'ITEM', :user, now(), now())"
        ),
        {"id": LEGACY_ITEM, "type_id": CUSTOMERS, "user": USER},
    )
    await connection.execute(
        text(
            "INSERT INTO pr_work_contributions "
            "(id, work_item_id, user_id, contribution_role, credit_weight, assigned_at, "
            " count_status, counted_at, created_at, updated_at) "
            "VALUES (:id, :item, :user, 'PRIMARY', 1.0, now(), 'COUNTED', now(), now(), now())"
        ),
        {"id": LEGACY_CONTRIBUTION, "item": LEGACY_ITEM, "user": USER},
    )
    await connection.execute(
        text(
            "INSERT INTO pr_reporting_periods "
            "(id, code, period_type, date_start, date_end, status, created_at, updated_at) "
            "VALUES (:id, '2026-09', 'MONTH', '2026-09-01', '2026-09-30', 'OPEN', now(), now())"
        ),
        {"id": PERIOD},
    )
    # A score allocation priced under 0035: eligible 3 of a counted 4.
    await connection.execute(
        text(
            "INSERT INTO pr_work_score_allocations "
            "(id, work_contribution_id, user_id, reporting_period_id, work_type_id, "
            " scoring_rule_id, eligible_amount, standard_minutes_per_unit, "
            " eligible_standard_minutes, status, evaluated_at, created_at, updated_at) "
            "VALUES (:id, :contribution, :user, :period, :type_id, NULL, 3.00, NULL, 0, "
            " 'NO_SCORING_RULE', now(), now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "contribution": LEGACY_CONTRIBUTION,
            "user": USER,
            "period": PERIOD,
            "type_id": CUSTOMERS,
        },
    )


USER = uuid.uuid4()
CUSTOMERS = uuid.uuid4()
SCRIPTS = uuid.uuid4()
LEGACY_ITEM = uuid.uuid4()
LEGACY_CONTRIBUTION = uuid.uuid4()
PERIOD = uuid.uuid4()


# ===========================================================================
# The models describe the migration
# ===========================================================================


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

    ours = [
        difference
        for difference in differences
        if any(name in str(difference) for name in NEW_NAMES)
    ]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision_on_disk(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    # ``0040`` is the content-work auto-provisioning revision: one nullable
    # column, no table, and it sits on top of this one.
    assert head == alembic_head()


# ===========================================================================
# The data step
# ===========================================================================


async def test_03_find_customers_reads_in_customers_and_history_keeps_its_unit(
    database: Database,
) -> None:
    async with database.session() as session:
        units = dict(
            (
                await session.execute(
                    text("SELECT code, default_unit FROM pr_work_types ORDER BY code")
                )
            ).all()
        )
        legacy_unit = await session.scalar(
            text("SELECT unit FROM pr_work_items WHERE id = :id"), {"id": LEGACY_ITEM}
        )
        counted = await session.scalar(
            text(
                "SELECT counted_amount FROM pr_work_score_allocations "
                "WHERE work_contribution_id = :id"
            ),
            {"id": LEGACY_CONTRIBUTION},
        )
    assert units["TIM_KHACH"] == "CUSTOMER", "recognised by its name"
    assert units["SHORT_SCRIPT"] == "ITEM", "everything else is left alone"
    assert legacy_unit == "ITEM", "a job filed under the old unit keeps it"
    assert str(counted) == "3.00", "backfilled from the amount those rows priced"


# ===========================================================================
# The rules the database enforces
# ===========================================================================


async def test_04_one_container_per_employee_type_and_month(database: Database) -> None:
    insert = text(
        "INSERT INTO pr_work_items "
        "(id, code, title, work_type_id, source_type, status, priority, quantity, unit, "
        " created_by_user_id, reporting_period_id, subject_user_id, created_at, updated_at) "
        "VALUES (:id, :code, 'Luồng', :type_id, 'MANUAL', 'ACCEPTED', 'NORMAL', 0, 'CUSTOMER', "
        " :user, :period, :user, now(), now())"
    )
    async with database.session() as session, session.begin():
        await session.execute(
            insert,
            {
                "id": uuid.uuid4(),
                "code": "WRK-C1",
                "type_id": CUSTOMERS,
                "user": USER,
                "period": PERIOD,
            },
        )
    with pytest.raises(IntegrityError) as caught:
        async with database.session() as session, session.begin():
            await session.execute(
                insert,
                {
                    "id": uuid.uuid4(),
                    "code": "WRK-C2",
                    "type_id": CUSTOMERS,
                    "user": USER,
                    "period": PERIOD,
                },
            )
    assert "uq_pr_work_items_period_container" in str(caught.value)

    # A container may hold zero; a one-off job still may not.
    with pytest.raises(IntegrityError) as refused:
        async with database.session() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO pr_work_items "
                    "(id, code, title, work_type_id, source_type, status, priority, quantity, "
                    " unit, created_by_user_id, created_at, updated_at) "
                    "VALUES (:id, 'WRK-Z', 'Không', :type_id, 'MANUAL', 'ACCEPTED', 'NORMAL', "
                    " 0, 'CUSTOMER', :user, now(), now())"
                ),
                {"id": uuid.uuid4(), "type_id": CUSTOMERS, "user": USER},
            )
    assert "quantity_positive" in str(refused.value)

    # And a subject without a period, or the reverse, is refused.
    with pytest.raises(IntegrityError) as half:
        async with database.session() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO pr_work_items "
                    "(id, code, title, work_type_id, source_type, status, priority, "
                    " created_by_user_id, subject_user_id, created_at, updated_at) "
                    "VALUES (:id, 'WRK-H', 'Nửa', :type_id, 'MANUAL', 'ACCEPTED', 'NORMAL', "
                    " :user, :user, now(), now())"
                ),
                {"id": uuid.uuid4(), "type_id": CUSTOMERS, "user": USER},
            )
    assert "period_container_has_subject" in str(half.value)


async def test_05_one_result_per_source_key(database: Database) -> None:
    async with database.session() as session:
        item_id = await session.scalar(text("SELECT id FROM pr_work_items WHERE code = 'WRK-C1'"))
    assert item_id is not None
    insert = text(
        "INSERT INTO pr_work_results "
        "(id, work_item_id, user_id, quantity, source_type, source_key, status, "
        " reported_by_user_id, reported_at, created_at, updated_at) "
        "VALUES (:id, :item, :user, 1, 'CONTENT', :key, 'PENDING', :user, now(), now(), now())"
    )
    key = f"content:{uuid.uuid4()}:CONTENT_CREATION"
    async with database.session() as session, session.begin():
        await session.execute(
            insert, {"id": uuid.uuid4(), "item": item_id, "user": USER, "key": key}
        )
    with pytest.raises(IntegrityError) as caught:
        async with database.session() as session, session.begin():
            await session.execute(
                insert, {"id": uuid.uuid4(), "item": item_id, "user": USER, "key": key}
            )
    assert "uq_pr_work_results_source" in str(caught.value)

    # Two manual results have no key and coexist.
    manual = text(
        "INSERT INTO pr_work_results "
        "(id, work_item_id, user_id, quantity, source_type, status, "
        " reported_by_user_id, reported_at, created_at, updated_at) "
        "VALUES (:id, :item, :user, :quantity, 'MANUAL', 'PENDING', :user, now(), now(), now())"
    )
    async with database.session() as session, session.begin():
        for quantity in (3, 5):
            await session.execute(
                manual, {"id": uuid.uuid4(), "item": item_id, "user": USER, "quantity": quantity}
            )

    # Counted pairs with its instant, at the database.
    with pytest.raises(IntegrityError) as half:
        async with database.session() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO pr_work_results "
                    "(id, work_item_id, user_id, quantity, source_type, status, "
                    " reported_by_user_id, reported_at, created_at, updated_at) "
                    "VALUES (:id, :item, :user, 1, 'MANUAL', 'COUNTED', :user, now(), "
                    " now(), now())"
                ),
                {"id": uuid.uuid4(), "item": item_id, "user": USER},
            )
    assert "counted_at_matches_status" in str(half.value)


# ===========================================================================
# The roundtrip
# ===========================================================================


async def test_06_downgrade_and_upgrade_again(dsn: str, database: Database) -> None:
    """``0039 -> 0038 -> 0039``. The results are gone, the rest survives."""
    await database.dispose()
    await downgrade_to(dsn, "0038")

    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            tables = {
                row[0]
                for row in (
                    await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).all()
            }
            assert "pr_work_results" not in tables
            columns = {
                row[0]
                for row in (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'pr_work_items'"
                        )
                    )
                ).all()
            }
            assert "reporting_period_id" not in columns and "subject_user_id" not in columns
            # The new units folded back so the older enum meets nothing strange.
            units = set(
                (await connection.execute(text("SELECT default_unit FROM pr_work_types"))).scalars()
            )
            assert units <= {"ITEM"}
            # The container became an ordinary accepted item with no quantity.
            zero = await connection.scalar(
                text("SELECT quantity FROM pr_work_items WHERE code = 'WRK-C1'")
            )
            assert zero is None
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0038"
    finally:
        await engine.dispose()

    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == alembic_head()
            counted = await connection.scalar(
                text(
                    "SELECT counted_amount FROM pr_work_score_allocations "
                    "WHERE work_contribution_id = :id"
                ),
                {"id": LEGACY_CONTRIBUTION},
            )
            assert str(counted) == "3.00", "the backfill is a copy and runs again"
    finally:
        await engine.dispose()
