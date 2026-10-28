"""Phòng Ads (0081): Google Ads and TikTok Ads typed by hand, the platform filter."""

from __future__ import annotations

from sqlalchemy import select

from meobot.db.models.ads import AdsAccount, AdsManualChange
from meobot.db.models.audit_log import AuditLog
from meobot.domain.ads.models import AdsPlatform, clean_external_id, manual_account_key
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_ads_department import D1, D2, person, staffed

PARAMS = {"start": D1.isoformat(), "end": D2.isoformat()}


def add_google(world: World, **over: object) -> dict[str, object]:
    body = {"platform": "GOOGLE", "external_id": "123-456-7890", "name": "GG Apex SG 01", **over}
    response = world.client.post("/api/ads/manual/accounts", json=body)
    assert response.status_code == 201, response.json()
    return response.json()


def cell(account_id: str, day: object, field: str, amount: object) -> dict[str, object]:
    return {"account_id": account_id, "day": str(day), "field": field, "amount": amount}


def test_01_the_typed_id() -> None:
    assert clean_external_id(" 123 456 7890 ") == "1234567890"
    assert clean_external_id("abc/def") is None and clean_external_id("") is None
    assert manual_account_key(AdsPlatform.GOOGLE, "123-456-7890") == "google:1234567890"
    assert manual_account_key(AdsPlatform.TIKTOK, "AB-12") == "tiktok:AB-12"


async def test_02_a_marketer_adds_their_own_google_account(world: World) -> None:
    head, ngoc, anh = await staffed(world)
    world.act_as(ngoc)
    account = add_google(world, site="Sài Gòn", card_last4="1234")
    assert account["account_id"] == "google:1234567890"
    assert account["external_id"] == "123-456-7890" and account["platform_label"] == "Google Ads"
    assert account["user_id"] == str(ngoc.id) and account["connection_name"] == "Nhập tay"
    assert account["brand"] == "APEX" and account["card_last4"] == "1234"
    # The same id typed without dashes is the same account.
    again = world.client.post(
        "/api/ads/manual/accounts",
        json={"platform": "GOOGLE", "external_id": "1234567890", "name": "x"},
    )
    assert again.status_code == 422 and again.json()["code"] == "ads_account_exists"
    facebook = world.client.post(
        "/api/ads/manual/accounts",
        json={"platform": "FACEBOOK", "external_id": "999", "name": "x"},
    )
    assert facebook.status_code == 422
    # A member cannot hand one to somebody else: it stays theirs.
    other = add_google(world, platform="TIKTOK", external_id="7000", user_id=str(anh.id))
    assert other["user_id"] == str(ngoc.id)
    world.act_as(head)
    given = add_google(world, platform="TIKTOK", external_id="7001", user_id=str(anh.id))
    assert given["user_id"] == str(anh.id)
    outsider = await person(world, "Người ngoài", None)
    world.act_as(outsider)
    assert (
        world.client.post(
            "/api/ads/manual/accounts",
            json={"platform": "GOOGLE", "external_id": "55", "name": "x"},
        ).status_code
        == 404
    )
    # "Tài khoản QC" lists the typed-in accounts too.
    world.act_as(head)
    listed = world.client.get("/api/ads/accounts", params={"platform": "TIKTOK"}).json()
    assert {row["external_id"] for row in listed["accounts"]} == {"7000", "7001"}


async def test_03_typing_days_keeps_every_change(world: World) -> None:
    head, ngoc, anh = await staffed(world)
    world.act_as(ngoc)
    key = add_google(world)["account_id"]
    saved = world.client.put(
        "/api/ads/manual/days",
        json={
            "entries": [
                cell(key, D1, "spend", "1.200.000"),
                cell(key, D1, "invoice", 1_000_000),
                cell(key, D2, "spend", 800_000),
            ]
        },
    )
    assert saved.json() == {"changed": 3}
    # Same value = no change; a blank clears the cell.
    again = world.client.put(
        "/api/ads/manual/days",
        json={"entries": [cell(key, D1, "spend", 1_200_000), cell(key, D2, "spend", "")]},
    )
    assert again.json() == {"changed": 1}
    changes = (await world.session.execute(select(AdsManualChange))).scalars().all()
    last = max(changes, key=lambda change: (change.changed_at, change.day))
    assert (last.field, last.day, last.old_amount, last.new_amount) == ("spend", D2, 800_000, None)
    assert len(changes) == 4
    history = world.client.get("/api/ads/manual/changes", params=PARAMS).json()["changes"]
    assert len(history) == 4 and history[0]["changed_by"] == "Mạnh Ngọc"
    assert {line["account_name"] for line in history} == {"GG Apex SG 01"}
    audit = (
        (await world.session.execute(select(AuditLog).where(AuditLog.action == "ads.manual.saved")))
        .scalars()
        .all()
    )
    assert len(audit) == 2

    future = world.client.put(
        "/api/ads/manual/days", json={"entries": [cell(key, "2099-01-01", "spend", 1)]}
    )
    assert future.status_code == 422
    meta = world.client.put("/api/ads/manual/days", json={"entries": [cell("111", D1, "spend", 1)]})
    assert meta.status_code == 422
    assert meta.json()["code"] == "ads_manual_invalid"
    negative = world.client.put(
        "/api/ads/manual/days", json={"entries": [cell(key, D1, "invoice", -5)]}
    )
    assert negative.status_code == 422
    # Somebody else's account: not seen by a member, typed by the head.
    world.act_as(anh)
    assert (
        world.client.put(
            "/api/ads/manual/days", json={"entries": [cell(key, D1, "spend", 1)]}
        ).status_code
        == 404
    )
    assert world.client.get("/api/ads/manual/changes", params=PARAMS).json()["changes"] == []
    world.act_as(head)
    assert world.client.put(
        "/api/ads/manual/days", json={"entries": [cell(key, D1, "spend", 1_300_000)]}
    ).json() == {"changed": 1}

    # An account with numbers (or their history) cannot be removed.
    world.act_as(ngoc)
    refused = world.client.delete(f"/api/ads/manual/accounts/{key}")
    assert refused.status_code == 422
    assert refused.json()["code"] == "ads_account_has_numbers"
    spare = add_google(world, external_id="999-000-1111", name="Nhầm")["account_id"]
    assert world.client.delete(f"/api/ads/manual/accounts/{spare}").status_code == 204
    assert await world.session.get(AdsAccount, spare) is None
    renamed = world.client.patch(
        f"/api/ads/manual/accounts/{key}", json={"name": "GPI Google 1", "card_last4": ""}
    ).json()
    assert renamed["name"] == "GPI Google 1" and renamed["brand"] == "GPI"
    assert renamed["card_last4"] is None
    assert world.client.patch("/api/ads/manual/accounts/111", json={"name": "x"}).status_code == 422


async def test_04_the_dashboard_by_platform(world: World) -> None:
    head, ngoc, _ = await staffed(world)
    world.act_as(ngoc)
    key = add_google(world)["account_id"]
    idle = add_google(world, external_id="222-333-4444", name="GG trống")["account_id"]
    world.client.put(
        "/api/ads/manual/days",
        json={"entries": [cell(key, D1, "spend", 2_000_000), cell(key, D1, "invoice", 2_100_000)]},
    )
    google = world.client.get("/api/ads/dashboard", params={**PARAMS, "platform": "GOOGLE"}).json()
    kpis = {kpi["key"]: kpi["value"] for kpi in google["kpis"]}
    assert kpis["spend"] == 2_000_000 and kpis["invoice"] == 2_100_000
    # Google revenue is entered on its ◆ line, not per marketer.
    assert google["revenue_note"] == "platform" and kpis["revenue"] is None
    assert google["connections"] == [] and google["can_add_account"] is True
    lines = {line["account_id"]: line for line in google["accounts"]}
    # Every typed-in account is listed, even one with nothing typed yet.
    assert set(lines) == {key, idle} and lines[idle]["spend"] == 0
    assert lines[key]["can_edit"] is True and lines[key]["spend_by_day"] == [2_000_000, 0]
    assert lines[key]["spend_entered"] == [True, False]
    assert google["last_synced_at"] is not None
    assert [option["value"] for option in google["platforms"]] == ["FACEBOOK", "GOOGLE", "TIKTOK"]
    # Facebook (the default) does not count it.
    facebook = world.client.get("/api/ads/dashboard", params=PARAMS).json()
    assert {kpi["key"]: kpi["value"] for kpi in facebook["kpis"]}["spend"] == 3_000_000
    assert key not in {line["account_id"] for line in facebook["accounts"]}

    world.act_as(head)
    world.client.put(
        "/api/ads/revenue",
        json={"day": D1.isoformat(), "entries": {"s:GOOGLE": 5_400_000, f"u:{ngoc.id}": 1_000}},
    )
    mine = world.client.get(
        "/api/ads/dashboard", params={**PARAMS, "platform": "GOOGLE", "user_id": str(ngoc.id)}
    ).json()
    assert mine["accounts"][0]["can_edit"] is True  # the head types anybody's
    team = world.client.get("/api/ads/team", params={**PARAMS, "platform": "GOOGLE"}).json()
    assert team["current"]["spend"] == 2_000_000 and team["current"]["invoice"] == 2_100_000
    assert team["current"]["revenue"] == 5_400_000 and team["revenue_note"] == "platform"
    assert round(team["current"]["ads_ratio"], 4) == round(2_100_000 / 5_000_000, 4)
    assert team["team_kpi"] is None
    assert [line["user_id"] for line in team["marketers"]] == [str(ngoc.id)]
    channels = {channel["platform"]: channel for channel in team["channels"]}
    assert channels["GOOGLE"]["figures"]["spend"] == 2_000_000
    assert channels["GOOGLE"]["accounts"] == 2 and channels["TIKTOK"]["figures"]["spend"] == 0
    assert channels["GOOGLE"]["people"][0]["name"] == "Mạnh Ngọc"
    assert channels["FACEBOOK"]["figures"]["spend"] == 3_500_000
    # The Facebook report keeps its own numbers and still sees every platform.
    fb = world.client.get("/api/ads/team", params=PARAMS).json()
    assert fb["current"]["spend"] == 3_500_000 and fb["revenue_note"] is None
    assert {c["platform"]: c["figures"]["spend"] for c in fb["channels"]}["GOOGLE"] == 2_000_000
    history = world.client.get(
        "/api/ads/history", params={"month": "2026-10", "platform": "GOOGLE"}
    ).json()
    day = next(line for line in history["days"] if line["day"] == D1.isoformat())
    assert day["figures"]["spend"] == 2_000_000 and day["figures"]["revenue"] == 5_400_000


async def test_05_a_member_without_a_seat_is_refused(world: World) -> None:
    await staffed(world)
    lead = await person(world, "Trưởng ban", UnitMemberRole.HEAD)
    world.act_as(lead)
    # A head may add for somebody seated in ADS only.
    outsider = await person(world, "Không ở ADS", None)
    refused = world.client.post(
        "/api/ads/manual/accounts",
        json={
            "platform": "GOOGLE",
            "external_id": "42",
            "name": "x",
            "user_id": str(outsider.id),
        },
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "ads_assignee_invalid"
