"""Migration 0034, on a database built the way production builds one.

Five claims, and only PostgreSQL can settle them:

* **the two tables are what the revision claims** - the columns, ``RESTRICT`` on
  every foreign key, and the check that keeps ``attempts`` sane;

* **the mapping indexes are partial, and there are two of them.** One plain
  unique on ``(contribution_kind, content_type)`` would look sufficient and
  would not be: PostgreSQL treats every ``NULL`` as distinct, so it would accept
  five competing default rules for one kind and the projector would pick
  whichever came back first. Both halves are asserted - a duplicate typed rule
  is refused, and so is a second default;

* **one projection row per content item.** What makes "request projection"
  idempotent, and what a content item that moves five times collapses onto;

* **0033's data is untouched.** A user, a work type, a content item and a
  **counted contribution** written before 0034 come through the upgrade, back
  down again and up again unchanged - the same ``count_status``, the same
  ``counted_at``, and no work item invented for the content. That is M3's whole
  risk profile: the projector is additive, and a mistake in it cannot reach the
  ledger the department's KPI already depends on;

* **the downgrade loses exactly what it says it loses** - the two tables, and
  nothing else.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_content_work_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_cwork_*`` database and
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

RULES = "pr_content_work_rules"
PROJECTIONS = "pr_content_work_projections"
M3_TABLES = (PROJECTIONS, RULES)

#: The columns 0034 is responsible for. Written out here rather than derived
#: from the models, so deleting one from a model does not quietly delete the
#: assertion that it exists.
EXPECTED_RULE_COLUMNS = frozenset(
    {
        "id",
        "contribution_kind",
        "content_type",
        "work_type_id",
        "is_active",
        "note",
        "created_by_user_id",
        "created_at",
        "updated_at",
    }
)

EXPECTED_PROJECTION_COLUMNS = frozenset(
    {
        "id",
        "content_id",
        "status",
        "requested_at",
        "claimed_at",
        "settled_at",
        "attempts",
        "last_outcome",
        "last_error_code",
        "created_at",
        "updated_at",
    }
)

#: A fixed instant, so "the counted timestamp did not move" is an equality
#: rather than a comparison against whatever ``now()`` happened to be.
_COUNTED_AT = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)

#: What the M3 tables held immediately after the upgrade, before any test wrote
#: a row. See ``test_pr_work_quota_migrations`` for why this is a fixture
#: snapshot rather than an assertion in place.
_AFTER_UPGRADE: dict[str, Any] = {}


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0033() -> AsyncIterator[tuple[Database, str]]:
    """A database at **0033**, with M1/M2 rows the projector must not touch."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_cwork_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0033")
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


async def _seed_pre_0034(database: Database) -> None:
    """One user, a work type, a brand, a content item and a counted contribution.

    Written through the **ORM**, not hand-rolled SQL, for every table whose
    mapper still describes it at 0033: 0034 does not touch them, and spelling
    their columns out by hand would mean this file quietly breaking every time
    an unrelated milestone adds a ``NOT NULL``.

    ``pr_work_items`` is the exception, and it stopped being writable this way
    the moment ``0037`` added ``execution_at`` to it - today's mapper emits a
    column a 0033 database does not have. So that one row is inserted with
    explicit SQL naming only the columns 0033 actually holds, which is what the
    fixture needs it to be: **a row as it existed before 0034**, not a row as
    today's model would write one. The reasoning above still holds for the rest.

    Counted on purpose: this is the row M3's "no backfill" promise is about. It
    must come through the upgrade with the same ``count_status`` and the same
    ``counted_at``, and with **no work item invented** for the content beside it.
    """
    from meobot.db.models.pr import PrBrand, PrContentItem
    from meobot.db.models.pr_work import PrWorkContribution, PrWorkType
    from meobot.db.models.user import User
    from meobot.domain.access.models import UserStatus
    from meobot.domain.identity.models import Role
    from meobot.domain.pr.models import PrWorkflowStage
    from meobot.domain.pr.work import (
        PrWorkContributionRole,
        PrWorkCountStatus,
        PrWorkUnit,
    )
    from meobot.domain.pr.work_quota import PrWorkQuotaBasis

    async with database.session() as session:
        user = User(
            full_name="Phương Nhung",
            role=Role.EMPLOYEE,
            status=UserStatus.ACTIVE,
            active=True,
        )
        brand = PrBrand(code="BRND-A", name="Apexmed")
        work_type = PrWorkType(
            code="SHORT_SCRIPT",
            name="Kịch bản ngắn",
            category="CONTENT",
            default_unit=PrWorkUnit.ITEM,
            default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
        session.add_all([user, brand, work_type])
        await session.flush()

        content = PrContentItem(
            code="CNT-2026-000001",
            title="Bí quyết ngủ ngon",
            brand_id=brand.id,
            workflow_stage=PrWorkflowStage.APPROVED,
            owner_user_id=user.id,
            created_by_user_id=user.id,
        )
        session.add(content)
        await session.flush()

        # See the docstring: 0033's ``pr_work_items``, named column by column.
        item_id = await session.scalar(
            text(
                "INSERT INTO pr_work_items "
                "(id, code, title, work_type_id, source_type, status, priority, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'WRK-2026-000001', 'Việc nhập tay', "
                " :work_type_id, 'MANUAL', 'APPROVED', 'NORMAL', :user_id, now(), now()) "
                "RETURNING id"
            ),
            {"work_type_id": work_type.id, "user_id": user.id},
        )

        session.add(
            PrWorkContribution(
                work_item_id=item_id,
                user_id=user.id,
                contribution_role=PrWorkContributionRole.PRIMARY,
                assigned_at=_COUNTED_AT,
                count_status=PrWorkCountStatus.COUNTED,
                counted_at=_COUNTED_AT,
            )
        )
        await session.commit()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def m3_database(stopped_at_0033: tuple[Database, str]) -> Database:
    """The same database, taken to **0034**, with 0033's rows already in it."""
    database, dsn = stopped_at_0033
    await _seed_pre_0034(database)
    await upgrade_to(dsn, "0034")
    async with database.session() as session:
        for table in (RULES, PROJECTIONS):
            _AFTER_UPGRADE[table] = await session.scalar(
                text(f"SELECT count(*) FROM {table}")  # noqa: S608 - a literal from the tuple
            )
        _AFTER_UPGRADE["source_work"] = await session.scalar(
            text("SELECT count(*) FROM pr_work_items WHERE source_type = 'CONTENT'")
        )
    return database


# ---------------------------------------------------------------------------
# 1: the models and the migration agree
# ---------------------------------------------------------------------------


async def test_the_models_and_the_migration_describe_the_same_schema(
    m3_database: Database,
) -> None:
    """Alembic's own comparator, restricted to the two new tables.

    Restricted for the reason every other migration suite here restricts itself:
    this repository has pre-existing drift between older models and older
    migrations, and widening the assertion would make it fail for reasons M3 did
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

    async with m3_database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    ours = [one for one in differences if "pr_content_work" in str(one)]
    # This database is pinned at ``0034``. ``0040`` later relaxed
    # ``created_by_user_id`` to nullable - the auto-provisioning provenance
    # marker - so the models are *expected* to differ from ``0034`` in exactly
    # that one way, and nothing else.
    later = [one for one in ours if _is_0040_nullable_change(one)]
    assert len(later) == 1, ours
    assert [one for one in ours if one not in later] == [], ours


def _is_0040_nullable_change(difference: Any) -> bool:
    flat = difference[0] if isinstance(difference, list) else difference
    return (
        isinstance(flat, tuple)
        and flat[0] == "modify_nullable"
        and flat[2] == "pr_content_work_rules"
        and flat[3] == "created_by_user_id"
    )


# ---------------------------------------------------------------------------
# 2-3: the shape, from the live catalog
# ---------------------------------------------------------------------------


async def test_the_tables_have_the_columns_the_revision_promised(
    m3_database: Database,
) -> None:
    async with m3_database.session() as session:
        for table, expected in (
            (RULES, EXPECTED_RULE_COLUMNS),
            (PROJECTIONS, EXPECTED_PROJECTION_COLUMNS),
        ):
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = :table"
                ),
                {"table": table},
            )
            assert {row[0] for row in result} == expected, table


async def test_every_foreign_key_restricts(m3_database: Database) -> None:
    """``RESTRICT`` throughout, with **no exception**.

    The PR module's rule kept: a work type a mapping names cannot be deleted,
    and neither can a content item the projector has looked at.
    """
    async with m3_database.session() as session:
        result = await session.execute(
            text(
                "SELECT tc.table_name, kcu.column_name, rc.delete_rule "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.referential_constraints rc "
                "  ON tc.constraint_name = rc.constraint_name "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "  AND tc.table_name LIKE 'pr_content_work%'"
            )
        )
        rules = {(row[0], row[1]): row[2] for row in result}

    assert rules, "no foreign keys found - the query is wrong, not the schema"
    for (table, column), rule in rules.items():
        assert rule == "RESTRICT", (table, column, rule)


# ---------------------------------------------------------------------------
# 4: the partial unique indexes, both halves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "predicate"),
    [
        ("uq_pr_content_work_rules_kind_type", "IS NOT NULL"),
        ("uq_pr_content_work_rules_kind_default", "IS NULL"),
    ],
)
async def test_the_mapping_indexes_are_partial(
    m3_database: Database, index: str, predicate: str
) -> None:
    """Read from the catalog: each must carry a ``WHERE``.

    A nullable column cannot carry a unique constraint that means what a reader
    expects, which is why there are two of these rather than one.
    """
    async with m3_database.session() as session:
        definition = await session.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": index}
        )
    assert definition is not None, index
    assert "UNIQUE" in definition
    assert predicate in definition, definition


async def test_one_rule_per_kind_and_type(m3_database: Database) -> None:
    """An ambiguous mapping is unrepresentable, not resolved by whichever row
    came back first."""
    ids = await _seed_ids(m3_database)
    async with m3_database.session() as session:
        await _insert_rule(session, ids, kind="CONTENT_CREATION", content_type="PRESS_ARTICLE")
        await session.commit()
    async with m3_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_rule(session, ids, kind="CONTENT_CREATION", content_type="PRESS_ARTICLE")
            await session.commit()
        await session.rollback()
    # A different type for the same kind is fine - that is the point of the pair.
    async with m3_database.session() as session:
        await _insert_rule(session, ids, kind="CONTENT_CREATION", content_type="CORPORATE_TVC")
        await session.commit()


async def test_one_default_per_kind(m3_database: Database) -> None:
    """**The half a plain unique would have missed.**

    PostgreSQL treats every ``NULL`` as distinct, so a unique on
    ``(contribution_kind, content_type)`` would accept five competing defaults
    for one kind and the projector would pick whichever came back first.
    """
    ids = await _seed_ids(m3_database)
    async with m3_database.session() as session:
        await _insert_rule(session, ids, kind="PRODUCTION", content_type=None)
        await session.commit()
    async with m3_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_rule(session, ids, kind="PRODUCTION", content_type=None)
            await session.commit()
        await session.rollback()


async def test_one_projection_row_per_content_item(m3_database: Database) -> None:
    """What makes "request projection" idempotent.

    A content item that moves five times before the sweeper wakes writes one row
    and touches it four times.
    """
    ids = await _seed_ids(m3_database)
    async with m3_database.session() as session:
        await _insert_projection(session, ids)
        await session.commit()
    async with m3_database.session() as session:
        with pytest.raises(IntegrityError):
            await _insert_projection(session, ids)
            await session.commit()
        await session.rollback()


# ---------------------------------------------------------------------------
# 5-6: nothing outside the projector moved
# ---------------------------------------------------------------------------


async def test_the_counted_contribution_survives_the_migration_unchanged(
    m3_database: Database,
) -> None:
    """**M3's whole risk profile, asserted.**

    The counted contribution written at 0033 still says ``COUNTED`` at the same
    instant, no mapping was invented, no projection was queued, and **no
    source-derived work item was created for the content that was sitting there
    approved**. That last one is the backfill this milestone deliberately did
    not do.
    """
    async with m3_database.session() as session:
        status, counted_at = (
            await session.execute(
                text("SELECT count_status, counted_at FROM pr_work_contributions LIMIT 1")
            )
        ).one()
    assert status == "COUNTED"
    assert counted_at == _COUNTED_AT
    assert _AFTER_UPGRADE[RULES] == 0, "the migration invented no mapping"
    assert _AFTER_UPGRADE[PROJECTIONS] == 0, "the migration queued nothing"
    assert _AFTER_UPGRADE["source_work"] == 0, "the migration backfilled no content work"


async def test_the_roundtrip_leaves_the_ledger_alone(
    stopped_at_0033: tuple[Database, str], m3_database: Database
) -> None:
    """0034 down to 0033 and up again. The two tables go; nothing else does.

    Run last in the module, because it takes the schema down and back up under
    the other tests' feet.
    """
    _, dsn = stopped_at_0033
    before = await _fingerprint(m3_database)

    await downgrade_to(dsn, "0033")
    async with m3_database.session() as session:
        remaining = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name LIKE 'pr_content_work%'"
            )
        )
        assert remaining == 0
    assert await _fingerprint(m3_database) == before

    await upgrade_to(dsn, "0034")
    async with m3_database.session() as session:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'pr_content_work%' ORDER BY 1"
            )
        )
        assert [row[0] for row in result] == sorted(M3_TABLES)
    assert await _fingerprint(m3_database) == before


# ---------------------------------------------------------------------------
# Raw-SQL helpers
# ---------------------------------------------------------------------------


async def _fingerprint(database: Database) -> dict[str, Any]:
    """What must not move: the ledger row a KPI reads, and the content beside it."""
    async with database.session() as session:
        contribution = (
            await session.execute(
                text(
                    "SELECT count_status, counted_at FROM pr_work_contributions "
                    "ORDER BY created_at LIMIT 1"
                )
            )
        ).one()
        content = (
            await session.execute(
                text(
                    "SELECT code, workflow_stage, owner_user_id FROM pr_content_items "
                    "ORDER BY created_at LIMIT 1"
                )
            )
        ).one()
        source_work = await session.scalar(
            text("SELECT count(*) FROM pr_work_items WHERE source_type = 'CONTENT'")
        )
    return {
        "contribution": tuple(contribution),
        "content": tuple(content),
        "source_work": source_work,
    }


async def _seed_ids(database: Database) -> dict[str, Any]:
    async with database.session() as session:
        return {
            "user": await session.scalar(text("SELECT id FROM users LIMIT 1")),
            "work_type": await session.scalar(text("SELECT id FROM pr_work_types LIMIT 1")),
            "content": await session.scalar(text("SELECT id FROM pr_content_items LIMIT 1")),
        }


async def _insert_rule(
    session: Any, ids: dict[str, Any], *, kind: str, content_type: str | None
) -> None:
    """One mapping row. Every value **bound**, never interpolated."""
    await session.execute(
        text(
            "INSERT INTO pr_content_work_rules (id, contribution_kind, content_type, "
            "work_type_id, is_active, created_by_user_id, created_at, updated_at) "
            "VALUES (:id, :kind, :ctype, :wtype, true, :user, now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "kind": kind,
            "ctype": content_type,
            "wtype": ids["work_type"],
            "user": ids["user"],
        },
    )


async def _insert_projection(session: Any, ids: dict[str, Any]) -> None:
    await session.execute(
        text(
            "INSERT INTO pr_content_work_projections (id, content_id, status, requested_at, "
            "attempts, created_at, updated_at) "
            "VALUES (:id, :content, 'PENDING', now(), 0, now(), now())"
        ),
        {"id": uuid.uuid4(), "content": ids["content"]},
    )
