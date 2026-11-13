"""Tài chính đợt 1 (0105): ONE CRM run for Phòng Ads and Tài chính (user
13/11), what it keeps, the "Doanh thu" month and its settings."""

# The CRM's times are naive Vietnam times, as the client parses them.
# ruff: noqa: DTZ001

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from meobot.api.routers.finance import get_task_sender
from meobot.application.crm.sync import CrmSyncService
from meobot.application.finance.revenue import FinanceRevenueService
from meobot.application.org.departments import ensure_default_departments, seat_person
from meobot.db.models.ads import AdsCrmPayment
from meobot.db.models.department import Department, DepartmentPermission
from meobot.db.models.finance import (
    CrmBranchRecord,
    CrmOrderRecord,
    CrmShareRecord,
    CrmStaffRecord,
    CrmTourRecord,
)
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.finance.crm import order_vat_percent, vat_inside, whole
from meobot.domain.identity.models import Role
from meobot.domain.org.models import Position
from meobot.integrations.crm.client import (
    CrmBranch,
    CrmDepartment,
    CrmError,
    CrmMarketingUser,
    CrmOrder,
    CrmPayment,
    CrmSharedRevenue,
    CrmTour,
    FakeCrmClient,
    parse_order,
    parse_shared_revenue,
    parse_tour,
)
from tests.unit.pr_world import World
from tests.unit.test_ads_department import Sender

D1 = date(2026, 10, 20)
D2 = date(2026, 10, 21)
TODAY = date(2026, 10, 21)


def _order(oid: int, day: date, total: int, vat: int, rest: int, branch: int = 1) -> CrmOrder:
    return CrmOrder(
        id=oid,
        code=f"DH{oid}",
        created_at=datetime(day.year, day.month, day.day, 10, 0),
        branch_id=branch,
        customer_id=oid,
        customer_code=f"KH{oid}",
        customer_name=f"Khách {oid}",
        source_id=None,
        all_total=Decimal(total),
        amount_before_vat=Decimal(total - vat),
        vat_amount=Decimal(vat),
        vat_percent=Decimal(8),
        gross_revenue=Decimal(total - rest),
        the_rest=Decimal(rest),
        discount=Decimal(0),
        payment_type=4,
        order_type=0,
        role_type=1,
        owner_id=50,
        owner_name="Người lên đơn",
    )


def _pay(
    pid: int, day: date, price: int, order: int | None, branch: int = 1, kind: int = 4
) -> CrmPayment:
    return CrmPayment(
        id=pid,
        day=day,
        price=Decimal(price),
        payment_type=kind,
        marketing_id=10,
        marketing_name="HN - MKT A",
        branch_id=branch,
        order_id=order,
        telesales_id=20,
        telesales_name="Sale B",
        cskh_id=30,
        cskh_name="CSKH C",
        owner_id=50,
        owner_name="Người lên đơn",
        order_code=f"DH{order}" if order else None,
        customer_name="Khách",
    )


def crm() -> FakeCrmClient:
    return FakeCrmClient(
        users=[CrmMarketingUser(10, "HN - MKT A", True)],
        branch_list=[CrmBranch(1, "HN1", 1), CrmBranch(9, "SG", 2)],
        rows=[
            _pay(1, D1, 1_080_000, 100),  # its order: 8% VAT
            _pay(2, D2, 2_000_000, None, branch=9),  # no order known: the default rate
            _pay(3, D2, 500_000, 100, kind=3),  # points: not money
            _pay(4, D2, -108_000, 100, kind=7),  # a refund
        ],
        order_list=[
            _order(100, D1, 1_080_000, 80_000, 0),
            _order(101, D2, 3_240_000, 240_000, 1_000_000, branch=9),
        ],
        departments=[CrmDepartment(10, "Bác Sĩ"), CrmDepartment(6, "Kỹ thuật viên")],
        shares=[
            CrmSharedRevenue(
                D1,
                10,
                "DH100",
                "KH100",
                "Khách",
                "Tiêm",
                Decimal(1_080_000),
                4,
                700,
                "BS An",
                1,
                Decimal(300_000),
                "HN1",
            ),
            CrmSharedRevenue(
                D2,
                6,
                "DH101",
                "KH101",
                "Khách",
                "Da",
                Decimal(2_000_000),
                4,
                800,
                "KTV Bình",
                0,
                Decimal(100_000),
                "SG",
            ),
        ],
        tour_list=[
            CrmTour(
                datetime(2026, 10, 20, 9),
                "DH100",
                "KH100",
                "Khách",
                "Tiêm",
                0,
                800,
                "KTV Bình",
                "primary",
                None,
                "HN1",
                None,
            ),
            CrmTour(
                datetime(2026, 10, 20, 9),
                "DH100",
                "KH100",
                "Khách",
                "Tiêm",
                0,
                801,
                "KTV Cường",
                "support",
                None,
                "HN1",
                None,
            ),
        ],
    )


def test_01_rules_and_parsing() -> None:
    assert order_vat_percent(Decimal(1_080_000), Decimal(80_000)) == Decimal(8)
    assert order_vat_percent(Decimal(0), Decimal(0)) is None
    assert whole(vat_inside(Decimal(1_080_000), Decimal(8))) == Decimal(80_000)
    order = parse_order(
        {
            "id": 456,
            "code": "DH000456",
            "created_at": "2026-10-20 11:00:00",
            "branch": {"id": 1, "name": "HN1"},
            "customer": {"id": 123, "account_code": "KH000123", "full_name": "A", "phone": "09"},
            "items": [
                {"service_id": 8, "service_name": "Da", "quantity": 1, "total_price": 1000000}
            ],
            "all_total": 1000000,
            "amount_before_vat": 925926,
            "vat_amount": 74074,
            "vat_percent": 8,
            "gross_revenue": 500000,
            "the_rest": 500000,
            "discount": 0,
            "owner": {"id": 50, "name": "X"},
        }
    )
    assert order.created_at == datetime(2026, 10, 20, 11) and order.items[0].service_id == 8
    assert order.the_rest == Decimal(500000) and order.customer_name == "A"
    assert "'09'" not in repr(order)
    share = parse_shared_revenue(
        {
            "order_code": "DH1",
            "payment_date": "2026-09-28",
            "payment_amount": 9000000,
            "payment_type": {"value": 4, "label": "Chuyển khoản"},
            "employee": {"id": 10049, "name": "BS"},
            "share_type": {"value": 2, "label": "Bác sĩ phẫu"},
            "shared_revenue": 15000000,
            "branch_name": "HN1",
        },
        10,
    )
    assert (share.share_type, share.payment_type, share.employee_id) == (2, 4, 10049)
    tour = parse_tour(
        {
            "order_code": "DH1",
            "employee": {"id": 10, "name": "A"},
            "work_type": {"value": "support", "label": "Hỗ trợ"},
            "treatment_type": {"value": 0, "label": "Trừ liệu trình"},
            "performed_at": "2026-10-20 11:05:00",
        }
    )
    assert tour.work_type == "support" and tour.treatment_type == 0


async def test_02_one_run_keeps_both(world: World) -> None:
    client = crm()
    report = await CrmSyncService(world.session, client).run(D1, D2, today=TODAY)
    assert report.status == "SUCCESS", report.message
    # One run, every dataset once (shares once per department).
    assert [name for name, *_ in client.calls] == [
        "users",
        "branches",
        "payments",
        "orders",
        "departments",
        "shares:10",
        "shares:6",
        "tours",
    ]
    assert "Tài chính: 2 đơn hàng, 2 doanh thu chia, 2 công tua" in report.message
    payment = await world.session.get(AdsCrmPayment, 1)
    assert payment is not None
    assert (payment.telesales_id, payment.cskh_id, payment.owner_id) == (20, 30, 50)
    assert payment.order_code == "DH100" and payment.customer_name == "Khách"
    assert {row.id for row in (await world.session.scalars(select(CrmBranchRecord))).all()} == {
        1,
        9,
    }
    staff = {row.id: row for row in (await world.session.scalars(select(CrmStaffRecord))).all()}
    assert set(staff[10].roles) == {"marketing"} and set(staff[50].roles) == {"owner"}
    assert staff[700].department_name == "Bác Sĩ" and "tour" in staff[800].roles
    assert len((await world.session.scalars(select(CrmOrderRecord))).all()) == 2
    assert len((await world.session.scalars(select(CrmShareRecord))).all()) == 2
    assert len((await world.session.scalars(select(CrmTourRecord))).all()) == 2

    # Again: the window's rows are replaced, not doubled; an order gone is gone.
    client.order_list = client.order_list[:1]
    await CrmSyncService(world.session, client).run(D1, D2, today=TODAY)
    assert len((await world.session.scalars(select(CrmOrderRecord))).all()) == 1
    assert len((await world.session.scalars(select(CrmShareRecord))).all()) == 2

    # One part refused: the others are kept, the run says FAILED.
    client.errors = {"tours": CrmError("CRM trả lỗi 500.")}
    client.order_list = crm().order_list
    failed = await CrmSyncService(world.session, client).run(D1, D2, today=TODAY)
    assert failed.status == "FAILED" and "công tua: lỗi (CRM trả lỗi 500.)" in failed.message
    assert len((await world.session.scalars(select(CrmOrderRecord))).all()) == 2
    assert len((await world.session.scalars(select(CrmTourRecord))).all()) == 2

    # The CRM refusing the key: nothing else is asked.
    broken = FakeCrmClient(error=CrmError("CRM từ chối key."))
    refused = await CrmSyncService(world.session, broken).run(D1, D2, today=TODAY)
    assert refused.status == "FAILED" and [c[0] for c in broken.calls] == ["users"]


async def test_03_the_5h_run_rereads_old_debts(world: World) -> None:
    old_paid = _order(7, date(2026, 6, 1), 1_000_000, 0, 400_000)
    old_owed = _order(8, date(2026, 6, 2), 2_000_000, 0, 600_000)
    client = crm()
    client.order_list = [*client.order_list, old_paid, old_owed]
    await CrmSyncService(world.session, client).run(
        date(2026, 6, 1), date(2026, 6, 30), today=TODAY
    )
    # Order 7 is paid off on the CRM: no longer in the unpaid list.
    client.order_list = [o for o in client.order_list if o.id != 7] + [
        _order(7, date(2026, 6, 1), 1_000_000, 0, 0)
    ]
    client.order_list = [o for o in client.order_list if o.id != 7 or o.the_rest > 0]
    report = await CrmSyncService(world.session, client).run(D1, D2, today=TODAY, debts=True)
    assert report.status == "SUCCESS"
    assert ("unpaid", date(2025, 10, 21), date(2026, 10, 19)) in client.calls
    paid = await world.session.get(CrmOrderRecord, 7)
    owed = await world.session.get(CrmOrderRecord, 8)
    assert paid is not None and paid.the_rest == 0
    assert owed is not None and owed.the_rest == Decimal(600_000)


async def _finance_people(world: World) -> tuple[User, User, User]:
    """A Kế toán head (all three), a Kế toán member (revenue + customers), a stranger."""
    session = world.session
    await ensure_default_departments(session)
    kt = Department(code="KT", name="Phòng Kế toán")
    session.add(kt)
    await session.flush()
    for position, permissions in (
        (Position.HEAD, ("REVENUE", "CUSTOMERS", "SETTINGS")),
        (Position.MEMBER, ("REVENUE", "CUSTOMERS")),
    ):
        for permission in permissions:
            session.add(
                DepartmentPermission(
                    department_id=kt.id,
                    position=position,
                    module="FINANCE",
                    permission=permission,
                    scope="ALL",
                )
            )
    people = []
    for name in ("Kế toán trưởng", "Kế toán viên", "Người ngoài"):
        user = User(full_name=name, role=Role.EMPLOYEE)
        session.add(user)
        people.append(user)
    await session.flush()
    await seat_person(session, kt.id, people[0].id, Position.HEAD)
    await seat_person(session, kt.id, people[1].id, Position.MEMBER)
    await session.flush()
    return people[0], people[1], people[2]


async def test_04_the_month_and_its_settings(world: World) -> None:
    head, member, stranger = await _finance_people(world)
    await CrmSyncService(world.session, crm()).run(D1, D2, today=TODAY)
    entity = LegalEntity(code="APEX", name="Apexmed")
    world.session.add(entity)
    await world.session.flush()

    world.act_as(stranger)
    assert world.client.get("/api/finance/access").json() == {
        "revenue": False,
        "customers": False,
        "settings": False,
        "fees": False,
        "codes": False,
    }
    assert world.client.get("/api/finance/revenue?month=2026-10").status_code == 403

    world.act_as(member)
    assert world.client.get("/api/finance/access").json()["settings"] is False
    refused = world.client.patch(
        "/api/finance/branches/1", json={"legal_entity_id": str(entity.id)}
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["reason"] == "finance_forbidden"

    world.act_as(head)
    mapped = world.client.patch(
        "/api/finance/branches/1", json={"legal_entity_id": str(entity.id), "apm_branch": "CS_HN"}
    )
    assert mapped.status_code == 200, mapped.json()
    assert mapped.json()["apm_branch"] == "CS_HN"
    bad = world.client.patch("/api/finance/branches/1", json={"apm_branch": "XX"})
    assert bad.json()["error"]["details"]["reason"] == "branch_invalid"
    # Only the keys sent change.
    kept = world.client.patch("/api/finance/branches/1", json={"apm_branch": "CS_HN"}).json()
    assert kept["legal_entity_id"] == str(entity.id)

    month = world.client.get("/api/finance/revenue?month=2026-10").json()
    figures = month["figures"]
    # 1,080,000 + 2,000,000 - 108,000; points left out.
    assert figures["collected"] == 2_972_000 and figures["payments"] == 3
    # VAT: 80,000 (order 8%) + 148,148 (default 8%) - 8,000 (refund, order 8%).
    assert figures["collected_vat"] == 220_148
    assert figures["collected_net"] == 2_972_000 - 220_148
    assert figures["refunds"] == -108_000
    assert (figures["sales"], figures["revenue"], figures["owed"]) == (
        4_320_000,
        4_000_000,
        1_000_000,
    )
    entities = {line["label"]: line["figures"]["collected"] for line in month["by_entity"]}
    assert entities == {"APEX - Apexmed": 972_000, "Chưa gắn": 2_000_000}
    assert {line["label"] for line in month["by_site"]} == {"CS - Hà Nội", "Chưa gắn"}
    assert [line["label"] for line in month["by_team"]] == ["Đội Hà Nội"]
    roles = {role["role"]: role["people"] for role in month["roles"]}
    assert (
        roles["telesales"][0]["name"] == "Sale B"
        and roles["telesales"][0]["collected"] == 2_972_000
    )
    assert [(s["name"], s["shared"]) for s in month["shares"]] == [
        ("BS An", 300_000),
        ("KTV Bình", 100_000),
    ]
    tours = {t["name"]: (t["primary"], t["support"]) for t in month["tours"]}
    assert tours == {"KTV Bình": (1, 0), "KTV Cường": (0, 1)}
    assert month["unmapped_branches"] == 1 and month["sync_status"] == "SUCCESS"
    # A past month is set against the whole month before; a running one
    # against the same days of it ("cùng kỳ").
    assert (month["previous_from"], month["previous_to"]) == ("2026-09-01", "2026-09-30")
    running = await FinanceRevenueService(world.session).month(date(2026, 10, 1), today=D2)
    assert (running.previous_from, running.previous_to) == (date(2026, 9, 1), date(2026, 9, 21))

    # A CRM person is mapped to an APM account by hand.
    linked = world.client.patch("/api/finance/staff/20", json={"user_id": str(member.id)})
    assert linked.status_code == 200 and linked.json()["user_name"] == "Kế toán viên"
    page = world.client.get("/api/finance/staff?unmapped=true").json()
    assert 20 not in {row["id"] for row in page["rows"]} and page["unmapped"] == page["total"]

    # VAT by pháp nhân: 10% for APEX moves the payments with no known order.
    saved = world.client.put(
        "/api/finance/vat", json={"vat_default": "8", "vat_by_entity": {str(entity.id): "0"}}
    )
    assert saved.status_code == 200, saved.json()
    wrong = world.client.put("/api/finance/vat", json={"vat_default": "120"})
    assert wrong.json()["error"]["details"]["reason"] == "vat_invalid"

    # "Đồng bộ ngay" queues the one shared job.
    sender = Sender()
    world.client.app.dependency_overrides[get_task_sender] = lambda: sender  # type: ignore[attr-defined]
    queued = world.client.post("/api/finance/sync", json={})
    assert queued.status_code == 202 and queued.json()["job_key"] == "ads.sync_crm_revenue"
    assert sender.sent[0][0] == "ads.sync_crm_revenue"
    world.act_as(member)
    assert world.client.post("/api/finance/sync", json={}).status_code == 403
    me = world.client.get("/api/units/me").json()
    assert {key: me["finance"][key] for key in ("revenue", "customers", "settings", "fees")} == {
        "revenue": True,
        "customers": True,
        "settings": False,
        "fees": False,
        "codes": False,
    }


async def test_05_a_module_in_development_is_the_owners_alone(world: World) -> None:
    """User 13/11: "Tính năng đang phát triển" - while FINANCE is listed, only
    the OWNER sees it: no menu, 403 on its API, left out of the permission
    screens - even for an ADMIN and the people granted it."""
    head, _member, _stranger = await _finance_people(world)
    admin = User(full_name="Quản trị viên", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()

    world.act_as(head)
    assert world.client.get("/api/permissions/developing-modules").status_code == 403
    assert (
        world.client.put("/api/permissions/developing-modules", json={"modules": []}).status_code
        == 403
    )

    world.act_as(world.owner)
    listed = world.client.get("/api/permissions/developing-modules").json()
    assert listed == {"modules": [{"module": "FINANCE", "label": "Tài chính", "developing": False}]}
    hidden = world.client.put("/api/permissions/developing-modules", json={"modules": ["FINANCE"]})
    assert hidden.status_code == 200 and hidden.json()["modules"][0]["developing"] is True
    bad = world.client.put("/api/permissions/developing-modules", json={"modules": ["ORDER"]})
    assert bad.status_code == 422
    # The OWNER keeps everything.
    assert world.client.get("/api/finance/access").json()["revenue"] is True
    assert world.client.get("/api/units/me").json()["finance"]["settings"] is True
    catalog = world.client.get("/api/departments/catalog").json()
    assert "FINANCE" in {entry["module"] for entry in catalog["modules"]}

    for user in (head, admin):
        world.act_as(user)
        assert world.client.get("/api/finance/access").json() == {
            "revenue": False,
            "customers": False,
            "settings": False,
            "fees": False,
            "codes": False,
        }
        assert world.client.get("/api/finance/revenue").status_code == 403
        flags = world.client.get("/api/units/me").json()["finance"]
        assert not any(flags[key] for key in ("revenue", "customers", "settings", "fees"))
        assert flags["requests"] is False
    # The ADMIN's permission screens leave it out.
    world.act_as(admin)
    catalog = world.client.get("/api/departments/catalog").json()
    assert "FINANCE" not in {entry["module"] for entry in catalog["modules"]}
    overview = world.client.get(f"/api/permissions/people/{head.id}").json()
    assert "FINANCE" not in {module["module"] for module in overview["modules"]}
    refused = world.client.put(
        f"/api/permissions/people/{head.id}/grants",
        json={"module": "FINANCE", "permissions": {"REVENUE": "ALL"}},
    )
    assert refused.status_code == 422

    # Released: the head's grants are back.
    world.act_as(world.owner)
    world.client.put("/api/permissions/developing-modules", json={"modules": []})
    world.act_as(head)
    assert world.client.get("/api/finance/access").json()["settings"] is True


async def test_06_the_ledger_fees_debts_kpi_and_the_workbook(world: World) -> None:
    """Đợt 2: the BCLT rows (an order's creation day + later payment days), the
    four fees (automatic, then typed), Tổng hợp, KPI, công nợ and its alert,
    the Excel export."""
    import io

    from openpyxl import load_workbook

    from meobot.application.finance.debts import FinanceDebtNotifier, FinanceDebtService
    from meobot.db.models.user_notification import UserNotification

    head, member, stranger = await _finance_people(world)
    client = crm()
    # Order 101 (D2) is also paid again by card a day later than created? No:
    # payment 2 has no order (TTCN row of its own); add a card payment on 101.
    client.rows = [*client.rows, _pay(5, D2, 3_240_000 - 1_000_000, 101, branch=9, kind=2)]
    await CrmSyncService(world.session, client).run(D1, D2, today=TODAY)
    # KT HEAD + MEMBER also hold FEES (0106 seeds it); the test world grants it here.
    kt = await world.session.scalar(select(Department).where(Department.code == "KT"))
    assert kt is not None
    for position in (Position.HEAD, Position.MEMBER):
        world.session.add(
            DepartmentPermission(
                department_id=kt.id,
                position=position,
                module="FINANCE",
                permission="FEES",
                scope="ALL",
            )
        )
    await world.session.flush()

    world.act_as(head)
    # Rules: 2% on card payments; a service costs 100.000 per unit.
    rules = world.client.put("/api/finance/fee-rules", json={"rates": {"2": "2"}})
    assert rules.status_code == 200, rules.json()
    assert world.client.put("/api/finance/fee-rules", json={"rates": {"7": "1"}}).status_code == 422

    ledger = world.client.get(
        "/api/finance/ledger", params={"since": D1.isoformat(), "until": D2.isoformat()}
    )
    assert ledger.status_code == 200, ledger.json()
    rows = ledger.json()["rows"]
    kinds = [(row["day"], row["kind"], row["order_code"]) for row in rows]
    assert ("2026-10-20", "NEW", "DH100") in kinds and ("2026-10-21", "NEW", "DH101") in kinds
    # Order 100 paid back on D2 (refund): a TTCN row; payment 2 (no order) too.
    assert ("2026-10-21", "TTCN", "DH100") in kinds
    assert sum(1 for kind in kinds if kind[1] == "TTCN") == 2
    new_101 = next(row for row in rows if row["order_code"] == "DH101")
    assert new_101["sales"] == 3_240_000 and new_101["owed"] == 1_000_000
    assert new_101["methods"] == {"2": 2_240_000}
    assert new_101["auto"]["card_fee"] == 44_800 and new_101["fees"]["card_fee"] == 44_800
    assert new_101["customer_name"] == "Khách 101"  # the head holds CUSTOMERS
    new_100 = next(row for row in rows if row["order_code"] == "DH100" and row["kind"] == "NEW")
    assert new_100["sellers"] == [] and new_100["doctors"]["Bác sĩ tiêm"]["name"] == "BS An"
    assert new_100["sale"] == "Sale B"

    # A typed fee wins; null goes back to automatic; FEES is needed.
    world.act_as(stranger)
    assert (
        world.client.patch(
            "/api/finance/fees", json={"order_id": 101, "day": D2.isoformat(), "cost": "500000"}
        ).status_code
        == 403
    )
    world.act_as(member)
    typed = world.client.patch(
        "/api/finance/fees",
        json={"order_id": 101, "day": D2.isoformat(), "cost": "500.000", "referral_fee": "100000"},
    )
    assert typed.status_code == 200, typed.json()
    row = typed.json()
    assert row["fees"]["cost"] == 500_000 and set(row["typed"]) == {"cost", "referral_fee"}
    assert row["fees"]["total"] == 44_800 + 500_000 + 100_000
    assert row["net"] == row["collected"] - row["vat"] - row["fees"]["total"]
    wrong = world.client.patch(
        "/api/finance/fees", json={"order_id": 101, "day": D1.isoformat(), "cost": "1"}
    )
    assert wrong.json()["error"]["details"]["reason"] == "fee_row_invalid"
    back = world.client.patch(
        "/api/finance/fees", json={"order_id": 101, "day": D2.isoformat(), "referral_fee": None}
    ).json()
    assert back["typed"] == ["cost"]

    # KPI + the month: fees and thực thu after VAT and fees.
    world.act_as(head)
    saved = world.client.put(
        "/api/finance/kpi",
        json={"month": "2026-10", "entries": {"total:": "31.000.000", "site:CS_HN": "10000000"}},
    )
    assert saved.status_code == 200, saved.json()
    assert saved.json()["values"] == {"total:": 31_000_000, "site:CS_HN": 10_000_000}
    assert (
        world.client.put(
            "/api/finance/kpi", json={"month": "2026-10", "entries": {"site:XX": "1"}}
        ).status_code
        == 422
    )
    world.client.patch("/api/finance/branches/1", json={"apm_branch": "CS_HN"})
    month = world.client.get("/api/finance/revenue?month=2026-10").json()
    assert month["kpi"] == 31_000_000 and month["fees"] == 44_800 + 500_000
    assert month["collected_after_fees"] == month["figures"]["collected_net"] - month["fees"]
    hn = next(line for line in month["by_site"] if line["key"] == "CS_HN")
    assert hn["kpi"] == 10_000_000
    days = world.client.get(
        "/api/finance/ledger", params={"since": D1.isoformat(), "until": D2.isoformat()}
    ).json()["days"]
    assert days[0]["kpi"] == 1_000_000  # 31.000.000 / 31 days

    # The workbook: BCLT + Tổng hợp.
    export = world.client.get("/api/finance/ledger.xlsx?month=2026-10")
    assert export.status_code == 200
    book = load_workbook(io.BytesIO(export.content))
    assert book.sheetnames == ["BCLT", "Tổng hợp"]
    assert book["BCLT"]["C1"].value == "Mã ĐH" and book["BCLT"].max_row == len(rows) + 1
    assert book["Tổng hợp"]["A2"].value == "Doanh số"

    # Công nợ: order 101 owes 1.000.000; 60 days later it passed 45 (default 30/45/60).
    report = await FinanceDebtService(world.session).report(today=date(2026, 12, 15))
    line = next(item for item in report.lines if item.code == "DH101")
    assert line.age == 55 and line.bucket == 45
    settings = world.client.put(
        "/api/finance/debts/settings",
        json={"milestones": [30, 45], "recipients": [str(head.id), str(world.owner.id)]},
    )
    assert settings.status_code == 200, settings.json()
    assert (
        world.client.put(
            "/api/finance/debts/settings", json={"milestones": [], "recipients": []}
        ).status_code
        == 422
    )
    listed = world.client.get("/api/finance/debts").json()
    assert listed["owed"] == 1_000_000 and listed["lines"][0]["code"] == "DH101"
    told = await FinanceDebtNotifier(world.session, world.settings).send(date(2026, 12, 15))
    assert told == 2
    # While FINANCE is "đang phát triển", only the OWNER among them is told.
    world.act_as(world.owner)
    world.client.put("/api/permissions/developing-modules", json={"modules": ["FINANCE"]})
    assert await FinanceDebtNotifier(world.session, world.settings).send(date(2026, 12, 16)) == 1
    inbox = (
        await world.session.scalars(
            select(UserNotification.body).where(
                UserNotification.event_type == "finance_debts_digest",
                UserNotification.recipient_user_id == head.id,
            )
        )
    ).all()
    assert len(inbox) == 1 and "quá 45 ngày: 1 đơn (1.000.000 ₫)" in inbox[0]
