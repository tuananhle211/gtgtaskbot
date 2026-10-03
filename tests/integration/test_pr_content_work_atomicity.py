"""Content → Work on a real PostgreSQL: the projector converges under concurrency.

**Result grain, since ``0039``; provisioning, since ``0040``.** A content
milestone no longer becomes a work item of its own. It becomes one
``pr_work_results`` row - unique on ``(source_type, source_key)`` - inside the
contributor's monthly container, and the container's single contribution is
what M2 and M6 read. A content type nobody has mapped is bound by the projector
itself: the work type, the rule and the first result are written in **one**
transaction. Every claim below is about that shape.

The offline suite proves the *rules*. It cannot prove what a real database
proves, because the unit fixture is SQLite in one rolled-back transaction:

* **two workers projecting the same content produce one result.** Offline,
  "run it twice" is two sequential calls where the second reads what the first
  wrote. Here they are two connections racing on ``uq_pr_work_results_source``;
* **two workers meeting one unseen content type produce one work type and one
  rule**, and each their own result - the race on ``pr_work_types.code`` and
  ``uq_pr_content_work_rules_kind_type``, absorbed inside a ``SAVEPOINT``. And
  the container race underneath: both workers open the writer's month at once,
  and the loser's transaction must survive it rather than abort;
* **a refusal writes nothing.** SQLite rolls the whole test back, so "nothing
  was half-provisioned" is true there whatever the service did. Here the
  surrounding transaction really commits or really rolls back, and a work type
  or rule that leaked past a refused result would be visible to the next
  connection.

Scenarios:

#. the lock is real - the precondition everything below rests on;
#. two concurrent projections of one approved script commit **one** result,
   **one** container and **one** COUNTED contribution;
#. eight concurrent projection *requests* leave **one** queue row;
#. undo and redo across real commits leave one result, counted once, at the
   redo's instant - and the container's actual follows it down and back up;
#. a reversal into a ``CLOSED`` month commits **nothing**;
#. the projection commits its M2 eligibility handoff in the **same**
   transaction;
#. the first piece of an unseen content type provisions the type, the rule and
   the result in one commit, and a retry changes nothing;
#. two pieces of the same unseen type racing produce one type, one rule, two
   results and a container of two - both workers succeed;
#. three concurrent replays of a settled result change nothing;
#. a projection that fails **after** provisioning rolls the type and the rule
   back with the result - no debris;
#. a first result refused by a ``CLOSED`` month leaves no debris either.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_content_work_atomicity.py -m integration

The fixture creates its own uniquely-named ``meobot_cworkatomic_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_content_work_projector import request_content_work_projection
from meobot.application.pr_services import build_pr_services
from meobot.application.pr_support import supports_row_locks
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrBrand, PrPlatform
from meobot.db.models.pr_content_work import PrContentWorkProjection, PrContentWorkRule
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_work import (
    PrContentWorkKind,
    PrContentWorkOutcome,
    auto_work_type_code,
    content_work_source_key,
)
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelCategory,
    PrContentType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCategory, PrWorkCountStatus
from meobot.domain.pr.work_quota import PrWorkQuotaStatus
from meobot.domain.pr.work_results import PrWorkResultSource
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


def settings() -> Settings:
    return Settings(**SETTINGS_KWARGS)  # type: ignore[arg-type]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db() -> AsyncIterator[Database]:
    """A scratch database at **head**, dropped afterwards.

    Head rather than ``"0034"``: this suite drives the *services* against the
    current ORM models, and pinning the schema to one revision would make every
    query select whatever a later milestone has since added. The migration
    boundary is ``test_pr_content_work_migrations.py``'s job.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_cworkatomic_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn)
        database = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))  # type: ignore[arg-type]
        try:
            yield database
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@dataclass
class People:
    """Five people, a brand, a channel and a work type. Ids only.

    Ids rather than ORM rows, deliberately: every test here opens its own
    session, and an instance attached to a closed one would either raise on
    first access or silently re-query.
    """

    owner: uuid.UUID
    lead: uuid.UUID
    head: uuid.UUID
    member: uuid.UUID
    other: uuid.UUID
    brand: uuid.UUID
    channel: uuid.UUID
    work_type: uuid.UUID

    def actor(self, user_id: uuid.UUID, role: Role = Role.EMPLOYEE) -> Actor:
        return Actor(user_id=user_id, full_name="Người thử", role=role)

    @property
    def owner_actor(self) -> Actor:
        return self.actor(self.owner, Role.OWNER)

    @property
    def head_actor(self) -> Actor:
        return self.actor(self.head, Role.ADMIN)

    @property
    def lead_actor(self) -> Actor:
        return self.actor(self.lead, Role.TEAM_LEAD)

    @property
    def member_actor(self) -> Actor:
        return self.actor(self.member)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def people(db: Database) -> People:
    """Built once, committed, and shared. Every test writes its own content."""
    async with db.session() as session:
        rows = {
            "owner": User(full_name="Chị Chủ", role=Role.OWNER),
            "lead": User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD),
            "head": User(full_name="Hà Trưởng Phòng", role=Role.ADMIN),
            "member": User(full_name="Phương Nhung", role=Role.EMPLOYEE),
            "other": User(full_name="Nguyễn A", role=Role.EMPLOYEE),
        }
        brand = PrBrand(code="BRND-A", name="Apexmed")
        # WEBSITE, not FACEBOOK: the policy-grounded platforms block AI review
        # until a distribution mode is set, which is a 1F.1 rule with its own
        # tests and nothing to do with projection.
        platform = PrPlatform(code="WEBSITE", name="Website")
        session.add_all([*rows.values(), brand, platform])
        await session.flush()

        services = build_pr_services(session, settings())
        granter = Actor(user_id=rows["owner"].id, full_name="Chị Chủ", role=Role.OWNER)
        for user, capability in (
            (rows["lead"], PrCapability.PR_TEAM_LEAD_REVIEW),
            (rows["head"], PrCapability.PR_HEAD_REVIEW),
            # The writer holds the head gate too, so the self-approval scenario
            # is reachable - it is the one M3 turns on. ``PR_WORK_VALIDATE`` is
            # deliberately absent from this list: it is decided by role, not
            # granted, and asking for it is refused.
            (rows["member"], PrCapability.PR_HEAD_REVIEW),
        ):
            await services.capabilities.grant(
                actor=granter, request_id=uuid.uuid4(), user_id=user.id, capability=capability
            )

        channel = await services.channels.create_channel(
            actor=granter,
            request_id=uuid.uuid4(),
            command=CreateChannelCommand(
                name="Apexmed Website",
                platform_id=platform.id,
                brand_id=brand.id,
                category=PrChannelCategory.SCALE,
            ),
        )
        work_type = await services.work.create_work_type(
            actor=granter,
            request_id=uuid.uuid4(),
            code="SHORT_SCRIPT",
            name="Kịch bản ngắn",
            category=PrWorkCategory.CONTENT,
        )
        # An **exact** rule for short-video scripts, and deliberately not the
        # kind's default: every other content type is then *unseen*, which is
        # what the provisioning scenarios need, while the explicit mapping
        # keeps meaning what it always meant for the scenarios that predate
        # them.
        await services.content_work_rules.upsert_rule(
            actor=granter,
            request_id=uuid.uuid4(),
            contribution_kind=PrContentWorkKind.CONTENT_CREATION,
            content_type=PrContentType.SHORT_VIDEO_SCRIPT,
            work_type_id=work_type.id,
        )
        built = People(
            owner=rows["owner"].id,
            lead=rows["lead"].id,
            head=rows["head"].id,
            member=rows["member"].id,
            other=rows["other"].id,
            brand=brand.id,
            channel=channel.id,
            work_type=work_type.id,
        )
        await session.commit()
    return built


# --- Walking a piece of content to APPROVED, committed ----------------------


async def _decide(
    services: object,
    *,
    actor: Actor,
    content_id: uuid.UUID,
    stage: PrApprovalStage,
) -> None:
    """One human decision at one gate, through the real approval service.

    The reviewer is the actor, always. That equality is what the self-approval
    tests turn on, and letting a caller pass a different reviewer id here would
    make it possible to write the very scenario M3 is meant to catch.
    """
    await services.approvals.record_decision(  # type: ignore[attr-defined]
        actor=actor,
        request_id=uuid.uuid4(),
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=actor.user_id,
            approval_stage=stage,
            decision=PrApprovalDecision.APPROVED,
            version_reviewed=1,
        ),
    )


async def approved_content(
    db: Database,
    people: People,
    *,
    title: str,
    head: uuid.UUID | None = None,
    content_type: PrContentType = PrContentType.SHORT_VIDEO_SCRIPT,
    writer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """One content item at ``APPROVED``, through the real workflow. **Committed.**

    ``head`` chooses who takes the final gate, which is how the self-approval
    case puts the writer on both sides of it. ``content_type`` chooses whether
    the piece meets the fixture's explicit mapping (short-video scripts) or an
    **unseen** type the projector has to provision for.
    """
    async with db.session() as session:
        services = build_pr_services(session, settings())
        writer = people.actor(writer_id) if writer_id is not None else people.member_actor
        snapshot = await services.content.create_content(
            actor=writer,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=people.brand,
                owner_user_id=writer_id or people.member,
                content_type=content_type,
                script_text="Nội dung.",
                targets=(ContentTargetSpec(channel_id=people.channel),),
            ),
        )
        content_id = snapshot.content.id
        for stage in (
            PrWorkflowStage.BRIEFING,
            PrWorkflowStage.SCRIPTING,
            PrWorkflowStage.AI_REVIEW,
        ):
            await services.workflow.request_transition(
                actor=people.owner_actor,
                request_id=uuid.uuid4(),
                content_id=content_id,
                target=stage,
            )
        await services.ai_reviews.record_review(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            command=RecordAiReviewCommand(
                content_id=content_id,
                reviewed_version=1,
                review_type=PrAiReviewType.FULL_REVIEW,
                result=PrAiReviewResult.PASS,
                model_name="claude-opus-5",
                prompt_version="p@1",
                reviewed_at=utcnow(),
            ),
        )
        await _decide(
            services,
            actor=people.lead_actor,
            content_id=content_id,
            stage=PrApprovalStage.TEAM_LEAD_REVIEW,
        )
        reviewer = people.actor(head, Role.EMPLOYEE) if head is not None else people.head_actor
        await _decide(
            services, actor=reviewer, content_id=content_id, stage=PrApprovalStage.HEAD_REVIEW
        )
        await session.commit()
        return content_id


async def project_once(db: Database, people: People, content_id: uuid.UUID) -> object:
    """One convergence run in its own transaction. **Committed.**"""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        report = await services.content_work.project_content(
            actor=people.owner_actor, request_id=uuid.uuid4(), content_id=content_id
        )
        await session.commit()
        return report


async def open_month(db: Database, people: People, at: datetime) -> uuid.UUID:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        local = at.astimezone(settings().timezone)
        period = await services.work_periods.ensure_month_period(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            year=local.year,
            month=local.month,
        )
        period_id = period.id
        await session.commit()
        return period_id


async def approve_plan(
    db: Database,
    people: People,
    *,
    period_id: uuid.UUID,
    user_id: uuid.UUID,
    target: Decimal = Decimal("5"),
    cap: Decimal = Decimal("5"),
) -> uuid.UUID:
    """An approved KPI plan with one quota, committed. **The administrator flow.**

    Needed by the handoff test because M2's incremental hook materialises an
    allocation only when a plan is in force: with no approved plan there is
    nothing to evaluate *against*, and writing a row would claim an assessment
    nobody made. See ``PrWorkQuotaEligibilityService.on_contributions_counted``.
    """
    async with db.session() as session:
        services = build_pr_services(session, settings())
        actor = people.owner_actor
        detail = await services.work_plans.create_plan(
            actor=actor, request_id=uuid.uuid4(), user_id=user_id, period_id=period_id
        )
        await services.work_plans.add_quota(
            actor=actor,
            request_id=uuid.uuid4(),
            plan_id=detail.plan.id,
            work_type_id=people.work_type,
            target_value=target,
            eligibility_cap=cap,
        )
        approved = await services.work_plans.approve(
            actor=actor, request_id=uuid.uuid4(), plan_id=detail.plan.id
        )
        plan_id = approved.plan.id
        await session.commit()
        return plan_id


async def _allocations_for(
    session: AsyncSession, contribution_id: uuid.UUID
) -> list[PrWorkQuotaAllocation]:
    return list(
        (
            await session.execute(
                select(PrWorkQuotaAllocation).where(
                    PrWorkQuotaAllocation.work_contribution_id == contribution_id
                )
            )
        )
        .scalars()
        .all()
    )


async def _results_for(session: AsyncSession, content_id: uuid.UUID) -> list[PrWorkResult]:
    """Every result the content-creation milestone of this piece produced.

    The idempotency contract at result grain: ``(CONTENT, source_key)`` is
    unique over ``pr_work_results``, so the honest count is one or zero.
    """
    key = content_work_source_key(PrContentWorkKind.CONTENT_CREATION, content_id)
    return list(
        (
            await session.execute(
                select(PrWorkResult).where(
                    PrWorkResult.source_type == PrWorkResultSource.CONTENT,
                    PrWorkResult.source_key == key,
                )
            )
        )
        .scalars()
        .all()
    )


async def _one_result(session: AsyncSession, content_id: uuid.UUID) -> PrWorkResult:
    rows = await _results_for(session, content_id)
    assert len(rows) == 1, [str(one.id) for one in rows]
    return rows[0]


async def _container(session: AsyncSession, result: PrWorkResult) -> PrWorkItem:
    item = await session.get(PrWorkItem, result.work_item_id)
    assert item is not None and item.is_period_container
    return item


async def _contributions_of(session: AsyncSession, item_id: uuid.UUID) -> list[PrWorkContribution]:
    return list(
        (
            await session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
            )
        )
        .scalars()
        .all()
    )


async def _consistent(session: AsyncSession, content_id: uuid.UUID) -> PrWorkItem:
    """The invariant every settled COUNTED result must satisfy, checked in one place.

    One result, COUNTED with an instant; its container's ``quantity`` is the
    sum of that container's COUNTED results; and the container's single
    contribution is COUNTED with the earliest counted instant - which is what
    M2 allocates and M6 prices. A result that was counted without its
    contribution, or a contribution counted at a different instant, is the
    inconsistency the whole ledger is built to make impossible.
    """
    result = await _one_result(session, content_id)
    assert result.status is PrWorkCountStatus.COUNTED and result.counted_at is not None
    item = await _container(session, result)
    counted_sum = await session.scalar(
        select(func.coalesce(func.sum(PrWorkResult.quantity), 0)).where(
            PrWorkResult.work_item_id == item.id,
            PrWorkResult.status == PrWorkCountStatus.COUNTED,
        )
    )
    assert item.quantity == counted_sum
    earliest = await session.scalar(
        select(func.min(PrWorkResult.counted_at)).where(
            PrWorkResult.work_item_id == item.id,
            PrWorkResult.status == PrWorkCountStatus.COUNTED,
        )
    )
    rows = await _contributions_of(session, item.id)
    assert len(rows) == 1
    assert rows[0].user_id == item.subject_user_id
    assert rows[0].count_status is PrWorkCountStatus.COUNTED
    assert rows[0].counted_at == earliest
    return item


async def _auto_rows(
    session: AsyncSession, content_type: PrContentType
) -> tuple[list[PrWorkType], list[PrContentWorkRule]]:
    """The provisioned work type(s) and rule(s) for one content type - ideally one each."""
    code = auto_work_type_code(PrContentWorkKind.CONTENT_CREATION, content_type)
    types = list(
        (await session.execute(select(PrWorkType).where(PrWorkType.code == code))).scalars().all()
    )
    rules = list(
        (
            await session.execute(
                select(PrContentWorkRule).where(
                    PrContentWorkRule.contribution_kind == PrContentWorkKind.CONTENT_CREATION,
                    PrContentWorkRule.content_type == content_type,
                )
            )
        )
        .scalars()
        .all()
    )
    return types, rules


async def _no_debris(
    session: AsyncSession, content_id: uuid.UUID, content_type: PrContentType
) -> None:
    """Nothing of a refused run survived: no type, no rule, no result."""
    types, rules = await _auto_rows(session, content_type)
    assert types == [], "a work type leaked past a refused projection"
    assert rules == [], "a mapping leaked past a refused projection"
    assert await _results_for(session, content_id) == []


async def project_expecting_failure(db: Database, people: People, content_id: uuid.UUID) -> str:
    """One run that is expected to raise, rolled back the way the worker rolls it back."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.content_work.project_content(
                actor=people.owner_actor, request_id=uuid.uuid4(), content_id=content_id
            )
        except PrValidationError as error:
            await session.rollback()
            return str(error.details.get("reason"))
        await session.commit()
        return "committed"


async def set_user_active(db: Database, user_id: uuid.UUID, active: bool) -> None:
    async with db.session() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.active = active
        await session.commit()


async def set_period_status(db: Database, period_id: uuid.UUID, status: PrPeriodStatus) -> None:
    async with db.session() as session:
        period = await session.get(PrReportingPeriod, period_id)
        assert period is not None
        period.status = status
        await session.commit()


# ---------------------------------------------------------------------------
# 1: the lock is real
# ---------------------------------------------------------------------------


async def test_the_lock_is_actually_taken_on_postgresql(db: Database) -> None:
    """The precondition every claim below rests on.

    ``lock_row`` degrades to a plain ``get`` off PostgreSQL - deliberately,
    since SQLite has no ``FOR UPDATE`` and serialises writers anyway. Without
    this assertion, the concurrency tests would pass by not being concurrent.
    """
    async with db.session() as session:
        assert supports_row_locks(session) is True


# ---------------------------------------------------------------------------
# 2: two workers, one result
# ---------------------------------------------------------------------------


async def test_two_concurrent_projections_produce_one_result(db: Database, people: People) -> None:
    """The claim the source key exists for.

    Two connections converge the same approved script at the same moment. The
    projector is state-convergent rather than event-driven, so **both may
    legitimately decide the result should exist** - and what stands between
    that and a doubled KPI is ``uq_pr_work_results_source``. The loser's insert
    fails inside a savepoint and it continues with the winner's row, so **both
    runs commit** rather than one of them poisoning its transaction.
    """
    content_id = await approved_content(db, people, title="Chạy song song")

    results = await asyncio.gather(
        project_once(db, people, content_id),
        project_once(db, people, content_id),
        return_exceptions=True,
    )
    raised = [one for one in results if isinstance(one, BaseException)]
    assert raised == [], raised

    async with db.session() as session:
        item = await _consistent(session, content_id)
        assert item.work_type_id == people.work_type, "the explicit mapping, not a provisioned type"
        assert item.quantity == Decimal("1.00")
        assert len(await _contributions_of(session, item.id)) == 1


# ---------------------------------------------------------------------------
# 3: many requests, one queue row
# ---------------------------------------------------------------------------


async def test_concurrent_projection_requests_leave_one_queue_row(
    db: Database, people: People
) -> None:
    """A piece that moves eight times in a minute enqueues one job.

    ``request_content_work_projection`` runs inside the content transaction and
    must be total: it cannot raise, because raising would roll back the approval
    that called it. Eight concurrent callers is the shape that finds an
    ``ON CONFLICT`` that was never really tested.
    """
    content_id = await approved_content(db, people, title="Xin chiếu nhiều lần")

    async def request() -> None:
        async with db.session() as session:
            await request_content_work_projection(session, content_id)
            await session.commit()

    outcomes = await asyncio.gather(*(request() for _ in range(8)), return_exceptions=True)
    raised = [one for one in outcomes if isinstance(one, BaseException)]
    assert raised == [], raised

    async with db.session() as session:
        rows = await session.scalar(
            select(func.count())
            .select_from(PrContentWorkProjection)
            .where(PrContentWorkProjection.content_id == content_id)
        )
        assert rows == 1


# ---------------------------------------------------------------------------
# 4: undo and redo across real commits
# ---------------------------------------------------------------------------


async def test_undo_then_redo_leaves_one_result_counted_once(db: Database, people: People) -> None:
    """The same job, taken back and put back. **Across commits.**

    Offline this is one transaction that rolls back at the end, so "the result
    came back ``COUNTED``" proves only that an in-memory object was mutated.
    Here every step commits: the container's actual has to follow the result
    down to zero and back to one, and the redo's ``counted_at`` has to be the
    **new** approval's instant - a stale one would file the work in whichever
    month the first, retracted approval happened to fall in.
    """
    content_id = await approved_content(db, people, title="Rút lại rồi làm lại")
    await project_once(db, people, content_id)

    # The writer's September stream is shared with the module's other tests,
    # so the actual is asserted as a **delta** on it rather than as a total.
    async with db.session() as session:
        item = await _consistent(session, content_id)
        item_id = item.id
        counted_with = item.quantity
        first_counted = (await _one_result(session, content_id)).counted_at
    assert first_counted is not None

    # Undo the head approval, then converge.
    async with db.session() as session:
        services = build_pr_services(session, settings())
        await services.undo.undo_last(
            actor=people.head_actor, request_id=uuid.uuid4(), content_id=content_id
        )
        await session.commit()
    await project_once(db, people, content_id)

    async with db.session() as session:
        result = await _one_result(session, content_id)
        assert result.status is PrWorkCountStatus.EXCLUDED
        item = await session.get(PrWorkItem, item_id)
        assert item is not None and item.quantity == counted_with - Decimal("1.00")
        rows = await _contributions_of(session, item_id)
        assert len(rows) == 1
        if item.quantity == Decimal("0.00"):
            assert rows[0].count_status is PrWorkCountStatus.PENDING, (
                "an empty stream is not counted"
            )
        else:
            assert rows[0].count_status is PrWorkCountStatus.COUNTED

    # Approve again, and converge again.
    async with db.session() as session:
        services = build_pr_services(session, settings())
        await _decide(
            services,
            actor=people.head_actor,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
        await session.commit()
    await project_once(db, people, content_id)

    async with db.session() as session:
        item = await _consistent(session, content_id)
        assert item.id == item_id, "the redo reused the stream rather than opening a second"
        assert item.quantity == counted_with
        result = await _one_result(session, content_id)
        assert result.counted_at is not None and result.counted_at > first_counted, (
            "the redo stamped the retracted approval's instant"
        )


# ---------------------------------------------------------------------------
# 5: a refusal commits nothing
# ---------------------------------------------------------------------------


async def test_a_shut_month_is_not_rewritten_across_commits(db: Database, people: People) -> None:
    """A month that was reported stays reported. **Across real commits.**

    The half SQLite cannot show. Offline the whole test rolls back, so "the
    counted result is unchanged" is true whatever the service did. Here the
    projection's transaction really commits, so a reversal that got halfway
    before the period check would survive and be visible to the next connection.

    The shape is the one that matters in practice: the work counted while the
    month was open, the source was then retracted, and the month has since been
    closed. Convergence would say *"take it back out"*; the period says no. The
    discrepancy is **reported**, not silently applied.
    """
    content_id = await approved_content(db, people, title="Tháng đã chốt")
    period_id = await open_month(db, people, utcnow())
    await project_once(db, people, content_id)

    async with db.session() as session:
        item = await _consistent(session, content_id)
        item_id = item.id
        assert item.reporting_period_id == period_id
        counted_with = item.quantity

    # Retract the approval, then shut the month behind it.
    async with db.session() as session:
        services = build_pr_services(session, settings())
        await services.undo.undo_last(
            actor=people.head_actor, request_id=uuid.uuid4(), content_id=content_id
        )
        await session.commit()
    await set_period_status(db, period_id, PrPeriodStatus.CLOSED)
    try:
        report = await project_once(db, people, content_id)
        outcomes = {one.outcome for one in report.results}  # type: ignore[attr-defined]
        assert PrContentWorkOutcome.BLOCKED_BY_PERIOD in outcomes, outcomes

        # Read from a *fresh* connection: whatever the refused run did is
        # committed by now, so this is the state a report would actually print.
        async with db.session() as session:
            item = await _consistent(session, content_id)
            assert item.id == item_id
            assert item.quantity == counted_with, "a closed month was rewritten"
            # Refused, and **on the record** - a silent refusal would be worse
            # than the rewrite, because nobody would know the numbers had
            # drifted.
            blocked = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "pr.content_work.blocked")
            )
            assert blocked and blocked >= 1
    finally:
        # Reopen, so the module's remaining tests see a normal world.
        await set_period_status(db, period_id, PrPeriodStatus.OPEN)


# ---------------------------------------------------------------------------
# 6: the M2 handoff commits with the projection
# ---------------------------------------------------------------------------


async def test_the_eligibility_handoff_commits_with_the_projection(
    db: Database, people: People
) -> None:
    """A counted contribution and its allocation are never observable apart.

    M2's handoff runs in a savepoint **inside** the projection's transaction, so
    an eligibility failure cannot lose the count and a success cannot be seen
    without it. On PostgreSQL that is a real ``SAVEPOINT``; offline it is not,
    and a handoff that had quietly become its own transaction would look
    identical right up until the day one half failed.

    Written as one sequence rather than three tests because the module's
    database is shared and an approved plan cannot be un-approved: the order
    *count first, plan later* is only arrangeable once, and it is the order the
    rule is about. Three facts, in the order a department actually produces
    them:

    1. **no approved plan → the count commits and no allocation does.** M2 never
       vetoes M1. The absence is the honest record: a materialised ``NO_QUOTA``
       would claim eligibility was assessed against a plan that does not exist;
    2. **approving the plan evaluates what was already counted.** Work done
       before anybody planned the month is not stranded - ``approve`` recomputes
       the period as its last step, so no reconcile is needed;
    3. **with a plan in force the handoff is atomic.** A later projection's
       count and its allocation are visible together on a fresh connection, or
       neither is.
    """
    # 1. Counted with nobody's plan in force.
    before_plan = await approved_content(db, people, title="Chưa có kế hoạch")
    period_id = await open_month(db, people, utcnow())
    await project_once(db, people, before_plan)

    async with db.session() as session:
        item = await _consistent(session, before_plan)
        early_contribution = (await _contributions_of(session, item.id))[0]
        # M1 is untouched: the work counted.
        assert early_contribution.count_status is PrWorkCountStatus.COUNTED
        assert early_contribution.counted_at is not None
        # M2 waited.
        assert await _allocations_for(session, early_contribution.id) == []
        early_id = early_contribution.id
        counted_before_plan = item.quantity

    # 2. The manager plans the month afterwards. No reconcile is called.
    await approve_plan(db, people, period_id=period_id, user_id=people.member)

    async with db.session() as session:
        caught_up = await _allocations_for(session, early_id)
        assert len(caught_up) == 1, "work counted before the plan was never evaluated"
        assert caught_up[0].quota_status is PrWorkQuotaStatus.ELIGIBLE

    # 3. And with the plan in force, the handoff commits with the projection.
    content_id = await approved_content(db, people, title="Bàn giao hạn mức")
    await project_once(db, people, content_id)

    # A fresh connection: everything below either committed together or not.
    # The second piece lands in the **same** container as the first - one
    # stream per employee, type and month - so the contribution M2 evaluates
    # is the one it already allocated, now worth two.
    async with db.session() as session:
        item = await _consistent(session, content_id)
        assert item.quantity == counted_before_plan + Decimal("1.00")
        contribution_id = (await _contributions_of(session, item.id))[0].id
        assert contribution_id == early_id
        allocations = await _allocations_for(session, contribution_id)
        assert len(allocations) == 1, "the count committed without its eligibility row"
        assert allocations[0].quota_status is PrWorkQuotaStatus.ELIGIBLE
        assert allocations[0].work_quota_id is not None


# ---------------------------------------------------------------------------
# 7: the first piece of an unseen type provisions everything in one commit
# ---------------------------------------------------------------------------


async def test_an_unseen_content_type_is_provisioned_and_counted_in_one_commit(
    db: Database, people: People
) -> None:
    """Type, rule and result appear together on a fresh connection, and a
    retry finds all three and changes none of them."""
    content_type = PrContentType.PRESS_ARTICLE
    async with db.session() as session:
        assert await _auto_rows(session, content_type) == ([], []), "unseen before the test"

    content_id = await approved_content(
        db, people, title="Bài báo đầu tiên", content_type=content_type
    )
    report = await project_once(db, people, content_id)
    outcomes = {one.outcome for one in report.results}  # type: ignore[attr-defined]
    assert outcomes == {PrContentWorkOutcome.PROJECTED}, outcomes

    async with db.session() as session:
        types, rules = await _auto_rows(session, content_type)
        assert len(types) == 1 and len(rules) == 1
        assert rules[0].work_type_id == types[0].id
        assert rules[0].created_by_user_id is None
        item = await _consistent(session, content_id)
        assert item.work_type_id == types[0].id
        assert item.quantity == Decimal("1.00")
        type_id, rule_id = types[0].id, rules[0].id

    again = await project_once(db, people, content_id)
    assert {one.outcome for one in again.results} == {PrContentWorkOutcome.UNCHANGED}  # type: ignore[attr-defined]
    async with db.session() as session:
        types, rules = await _auto_rows(session, content_type)
        assert [one.id for one in types] == [type_id]
        assert [one.id for one in rules] == [rule_id]
        item = await _consistent(session, content_id)
        assert item.quantity == Decimal("1.00")


# ---------------------------------------------------------------------------
# 8: two pieces of one unseen type, racing
# ---------------------------------------------------------------------------


async def test_two_pieces_of_one_unseen_type_racing_provision_it_once(
    db: Database, people: People
) -> None:
    """Worker A creates the type; worker B races on the same content type with a
    different piece. Three unique indexes are hit in order - the type's code,
    the rule's ``(kind, content_type)`` and the writer's container for the
    month - and each loser re-reads the winner **inside a savepoint**, so both
    transactions commit. One type, one rule, two results, a container of two.
    """
    content_type = PrContentType.LONG_YOUTUBE_SCRIPT
    first = await approved_content(db, people, title="YouTube một", content_type=content_type)
    second = await approved_content(db, people, title="YouTube hai", content_type=content_type)
    async with db.session() as session:
        assert await _auto_rows(session, content_type) == ([], [])

    results = await asyncio.gather(
        project_once(db, people, first),
        project_once(db, people, second),
        return_exceptions=True,
    )
    raised = [one for one in results if isinstance(one, BaseException)]
    assert raised == [], raised

    async with db.session() as session:
        types, rules = await _auto_rows(session, content_type)
        assert len(types) == 1, [one.id for one in types]
        assert len(rules) == 1, [one.id for one in rules]
        item_first = await _consistent(session, first)
        item_second = await _consistent(session, second)
        assert item_first.id == item_second.id, "one stream for one writer, type and month"
        assert item_first.work_type_id == types[0].id
        assert item_first.quantity == Decimal("2.00")
        first_result = await _one_result(session, first)
        second_result = await _one_result(session, second)
        assert first_result.id != second_result.id


# ---------------------------------------------------------------------------
# 9: replaying a settled result changes nothing
# ---------------------------------------------------------------------------


async def test_concurrent_replays_of_a_settled_result_change_nothing(
    db: Database, people: People
) -> None:
    """A sweep retry, a manual reconcile and a stale worker all landing at once.
    The row is found, not re-inserted; the container's actual does not move."""
    content_id = await approved_content(db, people, title="Chiếu lại")
    await project_once(db, people, content_id)
    async with db.session() as session:
        item = await _consistent(session, content_id)
        before = (item.id, item.quantity, (await _one_result(session, content_id)).counted_at)

    results = await asyncio.gather(*(project_once(db, people, content_id) for _ in range(3)))
    for report in results:
        assert {one.outcome for one in report.results} == {PrContentWorkOutcome.UNCHANGED}  # type: ignore[attr-defined]

    async with db.session() as session:
        item = await _consistent(session, content_id)
        after = (item.id, item.quantity, (await _one_result(session, content_id)).counted_at)
        assert after == before
        assert len(await _results_for(session, content_id)) == 1


# ---------------------------------------------------------------------------
# 10: a failure after provisioning leaves no debris
# ---------------------------------------------------------------------------


async def test_a_failed_projection_rolls_the_provisioned_type_and_rule_back(
    db: Database, people: People
) -> None:
    """The type and the rule are written **before** the result, in the same
    transaction. A result the container refuses - here because the writer has
    since been deactivated - takes them back down with it: no work type, no
    rule, no result survive, and the next run starts from nothing."""
    content_type = PrContentType.CORPORATE_TVC
    content_id = await approved_content(
        db, people, title="TVC của người đã nghỉ", content_type=content_type, writer_id=people.other
    )
    await set_user_active(db, people.other, False)
    try:
        reason = await project_expecting_failure(db, people, content_id)
        assert reason == "user_inactive"
        async with db.session() as session:
            await _no_debris(session, content_id, content_type)
    finally:
        await set_user_active(db, people.other, True)

    # Back to normal, the same run provisions and counts as if nothing happened.
    await project_once(db, people, content_id)
    async with db.session() as session:
        types, rules = await _auto_rows(session, content_type)
        assert len(types) == 1 and len(rules) == 1
        item = await _consistent(session, content_id)
        assert item.subject_user_id == people.other


# ---------------------------------------------------------------------------
# 11: a first result refused by a shut month leaves no debris
# ---------------------------------------------------------------------------


async def test_a_shut_month_refuses_the_first_result_without_provisioning_debris(
    db: Database, people: People
) -> None:
    """Existing behaviour, at the provisioning path: a first result cannot open
    a stream in a month that is not ``OPEN``, the refusal is the period
    service's own, and because the run is one transaction the type and rule it
    had provisioned roll back with it."""
    content_type = PrContentType.ULTRA_SHORT_SCRIPT
    period_id = await open_month(db, people, utcnow())
    content_id = await approved_content(
        db, people, title="Siêu ngắn, tháng đã chốt", content_type=content_type
    )
    await set_period_status(db, period_id, PrPeriodStatus.CLOSED)
    try:
        reason = await project_expecting_failure(db, people, content_id)
        assert reason == "period_not_open"
        async with db.session() as session:
            await _no_debris(session, content_id, content_type)
    finally:
        await set_period_status(db, period_id, PrPeriodStatus.OPEN)

    await project_once(db, people, content_id)
    async with db.session() as session:
        types, rules = await _auto_rows(session, content_type)
        assert len(types) == 1 and len(rules) == 1
        assert (await _consistent(session, content_id)).reporting_period_id == period_id


async def test_the_module_never_forces_anything() -> None:
    """There is no override, here or in the projector. Asserted, not assumed."""
    import inspect

    from meobot.application import pr_content_work_projector

    source = inspect.getsource(pr_content_work_projector)
    for forbidden in ("force=", "force:", "ignore_period", "skip_period"):
        assert forbidden not in source, forbidden


#: A fixed instant, used where a test needs one that is not "now".
_FIXED = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)
