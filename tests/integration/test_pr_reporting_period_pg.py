"""Kỳ báo cáo on PostgreSQL - the SQL the unit suite proves on SQLite, run for real.

Step 1F.2.3f.6. :mod:`tests.unit.test_pr_reporting_period` is the contract and
runs in-memory; this file re-runs the load-bearing scenarios against a
migrated PostgreSQL, because the read model is one ``CASE`` over correlated
scalar subqueries with ``COALESCE`` across ``timestamptz`` columns, and
"works on SQLite" says nothing about the planner, the type coercions or
``FOR UPDATE`` under the bulk archive's locks.

Driven through the services rather than the HTTP client: one asyncpg
connection cannot be shared with the test client's own event loop, and the
routes add nothing to what is being checked here - they parse and delegate.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_reporting_period_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_period_*`` database,
migrates it to ``head`` and drops it afterwards. Every test runs inside one
transaction that is rolled back, so nothing is left behind between tests.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_bulk_archive_service import BulkArchiveCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_query import ContentQuery
from meobot.application.pr_query_service import ContentPage
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.models.pr import PrBrand, PrPlatform
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_views import (
    PrContentBoardView,
    PrContentGroup,
    PrContentLane,
    PrContentViewScope,
)
from meobot.domain.pr.errors import PrBulkArchiveStaleError
from meobot.domain.pr.models import PrChannelCategory, PrProductionHandoff, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from tests.integration.test_dispatch_migrations import alembic_head, upgrade_to
from tests.unit.test_pr_derivatives_and_publications import publish, ready_to_publish
from tests.unit.test_pr_production_lifecycle import World, make_content, to_production
from tests.unit.test_pr_reporting_period import (
    AUGUST,
    JULY,
    LATE_AUGUST,
    SEPTEMBER,
    created_on,
    entered_on,
    move,
    to_review,
)

TEST_DATABASE_URL = os.environ.get("MEOBOT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEOBOT_TEST_DATABASE_URL not set; needs a PostgreSQL to create databases on",
    ),
    pytest.mark.asyncio(loop_scope="module"),
]

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def period_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_period_{uuid.uuid4().hex[:8]}"
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


@pytest_asyncio.fixture(loop_scope="module")
async def world(period_db: Database) -> AsyncIterator[World]:
    """The unit suite's world - five people, a brand, a channel - on PostgreSQL.

    One transaction per test, rolled back at the end. No HTTP client: see the
    module docstring.
    """
    async with period_db.session_factory() as session:
        try:
            owner = User(full_name="Chị Chủ", role=Role.OWNER)
            lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD)
            head = User(full_name="Hà Trưởng Phòng", role=Role.ADMIN)
            member = User(full_name="Phương Nhung", role=Role.EMPLOYEE)
            other = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
            brand = PrBrand(code="BRND-A", name="Apexmed")
            platform = PrPlatform(code="WEBSITE", name="Website")
            session.add_all([owner, lead, head, member, other, brand, platform])
            await session.flush()
            settings = Settings(web_base_url="https://pr.example.com", **SETTINGS_KWARGS)
            services = build_pr_services(session, settings)
            granter = Actor(user_id=owner.id, full_name=owner.full_name, role=Role.OWNER)
            for user, capability in (
                (lead, PrCapability.PR_TEAM_LEAD_REVIEW),
                (head, PrCapability.PR_HEAD_REVIEW),
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
            yield World(
                session=session,
                client=None,  # type: ignore[arg-type]
                settings=settings,
                services=services,
                owner=owner,
                lead=lead,
                head=head,
                member=member,
                other=other,
                brand_id=brand.id,
                channel_id=channel.id,
            )
        finally:
            await session.rollback()


def _month(code: str) -> date:
    year, month = code.split("-")
    return date(int(year), int(month), 1)


async def figures(world: World, period: str | None = None, **kwargs: object) -> ContentPage:
    """The board's figures, as the board route asks: the operational view."""
    query = ContentQuery(
        scope=PrContentViewScope.ALL,
        period_month=_month(period) if period and period != "CURRENT" else None,
        current_period=period == "CURRENT",
        view=PrContentBoardView.ACTIVE,
        limit=0,
        **kwargs,  # type: ignore[arg-type]
    )
    return await world.services.queries.content_page(actor=world.actor(world.owner), query=query)


async def column(world: World, lane: str, period: str, **kwargs: object) -> ContentPage:
    """One column for one month. The archive lane lives on the archive view."""
    view = PrContentBoardView.ARCHIVE if lane == "ARCHIVED" else PrContentBoardView.ACTIVE
    query = ContentQuery(
        scope=PrContentViewScope.ALL,
        lane=PrContentLane(lane),
        period_month=_month(period),
        view=view,
        limit=50,
        **kwargs,  # type: ignore[arg-type]
    )
    return await world.services.queries.content_page(actor=world.actor(world.owner), query=query)


def group_total(page: ContentPage, group: PrContentGroup) -> int:
    from meobot.domain.pr.content_views import stages_in_group

    return sum(page.stage_counts[stage] for stage in stages_in_group(group))


async def test_00_the_schema_is_at_head_and_needed_no_migration(period_db: Database) -> None:
    async with period_db.session() as active:
        current = await active.scalar(text("SELECT version_num FROM alembic_version"))
    assert current == alembic_head()


async def test_01_lane_facts_and_counts_agree_on_postgres(world: World) -> None:
    """Created August, produced September; created July, in review September;
    published August; archived September. Every count is its column's."""
    producing = await make_content(world, owner=world.member)
    await created_on(world, producing, LATE_AUGUST)
    await to_production(world, producing, producer=world.member)
    await entered_on(world, producing, SEPTEMBER)

    reviewing = await make_content(world, owner=world.member)
    await created_on(world, reviewing, JULY)
    await to_review(world, reviewing)
    await entered_on(world, reviewing, SEPTEMBER)

    published, master = await ready_to_publish(world)
    await publish(world, published, submission=master, at=AUGUST)

    archived, master = await ready_to_publish(world)
    await publish(world, archived, submission=master, at=JULY)
    await move(world, archived, PrWorkflowStage.ARCHIVED)
    row = await world.reload(archived)
    row.archived_at = SEPTEMBER
    await world.session.flush()

    september = await figures(world, "2026-09")
    august = await figures(world, "2026-08")
    july = await figures(world, "2026-07")
    stages = PrWorkflowStage
    assert september.production_state_counts[PrProductionHandoff.IN_PRODUCTION] == 1
    assert august.production_state_counts[PrProductionHandoff.IN_PRODUCTION] == 0
    assert september.stage_counts[stages.TEAM_LEAD_REVIEW] == 1
    assert july.stage_counts[stages.TEAM_LEAD_REVIEW] == 0
    assert august.stage_counts[stages.PUBLISHED] == 1
    assert september.stage_counts[stages.PUBLISHED] == 0
    # Step 1F.2.3f.6c: the operational board neither counts nor reads the
    # archive; the archive view does, by ``archived_at``.
    assert september.stage_counts[stages.ARCHIVED] == 0
    assert (await column(world, "ARCHIVED", "2026-09")).total == 1
    assert (await column(world, "ARCHIVED", "2026-07")).total == 0
    # And each figure is the very query its column runs.
    for period, page in (("2026-08", august), ("2026-09", september)):
        for lane in ("TEAM_LEAD_REVIEW", "PUBLISHED"):
            got = await column(world, lane, period)
            assert page.stage_counts[PrWorkflowStage(lane)] == got.total == len(got.items), (
                period,
                lane,
            )
        got = await column(world, "IN_PRODUCTION", period)
        assert page.production_state_counts[PrProductionHandoff.IN_PRODUCTION] == got.total
    assert sum(group_total(september, group) for group in PrContentGroup) == 2
    assert sum(group_total(august, group) for group in PrContentGroup) == 1


async def test_02_the_published_count_is_the_month_not_the_lifetime(world: World) -> None:
    for at in (JULY, AUGUST, AUGUST, SEPTEMBER):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=at)
    assert (await figures(world)).stage_counts[PrWorkflowStage.PUBLISHED] == 4
    august = await column(world, "PUBLISHED", "2026-08", group=PrContentGroup.COMPLETED)
    tabs = await figures(world, "2026-08", group=PrContentGroup.COMPLETED)
    assert tabs.stage_counts[PrWorkflowStage.PUBLISHED] == 2
    assert august.total == 2 == len(august.items)
    assert august.period == date(2026, 8, 1)
    current = await figures(world, "CURRENT")
    assert current.period == datetime.now(world.settings.timezone).date().replace(day=1)


async def test_03_bulk_archive_locks_validates_and_transitions_each_item(world: World) -> None:
    ids = []
    for at in (AUGUST, AUGUST, SEPTEMBER):
        content_id, master = await ready_to_publish(world)
        await publish(world, content_id, submission=master, at=at)
        ids.append(content_id)
    found = await world.services.queries.archive_candidates(
        actor=world.actor(world.head), period=date(2026, 8, 1)
    )
    assert found.total == 2 and found.may_archive
    outcome = await world.services.bulk_archive.archive(
        actor=world.actor(world.head),
        request_id=world.request_id,
        command=BulkArchiveCommand(period=date(2026, 8, 1), content_ids=found.content_ids),
    )
    assert outcome.archived_count == 2
    for content_id in ids[:2]:
        row = await world.reload(content_id)
        assert row.workflow_stage is PrWorkflowStage.ARCHIVED
        event = await world.session.scalar(
            select(PrContentTransitionEvent).where(
                PrContentTransitionEvent.content_id == content_id,
                PrContentTransitionEvent.to_stage == PrWorkflowStage.ARCHIVED,
            )
        )
        assert event is not None and event.from_stage is PrWorkflowStage.PUBLISHED
    assert (await world.reload(ids[2])).workflow_stage is PrWorkflowStage.PUBLISHED
    # A retry is a clean refusal, and the September piece is untouched.
    with pytest.raises(PrBulkArchiveStaleError) as refused:
        await world.services.bulk_archive.archive(
            actor=world.actor(world.head),
            request_id=world.request_id,
            command=BulkArchiveCommand(period=date(2026, 8, 1), content_ids=found.content_ids),
        )
    assert refused.value.details["reason"] == "already_archived"
    september = await column(world, "PUBLISHED", "2026-09")
    assert [row.id for row in september.items] == [ids[2]]
    this_month = datetime.now(UTC).strftime("%Y-%m")
    assert (await column(world, "ARCHIVED", this_month)).total == 2
