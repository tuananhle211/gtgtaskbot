"""The unified task: the projection, the page and the action endpoint.

* **the projection needs no backfill** - creating PR content or an Ads order
  through the ordinary services writes its ``tasks`` row in the same flush,
  and every stage change moves the row's stage, phase and clocks;
* **the page** - ``GET /api/tasks/{ref}`` answers for both units by task
  code, task id or source id, behind the unit wall (outsiders get 404, the
  OWNER sees both);
* **the actions** - ``POST /api/tasks/{ref}/actions`` dispatches the opaque
  keys the page offered to each unit's own write, and a stale version is a
  409 with the unit's own reason.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from meobot.application.pr_services import build_pr_services
from meobot.db.models.task import Task
from meobot.domain.orders.labels import PROCESS_SEPARATOR as SEP
from meobot.domain.pr.grants import GrantScope
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.units.models import UnitCode, UnitMemberRole, unit_seed_id
from tests.unit.pr_world import World
from tests.unit.test_order_commands import ads_world, create
from tests.unit.test_units_gating import error_reason, seed_units, tag, untag

pytestmark = pytest.mark.asyncio


async def task_for(world: World, **where: Any) -> Task | None:
    statement = select(Task).execution_options(populate_existing=True)
    for key, value in where.items():
        statement = statement.where(getattr(Task, key) == value)
    return await world.session.scalar(statement)


async def new_content(world: World) -> tuple[uuid.UUID, str]:
    item = await world.content(channels=(world.tiktok,))
    return item.id, item.code


def post(world: World, ref: str, key: str, version: int, **body: Any) -> Any:
    return world.client.post(
        f"/api/tasks/{ref}/actions", json={"key": key, "version": version, **body}
    )


def keys(detail: dict[str, Any]) -> list[str]:
    return [action["key"] for action in detail["actions"]]


# --- the projection ----------------------------------------------------------------


async def test_01_pr_content_gets_its_task_and_follows_its_stage(world: World) -> None:
    content_id, code = await new_content(world)
    task = await task_for(world, pr_content_id=content_id)
    assert task is not None
    assert (task.code, task.source_type, task.stage, task.phase) == (
        code,
        "PR_CONTENT",
        "IDEA",
        "ORDER",
    )
    assert task.unit_id == unit_seed_id(UnitCode.PR)
    assert task.kind == "SHORT_VIDEO_SCRIPT" and task.finished_at is None
    first_since = task.stage_since

    await world.to_team_lead_review(content_id)
    task = await task_for(world, pr_content_id=content_id)
    assert task is not None
    assert (task.stage, task.phase) == ("TEAM_LEAD_REVIEW", "REVIEW")
    assert task.stage_since >= first_since

    services = build_pr_services(world.session, world.settings)
    await services.workflow.cancel(
        actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
    )
    task = await task_for(world, pr_content_id=content_id)
    assert task is not None
    assert (task.stage, task.phase) == ("CANCELLED", "CANCELLED")
    assert task.finished_at is not None


async def test_02_an_order_gets_its_task_and_follows_its_stage(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads)
    order_id = uuid.UUID(detail["order"]["id"])
    task = await task_for(world, order_id=order_id)
    assert task is not None
    assert (task.code, task.source_type, task.stage, task.phase, task.kind) == (
        detail["order"]["code"],
        "ORDER",
        "ORDER_PENDING",
        "REVIEW",
        "BTD",
    )
    assert task.unit_id == ads.unit.id and task.owner_user_id == ads.orderer.id

    world.act_as(ads.head)
    response = post(world, detail["order"]["code"], "ads:APPROVE_ORDER", 1)
    assert response.status_code == 200, response.json()
    task = await task_for(world, order_id=order_id)
    assert task is not None
    assert (task.stage, task.phase) == ("BIEN_TAP", "PRODUCTION")


async def test_03_deleting_content_takes_its_task(world: World) -> None:
    content_id, _ = await new_content(world)
    services = build_pr_services(world.session, world.settings)
    await services.lifecycle.delete_content(
        actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
    )
    assert await task_for(world, pr_content_id=content_id) is None


# --- the page ----------------------------------------------------------------------


async def test_04_the_pr_page_by_code_task_id_and_content_id(world: World) -> None:
    content_id, code = await new_content(world)
    task = await task_for(world, pr_content_id=content_id)
    assert task is not None
    task_id = task.id
    world.act_as(world.owner)
    by_code = world.client.get(f"/api/tasks/{code}")
    assert by_code.status_code == 200, by_code.json()
    body = by_code.json()
    summary = body["task"]
    assert summary["id"] == str(task_id) and summary["code"] == code
    assert summary["unit"] == "PR" and summary["unit_label"] == "Luồng PR"
    assert summary["unit_short_label"] == "PR"
    assert summary["phase"] == "ORDER" and summary["stage"] == "IDEA"
    assert summary["stage_label"] == "Ý tưởng" and summary["kind_label"] == "Kịch bản video ngắn"
    assert summary["source"] == {"type": "PR_CONTENT", "id": str(content_id)}
    assert summary["version"] == 1 and summary["owner"]["name"] == world.owner.full_name
    assert [step["key"] for step in body["steps"]] == [
        "ORDER",
        "REVIEW",
        "PRODUCTION",
        "FINAL_REVIEW",
        "DONE",
    ]
    groups = {item["key"]: item["group"] for item in body["fields"]}
    assert groups["content"] == "common" and groups["brand"] == "pr"
    assert not any(group == "ads" for group in groups.values())
    assert "pr:TRANSITION:BRIEFING" in keys(body)
    assert body["timeline"][0]["label"] == "Tạo nội dung"
    assert any(item["label"] == "Kịch bản V1" for item in body["submissions"])
    for ref in (str(task_id), str(content_id)):
        again = world.client.get(f"/api/tasks/{ref}")
        assert again.status_code == 200 and again.json()["task"]["code"] == code


async def test_05_the_wall_holds_for_pr(world: World) -> None:
    units = await seed_units(world)
    _, code = await new_content(world)
    await untag(world, units[UnitCode.PR], world.member)
    await tag(world, units[UnitCode.ADS], world.member, UnitMemberRole.ORDERER, member_code="HAO")
    world.act_as(world.member)
    response = world.client.get(f"/api/tasks/{code}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"
    assert error_reason(response.json()) == "unit_not_visible"
    # Nor can they act on it.
    assert post(world, code, "pr:TRANSITION:BRIEFING", 1).status_code == 404
    assert world.client.get("/api/tasks/NO-SUCH-CODE").status_code == 404


async def test_06_the_ads_page_and_its_wall(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads)
    code = detail["order"]["code"]

    world.act_as(ads.head)
    response = world.client.get(f"/api/tasks/{code}")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["task"]["unit"] == "ADS" and body["task"]["stage"] == "ORDER_PENDING"
    assert body["task"]["phase"] == "REVIEW" and body["task"]["version"] == 1
    assert body["task"]["kind_label"] == f"Biên kịch{SEP}Design{SEP}Dựng"
    assert body["task"]["source"] == {"type": "ORDER", "id": detail["order"]["id"]}
    assert {"ads:APPROVE_ORDER", "ads:RETURN_ORDER", "ads:CANCEL"} <= set(keys(body))
    groups = {item["key"]: item["group"] for item in body["fields"]}
    assert groups["process"] == "ads" and not any(g == "pr" for g in groups.values())
    # No "Gắn link" step: the three production nodes, then the final review.
    assert [step["key"] for step in body["steps"]] == ["BIEN_TAP", "THIET_KE", "DUNG", "FINAL"]
    assert all("Gắn link" not in step["label"] for step in body["steps"])
    assert body["task"]["state"] == "CHO_DUYET"
    assert body["timeline"][0]["label"] == "Gửi order"
    returned = next(a for a in body["actions"] if a["key"] == "ads:RETURN_ORDER")
    assert returned["requires_note"] is True and returned["inputs"] == ["note"]

    # The order id works as a ref too.
    assert world.client.get(f"/api/tasks/{detail['order']['id']}").status_code == 200
    # A PR member (untagged: PR by the legacy rule) does not see Ads.
    world.act_as(ads.outsider)
    hidden = world.client.get(f"/api/tasks/{code}")
    assert hidden.status_code == 404 and error_reason(hidden.json()) == "unit_not_visible"
    # The OWNER sees both.
    world.act_as(world.owner)
    assert world.client.get(f"/api/tasks/{code}").status_code == 200


# --- the actions -------------------------------------------------------------------


async def test_07_ads_actions_run_through_the_order_engine(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads)
    code = detail["order"]["code"]

    world.act_as(ads.head)
    approved = post(world, code, "ads:APPROVE_ORDER", 1)
    assert approved.status_code == 200, approved.json()
    body = approved.json()
    assert body["task"]["stage"] == "BIEN_TAP" and body["task"]["version"] == 2

    # The same button again, on the version the screen still shows.
    stale = post(world, code, "ads:APPROVE_ORDER", 1)
    assert stale.status_code == 409
    assert error_reason(stale.json()) == "order_stale_version"

    # The script lead hands the node to a writer, picked from the offered list.
    world.act_as(ads.lead_bt)
    page = world.client.get(f"/api/tasks/{code}").json()
    assign = next(a for a in page["actions"] if a["key"].startswith("ads:ASSIGN:"))
    assert "assignee" in assign["inputs"]
    assert str(ads.writer.id) in {option["user_id"] for option in assign["assignee_options"]}
    assigned = post(
        world,
        code,
        assign["key"],
        page["task"]["version"],
        assignee_user_id=str(ads.writer.id),
    )
    assert assigned.status_code == 200, assigned.json()

    # Handed out, not accepted: the writer sees "Đã giao" and may only accept.
    world.act_as(ads.writer)
    page = world.client.get(f"/api/tasks/{code}").json()
    assert page["task"]["state"] == "DA_GIAO"
    assert page["task"]["stage_label"] == f"Biên tập · Đã giao {ads.writer.full_name}"
    steps = {step["key"]: step for step in page["steps"]}
    assert steps["BIEN_TAP"]["status"] == "DA_GIAO"
    assert steps["BIEN_TAP"]["status_label"] == f"Đã giao {ads.writer.full_name}"
    assert not any(a["key"].startswith("ads:SUBMIT_WORK:") for a in page["actions"])
    accept = next(a for a in page["actions"] if a["key"].startswith("ads:ACCEPT:"))
    accepted = post(world, code, accept["key"], page["task"]["version"])
    assert accepted.status_code == 200, accepted.json()
    assert accepted.json()["task"]["state"] == "DANG_LAM"

    # The writer hands in the script (not the last node: no link required).
    page = accepted.json()
    submit = next(a for a in page["actions"] if a["key"].startswith("ads:SUBMIT_WORK:"))
    assert submit["inputs"] == ["text", "link", "note"]
    assert submit["required_inputs"] == []
    submitted = post(
        world, code, submit["key"], page["task"]["version"], text="Kịch bản hoàn chỉnh."
    )
    assert submitted.status_code == 200, submitted.json()
    after = submitted.json()
    assert after["submissions"][0]["text"] == "Kịch bản hoàn chỉnh."
    assert after["task"]["stage"] != "BIEN_TAP"

    # A key from the other unit, or none at all, is refused.
    bad = post(world, code, "pr:TRANSITION:BRIEFING", after["task"]["version"])
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "task_action_unknown"


async def test_08_pr_actions_run_through_the_pr_services(world: World) -> None:
    content_id, code = await new_content(world)
    world.act_as(world.owner)
    moved = post(world, code, "pr:TRANSITION:BRIEFING", 1, note="Bắt đầu")
    assert moved.status_code == 200, moved.json()
    assert moved.json()["task"]["stage"] == PrWorkflowStage.BRIEFING.value

    # A draft number that is not the one on top is stale.
    stale = post(world, code, "pr:TRANSITION:SCRIPTING", 5)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "pr_stale_version"

    # Priority toggles "Ưu tiên".
    flagged = post(world, code, "pr:SET_PRIORITY", 1)
    assert flagged.status_code == 200, flagged.json()
    assert flagged.json()["task"]["is_priority"] is True

    # A move the PR matrix does not allow is PR's own refusal.
    refused = post(world, code, "pr:TRANSITION:PUBLISHED", 1)
    assert refused.status_code in (403, 409)

    task = await task_for(world, pr_content_id=content_id)
    assert task is not None and task.stage == "BRIEFING" and task.is_priority is True


async def test_09_a_pr_approval_through_the_task(world: World) -> None:
    content_id, code = await new_content(world)
    await world.to_team_lead_review(content_id)
    await world.grant(world.lead, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())
    world.act_as(world.lead)
    page = world.client.get(f"/api/tasks/{code}").json()
    assert "pr:APPROVAL:APPROVED" in keys(page)
    approved = post(world, code, "pr:APPROVAL:APPROVED", page["task"]["version"], note="OK")
    assert approved.status_code == 200, approved.json()
    body = approved.json()
    assert body["task"]["stage"] == "HEAD_REVIEW"
    assert any(entry["label"] == "Trưởng nhóm duyệt: Duyệt" for entry in body["timeline"])


async def test_10_assigning_a_pr_producer_through_the_task_reads_back(world: World) -> None:
    """The write flushes the PR row; the page rebuilt after it must not lazy-load."""
    item = await world.content(channels=(world.tiktok,))
    code, member_id, member_name = item.code, world.member.id, world.member.full_name
    item.workflow_stage = PrWorkflowStage.APPROVED
    await world.session.flush()
    await world.session.refresh(item)

    world.act_as(world.owner)
    detail = world.client.get(f"/api/tasks/{code}").json()
    assert "pr:ASSIGN_PRODUCER" in keys(detail)
    assigned = post(
        world,
        code,
        "pr:ASSIGN_PRODUCER",
        detail["task"]["version"],
        assignee_user_id=str(member_id),
    )
    assert assigned.status_code == 200, assigned.json()
    assert assigned.json()["task"]["current_person"]["name"] == member_name


async def test_11_the_ads_page_shows_the_process_the_kind_and_its_points(world: World) -> None:
    from decimal import Decimal

    from meobot.db.models.org_unit import UnitVideoKind

    ads = await ads_world(world)
    kind = UnitVideoKind(unit_id=ads.unit.id, name="Quay cả ngày", points=Decimal("3"))
    world.session.add(kind)
    await world.session.flush()
    detail = create(world, ads, video_type="BD", design_link="d", video_kind_id=str(kind.id))
    world.act_as(ads.head)
    body = world.client.get(f"/api/tasks/{detail['order']['code']}").json()
    fields = {item["key"]: item for item in body["fields"]}
    assert fields["process"]["label"] == "Quy trình"
    assert fields["process"]["value"] == f"Biên kịch{SEP}Dựng"
    assert fields["video_kind"]["label"] == "Loại video"
    assert fields["video_kind"]["value"] == "Quay cả ngày"
    assert fields["video_kind_points"]["label"] == "Điểm hiệu suất"
    assert fields["video_kind_points"]["value"] == "3"
    assert all(
        fields[key]["group"] == "ads" for key in ("process", "video_kind", "video_kind_points")
    )
    assert body["task"]["kind"] == "BD" and body["task"]["kind_label"] == "Quay cả ngày"
    # The strip still shows every node; the skipped one says so.
    steps = {step["key"]: step for step in body["steps"]}
    assert steps["THIET_KE"]["status"] == "BO_QUA"


async def test_12_the_last_node_hands_in_the_product_through_the_task(world: World) -> None:
    ads = await ads_world(world)
    detail = create(
        world, ads, video_type="D", design_link="d", preassigned={"DUNG": str(ads.editor.id)}
    )
    code = detail["order"]["code"]
    world.act_as(ads.head)
    assert post(world, code, "ads:APPROVE_ORDER", 1).status_code == 200
    world.act_as(ads.editor)
    page = world.client.get(f"/api/tasks/{code}").json()
    accept = next(a for a in page["actions"] if a["key"].startswith("ads:ACCEPT:"))
    page = post(world, code, accept["key"], page["task"]["version"]).json()
    submit = next(a for a in page["actions"] if a["key"].startswith("ads:SUBMIT_WORK:"))
    assert submit["label"] == "Nộp sản phẩm · Dựng"
    assert submit["required_inputs"] == ["link"]
    assert all("ATTACH_LINK" not in action["key"] for action in page["actions"])
    missing = post(world, code, submit["key"], page["task"]["version"], note="Xong")
    assert missing.status_code == 422 and error_reason(missing.json()) == "link_required"
    done = post(world, code, submit["key"], page["task"]["version"], link="https://e.com/cut")
    assert done.status_code == 200, done.json()
    # The edit is reviewed by its Leader by default; the approval opens the
    # orderer's final review with the cut as the product.
    world.act_as(ads.lead_dung)
    page = world.client.get(f"/api/tasks/{code}").json()
    approve = next(a for a in page["actions"] if a["key"].startswith("ads:APPROVE_NODE:"))
    final = post(world, code, approve["key"], page["task"]["version"]).json()
    assert final["task"]["stage"] == "FINAL_REVIEW"
    assert final["task"]["product_link"] == "https://e.com/cut"
    steps = {step["key"]: step for step in final["steps"]}
    assert steps["DUNG"]["status"] == "HOAN_THANH"
    assert steps["FINAL"]["status"] == "CHO_DUYET" and steps["FINAL"]["is_current"]
