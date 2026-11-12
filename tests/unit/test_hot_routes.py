"""The two routes prod measured slowest (12/11/2026, "Quản trị > Hiệu năng"):
``/api/pr/people`` (727 queries a call) and ``/api/board/counts`` (five whole
board pages for five numbers). The fast paths must answer exactly what the
slow ones did."""

from __future__ import annotations

from sqlalchemy import select

from meobot.application.board.task_board_service import TaskBoardService
from meobot.application.pr_services import build_pr_services
from meobot.application.units.directory import UnitDirectoryService
from meobot.db.models.user import User
from meobot.domain.board.models import BoardQuery
from meobot.domain.identity.models import Role
from tests.unit.pr_world import World
from tests.unit.streams import tag_pr


async def test_01_manages_many_answers_like_manages_for_everybody(world: World) -> None:
    admin = User(full_name="Quản trị", role=Role.ADMIN)
    outsider = User(full_name="Người ngoài", role=Role.EMPLOYEE)
    pr_staff = User(full_name="Nhân viên PR", role=Role.EMPLOYEE)
    world.session.add_all([admin, outsider, pr_staff])
    await world.session.flush()
    await tag_pr(world.session, [pr_staff])

    people = list((await world.session.scalars(select(User))).all())
    for viewer in (world.owner, admin, world.lead, world.member, outsider, pr_staff):
        actor = world.actor(viewer)
        one_by_one = {
            person.id: await world.capabilities.manages(actor, person) for person in people
        }
        assert await world.capabilities.manages_many(actor, people) == one_by_one, viewer.full_name
    # Something to compare: the OWNER manages others, a plain member nobody.
    owner_view = await world.capabilities.manages_many(world.actor(world.owner), people)
    assert owner_view[admin.id] is True
    assert not any((await world.capabilities.manages_many(world.actor(outsider), people)).values())


async def test_02_the_people_route_flags_who_the_viewer_manages(world: World) -> None:
    world.act_as(world.owner)
    rows = {row["user_id"]: row for row in world.client.get("/api/pr/people").json()}
    assert rows[str(world.member.id)]["can_manage_permissions"] is True
    world.act_as(world.member)
    rows = {row["user_id"]: row for row in world.client.get("/api/pr/people").json()}
    assert not any(row["can_manage_permissions"] for row in rows.values())


async def test_03_board_totals_equal_the_pages_totals(world: World) -> None:
    board = TaskBoardService(
        world.session,
        world.settings,
        directory=UnitDirectoryService(world.session),
        pr=build_pr_services(world.session, world.settings),
    )
    queries = [
        BoardQuery(mine=True, limit=1),
        BoardQuery(awaiting_me=True, limit=1),
        BoardQuery(priority=True, limit=1),
        BoardQuery(urgent=True, limit=1),
        BoardQuery(overdue=True, limit=1),
        BoardQuery(limit=1),
    ]
    for viewer in (world.owner, world.lead, world.member):
        actor = world.actor(viewer)
        for unit in ("PR", "ADS", "ALL") if viewer is world.owner else ("PR",):
            pages = [(await board.page(actor, unit=unit, query=query)).total for query in queries]
            assert await board.totals(actor, unit=unit, queries=queries) == pages, (
                viewer.full_name,
                unit,
            )
