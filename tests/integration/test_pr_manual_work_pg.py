"""M4A on a real PostgreSQL: the batch is atomic, and the handoff is M1's.

The offline suite proves the *rules* - the modes, the quantity rule, the
self-validation refusal, the preflight. It cannot prove the two claims M4A is
actually built on, because the unit fixture is SQLite and rolls back:

* **a refused batch really writes nothing.** Off PostgreSQL, ``lock_row``
  degrades to a plain ``get`` and the fixture discards the transaction anyway,
  so "nothing was validated" is true there whatever the service did. Here a
  refused batch has to leave ``status``, ``count_status``, ``counted_at``, the
  history table and the audit trail exactly as it found them - after a commit
  that was rolled back around them;
* **a separate-per-assignee batch commits all of it or none of it.** Three
  people assigned one instruction is three inserts in one transaction, and an
  inactive third assignee must not leave the first two holding work.

And one end-to-end claim that needs a real quota engine behind it:

* **manual work reaches M6 through M2 and nothing else.** A counted manual
  contribution allocates against an approved quota exactly as content-derived
  work does, and M4 writes no allocation of its own.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_manual_work_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_m4a_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_support import supports_row_locks
from meobot.application.pr_work_bulk_validation_service import BulkValidateCommand
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work import PrWorkContribution, PrWorkHistory, PrWorkItem
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrBulkApprovalStaleError, PrValidationError
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis, PrWorkQuotaStatus
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
SEPTEMBER = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def m4_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_m4a_{uuid.uuid4().hex[:8]}"
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


class People:
    """Five people and two work types, created once and reused."""

    owner_id: uuid.UUID
    lead_id: uuid.UUID
    member_id: uuid.UUID
    other_id: uuid.UUID
    third_id: uuid.UUID
    #: ``ITEM_COUNT``. One job is one quota unit.
    routine_type_id: uuid.UUID
    #: ``QUANTITY``, in comments. The M4 archetype.
    seeding_type_id: uuid.UUID

    def actor(self, user_id: uuid.UUID, role: Role) -> Actor:
        return Actor(user_id=user_id, full_name="Người thử", role=role)

    @property
    def owner(self) -> Actor:
        return self.actor(self.owner_id, Role.OWNER)

    @property
    def lead(self) -> Actor:
        return self.actor(self.lead_id, Role.TEAM_LEAD)

    @property
    def member(self) -> Actor:
        return self.actor(self.member_id, Role.EMPLOYEE)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def people(m4_db: Database) -> People:
    world = People()
    async with m4_db.session() as session:
        rows = {
            "owner": User(full_name="Chị Chủ", role=Role.OWNER),
            "lead": User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD),
            "member": User(full_name="Phương Nhung", role=Role.EMPLOYEE),
            "other": User(full_name="Nguyễn A", role=Role.EMPLOYEE),
            "third": User(full_name="Trần B", role=Role.EMPLOYEE),
        }
        session.add_all(list(rows.values()))
        await session.flush()
        world.owner_id = rows["owner"].id
        world.lead_id = rows["lead"].id
        world.member_id = rows["member"].id
        world.other_id = rows["other"].id
        world.third_id = rows["third"].id

        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        routine = await services.work.create_work_type(
            actor=world.owner,
            request_id=uuid.uuid4(),
            code="PAGE_RECOVERY",
            name="Kháng page",
            category=PrWorkCategory.OPERATIONS,
            default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
        seeding = await services.work.create_work_type(
            actor=world.owner,
            request_id=uuid.uuid4(),
            code="SEEDING_COMMENT",
            name="Seeding bình luận",
            category=PrWorkCategory.COMMUNITY,
            default_unit=PrWorkUnit.COMMENT,
            default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        )
        world.routine_type_id = routine.id
        world.seeding_type_id = seeding.id
        await session.commit()
    return world


async def _completed(m4_db: Database, people: People, *, by: uuid.UUID, title: str) -> uuid.UUID:
    """One assigned job, finished by its contributor. Committed."""
    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        item = await services.work.assign_work(
            actor=people.lead,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=people.routine_type_id,
                title=title,
                contributor_user_ids=(by,),
            ),
        )
        await services.work.complete(
            actor=people.actor(by, Role.EMPLOYEE),
            request_id=uuid.uuid4(),
            work_item_id=item.id,
        )
        await session.commit()
        return item.id


# ---------------------------------------------------------------------------
# 1: the precondition
# ---------------------------------------------------------------------------


async def test_the_lock_is_actually_taken_on_postgresql(m4_db: Database) -> None:
    """Without this, every atomicity claim below would pass by not locking."""
    async with m4_db.session() as session:
        assert supports_row_locks(session) is True


# ---------------------------------------------------------------------------
# 2-4: the batch is atomic
# ---------------------------------------------------------------------------


async def test_a_refused_batch_commits_absolutely_nothing(m4_db: Database, people: People) -> None:
    """**The all-or-nothing promise, against a database that would keep a
    partial write.**

    Four valid jobs and one the validator did themselves. The refusal has to
    leave all four exactly as they were - not four approvals and a rollback
    nobody noticed, and not an audit row describing work that was not counted.
    """
    valid = [
        await _completed(m4_db, people, by=people.member_id, title=f"Việc hợp lệ {index}")
        for index in range(4)
    ]
    mine = await _completed(m4_db, people, by=people.lead_id, title="Việc của chính tôi")

    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        with pytest.raises(PrBulkApprovalStaleError) as error:
            await services.work_bulk_validation.validate(
                actor=people.lead,
                request_id=uuid.uuid4(),
                command=BulkValidateCommand(work_item_ids=[*valid, mine]),
            )
        assert error.value.details["validated"] == 0
        await session.commit()

    async with m4_db.session() as session:
        for item_id in [*valid, mine]:
            item = await session.get(PrWorkItem, item_id)
            assert item is not None
            assert item.status is PrWorkStatus.COMPLETED
            assert item.approved_at is None
        counted = await session.scalar(
            select(func.count())
            .select_from(PrWorkContribution)
            .where(
                PrWorkContribution.work_item_id.in_([*valid, mine]),
                PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
            )
        )
        assert counted == 0
        history = await session.scalar(
            select(func.count())
            .select_from(PrWorkHistory)
            .where(
                PrWorkHistory.work_item_id.in_([*valid, mine]),
                PrWorkHistory.to_status == PrWorkStatus.APPROVED,
            )
        )
        assert history == 0
        batches = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "pr.work.validation_batch_recorded")
        )
        assert batches == 0


async def test_a_successful_batch_commits_all_of_it(m4_db: Database, people: People) -> None:
    """The other half: five jobs, one act, five approvals that survive a commit."""
    items = [
        await _completed(m4_db, people, by=people.member_id, title=f"Việc hàng loạt {index}")
        for index in range(5)
    ]
    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        outcome = await services.work_bulk_validation.validate(
            actor=people.lead,
            request_id=uuid.uuid4(),
            command=BulkValidateCommand(work_item_ids=items),
        )
        assert len(outcome.validated) == 5
        await session.commit()

    async with m4_db.session() as session:
        counted = await session.scalar(
            select(func.count())
            .select_from(PrWorkContribution)
            .where(
                PrWorkContribution.work_item_id.in_(items),
                PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
            )
        )
        assert counted == 5
        # One batch row for the sweep, five per-item rows for the record of who
        # counted whose work. Nothing collapsed.
        batches = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "pr.work.validation_batch_recorded")
        )
        assert batches == 1


async def test_a_refused_separate_assignment_creates_no_work_at_all(
    m4_db: Database, people: People
) -> None:
    """A batch assignment whose last assignee is inactive leaves the first two
    people holding nothing."""
    async with m4_db.session() as session:
        retired = User(full_name="Đã nghỉ", role=Role.EMPLOYEE, active=False)
        session.add(retired)
        await session.flush()
        retired_id = retired.id
        await session.commit()

    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        with pytest.raises(PrValidationError):
            await services.work.assign_work_batch(
                actor=people.lead,
                request_id=uuid.uuid4(),
                command=CreateWorkCommand(
                    work_type_id=people.routine_type_id,
                    title="Giao cho ba người, một người đã nghỉ",
                    contributor_user_ids=(people.member_id, people.other_id, retired_id),
                ),
                mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
            )
        await session.rollback()

    async with m4_db.session() as session:
        made = await session.scalar(
            select(func.count())
            .select_from(PrWorkItem)
            .where(PrWorkItem.title == "Giao cho ba người, một người đã nghỉ")
        )
        assert made == 0


async def test_a_separate_assignment_commits_one_item_per_person(
    m4_db: Database, people: People
) -> None:
    """Three obligations, three rows, each carrying the whole quantity."""
    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        items = await services.work.assign_work_batch(
            actor=people.lead,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=people.seeding_type_id,
                title="100 comment mỗi người",
                quantity=Decimal("100"),
                contributor_user_ids=(people.member_id, people.other_id, people.third_id),
            ),
            mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        )
        assert len(items) == 3
        await session.commit()

    async with m4_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(PrWorkItem).where(PrWorkItem.title == "100 comment mỗi người")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert {row.quantity for row in rows} == {Decimal("100.00")}
        assert {row.unit for row in rows} == {PrWorkUnit.COMMENT}
        assert {row.source_type for row in rows} == {PrWorkSourceType.MANUAL}
        for row in rows:
            contributions = (
                (
                    await session.execute(
                        select(PrWorkContribution).where(PrWorkContribution.work_item_id == row.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(contributions) == 1


# ---------------------------------------------------------------------------
# 5-6: the handoff to M2, and what M4 does not write
# ---------------------------------------------------------------------------


async def test_counted_manual_work_allocates_against_an_approved_quota(
    m4_db: Database, people: People
) -> None:
    """**The end-to-end claim.** Manual work reaches M2 by being counted, and by
    nothing else - the same path content-derived work takes."""
    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        period = await services.work_periods.ensure_month_period(
            actor=people.owner, request_id=uuid.uuid4(), year=2026, month=9
        )
        plan = await services.work_plans.create_plan(
            actor=people.owner,
            request_id=uuid.uuid4(),
            user_id=people.third_id,
            period_id=period.id,
        )
        await services.work_plans.add_quota(
            actor=people.owner,
            request_id=uuid.uuid4(),
            plan_id=plan.plan.id,
            work_type_id=people.routine_type_id,
            target_value=Decimal("10"),
            eligibility_cap=Decimal("10"),
        )
        await services.work_plans.approve(
            actor=people.owner, request_id=uuid.uuid4(), plan_id=plan.plan.id
        )
        period_id = period.id
        await session.commit()

    item_id = await _completed(m4_db, people, by=people.third_id, title="Kháng page có KPI")

    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        await services.work.approve(
            actor=people.lead, request_id=uuid.uuid4(), work_item_id=item_id
        )
        await session.commit()

    async with m4_db.session() as session:
        allocations = (
            (
                await session.execute(
                    select(PrWorkQuotaAllocation).where(
                        PrWorkQuotaAllocation.user_id == people.third_id,
                        PrWorkQuotaAllocation.reporting_period_id == period_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(allocations) == 1
        assert allocations[0].quota_status is PrWorkQuotaStatus.ELIGIBLE
        # ``ITEM_COUNT``: one job is one unit, whatever the item's quantity says.
        assert allocations[0].eligible_amount == Decimal("1.00")


async def test_counted_manual_work_without_a_quota_is_still_counted(
    m4_db: Database, people: People
) -> None:
    """``NO_QUOTA`` is real counted work with no eligible amount. **Not a refusal.**

    The ``other`` employee has no approved plan at all. The work counts, the
    ledger records it, and M4 creates no quota to make the number look better.
    """
    item_id = await _completed(m4_db, people, by=people.other_id, title="Kháng page không KPI")
    async with m4_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        detail = await services.work.approve(
            actor=people.lead, request_id=uuid.uuid4(), work_item_id=item_id
        )
        assert all(one.count_status is PrWorkCountStatus.COUNTED for one in detail.contributions)
        await session.commit()

    async with m4_db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        assert item is not None
        assert item.status is PrWorkStatus.APPROVED
        allocations = (
            (
                await session.execute(
                    select(PrWorkQuotaAllocation).where(
                        PrWorkQuotaAllocation.user_id == people.other_id
                    )
                )
            )
            .scalars()
            .all()
        )
        # Either nothing, or a NO_QUOTA row with no eligible amount. Both are
        # M2's business; what matters is that M4 wrote no quota of its own.
        assert all(one.eligible_amount == Decimal("0.00") for one in allocations)
