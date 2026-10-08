"""KPI self-service on PostgreSQL: migration 0038, roundtrip, and the lifecycle under locks.

The unit suite proves the rules on SQLite; this file proves what SQLite cannot:
that ``0038`` applies and reverts cleanly, that the model and the migration
agree on the five new columns and two constraints, that the check constraints
are enforced by the database, and that the submit-then-approve flow runs
through ``FOR UPDATE`` locks on a real server.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_kpi_self_service_pg.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.pr import PrBrand, PrPlatform
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrPermissionDeniedError, PrWorkPlanStateError
from meobot.domain.pr.models import PrChannelCategory
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work_quota import PrWorkPlanStatus
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    alembic_head,
    downgrade_to,
    upgrade_to,
)
from tests.unit.streams import tag_pr
from tests.unit.test_pr_kpi_self_service import approve, send_back, submit
from tests.unit.test_pr_production_lifecycle import World
from tests.unit.test_pr_work_quota import approved_plan, month, work_type

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
NEW_COLUMNS = (
    "submitted_at",
    "submitted_by_user_id",
    "returned_at",
    "returned_by_user_id",
    "return_note",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0038_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    handle = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))
    try:
        yield handle
    finally:
        await handle.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def world(database: Database) -> AsyncIterator[World]:
    """The unit suite's world on PostgreSQL, one rolled-back transaction per test."""
    async with database.session_factory() as session:
        try:
            yield await _build_world(session)
        finally:
            await session.rollback()


async def _build_world(session: AsyncSession) -> World:
    owner = User(full_name="Chị Chủ", role=Role.OWNER)
    lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD)
    head = User(full_name="Hà Trưởng Phòng", role=Role.ADMIN)
    member = User(full_name="Phương Nhung", role=Role.EMPLOYEE)
    other = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-A", name="Apexmed")
    platform = PrPlatform(code="WEBSITE", name="Website")
    session.add_all([owner, lead, head, member, other, brand, platform])
    await session.flush()
    await tag_pr(session, [lead, member, other])
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
    return World(
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


async def test_01_models_and_migration_agree(database: Database) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)
    ours = [one for one in differences if any(name in str(one) for name in NEW_COLUMNS)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision_on_disk(database: Database) -> None:
    """``0038`` applies on the way to head, whatever head is by now."""
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_the_database_ties_who_to_when(world: World) -> None:
    """The two check constraints are the server's, not the model's."""
    period = await month(world)
    detail = await world.services.work_plans.self_create_plan(
        actor=world.actor(world.member), request_id=world.request_id, period_id=period.id
    )
    await world.session.execute(
        text("SAVEPOINT bad"),
    )
    with pytest.raises(IntegrityError):
        await world.session.execute(
            text("UPDATE pr_work_plans SET submitted_at = now() WHERE id = :id"),
            {"id": detail.plan.id},
        )
    await world.session.execute(text("ROLLBACK TO SAVEPOINT bad"))
    await world.session.execute(text("SAVEPOINT bad2"))
    with pytest.raises(IntegrityError):
        await world.session.execute(
            text("UPDATE pr_work_plans SET returned_by_user_id = :who WHERE id = :id"),
            {"id": detail.plan.id, "who": world.head.id},
        )
    await world.session.execute(text("ROLLBACK TO SAVEPOINT bad2"))


async def test_04_the_lifecycle_under_real_locks(world: World) -> None:
    """Self-create, submit, return, resubmit, approve - and self-approval refused."""
    period = await month(world)
    type_row = await work_type(world)
    old = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=old.id
    )
    await world.services.work_plans.update_quota(
        actor=world.actor(world.member),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=draft.quotas[0].id,
        target_value=Decimal("7"),
        eligibility_cap=Decimal("7"),
    )
    await submit(world, draft.plan.id)
    with pytest.raises(PrWorkPlanStateError) as locked:
        await world.services.work_plans.update_quota(
            actor=world.actor(world.member),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            quota_id=draft.quotas[0].id,
            target_value=Decimal("8"),
        )
    assert locked.value.details["reason"] == "draft_submitted_locked"
    await send_back(world, draft.plan.id, note="Xem lại mục tiêu.")
    await submit(world, draft.plan.id)
    with pytest.raises(PrPermissionDeniedError):
        await approve(world, draft.plan.id, by=world.member)
    decided = await approve(world, draft.plan.id)
    assert decided.plan.status is PrWorkPlanStatus.APPROVED
    superseded = await world.session.scalar(
        text("SELECT status FROM pr_work_plans WHERE id = :id"), {"id": old.id}
    )
    assert superseded == "SUPERSEDED"
    with pytest.raises(PrWorkPlanStateError):
        await approve(world, draft.plan.id, by=world.owner)


async def test_05_the_migration_roundtrips(dsn: str) -> None:
    await downgrade_to(dsn, "0037")
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0037"
            )
            present = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'pr_work_plans' AND column_name = ANY(:names)"
                        ),
                        {"names": list(NEW_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert present == []
    finally:
        await engine.dispose()
    await upgrade_to(dsn)
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == alembic_head()
            )
            present = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'pr_work_plans' AND column_name = ANY(:names)"
                        ),
                        {"names": list(NEW_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert sorted(present) == sorted(NEW_COLUMNS)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# The write paths over HTTP, on PostgreSQL. Hotfix: *Bỏ bản nháp* and *Gửi
# duyệt* returned 500 in production because the response serializer touched
# an attribute the flushed UPDATE had expired. Driven through the real app
# and the real session dependency - one request, one transaction, commit on
# return - on one event loop, which is why this uses httpx's ASGI transport
# rather than the thread-portal test client.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="module")
async def http(database: Database) -> AsyncIterator[tuple[object, dict[str, User], object]]:
    """A real app over the scratch database, plus committed people and a month."""
    from httpx import ASGITransport, AsyncClient

    from meobot.api.deps import get_current_web_actor
    from meobot.api.main import create_app

    settings = Settings(
        database_url=database.engine.url.render_as_string(hide_password=False),
        web_base_url="https://pr.example.com",
        **SETTINGS_KWARGS,
    )
    async with database.session_factory() as session:
        stamp = uuid.uuid4().hex[:6]
        people = {
            "owner": User(full_name=f"Chị Chủ {stamp}", role=Role.OWNER),
            "head": User(full_name=f"Hà Trưởng Phòng {stamp}", role=Role.ADMIN),
            "member": User(full_name=f"Phương Nhung {stamp}", role=Role.EMPLOYEE),
            "other": User(full_name=f"Nguyễn A {stamp}", role=Role.EMPLOYEE),
        }
        session.add_all(people.values())
        await session.flush()
        await tag_pr(session, people.values())  # untagged sees no stream
        services = build_pr_services(session, settings)
        owner = Actor(user_id=people["owner"].id, full_name="Chị Chủ", role=Role.OWNER)
        period = await services.work_periods.ensure_month_period(
            actor=owner, request_id=uuid.uuid4(), year=2026, month=9
        )
        from meobot.domain.pr.work import PrWorkCategory, PrWorkUnit
        from meobot.domain.pr.work_quota import PrWorkQuotaBasis

        type_row = await services.work.create_work_type(
            actor=owner,
            request_id=uuid.uuid4(),
            code=f"HTTP_{stamp}",
            name="Kịch bản",
            category=PrWorkCategory.CONTENT,
            default_unit=PrWorkUnit.ITEM,
            default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
        await session.commit()
        period_id, type_id = period.id, type_row.id
        ids = {name: person.id for name, person in people.items()}
        names = {name: person.full_name for name, person in people.items()}
        roles = {name: person.role for name, person in people.items()}

    app = create_app(settings)
    app.state.database = database
    current = {"name": "member"}

    def actor() -> Actor:
        name = current["name"]
        return Actor(user_id=ids[name], full_name=names[name], role=roles[name], active=True)

    app.dependency_overrides[get_current_web_actor] = actor
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        yield client, people, {"period_id": period_id, "type_id": type_id, "as": current}
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_06_the_write_paths_over_http(http, database: Database) -> None:  # type: ignore[no-untyped-def]
    client, people, ctx = http
    period_id, type_id = str(ctx["period_id"]), str(ctx["type_id"])

    def act_as(name: str) -> None:
        ctx["as"]["name"] = name

    async def row(plan_id: str) -> dict[str, object]:
        async with database.session() as session:
            result = await session.execute(
                text(
                    "SELECT status, submitted_at, submitted_by_user_id, returned_at, "
                    "returned_by_user_id, return_note, discarded_at, discarded_by_user_id, "
                    "(SELECT count(*) FROM pr_work_quotas q WHERE q.plan_id = p.id) AS quotas "
                    "FROM pr_work_plans p WHERE id = :id"
                ),
                {"id": plan_id},
            )
            return dict(result.mappings().one())

    # CASE A - an empty own draft is discardable, and nothing else moves.
    act_as("member")
    created = await client.post("/api/pr/work/plans/mine", json={"period_id": period_id})
    assert created.status_code == 201, created.text
    first = created.json()["plan"]["id"]
    dropped = await client.post(f"/api/pr/work/plans/{first}/discard", json={"note": None})
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["plan"]["status"] == "DISCARDED"
    state = await row(first)
    assert state["status"] == "DISCARDED" and state["discarded_at"] is not None
    assert state["discarded_by_user_id"] == people["member"].id
    assert state["submitted_at"] is None and state["submitted_by_user_id"] is None

    # CASE B - an empty draft is refused for submission, with the code, and stays editable.
    created = await client.post("/api/pr/work/plans/mine", json={"period_id": period_id})
    assert created.status_code == 201, created.text
    plan_id = created.json()["plan"]["id"]
    assert created.json()["plan"]["version_no"] == 2
    empty = await client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"]["details"]["reason"] == "plan_not_ready"
    assert empty.json()["error"]["details"]["blockers"] == ["plan_has_no_quotas"]
    assert (await row(plan_id))["submitted_at"] is None

    # CASE C - a ready draft submits: both submission columns set together.
    added = await client.post(
        f"/api/pr/work/plans/{plan_id}/quotas",
        json={"work_type_id": type_id, "target_value": "5", "eligibility_cap": "5"},
    )
    assert added.status_code == 201, added.text
    sent = await client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["plan"]["status"] == "DRAFT" and body["plan"]["review_state"] == "SUBMITTED"
    assert body["can_edit"] is False and body["can_submit"] is False
    state = await row(plan_id)
    assert state["status"] == "DRAFT"
    assert state["submitted_at"] is not None
    assert state["submitted_by_user_id"] == people["member"].id
    again = await client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert (
        again.status_code == 409
        and again.json()["error"]["details"]["reason"] == "draft_already_submitted"
    )
    # Locked for the author, including a discard.
    locked = await client.post(f"/api/pr/work/plans/{plan_id}/discard", json={"note": None})
    assert locked.status_code == 409, locked.text
    assert locked.json()["error"]["details"]["reason"] == "draft_submitted_locked"
    quota_id = added.json()["quotas"][0]["id"]
    stale = await client.patch(
        f"/api/pr/work/plans/{plan_id}/quotas/{quota_id}", json={"target_value": "9"}
    )
    assert (
        stale.status_code == 409
        and stale.json()["error"]["details"]["reason"] == "draft_submitted_locked"
    )
    # And somebody else's draft is nobody's to discard.
    act_as("other")
    theirs = await client.post(f"/api/pr/work/plans/{plan_id}/discard", json={"note": None})
    assert theirs.status_code == 404, theirs.text

    # The manager edits in place, returns with a note, the author resubmits.
    act_as("head")
    corrected = await client.patch(
        f"/api/pr/work/plans/{plan_id}/quotas/{quota_id}",
        json={"target_value": "6", "eligibility_cap": "6"},
    )
    assert corrected.status_code == 200, corrected.text
    returned = await client.post(
        f"/api/pr/work/plans/{plan_id}/return", json={"note": "Thêm video."}
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["plan"]["review_state"] == "RETURNED"
    state = await row(plan_id)
    assert state["submitted_at"] is None and state["submitted_by_user_id"] is None
    assert state["returned_at"] is not None and state["returned_by_user_id"] == people["head"].id
    assert state["return_note"] == "Thêm video."
    act_as("member")
    resent = await client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert resent.status_code == 200, resent.text
    state = await row(plan_id)
    assert state["submitted_at"] is not None and state["returned_at"] is None
    assert state["return_note"] is None
    # Self-approval refused; the head approves; the plan is in force.
    mine = await client.post(f"/api/pr/work/plans/{plan_id}/approve", json={})
    assert mine.status_code == 403, mine.text
    act_as("head")
    decided = await client.post(f"/api/pr/work/plans/{plan_id}/approve", json={})
    assert decided.status_code == 200, decided.text
    assert decided.json()["plan"]["status"] == "APPROVED"
    assert decided.json()["quotas"][0]["target_value"] == "6.00"
    act_as("member")
    in_force = await client.get("/api/pr/work/plans/mine", params={"period_id": period_id})
    assert in_force.status_code == 200 and in_force.json()["plan"]["id"] == plan_id
    twice = await client.post(f"/api/pr/work/plans/{plan_id}/approve", json={})
    assert twice.status_code in (403, 409)


async def test_07_workload_is_priced_and_batched_on_postgresql(world: World) -> None:
    """KPI workload visibility, on a real server.

    The one calculator prices a plan through the batched rule and calendar
    readers - ``IN (...)`` lists, a window over effective dates - and the
    manager list stays at a fixed number of statements however many people
    it prices. SQLite proved the arithmetic; this proves the SQL.
    """
    from sqlalchemy import event

    from tests.unit.test_pr_performance import policy, rule, schedule
    from tests.unit.test_pr_work_quota import seeding_type

    await schedule(world)
    await policy(world)
    period = await month(world)
    posts = await work_type(world, code="GROUP_POST", name="Bài Group")
    seeding = await seeding_type(world)
    unpriced = await work_type(world, code="NEW", name="Loại mới")
    await rule(world, posts, minutes=Decimal("30"))
    await rule(world, seeding, minutes=Decimal("1"))

    people = [User(full_name=f"NV {index}", role=Role.EMPLOYEE) for index in range(12)]
    world.session.add_all(people)
    await world.session.flush()
    for index, person in enumerate(people):
        quotas = [(posts, Decimal("28"), Decimal("28")), (seeding, Decimal("600"), Decimal("600"))]
        if index % 3 == 0:
            quotas.append((unpriced, Decimal("2"), Decimal("2")))
        plan = await approved_plan(world, period=period, user=person, quotas=tuple(quotas))
        if index % 2 == 0:
            await world.services.work_plans.revise(
                actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
            )

    async def priced(count: int) -> tuple[int, list]:  # type: ignore[type-arg]
        counted: list[str] = []

        def record(*args: object, **kwargs: object) -> None:
            counted.append("x")

        engine = world.session.get_bind().engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            rows = await world.services.work_plans.period_summary(
                actor=world.actor(world.owner), period_id=period.id
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(counted), [row for row in rows if row.current_plan is not None][:count]

    statements, rows = await priced(12)
    assert statements <= 12, f"{statements} statements for the manager list"
    assert len(rows) == 12
    for row in rows:
        workload = row.current_workload
        assert workload is not None
        assert workload.target_minutes == Decimal("6600.00")
        by_type = {quota.work_type_id: quota for quota in workload.quotas}
        assert by_type[posts.id].contribution_minutes == Decimal("840.00")
        assert by_type[posts.id].rule_label == "30 phút / đầu việc"
        assert by_type[seeding.id].contribution_minutes == Decimal("600.00")
        assert by_type[seeding.id].rule_label == "1 phút / bình luận"
        if unpriced.id in by_type:
            assert workload.is_complete is False and workload.percent is None
            assert workload.projected_minutes == Decimal("1440.00")
        else:
            assert workload.is_complete is True
            assert workload.percent == Decimal("21.8")
    drafts = [row for row in rows if row.latest_draft is not None]
    assert len(drafts) == 6
    assert all(row.draft_workload is not None for row in drafts)
