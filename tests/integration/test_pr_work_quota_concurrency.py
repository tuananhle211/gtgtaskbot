"""M2 on a real PostgreSQL: a cap is never oversubscribed, and never doubled.

The offline suite proves the *rules* - the allocation arithmetic, the four
statuses, the lifecycle - and it cannot prove the two claims the milestone
actually rests on, because the unit fixture is SQLite:

* **the lock is real.** ``lock_row`` degrades to a plain ``get`` off PostgreSQL,
  deliberately - SQLite has no ``FOR UPDATE`` and serialises writers anyway - so
  every offline test of "two transactions race for the last quota slot" is a
  test of a code path that did not lock. Here it does;
* **the transaction really commits.** SQLite in the unit fixture rolls back, so
  "the cap was not exceeded" is true there whatever the evaluator did. Here two
  committed approvals have to leave exactly one eligible slot filled.

Six scenarios, each one of M2's promises:

#. two work items becoming ``COUNTED`` **concurrently** for the last
   ``ITEM_COUNT`` slot produce one ``ELIGIBLE`` and one ``OVER_QUOTA`` - never
   cap + 1;
#. the same for ``QUANTITY``: the eligible amounts sum to the cap exactly, and
   the partial split is correct;
#. two administrators approving two drafts of one plan concurrently produce one
   approved plan - the partial unique index, not a hopeful check;
#. two reconciliations running **at the same time** produce one allocation per
   contribution, not two;
#. a duplicate approval retry is refused and writes nothing;
#. **the approval survives a projection that fails.** The savepoint is what
   makes "validating work never depends on a quota" true under a real
   transaction rather than only in a docstring.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_quota_concurrency.py -m integration

The fixture creates its own uniquely-named ``meobot_quotarace_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_support import supports_row_locks
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.config import Settings
from meobot.db.models.pr_work import PrWorkContribution
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota, PrWorkQuotaAllocation
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkPlanStateError,
)
from meobot.domain.pr.work import PrWorkCategory, PrWorkCountStatus, PrWorkUnit
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

#: The month everything below is counted into. Fixed, because "which period is
#: this" must not depend on when the suite runs.
YEAR, MONTH = 2026, 9


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def quota_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_quotarace_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        # **Head, not 0033.** This suite drives today's M2 services, and a
        # service writes today's columns - so pinning the schema to the
        # revision M2 shipped on made the pair inconsistent the moment any
        # later migration added a column to a table M2 writes, which ``0037``
        # did. Nothing here asserts about a revision boundary; what it asserts
        # is that two concurrent transactions cannot oversubscribe a cap, and
        # that is a property of the constraints head still carries.
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
    """Four people, two work types and the September period, created once."""

    owner_id: uuid.UUID
    head_id: uuid.UUID
    lead_id: uuid.UUID
    member_id: uuid.UUID
    other_id: uuid.UUID
    item_type_id: uuid.UUID
    quantity_type_id: uuid.UUID
    period_id: uuid.UUID

    def actor(self, user_id: uuid.UUID, role: Role) -> Actor:
        return Actor(user_id=user_id, full_name="Người thử", role=role)

    @property
    def owner(self) -> Actor:
        return self.actor(self.owner_id, Role.OWNER)

    @property
    def head(self) -> Actor:
        return self.actor(self.head_id, Role.ADMIN)

    @property
    def lead(self) -> Actor:
        return self.actor(self.lead_id, Role.TEAM_LEAD)

    @property
    def member(self) -> Actor:
        return self.actor(self.member_id, Role.EMPLOYEE)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def people(quota_db: Database) -> People:
    world = People()
    async with quota_db.session() as session:
        rows = {
            "owner": User(full_name="Chị Chủ", role=Role.OWNER),
            "head": User(full_name="Hà Trưởng Phòng", role=Role.ADMIN),
            "lead": User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD),
            "member": User(full_name="Phương Nhung", role=Role.EMPLOYEE),
            "other": User(full_name="Nguyễn A", role=Role.EMPLOYEE),
        }
        session.add_all(list(rows.values()))
        await session.flush()
        world.owner_id = rows["owner"].id
        world.head_id = rows["head"].id
        world.lead_id = rows["lead"].id
        world.member_id = rows["member"].id
        world.other_id = rows["other"].id

        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        item_type = await services.work.create_work_type(
            actor=world.owner,
            request_id=uuid.uuid4(),
            code="SHORT_SCRIPT",
            name="Kịch bản ngắn",
            category=PrWorkCategory.CONTENT,
        )
        quantity_type = await services.work.create_work_type(
            actor=world.owner,
            request_id=uuid.uuid4(),
            code="SEEDING_COMMENT",
            name="Seeding bình luận",
            category=PrWorkCategory.COMMUNITY,
            default_unit=PrWorkUnit.COMMENT,
            default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        )
        world.item_type_id = item_type.id
        world.quantity_type_id = quantity_type.id
        period = await services.work_periods.ensure_month_period(
            actor=world.owner, request_id=uuid.uuid4(), year=YEAR, month=MONTH
        )
        world.period_id = period.id
        await session.commit()
    return world


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _plan(
    quota_db: Database,
    people: People,
    *,
    user_id: uuid.UUID,
    work_type_id: uuid.UUID,
    target: str,
    cap: str,
) -> uuid.UUID:
    """One approved plan with one quota. Committed."""
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        draft = await services.work_plans.create_plan(
            actor=people.head,
            request_id=uuid.uuid4(),
            user_id=user_id,
            period_id=people.period_id,
        )
        await services.work_plans.add_quota(
            actor=people.head,
            request_id=uuid.uuid4(),
            plan_id=draft.plan.id,
            work_type_id=work_type_id,
            target_value=Decimal(target),
            eligibility_cap=Decimal(cap),
        )
        approved = await services.work_plans.approve(
            actor=people.head, request_id=uuid.uuid4(), plan_id=draft.plan.id
        )
        await session.commit()
        return approved.plan.id


async def _completed(
    quota_db: Database,
    people: People,
    *,
    user_id: uuid.UUID,
    work_type_id: uuid.UUID,
    title: str,
    quantity: Decimal | None = None,
) -> uuid.UUID:
    """Assigned and completed - the state a validator sees. Committed."""
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        item = await services.work.assign_work(
            actor=people.head,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=work_type_id,
                title=title,
                quantity=quantity,
                contributor_user_ids=(user_id,),
            ),
        )
        await services.work.complete(
            actor=people.actor(user_id, Role.EMPLOYEE),
            request_id=uuid.uuid4(),
            work_item_id=item.id,
        )
        await session.commit()
        return item.id


async def _approve_into_september(quota_db: Database, people: People, item_id: uuid.UUID) -> str:
    """Validate one item and move its ``counted_at`` into the fixed month.

    Both in **one** transaction, because the projection runs inside the approval
    and has to see the instant that decides which period the work belongs to.
    ``counted_at`` is written directly for the reason M1's own suites write it
    directly: no service path changes it after validation, and inventing one for
    a test would be inventing the correction workflow M2 is not building.

    Validated by the **head** rather than the lead. Both hold
    ``PR_WORK_VALIDATE`` and neither is a contributor, so either may approve -
    but ``approve`` returns the refreshed detail, and reading a work item takes
    one of M1's four relationships or ``PR_WORK_VIEW_ALL``. The head assigned
    these items and holds the capability; a lead who did neither is refused at
    the *read*, which would be a test failing for a reason that has nothing to
    do with quotas.
    """
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        try:
            await services.work.approve(
                actor=people.head, request_id=uuid.uuid4(), work_item_id=item_id
            )
            await session.execute(
                text(
                    "UPDATE pr_work_contributions SET counted_at = :when WHERE work_item_id = :item"
                ),
                {"when": _SEPTEMBER, "item": item_id},
            )
            # The projection ran against ``utcnow`` above; re-run it now that the
            # instant is the one the test means, in the same transaction.
            period = await services.work_periods.require_period(people.period_id)
            contribution = (
                (
                    await session.execute(
                        select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                    )
                )
                .scalars()
                .one()
            )
            await services.work_eligibility.evaluate(user_id=contribution.user_id, period=period)
            await session.commit()
            return "ok"
        except (PrValidationError, PrPermissionDeniedError) as exc:
            await session.rollback()
            return type(exc).__name__


_SEPTEMBER = datetime(YEAR, MONTH, 15, 3, 0, tzinfo=UTC)


async def _allocations(quota_db: Database, user_id: uuid.UUID) -> list[PrWorkQuotaAllocation]:
    async with quota_db.session() as session:
        return list(
            (
                await session.execute(
                    select(PrWorkQuotaAllocation).where(PrWorkQuotaAllocation.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# 0: the precondition
# ---------------------------------------------------------------------------


async def test_the_lock_is_actually_taken_on_postgresql(quota_db: Database) -> None:
    """The precondition every claim below rests on.

    Without it the concurrency tests would pass by not being concurrent.
    """
    async with quota_db.session() as session:
        assert supports_row_locks(session) is True


# ---------------------------------------------------------------------------
# 1: the last ITEM_COUNT slot
# ---------------------------------------------------------------------------


async def test_two_concurrent_countings_do_not_produce_cap_plus_one_eligible(
    quota_db: Database, people: People
) -> None:
    """**The concurrency claim.** A cap of 1, two approvals, one eligible.

    Two transactions each believing one slot remains is precisely what the
    period row lock exists to prevent: the second waits on
    ``SELECT ... FOR UPDATE``, re-reads the allocations the first committed, and
    fills nothing.
    """
    await _plan(
        quota_db,
        people,
        user_id=people.member_id,
        work_type_id=people.item_type_id,
        target="1",
        cap="1",
    )
    first = await _completed(
        quota_db,
        people,
        user_id=people.member_id,
        work_type_id=people.item_type_id,
        title="Kịch bản A",
    )
    second = await _completed(
        quota_db,
        people,
        user_id=people.member_id,
        work_type_id=people.item_type_id,
        title="Kịch bản B",
    )

    outcomes = await asyncio.gather(
        _approve_into_september(quota_db, people, first),
        _approve_into_september(quota_db, people, second),
    )
    assert outcomes == ["ok", "ok"], outcomes

    rows = await _allocations(quota_db, people.member_id)
    assert len(rows) == 2, "one current decision per contribution"
    eligible = [row for row in rows if row.quota_status is PrWorkQuotaStatus.ELIGIBLE]
    assert len(eligible) == 1, [row.quota_status for row in rows]
    assert sum(row.eligible_amount for row in rows) == Decimal("1.00")
    # And both contributions are still COUNTED: quota status changed nothing
    # about whether the work was valid.
    async with quota_db.session() as session:
        counted = await session.scalar(
            select(func.count())
            .select_from(PrWorkContribution)
            .where(
                PrWorkContribution.user_id == people.member_id,
                PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
            )
        )
    assert counted == 2


# ---------------------------------------------------------------------------
# 2: the last QUANTITY units
# ---------------------------------------------------------------------------


async def test_concurrent_quantity_allocation_never_exceeds_the_cap(
    quota_db: Database, people: People
) -> None:
    """60 + 60 against a cap of 100, counted concurrently.

    The eligible amounts must sum to exactly 100 - and the second must be split
    rather than refused wholesale, because all-or-nothing would make two
    employees who did the same 120 comments score differently on how they chose
    to divide the job.
    """
    await _plan(
        quota_db,
        people,
        user_id=people.other_id,
        work_type_id=people.quantity_type_id,
        target="100",
        cap="100",
    )
    first = await _completed(
        quota_db,
        people,
        user_id=people.other_id,
        work_type_id=people.quantity_type_id,
        title="Seeding A",
        quantity=Decimal("60"),
    )
    second = await _completed(
        quota_db,
        people,
        user_id=people.other_id,
        work_type_id=people.quantity_type_id,
        title="Seeding B",
        quantity=Decimal("60"),
    )

    outcomes = await asyncio.gather(
        _approve_into_september(quota_db, people, first),
        _approve_into_september(quota_db, people, second),
    )
    assert outcomes == ["ok", "ok"], outcomes

    rows = await _allocations(quota_db, people.other_id)
    assert len(rows) == 2
    assert sum(row.eligible_amount for row in rows) == Decimal("100.00")
    assert sum(row.over_quota_amount for row in rows) == Decimal("20.00")
    statuses = sorted(row.quota_status.value for row in rows)
    assert statuses == ["ELIGIBLE", "PARTIALLY_ELIGIBLE"], statuses
    for row in rows:
        assert row.basis_amount == row.eligible_amount + row.over_quota_amount


# ---------------------------------------------------------------------------
# 3: two administrators approving at once
# ---------------------------------------------------------------------------


async def test_two_concurrent_plan_approvals_leave_one_plan_in_force(
    quota_db: Database, people: People
) -> None:
    """The partial unique index, not a hopeful check.

    Two administrators press *Duyệt kế hoạch* on the same draft in the same
    instant. One wins on the row lock and the other re-reads ``APPROVED``; if
    both somehow got past that, ``uq_pr_work_plans_approved`` would still refuse
    the second.
    """
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        draft = await services.work_plans.create_plan(
            actor=people.head,
            request_id=uuid.uuid4(),
            user_id=people.lead_id,
            period_id=people.period_id,
        )
        await services.work_plans.add_quota(
            actor=people.head,
            request_id=uuid.uuid4(),
            plan_id=draft.plan.id,
            work_type_id=people.item_type_id,
            target_value=Decimal("5"),
            eligibility_cap=Decimal("5"),
        )
        await session.commit()
        plan_id = draft.plan.id

    async def approve(actor: Actor) -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work_plans.approve(
                    actor=actor, request_id=uuid.uuid4(), plan_id=plan_id
                )
                await session.commit()
                return "ok"
            except (PrWorkPlanStateError, PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(approve(people.head), approve(people.owner))
    assert outcomes.count("ok") == 1, outcomes

    async with quota_db.session() as session:
        in_force = await session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == people.lead_id,
                PrWorkPlan.period_id == people.period_id,
                PrWorkPlan.status == "APPROVED",
            )
        )
        approvals = await session.scalar(
            text(
                "SELECT count(*) FROM audit_logs WHERE action = 'pr.work_plan.approved' "
                "AND entity_id = :plan"
            ),
            {"plan": str(plan_id)},
        )
    assert in_force == 1
    assert approvals == 1, "a refused approval writes no audit row claiming one"


# ---------------------------------------------------------------------------
# 4: two reconciliations at once
# ---------------------------------------------------------------------------


async def test_two_concurrent_reconciliations_produce_one_allocation_each(
    quota_db: Database, people: People
) -> None:
    """Duplicate HTTP retries, or an operator pressing twice.

    ``uq_pr_work_quota_allocations_contribution`` is the guarantee, and the
    period lock is what makes the second call wait rather than fail - so the
    outcome is idempotent rather than merely safe.
    """
    before = {row.work_contribution_id for row in await _allocations(quota_db, people.member_id)}
    assert before, "the earlier test committed allocations this one reconciles"

    async def reconcile() -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work_eligibility.reconcile_period(
                    actor=people.head,
                    request_id=uuid.uuid4(),
                    period_id=people.period_id,
                    user_ids=[people.member_id],
                )
                await session.commit()
                return "ok"
            except Exception as exc:  # pragma: no cover - a failure is the finding
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(reconcile(), reconcile())
    assert outcomes == ["ok", "ok"], outcomes

    after = await _allocations(quota_db, people.member_id)
    assert {row.work_contribution_id for row in after} == before
    assert len(after) == len(before), "a concurrent reconcile appends nothing"
    assert sum(row.eligible_amount for row in after) == Decimal("1.00")


# ---------------------------------------------------------------------------
# 5: the approval survives a failed projection
# ---------------------------------------------------------------------------


async def test_a_failing_projection_does_not_undo_a_committed_approval(
    quota_db: Database, people: People
) -> None:
    """**The savepoint, under a real transaction.**

    Work validation must never depend on a quota engine that happens to be
    working. A projection that raises is rolled back to the savepoint; the
    approval, the ``counted_at`` values, the history rows and the audit row all
    commit, and the next reconcile converges.
    """
    item_id = await _completed(
        quota_db,
        people,
        user_id=people.member_id,
        work_type_id=people.item_type_id,
        title="Kịch bản khi projector hỏng",
    )

    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))

        async def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("projection is down")

        services.work_eligibility.on_contributions_counted = explode  # type: ignore[method-assign]
        await services.work.approve(
            actor=people.head, request_id=uuid.uuid4(), work_item_id=item_id
        )
        await session.commit()

    async with quota_db.session() as session:
        contribution = (
            (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                )
            )
            .scalars()
            .one()
        )
        assert contribution.count_status is PrWorkCountStatus.COUNTED
        assert contribution.counted_at is not None
        approvals = await session.scalar(
            text(
                "SELECT count(*) FROM audit_logs WHERE action = 'pr.work.approved' "
                "AND entity_id = :item"
            ),
            {"item": str(item_id)},
        )
    assert approvals == 1

    # And the module converges: a reconcile picks the contribution up.
    async with quota_db.session() as session:
        await session.execute(
            text("UPDATE pr_work_contributions SET counted_at = :when WHERE work_item_id = :item"),
            {"when": _SEPTEMBER, "item": item_id},
        )
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        outcome = await services.work_eligibility.reconcile_period(
            actor=people.head,
            request_id=uuid.uuid4(),
            period_id=people.period_id,
            user_ids=[people.member_id],
        )
        await session.commit()
    assert outcome.created == 1

    rows = await _allocations(quota_db, people.member_id)
    assert len(rows) == 3
    # The cap is still 1: the recovered contribution is over quota, not extra
    # capacity conjured by the failure.
    assert sum(row.eligible_amount for row in rows) == Decimal("1.00")


# ---------------------------------------------------------------------------
# 6: the quota rows themselves
# ---------------------------------------------------------------------------


async def test_a_revision_supersedes_atomically_under_a_real_transaction(
    quota_db: Database, people: People
) -> None:
    """v1 becomes SUPERSEDED and v2 becomes APPROVED, or neither does.

    Asserted on a database that really commits, because on SQLite "neither
    happened" would be true whatever the service did.
    """
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        current = (
            (
                await session.execute(
                    select(PrWorkPlan).where(
                        PrWorkPlan.user_id == people.member_id,
                        PrWorkPlan.period_id == people.period_id,
                        PrWorkPlan.status == "APPROVED",
                    )
                )
            )
            .scalars()
            .one()
        )
        revision = await services.work_plans.revise(
            actor=people.head, request_id=uuid.uuid4(), plan_id=current.id
        )
        quota = (
            (
                await session.execute(
                    select(PrWorkQuota).where(PrWorkQuota.plan_id == revision.plan.id)
                )
            )
            .scalars()
            .one()
        )
        await services.work_plans.update_quota(
            actor=people.head,
            request_id=uuid.uuid4(),
            plan_id=revision.plan.id,
            quota_id=quota.id,
            target_value=Decimal("3"),
            eligibility_cap=Decimal("3"),
        )
        await services.work_plans.approve(
            actor=people.head, request_id=uuid.uuid4(), plan_id=revision.plan.id
        )
        await session.commit()
        old_id, new_id = current.id, revision.plan.id

    async with quota_db.session() as session:
        statuses = {
            row.id: row.status.value
            for row in (
                await session.execute(select(PrWorkPlan).where(PrWorkPlan.id.in_([old_id, new_id])))
            )
            .scalars()
            .all()
        }
    assert statuses[old_id] == "SUPERSEDED"
    assert statuses[new_id] == "APPROVED"

    # Raising the cap promoted the over-quota work, and the allocations now name
    # the version that decided them.
    rows = await _allocations(quota_db, people.member_id)
    assert sum(row.eligible_amount for row in rows) == Decimal("3.00")
    assert {row.work_plan_id for row in rows} == {new_id}


# ---------------------------------------------------------------------------
# 7: one revision in flight, under a real index
# ---------------------------------------------------------------------------


async def _fresh_employee(quota_db: Database) -> uuid.UUID:
    """One new active employee, committed. **A subject nobody else has used.**

    This module's people are created once and shared, and *"one approved plan"*
    and *"one draft"* are both unique per employee-month - so a lifecycle test
    that borrowed a colleague would collide on the index with whatever an
    earlier test left behind, and fail for a reason that has nothing to do with
    what it is asserting. Each such test gets its own person instead.
    """
    async with quota_db.session() as session:
        row = User(full_name="Nhân sự thử", role=Role.EMPLOYEE)
        session.add(row)
        await session.flush()
        user_id = row.id
        await session.commit()
        return user_id


async def _approved_plan_for(
    quota_db: Database, people: People, *, user_id: uuid.UUID
) -> uuid.UUID:
    """One approved plan for ``user_id``, committed. Where a revision starts from."""
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        draft = await services.work_plans.create_plan(
            actor=people.head,
            request_id=uuid.uuid4(),
            user_id=user_id,
            period_id=people.period_id,
        )
        await services.work_plans.add_quota(
            actor=people.head,
            request_id=uuid.uuid4(),
            plan_id=draft.plan.id,
            work_type_id=people.item_type_id,
            target_value=Decimal("5"),
            eligibility_cap=Decimal("5"),
        )
        await services.work_plans.approve(
            actor=people.head, request_id=uuid.uuid4(), plan_id=draft.plan.id
        )
        await session.commit()
        return draft.plan.id


async def test_two_concurrent_revisions_produce_one_draft(
    quota_db: Database, people: People
) -> None:
    """**The race the hidden button does not settle.**

    Hiding *Tạo bản điều chỉnh* while a draft exists is a courtesy - two
    browsers can both have rendered before either clicked. ``uq_pr_work_plans_draft``
    is the rule, and it is a partial unique index rather than a service check,
    so the second transaction is refused by PostgreSQL rather than by whichever
    of the two happened to read first.

    The loser gets ``draft_already_exists`` with the plan id it collided with,
    which is what lets a screen open the existing revision instead of dead-ending.
    """
    subject = await _fresh_employee(quota_db)
    plan_id = await _approved_plan_for(quota_db, people, user_id=subject)

    async def revise(actor: Actor) -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work_plans.revise(
                    actor=actor, request_id=uuid.uuid4(), plan_id=plan_id
                )
                await session.commit()
                return "ok"
            except (PrWorkPlanStateError, PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__
            except IntegrityError:
                # The index, when both transactions got past the read.
                await session.rollback()
                return "IntegrityError"

    outcomes = await asyncio.gather(revise(people.head), revise(people.owner))
    assert outcomes.count("ok") == 1, outcomes

    async with quota_db.session() as session:
        drafts = await session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == subject,
                PrWorkPlan.period_id == people.period_id,
                PrWorkPlan.status == "DRAFT",
            )
        )
    assert drafts == 1


async def test_approve_and_discard_of_one_draft_cannot_both_win(
    quota_db: Database, people: People
) -> None:
    """One draft, two decisions, one outcome.

    *Duyệt* and *Bỏ bản nháp* are both offered on the draft panel, and two
    administrators can press them at the same instant. Both take the row lock in
    ``_lock_plan``; the loser re-reads a status that is no longer ``DRAFT`` and
    is refused. Whatever happens, the plan ends in exactly one terminal state and
    the version is not both approved and abandoned.
    """
    subject = await _fresh_employee(quota_db)
    plan_id = await _approved_plan_for(quota_db, people, user_id=subject)
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        revision = await services.work_plans.revise(
            actor=people.head, request_id=uuid.uuid4(), plan_id=plan_id
        )
        await session.commit()
        draft_id = revision.plan.id

    async def act(kind: str) -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                if kind == "approve":
                    await services.work_plans.approve(
                        actor=people.head, request_id=uuid.uuid4(), plan_id=draft_id
                    )
                else:
                    await services.work_plans.discard(
                        actor=people.owner, request_id=uuid.uuid4(), plan_id=draft_id
                    )
                await session.commit()
                return "ok"
            except (PrWorkPlanStateError, PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(act("approve"), act("discard"))
    assert outcomes.count("ok") == 1, outcomes

    async with quota_db.session() as session:
        row = await session.get(PrWorkPlan, draft_id)
        assert row is not None
        # Exactly one transition happened, and it left a terminal-or-in-force
        # status rather than a draft somebody could act on twice.
        assert row.status in {"APPROVED", "DISCARDED"}
        # And the two lifecycle stamps are never both set.
        assert (row.approved_at is None) != (row.discarded_at is None)
        drafts = await session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == subject,
                PrWorkPlan.period_id == people.period_id,
                PrWorkPlan.status == "DRAFT",
            )
        )
    assert drafts == 0


async def test_a_discarded_draft_frees_the_slot_for_the_next_revision(
    quota_db: Database, people: People
) -> None:
    """Part Y, on the real index. The slot reopens; the number does not.

    ``uq_pr_work_plans_draft`` is partial, so a discarded row stops occupying the
    one-draft slot - and ``uq_pr_work_plans_user_period_version`` still forbids
    reusing v2, because a version number is a historical identifier and an
    allocation explained by "v2" has to keep meaning one plan.
    """
    subject = await _fresh_employee(quota_db)
    plan_id = await _approved_plan_for(quota_db, people, user_id=subject)
    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        first = await services.work_plans.revise(
            actor=people.head, request_id=uuid.uuid4(), plan_id=plan_id
        )
        await services.work_plans.discard(
            actor=people.head, request_id=uuid.uuid4(), plan_id=first.plan.id
        )
        await session.commit()
        discarded_version = first.plan.version_no

    async with quota_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        second = await services.work_plans.revise(
            actor=people.head, request_id=uuid.uuid4(), plan_id=plan_id
        )
        await session.commit()
        assert second.plan.version_no == discarded_version + 1

    async with quota_db.session() as session:
        drafts = await session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == subject,
                PrWorkPlan.period_id == people.period_id,
                PrWorkPlan.status == "DRAFT",
            )
        )
        discarded = await session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == subject,
                PrWorkPlan.period_id == people.period_id,
                PrWorkPlan.status == "DISCARDED",
            )
        )
    assert drafts == 1
    assert discarded == 1


# ---------------------------------------------------------------------------
# 8: one creation path per state, under a real index
# ---------------------------------------------------------------------------


async def test_two_concurrent_first_plans_produce_one_initial_draft(
    quota_db: Database, people: People
) -> None:
    """Case A, raced. Two administrators start the same employee's first plan.

    The service check reads before either has written, so both can pass it -
    which is exactly why the guarantee is ``uq_pr_work_plans_draft`` and not the
    check. One insert wins; the other is refused by PostgreSQL.
    """

    subject = await _fresh_employee(quota_db)

    async def create(actor: Actor) -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work_plans.create_plan(
                    actor=actor,
                    request_id=uuid.uuid4(),
                    user_id=subject,
                    period_id=people.period_id,
                )
                await session.commit()
                return "ok"
            except (PrWorkPlanStateError, PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__
            except IntegrityError:
                await session.rollback()
                return "IntegrityError"

    outcomes = await asyncio.gather(create(people.head), create(people.owner))
    assert outcomes.count("ok") == 1, outcomes

    async with quota_db.session() as session:
        drafts = await session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == subject,
                PrWorkPlan.period_id == people.period_id,
                PrWorkPlan.status == "DRAFT",
            )
        )
    assert drafts == 1


async def test_revise_and_create_plan_from_an_approved_state_cannot_both_win(
    quota_db: Database, people: People
) -> None:
    """**The invariant this patch adds, raced.**

    Two browsers both showing an approved v3 with no draft. One presses *Tạo bản
    điều chỉnh*, the other - stale, or reaching for the wrong control - calls
    ``create_plan``. Exactly one draft may exist afterwards, and it must not be
    the **empty** one: an empty alternate revision under an approved plan is the
    production shape this whole patch exists to make unreachable.

    Whichever transaction is refused, the surviving draft is a real revision -
    it supersedes the plan in force and carries its quotas across.
    """
    subject = await _fresh_employee(quota_db)
    plan_id = await _approved_plan_for(quota_db, people, user_id=subject)

    async def revise() -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work_plans.revise(
                    actor=people.head, request_id=uuid.uuid4(), plan_id=plan_id
                )
                await session.commit()
                return "revise"
            except (PrWorkPlanStateError, PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__
            except IntegrityError:
                await session.rollback()
                return "IntegrityError"

    async def create() -> str:
        async with quota_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work_plans.create_plan(
                    actor=people.owner,
                    request_id=uuid.uuid4(),
                    user_id=subject,
                    period_id=people.period_id,
                )
                await session.commit()
                return "create"
            except (PrWorkPlanStateError, PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__
            except IntegrityError:
                await session.rollback()
                return "IntegrityError"

    outcomes = await asyncio.gather(revise(), create())
    # `create_plan` is refused from this state whatever the timing, so the
    # revision is the only call that can succeed.
    assert "create" not in outcomes, outcomes
    assert outcomes.count("revise") == 1, outcomes

    async with quota_db.session() as session:
        drafts = (
            (
                await session.execute(
                    select(PrWorkPlan).where(
                        PrWorkPlan.user_id == subject,
                        PrWorkPlan.period_id == people.period_id,
                        PrWorkPlan.status == "DRAFT",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(drafts) == 1
        draft = drafts[0]
        # A real revision, not an empty alternate.
        assert draft.supersedes_plan_id == plan_id
        quotas = await session.scalar(
            select(func.count()).select_from(PrWorkQuota).where(PrWorkQuota.plan_id == draft.id)
        )
        assert quotas and quotas > 0

        # And the plan in force is untouched by the refused call.
        in_force = await session.get(PrWorkPlan, plan_id)
        assert in_force is not None
        assert in_force.status == "APPROVED"
        assert in_force.superseded_at is None
