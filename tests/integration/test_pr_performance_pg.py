"""M6 against a real PostgreSQL: uniqueness, concurrency and exact money.

What only a real database answers here:

* **the constraints are actually there.** One review per person per month, one
  result per person per month, one rule version per work type - and the CHECKs
  that refuse a self-review, a note-less bad rating and an override with no
  reason. A service can be talked out of a rule; an index cannot;
* **the models describe the migration.** ``compare_metadata`` over M6's six
  tables, which is the exit-gate item that stops the ORM and ``0035`` drifting;
* **the scope correction holds at the schema.** Six tables, no bonus pool, no
  allocation table, and no money column anywhere - asserted against the live
  catalogue rather than against the migration that was supposed to produce it.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_performance_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_m6_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.pr_performance import (
    PrPerformanceReview,
    PrWorkScoringRule,
)
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.pr.performance import (
    PrPerformanceLevel,
    PrScoringRuleStatus,
    PrWorkScoringMode,
)
from meobot.domain.pr.reporting import PrPeriodStatus, PrPeriodType
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    alembic_head,
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def m6_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_m6_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn)
        database = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))
        try:
            yield database
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def fixtures(m6_db: Database) -> dict[str, uuid.UUID]:
    """Two people, a work type and an open September."""
    async with m6_db.session() as session:
        owner = User(full_name="Chị Chủ", role=Role.OWNER)
        member = User(full_name="Bùi Mỹ Hảo", role=Role.EMPLOYEE)
        session.add_all([owner, member])
        await session.flush()

        from meobot.db.models.pr_work import PrWorkType
        from meobot.domain.pr.work import PrWorkCategory, PrWorkUnit
        from meobot.domain.pr.work_quota import PrWorkQuotaBasis

        work_type = PrWorkType(
            code="VIDEO_EDIT",
            name="Dựng video",
            category=PrWorkCategory.PRODUCTION,
            default_unit=PrWorkUnit.ITEM,
            default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
        period = PrReportingPeriod(
            code="2026-09",
            period_type=PrPeriodType.MONTH,
            date_start=date(2026, 9, 1),
            date_end=date(2026, 9, 30),
            status=PrPeriodStatus.OPEN,
        )
        session.add_all([work_type, period])
        await session.flush()
        ids = {
            "owner": owner.id,
            "member": member.id,
            "work_type": work_type.id,
            "period": period.id,
        }
        await session.commit()
    return ids


# ===========================================================================
# The models describe the migration
# ===========================================================================


async def test_01_models_and_migration_agree(m6_db: Database) -> None:
    """**The exit-gate check.** Zero drift over M6's six tables.

    Filtered to M6's own names because the repository has pre-existing drift
    between older models and older migrations - widening the assertion would make
    it fail for reasons this milestone did not cause and must not fix.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with m6_db.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    ours = [
        difference
        for difference in differences
        if any(
            name in str(difference)
            for name in (
                "pr_work_scoring_rules",
                "pr_work_score_allocations",
                "pr_performance_policies",
                "pr_performance_reviews",
                "pr_performance_target_overrides",
                "pr_performance_results",
            )
        )
    ]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision_on_disk(m6_db: Database) -> None:
    """The fixture migrated to ``head``, so this asserts *which* head that was.

    Read from the filesystem rather than written in as ``"0035"``. That literal
    turned "M6's migration applies" into "M6's migration is the newest one",
    which stopped being true the moment M4B added ``0036`` - and the test went
    red for a milestone it says nothing about. ``alembic_head`` is the same
    helper the migration suites already share for exactly this reason.
    """
    async with m6_db.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


# ===========================================================================
# Constraints a service cannot be talked out of
# ===========================================================================


async def test_03_one_review_per_person_per_month(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """**The product decision, at the database.**

    Two concurrent managers opening the same form both insert; the second fails
    on the unique index rather than producing a second review, which is the case
    a service-level check cannot cover.
    """
    async with m6_db.session() as session:
        session.add(
            PrPerformanceReview(user_id=fixtures["member"], reporting_period_id=fixtures["period"])
        )
        await session.commit()

    async with m6_db.session() as session:
        session.add(
            PrPerformanceReview(user_id=fixtures["member"], reporting_period_id=fixtures["period"])
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_04_a_reviewer_is_never_the_person_reviewed(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """Self-review is refused by a CHECK, not only by the service.

    The one failure that would invalidate the whole exercise, so it is refused
    in two independent places.
    """
    async with m6_db.session() as session:
        session.add(
            PrPerformanceReview(
                user_id=fixtures["owner"],
                reporting_period_id=fixtures["period"],
                reviewer_user_id=fixtures["owner"],
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_05_a_rating_other_than_dat_needs_a_note(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """**Anything but Đạt is explained.** The result moves money."""
    async with m6_db.session() as session:
        review = (
            (
                await session.execute(
                    select(PrPerformanceReview).where(
                        PrPerformanceReview.user_id == fixtures["member"]
                    )
                )
            )
            .scalars()
            .one()
        )
        review.quality_level = PrPerformanceLevel.POOR
        review.quality_score = Decimal("70")
        review.quality_note = None
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    # And Đạt may omit it.
    async with m6_db.session() as session:
        review = (
            (
                await session.execute(
                    select(PrPerformanceReview).where(
                        PrPerformanceReview.user_id == fixtures["member"]
                    )
                )
            )
            .scalars()
            .one()
        )
        review.quality_level = PrPerformanceLevel.MEETS_EXPECTATIONS
        review.quality_score = Decimal("100")
        review.quality_note = None
        await session.commit()


async def test_06_a_level_and_its_score_are_written_together(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """A level with no number cannot be computed with; a number with no level is
    one nobody chose."""
    async with m6_db.session() as session:
        review = (
            (
                await session.execute(
                    select(PrPerformanceReview).where(
                        PrPerformanceReview.user_id == fixtures["member"]
                    )
                )
            )
            .scalars()
            .one()
        )
        review.timeliness_level = PrPerformanceLevel.GOOD
        review.timeliness_note = "chủ động"
        review.timeliness_score = None
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_07_one_rule_version_per_work_type(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    async with m6_db.session() as session:
        session.add(
            PrWorkScoringRule(
                work_type_id=fixtures["work_type"],
                version_no=1,
                mode=PrWorkScoringMode.STANDARD_MINUTES,
                standard_minutes_per_unit=Decimal("90"),
                effective_from=date(2026, 1, 1),
                status=PrScoringRuleStatus.APPROVED,
            )
        )
        await session.commit()

    async with m6_db.session() as session:
        session.add(
            PrWorkScoringRule(
                work_type_id=fixtures["work_type"],
                version_no=1,
                mode=PrWorkScoringMode.STANDARD_MINUTES,
                standard_minutes_per_unit=Decimal("105"),
                effective_from=date(2027, 1, 1),
                status=PrScoringRuleStatus.DRAFT,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_08_excluded_and_priced_are_mutually_exclusive(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """*Excluded* and *worth nothing per unit* are different statements, and the
    CHECK is what keeps them from being written as each other."""
    async with m6_db.session() as session:
        session.add(
            PrWorkScoringRule(
                work_type_id=fixtures["work_type"],
                version_no=99,
                mode=PrWorkScoringMode.EXCLUDED_FROM_PERFORMANCE,
                standard_minutes_per_unit=Decimal("90"),
                effective_from=date(2026, 1, 1),
                status=PrScoringRuleStatus.DRAFT,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_10_policy_weights_must_total_one_hundred(m6_db: Database) -> None:
    """A policy summing to 90 would deflate every index computed under it."""
    from meobot.db.models.pr_performance import PrPerformancePolicy

    async with m6_db.session() as session:
        session.add(
            PrPerformancePolicy(
                version_no=1,
                effective_from=date(2026, 1, 1),
                daily_target_minutes=300,
                workload_weight=Decimal("50"),
                quality_weight=Decimal("20"),
                timeliness_weight=Decimal("10"),
                business_contribution_weight=Decimal("10"),
                workload_score_cap=Decimal("120"),
                quality_scores={},
                timeliness_scores={},
                business_contribution_scores={},
                quality_gate=[],
                performance_bands=[],
                status=PrScoringRuleStatus.DRAFT,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_11_one_result_per_person_per_month(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """What makes a concurrent recalculation converge rather than accumulate."""
    from meobot.db.models.pr_performance import PrPerformanceResult
    from meobot.domain.pr.performance import PrPerformanceCalculationStatus

    def _row() -> PrPerformanceResult:
        return PrPerformanceResult(
            user_id=fixtures["member"],
            reporting_period_id=fixtures["period"],
            eligible_standard_minutes=Decimal("0"),
            calculation_status=PrPerformanceCalculationStatus.READY,
            calculated_at=datetime.now(UTC),
        )

    async with m6_db.session() as session:
        session.add(_row())
        await session.commit()
    async with m6_db.session() as session:
        session.add(_row())
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_12_a_finalised_result_must_name_its_policy(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """A figure whose policy is not stored beside it cannot be reproduced, which
    is the entire reason the row exists."""
    from meobot.db.models.pr_performance import PrPerformanceResult

    async with m6_db.session() as session:
        result = (
            (
                await session.execute(
                    select(PrPerformanceResult).where(
                        PrPerformanceResult.user_id == fixtures["member"]
                    )
                )
            )
            .scalars()
            .one()
        )
        result.finalized_at = datetime.now(UTC)
        result.policy_id = None
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# ===========================================================================
# The scope correction, at the schema
# ===========================================================================


async def test_13_there_is_no_money_in_the_database(m6_db: Database) -> None:
    """**M6 scores performance and allocates none.**

    Asserted against the live catalogue: no bonus table, and no column anywhere
    in M6's six that could hold a coefficient or an amount. A performance index
    is an evaluation result, and a schema that stored it beside a multiplier
    would make it read as a promise about somebody's pay.
    """
    async with m6_db.session() as session:
        tables = set(
            (
                await session.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE tablename LIKE 'pr_performance%' OR tablename LIKE 'pr_work_sco%'"
                    )
                )
            )
            .scalars()
            .all()
        )
        columns = set(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = ANY(:tables)"
                    ),
                    {"tables": list(tables)},
                )
            )
            .scalars()
            .all()
        )

    assert tables == {
        "pr_work_scoring_rules",
        "pr_work_score_allocations",
        "pr_performance_policies",
        "pr_performance_reviews",
        "pr_performance_target_overrides",
        "pr_performance_results",
    }
    for forbidden in (
        "bonus_coefficient",
        "bonus_coefficient_cap",
        "base_performance_amount",
        "bonus_weight",
        "allocated_amount",
        "share_weight",
        "pool_amount",
    ):
        assert forbidden not in columns, forbidden


async def test_14_a_target_override_needs_both_halves(
    m6_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """The renamed table holds exactly one thing, and both its columns are required.

    ``pr_performance_inputs`` once carried a bonus basis and a share weight too.
    With compensation out of M6 the generic name described a table with one
    purpose, so it was renamed while ``0035`` was still undeployed - and the
    columns became ``NOT NULL``, because a row here *is* a deliberate override.
    """
    from meobot.db.models.pr_performance import PrPerformanceTargetOverride

    async with m6_db.session() as session:
        session.add(
            PrPerformanceTargetOverride(
                user_id=fixtures["member"],
                reporting_period_id=fixtures["period"],
                monthly_target_override=Decimal("4000"),
                override_reason="   ",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with m6_db.session() as session:
        session.add(
            PrPerformanceTargetOverride(
                user_id=fixtures["member"],
                reporting_period_id=fixtures["period"],
                monthly_target_override=Decimal("4000"),
                override_reason="Vào làm từ 15/09",
            )
        )
        await session.commit()
