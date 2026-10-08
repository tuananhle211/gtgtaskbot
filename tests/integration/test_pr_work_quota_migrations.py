"""Migration 0033, on a database built the way production builds one.

Seven claims, and only PostgreSQL can settle them:

* **the three tables and their constraints are what the revision claims** - the
  columns, ``RESTRICT`` on every foreign key, and the checks that carry rules;

* **the plan indexes are partial.** At most one ``APPROVED`` plan and one
  ``DRAFT`` per employee and period, while superseded versions accumulate for
  ever. A non-partial unique would allow the first and forbid the second, which
  would make the version chain - the whole point of the table - impossible;

* **an ambiguous quota match is unrepresentable.** Two quotas for one work type
  in one plan version is refused by the database, not only by a validator;

* **one current decision per contribution.** What makes running reconciliation
  twice harmless rather than additive;

* **the anti-gaming constraint holds.** A ``NO_QUOTA`` allocation with a
  non-zero ``eligible_amount`` cannot be inserted, so *"missing quota means
  unlimited"* is impossible in the schema and not only in the evaluator;

* **the semantic constraints hold.** ``PENDING_EVALUATION`` cannot be stored at
  all - it describes the absence of a row; ``UNMEASURABLE`` cannot exist without
  a reason code and no other status may carry one; and each decided status is
  pinned to the split it means, so an ``ELIGIBLE`` row cannot quietly carry an
  over-quota amount;

* **0032's data is untouched.** A user, a work type, a work item and a
  **counted contribution** written before 0033 come through the upgrade, back
  down again and up again unchanged - the same ``count_status``, the same
  ``counted_at``. That is M2's whole risk profile: the quota engine is
  additive, and a mistake in it cannot reach the ledger the department's KPI
  already depends on;

* **the downgrade loses exactly what it says it loses** - the three tables and
  the one added column, and nothing else.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_quota_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_quota_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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

WORK_PLANS = "pr_work_plans"
WORK_QUOTAS = "pr_work_quotas"
ALLOCATIONS = "pr_work_quota_allocations"
QUOTA_TABLES = (WORK_PLANS, WORK_QUOTAS, ALLOCATIONS)

APPROVED_INDEX = "uq_pr_work_plans_approved"
DRAFT_INDEX = "uq_pr_work_plans_draft"
ALLOCATION_INDEX = "uq_pr_work_quota_allocations_contribution"

#: The columns 0033 is responsible for. Written out here rather than derived
#: from the models, so deleting one from a model does not quietly delete the
#: assertion that it exists.
EXPECTED_PLAN_COLUMNS = frozenset(
    {
        "id",
        "user_id",
        "period_id",
        "version_no",
        "status",
        "supersedes_plan_id",
        "note",
        "created_by_user_id",
        "approved_by_user_id",
        "approved_at",
        "superseded_at",
        "discarded_at",
        "discarded_by_user_id",
        "created_at",
        "updated_at",
    }
)

EXPECTED_QUOTA_COLUMNS = frozenset(
    {
        "id",
        "plan_id",
        "work_type_id",
        "basis",
        "target_value",
        "eligibility_cap",
        "unit",
        "note",
        "created_at",
        "updated_at",
    }
)

EXPECTED_ALLOCATION_COLUMNS = frozenset(
    {
        "id",
        "work_contribution_id",
        "reporting_period_id",
        "user_id",
        "work_type_id",
        "work_plan_id",
        "work_quota_id",
        "quota_status",
        "basis",
        "unit",
        "basis_amount",
        "eligible_amount",
        "over_quota_amount",
        "reason_code",
        "evaluated_at",
        "created_at",
        "updated_at",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0032() -> AsyncIterator[tuple[Database, str]]:
    """A database at **0032**, with M1 rows the quota engine must not touch.

    One revision short of head, so a work item and a **counted** contribution
    exist before the quota tables do - which is the only way to prove 0033
    walked past them.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_quota_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0032")

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


async def _seed_pre_0033(database: Database) -> None:
    """One user, one work type, one work item and one **counted** contribution.

    Counted on purpose: the row M2's whole "no backfill" promise is about. It
    must come through the upgrade with the same ``count_status`` and the same
    ``counted_at``, and with **no allocation** invented for it.
    """
    async with database.session() as session:
        user_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO users (id, full_name, role, status, active, created_at, "
                "updated_at) VALUES (:id, 'Phương Nhung', 'employee', 'active', true, "
                "now(), now())"
            ),
            {"id": user_id},
        )
        type_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO pr_work_types (id, code, name, category, default_unit, "
                "requires_evidence, is_active, display_order, created_at, updated_at) "
                "VALUES (:id, 'SEEDING_COMMENT', 'Seeding', 'COMMUNITY', 'COMMENT', "
                "false, true, 0, now(), now())"
            ),
            {"id": type_id},
        )
        item_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO pr_work_items (id, code, title, work_type_id, source_type, "
                "status, priority, quantity, unit, created_by_user_id, approved_at, "
                "created_at, updated_at) "
                "VALUES (:id, 'WRK-2026-900001', 'Seeding 100', :type, 'MANUAL', "
                "'APPROVED', 'NORMAL', 100, 'COMMENT', :user, :when, now(), now())"
            ),
            {"id": item_id, "type": type_id, "user": user_id, "when": _COUNTED_AT},
        )
        await session.execute(
            text(
                "INSERT INTO pr_work_contributions (id, work_item_id, user_id, "
                "contribution_role, credit_weight, assigned_at, count_status, counted_at, "
                "created_at, updated_at) "
                "VALUES (:id, :item, :user, 'PRIMARY', 1.0, now(), 'COUNTED', :when, "
                "now(), now())"
            ),
            {
                "id": uuid.uuid4(),
                "item": item_id,
                "user": user_id,
                "when": _COUNTED_AT,
            },
        )
        period_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO pr_reporting_periods (id, code, period_type, date_start, "
                "date_end, status, created_at, updated_at) "
                "VALUES (:id, '2026-09', 'MONTH', DATE '2026-09-01', DATE '2026-09-30', "
                "'OPEN', now(), now())"
            ),
            {"id": period_id},
        )
        await session.commit()


#: A fixed instant, so "the counted timestamp did not move" is an equality
#: rather than a comparison against whatever ``now()`` happened to be.
_COUNTED_AT = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)


#: What the quota tables held **immediately after the upgrade**, before any test
#: in this module wrote a row.
#:
#: Captured in the fixture rather than asserted in place, because the tests below
#: share one module-scoped database and several of them insert plans and
#: allocations on purpose. "The migration backfilled nothing" is a claim about
#: the instant the migration finished, and reading it later would be reading the
#: suite's own writes back.
_AFTER_UPGRADE: dict[str, Any] = {}


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def quota_database(stopped_at_0032: tuple[Database, str]) -> Database:
    """The same database, taken to **0033**, with 0032's rows already in it."""
    database, dsn = stopped_at_0032
    await _seed_pre_0033(database)
    await upgrade_to(dsn, "0033")
    async with database.session() as session:
        for table in ("pr_work_quota_allocations", "pr_work_plans", "pr_work_quotas"):
            _AFTER_UPGRADE[table] = await session.scalar(
                text(f"SELECT count(*) FROM {table}")  # noqa: S608 - a literal from the tuple above
            )
    return database


# ---------------------------------------------------------------------------
# 1: the models and the migration agree
# ---------------------------------------------------------------------------


async def test_the_models_and_the_migration_describe_the_same_quota_schema(
    quota_database: Database,
) -> None:
    """Alembic's own comparator, restricted to the quota tables and work types.

    Restricted for the reason M1's equivalent restricts itself: this repository
    has pre-existing drift between older models and older migrations, and
    widening the assertion would make it fail for reasons M2 did not cause and
    must not fix. ``pr_work_types`` is inside the net because 0033 adds a column
    to it.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from meobot.db.base import Base

    def _compare(connection: Any) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with quota_database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    # ``pr_work_types`` is matched because 0033 adds a column to it - and that
    # same substring is why M3's two tables have to be excluded again here. This
    # database stands at 0033, so ``pr_content_work_rules`` and
    # ``pr_content_work_projections`` are legitimately absent; both carry a
    # foreign key to ``pr_work_types``, so both appear in the match above. 0034's
    # own suite asserts them against a 0034 database.
    later = (
        # 0042. The Ads order engine's KPI mapping, found through its foreign
        # key to ``pr_work_types``.
        "order_work_rules",
        # 0034
        "pr_content_work_rules",
        "pr_content_work_projections",
        # 0035. M6's tables match the same substrings - two by name, the rest
        # through their foreign keys to ``pr_work_types``. This database stands
        # at 0033, so all of them are legitimately absent.
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
        # 0038. KPI self-service adds five columns and two check constraints
        # to ``pr_work_plans`` itself, so they match the first substring by
        # name. This database stands at 0033 and they are legitimately absent;
        # ``test_pr_kpi_self_service_pg`` asserts them against a 0038 database.
        "submitted_at",
        "submitted_by",
        "returned_at",
        "returned_by",
        "return_note",
    )
    ours = [
        difference
        for difference in differences
        if (
            "pr_work_plan" in str(difference)
            or "pr_work_quota" in str(difference)
            or "pr_work_types" in str(difference)
        )
        and not any(name in str(difference) for name in later)
    ]
    assert ours == [], ours


# ---------------------------------------------------------------------------
# 2-3: the shape, from the live catalog
# ---------------------------------------------------------------------------


async def test_the_quota_tables_have_the_columns_the_revision_promised(
    quota_database: Database,
) -> None:
    async with quota_database.session() as session:
        for table, expected in (
            (WORK_PLANS, EXPECTED_PLAN_COLUMNS),
            (WORK_QUOTAS, EXPECTED_QUOTA_COLUMNS),
            (ALLOCATIONS, EXPECTED_ALLOCATION_COLUMNS),
        ):
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = :table"
                ),
                {"table": table},
            )
            assert {row[0] for row in result} == expected, table


async def test_the_work_type_gained_exactly_one_column(quota_database: Database) -> None:
    """One column on one M1 table, ``NOT NULL`` with a server default.

    The default is what lets the column arrive without an ``UPDATE`` this
    revision writes, and ``ITEM_COUNT`` is what M1's figures already implied:
    nothing was measured by quantity, because nothing was measured against a
    quota at all.
    """
    async with quota_database.session() as session:
        result = await session.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'pr_work_types' AND column_name = 'default_quota_basis'"
            )
        )
        nullable, default = result.one()
    assert nullable == "NO"
    assert "ITEM_COUNT" in default
    async with quota_database.session() as session:
        seeded = await session.scalar(
            text("SELECT default_quota_basis FROM pr_work_types WHERE code = 'SEEDING_COMMENT'")
        )
    assert seeded == "ITEM_COUNT", "existing types are not guessed at from their unit"


async def test_every_quota_foreign_key_restricts(quota_database: Database) -> None:
    """``RESTRICT`` throughout, with **no exception**.

    The PR module's rule kept: a plan that decided something cannot be deleted,
    and neither can the period it was written for. An allocation's provenance is
    not something a delete may remove.
    """
    async with quota_database.session() as session:
        result = await session.execute(
            text(
                "SELECT tc.table_name, kcu.column_name, rc.delete_rule "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.referential_constraints rc "
                "  ON tc.constraint_name = rc.constraint_name "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "  AND tc.table_name IN ('pr_work_plans', 'pr_work_quotas', "
                "                        'pr_work_quota_allocations')"
            )
        )
        rules = {(row[0], row[1]): row[2] for row in result}

    assert rules, "no foreign keys found - the query is wrong, not the schema"
    for (table, column), rule in rules.items():
        assert rule == "RESTRICT", (table, column, rule)


# ---------------------------------------------------------------------------
# 4: the partial unique indexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "predicate"),
    [(APPROVED_INDEX, "APPROVED"), (DRAFT_INDEX, "DRAFT")],
)
async def test_the_plan_indexes_are_partial(
    quota_database: Database, index: str, predicate: str
) -> None:
    """Read from the catalog: each must carry a ``WHERE``.

    A non-partial unique on ``(user_id, period_id)`` would allow one plan in
    force and forbid every superseded version behind it - which would make the
    version chain, the whole point of the table, impossible.
    """
    async with quota_database.session() as session:
        definition = await session.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": index}
        )
    assert definition is not None, index
    assert "UNIQUE" in definition
    assert f"WHERE ((status)::text = '{predicate}'::text)" in definition, definition


async def test_only_one_plan_can_be_in_force(quota_database: Database) -> None:
    """Two administrators approving two drafts in the same instant.

    Exactly the race an application check loses and a partial unique index wins,
    and the reason the index exists rather than only a service rule.
    """
    ids = await _seed_ids(quota_database)
    async with quota_database.session() as session:
        await _insert_plan(session, ids, version=1, status="APPROVED")
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_plan(session, ids, version=2, status="APPROVED")
            await session.commit()
        await session.rollback()
    # And a superseded version alongside it is fine - which is what a
    # non-partial index would have forbidden.
    async with quota_database.session() as session:
        await _insert_plan(session, ids, version=3, status="SUPERSEDED")
        await _insert_plan(session, ids, version=4, status="SUPERSEDED")
        await session.commit()


async def test_only_one_draft_revision_can_be_in_flight(quota_database: Database) -> None:
    """Two people each writing a different next version of one plan.

    Surfaced while it is still cheap: "somebody is already revising this" is a
    better refusal than two plans racing to be approved.
    """
    ids = await _seed_ids(quota_database, code="2026-11")
    async with quota_database.session() as session:
        await _insert_plan(session, ids, version=1, status="DRAFT")
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_plan(session, ids, version=2, status="DRAFT")
            await session.commit()
        await session.rollback()


# ---------------------------------------------------------------------------
# 5: the constraints that carry rules
# ---------------------------------------------------------------------------


async def test_two_quotas_for_one_work_type_are_refused_by_the_database(
    quota_database: Database,
) -> None:
    """An ambiguous quota match is unrepresentable, not merely validated against."""
    ids = await _seed_ids(quota_database, code="2026-12")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="DRAFT")
        await _insert_quota(session, plan_id, ids["work_type"])
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_quota(session, plan_id, ids["work_type"])
            await session.commit()
        await session.rollback()


async def test_a_cap_below_its_target_is_refused_by_the_database(
    quota_database: Database,
) -> None:
    """A plan asking for more work than it would call eligible."""
    ids = await _seed_ids(quota_database, code="2027-01")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="DRAFT")
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_quota(session, plan_id, ids["work_type"], target="20", cap="10")
            await session.commit()
        await session.rollback()


async def test_one_allocation_per_contribution(quota_database: Database) -> None:
    """What makes running reconciliation twice harmless rather than additive."""
    ids = await _seed_ids(quota_database, code="2027-02")
    async with quota_database.session() as session:
        await _insert_allocation(session, ids)
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(session, ids)
            await session.commit()
        await session.rollback()


async def test_no_quota_work_can_never_be_eligible(quota_database: Database) -> None:
    """**The anti-gaming constraint**, said by the database.

    Missing quota is never unlimited eligibility, independently of the
    evaluator: a ``NO_QUOTA`` row with a non-zero ``eligible_amount`` cannot be
    inserted at all.
    """
    ids = await _seed_ids(quota_database, code="2027-03")
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(session, ids, eligible="5.00")
            await session.commit()
        await session.rollback()


async def test_a_decided_allocation_must_reconcile(quota_database: Database) -> None:
    """Every unit of quota-decided work is on exactly one side of the cap.

    A row where the parts do not add up to the whole is a row a report would
    quietly get wrong.
    """
    ids = await _seed_ids(quota_database, code="2027-04")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="APPROVED")
        quota_id = await _insert_quota(session, plan_id, ids["work_type"])
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(
                session,
                ids,
                status="ELIGIBLE",
                plan_id=plan_id,
                quota_id=quota_id,
                basis="10.00",
                eligible="3.00",
                over="3.00",
            )
            await session.commit()
        await session.rollback()


async def test_a_decided_allocation_must_name_its_quota(quota_database: Database) -> None:
    """Provenance is not optional for anything an approved quota decided.

    ``NO_QUOTA`` names none, and every other status names one - which is what
    lets "why was this eligible yesterday" be answered from the row.
    """
    ids = await _seed_ids(quota_database, code="2027-05")
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(
                session, ids, status="ELIGIBLE", basis="1.00", eligible="1.00", over="0.00"
            )
            await session.commit()
        await session.rollback()


# ---------------------------------------------------------------------------
# 5b: the semantic constraints this patch added
# ---------------------------------------------------------------------------


async def test_pending_evaluation_can_never_be_stored(quota_database: Database) -> None:
    """It describes the **absence** of an allocation.

    A row holding it would contradict itself, and would be indistinguishable
    from a real decision to every reader that trusts the column. The value
    exists so a *read* can say "an approved quota covers this and nothing has
    evaluated it yet"; the database is what stops it becoming a stored claim.
    """
    ids = await _seed_ids(quota_database, code="2027-06")
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(session, ids, status="PENDING_EVALUATION")
            await session.commit()
        await session.rollback()


async def test_unmeasurable_must_say_what_is_missing(quota_database: Database) -> None:
    """A reason belongs to exactly one status, in both directions.

    No row may claim *"cannot measure"* without naming the field somebody has to
    fix - and a stale reason cannot survive a recompute that resolved it.
    """
    ids = await _seed_ids(quota_database, code="2027-07")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="APPROVED")
        quota_id = await _insert_quota(session, plan_id, ids["work_type"])
        await session.commit()

    # UNMEASURABLE with no reason.
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(
                session,
                ids,
                status="UNMEASURABLE",
                plan_id=plan_id,
                quota_id=quota_id,
                basis=None,
            )
            await session.commit()
        await session.rollback()

    # A reason on a status that is not UNMEASURABLE.
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(session, ids, status="NO_QUOTA", reason="MISSING_QUANTITY")
            await session.commit()
        await session.rollback()

    # And the shape that **is** legal: a quota named, no amount, a reason. On its
    # own contribution, because ``uq_..._contribution`` allows one decision per
    # contribution and an earlier test in this module has already claimed the
    # seeded one - which is that index doing its job.
    async with quota_database.session() as session:
        ids = {**ids, "contribution": await _fresh_contribution(session, ids)}
        await _insert_allocation(
            session,
            ids,
            status="UNMEASURABLE",
            plan_id=plan_id,
            quota_id=quota_id,
            basis=None,
            reason="MISSING_QUANTITY",
        )
        await session.commit()


async def test_unmeasurable_work_can_never_be_eligible(quota_database: Database) -> None:
    """The anti-gaming constraint's twin.

    A contribution nobody could measure must not be eligible for an amount
    nobody computed - the same failure as "missing quota means unlimited", one
    step along.
    """
    ids = await _seed_ids(quota_database, code="2027-08")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="APPROVED")
        quota_id = await _insert_quota(session, plan_id, ids["work_type"])
        await session.commit()
    async with quota_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_allocation(
                session,
                ids,
                status="UNMEASURABLE",
                plan_id=plan_id,
                quota_id=quota_id,
                basis="5.00",
                eligible="5.00",
                reason="MISSING_QUANTITY",
            )
            await session.commit()
        await session.rollback()


async def test_a_decided_row_must_carry_a_positive_amount(quota_database: Database) -> None:
    """Measured work has a number, and the number is above zero.

    A contribution worth ``0.00`` is a measurement that failed, not "eligible for
    nothing" - and at zero the ``ELIGIBLE`` and ``OVER_QUOTA`` rules would both
    hold, which would make the two statuses indistinguishable.
    """
    ids = await _seed_ids(quota_database, code="2027-09")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="APPROVED")
        quota_id = await _insert_quota(session, plan_id, ids["work_type"])
        await session.commit()

    for basis, eligible, over in ((None, "0.00", "0.00"), ("0.00", "0.00", "0.00")):
        async with quota_database.session() as session:
            with pytest.raises(IntegrityError):
                await _insert_allocation(
                    session,
                    ids,
                    status="ELIGIBLE",
                    plan_id=plan_id,
                    quota_id=quota_id,
                    basis=basis,
                    eligible=eligible,
                    over=over,
                )
                await session.commit()
            await session.rollback()


async def test_each_decided_status_is_pinned_to_its_split(quota_database: Database) -> None:
    """``ELIGIBLE`` is wholly inside, ``OVER_QUOTA`` wholly outside, and
    ``PARTIALLY_ELIGIBLE`` on both sides.

    What stops an ``ELIGIBLE`` row quietly carrying an over-quota amount - a row
    no screen would question and every total would be wrong by.
    """
    ids = await _seed_ids(quota_database, code="2027-10")
    async with quota_database.session() as session:
        plan_id = await _insert_plan(session, ids, version=1, status="APPROVED")
        quota_id = await _insert_quota(session, plan_id, ids["work_type"])
        await session.commit()

    for status, basis, eligible, over in (
        ("ELIGIBLE", "5.00", "4.00", "1.00"),
        ("OVER_QUOTA", "5.00", "1.00", "4.00"),
        ("PARTIALLY_ELIGIBLE", "5.00", "5.00", "0.00"),
    ):
        async with quota_database.session() as session:
            with pytest.raises(IntegrityError):
                await _insert_allocation(
                    session,
                    ids,
                    status=status,
                    plan_id=plan_id,
                    quota_id=quota_id,
                    basis=basis,
                    eligible=eligible,
                    over=over,
                )
                await session.commit()
            await session.rollback()


# ---------------------------------------------------------------------------
# 6-7: nothing outside the quota engine moved
# ---------------------------------------------------------------------------


async def test_the_counted_contribution_survives_the_migration_unchanged(
    quota_database: Database,
) -> None:
    """**M2's whole risk profile, asserted.**

    The counted contribution written at 0032 still says ``COUNTED`` at the same
    instant, and no allocation was invented for it. Both halves matter: the
    first is "no M1 row was mutated", and the second is "no backfill wrote a
    KPI decision nobody asked for".
    """
    async with quota_database.session() as session:
        status, counted_at = (
            await session.execute(
                text("SELECT count_status, counted_at FROM pr_work_contributions LIMIT 1")
            )
        ).one()
    assert status == "COUNTED"
    assert counted_at == _COUNTED_AT
    # Read from the snapshot the fixture took the instant the upgrade finished -
    # see ``_AFTER_UPGRADE``. The suite deliberately inserts plans and
    # allocations further down, and counting them here would be counting its own
    # writes rather than the migration's.
    assert _AFTER_UPGRADE["pr_work_quota_allocations"] == 0, "the migration backfilled nothing"
    assert _AFTER_UPGRADE["pr_work_plans"] == 0
    assert _AFTER_UPGRADE["pr_work_quotas"] == 0


async def test_the_roundtrip_leaves_the_ledger_alone(
    stopped_at_0032: tuple[Database, str], quota_database: Database
) -> None:
    """0033 down to 0032 and up again. The quota engine goes; nothing else does.

    Run last in the module, because it takes the schema down and back up under
    the other tests' feet.
    """
    _, dsn = stopped_at_0032
    before = await _fingerprint(quota_database)

    await downgrade_to(dsn, "0032")
    async with quota_database.session() as session:
        remaining = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('pr_work_plans', 'pr_work_quotas', "
                "                     'pr_work_quota_allocations')"
            )
        )
        assert remaining == 0
        column = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'pr_work_types' AND column_name = 'default_quota_basis'"
            )
        )
        assert column == 0
    assert await _fingerprint(quota_database) == before

    await upgrade_to(dsn, "0033")
    async with quota_database.session() as session:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('pr_work_plans', 'pr_work_quotas', "
                "                     'pr_work_quota_allocations') ORDER BY 1"
            )
        )
        assert [row[0] for row in result] == sorted(QUOTA_TABLES)
    assert await _fingerprint(quota_database) == before


# ---------------------------------------------------------------------------
# Raw-SQL helpers
# ---------------------------------------------------------------------------


async def _fingerprint(database: Database) -> dict[str, Any]:
    """What must not move: the ledger row that a KPI reads.

    ``counted_at`` and ``count_status`` are the two fields M2 promises never to
    write, so they are the fingerprint rather than a row count.
    """
    async with database.session() as session:
        contribution = (
            await session.execute(
                text(
                    "SELECT count_status, counted_at, credit_weight FROM "
                    "pr_work_contributions ORDER BY created_at LIMIT 1"
                )
            )
        ).one()
        item = (
            await session.execute(
                text(
                    "SELECT code, status, quantity, unit FROM pr_work_items "
                    "ORDER BY created_at LIMIT 1"
                )
            )
        ).one()
    return {"contribution": tuple(contribution), "item": tuple(item)}


async def _seed_ids(database: Database, *, code: str = "2026-10") -> dict[str, Any]:
    """The ids the raw-SQL helpers below need, plus a period of their own.

    A fresh period per test, so one test's approved plan cannot make another's
    partial unique index fire for the wrong reason.
    """
    async with database.session() as session:
        user = await session.scalar(text("SELECT id FROM users LIMIT 1"))
        work_type = await session.scalar(text("SELECT id FROM pr_work_types LIMIT 1"))
        contribution = await session.scalar(text("SELECT id FROM pr_work_contributions LIMIT 1"))
        period = await session.scalar(
            text("SELECT id FROM pr_reporting_periods WHERE code = :code"), {"code": code}
        )
        if period is None:
            period = uuid.uuid4()
            year, month = code.split("-")
            await session.execute(
                text(
                    "INSERT INTO pr_reporting_periods (id, code, period_type, date_start, "
                    "date_end, status, created_at, updated_at) "
                    "VALUES (:id, :code, 'MONTH', make_date(:y, :m, 1), "
                    "make_date(:y, :m, 28), 'OPEN', now(), now())"
                ),
                {"id": period, "code": code, "y": int(year), "m": int(month)},
            )
            await session.commit()
    return {
        "user": user,
        "work_type": work_type,
        "contribution": contribution,
        "period": period,
    }


#: A fixed instant for the plan timestamps the raw-SQL helpers write. Any value
#: satisfies the CHECK constraints; a constant keeps the statements free of
#: ``now()`` inside a ``CASE``.
_APPROVED_AT = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)


async def _insert_plan(
    session: Any, ids: dict[str, Any], *, version: int, status: str
) -> uuid.UUID:
    """One plan row. Every value **bound**, never interpolated.

    The approval and supersede timestamps are decided in Python rather than by a
    ``CASE`` in the statement: asyncpg deduces a parameter's type from how it is
    used, and one placeholder appearing both as a ``uuid`` column value and
    inside a ``CASE`` predicate is a parameter with two deduced types.
    """
    plan_id = uuid.uuid4()
    approved = status in {"APPROVED", "SUPERSEDED"}
    await session.execute(
        text(
            "INSERT INTO pr_work_plans (id, user_id, period_id, version_no, status, "
            "created_by_user_id, approved_by_user_id, approved_at, superseded_at, "
            "created_at, updated_at) "
            "VALUES (:id, :user, :period, :version, :status, :creator, :approver, "
            ":approved_at, :superseded_at, now(), now())"
        ),
        {
            "id": plan_id,
            "user": ids["user"],
            "period": ids["period"],
            "version": version,
            "status": status,
            "creator": ids["user"],
            "approver": ids["user"] if approved else None,
            "approved_at": _APPROVED_AT if approved else None,
            "superseded_at": _APPROVED_AT if status == "SUPERSEDED" else None,
        },
    )
    return plan_id


async def _insert_quota(
    session: Any,
    plan_id: uuid.UUID,
    work_type_id: Any,
    *,
    target: str = "20",
    cap: str = "25",
) -> uuid.UUID:
    quota_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO pr_work_quotas (id, plan_id, work_type_id, basis, target_value, "
            "eligibility_cap, unit, created_at, updated_at) "
            "VALUES (:id, :plan, :type, 'ITEM_COUNT', :target, :cap, NULL, now(), now())"
        ),
        {
            "id": quota_id,
            "plan": plan_id,
            "type": work_type_id,
            "target": target,
            "cap": cap,
        },
    )
    return quota_id


async def _fresh_contribution(session: Any, ids: dict[str, Any]) -> uuid.UUID:
    """One more counted contribution, on a work item of its own.

    For the tests that need to insert a *legal* allocation: the seeded
    contribution can only carry one, which is the whole point of
    ``uq_pr_work_quota_allocations_contribution``.
    """
    item_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO pr_work_items (id, code, title, work_type_id, source_type, "
            "status, priority, created_by_user_id, approved_at, created_at, updated_at) "
            "VALUES (:id, :code, 'Việc thêm', :type, 'MANUAL', 'APPROVED', 'NORMAL', "
            ":user, :when, now(), now())"
        ),
        {
            "id": item_id,
            "code": f"WRK-2026-{uuid.uuid4().hex[:6]}",
            "type": ids["work_type"],
            "user": ids["user"],
            "when": _COUNTED_AT,
        },
    )
    contribution_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO pr_work_contributions (id, work_item_id, user_id, "
            "contribution_role, credit_weight, assigned_at, count_status, counted_at, "
            "created_at, updated_at) VALUES (:id, :item, :user, 'PRIMARY', 1.0, :when, "
            "'COUNTED', :when, :when, :when)"
        ),
        {"id": contribution_id, "item": item_id, "user": ids["user"], "when": _COUNTED_AT},
    )
    return contribution_id


async def _insert_allocation(
    session: Any,
    ids: dict[str, Any],
    *,
    status: str = "NO_QUOTA",
    plan_id: uuid.UUID | None = None,
    quota_id: uuid.UUID | None = None,
    basis: str | None = "1.00",
    eligible: str = "0.00",
    over: str = "0.00",
    reason: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO pr_work_quota_allocations (id, work_contribution_id, "
            "reporting_period_id, user_id, work_type_id, work_plan_id, work_quota_id, "
            "quota_status, basis, unit, basis_amount, eligible_amount, over_quota_amount, "
            "reason_code, evaluated_at, created_at, updated_at) "
            "VALUES (:id, :contribution, :period, :user, :type, :plan, :quota, :status, "
            "'ITEM_COUNT', NULL, :basis, :eligible, :over, :reason, now(), now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "contribution": ids["contribution"],
            "period": ids["period"],
            "user": ids["user"],
            "type": ids["work_type"],
            "plan": plan_id,
            "quota": quota_id,
            "status": status,
            "basis": basis,
            "eligible": eligible,
            "over": over,
            "reason": reason,
        },
    )
