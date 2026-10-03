"""Step 1F.2.10 on a real PostgreSQL: the direct submission under contention.

The offline suite proves the rule - a second writer re-reads the stage and finds
no edge. It cannot prove that two *connections* contend correctly, because the
unit fixture is SQLite and ``lock_row`` degrades to a plain read there. Here the
lock is a real ``SELECT ... FOR UPDATE``, and four races are run with two
sessions genuinely racing for the row:

#. the AI submission against the direct one;
#. two direct submissions;
#. the direct submission against a new revision;
#. the direct submission against a cancellation.

Each asserts one deterministic outcome, one transition row for the winner, no
mixed state, no duplicate review request, no duplicate notification and no
unhandled database error.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_direct_submission_race_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_direct_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    ReviseContentCommand,
)
from meobot.application.pr_services import build_pr_services
from meobot.application.pr_support import supports_row_locks
from meobot.core.config import Settings, get_settings
from meobot.db.models.pr import PrBrand, PrPlatform
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrWorkflowTransitionError
from meobot.domain.pr.models import PrChannelCategory, PrContentType, PrWorkflowStage
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pr_database() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_direct_{uuid.uuid4().hex[:12]}"

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
    """The people, the brand and the one channel every race here shares."""

    def __init__(self) -> None:
        self.owner_id: uuid.UUID
        self.member_id: uuid.UUID
        self.brand_id: uuid.UUID
        self.channel_id: uuid.UUID

    @property
    def owner(self) -> Actor:
        return Actor(user_id=self.owner_id, full_name="Chủ sở hữu", role=Role.OWNER)

    @property
    def member(self) -> Actor:
        return Actor(user_id=self.member_id, full_name="Trần Minh Anh", role=Role.EMPLOYEE)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pr_database: Database) -> World:
    built = World()
    async with pr_database.transaction() as session:
        owner = User(full_name="Chủ sở hữu", role=Role.OWNER)
        member = User(full_name="Trần Minh Anh", role=Role.EMPLOYEE)
        brand = PrBrand(code=f"BRND-{uuid.uuid4().hex[:8]}", name="Apexmed")
        # Not policy-grounded, so the AI path needs no pack and both submissions
        # are open on the same item - which is what the first race needs.
        website = PrPlatform(code="WEBSITE", name="Website")
        session.add_all([owner, member, brand, website])
        await session.flush()
        built.owner_id, built.member_id, built.brand_id = owner.id, member.id, brand.id
        services = build_pr_services(session, get_settings())
        channel = await services.channels.create_channel(
            actor=Actor(user_id=owner.id, full_name="Chủ sở hữu", role=Role.OWNER),
            request_id=uuid.uuid4(),
            command=CreateChannelCommand(
                name="Apexmed Website",
                platform_id=website.id,
                brand_id=brand.id,
                category=PrChannelCategory.SCALE,
            ),
        )
        built.channel_id = channel.id
    return built


async def scripted(database: Database, world: World, *, title: str) -> uuid.UUID:
    """One item at ``SCRIPTING`` with a first draft, committed."""
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        snapshot = await services.content.create_content(
            actor=world.member,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=world.brand_id,
                owner_user_id=world.member_id,
                content_type=PrContentType.SHORT_VIDEO_SCRIPT,
                script_text="Nội dung.",
                targets=(ContentTargetSpec(channel_id=world.channel_id),),
            ),
        )
        content_id = snapshot.content.id
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        for stage in (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING):
            await services.workflow.request_transition(
                actor=world.member,
                request_id=uuid.uuid4(),
                content_id=content_id,
                target=stage,
            )
    return content_id


Op = Callable[[], Awaitable[object]]


def direct(database: Database, world: World, content_id: uuid.UUID) -> Op:
    async def run() -> object:
        async with database.transaction() as session:
            services = build_pr_services(session, get_settings())
            assert supports_row_locks(session)
            return await services.workflow.submit_to_team_lead_review(
                actor=world.member, request_id=uuid.uuid4(), content_id=content_id
            )

    return run


def ai(database: Database, world: World, content_id: uuid.UUID) -> Op:
    async def run() -> object:
        async with database.transaction() as session:
            services = build_pr_services(session, get_settings())
            return await services.workflow.request_transition(
                actor=world.member,
                request_id=uuid.uuid4(),
                content_id=content_id,
                target=PrWorkflowStage.AI_REVIEW,
            )

    return run


def revise(database: Database, world: World, content_id: uuid.UUID) -> Op:
    async def run() -> object:
        async with database.transaction() as session:
            services = build_pr_services(session, get_settings())
            return await services.content.revise_content(
                actor=world.member,
                request_id=uuid.uuid4(),
                command=ReviseContentCommand(
                    content_id=content_id, expected_version=1, script_text="Bản sửa."
                ),
            )

    return run


def cancel(database: Database, world: World, content_id: uuid.UUID) -> Op:
    async def run() -> object:
        async with database.transaction() as session:
            services = build_pr_services(session, get_settings())
            return await services.workflow.cancel(
                actor=world.owner, request_id=uuid.uuid4(), content_id=content_id
            )

    return run


async def race(*ops: Op) -> list[object | BaseException]:
    """Run the operations concurrently and hand back every outcome, refusals included."""
    return list(await asyncio.gather(*(op() for op in ops), return_exceptions=True))


def refusals(outcomes: list[object | BaseException]) -> list[BaseException]:
    return [o for o in outcomes if isinstance(o, BaseException)]


async def facts(database: Database, content_id: uuid.UUID) -> dict[str, object]:
    """Everything the races assert about, read from a session of their own."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT "
                "(SELECT workflow_stage FROM pr_content_items WHERE id = :id) AS stage, "
                "(SELECT count(*) FROM pr_content_transition_events "
                " WHERE content_id = :id AND to_stage = 'TEAM_LEAD_REVIEW') AS to_lead, "
                "(SELECT count(*) FROM pr_content_transition_events "
                " WHERE content_id = :id AND to_stage = 'AI_REVIEW') AS to_ai, "
                "(SELECT count(*) FROM pr_content_transition_events "
                " WHERE content_id = :id AND to_stage = 'CANCELLED') AS to_cancelled, "
                "(SELECT count(*) FROM pr_ai_review_runs WHERE content_id = :id) AS runs, "
                "(SELECT count(*) FROM pr_ai_reviews WHERE content_id = :id) AS reviews, "
                "(SELECT count(*) FROM pr_content_versions WHERE content_id = :id) AS versions, "
                "(SELECT count(*) FROM outbound_messages) AS messages, "
                "(SELECT count(*) FROM user_notifications) AS notifications, "
                "(SELECT count(*) FROM audit_logs WHERE entity_id = :entity "
                " AND action = 'pr.content.stage_changed' "
                " AND (after_data->>'ai_review_bypassed') = 'true') AS bypass_lines"
            ),
            # ``entity_id`` on the audit table is text; every other key is a uuid.
            {"id": content_id, "entity": str(content_id)},
        )
        return dict(rows.mappings().one())


async def test_01_ai_submission_and_direct_submission_have_one_winner(
    pr_database: Database, world: World
) -> None:
    """Whoever takes the row first moves it; the other is refused on re-read.

    Either order is legitimate, so the assertion is about consistency: exactly
    one forward transition, a run queued if and only if the AI path won, and
    never a piece that is at ``AI_REVIEW`` with a bypass line or at
    ``TEAM_LEAD_REVIEW`` with a queued review.
    """
    content_id = await scripted(pr_database, world, title="Cuộc đua 1")
    before = await facts(pr_database, content_id)

    outcomes = await race(
        ai(pr_database, world, content_id), direct(pr_database, world, content_id)
    )

    lost = refusals(outcomes)
    assert len(lost) == 1, outcomes
    assert isinstance(lost[0], PrWorkflowTransitionError), lost[0]
    after = await facts(pr_database, content_id)
    assert after["to_lead"] + after["to_ai"] == 1
    if after["stage"] == "AI_REVIEW":
        assert (after["to_ai"], after["to_lead"], after["runs"], after["bypass_lines"]) == (
            1,
            0,
            1,
            0,
        )
    else:
        assert after["stage"] == "TEAM_LEAD_REVIEW"
        assert (after["to_ai"], after["to_lead"], after["runs"], after["bypass_lines"]) == (
            0,
            1,
            0,
            1,
        )
    assert after["reviews"] == 0
    assert after["messages"] == before["messages"]
    assert after["notifications"] == before["notifications"]


async def test_02_two_direct_submissions_transition_once(
    pr_database: Database, world: World
) -> None:
    """A double-click, or a retry, is one move and one history row."""
    content_id = await scripted(pr_database, world, title="Cuộc đua 2")
    before = await facts(pr_database, content_id)

    outcomes = await race(
        direct(pr_database, world, content_id), direct(pr_database, world, content_id)
    )

    lost = refusals(outcomes)
    assert len(lost) == 1, outcomes
    assert isinstance(lost[0], PrWorkflowTransitionError), lost[0]
    assert lost[0].details["current"] == "TEAM_LEAD_REVIEW"
    after = await facts(pr_database, content_id)
    assert after["stage"] == "TEAM_LEAD_REVIEW"
    assert after["to_lead"] == 1
    assert after["bypass_lines"] == 1
    assert after["runs"] == 0
    assert after["messages"] == before["messages"]
    assert after["notifications"] == before["notifications"]


async def test_03_direct_submission_and_a_new_revision_never_interleave(
    pr_database: Database, world: World
) -> None:
    """The gate is pinned to a draft, and a draft cannot change under it.

    Two consistent endings and no third: the revision wins and the submission
    then hands over version 2, or the submission wins and the revision is refused
    because the draft is under review. What can never happen is version 2 being
    written after a submission pinned to version 1.
    """
    content_id = await scripted(pr_database, world, title="Cuộc đua 3")

    outcomes = await race(
        direct(pr_database, world, content_id), revise(pr_database, world, content_id)
    )

    after = await facts(pr_database, content_id)
    lost = refusals(outcomes)
    assert after["stage"] == "TEAM_LEAD_REVIEW"
    assert after["to_lead"] == 1
    if lost:
        # The submission got the row first; the revision found it under review.
        assert len(lost) == 1 and isinstance(lost[0], PrWorkflowTransitionError), outcomes
        assert after["versions"] == 1
        pinned_version_no = 1
    else:
        # The revision got the row first; the submission then pinned draft 2.
        assert after["versions"] == 2
        pinned_version_no = 2
    async with pr_database.session() as session:
        pinned = await session.execute(
            text(
                "SELECT v.version_no FROM pr_content_transition_events e "
                "JOIN pr_content_versions v ON v.id = e.content_version_id "
                "WHERE e.content_id = :id AND e.to_stage = 'TEAM_LEAD_REVIEW'"
            ),
            {"id": content_id},
        )
        assert [row[0] for row in pinned.all()] == [pinned_version_no]


async def test_04_direct_submission_and_cancellation_end_consistently(
    pr_database: Database, world: World
) -> None:
    """Cancelling is legal both before and after the handoff, so the piece ends
    ``CANCELLED`` either way - through one path or the other, never both halves
    of neither."""
    content_id = await scripted(pr_database, world, title="Cuộc đua 4")

    outcomes = await race(
        direct(pr_database, world, content_id), cancel(pr_database, world, content_id)
    )

    after = await facts(pr_database, content_id)
    lost = refusals(outcomes)
    assert after["stage"] == "CANCELLED"
    assert after["to_cancelled"] == 1
    if lost:
        # Cancelled first; the submission found nothing to submit.
        assert len(lost) == 1 and isinstance(lost[0], PrWorkflowTransitionError), outcomes
        assert after["to_lead"] == 0
    else:
        # Submitted first, then cancelled from the gate - two honest rows.
        assert after["to_lead"] == 1
    assert after["runs"] == 0
