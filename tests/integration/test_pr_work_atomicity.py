"""M1 on a real PostgreSQL: validation counts whole, once, or not at all.

The offline suite proves the *rules* - the ladder, the two self-approval
refusals, the five figures. It cannot prove the two claims the milestone is
actually built on, because the unit fixture is SQLite:

* **the lock is real.** ``lock_row`` degrades to a plain ``get`` off PostgreSQL,
  deliberately - SQLite has no ``FOR UPDATE`` and serialises writers anyway - so
  every offline test of "two validators race" is a test of a code path that did
  not lock. Here it does;
* **the transaction really commits.** SQLite in the unit fixture rolls back, so
  "nothing was counted" is true there whatever the service did. Here a refused
  approval has to leave ``count_status``, ``counted_at``, the history table and
  the audit trail exactly as they were.

Four scenarios, each one of M1's promises:

#. two validators approving the same item **concurrently** produce one approval,
   one set of ``counted_at`` values, and one ``COUNTED`` history row per person.
   The loser is refused;
#. a three-person job commits **one** approved item and **three** counted
   contributions, all carrying the same instant - so a shoot cannot land in two
   months because two writes straddled midnight;
#. a contributor's own approval is refused and **writes nothing** - not a
   partial approval, not an audit row claiming one;
#. the ledger commits without touching ``pr_tasks`` or ``pr_content_items``.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_atomicity.py -m integration

The fixture creates its own uniquely-named ``meobot_workatomic_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_support import supports_row_locks
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrContentItem, PrTask
from meobot.db.models.pr_work import PrWorkContribution, PrWorkHistory, PrWorkItem
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrPermissionDeniedError, PrValidationError
from meobot.domain.pr.work import PrWorkCategory, PrWorkCountStatus, PrWorkStatus
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def work_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_workatomic_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        # **head**, not ``"0032"``. This suite exercises the M1 *services*
        # against the current ORM models, and a model gains columns as later
        # revisions add them - M2's ``pr_work_types.default_quota_basis`` is the
        # first. Pinning the schema one revision behind the models makes every
        # query select a column that is not there. The migration *boundary* is
        # ``test_pr_work_core_migrations.py``'s job, and it still stops at 0032
        # deliberately.
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
    """Four people, and a work type. Created once and reused by every test."""

    def __init__(self) -> None:
        self.owner_id: uuid.UUID
        self.head_id: uuid.UUID
        self.lead_id: uuid.UUID
        self.member_id: uuid.UUID
        self.other_id: uuid.UUID
        self.work_type_id: uuid.UUID

    def actor(self, user_id: uuid.UUID, role: Role) -> Actor:
        return Actor(user_id=user_id, full_name="Người thử", role=role)

    @property
    def head(self) -> Actor:
        return self.actor(self.head_id, Role.ADMIN)

    @property
    def lead(self) -> Actor:
        return self.actor(self.lead_id, Role.TEAM_LEAD)

    @property
    def member(self) -> Actor:
        return self.actor(self.member_id, Role.EMPLOYEE)

    @property
    def other(self) -> Actor:
        return self.actor(self.other_id, Role.EMPLOYEE)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def people(work_db: Database) -> People:
    world = People()
    async with work_db.session() as session:
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
        work_type = await services.work.create_work_type(
            actor=world.actor(world.owner_id, Role.OWNER),
            request_id=uuid.uuid4(),
            code="HALF_DAY_SHOOT",
            name="Quay nửa buổi",
            category=PrWorkCategory.PRODUCTION,
        )
        world.work_type_id = work_type.id
        await session.commit()
    return world


async def _assign(
    work_db: Database, people: People, *, contributors: list[uuid.UUID], title: str
) -> uuid.UUID:
    """Assigned, started and completed - the state a validator sees. Committed."""
    async with work_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        item = await services.work.assign_work(
            actor=people.head,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=people.work_type_id,
                title=title,
                contributor_user_ids=tuple(contributors),
            ),
        )
        doer = people.actor(contributors[0], Role.EMPLOYEE)
        await services.work.complete(actor=doer, request_id=uuid.uuid4(), work_item_id=item.id)
        await session.commit()
        return item.id


# ---------------------------------------------------------------------------
# 1: the lock is real
# ---------------------------------------------------------------------------


async def test_the_lock_is_actually_taken_on_postgresql(work_db: Database) -> None:
    """The precondition every claim below rests on.

    Without it the concurrency test would pass by not being concurrent.
    """
    async with work_db.session() as session:
        assert supports_row_locks(session) is True


async def test_two_validators_racing_count_exactly_once(work_db: Database, people: People) -> None:
    """**The concurrency claim.** One approval, one set of times, one history row.

    Two sessions, two validators, one item, ``asyncio.gather``. The second waits
    on ``SELECT ... FOR UPDATE``, re-reads ``APPROVED``, and is refused by the
    transition matrix - so ``counted_at`` is written once and the timeline
    records one ``COUNTED`` per person rather than two.
    """
    item_id = await _assign(work_db, people, contributors=[people.member_id], title="Đua xác nhận")

    async def approve(actor: Actor) -> str:
        async with work_db.session() as session:
            services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
            try:
                await services.work.approve(
                    actor=actor, request_id=uuid.uuid4(), work_item_id=item_id
                )
                await session.commit()
                return "ok"
            except (PrValidationError, PrPermissionDeniedError) as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(approve(people.head), approve(people.lead))
    assert outcomes.count("ok") == 1, outcomes

    async with work_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].count_status is PrWorkCountStatus.COUNTED
        assert rows[0].counted_at is not None

        counted_events = await session.scalar(
            select(func.count())
            .select_from(PrWorkHistory)
            .where(
                PrWorkHistory.work_item_id == item_id,
                PrWorkHistory.event_type == "COUNTED",
            )
        )
        assert counted_events == 1

        approvals = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == str(item_id), AuditLog.action == "pr.work.approved")
        )
        assert approvals == 1


# ---------------------------------------------------------------------------
# 2: one job, three credits, one instant
# ---------------------------------------------------------------------------


async def test_a_three_person_job_commits_one_item_and_three_credits(
    work_db: Database, people: People
) -> None:
    """The two-count rule, committed and read back from a fresh session.

    All three ``counted_at`` values are the **same instant**: the approval takes
    one clock reading and stamps it everywhere, so no contributor on one job can
    land in a different reporting period from another.
    """
    item_id = await _assign(
        work_db,
        people,
        contributors=[people.member_id, people.other_id, people.lead_id],
        title="Quay TVC ba người",
    )
    async with work_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        await services.work.approve(
            actor=people.head, request_id=uuid.uuid4(), work_item_id=item_id
        )
        await session.commit()

    async with work_db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        assert item is not None
        assert item.status is PrWorkStatus.APPROVED

        rows = (
            (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert all(row.count_status is PrWorkCountStatus.COUNTED for row in rows)
        assert len({row.counted_at for row in rows}) == 1
        assert rows[0].counted_at == item.approved_at


# ---------------------------------------------------------------------------
# 3: a refused approval writes nothing
# ---------------------------------------------------------------------------


async def test_a_contributors_own_approval_writes_nothing_at_all(
    work_db: Database, people: People
) -> None:
    """**The anti-gaming claim, committed.**

    A ``TEAM_LEAD`` who did the work holds ``PR_WORK_VALIDATE`` and is refused.
    What matters here more than the refusal is what is left behind: the item is
    still ``COMPLETED``, the contribution is still ``PENDING``, and the audit
    trail has no row claiming an approval happened.
    """
    item_id = await _assign(work_db, people, contributors=[people.lead_id], title="Tự xác nhận")
    async with work_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        with pytest.raises(PrPermissionDeniedError) as caught:
            await services.work.approve(
                actor=people.lead, request_id=uuid.uuid4(), work_item_id=item_id
            )
        assert caught.value.details["reason"] == "self_validation"
        await session.rollback()

    async with work_db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        assert item is not None
        assert item.status is PrWorkStatus.COMPLETED
        assert item.approved_at is None

        row = (
            await session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
            )
        ).scalar_one()
        assert row.count_status is PrWorkCountStatus.PENDING
        assert row.counted_at is None

        approvals = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == str(item_id), AuditLog.action == "pr.work.approved")
        )
        assert approvals == 0


# ---------------------------------------------------------------------------
# 4: the ledger is additive
# ---------------------------------------------------------------------------


async def test_the_committed_ledger_never_touched_tasks_or_content(
    work_db: Database, people: People
) -> None:
    """M1's risk profile, on a database where the writes really landed."""
    async with work_db.session() as session:
        work_items = await session.scalar(select(func.count()).select_from(PrWorkItem))
        tasks = await session.scalar(select(func.count()).select_from(PrTask))
        content = await session.scalar(select(func.count()).select_from(PrContentItem))

    assert work_items is not None and work_items >= 3
    assert tasks == 0
    assert content == 0
