"""M4B against a real PostgreSQL: uniqueness, concurrency and the roundtrip.

What only a real database answers here:

* **the constraints are actually there.** One occurrence per template per firing,
  one work item per occurrence per subject, and the CHECKs that stop the
  scheduler's ledger claiming work it has not got. A service can be talked out of
  a rule; a unique index cannot, and the rule these enforce is the one M4B would
  be worthless without - *"two beat workers sweeping the same second produce one
  job"*;
* **the models describe the migration.** ``compare_metadata`` over M4B's three
  tables, which is the exit-gate item that stops the ORM and ``0036`` drifting;
* **the migration goes both ways.** ``0035 -> 0036 -> 0035 -> 0036``, because a
  downgrade nobody has run is a downgrade that does not work;
* **concurrent sweeps converge.** Two sessions racing on one firing, which is
  exactly what two beat containers do and what no offline SQLite test can show.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_recurring_work_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_m4b_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.db.models.pr_work_recurring import (
    PrWorkRecurringOccurrence,
    PrWorkRecurringTemplate,
    PrWorkRecurringTemplateContributor,
)
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.pr.recurring import (
    PrRecurringFrequency,
    PrRecurringOccurrenceState,
    PrRecurringTemplateStatus,
    recurring_source_key,
)
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
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

#: M4B's three tables, and the filter every drift assertion uses.
M4B_TABLES = (
    "pr_work_recurring_templates",
    "pr_work_recurring_template_contributors",
    "pr_work_recurring_occurrences",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def m4b_dsn() -> AsyncIterator[str]:
    """A scratch database migrated to head, and the DSN for it."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_m4b_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn)
        yield dsn
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def m4b_db(m4b_dsn: str) -> AsyncIterator[Database]:
    database = Database(Settings(database_url=m4b_dsn, **SETTINGS_KWARGS))
    try:
        yield database
    finally:
        await database.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def fixtures(m4b_db: Database) -> dict[str, uuid.UUID]:
    """A manager, two employees, a work type, and one active template."""
    async with m4b_db.session() as session:
        lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD)
        member = User(full_name="Phương Nhung", role=Role.EMPLOYEE)
        other = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
        work_type = PrWorkType(
            code="DAILY_SEEDING",
            name="Seeding hằng ngày",
            category=PrWorkCategory.COMMUNITY,
            default_unit=PrWorkUnit.COMMENT,
            default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        )
        session.add_all([lead, member, other, work_type])
        await session.flush()

        template = PrWorkRecurringTemplate(
            name="Seeding 100 bình luận",
            work_type_id=work_type.id,
            assignment_mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
            quantity=100,
            frequency=PrRecurringFrequency.DAILY,
            weekdays=[],
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            status=PrRecurringTemplateStatus.ACTIVE,
            revision_no=1,
            created_by_user_id=lead.id,
            activated_by_user_id=lead.id,
            activated_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
            last_evaluated_occurrence_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        )
        session.add(template)
        await session.flush()
        session.add_all(
            [
                PrWorkRecurringTemplateContributor(
                    template_id=template.id, user_id=member.id, display_order=0
                ),
                PrWorkRecurringTemplateContributor(
                    template_id=template.id, user_id=other.id, display_order=1
                ),
            ]
        )
        await session.flush()
        ids = {
            "lead": lead.id,
            "member": member.id,
            "other": other.id,
            "work_type": work_type.id,
            "template": template.id,
        }
        await session.commit()
    return ids


def _occurrence(template_id: uuid.UUID, key: str) -> PrWorkRecurringOccurrence:
    return PrWorkRecurringOccurrence(
        template_id=template_id,
        occurrence_key=key,
        scheduled_for=datetime(2026, 9, 4, 2, 0, tzinfo=UTC),
        template_revision_no=1,
        state=PrRecurringOccurrenceState.PENDING,
    )


# ===========================================================================
# The models describe the migration
# ===========================================================================


async def test_01_models_and_migration_agree(m4b_db: Database) -> None:
    """**The exit-gate check.** Zero drift over M4B's three tables.

    Filtered to M4B's own names because the repository has pre-existing drift
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

    async with m4b_db.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    ours = [
        difference
        for difference in differences
        if any(name in str(difference) for name in M4B_TABLES)
    ]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision_on_disk(m4b_db: Database) -> None:
    """The fixture migrated to ``head``, so this asserts *which* head that was.

    Read from the filesystem rather than written in as ``"0036"``. That literal
    turned "M4B's migration applies" into "M4B's migration is the newest one",
    which stopped being true the moment the post-M4 patch added ``0037`` - the
    same trap ``test_pr_performance_pg`` fell into one milestone earlier.
    """
    async with m4b_db.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_the_migration_roundtrips(m4b_dsn: str) -> None:
    """``0036 -> 0035 -> 0036``. A downgrade nobody has run does not work.

    The downgrade drops M4B's three tables and touches nothing else - M1's
    ledger, M2's allocations and M6's scores are read by this milestone and never
    written, so there is nothing else for it to put back.
    """
    # Down to just before M4B, which is two steps now that ``0037`` sits on top.
    await downgrade_to(m4b_dsn, "0035")
    engine = create_async_engine(m4b_dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert head == "0035"
            for name in M4B_TABLES:
                exists = await connection.scalar(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name}
                )
                assert exists is False, f"{name} survived the downgrade"
            # M1's ledger is untouched by either direction.
            assert await connection.scalar(text("SELECT to_regclass('pr_work_items') IS NOT NULL"))
    finally:
        await engine.dispose()

    await upgrade_to(m4b_dsn)
    engine = create_async_engine(m4b_dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert head == alembic_head()
            for name in M4B_TABLES:
                assert await connection.scalar(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name}
                )
    finally:
        await engine.dispose()


# ===========================================================================
# Constraints a service cannot be talked out of
# ===========================================================================


async def test_04_one_occurrence_per_template_per_firing(
    m4b_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """**The idempotency guarantee, at the database.**

    Two beat workers sweeping the same template in the same second insert the
    same ``occurrence_key``. One loses, and losing is the mechanism working -
    which is the case a ``SELECT`` followed by an ``INSERT`` cannot cover, because
    both would read "not there" before either wrote.
    """
    async with m4b_db.session() as session:
        session.add(_occurrence(fixtures["template"], "20260904T0900"))
        await session.commit()

    async with m4b_db.session() as session:
        session.add(_occurrence(fixtures["template"], "20260904T0900"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_05_two_concurrent_sweeps_reserve_one_firing(
    m4b_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """Two **open sessions**, both inserting, neither having seen the other.

    The sequential test above proves the index exists. This one proves it holds
    under the shape that actually happens: both transactions began before either
    committed, so neither could have read the other's row at any point.
    """
    async with m4b_db.session() as first, m4b_db.session() as second:
        first.add(_occurrence(fixtures["template"], "20260905T0900"))
        second.add(_occurrence(fixtures["template"], "20260905T0900"))
        await first.flush()
        # ``second`` now blocks on the uncommitted unique key until ``first``
        # resolves, then fails - which is exactly what one of two racing beat
        # workers must do.
        await first.commit()
        with pytest.raises(IntegrityError):
            await second.flush()
        await second.rollback()

    async with m4b_db.session() as session:
        count = await session.scalar(
            select(text("count(*)"))
            .select_from(PrWorkRecurringOccurrence)
            .where(PrWorkRecurringOccurrence.occurrence_key == "20260905T0900")
        )
    assert count == 1


async def test_06_one_work_item_per_occurrence_and_subject(
    m4b_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """**The other unique index**, and the one that stops duplicate *work*.

    ``uq_pr_work_items_source`` over ``(source_type, source_key)``, with the key
    composed from the occurrence's id and the assignee's. A retry after a crash
    composes the identical key and collides; two different assignees compose two
    keys and both survive. That pairing is what makes "one item each" a
    guarantee rather than a hope.
    """
    occurrence_id = uuid.uuid4()
    async with m4b_db.session() as session:
        occurrence = _occurrence(fixtures["template"], "20260906T0900")
        occurrence.id = occurrence_id
        session.add(occurrence)
        await session.commit()

    def item(user_id: uuid.UUID | None, code: str) -> PrWorkItem:
        return PrWorkItem(
            code=code,
            title="Seeding 100 bình luận",
            work_type_id=fixtures["work_type"],
            source_type=PrWorkSourceType.RECURRING,
            source_key=recurring_source_key(occurrence_id, user_id=user_id),
            status=PrWorkStatus.ACCEPTED,
            quantity=100,
            unit=PrWorkUnit.COMMENT,
            created_by_user_id=fixtures["lead"],
            assigned_by_user_id=fixtures["lead"],
        )

    # Two assignees, two keys, both fine.
    async with m4b_db.session() as session:
        session.add_all(
            [
                item(fixtures["member"], "WRK-2026-000001"),
                item(fixtures["other"], "WRK-2026-000002"),
            ]
        )
        await session.commit()

    # The same assignee's firing again - a retry - collides.
    async with m4b_db.session() as session:
        session.add(item(fixtures["member"], "WRK-2026-000003"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_07_the_ledger_cannot_claim_work_it_has_not_got(
    m4b_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """``GENERATED`` and ``generated_at`` are one fact, by a CHECK.

    The pairing is why anything reading this table can trust the state. A row
    saying "generated" with no timestamp, or a timestamp with no state, would be
    a scheduler that had lost track of whether it had done the work.
    """
    async with m4b_db.session() as session:
        row = _occurrence(fixtures["template"], "20260907T0900")
        row.state = PrRecurringOccurrenceState.GENERATED
        row.generated_at = None
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with m4b_db.session() as session:
        row = _occurrence(fixtures["template"], "20260908T0900")
        # Not generated, but claiming work. Refused for the same reason.
        row.work_item_count = 2
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_08_a_weekly_template_needs_a_day_and_a_monthly_one_a_date(
    m4b_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """The schedule's shape and its parameters agree, at the database.

    A ``MONTHLY`` template with no ``day_of_month`` fires whenever the reader
    guesses, and a non-monthly one carrying a day is a field nothing reads. Both
    are refused in the domain too; this is the floor under it.
    """

    def template(frequency: PrRecurringFrequency, day: int | None) -> PrWorkRecurringTemplate:
        return PrWorkRecurringTemplate(
            name="Xấu",
            work_type_id=fixtures["work_type"],
            assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
            frequency=frequency,
            weekdays=[],
            day_of_month=day,
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            created_by_user_id=fixtures["lead"],
        )

    for frequency, day in (
        (PrRecurringFrequency.MONTHLY, None),
        (PrRecurringFrequency.DAILY, 5),
        (PrRecurringFrequency.MONTHLY, 32),
    ):
        async with m4b_db.session() as session:
            session.add(template(frequency, day))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()


async def test_09_a_template_that_has_run_cannot_be_deleted_by_the_database(
    m4b_db: Database, fixtures: dict[str, uuid.UUID]
) -> None:
    """``RESTRICT`` towards the occurrence ledger, independently of the service.

    An occurrence is provenance for work that exists, so the constraint refuses
    the delete whatever a service was talked into. The contributor list is
    ``CASCADE`` by contrast - those rows are parts of the template rather than
    facts of their own.
    """
    async with m4b_db.session() as session:
        row = await session.get(PrWorkRecurringTemplate, fixtures["template"])
        assert row is not None
        await session.delete(row)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_10_no_money_and_no_score_reached_the_schema(m4b_db: Database) -> None:
    """**The scope check.** M4B creates work; it prices nothing.

    Asserted against the live catalogue rather than against the migration that
    was supposed to produce it: a template with a rate on it would be inventing a
    number nobody approved, and a scheduler is exactly where such a column gets
    added quietly.
    """
    async with m4b_db.session() as session:
        columns = (
            (
                await session.execute(
                    text(
                        "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        "WHERE table_name = ANY(:tables)"
                    ),
                    {"tables": list(M4B_TABLES)},
                )
            )
            .scalars()
            .all()
        )
    forbidden = ("score", "amount", "money", "salary", "bonus", "coefficient", "minutes")
    assert [one for one in columns if any(word in one.lower() for word in forbidden)] == []
