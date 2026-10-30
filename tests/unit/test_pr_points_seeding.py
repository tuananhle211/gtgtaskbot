"""PR task codes, +10 per publication, and the seeding group (0085).

* a PR piece's **Nội dung** codes are its owner's to type once the content is
  approved, its **Sản xuất** codes anybody in PR who worked on it once it is
  through internal review; a Trưởng phòng PR approves (or refuses with a
  reason), the points then show on the reports like an ORD step's;
* every publication with a link is worth 10 to whoever published it;
* the seeding group: a Trưởng phòng PR adds people, sets the team KPI, members
  hand numbers in (never twice in the system), approved numbers are worth
  200 each, "việc khác" counts once approved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.user import User
from meobot.db.models.work_points import WorkPoint
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.reporting import PrPublicationStatus
from tests.unit.pr_world import World
from tests.unit.streams import tag_pr
from tests.unit.test_order_commands import Ads, person
from tests.unit.test_task_codes import coded_world, codes_of

pytestmark = pytest.mark.asyncio


async def pr_world(world: World) -> tuple[Ads, User, User, Any]:
    """ORD's catalogue (N3 120, N1 15, T1 90, D3 240, D4 480) and two PR
    writers / makers; ``world.lead`` heads PR."""
    ads, _ = await coded_world(world)
    writer = await person(world, "Biên tập PR")
    maker = await person(world, "Dựng PR")
    await tag_pr(world.session, [writer, maker])
    await world.session.flush()
    content = await world.content()
    content.owner_user_id = writer.id
    await world.session.flush()
    return ads, writer, maker, content


def call(world: World, user: User, method: str, path: str, **body: Any) -> Any:
    world.act_as(user)
    if method == "GET":
        return world.client.get(path)
    return world.client.request(method, path, json=body or None)


async def at_stage(world: World, content: Any, stage: PrWorkflowStage) -> None:
    content.workflow_stage = stage
    await world.session.flush()


async def test_01_content_codes_wait_for_approval_then_count(world: World) -> None:
    _, writer, maker, content = await pr_world(world)
    codes = codes_of(world)
    base = f"/api/performance/pr/contents/{content.id}"
    await at_stage(world, content, PrWorkflowStage.SCRIPTING)
    page = call(world, writer, "GET", base).json()
    stages = {stage["stage"]: stage for stage in page["stages"]}
    assert stages["CONTENT"]["open"] is False and stages["CONTENT"]["may_claim"] is False
    early = call(
        world,
        writer,
        "POST",
        f"{base}/claims",
        stage="CONTENT",
        task_codes={"BIEN_TAP": [{"code_id": codes["N3"]}]},
    )
    assert early.status_code == 422 and early.json()["details"]["reason"] == "pr_points_not_open"

    await at_stage(world, content, PrWorkflowStage.APPROVED)
    sent = call(
        world,
        writer,
        "POST",
        f"{base}/claims",
        stage="CONTENT",
        task_codes={
            "BIEN_TAP": [{"code_id": codes["N3"]}, {"code_id": codes["N1"], "quantity": 2}]
        },
    )
    assert sent.status_code == 200, sent.json()
    claim = sent.json()
    assert claim["status"] == "PENDING" and claim["points"] == 120 + 30
    # Only the owner types the content's codes; production waits.
    other = call(
        world,
        maker,
        "POST",
        f"{base}/claims",
        stage="CONTENT",
        task_codes={"BIEN_TAP": [{"code_id": codes["N3"]}]},
    )
    assert other.status_code == 403
    production = call(
        world,
        maker,
        "POST",
        f"{base}/claims",
        stage="PRODUCTION",
        task_codes={"DUNG": [{"code_id": codes["D3"]}]},
    )
    assert production.status_code == 422
    # Not counted before the Trưởng phòng decides.
    assert call(world, writer, "GET", "/api/performance/people/me").json()["figures"]["points"] == 0
    # A member may not decide; the Trưởng phòng PR approves.
    path = f"/api/performance/pr/claims/{claim['id']}/decide"
    assert call(world, maker, "POST", path, approve=True).status_code == 403
    queue = call(world, world.lead, "GET", "/api/performance/pr/claims?status=PENDING").json()
    assert [item["id"] for item in queue] == [claim["id"]]
    approved = call(world, world.lead, "POST", path, approve=True)
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"

    me = call(world, writer, "GET", "/api/performance/people/me").json()
    assert me["figures"]["points"] == 150 and me["figures"]["steps_done"] == 1
    line = me["lines"][0]
    assert line["source"] == "PR_CONTENT" and line["phase_label"] == "Nội dung (PR)"
    assert line["link"] == f"/tasks/{content.code}" and line["order_id"] is None
    assert {row["code"]: row["quantity"] for row in me["codes"]} == {"N3": 1, "N1": 2}
    # Approved = final.
    again = call(
        world,
        writer,
        "POST",
        f"{base}/claims",
        stage="CONTENT",
        task_codes={"BIEN_TAP": [{"code_id": codes["N3"]}]},
    )
    assert again.status_code == 409


async def test_02_production_is_each_maker_s_own_part(world: World) -> None:
    _, writer, maker, content = await pr_world(world)
    codes = codes_of(world)
    base = f"/api/performance/pr/contents/{content.id}"
    await at_stage(world, content, PrWorkflowStage.READY_TO_PUBLISH)
    dung = call(
        world,
        maker,
        "POST",
        f"{base}/claims",
        stage="PRODUCTION",
        task_codes={"DUNG": [{"code_id": codes["D3"]}]},
    ).json()
    design = call(
        world,
        writer,
        "POST",
        f"{base}/claims",
        stage="PRODUCTION",
        task_codes={"THIET_KE": [{"code_id": codes["T1"], "quantity": 2}]},
    ).json()
    assert dung["points"] == 240 and design["points"] == 180
    # Production takes no content codes, and at least one line.
    wrong = call(
        world,
        maker,
        "POST",
        f"{base}/claims",
        stage="PRODUCTION",
        task_codes={"BIEN_TAP": [{"code_id": codes["N3"]}]},
    )
    assert wrong.status_code == 422
    empty = call(world, maker, "POST", f"{base}/claims", stage="PRODUCTION", task_codes={})
    assert empty.status_code == 422

    # Refused with a reason, edited, sent again, approved.
    path = f"/api/performance/pr/claims/{design['id']}/decide"
    no_reason = call(world, world.lead, "POST", path, approve=False)
    assert no_reason.status_code == 422
    refused = call(world, world.lead, "POST", path, approve=False, return_reason="WRONG_CODES")
    assert refused.json()["status"] == "REJECTED"
    resent = call(
        world,
        writer,
        "POST",
        f"{base}/claims",
        stage="PRODUCTION",
        task_codes={"THIET_KE": [{"code_id": codes["T1"]}]},
    ).json()
    assert resent["id"] == design["id"] and resent["status"] == "PENDING"
    call(world, world.lead, "POST", path, approve=True)
    call(world, world.lead, "POST", f"/api/performance/pr/claims/{dung['id']}/decide", approve=True)
    page = call(world, writer, "GET", base).json()
    production = next(stage for stage in page["stages"] if stage["stage"] == "PRODUCTION")
    assert sorted(claim["status"] for claim in production["claims"]) == ["APPROVED", "APPROVED"]
    me = call(world, maker, "GET", "/api/performance/people/me").json()
    assert me["by_phase"] == {"DUNG": 240}

    # A Trưởng phòng's own codes stand at once.
    own = call(
        world,
        world.lead,
        "POST",
        f"{base}/claims",
        stage="PRODUCTION",
        task_codes={"DUNG": [{"code_id": codes["D4"]}]},
    ).json()
    assert own["status"] == "APPROVED"


async def test_03_a_publication_with_a_link_is_worth_ten(world: World) -> None:
    _, writer, _, content = await pr_world(world)
    publication = PrPublication(
        code=f"PUB-{uuid.uuid4().hex[:6]}",
        content_id=content.id,
        channel_id=world.tiktok.id,
        url="https://tiktok.com/@apexmed/1",
        published_at=datetime.now(UTC),
        publisher_user_id=writer.id,
        status=PrPublicationStatus.PUBLISHED,
    )
    world.session.add(publication)
    await world.session.flush()

    async def points() -> list[int]:
        rows = (
            await world.session.scalars(
                select(WorkPoint.points).where(WorkPoint.user_id == writer.id)
            )
        ).all()
        return list(rows)

    assert await points() == [10]
    me = call(world, writer, "GET", "/api/performance/people/me").json()
    assert me["by_phase"] == {"PUBLISH": 10} and me["figures"]["steps_done"] == 0
    # No link, or taken down: no points.
    publication.url = None
    await world.session.flush()
    assert await points() == []
    publication.url = "https://tiktok.com/@apexmed/1"
    publication.status = PrPublicationStatus.REMOVED
    await world.session.flush()
    assert await points() == []


async def test_04_the_seeding_group_kpi_phones_and_other_work(world: World) -> None:
    _, writer, maker, _ = await pr_world(world)
    # Not in the group yet: no menu, no access.
    assert call(world, maker, "GET", "/api/units/me").json()["seeding"] == {
        "member": False,
        "manage": False,
    }
    assert call(world, maker, "GET", "/api/seeding/overview").status_code == 403
    assert (
        call(world, maker, "POST", "/api/seeding/members", user_id=str(writer.id)).status_code
        == 403
    )
    # The Trưởng phòng PR adds people from PR.
    lead_flags = call(world, world.lead, "GET", "/api/units/me").json()
    assert (
        lead_flags["seeding"]["manage"] is True and lead_flags["performance"]["review_pr"] is True
    )
    candidates = call(world, world.lead, "GET", "/api/seeding/candidates").json()
    assert {row["name"] for row in candidates} >= {writer.full_name, maker.full_name}
    assert (
        call(world, world.lead, "POST", "/api/seeding/members", user_id=str(maker.id)).status_code
        == 204
    )
    assert call(world, maker, "GET", "/api/units/me").json()["seeding"]["member"] is True

    # One team KPI per week.
    today = call(world, maker, "GET", "/api/seeding/overview").json()["day"]
    assert (
        call(
            world, world.lead, "PUT", "/api/seeding/targets", kind="WEEK", day=today, target=4
        ).status_code
        == 204
    )

    sent = call(
        world,
        maker,
        "POST",
        "/api/seeding/phones",
        text="0912 345 678\n+84 987 654 321\n0912345678\nabc\n\n0369999999",
    ).json()
    assert sent["accepted"] == 3
    assert [line["status"] for line in sent["lines"]] == [
        "NEW",
        "NEW",
        "REPEATED",
        "INVALID",
        "NEW",
    ]
    assert sent["lines"][1]["phone"] == "0987654321"
    # Already in the system (anybody's, not refused).
    again = call(world, maker, "POST", "/api/seeding/phones", text="0987654321").json()
    assert [line["status"] for line in again["lines"]] == ["DUPLICATE"]

    pending = call(world, world.lead, "GET", "/api/seeding/phones?status=PENDING").json()
    assert len(pending) == 3
    approve = [row["id"] for row in pending if row["phone"] != "0369999999"]
    refuse = [row["id"] for row in pending if row["phone"] == "0369999999"]
    assert (
        call(
            world, maker, "POST", "/api/seeding/phones/decide", ids=approve, approve=True
        ).status_code
        == 403
    )
    assert call(
        world, world.lead, "POST", "/api/seeding/phones/decide", ids=approve, approve=True
    ).json() == {"decided": 2}
    call(world, world.lead, "POST", "/api/seeding/phones/decide", ids=refuse, approve=False)
    # A refused number may be handed in again.
    back = call(world, maker, "POST", "/api/seeding/phones", text="0369999999").json()
    assert back["lines"][0]["status"] == "NEW"

    overview = call(world, maker, "GET", "/api/seeding/overview").json()
    assert overview["week"]["target"] == 4 and overview["week"]["approved"] == 2
    assert overview["week"]["rate"] == 0.5 and overview["week"]["pending"] == 1
    row = next(item for item in overview["members"] if item["person"]["user_id"] == str(maker.id))
    assert row["approved_month"] == 2 and row["phone_points_month"] == 400

    # Việc khác: counted once approved.
    other = call(
        world, maker, "POST", "/api/seeding/other", description="Viết 5 comment", points=150
    ).json()
    assert other["status"] == "PENDING"
    assert call(
        world, world.lead, "POST", "/api/seeding/other/decide", ids=[other["id"]], approve=True
    ).json() == {"decided": 1}

    me = call(world, maker, "GET", "/api/performance/people/me").json()
    assert me["figures"]["points"] == 400 + 150
    assert me["by_phase"] == {"SEEDING": 400, "OTHER": 150}
    codes = {row["code"]: row for row in me["codes"]}
    assert codes["SĐT"]["quantity"] == 2 and codes["SĐT"]["phase_label"] == "Seeding"
    assert codes["Việc khác"]["points"] == 150

    # Removed from the group: the menu goes.
    assert call(world, world.lead, "DELETE", f"/api/seeding/members/{maker.id}").status_code == 204
    assert call(world, maker, "GET", "/api/seeding/overview").status_code == 403


async def test_05_deleting_a_task_takes_its_points_with_it(world: World) -> None:
    from meobot.db.models.order import OrderNodePoints
    from tests.unit.test_performance_reports import dung_done

    ads, writer, _, content = await pr_world(world)
    codes = codes_of(world)
    await at_stage(world, content, PrWorkflowStage.APPROVED)
    claim = call(
        world,
        writer,
        "POST",
        f"/api/performance/pr/contents/{content.id}/claims",
        stage="CONTENT",
        task_codes={"BIEN_TAP": [{"code_id": codes["N3"]}]},
    ).json()
    call(
        world, world.lead, "POST", f"/api/performance/pr/claims/{claim['id']}/decide", approve=True
    )
    assert (await world.session.scalars(select(WorkPoint.id))).all()
    # The OWNER deletes the PR piece: its claims and points go too.
    gone = call(world, world.owner, "DELETE", f"/api/tasks/{content.code}")
    assert gone.status_code == 204, gone.text
    assert (await world.session.scalars(select(WorkPoint.id))).all() == []
    # An ORD order whose steps earned points can be deleted as well.
    order_id = await dung_done(world, ads)
    code = call(world, world.owner, "GET", f"/api/orders/{order_id}").json()["order"]["code"]
    deleted = call(world, world.owner, "DELETE", f"/api/tasks/{code}")
    assert deleted.status_code == 204, deleted.text
    rows = await world.session.scalars(
        select(OrderNodePoints.id).where(OrderNodePoints.order_id == uuid.UUID(order_id))
    )
    assert rows.all() == []
