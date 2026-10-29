"""Phòng Ads (0082): Ngân sách chạy ads theo tuần, set against the week's spend."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from meobot.application.ads import budget_service
from meobot.db.models.ads import AdsBudget
from meobot.db.models.audit_log import AuditLog
from meobot.domain.ads.budget import BudgetStatus, budget_status, budget_weeks
from tests.unit.pr_world import World
from tests.unit.test_ads_department import D1, person, staffed
from tests.unit.test_ads_manual import add_google, cell

#: D1 / D2 (20-21/10) fall in week 3 (15-21/10); "today" is in week 4.
TODAY = date(2026, 10, 25)


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget_service, "vn_today", lambda: TODAY)


def test_01_the_four_weeks_and_their_status() -> None:
    weeks = budget_weeks(date(2026, 10, 14))
    assert [(week.start.day, week.end.day) for week in weeks] == [
        (1, 7),
        (8, 14),
        (15, 21),
        (22, 31),
    ]
    assert weeks[3].label == "Tuần 4 · 22/10 - 31/10"
    assert budget_weeks(date(2027, 2, 1))[3].end == date(2027, 2, 28)
    assert budget_weeks(date(2028, 2, 1))[3].end == date(2028, 2, 29)
    week3, week4 = weeks[2], weeks[3]
    assert budget_status(100, None, week3, TODAY) is BudgetStatus.NO_BUDGET
    assert budget_status(101, 100, week3, TODAY) is BudgetStatus.OVER
    assert budget_status(90, 100, week3, TODAY) is BudgetStatus.REACHED
    assert budget_status(89, 100, week3, TODAY) is BudgetStatus.UNDER
    # The running week: over is over at once, under is only "running".
    assert budget_status(10, 100, week4, TODAY) is BudgetStatus.RUNNING
    assert budget_status(120, 100, week4, TODAY) is BudgetStatus.OVER
    assert budget_status(0, 100, week4, date(2026, 10, 21)) is BudgetStatus.UPCOMING


async def test_02_the_head_sets_budgets_and_sees_who_is_over(world: World) -> None:
    head, ngoc, anh = await staffed(world)
    world.act_as(ngoc)
    google = add_google(world)["account_id"]
    world.client.put("/api/ads/manual/days", json={"entries": [cell(google, D1, "spend", 500_000)]})
    world.act_as(head)
    saved = world.client.put(
        "/api/ads/budget",
        json={
            "month": "2026-10",
            "entries": {
                f"{ngoc.id}:3": "3.000.000",
                f"{ngoc.id}:4": 2_000_000,
                f"{anh.id}:3": 10_000_000,
            },
        },
    )
    assert saved.status_code == 200, saved.json()
    data = saved.json()
    assert [week["label"] for week in data["weeks"]][2] == "Tuần 3 · 15/10 - 21/10"
    assert data["can_edit"] is True and data["month"] == "2026-10"
    lines = {line["name"]: line for line in data["lines"]}
    mine = lines["Mạnh Ngọc"]["cells"]
    # Facebook 3.000.000 + Google 500.000: every platform counts.
    assert mine[2]["spend"] == 3_500_000 and mine[2]["budget"] == 3_000_000
    assert mine[2]["status"] == "OVER" and mine[2]["status_label"] == "Vượt ngưỡng"
    assert mine[3]["status"] == "RUNNING" and mine[0]["status"] == "NO_BUDGET"
    assert lines["Mạnh Ngọc"]["budget"] == 5_000_000
    theirs = lines["Hoàng Anh"]["cells"][2]
    assert theirs["status"] == "UNDER" and theirs["rate"] < 0.9
    assert lines["Trưởng phòng Ads"]["budget"] is None
    assert data["total"]["cells"][2]["budget"] == 13_000_000
    assert data["total"]["cells"][2]["spend"] == mine[2]["spend"] + theirs["spend"]

    # A blank clears a cell; the same value again changes nothing.
    world.client.put(
        "/api/ads/budget",
        json={"month": "2026-10", "entries": {f"{ngoc.id}:4": "", f"{ngoc.id}:3": 3_000_000}},
    )
    rows = (await world.session.execute(select(AdsBudget))).scalars().all()
    assert {(row.user_id, row.week) for row in rows} == {(ngoc.id, 3), (anh.id, 3)}
    audit = (
        (await world.session.execute(select(AuditLog).where(AuditLog.action == "ads.budget.saved")))
        .scalars()
        .all()
    )
    assert len(audit) == 2

    bad = world.client.put(
        "/api/ads/budget", json={"month": "2026-10", "entries": {f"{ngoc.id}:5": 1}}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "ads_budget_invalid"
    outsider = await person(world, "Người ngoài", None)
    stranger = world.client.put(
        "/api/ads/budget", json={"month": "2026-10", "entries": {f"{outsider.id}:1": 1}}
    )
    assert stranger.status_code == 422
    negative = world.client.put(
        "/api/ads/budget", json={"month": "2026-10", "entries": {f"{ngoc.id}:1": -1}}
    )
    assert negative.status_code == 422

    one = world.client.get(
        "/api/ads/budget", params={"month": "2026-10", "user_id": str(ngoc.id)}
    ).json()
    assert [line["name"] for line in one["lines"]] == ["Mạnh Ngọc"] and one["total"] is None


async def test_03_a_member_sees_only_their_own(world: World) -> None:
    head, ngoc, anh = await staffed(world)
    world.act_as(head)
    world.client.put(
        "/api/ads/budget",
        json={"month": "2026-10", "entries": {f"{ngoc.id}:3": 1, f"{anh.id}:3": 1}},
    )
    world.act_as(ngoc)
    mine = world.client.get("/api/ads/budget", params={"month": "2026-10"}).json()
    assert [line["name"] for line in mine["lines"]] == ["Mạnh Ngọc"]
    assert mine["can_edit"] is False and mine["total"] is None
    other = world.client.get(
        "/api/ads/budget", params={"month": "2026-10", "user_id": str(anh.id)}
    ).json()
    assert [line["name"] for line in other["lines"]] == ["Mạnh Ngọc"]
    refused = world.client.put(
        "/api/ads/budget", json={"month": "2026-10", "entries": {f"{ngoc.id}:1": 9}}
    )
    assert refused.status_code == 403
    world.act_as(await person(world, "Không ở ADS", None))
    assert world.client.get("/api/ads/budget").status_code == 404
