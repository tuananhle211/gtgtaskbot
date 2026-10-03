"""Who may see whose work. The M1 patch that stopped a Trưởng nhóm seeing everybody.

M1 shipped with one gate on three scopes: holding ``PR_WORK_MANAGE`` admitted
``ASSIGNED_BY_ME``, ``NEEDS_MY_DECISION`` **and** ``ALL``, so a Trưởng nhóm who
had assigned one job could read every colleague's record. There were three
routes to that, and this file exists to keep all three shut:

#. the **scope gate** - one flag for three scopes (tests 1-8);
#. the **user filter** - ``scope=MINE&user_id=<somebody else>``, which handed
   over any employee's whole list through the *employee* scope (tests 9-11);
#. the **detail route** - ``PR_WORK_MANAGE`` alone granted read of any item, so
   the list scopes could be walked past one id at a time (tests 12-17).

The effective matrix these tests pin:

===========  ======  ================  ==================  ===
Role         MINE    ASSIGNED_BY_ME    NEEDS_MY_DECISION   ALL
===========  ======  ================  ==================  ===
EMPLOYEE     yes     no                no                  no
TEAM_LEAD    yes     yes               yes                 **no**
ADMIN/OWNER  yes     yes               yes                 yes
===========  ======  ================  ==================  ===

No team, department or manager table was added to achieve it. The scopes are
existing relationships plus one explicit capability, ``PR_WORK_VIEW_ALL``, which
is paired with ``user.read`` and therefore reaches Head and Admin.
"""

from __future__ import annotations

# ruff: noqa: F811
import uuid

import pytest

from meobot.application.pr_work_query_service import PrWorkScope, WorkQuery
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrPermissionDeniedError
from meobot.domain.pr.policy import PrCapability, meets_baseline
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)
from tests.unit.test_pr_work_core import (
    assigned,
    contributions_of,
    proposed,
    take_to_completed,
    work_type,
)

pytestmark = pytest.mark.asyncio


async def page(world: World, who, **kwargs):
    """One page of work for ``who``. Raises the same refusal the API would."""
    return await world.services.work_queries.page(actor=world.actor(who), query=WorkQuery(**kwargs))


# ===========================================================================
# 1-8: THE SCOPE GATE
# ===========================================================================


async def test_01_view_all_is_its_own_capability_reaching_head_and_admin() -> None:
    """The fix, stated as a fact about the permission matrix.

    ``PR_WORK_MANAGE`` reaches ``TEAM_LEAD``; ``PR_WORK_VIEW_ALL`` does not.
    That gap is the whole patch - managing work and surveying it are different
    acts, and one capability cannot separate them.
    """

    def holders(capability: PrCapability) -> set[str]:
        return {
            role.value
            for role in Role
            if meets_baseline(Actor(user_id=None, full_name="x", role=role), capability)
        }

    assert holders(PrCapability.PR_WORK_MANAGE) == {"OWNER", "ADMIN", "TEAM_LEAD"}
    assert holders(PrCapability.PR_WORK_VIEW_ALL) == {"OWNER", "ADMIN"}
    assert "TEAM_LEAD" not in holders(PrCapability.PR_WORK_VIEW_ALL)


async def test_02_an_employee_sees_their_own_work(world: World) -> None:
    item = await assigned(world)
    result = await page(world, world.member, scope=PrWorkScope.MINE)
    assert [row.id for row in result.items] == [item.id]


async def test_03_an_employee_cannot_ask_for_any_wider_scope(world: World) -> None:
    """A refusal, not a silent narrowing - with a stable business reason code."""
    await assigned(world)
    for scope in (
        PrWorkScope.ALL,
        PrWorkScope.ASSIGNED_BY_ME,
        PrWorkScope.NEEDS_MY_DECISION,
    ):
        with pytest.raises(PrPermissionDeniedError) as caught:
            await page(world, world.member, scope=scope)
        assert caught.value.details["reason"] == "scope_not_permitted"
        assert caught.value.details["scope"] == scope.value


async def test_04_a_team_lead_sees_the_work_they_assigned(world: World) -> None:
    mine = await assigned(world, manager=world.lead, title="Tôi giao")
    await assigned(
        world,
        manager=world.head,
        contributors=(world.other,),
        type_row=await work_type(world, code="B", name="B"),
        title="Người khác giao",
    )
    result = await page(world, world.lead, scope=PrWorkScope.ASSIGNED_BY_ME)
    assert [row.id for row in result.items] == [mine.id]


async def test_05_a_team_lead_does_not_automatically_see_unrelated_work(
    world: World,
) -> None:
    """**The bug this patch fixes.**

    Work another manager assigned to another employee, with the lead nowhere
    near it. Before the patch ``scope=ALL`` returned it to any
    ``PR_WORK_MANAGE`` holder.
    """
    await assigned(world, manager=world.head, contributors=(world.other,), title="Không liên quan")

    with pytest.raises(PrPermissionDeniedError) as caught:
        await page(world, world.lead, scope=PrWorkScope.ALL)
    assert caught.value.details["reason"] == "scope_not_permitted"
    assert caught.value.details["requires"] == ["PR_WORK_VIEW_ALL"]

    # And the scopes they *do* have return nothing, because they are in none of
    # the relationships this work has.
    assert (await page(world, world.lead, scope=PrWorkScope.MINE)).total == 0
    assert (await page(world, world.lead, scope=PrWorkScope.ASSIGNED_BY_ME)).total == 0


async def test_06_manage_alone_does_not_imply_all(world: World) -> None:
    """Stated directly, because it is the sentence the patch is about."""
    assert (
        await world.services.capabilities.allows(
            world.actor(world.lead), PrCapability.PR_WORK_MANAGE
        )
        is True
    )
    assert (
        await world.services.capabilities.allows(
            world.actor(world.lead), PrCapability.PR_WORK_VIEW_ALL
        )
        is False
    )


async def test_07_head_and_admin_may_use_all(world: World) -> None:
    item = await assigned(world, manager=world.head, contributors=(world.other,))
    for person in (world.head, world.owner):
        result = await page(world, person, scope=PrWorkScope.ALL)
        assert item.id in {row.id for row in result.items}


async def test_08_the_decision_queue_narrows_to_what_the_actor_may_decide(
    world: World,
) -> None:
    """A queue of items its reader would be refused on is a queue of refusals.

    Four items, and only one belongs in the lead's queue: somebody else's
    proposal is theirs to accept, somebody else's finished work is theirs to
    validate - but their **own** proposal and work they **contributed to** are
    exactly what ``accept`` and ``approve`` refuse them.
    """
    theirs = await proposed(world, proposer=world.member)
    own = await world.services.work.propose_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=(await _command(world, "Của tôi")),
    )
    contributed = await assigned(
        world,
        manager=world.head,
        contributors=(world.lead,),
        type_row=await work_type(world, code="C", name="C"),
        title="Tôi có tham gia",
    )
    await take_to_completed(world, contributed, by=world.lead)
    other_done = await assigned(
        world,
        manager=world.head,
        contributors=(world.other,),
        type_row=await work_type(world, code="D", name="D"),
        title="Người khác làm",
    )
    await take_to_completed(world, other_done, by=world.other)

    queue = {
        row.id for row in (await page(world, world.lead, scope=PrWorkScope.NEEDS_MY_DECISION)).items
    }
    assert theirs.id in queue
    assert other_done.id in queue
    assert own.id not in queue
    assert contributed.id not in queue


async def _command(world: World, title: str):
    from meobot.application.pr_work_service import CreateWorkCommand

    row = await work_type(world, code=f"T{abs(hash(title)) % 9999}", name=title)
    return CreateWorkCommand(work_type_id=row.id, title=title)


# ===========================================================================
# 9-11: THE USER FILTER
# ===========================================================================


async def test_09_a_team_lead_cannot_read_a_colleague_through_the_user_filter(
    world: World,
) -> None:
    """**The quiet bypass.**

    ``scope=MINE&user_id=<somebody else>`` used to be gated on
    ``PR_WORK_MANAGE``, which handed a lead any employee's whole work list
    through the *employee* scope. Naming another person is now
    ``PR_WORK_VIEW_ALL``.
    """
    await assigned(world, manager=world.head, contributors=(world.other,))
    with pytest.raises(PrPermissionDeniedError) as caught:
        await page(world, world.lead, scope=PrWorkScope.MINE, user_id=world.other.id)
    assert caught.value.details["reason"] == "user_filter_not_permitted"
    assert caught.value.details["requires"] == ["PR_WORK_VIEW_ALL"]


async def test_10_naming_somebody_inside_your_own_book_is_allowed(world: World) -> None:
    """The one exception, and why it is not a widening.

    ``ASSIGNED_BY_ME`` is already restricted to work this actor put somebody on,
    so filtering it by that somebody narrows a list they may already see.
    """
    item = await assigned(world, manager=world.lead, contributors=(world.member,))
    result = await page(
        world, world.lead, scope=PrWorkScope.ASSIGNED_BY_ME, user_id=world.member.id
    )
    assert [row.id for row in result.items] == [item.id]


async def test_11_head_may_ask_about_anybody(world: World) -> None:
    item = await assigned(world, manager=world.lead, contributors=(world.member,))
    result = await page(world, world.head, scope=PrWorkScope.ALL, user_id=world.member.id)
    assert [row.id for row in result.items] == [item.id]


# ===========================================================================
# 12-17: THE DETAIL ROUTE CANNOT WALK PAST THE LIST SCOPES
# ===========================================================================


async def test_12_an_unrelated_manager_cannot_read_one_item_by_id(world: World) -> None:
    """The third route in, and the one a scope gate alone would leave open.

    If the detail route admitted any ``PR_WORK_MANAGE`` holder, the list scopes
    would be a formality: a lead could walk the module one id at a time.
    """
    item = await assigned(world, manager=world.head, contributors=(world.other,))
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.detail(actor=world.actor(world.lead), work_item_id=item.id)
    assert caught.value.details["reason"] == "not_involved"


async def test_13_the_four_ways_in_all_work(world: World) -> None:
    """Contributor, assigner, decision queue, and ``PR_WORK_VIEW_ALL``."""
    item = await assigned(world, manager=world.lead, contributors=(world.member,))
    for person in (world.member, world.lead, world.head, world.owner):
        assert (
            await world.services.work.detail(actor=world.actor(person), work_item_id=item.id)
        ).item.id == item.id
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.detail(actor=world.actor(world.other), work_item_id=item.id)


async def test_14_a_validator_can_open_the_item_they_are_asked_to_validate(
    world: World,
) -> None:
    """Otherwise the queue lists work its reader cannot open."""
    item = await assigned(world, manager=world.head, contributors=(world.other,))
    # Not visible to the lead while it is still being done...
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.detail(actor=world.actor(world.lead), work_item_id=item.id)

    await take_to_completed(world, item, by=world.other)
    # ...and visible once it is waiting for a validator, which the lead is.
    detail = await world.services.work.detail(actor=world.actor(world.lead), work_item_id=item.id)
    assert detail.can_validate is True
    # But not manageable: the lead did not put this work there.
    assert detail.can_manage is False


async def test_15_history_inherits_the_detail_rule(world: World) -> None:
    """One visibility rule, not two - ``history`` is gated by calling ``detail``."""
    item = await assigned(world, manager=world.head, contributors=(world.other,))
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.history(actor=world.actor(world.lead), work_item_id=item.id)


async def test_16_an_unrelated_manager_cannot_write_to_the_item_either(
    world: World,
) -> None:
    """The write path is never wider than the read path.

    Somebody who cannot see a job must not be able to move its deadline, cancel
    it, add people to it, or mark it finished.
    """
    item = await assigned(world, manager=world.head, contributors=(world.other,))
    lead = world.actor(world.lead)

    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.change_deadline(
            actor=lead, request_id=world.request_id, work_item_id=item.id, due_at=None
        )
    assert caught.value.details["reason"] == "not_item_manager"

    for call in (
        world.services.work.cancel(actor=lead, request_id=world.request_id, work_item_id=item.id),
        world.services.work.add_contributor(
            actor=lead,
            request_id=world.request_id,
            work_item_id=item.id,
            user_id=world.member.id,
        ),
    ):
        with pytest.raises(PrPermissionDeniedError):
            await call

    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.complete(
            actor=lead, request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "not_a_contributor"


async def test_17_the_assigner_keeps_every_management_control(world: World) -> None:
    """The narrowing must not cost the person who actually manages the job."""
    item = await assigned(world, manager=world.lead, contributors=(world.member,))
    lead = world.actor(world.lead)
    await world.services.work.change_deadline(
        actor=lead, request_id=world.request_id, work_item_id=item.id, due_at=None
    )
    await world.services.work.add_contributor(
        actor=lead, request_id=world.request_id, work_item_id=item.id, user_id=world.other.id
    )
    assert len(await contributions_of(world, item.id)) == 2
    assert (await world.services.work.detail(actor=lead, work_item_id=item.id)).can_manage is True


# ===========================================================================
# 18-20: THE SUMMARY OBEYS THE SAME RULES
# ===========================================================================


async def test_18_the_summary_refuses_a_scope_the_list_would_refuse(
    world: World,
) -> None:
    """The two must not disagree: a figure is only as scoped as its query."""
    await assigned(world)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_queries.summary(
            actor=world.actor(world.lead), query=WorkQuery(scope=PrWorkScope.ALL)
        )
    assert caught.value.details["reason"] == "scope_not_permitted"


async def test_19_the_summary_refuses_the_user_filter_bypass(world: World) -> None:
    await assigned(world, manager=world.head, contributors=(world.other,))
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_queries.summary(
            actor=world.actor(world.lead),
            query=WorkQuery(scope=PrWorkScope.MINE, user_id=world.other.id),
        )
    assert caught.value.details["reason"] == "user_filter_not_permitted"


async def test_20_a_lead_with_no_related_work_counts_nothing(world: World) -> None:
    """Not an error - a true zero. The lead really has no work here."""
    await assigned(world, manager=world.head, contributors=(world.other,))
    summary = await world.services.work_queries.summary(
        actor=world.actor(world.lead), query=WorkQuery(scope=PrWorkScope.MINE)
    )
    assert (summary.open, summary.counted_contributions, summary.overdue) == (0, 0, 0)


async def test_21_an_actor_with_no_user_row_is_refused(world: World) -> None:
    """A synthetic actor has no relationships, so it can see nothing."""
    ghost = Actor(user_id=None, full_name="Không có hồ sơ", role=Role.OWNER)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_queries.page(actor=ghost, query=WorkQuery())
    assert caught.value.details["reason"] == "actor_has_no_user_row"


async def test_22_no_team_or_department_table_was_added(world: World) -> None:
    """The constraint this patch worked under, asserted rather than promised.

    The scopes are existing relationships plus one capability. Nothing infers a
    hierarchy from channel assignments, and no organisational table exists.
    """
    from meobot.db.base import Base

    tables = set(Base.metadata.tables)
    for forbidden in ("teams", "team_members", "departments", "org_units"):
        assert forbidden not in tables

    source = (
        __import__("pathlib")
        .Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("src/meobot/application/pr_work_query_service.py")
        .read_text(encoding="utf-8")
    )
    assert "PrChannelAssignment" not in source
    assert "channel_assignment" not in source
    _ = uuid.uuid4  # keep the import honest


# ===========================================================================
# 23-26: THE SAME RULES OVER HTTP
# ===========================================================================
#
# Part B's requirement in as many words: "Ensure direct API access follows the
# same rule." A scope gate that only exists in a service a browser happens to
# call through is not a gate.


async def test_23_the_list_route_refuses_a_scope_over_http(world: World) -> None:
    """``403`` with a stable business code, not a silent narrowing.

    A client that asked for the department and got its own work back would draw
    a figure labelled as something it is not - so the answer is a refusal the
    caller can branch on.
    """
    await assigned(world)
    await world.session.flush()
    world.act_as(world.lead)

    response = world.client.get("/api/pr/work?scope=ALL")
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["details"]["reason"] == "scope_not_permitted"
    assert body["details"]["requires"] == ["PR_WORK_VIEW_ALL"]


async def test_24_the_user_filter_bypass_is_refused_over_http(world: World) -> None:
    """``scope=MINE&user_id=<somebody else>`` - the quiet route in."""
    await assigned(world, manager=world.head, contributors=(world.other,))
    await world.session.flush()
    world.act_as(world.lead)

    response = world.client.get(f"/api/pr/work?scope=MINE&user_id={world.other.id}")
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "user_filter_not_permitted"


async def test_25_the_detail_route_refuses_an_unrelated_manager_over_http(
    world: World,
) -> None:
    """And the summary too - one rule, every entry point."""
    item = await assigned(world, manager=world.head, contributors=(world.other,))
    await world.session.flush()
    world.act_as(world.lead)

    assert world.client.get(f"/api/pr/work/{item.id}").status_code == 403
    assert world.client.get(f"/api/pr/work/{item.id}/history").status_code == 403
    assert world.client.get("/api/pr/work/summary?scope=ALL").status_code == 403


async def test_26_head_gets_the_department_over_http(world: World) -> None:
    """The control, without which every refusal above proves nothing."""
    item = await assigned(world, manager=world.lead, contributors=(world.member,))
    await world.session.flush()
    world.act_as(world.head)

    response = world.client.get("/api/pr/work?scope=ALL")
    assert response.status_code == 200
    assert str(item.id) in {row["id"] for row in response.json()["items"]}
    assert world.client.get(f"/api/pr/work/{item.id}").status_code == 200
