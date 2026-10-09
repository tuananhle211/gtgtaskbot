"""Streams ("Luồng"): no tag on join, who may tag whom, ADMIN = every stream,
deactivate/reactivate, the chip labels, and the board's "todo first" order.

The PR test world tags its team lead and member PR (``tests/unit/streams.py``);
anybody built here with :func:`newcomer` has no tag at all, like an account
that just redeemed an invite.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from meobot.application.audit_service import AuditService
from meobot.application.invite_service import InviteService
from meobot.application.units.directory import UnitDirectoryService
from meobot.application.web_auth_service import WebAuthService
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionAuthMethod
from meobot.domain.identity.models import Role
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.units.labels import function_tag, unit_short_label
from meobot.domain.units.models import UnitCode, UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_order_commands import act, ads_world, create
from tests.unit.test_units_gating import error_reason, newcomer, seed_units, tag

pytestmark = pytest.mark.asyncio


def _code(body: dict[str, Any]) -> str | None:
    """The error code, under the ``error`` envelope or (``/api/invites``) at the top."""
    return body.get("error", body).get("code")


async def _open_tags(world: World, user: User) -> list[tuple[str, str]]:
    directory = UnitDirectoryService(world.session)
    membership = await directory.membership_for(world.actor(user))
    return [(entry.unit_code.value, entry.role.value) for entry in membership.entries]


# --- labels -------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_01_the_short_labels_and_function_tags() -> None:
    assert unit_short_label(UnitCode.PR) == "PR"
    assert unit_short_label(UnitCode.ADS) == "ORD"
    assert function_tag(UnitCode.ADS, UnitMemberRole.BIEN_TAP) == "BT"
    assert function_tag(UnitCode.ADS, UnitMemberRole.THIET_KE) == "TK"
    assert function_tag(UnitCode.ADS, UnitMemberRole.DUNG) == "D"
    for role in (UnitMemberRole.HEAD, UnitMemberRole.ORDERER):
        assert function_tag(UnitCode.ADS, role) is None
    assert function_tag(UnitCode.PR, UnitMemberRole.MEMBER) is None


# --- no tag on join -------------------------------------------------------------------


async def test_02_redeeming_an_invite_creates_no_tag(world: World) -> None:
    await seed_units(world)
    service = InviteService(world.session, AuditService(world.session))
    _, code = await service.create(
        actor=world.actor(world.owner), request_id=uuid.uuid4(), role=Role.EMPLOYEE
    )
    user = await service.redeem(
        request_id=uuid.uuid4(), code=code, telegram_user_id=55501, full_name="Người mới"
    )
    rows = await world.session.scalar(
        select(func.count()).select_from(OrgUnitMember).where(OrgUnitMember.user_id == user.id)
    )
    assert rows == 0
    assert await _open_tags(world, user) == []
    world.act_as(user)
    assert world.client.get("/api/units/me").json()["units"] == []
    assert world.client.get("/api/pr/dashboard").status_code == 404


# --- who may tag ----------------------------------------------------------------------


async def test_03_a_pr_team_lead_tags_and_untags_in_pr_only(world: World) -> None:
    await seed_units(world)
    person = await newcomer(world)
    world.act_as(world.lead)
    me = world.client.get("/api/units/me").json()
    assert me["can_tag"] == ["PR"]
    assert me["can_admin"] == []

    added = world.client.post(
        "/api/units/PR/members", json={"user_id": str(person.id), "role": "MEMBER"}
    )
    assert added.status_code == 201, added.json()
    assert added.json()["function_tag"] is None
    assert await _open_tags(world, person) == [("PR", "MEMBER")]
    changed = world.client.patch(f"/api/units/PR/members/{person.id}", json={"is_lead": True})
    assert changed.status_code == 200, changed.json()
    assert changed.json()["is_lead"] is False  # no function, no lead
    removed = world.client.delete(f"/api/units/PR/members/{person.id}")
    assert removed.status_code == 200, removed.json()
    assert await _open_tags(world, person) == []

    # Not in ORD: refused there, by name.
    response = world.client.post(
        "/api/units/ADS/members", json={"user_id": str(person.id), "role": "ORDERER"}
    )
    assert response.status_code == 403
    assert _code(response.json()) == "unit_tag_forbidden"
    assert error_reason(response.json()) == "unit_tag_forbidden"
    # Nor the settings: tagging is not administering.
    assert world.client.patch("/api/units/PR/settings", json={"urgent_days": 3}).status_code == 403


async def test_04_a_team_lead_moves_an_ord_person_into_pr_but_not_out_of_ord(
    world: World,
) -> None:
    units = await seed_units(world)
    person = await newcomer(world)
    await tag(world, units[UnitCode.ADS], person, UnitMemberRole.DUNG)
    world.act_as(world.lead)
    added = world.client.post(
        "/api/units/PR/members", json={"user_id": str(person.id), "role": "MEMBER"}
    )
    assert added.status_code == 201, added.json()
    response = world.client.delete(f"/api/units/ADS/members/{person.id}")
    assert response.status_code == 403
    assert _code(response.json()) == "unit_tag_forbidden"
    assert sorted(await _open_tags(world, person)) == [("ADS", "DUNG"), ("PR", "MEMBER")]


async def test_05_a_team_lead_never_touches_admins_owners_or_themselves(world: World) -> None:
    await seed_units(world)
    admin = await newcomer(world, "Quản trị", Role.ADMIN)
    world.act_as(world.lead)
    for target in (admin, world.owner):
        response = world.client.post(
            "/api/units/PR/members", json={"user_id": str(target.id), "role": "MEMBER"}
        )
        assert response.status_code == 403, target.full_name
        assert _code(response.json()) == "unit_tag_forbidden"
    response = world.client.delete(f"/api/units/PR/members/{world.lead.id}")
    assert response.status_code == 403
    assert _code(response.json()) == "unit_tag_forbidden"
    response = world.client.patch(f"/api/units/PR/members/{world.lead.id}", json={"is_lead": True})
    assert response.status_code == 403
    assert await _open_tags(world, world.lead) == [("PR", "MEMBER")]


async def test_06_an_employee_may_not_tag_and_an_outsider_reads_404(world: World) -> None:
    await seed_units(world)
    person = await newcomer(world)
    world.act_as(world.member)
    assert world.client.get("/api/units/me").json()["can_tag"] == []
    response = world.client.post(
        "/api/units/PR/members", json={"user_id": str(person.id), "role": "MEMBER"}
    )
    assert response.status_code == 403
    assert _code(response.json()) == "unit_tag_forbidden"
    # Outside ORD and unable to tag anywhere: not even told it exists.
    response = world.client.post(
        "/api/units/ADS/members", json={"user_id": str(person.id), "role": "ORDERER"}
    )
    assert response.status_code == 404
    # An untagged team lead tags nowhere.
    loner = await newcomer(world, "Trưởng nhóm mới", Role.TEAM_LEAD)
    world.act_as(loner)
    assert world.client.get("/api/units/me").json()["can_tag"] == []
    response = world.client.post(
        "/api/units/PR/members", json={"user_id": str(person.id), "role": "MEMBER"}
    )
    assert response.status_code == 404


async def test_07_an_admin_tags_anyone_anywhere_and_sees_all(world: World) -> None:
    await seed_units(world)
    admin = await newcomer(world, "Quản trị", Role.ADMIN)
    other_admin = await newcomer(world, "Quản trị 2", Role.ADMIN)
    person = await newcomer(world)
    world.act_as(admin)
    me = world.client.get("/api/units/me").json()
    assert me["can_view_all"] is True
    assert me["can_tag"] == ["ADS", "PR"] and me["can_admin"] == ["ADS", "PR"]
    tagged = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(person.id), "role": "BIEN_TAP", "is_lead": True},
    )
    assert tagged.status_code == 201, tagged.json()
    assert tagged.json()["function_tag"] == "BT" and tagged.json()["is_lead"] is True
    assert (
        world.client.post(
            "/api/units/PR/members", json={"user_id": str(other_admin.id), "role": "MEMBER"}
        ).status_code
        == 201
    )
    listed = world.client.get("/api/units/ADS/members").json()
    assert listed["unit_label"] == "Luồng Order (ORD)" and listed["unit_short_label"] == "ORD"
    row = next(item for item in listed["members"] if item["user_id"] == str(person.id))
    assert row["function_tag"] == "BT"
    assert row["role_label"] == "Trưởng phòng Biên kịch"
    assert row["avatar_url"] is None
    assert world.client.get("/api/board/tasks", params={"unit": "ALL"}).status_code == 200

    # The account screen: everyone, with the tags in full.
    members = world.client.get("/api/account/members").json()["members"]
    by_id = {item["user_id"]: item for item in members}
    assert by_id[str(person.id)]["function_tag"] == "BT"
    assert by_id[str(person.id)]["is_lead"] is True
    assert by_id[str(person.id)]["active"] is True
    assert by_id[str(person.id)]["unit_tags"] == [
        {
            "code": "ADS",
            "label": "Luồng Order (ORD)",
            "short_label": "ORD",
            "role": "BIEN_TAP",
            "role_label": "Trưởng phòng Biên kịch",
            "is_lead": True,
            "function_tag": "BT",
            "member_code": None,
        }
    ]
    assert str(world.member.id) in by_id  # a PR member, outside any ADMIN tag

    world.act_as(person)
    units = world.client.get("/api/account/me").json()["units"]
    assert units[0]["short_label"] == "ORD" and units[0]["function_tag"] == "BT"


# --- untagged list ----------------------------------------------------------------------


async def test_08_the_untagged_list_and_who_may_read_it(world: World) -> None:
    await seed_units(world)
    newbie = await newcomer(world, "Người mới")
    gone = await newcomer(world, "Đã nghỉ")
    gone.active = False
    await world.session.flush()

    for reader in (world.owner, world.lead):
        world.act_as(reader)
        response = world.client.get("/api/units/untagged")
        assert response.status_code == 200, response.json()
        users = response.json()["users"]
        ids = {item["user_id"] for item in users}
        assert str(newbie.id) in ids
        # Tagged people and deactivated accounts are not listed.
        assert not ids & {str(world.member.id), str(world.lead.id), str(gone.id)}
        row = next(item for item in users if item["user_id"] == str(newbie.id))
        assert set(row) == {
            "user_id",
            "full_name",
            "telegram_username",
            "role",
            "role_label",
            "created_at",
            "avatar_url",
        }
        assert row["role"] == "EMPLOYEE" and row["role_label"] == "Nhân viên"

    admin = await newcomer(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert world.client.get("/api/units/untagged").status_code == 200

    for refused in (world.member, await newcomer(world, "TN chưa gắn", Role.TEAM_LEAD)):
        world.act_as(refused)
        response = world.client.get("/api/units/untagged")
        assert response.status_code == 403
        assert _code(response.json()) == "unit_tag_forbidden"


# --- deactivate / reactivate -------------------------------------------------------------


async def test_09_admins_deactivate_and_reactivate_accounts(world: World) -> None:
    await seed_units(world)
    admin = await newcomer(world, "Quản trị", Role.ADMIN)
    auth = WebAuthService(world.session, world.settings)
    await auth.issue_session(user=world.member, auth_method=WebSessionAuthMethod.PASSWORD)

    world.act_as(admin)
    response = world.client.post(f"/api/account/members/{world.member.id}/deactivate")
    assert response.status_code == 204, response.text
    await world.session.refresh(world.member)
    assert world.member.active is False
    open_sessions = await world.session.scalar(
        select(func.count())
        .select_from(WebSession)
        .where(WebSession.user_id == world.member.id, WebSession.revoked_at.is_(None))
    )
    assert open_sessions == 0
    audit = await world.session.scalar(
        select(AuditLog).where(
            AuditLog.action == "user.suspended", AuditLog.entity_id == str(world.member.id)
        )
    )
    assert audit is not None
    # Gone from the list, back with include_inactive (admins only).
    members = world.client.get("/api/account/members").json()["members"]
    assert str(world.member.id) not in {item["user_id"] for item in members}
    members = world.client.get("/api/account/members?include_inactive=true").json()["members"]
    row = next(item for item in members if item["user_id"] == str(world.member.id))
    assert row["active"] is False
    # The tag survives a deactivation.
    assert await _open_tags(world, world.member) == [("PR", "MEMBER")]

    response = world.client.post(f"/api/account/members/{world.member.id}/reactivate")
    assert response.status_code == 204, response.text
    await world.session.refresh(world.member)
    assert world.member.active is True


async def test_10_deactivation_is_refused_to_others_on_self_and_on_owners(world: World) -> None:
    await seed_units(world)
    admin = await newcomer(world, "Quản trị", Role.ADMIN)
    cases: list[tuple[User, User]] = [
        (world.member, world.lead),  # an employee
        (world.lead, world.member),  # a team lead
        (admin, admin),  # oneself
        (admin, world.owner),  # an ADMIN on the OWNER
        (world.owner, world.owner),  # the OWNER on themselves
    ]
    for actor, target in cases:
        world.act_as(actor)
        for path in ("deactivate", "reactivate"):
            response = world.client.post(f"/api/account/members/{target.id}/{path}")
            assert response.status_code == 403, (actor.full_name, target.full_name, path)
            assert _code(response.json()) == "account_status_forbidden"
    world.act_as(admin)
    assert world.client.post(f"/api/account/members/{uuid.uuid4()}/deactivate").status_code == 404
    # include_inactive is for OWNER/ADMIN only.
    units = await seed_units(world)
    await tag(world, units[UnitCode.ADS], world.lead, UnitMemberRole.HEAD)
    world.act_as(world.lead)
    assert world.client.get("/api/account/members?unit=ADS").status_code == 200
    response = world.client.get("/api/account/members?include_inactive=true&unit=ADS")
    assert response.status_code == 403
    # The OWNER deactivates an ADMIN.
    world.act_as(world.owner)
    assert world.client.post(f"/api/account/members/{admin.id}/deactivate").status_code == 204


# --- invites ------------------------------------------------------------------------------


async def test_11_web_invites_are_for_team_leads_and_above(world: World) -> None:
    world.act_as(world.member)
    for response in (
        world.client.get("/api/invites"),
        world.client.post("/api/invites", json={}),
    ):
        assert response.status_code == 403
        assert _code(response.json()) == "invite_forbidden"

    world.act_as(world.lead)
    created = world.client.post("/api/invites", json={})
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["role"] == "EMPLOYEE" and body["code"]
    # A team lead may not mint a team lead.
    assert world.client.post("/api/invites", json={"role": "TEAM_LEAD"}).status_code == 403
    listed = world.client.get("/api/invites").json()
    assert [item["id"] for item in listed["items"]] == [body["id"]]
    assert "code" not in listed["items"][0]

    # Somebody else's invite is not theirs to revoke.
    world.act_as(world.owner)
    owners = world.client.post("/api/invites", json={"role": "TEAM_LEAD"})
    assert owners.status_code == 201
    world.act_as(world.lead)
    assert world.client.post(f"/api/invites/{owners.json()['id']}/disable").status_code == 404
    revoked = world.client.post(f"/api/invites/{body['id']}/disable")
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    assert world.client.get("/api/invites").json()["items"] == []


# --- board: short labels and "todo first" -----------------------------------------------------


def _tasks(world: World, **params: Any) -> dict[str, Any]:
    response = world.client.get("/api/board/tasks", params=params)
    assert response.status_code == 200, response.json()
    return response.json()


async def test_12_ord_todo_first_lists_what_waits_on_me_first_and_pages(world: World) -> None:
    ads = await ads_world(world)
    # Five orders with the writer chosen for the script. The three oldest are
    # approved, so the writer holds their script node: they wait on the writer.
    # The two newest still wait for the head: the writer sees them, but they do
    # not wait on the writer.
    pick = {"BIEN_TAP": str(ads.writer.id)}
    waiting = []
    for n in range(3):
        detail = create(world, ads, title=f"Đang viết {n}", preassigned=pick)
        act(world, ads.head, "/approve", detail)
        waiting.append(detail["order"]["code"])
    for n in range(2):
        create(world, ads, title=f"Chờ duyệt {n}", preassigned=pick)

    world.act_as(ads.writer)
    plain = [row["code"] for row in _tasks(world, unit="ADS")["items"]]
    assert plain[0] not in waiting  # newest first: the pending ones lead
    page = _tasks(world, unit="ADS", order="todo_first")
    rows = page["items"]
    assert page["total"] == 5
    assert [row["code"] for row in rows[:3]] == waiting[::-1]
    assert [row["awaiting_me"] for row in rows] == [True, True, True, False, False]
    assert rows[0]["unit_short_label"] == "ORD" and rows[0]["unit_label"] == "Luồng Order (ORD)"
    # Paging continues across the two parts, nothing repeated or skipped.
    paged = [
        row["code"]
        for offset in (0, 2, 4)
        for row in _tasks(world, unit="ADS", order="todo_first", limit=2, offset=offset)["items"]
    ]
    assert paged == [row["code"] for row in rows]
    # The marker is there without the order too.
    marked = {row["code"]: row["awaiting_me"] for row in _tasks(world, unit="ADS")["items"]}
    assert {code for code, flag in marked.items() if flag} == set(waiting)
    # An unknown order is refused.
    response = world.client.get("/api/board/tasks", params={"unit": "ADS", "order": "random"})
    assert response.status_code == 422


async def test_13_pr_and_all_todo_first(world: World) -> None:
    ads = await ads_world(world)
    items = {item.code: item for item in [await world.content() for _ in range(3)]}
    world.act_as(world.lead)
    # The item the plain order lists last is the one put at the lead's gate.
    reviewed = items[_tasks(world, unit="PR")["items"][-1]["code"]]
    reviewed.workflow_stage = PrWorkflowStage.TEAM_LEAD_REVIEW
    await world.session.flush()
    await world.grant(
        world.lead,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope(content_type_scope=PrGrantScopeMode.ALL, channel_scope=PrGrantScopeMode.ALL),
    )
    world.act_as(world.lead)
    assert {row["code"] for row in _tasks(world, unit="PR", awaiting_me="true")["items"]} == {
        reviewed.code
    }
    plain = _tasks(world, unit="PR")["items"]
    assert plain[0]["code"] != reviewed.code
    assert plain[0]["unit_short_label"] == "PR"
    page = _tasks(world, unit="PR", order="todo_first")
    assert page["total"] == 3
    assert page["items"][0]["code"] == reviewed.code
    assert [row["awaiting_me"] for row in page["items"]] == [True, False, False]
    paged = [
        row["code"]
        for offset in (0, 1, 2)
        for row in _tasks(world, unit="PR", order="todo_first", limit=1, offset=offset)["items"]
    ]
    assert paged == [row["code"] for row in page["items"]]

    # "Tất cả" for the OWNER: whatever waits on them (both streams) first.
    pending = create(world, ads, title="Chờ duyệt")["order"]["code"]
    world.act_as(world.owner)
    expected = {
        row["code"] for row in _tasks(world, unit="ALL", awaiting_me="true", limit=50)["items"]
    }
    everything = _tasks(world, unit="ALL", order="todo_first", limit=20)
    assert everything["total"] == 4
    assert pending in expected and 0 < len(expected) < 4
    head = everything["items"][: len(expected)]
    assert {row["code"] for row in head} == expected
    assert [row["awaiting_me"] for row in everything["items"]] == [True] * len(expected) + [
        False
    ] * (4 - len(expected))
    paged = [
        row["code"]
        for offset in (0, 2)
        for row in _tasks(world, unit="ALL", order="todo_first", limit=2, offset=offset)["items"]
    ]
    assert paged == [row["code"] for row in everything["items"]]


async def test_14_task_detail_carries_the_short_label(world: World) -> None:
    item = await world.content()
    world.act_as(world.member)
    response = world.client.get(f"/api/tasks/{item.code}")
    assert response.status_code == 200, response.json()
    task = response.json()["task"]
    assert task["unit_label"] == "Luồng PR" and task["unit_short_label"] == "PR"
