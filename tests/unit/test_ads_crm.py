"""Phòng Ads (0083): thực thu from the CRM aicrmtech.vn - the client, the
hourly sync, CRM accounts summed per person (HCM -> SG, HN -> HN), the
mapping screen and its workbook import, and the dashboards reading it."""

from __future__ import annotations

import base64
import io
import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from openpyxl import Workbook
from pydantic import SecretStr
from sqlalchemy import select

from meobot.api.deps import get_app_settings
from meobot.api.routers.ads import get_task_sender
from meobot.application.ads.crm_revenue import load_coverage
from meobot.application.ads.crm_service import AdsCrmSyncService, crm_window
from meobot.db.models.ads import AdsCrmAccount, AdsCrmPayment
from meobot.domain.ads.crm import CrmRegion, region_of
from meobot.integrations.crm.client import (
    CrmBranch,
    CrmError,
    CrmMarketingUser,
    CrmPayment,
    FakeCrmClient,
    HttpCrmClient,
    parse_payment,
)
from tests.unit.pr_world import World
from tests.unit.test_ads_department import D1, D2, Sender, person, staffed

KEY = "crm-test-key-123"
PARAMS = {"start": D1.isoformat(), "end": D2.isoformat()}


def pay(pid: int, day: date, price: int, marketing: int | None, kind: int = 4) -> CrmPayment:
    return CrmPayment(pid, day, Decimal(price), kind, marketing, f"MKT {marketing}")


CRM = FakeCrmClient(
    users=[
        CrmMarketingUser(10, "SG - MKT MẠNH NGỌC", True),
        CrmMarketingUser(11, "HN - MKT MẠNH NGỌC", True),
        CrmMarketingUser(12, "Hotline FB", True),
        CrmMarketingUser(13, "MKT - HOÀNG ANH", False),
    ],
    rows=[
        pay(1, D1, 5_000_000, 10),
        pay(2, D1, 1_000_000, 11),
        pay(3, D1, 700_000, 10, kind=3),  # points: not money
        pay(4, D2, -500_000, 10, kind=7),  # a refund
        pay(5, D2, 2_000_000, 12),
        pay(6, D2, 900_000, 99),  # an id the directory does not list
        pay(7, D2, 300_000, None),
    ],
)


def test_01_rules() -> None:
    assert region_of("HN - MKT NGUYỄN QUANG NGUYÊN") is CrmRegion.HN
    assert region_of("HN-FB-BÙI ĐÌNH ĐỨC") is CrmRegion.HN
    assert region_of("SG - MKT PHẠM NGỌC THẮNG") is CrmRegion.HCM
    assert region_of("HCM-FB-HOÀNG ANH") is CrmRegion.HCM
    assert region_of("HCM- MKT ĐỖ SƠN TÙNG") is CrmRegion.HCM
    assert region_of("MKT - VƯƠNG ANH ĐỨC") is None and region_of("Hotline FB") is None
    assert region_of("HNI shop") is None
    assert region_of("Đã nghỉ - HN-FB-ĐỖ MINH SANG") is CrmRegion.HN
    assert region_of("ĐÃ NGHỈ - MKT- ĐỖ MINH SANG") is None
    assert crm_window(date(2026, 10, 29)) == (date(2026, 10, 1), date(2026, 10, 29))
    assert crm_window(date(2026, 11, 3)) == (date(2026, 10, 1), date(2026, 11, 3))
    raw = {
        "id": 9,
        "payment_date": "2026-10-20",
        "price": -1500000,
        "payment_type": 7,
        "marketing": {"id": 467, "name": "HCM-FB-HOÀNG ANH"},
        "branch": {"id": 9, "name": "SG"},
        "order": {"id": 77, "code": "DH1"},
        "customer": {"id": 1, "full_name": "Khách", "phone": "09", "is_upsale": 1},
        "customer_source": {"id": 3, "name": "Facebook"},
    }
    parsed = parse_payment(raw)
    assert (parsed.price, parsed.marketing_id, parsed.branch_id, parsed.order_id) == (
        Decimal(-1500000),
        467,
        9,
        77,
    )
    assert parsed.is_upsale is True and parsed.source_id == 3
    # Nothing personal is kept.
    assert "phone" not in repr(parsed) and "Khách" not in repr(parsed)


async def test_02_the_http_client_sends_the_key_in_a_header_only() -> None:
    seen: list[httpx.Request] = []
    answers = iter(
        [
            httpx.Response(429, json={"code": 429}),
            httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "payment_date": "2026-10-20", "price": 10}],
                    "meta": {"last_page": 2},
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [{"id": 2, "payment_date": "2026-10-21", "price": 20}],
                    "meta": {"last_page": 2},
                },
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(answers)

    client = HttpCrmClient(
        "https://crm.test/api/internal", KEY, transport=httpx.MockTransport(handler), waits=(0,)
    )
    rows = await client.payments(D1, D2)
    await client.aclose()
    assert [row.id for row in rows] == [1, 2]
    assert all(request.headers["x-api-key"] == KEY for request in seen)
    assert all(KEY not in str(request.url) for request in seen)
    assert seen[-1].url.params["page"] == "2" and seen[-1].url.params["start_date"] == "2026-10-20"
    assert seen[0].url.path == "/api/internal/raw-data/order-payments"

    refused = HttpCrmClient(
        "https://crm.test/api/internal",
        "bad",
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={})),
        waits=(),
    )
    try:
        await refused.marketing_users()
        raise AssertionError("expected a refusal")
    except CrmError as error:
        assert "key" in error.message and error.status == 401
    await refused.aclose()


async def test_03_the_sync_replaces_the_days_it_reads(world: World) -> None:
    report = await AdsCrmSyncService(world.session, CRM).sync(D1, D2)
    assert report.status == "SUCCESS" and report.payments == 7 and report.new_accounts == 5
    # Counted: 5.000.000 + 1.000.000 - 500.000 + 2.000.000 + 900.000 + 300.000.
    assert report.total == Decimal(8_700_000)
    accounts = {a.crm_id: a for a in (await world.session.scalars(select(AdsCrmAccount))).all()}
    assert accounts[10].region is CrmRegion.HCM and accounts[11].region is CrmRegion.HN
    assert accounts[12].region is None and accounts[13].active is False
    assert accounts[99].in_directory is False and accounts[99].name == "MKT 99"
    coverage, _ = await load_coverage(world.session)
    assert (coverage.synced_from, coverage.synced_to, coverage.last_status) == (
        D1,
        D2,
        "SUCCESS",
    )

    # Payment 5 was deleted on the CRM, payment 2 moved to D2: the stored
    # days follow.
    later = FakeCrmClient(
        users=CRM.users,
        rows=[
            pay(1, D1, 5_000_000, 10),
            pay(2, D2, 1_000_000, 11),
        ],
    )
    await AdsCrmSyncService(world.session, later).sync(D1, D2)
    stored = {p.id: p for p in (await world.session.scalars(select(AdsCrmPayment))).all()}
    assert set(stored) == {1, 2} and stored[2].payment_date == D2

    broken = FakeCrmClient(error=CrmError("CRM từ chối key (x-api-key sai hoặc đã bị thu hồi)."))
    failed = await AdsCrmSyncService(world.session, broken).sync(D1, D2)
    assert failed.status == "FAILED" and "từ chối key" in failed.as_task_result()["message"]
    coverage, _ = await load_coverage(world.session)
    assert coverage.synced_to == D2 and coverage.last_status == "FAILED"


async def _mapped(world: World) -> tuple[Any, Any, Any]:
    head, ngoc, anh = await staffed(world)
    await AdsCrmSyncService(world.session, CRM).sync(D1, D2)
    world.act_as(head)
    for crm_id, body in (
        (10, {"user_id": str(ngoc.id)}),
        (11, {"user_id": str(ngoc.id)}),
        (12, {"source": "HOTLINE"}),
    ):
        response = world.client.patch(f"/api/ads/crm/accounts/{crm_id}", json=body)
        assert response.status_code == 200, response.json()
    return head, ngoc, anh


async def test_04_accounts_sum_per_person_and_replace_typed_revenue(world: World) -> None:
    _, ngoc, anh = await staffed(world)
    # Typed by hand before the CRM: Ngọc's will be replaced, Anh's stays.
    world.client.put(
        "/api/ads/revenue",
        json={
            "day": D1.isoformat(),
            "entries": {f"u:{ngoc.id}": 9_999_000, f"u:{anh.id}": 4_000_000},
        },
    )
    await AdsCrmSyncService(world.session, CRM).sync(D1, D2)
    for crm_id, body in (
        (10, {"user_id": str(ngoc.id)}),
        (11, {"user_id": str(ngoc.id)}),
        (12, {"source": "HOTLINE"}),
    ):
        world.client.patch(f"/api/ads/crm/accounts/{crm_id}", json=body)

    day = world.client.get("/api/ads/revenue", params={"day": D1.isoformat()}).json()
    lines = {line["key"]: line for line in day["lines"]}
    mine = lines[f"u:{ngoc.id}"]
    # HCM account -> SG, HN account -> HN; points left out.
    assert (mine["amount"], mine["amount_hn"], mine["from_crm"]) == (5_000_000, 1_000_000, True)
    assert lines[f"u:{anh.id}"]["amount"] == 4_000_000 and not lines[f"u:{anh.id}"]["from_crm"]
    assert lines["s:HOTLINE"]["from_crm"] is True and lines["s:HOTLINE"]["amount"] == 0
    refused = world.client.put(
        "/api/ads/revenue", json={"day": D1.isoformat(), "entries": {f"u:{ngoc.id}": 1}}
    )
    assert refused.status_code == 422 and refused.json()["code"] == "ads_revenue_from_crm"

    team = world.client.get("/api/ads/team", params=PARAMS).json()
    by_person = {line["user_id"]: line for line in team["marketers"]}
    # Ngọc: 5.000.000 + 1.000.000 (HN) - 500.000 refund on D2.
    assert by_person[str(ngoc.id)]["figures"]["revenue"] == 5_500_000
    assert by_person[str(anh.id)]["figures"]["revenue"] == 4_000_000
    sources = {line["key"]: line for line in team["sources"]}
    assert sources["s:HOTLINE"]["revenue"] == 2_000_000
    # The unmapped id 99 and the payment with no marketing count for nobody.
    world.act_as(ngoc)
    own = world.client.get("/api/ads/dashboard", params=PARAMS).json()
    kpis = {kpi["key"]: kpi["value"] for kpi in own["kpis"]}
    assert kpis["revenue_sg"] == 4_500_000 and kpis["revenue_hn"] == 1_000_000


async def test_05_the_screen_edits_and_imports_the_mapping(world: World) -> None:
    _, ngoc, anh = await _mapped(world)
    ngoc.member_code, anh.member_code = "M064NGOC", "M016ANH"
    await world.session.flush()
    overview = world.client.get("/api/ads/crm", params={"month": "2026-10"}).json()
    assert overview["configured"] is False and overview["synced_to"] == D2.isoformat()
    rows = {row["crm_id"]: row for row in overview["accounts"]}
    assert rows[10]["user_name"] == "Mạnh Ngọc" and rows[10]["region"] == "HCM"
    assert rows[10]["total"] == 4_500_000 and rows[12]["source_label"] == "Hotline"
    assert overview["unassigned_total"] == 900_000 and overview["unassigned_accounts"] == 1
    assert overview["people"][0]["in_ads"] is True

    # One person or one source: picking a source drops the person.
    switched = world.client.patch("/api/ads/crm/accounts/11", json={"source": "SEEDING"}).json()
    row = next(r for r in switched["accounts"] if r["crm_id"] == 11)
    assert row["user_id"] is None and row["source"] == "SEEDING"
    both = world.client.patch(
        "/api/ads/crm/accounts/11", json={"source": "SEEDING", "user_id": str(ngoc.id)}
    )
    assert both.status_code == 422
    region = world.client.patch("/api/ads/crm/accounts/12", json={"region": "HN"}).json()
    assert next(r for r in region["accounts"] if r["crm_id"] == 12)["region_label"] == "Hà Nội"
    assert world.client.patch("/api/ads/crm/accounts/555", json={"note": "x"}).status_code == 404

    def workbook(rows: list[list[object]]) -> str:
        book = Workbook()
        sheet = book.active
        sheet.title = "Gắn mã"
        sheet.append(
            [
                "STT",
                "CRM ID",
                "Tên Marketing trên CRM",
                "Mã nhân viên APM",
                "Hoặc tính vào nguồn ◆",
            ]
        )
        for line in rows:
            sheet.append(line)
        buffer = io.BytesIO()
        book.save(buffer)
        return base64.b64encode(buffer.getvalue()).decode()

    bad = world.client.post(
        "/api/ads/crm/import",
        json={"data": workbook([[1, 13, "x", "NOPE", None], [2, 12, "y", "M064NGOC", "Hotline"]])},
    )
    assert bad.status_code == 422 and bad.json()["code"] == "ads_crm_import_invalid"
    assert len(bad.json()["details"]["problems"]) == 2
    imported = world.client.post(
        "/api/ads/crm/import",
        json={
            "data": workbook(
                [
                    [1, 13, "MKT - HOÀNG ANH", "m016anh", None],
                    [2, 11, "HN - MKT MẠNH NGỌC", "M064NGOC", None],
                    [3, 12, "Hotline FB", None, "Không tính"],
                    [4, 4242, "HN - MKT MỚI", None, "Tiktok"],
                ]
            )
        },
    )
    assert imported.status_code == 200, imported.json()
    assert imported.json() == {"rows": 4, "changed": 4, "created": 1}
    accounts = {a.crm_id: a for a in (await world.session.scalars(select(AdsCrmAccount))).all()}
    assert accounts[13].user_id == anh.id and accounts[11].user_id == ngoc.id
    assert accounts[12].source is None and accounts[12].user_id is None
    assert accounts[4242].region is CrmRegion.HN and accounts[4242].source is not None

    world.act_as(ngoc)
    assert world.client.get("/api/ads/crm").status_code == 403
    world.act_as(await person(world, "Không ở ADS", None))
    assert world.client.get("/api/ads/crm").status_code == 404


async def test_06_sync_now_and_the_configured_flag(world: World) -> None:
    head, _, _ = await staffed(world)
    sender = Sender()
    app = world.client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_task_sender] = lambda: sender
    settings = world.ads_settings.model_copy(update={"crm_api_key": SecretStr(KEY)})  # type: ignore[attr-defined]
    app.dependency_overrides[get_app_settings] = lambda: settings
    world.act_as(head)
    assert world.client.get("/api/ads/crm").json()["configured"] is True
    queued = world.client.post(
        "/api/ads/crm/sync", json={"since": "2026-10-01", "until": "2026-10-21"}
    )
    assert queued.status_code == 202 and queued.json()["job_key"] == "ads.sync_crm_revenue"
    name, kwargs, _ = sender.sent[0]
    assert name == "ads.sync_crm_revenue" and kwargs == {
        "since": "2026-10-01",
        "until": "2026-10-21",
    }
    assert KEY not in json.dumps(sender.sent)
    future = world.client.post(
        "/api/ads/crm/sync", json={"since": "2026-10-01", "until": "2099-01-01"}
    )
    assert future.status_code == 422
    # The dashboard's default: the whole month (2026-10-30).
    from meobot.application.ads.accounts_service import month_of
    from meobot.application.ads.sync_service import vn_today

    board = world.client.get("/api/ads/dashboard").json()
    month = month_of(vn_today())
    assert (board["start"], board["end"]) == (month.start.isoformat(), month.end.isoformat())


async def test_07_the_branch_tells_where_the_customer_was_served(world: World) -> None:
    """HN accounts = the HN team; the payment's branch = where the customer was
    served: HN team served in SG = "HN chuyển SG", HCM team served in HN =
    "SG chuyển HN". Doanh số SG / HN follow the branch."""
    head, ngoc, _ = await staffed(world)
    crm = FakeCrmClient(
        users=CRM.users,
        branch_list=[CrmBranch(12, "HN1"), CrmBranch(1, "HN2"), CrmBranch(9, "SG")],
        rows=[
            CrmPayment(1, D1, Decimal(4_000_000), 4, 10, branch_id=9),  # HCM -> SG
            CrmPayment(2, D1, Decimal(1_000_000), 4, 10, branch_id=12),  # SG chuyển HN
            CrmPayment(3, D1, Decimal(2_000_000), 4, 11, branch_id=1),  # HN -> HN
            CrmPayment(4, D1, Decimal(500_000), 4, 11, branch_id=9),  # HN chuyển SG
        ],
    )
    await AdsCrmSyncService(world.session, crm).sync(D1, D2)
    coverage, _ = await load_coverage(world.session)
    assert coverage.branches == {12: CrmRegion.HN, 1: CrmRegion.HN, 9: CrmRegion.HCM}
    world.act_as(head)
    for crm_id in (10, 11):
        world.client.patch(f"/api/ads/crm/accounts/{crm_id}", json={"user_id": str(ngoc.id)})

    day = world.client.get("/api/ads/revenue", params={"day": D1.isoformat()}).json()
    mine = next(line for line in day["lines"] if line["key"] == f"u:{ngoc.id}")
    # Served in SG: 4.000.000 + 500.000; served in HN: 1.000.000 + 2.000.000.
    assert (mine["amount"], mine["amount_hn"]) == (4_500_000, 3_000_000)

    def team(site: str) -> Any:
        return world.client.get("/api/ads/team", params={**PARAMS, "site": site}).json()

    flows = {flow["key"]: flow["revenue"] for flow in team("all")["flows"]}
    assert flows == {
        "hcm_hcm": 4_000_000,
        "hn_hn": 2_000_000,
        "hn_hcm": 500_000,
        "hcm_hn": 1_000_000,
    }
    assert team("all")["current"]["revenue"] == 7_500_000
    # Doanh thu Marketing (by team) and Cơ sở (by branch): same total.
    regions = team("all")["regions"]
    assert (regions["hn"]["marketing"], regions["hcm"]["marketing"]) == (2_500_000, 5_000_000)
    assert (regions["hn"]["site"], regions["hcm"]["site"]) == (3_000_000, 4_500_000)
    # The cost by the BM's cơ sở (BM Apex = Sài Gòn); no HN cost.
    # (the second charge falls on 22/10 Vietnam time, outside the days)
    assert regions["hcm"]["spend"] == 3_500_000 and regions["hcm"]["invoice"] == 1_000_000
    assert regions["hn"]["spend"] == 0 and regions["hn"]["ads_ratio"] == 0
    # Hiệu suất = BM spend ÷ CRM revenue VAT included.
    assert regions["hcm"]["ads_ratio"] == 3_500_000 / 5_000_000
    assert regions["flows"]["hn_hcm"] == 500_000
    # BM Apex (Sài Gòn): the revenue the HCM accounts brought; Cơ sở unchanged.
    from meobot.db.models.ads import AdsConnection

    apex = (await world.session.execute(select(AdsConnection))).scalar_one()
    sg = world.client.get("/api/ads/team", params={**PARAMS, "connection_id": str(apex.id)}).json()
    assert sg["bm_region"] == "HCM" and sg["current"]["revenue"] == 5_000_000
    assert sg["regions"]["hn"]["site"] == 3_000_000
    assert team("site_hcm")["current"]["revenue"] == 4_500_000
    assert team("site_hn")["current"]["revenue"] == 3_000_000
    assert team("mkt_hn")["current"]["revenue"] == 2_500_000
    assert team("mkt_hcm")["current"]["revenue"] == 5_000_000
    assert team("hn_hcm")["current"]["revenue"] == 500_000
    assert team("hcm_hn")["marketers"][0]["figures"]["revenue"] == 1_000_000

    world.act_as(ngoc)
    own = world.client.get("/api/ads/dashboard", params={**PARAMS, "region": "mkt_hn"}).json()
    kpis = {kpi["key"]: kpi["value"] for kpi in own["kpis"]}
    assert kpis["revenue"] == 2_500_000 and kpis["revenue_sg"] == 500_000
    assert own["flows"] == {"hcm_hcm": 0, "hn_hn": 2_000_000, "hn_hcm": 500_000, "hcm_hn": 0}
    whole = world.client.get("/api/ads/dashboard", params=PARAMS).json()["regions"]
    assert (whole["hn"]["marketing"], whole["hcm"]["site"]) == (2_500_000, 4_500_000)
    assert whole["hcm"]["spend"] == 3_000_000


async def test_08_hotline_and_former_marketers_are_lines_of_their_own(world: World) -> None:
    """A ◆ Hotline account (its hotline picked or read from the name) goes to the
    BM of the branch that served; a former marketer (no app account) is a line
    under the CRM name. Neither has a cost or a page."""
    from meobot.db.models.ads import AdsConnection

    head, _, _ = await staffed(world)
    crm = FakeCrmClient(
        users=CRM.users,
        branch_list=[CrmBranch(12, "HN1"), CrmBranch(9, "SG")],
        rows=[
            CrmPayment(1, D1, Decimal(3_000_000), 4, 12, branch_id=12),  # hotline, HN
            CrmPayment(2, D1, Decimal(1_000_000), 4, 12, branch_id=9),  # hotline, SG
            CrmPayment(3, D1, Decimal(700_000), 4, 13, branch_id=9),  # former, HN -> SG
        ],
    )
    await AdsCrmSyncService(world.session, crm).sync(D1, D2)
    world.act_as(head)
    hotline = world.client.patch("/api/ads/crm/accounts/12", json={"source": "HOTLINE"}).json()
    row = next(r for r in hotline["accounts"] if r["crm_id"] == 12)
    assert row["hotline_kind"] == "FB" and row["hotline_kind_label"] == "Facebook"
    assert {o["value"] for o in hotline["hotline_kinds"]} == {"FB", "TIKTOK", "GOOGLE"}
    former = world.client.patch(
        "/api/ads/crm/accounts/13", json={"former": True, "region": "HN"}
    ).json()
    assert next(r for r in former["accounts"] if r["crm_id"] == 13)["former"] is True
    both = world.client.patch(
        "/api/ads/crm/accounts/13", json={"former": True, "source": "HOTLINE"}
    )
    assert both.status_code == 422

    team = world.client.get("/api/ads/team", params=PARAMS).json()
    others = {line["kind"]: line for line in team["others"]}
    assert others["hotline"]["name"] == "Hotline"
    assert others["hotline"]["figures"]["revenue"] == 4_000_000
    assert others["hotline"]["figures"]["spend"] == 0
    assert others["hotline"]["kinds"] == {"FB": 4_000_000}
    assert others["former"]["name"] == "MKT - HOÀNG ANH"
    assert others["former"]["figures"]["revenue"] == 700_000
    # The hotline is nobody's team: HN-served = HN, SG-served = SG; the former
    # HN marketer's SG customer = HN chuyển SG.
    flows = {flow["key"]: flow["revenue"] for flow in team["flows"]}
    assert flows["hn_hn"] == 3_000_000 and flows["hcm_hcm"] == 1_000_000
    assert flows["hn_hcm"] == 700_000
    apex = (await world.session.execute(select(AdsConnection))).scalar_one()
    sg = world.client.get("/api/ads/team", params={**PARAMS, "connection_id": str(apex.id)}).json()
    assert [(line["kind"], line["figures"]["revenue"]) for line in sg["others"]] == [
        ("hotline", 1_000_000)
    ]
    # The entry page names the former marketer.
    day = world.client.get("/api/ads/revenue", params={"day": D1.isoformat()}).json()
    names = {line["key"]: line["label"] for line in day["lines"]}
    assert names["c:13"] == "MKT - HOÀNG ANH (đã nghỉ)"


async def test_09_a_day_lists_who_paid_what(world: World) -> None:
    """ "Báo cáo ngày" (0086): a day opens on the customer codes behind its
    revenue - each customer's thực thu that day, refunds included, points not -
    plus what was typed by hand; together the day's revenue, narrowed like the
    report (a member: their own; a BM: its region's Marketing)."""
    parsed = parse_payment(
        {"id": 1, "payment_date": "2026-10-20", "price": 1, "customer": {"account_code": " KH9 "}}
    )
    assert parsed.customer_code == "KH9"
    head, ngoc, anh = await staffed(world)
    crm = FakeCrmClient(
        users=CRM.users,
        branch_list=[CrmBranch(12, "HN1"), CrmBranch(1, "HN2"), CrmBranch(9, "SG")],
        rows=[
            CrmPayment(1, D1, Decimal(4_000_000), 4, 10, branch_id=9, customer_code="KH001"),
            CrmPayment(2, D1, Decimal(1_000_000), 1, 10, branch_id=9, customer_code="KH001"),
            CrmPayment(3, D1, Decimal(2_000_000), 4, 11, branch_id=1, customer_code="KH002"),
            CrmPayment(4, D1, Decimal(700_000), 3, 10, branch_id=9, customer_code="KH003"),
            CrmPayment(5, D1, Decimal(-300_000), 7, 10, branch_id=9, customer_code="KH004"),
            CrmPayment(6, D1, Decimal(900_000), 4, 12, branch_id=12, customer_code="KH005"),
            CrmPayment(7, D2, Decimal(800_000), 4, 10, branch_id=9, customer_code="KH006"),
        ],
    )
    await AdsCrmSyncService(world.session, crm).sync(D1, D2)
    stored = await world.session.get(AdsCrmPayment, 1)
    assert stored is not None and stored.customer_code == "KH001"
    world.act_as(head)
    for crm_id, body in (
        (10, {"user_id": str(ngoc.id)}),
        (11, {"user_id": str(ngoc.id)}),
        (12, {"source": "HOTLINE"}),
    ):
        world.client.patch(f"/api/ads/crm/accounts/{crm_id}", json=body)
    world.client.put(
        "/api/ads/revenue", json={"day": D1.isoformat(), "entries": {f"u:{anh.id}": 4_000_000}}
    )

    def detail(**params: str) -> Any:
        response = world.client.get(
            "/api/ads/history/day", params={"day": D1.isoformat(), **params}
        )
        assert response.status_code == 200, response.json()
        return response.json()

    def listed(body: Any) -> list[tuple[str | None, float, int]]:
        return [(c["customer_code"], c["amount"], c["payments"]) for c in body["customers"]]

    team = detail()
    assert listed(team) == [
        ("KH001", 5_000_000, 2),
        ("KH002", 2_000_000, 1),
        ("KH005", 900_000, 1),
        ("KH004", -300_000, 1),
    ]
    assert team["typed"] == 4_000_000 and team["revenue"] == 11_600_000
    # The same figure as the day's line of the report.
    history = world.client.get("/api/ads/history", params={"month": "2026-10"}).json()
    line = next(d for d in history["days"] if d["day"] == D1.isoformat())
    assert line["figures"]["revenue"] == team["revenue"]

    one = detail(user_id=str(ngoc.id))
    assert [c[0] for c in listed(one)] == ["KH001", "KH002", "KH004"]
    assert (one["revenue"], one["typed"]) == (6_700_000, 0)

    # BM Apex (Sài Gòn): what the HCM team brought - the hotline served in HN
    # and the HN account's customer leave; Anh's typed line stays.
    from meobot.db.models.ads import AdsConnection

    apex = (await world.session.execute(select(AdsConnection))).scalar_one()
    sg = detail(connection_id=str(apex.id))
    assert [c[0] for c in listed(sg)] == ["KH001", "KH004"]
    assert (sg["revenue"], sg["typed"]) == (8_700_000, 4_000_000)

    # A member sees only their own, whoever they ask for.
    world.act_as(anh)
    mine = detail(user_id=str(ngoc.id))
    assert (mine["customers"], mine["typed"], mine["revenue"]) == ([], 4_000_000, 4_000_000)
