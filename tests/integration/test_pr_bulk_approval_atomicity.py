"""Step 1F.2.8 on a real PostgreSQL: a bulk approval commits whole or not at all.

The offline suite proves the *rules* - the same-step check, the per-item scope
check, the refusal shapes, the de-duplication. It cannot prove the two claims the
step is actually built on, because the unit fixture is SQLite:

* **the locks are real.** ``lock_row`` degrades to a plain ``get`` off
  PostgreSQL - deliberately, since SQLite has no ``FOR UPDATE`` and serialises
  writers anyway - so every offline test of "locked in a deterministic order"
  is a test of a code path that did not lock. Here it does, and
  :func:`supports_row_locks` says so;
* **nothing is left behind.** SQLite in the unit fixture never commits, so "the
  batch approved nothing" is true there whatever the service did. Here the
  transaction really commits, and a refused batch has to leave the approval
  table, the transition table, the audit trail and every item's
  ``workflow_stage`` exactly as they were.

Four scenarios, and each is one of the promises in
``docs/pr/STEP_1F28_BULK_APPROVAL_AND_CONFIRMATION.md``:

#. a valid batch of three commits three approvals, three transitions, three
   audit rows and one batch row - the control, without which "nothing was
   written" below proves nothing;
#. one item at another gate refuses the batch and leaves the other two at their
   own gate, unapproved;
#. one item outside the actor's grant scope does the same;
#. every audit row from one batch carries the same correlation id, and a
   single-item approval carries none - the claim that needed no migration.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_bulk_approval_atomicity.py -m integration

The fixture creates its own uniquely-named ``meobot_bulk_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_bulk_approval_service import BulkApproveCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.application.pr_support import supports_row_locks
from meobot.core.config import Settings, get_settings
from meobot.db.models.pr import PrBrand, PrPlatform
from meobot.db.models.pr_platform_policy import PrPlatformPolicyPack
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrBulkApprovalStaleError,
    PrBulkApprovalUnauthorizedError,
)
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelCategory,
    PrContentType,
    PrDistributionMode,
    PrPolicyPackStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
GATE = PrApprovalStage.TEAM_LEAD_REVIEW


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pr_database() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_bulk_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn)
        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


class World:
    """The people, the brand and the two channels every test here shares."""

    def __init__(self) -> None:
        self.owner_id: uuid.UUID
        self.member_id: uuid.UUID
        self.brand_id: uuid.UUID
        self.tiktok_id: uuid.UUID
        self.youtube_id: uuid.UUID

    @property
    def owner(self) -> Actor:
        return Actor(user_id=self.owner_id, full_name="Chủ sở hữu", role=Role.OWNER)

    @property
    def member(self) -> Actor:
        return Actor(user_id=self.member_id, full_name="Hảo", role=Role.EMPLOYEE)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pr_database: Database) -> World:
    built = World()
    async with pr_database.transaction() as session:
        owner = User(full_name="Chủ sở hữu", role=Role.OWNER)
        member = User(full_name="Hảo", role=Role.EMPLOYEE)
        brand = PrBrand(code=f"BRND-{uuid.uuid4().hex[:8]}", name="Apexmed")
        tiktok = PrPlatform(code="TIKTOK", name="TikTok")
        youtube = PrPlatform(code="YOUTUBE", name="YouTube")
        session.add_all([owner, member, brand, tiktok, youtube])
        await session.flush()
        # TikTok is policy-grounded, so an organic target on it needs an ACTIVE
        # pack before the item may enter AI review. A precondition of reaching
        # the gate, and nothing to do with who may approve.
        session.add(
            PrPlatformPolicyPack(
                platform_code="TIKTOK",
                distribution_mode=PrDistributionMode.ORGANIC.value,
                version=1,
                label="TIKTOK/ORGANIC v1",
                status=PrPolicyPackStatus.ACTIVE,
                manifest_hash="a" * 64,
            )
        )
        built.owner_id, built.member_id, built.brand_id = owner.id, member.id, brand.id
        services = build_pr_services(session, get_settings())
        granter = Actor(user_id=owner.id, full_name="Chủ sở hữu", role=Role.OWNER)
        for name, platform, attribute in (
            ("TikTok BS Tiến", tiktok, "tiktok_id"),
            ("YouTube Apexmed", youtube, "youtube_id"),
        ):
            channel = await services.channels.create_channel(
                actor=granter,
                request_id=uuid.uuid4(),
                command=CreateChannelCommand(
                    name=name,
                    platform_id=platform.id,
                    brand_id=brand.id,
                    category=PrChannelCategory.SCALE,
                ),
            )
            setattr(built, attribute, channel.id)
    return built


async def grant(database: Database, world: World, *, channels: Sequence[uuid.UUID]) -> uuid.UUID:
    """One scoped ``TEAM_LEAD_REVIEW`` grant to the member, over these channels."""
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        row = await services.capabilities.grant(
            actor=world.owner,
            request_id=uuid.uuid4(),
            user_id=world.member_id,
            capability=PrCapability.PR_TEAM_LEAD_REVIEW,
            scope=GrantScope(
                content_type_scope=PrGrantScopeMode.SELECTED,
                content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
                channel_scope=PrGrantScopeMode.SELECTED,
                channel_ids=frozenset(channels),
            ),
        )
        return row.id


async def waiting(database: Database, world: World, *, title: str, channel: uuid.UUID) -> uuid.UUID:
    """One item walked to ``TEAM_LEAD_REVIEW`` through the real edges."""
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        snapshot = await services.content.create_content(
            actor=world.owner,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=world.brand_id,
                owner_user_id=world.owner_id,
                content_type=PrContentType.SHORT_VIDEO_SCRIPT,
                script_text="Nội dung.",
                targets=(
                    ContentTargetSpec(
                        channel_id=channel, distribution_mode=PrDistributionMode.ORGANIC
                    ),
                ),
            ),
        )
        content_id = snapshot.content.id
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        for stage in (
            PrWorkflowStage.BRIEFING,
            PrWorkflowStage.SCRIPTING,
            PrWorkflowStage.AI_REVIEW,
        ):
            await services.workflow.request_transition(
                actor=world.owner,
                request_id=uuid.uuid4(),
                content_id=content_id,
                target=stage,
            )
        await services.ai_reviews.record_review(
            actor=world.owner,
            request_id=uuid.uuid4(),
            command=RecordAiReviewCommand(
                content_id=content_id,
                reviewed_version=1,
                review_type=PrAiReviewType.FULL_REVIEW,
                result=PrAiReviewResult.PASS,
                model_name="claude-opus-5",
                prompt_version="p@1",
                reviewed_at=NOW,
            ),
        )
    return content_id


async def counts(database: Database) -> dict[str, int]:
    """Everything a batch writes, counted from a session of its own."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM pr_approval_events) AS approvals, "
                "(SELECT count(*) FROM pr_content_transition_events) AS transitions, "
                "(SELECT count(*) FROM audit_logs "
                " WHERE action = 'pr.approval.recorded') AS per_item, "
                "(SELECT count(*) FROM audit_logs "
                " WHERE action = 'pr.approval.batch_recorded') AS batches"
            )
        )
        return dict(rows.mappings().one())


async def stages(database: Database, ids: Sequence[uuid.UUID]) -> list[str]:
    async with database.session() as session:
        rows = await session.execute(
            text("SELECT workflow_stage FROM pr_content_items WHERE id = ANY(:ids) ORDER BY code"),
            {"ids": list(ids)},
        )
        return [row[0] for row in rows.all()]


async def bulk(database: Database, world: World, ids: Sequence[uuid.UUID]) -> object:
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        # The claim the offline suite cannot make: on this dialect the row lock
        # is a real ``SELECT ... FOR UPDATE``.
        assert supports_row_locks(session)
        return await services.bulk_approvals.approve(
            actor=world.member,
            request_id=uuid.uuid4(),
            command=BulkApproveCommand(
                gate=GATE,
                content_ids=list(ids),
                reviewer_user_id=world.member_id,
                comment="Duyệt cả lô.",
            ),
        )


async def test_a_valid_batch_commits_every_approval_and_one_batch_row(
    pr_database: Database, world: World
) -> None:
    """The control. Without it, "nothing was written" below proves nothing."""
    await grant(pr_database, world, channels=[world.tiktok_id])
    ids = [
        await waiting(pr_database, world, title=f"Bài {index}", channel=world.tiktok_id)
        for index in range(3)
    ]
    before = await counts(pr_database)

    outcome = await bulk(pr_database, world, ids)

    after = await counts(pr_database)
    assert after["approvals"] == before["approvals"] + 3
    # One transition per approval, committed with it - which is the part a bulk
    # ``INSERT`` into ``pr_approval_events`` would have silently dropped.
    assert after["transitions"] == before["transitions"] + 3
    # Requirement 9: the per-item audit rows survive, and the batch row is
    # *beside* them rather than instead of them.
    assert after["per_item"] == before["per_item"] + 3
    assert after["batches"] == before["batches"] + 1
    assert await stages(pr_database, ids) == [PrWorkflowStage.HEAD_REVIEW.value] * 3

    async with pr_database.session() as session:
        rows = await session.execute(
            text(
                "SELECT after_data->>'batch_id' FROM audit_logs "
                "WHERE action = 'pr.approval.recorded'"
            )
        )
        stamped = {row[0] for row in rows.all()}
    assert str(outcome.batch_id) in stamped  # type: ignore[attr-defined]


async def test_a_batch_with_one_moved_item_writes_nothing_at_all(
    pr_database: Database, world: World
) -> None:
    """Requirement 7 and 8, on a database that really commits."""
    ids = [
        await waiting(pr_database, world, title=f"Đã chuyển {index}", channel=world.tiktok_id)
        for index in range(3)
    ]
    # Somebody else approves the third, so it stands at HEAD_REVIEW when the
    # batch arrives. Walked through the real write, not stamped onto the row.
    async with pr_database.transaction() as session:
        services = build_pr_services(session, get_settings())
        await services.approvals.record_decision(
            actor=world.member,
            request_id=uuid.uuid4(),
            command=RecordApprovalCommand(
                content_id=ids[2],
                reviewer_user_id=world.member_id,
                approval_stage=GATE,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
            ),
        )
    before = await counts(pr_database)

    with pytest.raises(PrBulkApprovalStaleError) as refused:
        await bulk(pr_database, world, ids)

    assert refused.value.details["approved"] == 0
    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [str(ids[2])]
    assert await counts(pr_database) == before
    assert await stages(pr_database, ids[:2]) == [PrWorkflowStage.TEAM_LEAD_REVIEW.value] * 2


async def test_a_batch_with_one_out_of_scope_item_writes_nothing_at_all(
    pr_database: Database, world: World
) -> None:
    """Requirement 3: the scope check is per item, under the lock."""
    covered = await waiting(pr_database, world, title="Trong phạm vi", channel=world.tiktok_id)
    outside = await waiting(pr_database, world, title="Ngoài phạm vi", channel=world.youtube_id)
    before = await counts(pr_database)

    with pytest.raises(PrBulkApprovalUnauthorizedError) as refused:
        await bulk(pr_database, world, [covered, outside])

    assert refused.value.details["approved"] == 0
    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [str(outside)]
    assert await counts(pr_database) == before
    assert await stages(pr_database, [covered]) == [PrWorkflowStage.TEAM_LEAD_REVIEW.value]


async def test_a_single_approval_writes_no_batch_row_and_no_correlation_id(
    pr_database: Database, world: World
) -> None:
    """Requirement 36: the one-item path is what it was, apart from a null key."""
    content_id = await waiting(pr_database, world, title="Một mình", channel=world.tiktok_id)
    before = await counts(pr_database)

    async with pr_database.transaction() as session:
        services = build_pr_services(session, get_settings())
        await services.approvals.record_decision(
            actor=world.member,
            request_id=uuid.uuid4(),
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=world.member_id,
                approval_stage=GATE,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
            ),
        )

    after = await counts(pr_database)
    assert after["approvals"] == before["approvals"] + 1
    assert after["per_item"] == before["per_item"] + 1
    assert after["batches"] == before["batches"]

    async with pr_database.session() as session:
        latest = await session.execute(
            text(
                "SELECT after_data->>'batch_id' FROM audit_logs "
                "WHERE action = 'pr.approval.recorded' "
                "AND after_data->>'content_id' = :id"
            ),
            {"id": str(content_id)},
        )
        assert latest.scalar_one() is None
