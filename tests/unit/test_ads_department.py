"""Phòng Ads, step 1 (0078): Kết nối BM, the Meta syncs, Tài khoản QC, the dashboard."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from meobot.api.deps import get_app_settings
from meobot.api.routers.ads import get_meta_ads_client, get_task_sender
from meobot.application.ads.sync_service import AdsSyncService, charge_window, spend_window
from meobot.application.org.departments import place
from meobot.core.secrets import build_secret_box, generate_key
from meobot.db.models.ads import AdsAccount, AdsChargeDaily, AdsSpendDaily
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.user import User
from meobot.domain.ads.models import (
    AdsBrand,
    AdsGrouping,
    Period,
    brand_of,
    bucket,
    invoice_factor,
    matches,
)
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitCode, UnitMemberRole
from meobot.integrations.meta_ads.client import (
    AdAccountInfo,
    BillingCharge,
    DailyInsight,
    FakeMetaAdsClient,
    GraphMetaAdsClient,
    MetaAdsError,
    explain,
)
from tests.unit.pr_world import World
from tests.unit.streams import ensure_units

TOKEN = "EAAtest-system-user-token-9876"
BM = "1234567890"
D1 = date(2026, 10, 20)
D2 = date(2026, 10, 21)


# --- the rules -------------------------------------------------------------------


def test_01_brand_fee_and_matching() -> None:
    assert brand_of("[COREMIND] (XH-SG-VAT)TK 2 - MẠNH NGỌC") is AdsBrand.COREMIND
    assert brand_of("GPI TECH SG 01") is AdsBrand.GPI
    assert brand_of("Apexmed - Hoàng Anh") is AdsBrand.APEX
    assert brand_of("Tài khoản lẻ") is AdsBrand.OTHER
    # "Số tiền lập hoá đơn" = what Meta charged: no VAT, no card fee (27/10).
    rented = "[COREMIND] (XH-SG-VAT)TK 2"
    assert invoice_factor(rented, AdsBrand.COREMIND, date(2026, 10, 1)) == 1
    assert invoice_factor("TK 1", AdsBrand.OTHER, date(2026, 6, 30)) == 1
    assert matches(Decimal(1_000_000), Decimal(1_009_000))
    assert not matches(Decimal(1_000_000), Decimal(1_100_000))
    assert matches(Decimal(100_000), Decimal(108_000))  # within the 10.000 ₫ floor
    period = Period(start=date(2026, 10, 13), end=date(2026, 10, 26))
    assert period.days == 14
    assert period.previous() == Period(start=date(2026, 9, 29), end=date(2026, 10, 12))
    assert bucket(date(2026, 10, 22), AdsGrouping.WEEK) == date(2026, 10, 19)
    assert bucket(date(2026, 10, 22), AdsGrouping.MONTH) == date(2026, 10, 1)


def test_02_the_schedule_windows() -> None:
    assert spend_window(date(2026, 10, 27)) == (date(2026, 10, 26), date(2026, 10, 26))
    assert charge_window(date(2026, 10, 27)) == (date(2026, 10, 23), date(2026, 10, 27))
    # Early in the month the whole previous month is read again.
    assert charge_window(date(2026, 11, 2)) == (date(2026, 10, 1), date(2026, 11, 2))


# --- the world -------------------------------------------------------------------


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def __call__(self, name: str, args: Any, kwargs: Any, headers: Any, options: Any) -> str:
        self.sent.append((name, dict(kwargs), dict(headers)))
        return f"task-{len(self.sent)}"


def setup(world: World) -> tuple[FakeMetaAdsClient, Sender]:
    settings = world.settings.model_copy(
        update={
            "pr_secret_encryption_key": SecretStr(generate_key()),
            "pr_secret_encryption_key_file": None,
        }
    )
    world.ads_settings = settings  # type: ignore[attr-defined]
    client = FakeMetaAdsClient(
        names={BM: "Apexmed SG"},
        accounts={
            BM: [
                AdAccountInfo("111", "[COREMIND] TK 1 - MẠNH NGỌC", status=1, currency="VND"),
                AdAccountInfo("222", "GPI SG TK 2 - HOÀNG ANH", status=1, currency="VND"),
                AdAccountInfo("333", "Tài khoản lẻ", status=2, currency="VND"),
            ]
        },
        insights=[
            DailyInsight("111", D1, Decimal(1_000_000), 40, 10),
            DailyInsight("111", D2, Decimal(2_000_000), 50, 5),
            DailyInsight("222", D2, Decimal(500_000), 10, 0),
        ],
        charges=[
            BillingCharge(
                "111", datetime(2026, 10, 20, 3, 0, tzinfo=UTC), Decimal(1_000_000), "t1"
            ),
            BillingCharge(
                "111", datetime(2026, 10, 21, 18, 0, tzinfo=UTC), Decimal(1_500_000), "t2"
            ),
        ],
    )
    sender = Sender()
    app = world.client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_meta_ads_client] = lambda: client
    app.dependency_overrides[get_task_sender] = lambda: sender
    return client, sender


async def person(world: World, name: str, role: UnitMemberRole | None) -> User:
    user = User(full_name=name, role=Role.EMPLOYEE)
    world.session.add(user)
    await world.session.flush()
    if role is not None:
        await ensure_units(world.session)
        await place(world.session, UnitCode.ADS, user.id, role)
    return user


def add_bm(world: World, **over: Any) -> dict[str, Any]:
    world.act_as(world.owner)
    body = {"name": "BM Apex", "business_id": BM, "site": "Sài Gòn", "token": TOKEN, **over}
    response = world.client.post("/api/ads/connections", json=body)
    assert response.status_code == 201, response.json()
    return response.json()


# --- Kết nối BM --------------------------------------------------------------------


async def test_03_the_owner_adds_a_bm_and_the_token_never_comes_back(world: World) -> None:
    setup(world)
    made = add_bm(world)
    assert made["token_hint"] == "••••9876" and TOKEN not in str(made)
    listing = world.client.get("/api/ads/connections")
    assert listing.status_code == 200 and TOKEN not in listing.text
    assert listing.json()["sites"] == ["Sài Gòn"] and listing.json()["secret_ready"] is True
    row = (await world.session.execute(select(AdsAccount))).scalars().all()
    assert row == []
    from meobot.db.models.ads import AdsConnection

    stored = await world.session.get(AdsConnection, uuid.UUID(made["id"]))
    assert stored is not None and TOKEN not in stored.token_encrypted
    box = build_secret_box(world.ads_settings)  # type: ignore[attr-defined]
    assert box.decrypt(stored.token_encrypted, aad=f"ads_connection:{stored.id}") == TOKEN
    logs = (await world.session.execute(select(AuditLog))).scalars().all()
    assert all(TOKEN not in str(log.before_data) + str(log.after_data) for log in logs)

    # The same BM twice, a non-numeric id: refused.
    again = world.client.post(
        "/api/ads/connections",
        json={"name": "x", "business_id": BM, "site": "", "token": TOKEN},
    )
    assert again.status_code == 422 and again.json()["details"]["reason"] == "ads_business_id_taken"
    bad = world.client.post(
        "/api/ads/connections",
        json={"name": "x", "business_id": "abc", "site": "", "token": TOKEN},
    )
    assert bad.json()["details"]["reason"] == "ads_business_id_invalid"

    # Somebody else: 403.
    world.act_as(world.member)
    assert world.client.get("/api/ads/connections").status_code == 403


async def test_04_test_connection_says_what_meta_answered(world: World) -> None:
    client, _ = setup(world)
    made = add_bm(world)
    tested = world.client.post(f"/api/ads/connections/{made['id']}/test").json()
    assert tested["ok"] is True
    assert tested["message"] == "Kết nối được · BM Apexmed SG · 3 tài khoản quảng cáo."
    assert tested["connection"]["account_count"] == 3
    client.broken_tokens[TOKEN] = MetaAdsError("Token không hợp lệ hoặc đã hết hạn.", code=190)
    failed = world.client.post(f"/api/ads/connections/{made['id']}/test").json()
    assert failed["ok"] is False and "hết hạn" in failed["message"]
    assert failed["connection"]["last_test_ok"] is False


# --- the syncs -----------------------------------------------------------------


async def test_05_spend_and_charges_are_pulled_and_replaced(world: World) -> None:
    client, _ = setup(world)
    add_bm(world)
    service = AdsSyncService(
        world.session,
        world.ads_settings,
        client=client,  # type: ignore[attr-defined]
    )
    report = await service.sync_spend(D1, D2)
    assert report.status == "SUCCESS" and report.accounts == 3 and report.rows == 3
    rows = (await world.session.execute(select(AdsSpendDaily))).scalars().all()
    assert sorted((row.account_id, row.day, int(row.spend)) for row in rows) == [
        ("111", D1, 1_000_000),
        ("111", D2, 2_000_000),
        ("222", D2, 500_000),
    ]
    accounts = {
        a.account_id: a for a in (await world.session.execute(select(AdsAccount))).scalars()
    }
    assert accounts["111"].brand is AdsBrand.COREMIND and accounts["222"].brand is AdsBrand.GPI
    assert accounts["111"].user_id is None

    # Meta corrects the day: the re-read replaces it.
    client.insights[1] = DailyInsight("111", D2, Decimal(2_100_000), 52, 6)
    await service.sync_spend(D2, D2)
    corrected = await world.session.get(AdsSpendDaily, (D2, "111"))
    assert corrected is not None and int(corrected.spend) == 2_100_000 and corrected.phones == 6

    # One account failing keeps its rows and makes the run PARTIAL.
    client.broken_accounts["222"] = "Meta không trả lời tài khoản này."
    partial = await service.sync_spend(D2, D2)
    assert partial.status == "PARTIAL"
    assert partial.as_task_result()["status"] == "PARTIAL"
    assert await world.session.get(AdsSpendDaily, (D2, "222")) is not None

    # Charges, cut by the Vietnam day (18:00 UTC on the 21st = the 22nd in Vietnam).
    charges = await service.sync_charges(D1, date(2026, 10, 22))
    assert charges.status == "PARTIAL"
    days = {
        (row.account_id, row.day): int(row.amount)
        for row in (await world.session.execute(select(AdsChargeDaily))).scalars()
    }
    assert days == {("111", D1): 1_000_000, ("111", date(2026, 10, 22)): 1_500_000}

    # A token that cannot be decrypted fails that BM only, with a reason.
    from meobot.db.models.ads import AdsConnection

    [bm] = (await world.session.execute(select(AdsConnection))).scalars().all()
    good_envelope = bm.token_encrypted
    bm.token_encrypted = "v1:damaged"
    damaged = await service.sync_spend(D1, D2)
    assert damaged.status == "FAILED" and "nhập lại token" in damaged.message
    bm.token_encrypted = good_envelope

    # A dead token: the whole BM fails, the run is FAILED.
    client.broken_tokens[TOKEN] = MetaAdsError("Token không hợp lệ hoặc đã hết hạn.", code=190)
    failed = await service.sync_spend(D1, D2)
    assert failed.status == "FAILED" and "hết hạn" in failed.message


async def test_06_sync_now_queues_a_manual_run(world: World) -> None:
    _, sender = setup(world)
    add_bm(world)
    response = world.client.post(
        "/api/ads/sync", json={"kind": "spend", "since": "2026-10-01", "until": "2026-10-26"}
    )
    assert response.status_code == 202, response.json()
    [(name, kwargs, headers)] = sender.sent
    assert name == "ads.sync_spend"
    assert kwargs == {"since": "2026-10-01", "until": "2026-10-26"}
    assert headers["cron_trigger"] == "MANUAL"
    too_long = world.client.post(
        "/api/ads/sync", json={"kind": "charges", "since": "2026-01-01", "until": "2026-10-26"}
    )
    assert too_long.status_code == 422
    half = world.client.post("/api/ads/sync", json={"kind": "spend", "since": "2026-10-01"})
    assert half.status_code == 422
    world.act_as(world.member)
    assert world.client.post("/api/ads/sync", json={"kind": "spend"}).status_code in (403, 404)


# --- Tài khoản QC + the dashboard ----------------------------------------------------


async def synced(world: World) -> FakeMetaAdsClient:
    client, _ = setup(world)
    add_bm(world)
    service = AdsSyncService(
        world.session,
        world.ads_settings,
        client=client,  # type: ignore[attr-defined]
    )
    await service.sync_spend(D1, D2)
    await service.sync_charges(D1, D2)
    return client


async def test_07_only_the_ads_department_sees_it(world: World) -> None:
    await synced(world)
    head = await person(world, "Trưởng phòng Ads", UnitMemberRole.HEAD)
    marketer = await person(world, "Mạnh Ngọc", UnitMemberRole.ORDERER)
    outsider = await person(world, "Người ngoài", None)

    world.act_as(outsider)
    response = world.client.get("/api/ads/dashboard")
    assert response.status_code == 404 and response.json()["code"] == "ads_not_visible"
    assert world.client.get("/api/units/me").json()["ads"]["view"] is False

    world.act_as(head)
    me = world.client.get("/api/units/me").json()["ads"]
    assert me == {
        "view": True,
        "view_all": True,
        "manage_accounts": True,
        "manage_connections": False,
        "edit_revenue": True,
    }
    listing = world.client.get("/api/ads/accounts", params={"month": "2026-10"}).json()
    assert {a["account_id"] for a in listing["accounts"]} == {"111", "222", "333"}
    assert {p["name"] for p in listing["people"]} >= {"Trưởng phòng Ads", "Mạnh Ngọc"}
    by_id = {a["account_id"]: a for a in listing["accounts"]}
    assert by_id["111"]["spend"] == 3_000_000 and by_id["111"]["user_name"] is None

    # The head gives TK 111 to the marketer; an outsider cannot receive one.
    given = world.client.patch("/api/ads/accounts/111", json={"user_id": str(marketer.id)})
    assert given.status_code == 200 and given.json()["user_name"] == "Mạnh Ngọc"
    refused = world.client.patch("/api/ads/accounts/222", json={"user_id": str(outsider.id)})
    assert refused.status_code == 422

    # The marketer sees their own account and numbers only, and cannot assign.
    world.act_as(marketer)
    mine = world.client.get("/api/ads/accounts", params={"month": "2026-10"}).json()
    assert [a["account_id"] for a in mine["accounts"]] == ["111"] and mine["can_manage"] is False
    assert world.client.patch("/api/ads/accounts/111", json={"user_id": None}).status_code == 403
    board = world.client.get(
        "/api/ads/dashboard",
        params={"start": D1.isoformat(), "end": D2.isoformat(), "user_id": str(head.id)},
    ).json()
    # Asking for somebody else as a member still gives their own numbers.
    assert board["user_id"] == str(marketer.id)
    kpis = {kpi["key"]: kpi for kpi in board["kpis"]}
    assert kpis["spend"]["value"] == 3_000_000
    assert kpis["messages"]["value"] == 90 and kpis["phones"]["value"] == 15
    assert kpis["cost_per_phone"]["value"] == 200_000
    assert kpis["cost_per_phone"]["lower_is_better"] is True
    assert [line["account_id"] for line in board["accounts"]] == ["111"]
    line = board["accounts"][0]
    assert line["charged"] == 1_000_000 and line["difference"] == -2_000_000
    assert line["matched"] is False
    assert line["invoice"] == 1_000_000
    assert board["best_day"]["start"] == D1.isoformat()
    assert board["revenue"] is None
    assert [point["start"] for point in board["series"]] == [D1.isoformat(), D2.isoformat()]


async def test_08_the_head_sees_the_whole_department_or_one_person(world: World) -> None:
    await synced(world)
    head = await person(world, "Trưởng phòng Ads", UnitMemberRole.HEAD)
    marketer = await person(world, "Hoàng Anh", UnitMemberRole.ORDERER)
    world.act_as(head)
    world.client.patch("/api/ads/accounts/222", json={"user_id": str(marketer.id)})
    params = {"start": D1.isoformat(), "end": D2.isoformat()}
    everyone = world.client.get("/api/ads/dashboard", params=params).json()
    assert {kpi["key"]: kpi["value"] for kpi in everyone["kpis"]}["spend"] == 3_500_000
    assert {person["name"] for person in everyone["people"]} >= {"Hoàng Anh"}
    one = world.client.get(
        "/api/ads/dashboard", params={**params, "user_id": str(marketer.id)}
    ).json()
    assert {kpi["key"]: kpi["value"] for kpi in one["kpis"]}["spend"] == 500_000
    gpi = world.client.get("/api/ads/dashboard", params={**params, "brand": "GPI"}).json()
    assert [line["account_id"] for line in gpi["accounts"]] == ["222"]
    weekly = world.client.get("/api/ads/dashboard", params={**params, "group": "week"}).json()
    assert [point["start"] for point in weekly["series"]] == ["2026-10-19"]
    assert (
        world.client.get(
            "/api/ads/dashboard", params={"start": "2026-10-26", "end": "2026-10-01"}
        ).status_code
        == 422
    )
    # The OWNER sees it all too.
    world.act_as(world.owner)
    assert world.client.get("/api/units/me").json()["ads"]["manage_connections"] is True
    previous_period = (D1 - timedelta(days=2)).isoformat()
    assert everyone["previous_start"] == previous_period


# --- step 2: revenue, KPI, Quản lý A (0079) -----------------------------------------


async def staffed(world: World) -> tuple[User, User, User]:
    """Synced data; TK 111 run by Ngọc, TK 222 by Hoàng Anh, TK 333 nobody's."""
    await synced(world)
    head = await person(world, "Trưởng phòng Ads", UnitMemberRole.HEAD)
    ngoc = await person(world, "Mạnh Ngọc", UnitMemberRole.ORDERER)
    anh = await person(world, "Hoàng Anh", UnitMemberRole.ORDERER)
    world.act_as(head)
    world.client.patch("/api/ads/accounts/111", json={"user_id": str(ngoc.id)})
    world.client.patch("/api/ads/accounts/222", json={"user_id": str(anh.id)})
    return head, ngoc, anh


async def test_09_the_head_enters_revenue_by_day(world: World) -> None:
    _, ngoc, anh = await staffed(world)
    day = world.client.get("/api/ads/revenue", params={"day": D1.isoformat()}).json()
    keys = {line["key"]: line for line in day["lines"]}
    assert f"u:{ngoc.id}" in keys and "s:HOTLINE" in keys and "s:SEEDING" in keys
    assert keys["s:HOTLINE"]["label"] == "◆ Hotline"
    assert all(line["amount"] is None for line in day["lines"]) and day["total"] is None

    saved = world.client.put(
        "/api/ads/revenue",
        json={
            "day": D1.isoformat(),
            "entries": {
                f"u:{ngoc.id}": 4_000_000,
                f"u:{ngoc.id}:hn": "1.000.000",
                "s:HOTLINE": "1.500.000",
                f"u:{anh.id}": None,
            },
        },
    ).json()
    lines = {line["key"]: line for line in saved["lines"]}
    assert lines[f"u:{ngoc.id}"]["amount"] == 4_000_000
    assert lines[f"u:{ngoc.id}"]["amount_hn"] == 1_000_000
    assert lines["s:HOTLINE"]["amount"] == 1_500_000
    assert lines[f"u:{anh.id}"]["amount"] is None
    # A blank clears a part; a line with no part left is "not entered" again.
    world.client.put(
        "/api/ads/revenue",
        json={"day": D1.isoformat(), "entries": {"s:HOTLINE": "", f"u:{ngoc.id}:hn": None}},
    )
    from meobot.db.models.ads import AdsRevenueChange, AdsRevenueDaily

    rows = (await world.session.execute(select(AdsRevenueDaily))).scalars().all()
    assert [(row.subject_key, int(row.amount or 0), row.amount_hn) for row in rows] == [
        (f"u:{ngoc.id}", 4_000_000, None)
    ]
    changes = (await world.session.execute(select(AdsRevenueChange))).scalars().all()
    assert sorted(change.part for change in changes) == ["hn", "hn", "sg", "sg", "sg"]

    for bad_entries in ({"u:nobody": 1}, {"s:HOTLINE:hn": 1}, {f"u:{ngoc.id}": -5}):
        bad = world.client.put(
            "/api/ads/revenue", json={"day": D1.isoformat(), "entries": bad_entries}
        )
        assert bad.status_code == 422, bad_entries
    world.act_as(ngoc)
    assert world.client.get("/api/ads/revenue").status_code == 403
    assert world.client.get("/api/units/me").json()["ads"]["edit_revenue"] is False


async def test_10_the_dashboard_shows_revenue_and_ads_ratio(world: World) -> None:
    head, ngoc, _ = await staffed(world)
    entries = {f"u:{ngoc.id}": 5_400_000, f"u:{ngoc.id}:hn": 1_080_000, "s:GOOGLE": 1_000_000}
    world.client.put("/api/ads/revenue", json={"day": D2.isoformat(), "entries": entries})
    params = {"start": D1.isoformat(), "end": D2.isoformat()}
    world.act_as(ngoc)
    mine = world.client.get("/api/ads/dashboard", params=params).json()
    kpis = {kpi["key"]: kpi["value"] for kpi in mine["kpis"]}
    # Xét KPI = SG + HN; hoa hồng = that without the 8% VAT (October 2026).
    assert kpis["revenue"] == 6_480_000 and kpis["revenue_sg"] == 5_400_000
    assert kpis["revenue_hn"] == 1_080_000
    assert round(kpis["revenue_net"]) == 6_000_000 and round(mine["revenue_net"]) == 6_000_000
    assert round(kpis["ads_ratio"], 4) == 0.5  # spend 3.000.000 / 6.000.000
    assert [point["revenue"] for point in mine["series"]] == [None, 6_480_000]
    highlights = mine["highlights"]
    assert highlights["best_revenue_day"]["start"] == D2.isoformat()
    assert [point["start"] for point in highlights["zero_revenue_days"]] == [D1.isoformat()]
    assert highlights["last5_spend"] == 3_000_000 and highlights["last5_days"] == 2
    assert mine["days"] == [D1.isoformat(), D2.isoformat()]
    line = mine["accounts"][0]
    assert line["spend_by_day"] == [1_000_000, 2_000_000]
    assert line["invoice_ratio"] == line["invoice"] / line["spend"]
    world.act_as(head)
    team = world.client.get("/api/ads/dashboard", params=params).json()
    # The ads marketers only: the ◆ sources (Google here) are nobody's ad spend.
    assert team["revenue"] == 6_480_000
    # An ad-account filter narrows the costs, never the revenue (2026-10-30).
    filtered = world.client.get("/api/ads/dashboard", params={**params, "brand": "GPI"}).json()
    assert filtered["revenue"] == 6_480_000 and filtered["revenue_note"] is None
    # "Miền": HCM = the SG part, HN = the HN part.
    hcm = world.client.get("/api/ads/dashboard", params={**params, "region": "hcm"}).json()
    kpis = {kpi["key"]: kpi["value"] for kpi in hcm["kpis"]}
    assert kpis["revenue"] == 5_400_000 and kpis["revenue_hn"] == 0
    hn = world.client.get("/api/ads/dashboard", params={**params, "region": "hn"}).json()
    assert hn["revenue"] == 1_080_000
    assert hn["flows"] == {"hcm_hcm": 0, "hn_hn": 0, "hn_hcm": 0, "hcm_hn": 1_080_000}
    assert (
        world.client.get("/api/ads/dashboard", params={**params, "region": "x"}).status_code == 422
    )


async def test_11_kpis_and_the_team_dashboard(world: World) -> None:
    _, ngoc, _ = await staffed(world)
    entries = {
        f"u:{ngoc.id}": 10_800_000,
        f"u:{ngoc.id}:hn": 1_080_000,
        "s:HOTLINE": 2_160_000,
        "s:HN": 1_080_000,
    }
    world.client.put("/api/ads/revenue", json={"day": D2.isoformat(), "entries": entries})
    saved = world.client.put(
        "/api/ads/kpi",
        json={"month": "2026-10", "entries": {"team": 30_240_000, f"u:{ngoc.id}": "10.000.000"}},
    ).json()
    assert {line["key"]: line["amount"] for line in saved["lines"]}["team"] == 30_240_000

    report = world.client.get("/api/ads/team", params={"month": "2026-10"}).json()
    now = report["current"]
    # "Miền" = both (2026-10-30): Ngọc's SG + HN parts, Hotline, HN chuyển HCM.
    assert now["revenue"] == 15_120_000 and report["marketers_hn"] == 1_080_000
    # By the branch that served: HCM = SG parts + Hotline + HN chuyển HCM; HN =
    # Ngọc's HN part (typed lines are the HCM team's).
    assert report["revenue_hn_transfer"] == 1_080_000 and report["revenue_hcm"] == 14_040_000
    flows = {flow["key"]: flow["revenue"] for flow in report["flows"]}
    assert flows == {
        "hcm_hcm": 12_960_000,
        "hn_hn": None,
        "hn_hcm": 1_080_000,
        "hcm_hn": 1_080_000,
    }
    assert round(now["revenue_net"]) == 14_000_000
    assert report["team_kpi_rate"] == 0.5 and 0 < report["month_progress"] <= 1
    # No charges yet that day for 222: Ads / DT uses the charges, else the spend.
    assert now["spend"] == 3_500_000
    sources = {source["key"]: source["revenue"] for source in report["sources"]}
    assert sources["sg"] == 11_880_000 and sources["s:HN"] == 1_080_000
    assert sources["s:HOTLINE"] == 2_160_000 and sources["s:GOOGLE"] is None
    nets = {source["key"]: source["revenue_net"] for source in report["sources"]}
    assert round(nets["s:HOTLINE"]) == 2_000_000 and nets["s:GOOGLE"] is None
    first = report["marketers"][0]
    assert first["name"] == "Mạnh Ngọc" and first["figures"]["revenue"] == 11_880_000
    assert round(first["kpi_rate"], 3) == 1.188 and first["tone"] == "good"
    assert {alert["name"]: alert["kind"] for alert in report["attention"]} == {
        "Hoàng Anh": "no_revenue"
    }
    assert report["facebook"]["spend"] == 3_000_000 + 500_000
    history = world.client.get("/api/ads/history", params={"month": "2026-10"}).json()
    assert history["user_id"] is None
    assert history["months"][0]["month"] == "2026-04-01"
    assert history["months"][-1]["month"] == "2026-10-01"
    assert history["months"][-1]["kpi_rate"] == 0.5
    assert history["months"][-1]["figures"]["revenue"] == 15_120_000
    assert history["days"][0]["day"] == "2026-10-01"
    by_day = {line["day"]: line["figures"] for line in history["days"]}
    assert by_day[D2.isoformat()]["revenue"] == 15_120_000
    assert by_day[D1.isoformat()]["spend"] == 1_000_000
    # One marketer: their accounts, their own revenue (SG + HN), their KPI.
    mine = world.client.get(
        "/api/ads/history", params={"month": "2026-10", "user_id": str(ngoc.id)}
    ).json()
    october = mine["months"][-1]
    assert mine["user_id"] == str(ngoc.id)
    assert october["figures"]["spend"] == 3_000_000
    assert october["figures"]["revenue"] == 11_880_000
    assert october["kpi"] == 10_000_000 and round(october["kpi_rate"], 3) == 1.188
    # A range: compared with the same number of days before; the KPI stays
    # October's, measured from the 1st to the range's end.
    window = {"start": D2.isoformat(), "end": D2.isoformat()}
    one_day = world.client.get("/api/ads/team", params=window).json()
    assert (one_day["start"], one_day["end"]) == (D2.isoformat(), D2.isoformat())
    assert one_day["previous_end"] == D1.isoformat() and one_day["month"] == "2026-10"
    assert one_day["current"]["spend"] == 2_500_000 and one_day["before"]["spend"] == 1_000_000
    assert one_day["team_kpi_rate"] == 0.5
    first_day = world.client.get(
        "/api/ads/team", params={"start": D1.isoformat(), "end": D1.isoformat()}
    ).json()
    assert first_day["current"]["revenue"] is None and first_day["team_kpi_rate"] == 0
    assert world.client.get("/api/ads/team", params={"start": D2.isoformat()}).status_code == 422
    backwards = {"start": D2.isoformat(), "end": D1.isoformat()}
    assert world.client.get("/api/ads/team", params=backwards).status_code == 422
    hcm = world.client.get("/api/ads/team", params={"month": "2026-10", "site": "site_hcm"}).json()
    assert hcm["current"]["revenue"] == 14_040_000
    assert {s["key"]: s["revenue"] for s in hcm["flows"]}["hcm_hn"] is None
    # The KPI is the department's, both regions, whatever "Miền".
    assert hcm["team_kpi_rate"] == 0.5
    assert hcm["marketers"][0]["figures"]["revenue"] == 10_800_000
    only_hn = world.client.get("/api/ads/team", params={"month": "2026-10", "site": "hn"}).json()
    assert only_hn["current"]["revenue"] == 1_080_000
    assert only_hn["marketers"][0]["figures"]["revenue"] == 1_080_000
    # By the marketing team: HN = what HN accounts brought (here "HN chuyển HCM").
    team_hn = world.client.get(
        "/api/ads/team", params={"month": "2026-10", "site": "mkt_hn"}
    ).json()
    assert team_hn["current"]["revenue"] == 1_080_000
    one_flow = world.client.get(
        "/api/ads/team", params={"month": "2026-10", "site": "hcm_hn"}
    ).json()
    assert one_flow["current"]["revenue"] == 1_080_000
    assert world.client.get("/api/ads/team", params={"site": "nowhere"}).status_code == 422

    world.act_as(ngoc)
    assert world.client.get("/api/ads/team").status_code == 404
    # A member's reports are always their own, whoever they ask for.
    own = world.client.get(
        "/api/ads/history", params={"month": "2026-10", "user_id": str(uuid.uuid4())}
    ).json()
    assert own["user_id"] == str(ngoc.id)
    assert own["months"][-1]["figures"]["revenue"] == 11_880_000
    denied = world.client.put("/api/ads/kpi", json={"month": "2026-10", "entries": {"team": 1}})
    assert denied.status_code == 403


async def test_12_meta_aborting_part_of_a_batch_is_asked_again() -> None:
    """A real 181-account BM: 50 ``/activities`` at once made Meta abort part of
    the batch (code 960, "the entire request timed out"). Those accounts are
    asked again in a smaller batch, and the reason is not called a permission
    problem. An account with more than one page of activities keeps the token
    and cursor of Meta's "next" link (httpx used to drop them)."""
    aborted = {
        "code": 500,
        "body": json.dumps({"error": {"code": 960, "message": "Request aborted."}}),
    }
    sizes: list[int] = []

    def charge(account: str) -> dict[str, Any]:
        event = {
            "event_time": "2026-10-20T03:00:00+0000",
            "event_type": "ad_account_billing_charge",
            "extra_data": json.dumps({"new_value": 1000, "transaction_id": f"t-{account}"}),
        }
        return {"code": 200, "body": json.dumps({"data": [event]})}

    def page_two(request: httpx.Request) -> httpx.Response:
        # Meta's "next" link carries the token and cursor; without them: #200.
        if request.url.params.get("access_token") != TOKEN or not request.url.params.get("after"):
            return httpx.Response(
                400, json={"error": {"code": 200, "message": "(#200) Provide valid app ID"}}
            )
        return httpx.Response(200, json={"data": [json.loads(charge("1003b")["body"])["data"][0]]})

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return page_two(request)
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        batch = json.loads(httpx.QueryParams(f"b={form['batch']}")["b"])
        sizes.append(len(batch))
        accounts = [item["relative_url"].split("/")[0].removeprefix("act_") for item in batch]
        first_round = len(sizes) <= 2
        answers = []
        for account in accounts:
            if first_round and account.endswith("7"):
                answers.append(aborted)
            elif account == "1003":
                # More than one page of activities.
                first = json.loads(charge(account)["body"])
                first["paging"] = {
                    "next": f"https://graph.facebook.com/v26.0/act_1003/activities"
                    f"?limit=500&access_token={TOKEN}&after=cursor-2"
                }
                answers.append({"code": 200, "body": json.dumps(first)})
            else:
                answers.append(charge(account))
        return httpx.Response(200, json=answers)

    client = GraphMetaAdsClient(
        http=httpx.AsyncClient(transport=httpx.MockTransport(answer)), pause=0
    )
    accounts = [str(1000 + index) for index in range(12)]
    pulled = await client.billing_charges(TOKEN, accounts, D1, D2)
    await client.aclose()

    assert pulled.errors == {}
    assert sorted(charge.account_id for charge in pulled.rows) == sorted([*accounts, "1003"])
    # Ten at a time, then the one aborted account again in a half-size batch.
    assert sizes == [10, 2, 1]
    assert "thử lại" in explain({"code": 960, "message": "Request aborted."}).message
    assert "quyền" not in explain({"code": 960, "message": "Request aborted."}).message


async def test_13_two_syncs_meeting_the_same_new_accounts(world: World) -> None:
    """ "Đồng bộ ngay" for spend and charges run side by side: on a new BM both
    discover the same accounts. The one that looked first and found nothing must
    not fail on the rows the other has just written (27/10: pk_ads_accounts)."""
    client, _ = setup(world)
    add_bm(world)
    service = AdsSyncService(
        world.session,
        world.ads_settings,
        client=client,  # type: ignore[attr-defined]
    )
    await service.sync_spend(D1, D1)
    world.session.expunge_all()
    looked = service._accounts
    calls = 0

    async def first_look_misses(ids: list[str]) -> dict[str, AdsAccount]:
        nonlocal calls
        calls += 1
        return {} if calls == 1 else await looked(ids)

    service._accounts = first_look_misses  # type: ignore[method-assign]
    client.accounts[BM][0] = AdAccountInfo("111", "[COREMIND] TK 1 - đổi tên", status=1)
    report = await service.sync_charges(D1, D1)
    assert report.status == "SUCCESS" and report.accounts == 3
    renamed = await world.session.get(AdsAccount, "111")
    assert renamed is not None and renamed.name == "[COREMIND] TK 1 - đổi tên"


async def test_14_every_report_filters_by_bm(world: World) -> None:
    """Pick a BM: spend, charges and the marketers narrow to its accounts; the
    revenue (per person, not per BM) stays whole (2026-10-30)."""
    from meobot.db.models.ads import AdsConnection

    _, ngoc, anh = await staffed(world)
    world.client.put(
        "/api/ads/revenue", json={"day": D2.isoformat(), "entries": {f"u:{ngoc.id}": 5_400_000}}
    )
    first = (await world.session.execute(select(AdsConnection))).scalar_one()
    second = AdsConnection(name="BM Hà Nội", business_id="999", site="Hà Nội", token_encrypted="x")
    world.session.add(second)
    await world.session.flush()
    account = await world.session.get(AdsAccount, "222")
    assert account is not None
    account.connection_id = second.id
    await world.session.flush()

    params = {"start": D1.isoformat(), "end": D2.isoformat()}
    staff = world.client.get(
        "/api/ads/dashboard", params={**params, "connection_id": str(second.id)}
    ).json()
    assert [line["account_id"] for line in staff["accounts"]] == ["222"]
    assert staff["revenue"] == 5_400_000 and staff["revenue_note"] is None

    whole = world.client.get("/api/ads/team", params={"month": "2026-10"}).json()
    assert whole["revenue_note"] is None and whole["connection_id"] is None
    assert {row["name"] for row in whole["connections"]} == {"BM Apex", "BM Hà Nội"}
    assert whole["current"]["spend"] == 3_500_000

    hanoi = world.client.get(
        "/api/ads/team", params={"month": "2026-10", "connection_id": str(second.id)}
    ).json()
    assert hanoi["connection_id"] == str(second.id) and hanoi["revenue_note"] is None
    assert hanoi["current"]["spend"] == 500_000 and hanoi["current"]["revenue"] == 5_400_000
    assert [line["name"] for line in hanoi["marketers"]] == ["Hoàng Anh"]
    assert hanoi["facebook"]["spend"] == 500_000
    assert [alert["kind"] for alert in hanoi["attention"]] == ["no_revenue"]
    reports = world.client.get(
        "/api/ads/history", params={"month": "2026-10", "connection_id": str(second.id)}
    ).json()
    assert reports["revenue_note"] is None
    assert reports["months"][-1]["figures"]["spend"] == 500_000
    assert reports["months"][-1]["figures"]["revenue"] == 5_400_000
    assert sum(line["figures"]["spend"] for line in reports["days"]) == 500_000

    apex = world.client.get(
        "/api/ads/team", params={"month": "2026-10", "connection_id": str(first.id)}
    ).json()
    assert apex["current"]["spend"] == 3_000_000
    assert [line["user_id"] for line in apex["marketers"]] == [str(ngoc.id)]
    assert str(anh.id) not in {line["user_id"] for line in apex["marketers"]}

    # One person, accounts in several BMs: their own page lists those BMs and
    # filters them apart.
    other = await world.session.get(AdsAccount, "333")
    assert other is not None
    other.connection_id = second.id
    other.user_id = ngoc.id
    await world.session.flush()
    world.act_as(ngoc)
    mine = world.client.get("/api/ads/dashboard", params=params).json()
    assert {row["name"] for row in mine["connections"]} == {"BM Apex", "BM Hà Nội"}
    apex_only = world.client.get(
        "/api/ads/dashboard", params={**params, "connection_id": str(first.id)}
    ).json()
    assert [line["account_id"] for line in apex_only["accounts"]] == ["111"]
    assert {kpi["key"]: kpi["value"] for kpi in apex_only["kpis"]}["spend"] == 3_000_000
    world.act_as(anh)
    theirs = world.client.get("/api/ads/dashboard", params=params).json()
    assert [row["name"] for row in theirs["connections"]] == ["BM Hà Nội"]
