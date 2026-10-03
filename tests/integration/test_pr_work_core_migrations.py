"""Migration 0032, on a database built the way production builds one.

Six claims, and only PostgreSQL can settle them:

* **the five tables and their constraints are what the revision claims** - the
  columns, ``RESTRICT`` on every foreign key but one, the non-empty checks, and
  the check that couples ``count_status`` to ``counted_at``;

* **the source index is partial.** Derived work must exist exactly once per
  semantic event however often its source event is replayed, while manual work -
  which has no key - must be unconstrained. A non-partial index would pass the
  first half and break the second, so both are asserted;

* **one person, one capacity, one job.** Crediting somebody twice for one job in
  one role is refused by the database rather than only by the service;

* **the check constraint refuses a half-write.** A contribution marked
  ``COUNTED`` with no ``counted_at`` - or the reverse - is a row no reporting
  period could claim, and it cannot be inserted;

* **0031's data is untouched.** A user, a channel, a task and a content item
  written *before* 0032 come through the upgrade, back down again and up again
  unchanged. That is the claim M1's whole risk profile rests on: the ledger is
  additive, and a mistake in it cannot reach the workflow the department runs on;

* **the downgrade works and loses exactly what it says it loses** - the five
  work tables, and nothing else.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_core_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_work_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.session import Database
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
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

WORK_TYPES = "pr_work_types"
WORK_ITEMS = "pr_work_items"
WORK_CONTRIBUTIONS = "pr_work_contributions"
WORK_EVIDENCE = "pr_work_evidence"
WORK_HISTORY = "pr_work_history"
WORK_TABLES = (WORK_TYPES, WORK_ITEMS, WORK_CONTRIBUTIONS, WORK_EVIDENCE, WORK_HISTORY)

SOURCE_INDEX = "uq_pr_work_items_source"
ROLE_INDEX = "uq_pr_work_contributions_item_user_role"

#: The columns 0032 is responsible for. Written out here rather than derived
#: from the models, so that deleting one from a model does not quietly delete
#: the assertion that it exists.
EXPECTED_ITEM_COLUMNS = frozenset(
    {
        "id",
        "code",
        "title",
        "description",
        "work_type_id",
        "source_type",
        "source_key",
        "status",
        "priority",
        "quantity",
        "unit",
        "created_by_user_id",
        "assigned_by_user_id",
        "assigned_at",
        "accepted_at",
        "started_at",
        "completed_at",
        "completed_by_user_id",
        "approved_at",
        "approved_by_user_id",
        "due_at",
        "cancelled_at",
        "cancelled_by_user_id",
        "cancel_reason",
        "channel_id",
        "content_id",
        "task_id",
        "created_at",
        "updated_at",
    }
)

EXPECTED_CONTRIBUTION_COLUMNS = frozenset(
    {
        "id",
        "work_item_id",
        "user_id",
        "contribution_role",
        "credit_weight",
        "assigned_at",
        "count_status",
        "counted_at",
        "excluded_reason",
        "excluded_by_user_id",
        "excluded_at",
        "created_at",
        "updated_at",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0031() -> AsyncIterator[tuple[Database, str]]:
    """A database at **0031**, with pre-existing rows the ledger must not touch.

    One revision short of head, so a user, a channel, a task and a content item
    exist *before* the work tables do - which is the only way to prove 0032
    walked past them.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_work_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0031")

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database, dsn
        finally:
            await database.engine.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": scratch},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}"'))
        await admin.dispose()


async def _seed_pre_0032(database: Database) -> dict[str, Any]:
    """A user, a platform, a channel, a content item and a task, written at 0031.

    Raw SQL rather than the ORM: the point is to write rows the way 0031's
    schema has them and read them back after 0032 without the models being
    involved in either direction.
    """
    ids = {
        "user": uuid.uuid4(),
        "platform": uuid.uuid4(),
        "brand": uuid.uuid4(),
        "channel": uuid.uuid4(),
        "content": uuid.uuid4(),
        "task": uuid.uuid4(),
    }
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, full_name, role, active, status, created_at, updated_at) "
                "VALUES (:id, 'Người thử', 'EMPLOYEE', true, 'ACTIVE', now(), now())"
            ),
            {"id": ids["user"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_platforms (id, code, name, api_available, status, "
                "created_at, updated_at) "
                "VALUES (:id, 'TIKTOK', 'TikTok', false, 'ACTIVE', now(), now())"
            ),
            {"id": ids["platform"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_brands (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, 'APEXMED', 'Apexmed', 'ACTIVE', now(), now())"
            ),
            {"id": ids["brand"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_channels (id, code, name, platform_id, category, status, "
                "created_at, updated_at) "
                "VALUES (:id, 'CH-9001', 'Kênh thử', :platform, 'SCALE', 'ACTIVE', now(), now())"
            ),
            {"id": ids["channel"], "platform": ids["platform"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_content_items (id, code, title, brand_id, priority, "
                "workflow_stage, owner_user_id, created_by_user_id, created_at, updated_at) "
                "VALUES (:id, 'CNT-2026-900001', 'Nội dung thử', :brand, 'NORMAL', 'IDEA', "
                ":user, :user, now(), now())"
            ),
            {"id": ids["content"], "brand": ids["brand"], "user": ids["user"]},
        )
        await session.execute(
            text(
                "INSERT INTO pr_tasks (id, code, task_type, title, priority, status, "
                "created_by_user_id, created_at, updated_at) "
                "VALUES (:id, 'TSK-2026-900001', 'SCRIPT', 'Task thử', 'NORMAL', 'TODO', "
                ":user, now(), now())"
            ),
            {"id": ids["task"], "user": ids["user"]},
        )
        await session.commit()
    return ids


async def _fingerprint(database: Database) -> dict[str, Any]:
    """What the pre-existing rows say. Compared before and after the migration."""
    async with database.session() as session:
        rows = {}
        for table, column in (
            ("users", "full_name"),
            ("pr_channels", "code"),
            ("pr_content_items", "workflow_stage"),
            ("pr_tasks", "status"),
        ):
            result = await session.execute(
                text(f"SELECT count(*), max({column}::text) FROM {table}")  # noqa: S608
            )
            rows[table] = tuple(result.one())
        return rows


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def work_database(stopped_at_0031: tuple[Database, str]) -> Database:
    """The same database, taken to **0032**, with 0031's rows already in it."""
    database, dsn = stopped_at_0031
    await _seed_pre_0032(database)
    await upgrade_to(dsn, "0032")
    return database


# ---------------------------------------------------------------------------
# 1: the models and the migration agree
# ---------------------------------------------------------------------------


async def test_the_models_and_the_migration_describe_the_same_work_schema(
    work_database: Database,
) -> None:
    """Alembic's own comparator, restricted to ``pr_work_`` tables.

    Restricted for the reason ``test_pr_core_migrations`` restricts its own:
    this repository has pre-existing drift between older models and older
    migrations, and widening the assertion would make it fail for reasons M1 did
    not cause and must not fix.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from meobot.db.base import Base

    def _compare(connection: Any) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with work_database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    # Restricted to **0032's own five tables**, by name.
    #
    # A ``"pr_work" in str(difference)`` match was enough while 0032 was head. It
    # stopped being enough at 0033 (``pr_work_plans``, ``pr_work_quotas``,
    # ``pr_work_quota_allocations``, and one column on ``pr_work_types``) and
    # again at 0034 - M3's two tables are not named ``pr_work_*`` at all, but
    # both carry a foreign key to ``pr_work_types``, so ``pr_work`` appears in
    # their repr anyway.
    #
    # This database is deliberately stopped at 0032, so every one of those
    # *should* read as missing; reporting them here would be reporting that a
    # later revision exists. Each revision's own suite asserts its own tables
    # against a database standing at that revision.
    later = (
        # 0033
        "pr_work_plans",
        "pr_work_quotas",
        "pr_work_quota_allocations",
        "default_quota_basis",
        # 0034
        "pr_content_work_rules",
        "pr_content_work_projections",
        # 0035. M6's tables are the same shape of false positive one revision
        # further on: ``pr_work_scoring_rules`` and ``pr_work_score_allocations``
        # match ``pr_work`` by name, and the six ``pr_performance_*`` tables
        # match it through their foreign keys to ``pr_work_types``.
        "pr_work_scoring_rules",
        "pr_work_score_allocations",
        "pr_performance_policies",
        "pr_performance_reviews",
        "pr_performance_inputs",
        "pr_performance_results",
        "pr_performance_bonus_pools",
        "pr_performance_bonus_allocations",
        # 0036. M4B's three tables are the same shape of false positive one
        # revision further on: two of them match ``pr_work`` by name, and the
        # third matches it through its foreign key to ``pr_work_types``. This
        # database stands earlier, so all three are legitimately absent.
        "pr_work_recurring_templates",
        "pr_work_recurring_template_contributors",
        "pr_work_recurring_occurrences",
        # 0037. Two columns added to ``pr_work_items`` itself, so they match
        # ``pr_work`` by name rather than through a foreign key. This database
        # stands at 0032 and legitimately has neither.
        "execution_at",
        "recurring_occurrence_id",
        # 0039. Period containers: two columns and an index on ``pr_work_items``,
        # a results table keyed to it, a template flag and a score column. All
        # match ``pr_work`` by name; this database stands at 0032.
        "pr_work_results",
        "reporting_period_id",
        "subject_user_id",
        "uq_pr_work_items_period_container",
        "period_container_has_subject",
        "quantity_positive",
        "accumulate_by_period",
        "counted_amount",
    )
    work = [
        difference
        for difference in differences
        if "pr_work" in str(difference) and not any(name in str(difference) for name in later)
    ]
    assert work == [], work


# ---------------------------------------------------------------------------
# 2-3: the shape, from the live catalog
# ---------------------------------------------------------------------------


async def test_the_work_tables_have_the_columns_the_revision_promised(
    work_database: Database,
) -> None:
    async with work_database.session() as session:
        for table, expected in (
            (WORK_ITEMS, EXPECTED_ITEM_COLUMNS),
            (WORK_CONTRIBUTIONS, EXPECTED_CONTRIBUTION_COLUMNS),
        ):
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = :table"
                ),
                {"table": table},
            )
            assert {row[0] for row in result} == expected, table


async def test_every_foreign_key_restricts(work_database: Database) -> None:
    """``RESTRICT`` throughout, with **no exception**.

    The whole PR module's rule, and the Work Ledger keeps it: history is not
    something a delete may remove. ``pr_work_history.actor_user_id`` is
    deliberately not ``audit_logs``' ``SET NULL`` - a MeoBot user row is never
    hard-deleted, so nothing needs the escape hatch, and forgetting who moved a
    piece of work would lose the answer that table exists to give.
    """
    async with work_database.session() as session:
        result = await session.execute(
            text(
                "SELECT tc.table_name, kcu.column_name, rc.delete_rule "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.referential_constraints rc "
                "  ON tc.constraint_name = rc.constraint_name "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "  AND tc.table_name LIKE 'pr_work%'"
            )
        )
        rules = {(row[0], row[1]): row[2] for row in result}

    assert rules, "no foreign keys found - the query is wrong, not the schema"
    for (table, column), rule in rules.items():
        assert rule == "RESTRICT", (table, column, rule)


# ---------------------------------------------------------------------------
# 4: the partial unique index, both halves
# ---------------------------------------------------------------------------


async def test_the_source_index_is_partial(work_database: Database) -> None:
    """Read from the catalog: it must carry a ``WHERE``.

    A non-partial index would forbid a second manual row - because both would
    share a ``NULL`` key - and that would make the ordinary case impossible in
    order to protect the derived one.
    """
    async with work_database.session() as session:
        result = await session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": SOURCE_INDEX},
        )
        definition = result.scalar_one()
    assert "UNIQUE" in definition
    assert "WHERE (source_key IS NOT NULL)" in definition


async def test_two_derived_items_cannot_share_a_source_key(
    work_database: Database,
) -> None:
    """The idempotency guarantee, enforced by the database.

    A replayed projector, a retried Celery task and a re-run backfill all try to
    write the same key; exactly one row survives.
    """
    ids = await _existing_ids(work_database)
    key = f"content:{uuid.uuid4()}:SCRIPT_APPROVED"
    async with work_database.session() as session:
        await _insert_item(session, ids, code="WRK-2026-800001", source="CONTENT", key=key)
        await session.commit()

    async with work_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_item(session, ids, code="WRK-2026-800002", source="CONTENT", key=key)
            await session.commit()
        await session.rollback()


async def test_many_manual_items_coexist_on_a_null_key(work_database: Database) -> None:
    """The other half. Manual work has no key and must not be unique on one."""
    ids = await _existing_ids(work_database)
    async with work_database.session() as session:
        for index in range(3):
            await _insert_item(
                session, ids, code=f"WRK-2026-81000{index}", source="MANUAL", key=None
            )
        await session.commit()
        count = await session.scalar(
            text("SELECT count(*) FROM pr_work_items WHERE source_key IS NULL")
        )
    assert count is not None and count >= 3


# ---------------------------------------------------------------------------
# 5-6: the constraints that carry rules
# ---------------------------------------------------------------------------


async def test_a_person_cannot_be_credited_twice_in_one_role(
    work_database: Database,
) -> None:
    """``uq_pr_work_contributions_item_user_role``, from the database's side."""
    ids = await _existing_ids(work_database)
    item_id = uuid.uuid4()
    async with work_database.session() as session:
        await _insert_item(
            session, ids, code="WRK-2026-820001", source="MANUAL", key=None, item_id=item_id
        )
        await _insert_contribution(session, item_id, ids["user"])
        await session.commit()

    async with work_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_contribution(session, item_id, ids["user"])
            await session.commit()
        await session.rollback()


async def test_counted_status_and_counted_at_cannot_disagree(
    work_database: Database,
) -> None:
    """The check that refuses a half-write.

    Both directions: ``COUNTED`` with no time, and a time with no ``COUNTED``.
    Either would be a row a reporting period cannot reason about.
    """
    ids = await _existing_ids(work_database)
    item_id = uuid.uuid4()
    async with work_database.session() as session:
        await _insert_item(
            session, ids, code="WRK-2026-830001", source="MANUAL", key=None, item_id=item_id
        )
        await session.commit()

    # Both statements are written out rather than interpolated: a test that
    # builds SQL by concatenation is a test that teaches the pattern.
    columns = (
        "INSERT INTO pr_work_contributions "
        "(id, work_item_id, user_id, contribution_role, credit_weight, "
        " assigned_at, count_status, counted_at, created_at, updated_at) "
    )
    counted_with_no_time = (
        columns + "VALUES (:id, :item, :user, 'PRIMARY', 1.0, now(), 'COUNTED', NULL, now(), now())"
    )
    time_with_no_counted = (
        columns
        + "VALUES (:id, :item, :user, 'PRIMARY', 1.0, now(), 'PENDING', now(), now(), now())"
    )
    for statement in (counted_with_no_time, time_with_no_counted):
        async with work_database.session() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(statement),
                    {"id": uuid.uuid4(), "item": item_id, "user": ids["user"]},
                )
                await session.commit()
            await session.rollback()


async def test_a_credit_weight_above_one_is_refused_by_the_database(
    work_database: Database,
) -> None:
    """Nobody's share of a job is worth two people's, whoever writes the row."""
    ids = await _existing_ids(work_database)
    item_id = uuid.uuid4()
    async with work_database.session() as session:
        await _insert_item(
            session, ids, code="WRK-2026-840001", source="MANUAL", key=None, item_id=item_id
        )
        await session.commit()
    async with work_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_contribution(session, item_id, ids["user"], weight="2.0")
            await session.commit()
        await session.rollback()


# ---------------------------------------------------------------------------
# 7: nothing outside the ledger moved
# ---------------------------------------------------------------------------


async def test_the_pre_existing_rows_survive_the_migration_unchanged(
    work_database: Database,
) -> None:
    """M1's whole risk profile, asserted.

    The user, the channel, the content item and the task written at 0031 are
    still there, still saying the same thing.
    """
    after = await _fingerprint(work_database)
    assert after["users"][0] >= 1
    assert after["pr_channels"] == (1, "CH-9001")
    assert after["pr_content_items"] == (1, "IDEA")
    assert after["pr_tasks"] == (1, "TODO")


async def test_the_roundtrip_leaves_existing_data_alone(
    stopped_at_0031: tuple[Database, str], work_database: Database
) -> None:
    """0032 down to 0031 and up again. The ledger goes; nothing else does.

    Run last in the module, because it takes the schema down and back up under
    the other tests' feet.
    """
    _, dsn = stopped_at_0031
    before = await _fingerprint(work_database)

    await downgrade_to(dsn, "0031")
    async with work_database.session() as session:
        result = await session.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'pr_work%'")
        )
        assert result.scalar_one() == 0
    assert await _fingerprint(work_database) == before

    await upgrade_to(dsn, "0032")
    async with work_database.session() as session:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'pr_work%' ORDER BY 1"
            )
        )
        assert [row[0] for row in result] == sorted(WORK_TABLES)
    assert await _fingerprint(work_database) == before


# ---------------------------------------------------------------------------
# Raw-SQL helpers
# ---------------------------------------------------------------------------


async def _existing_ids(database: Database) -> dict[str, Any]:
    """The seeded ids, read back rather than remembered across fixtures."""
    async with database.session() as session:
        user = await session.scalar(text("SELECT id FROM users LIMIT 1"))
        work_type = await session.scalar(text("SELECT id FROM pr_work_types LIMIT 1"))
        if work_type is None:
            work_type = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO pr_work_types (id, code, name, category, default_unit, "
                    "requires_evidence, is_active, display_order, created_at, updated_at) "
                    "VALUES (:id, 'SHORT_SCRIPT', 'Kịch bản ngắn', 'CONTENT', 'ITEM', "
                    "false, true, 0, now(), now())"
                ),
                {"id": work_type},
            )
            await session.commit()
    return {"user": user, "work_type": work_type}


async def _insert_item(
    session: Any,
    ids: dict[str, Any],
    *,
    code: str,
    source: str,
    key: str | None,
    item_id: uuid.UUID | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO pr_work_items (id, code, title, work_type_id, source_type, "
            "source_key, status, priority, created_by_user_id, created_at, updated_at) "
            "VALUES (:id, :code, 'Việc thử', :type, :source, :key, 'ACCEPTED', 'NORMAL', "
            ":user, now(), now())"
        ),
        {
            "id": item_id or uuid.uuid4(),
            "code": code,
            "type": ids["work_type"],
            "source": source,
            "key": key,
            "user": ids["user"],
        },
    )


async def _insert_contribution(
    session: Any, item_id: uuid.UUID, user_id: Any, *, weight: str = "1.0"
) -> None:
    """One contribution. ``weight`` is **bound**, never interpolated.

    A test that builds SQL by concatenation is a test that teaches the pattern,
    even when every value in it is a literal the test itself chose.
    """
    await session.execute(
        text(
            "INSERT INTO pr_work_contributions (id, work_item_id, user_id, "
            "contribution_role, credit_weight, assigned_at, count_status, created_at, "
            "updated_at) "
            "VALUES (:id, :item, :user, 'PRIMARY', :weight, now(), 'PENDING', now(), now())"
        ),
        {"id": uuid.uuid4(), "item": item_id, "user": user_id, "weight": weight},
    )
