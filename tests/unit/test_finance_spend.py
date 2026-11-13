"""Tài chính đợt 3 (0107): mã chi + tuyến duyệt (Sổ tay), vai trò tài chính, hồ
sơ chi (duyệt mua sắm -> mua sắm & nghiệm thu -> duyệt thanh toán -> chi
tiền), chứng từ, ngân sách, MISA, thu - chi, nhắc việc."""

from __future__ import annotations

import io
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from meobot.application.finance.reminders import send_reminders
from meobot.application.org.departments import ensure_default_departments, seat_person
from meobot.db.models.department import Department, DepartmentPermission
from meobot.db.models.finance_spend import FinanceCode, FinanceRequest
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.finance.spend import FinanceRole, escalate, parse_codebook
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from tests.unit.pr_world import World

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_01_the_codebook_and_escalation() -> None:
    seeds = {seed.code: seed for seed in parse_codebook()}
    stock = seeds["CO.03.01"].route
    assert stock.purchase == (FinanceRole.DEPT_HEAD, FinanceRole.BLOCK_DIRECTOR, FinanceRole.CFO)
    assert stock.executor is FinanceRole.ACC_WAREHOUSE
    assert stock.payment[-2:] == (FinanceRole.ACC_WAREHOUSE, FinanceRole.CHIEF_ACCOUNTANT)
    assert stock.cash == (FinanceRole.CASHIER_VP,)
    bank = seeds["CO.06.06.02"].route
    assert bank.no_approval and bank.cash == (FinanceRole.CASHIER_VP,)
    assert seeds["CO.05.01"].route.empty and seeds["CO.06.03.08"].route.empty
    assert not seeds["CO.06"].leaf
    # F6: the next level not already further on in the phase.
    assert escalate(FinanceRole.CFO, []) is FinanceRole.CHIEF_ACCOUNTANT
    assert escalate(FinanceRole.DEPT_HEAD, [FinanceRole.BLOCK_DIRECTOR]) is FinanceRole.CFO
    assert escalate(FinanceRole.ACC_MKT, [FinanceRole.CHIEF_ACCOUNTANT]) is FinanceRole.CEO
    assert escalate(FinanceRole.CEO, []) is None


async def _company(world: World) -> dict[str, Any]:
    session = world.session
    await ensure_default_departments(session)
    for index, seed in enumerate(parse_codebook()):
        session.add(
            FinanceCode(
                code=seed.code,
                name=seed.name,
                parent_code=seed.parent,
                kind=seed.kind,
                leaf=seed.leaf,
                sort_order=index,
                route=seed.route.as_value(),
            )
        )
    bod = Department(code="BOD", name="Ban giám đốc", function=DepartmentFunction.BOARD)
    office = Department(code="VPKVH", name="Văn phòng khối vận hành", block="Khối vận hành")
    care = Department(code="QHKH", name="Phòng QHKH", block="Khối vận hành")
    kt = Department(code="KT", name="Phòng Kế toán")
    entity = LegalEntity(code="APEXMED", name="Apexmed")
    session.add_all([bod, office, care, kt, entity])
    await session.flush()
    for permission in ("VIEW_REQUESTS", "CODES", "BUDGET", "MISA", "REPORT"):
        session.add(
            DepartmentPermission(
                department_id=kt.id,
                position=Position.HEAD,
                module="FINANCE",
                permission=permission,
                scope="ALL",
            )
        )
    people: dict[str, User] = {}
    for key in (
        "director",
        "director2",
        "block",
        "head",
        "staff",
        "stranger",
        "cfo",
        "chief",
        "stock",
        "cashier",
    ):
        user = User(full_name=key.capitalize(), role=Role.EMPLOYEE)
        session.add(user)
        people[key] = user
    await session.flush()
    await seat_person(session, bod.id, people["director"].id, Position.HEAD)
    await seat_person(session, bod.id, people["director2"].id, Position.HEAD)
    await seat_person(session, office.id, people["block"].id, Position.HEAD)
    await seat_person(session, care.id, people["head"].id, Position.HEAD)
    await seat_person(session, care.id, people["staff"].id, Position.MEMBER)
    await seat_person(session, kt.id, people["chief"].id, Position.HEAD)
    await seat_person(session, kt.id, people["stock"].id, Position.MEMBER)
    await seat_person(session, kt.id, people["cashier"].id, Position.MEMBER)
    await seat_person(session, kt.id, people["cfo"].id, Position.MEMBER)
    await session.flush()
    world.act_as(people["chief"])
    for role, key in (
        ("CFO", "cfo"),
        ("CHIEF_ACCOUNTANT", "chief"),
        ("ACC_WAREHOUSE", "stock"),
        ("CASHIER_VP", "cashier"),
    ):
        added = world.client.post(
            "/api/finance/roles", json={"role": role, "user_id": str(people[key].id)}
        )
        assert added.status_code == 201, added.json()
    return {"people": people, "entity": entity, "care": care, "kt": kt}


def _act(world: World, user: User, request_id: str, action: str, **extra: Any) -> Any:
    world.act_as(user)
    return world.client.post(
        f"/api/finance/requests/{request_id}/actions", json={"action": action, **extra}
    )


async def test_02_a_request_goes_all_the_way(world: World) -> None:
    company = await _company(world)
    people, entity, care = company["people"], company["entity"], company["care"]
    world.act_as(people["chief"])
    # An org role cannot be assigned by hand.
    refused = world.client.post(
        "/api/finance/roles", json={"role": "DEPT_HEAD", "user_id": str(people["head"].id)}
    )
    assert refused.json()["error"]["details"]["reason"] == "role_from_org_chart"
    budget = world.client.put(
        "/api/finance/budgets/cell",
        json={
            "month": "2026-11",
            "legal_entity_id": str(entity.id),
            "department_id": str(care.id),
            "code": "CO.03.01",
            "amount": "10.000.000",
        },
    )
    assert budget.status_code == 204, budget.json()

    world.act_as(people["staff"])
    no_route = world.client.post(
        "/api/finance/requests",
        json={
            "legal_entity_id": str(entity.id),
            "department_id": str(care.id),
            "budget_code": "CO.05.01",
            "title": "Thuê phòng mổ",
            "amount": "1000000",
            "budget_month": "2026-11",
        },
    )
    assert no_route.json()["error"]["details"]["reason"] == "finance_code_no_route"
    sent = world.client.post(
        "/api/finance/requests",
        json={
            "legal_entity_id": str(entity.id),
            "department_id": str(care.id),
            "budget_code": "CO.03.01",
            "title": "Vật tư tiêu hao tháng 11",
            "amount": "4.000.000",
            "budget_month": "2026-11",
        },
    )
    assert sent.status_code == 201, sent.json()
    rid = sent.json()["id"]
    assert sent.json()["code"].startswith("DC-")
    detail = world.client.get(f"/api/finance/requests/{rid}").json()
    phases = [(step["phase"], step["role"]) for step in detail["steps"]]
    assert phases == [
        ("PURCHASE", "DEPT_HEAD"),
        ("PURCHASE", "BLOCK_DIRECTOR"),
        ("PURCHASE", "CFO"),
        ("EXECUTE", "ACC_WAREHOUSE"),
        ("PAYMENT", "DEPT_HEAD"),
        ("PAYMENT", "BLOCK_DIRECTOR"),
        ("PAYMENT", "ACC_WAREHOUSE"),
        ("PAYMENT", "CHIEF_ACCOUNTANT"),
        ("CASH", "CASHIER_VP"),
    ]
    assert detail["steps"][0]["approver"] == "Head" and detail["phase"] == "PURCHASE"
    assert detail["budget"]["committed"] == 4_000_000 and detail["budget"]["remaining"] == 6_000_000
    assert {item["value"] for item in detail["missing"]} >= {"INVOICE", "CONTRACT"}
    told = await world.session.scalar(
        select(UserNotification.event_type).where(
            UserNotification.recipient_user_id == people["head"].id
        )
    )
    assert told == "finance_request_awaiting"

    # A later approver does not see it before it reaches them; nor a stranger.
    assert _act(world, people["block"], rid, "approve").status_code == 404
    world.act_as(people["stranger"])
    assert world.client.get(f"/api/finance/requests/{rid}").status_code == 404
    for key in ("head", "block", "cfo"):
        moved = _act(world, people[key], rid, "approve")
        assert moved.status_code == 200, moved.json()
    executed = _act(world, people["stock"], rid, "execute")
    assert executed.json()["error"]["details"]["reason"] == "purchase_note_required"
    executed = _act(
        world, people["stock"], rid, "execute", purchase_note="Báo giá NCC A, đã nghiệm thu 12/11"
    )
    assert executed.status_code == 200 and executed.json()["realized_at"] is not None
    for key in ("head", "block", "stock", "chief"):
        assert _act(world, people[key], rid, "approve").status_code == 200
    no_proof = _act(world, people["cashier"], rid, "pay")
    assert no_proof.json()["error"]["details"]["reason"] == "payment_proof_required"
    world.act_as(people["cashier"])
    uploaded = world.client.post(
        f"/api/finance/requests/{rid}/documents",
        params={"kind": "PAYMENT_PROOF", "name": "unc.png"},
        content=PNG,
    )
    assert uploaded.status_code == 201, uploaded.json()
    assert uploaded.json()["storage"] == "db"
    bad = world.client.post(
        f"/api/finance/requests/{rid}/documents",
        params={"kind": "INVOICE", "name": "x.exe"},
        content=b"MZ\x90\x00",
    )
    assert bad.json()["error"]["details"]["reason"] == "file_invalid"
    paid = _act(world, people["cashier"], rid, "pay")
    assert paid.status_code == 200, paid.json()
    assert paid.json()["status"] == "PAID" and paid.json()["docs_due_on"] is not None
    # The papers: the chief ticks "đã nhận bản giấy".
    world.act_as(people["chief"])
    assert (
        world.client.post(
            f"/api/finance/requests/{rid}/papers", json={"kind": "INVOICE"}
        ).status_code
        == 201
    )
    final = world.client.get(f"/api/finance/requests/{rid}").json()
    assert "INVOICE" not in {item["value"] for item in final["missing"]}
    assert final["budget"]["realized"] == 4_000_000
    proof = next(item for item in final["documents"] if item["kind"] == "PAYMENT_PROOF")
    file = world.client.get(f"/api/finance/documents/{proof['id']}")
    assert file.status_code == 200 and file.content == PNG
    world.act_as(people["staff"])
    updates = (
        await world.session.scalars(
            select(UserNotification.title).where(
                UserNotification.recipient_user_id == people["staff"].id
            )
        )
    ).all()
    assert "Hồ sơ chi đã được chi tiền" in updates


async def test_03_escalation_return_reject_bank_fee(world: World) -> None:
    company = await _company(world)
    people, entity, care, kt = (
        company["people"],
        company["entity"],
        company["care"],
        company["kt"],
    )
    body = {
        "legal_entity_id": str(entity.id),
        "department_id": str(kt.id),
        "budget_code": "CO.03.01",
        "title": "Vật tư",
        "amount": "2000000",
        "budget_month": "2026-11",
    }
    # F6: the CFO files one; its CFO step goes up to the Kế toán trưởng.
    world.act_as(people["cfo"])
    sent = world.client.post("/api/finance/requests", json=body).json()
    # The KT department has no GĐ khối: that level is skipped.
    detail = _act(world, people["chief"], sent["id"], "approve").json()
    block_step = next(step for step in detail["steps"] if step["role"] == "BLOCK_DIRECTOR")
    assert block_step["status"] == "SKIPPED"
    cfo_step = next(step for step in detail["steps"] if step["role"] == "CFO")
    assert cfo_step["approver"] == "Chief" and "Kế toán trưởng" in cfo_step["remark"]

    # Returned, edited, sent again: a new round.
    world.act_as(people["staff"])
    sent = world.client.post(
        "/api/finance/requests", json={**body, "department_id": str(care.id)}
    ).json()
    rid = sent["id"]
    no_note = _act(world, people["head"], rid, "return")
    assert no_note.json()["error"]["details"]["reason"] == "note_required"
    returned = _act(world, people["head"], rid, "return", note="Thiếu báo giá")
    assert returned.json()["status"] == "RETURNED"
    world.act_as(people["staff"])
    edited = world.client.patch(f"/api/finance/requests/{rid}", json={"amount": "2500000"})
    assert edited.status_code == 200, edited.json()
    again = _act(world, people["staff"], rid, "resubmit")
    assert again.json()["status"] == "PENDING" and again.json()["amount"] == 2_500_000
    assert {step["round"] for step in again.json()["steps"]} == {1, 2}
    rejected = _act(world, people["head"], rid, "reject", note="Không cần")
    assert rejected.json()["status"] == "REJECTED"
    assert _act(world, people["head"], rid, "approve").status_code == 409

    # "Phí ngân hàng": no approval, only Thủ quỹ VP records it.
    world.act_as(people["chief"])
    fee = world.client.post(
        "/api/finance/requests", json={**body, "budget_code": "CO.06.06.02", "title": "Phí NH"}
    ).json()
    steps = world.client.get(f"/api/finance/requests/{fee['id']}").json()["steps"]
    assert [(step["phase"], step["role"]) for step in steps] == [("CASH", "CASHIER_VP")]

    # Lists: mine / awaiting / all (VIEW_REQUESTS).
    world.act_as(people["cashier"])
    awaiting = world.client.get("/api/finance/requests?tab=awaiting").json()
    assert awaiting["awaiting"] == 1 and awaiting["rows"][0]["code"] == fee["code"]
    world.act_as(people["chief"])
    every = world.client.get("/api/finance/requests?tab=all").json()["rows"]
    assert len(every) == 3

    # The 8:00 reminder reaches who something waits on.
    counts = await send_reminders(world.session, world.settings, date(2026, 11, 14))
    assert counts["approvers"] >= 1
    reminded = await world.session.scalar(
        select(UserNotification.body).where(
            UserNotification.recipient_user_id == people["cashier"].id,
            UserNotification.event_type == "finance_request_reminder",
        )
    )
    assert reminded is not None and reminded.startswith("Có 1 hồ sơ chi chờ bạn")


async def test_04_codes_budgets_misa_and_the_report(world: World) -> None:
    company = await _company(world)
    people, entity, care = company["people"], company["entity"], company["care"]
    world.act_as(people["staff"])
    assert (
        world.client.patch(
            "/api/finance/codes/CO.05.01", json={"route": {"payment": ["CHIEF_ACCOUNTANT"]}}
        ).status_code
        == 403
    )
    world.act_as(people["chief"])
    changed = world.client.patch(
        "/api/finance/codes/CO.05.01",
        json={"route": {"payment": ["DEPT_HEAD", "CHIEF_ACCOUNTANT"], "cash": ["CASHIER_VP"]}},
    )
    assert changed.status_code == 200, changed.json()
    assert changed.json()["no_route"] is False
    history = world.client.get("/api/finance/codes/CO.05.01/history").json()
    assert history[0]["by"] == "Chief" and history[0]["after"]["route"]["payment"][0] == "DEPT_HEAD"
    assert (
        world.client.patch(
            "/api/finance/codes/CO.05.01", json={"route": {"payment": ["CASHIER_VP"]}}
        ).json()["error"]["details"]["reason"]
        == "route_invalid"
    )
    threshold = world.client.put(
        "/api/finance/spend-settings", json={"ceo_threshold": "50.000.000"}
    )
    assert threshold.json()["ceo_threshold"] == 50_000_000

    # Budget workbook: template, then an import with one bad line refused whole.
    template = world.client.get("/api/finance/budgets/template.xlsx?month=2026-11")
    assert template.status_code == 200
    book = load_workbook(io.BytesIO(template.content))
    assert book.sheetnames[:2] == ["Ngân sách", "Pháp nhân"]
    upload = Workbook()
    sheet = upload.active
    assert sheet is not None
    sheet.title = "Ngân sách"
    sheet.append(["Pháp nhân (mã)", "Mã phòng ban", "Mã ngân sách", "Tháng (MM/YYYY)", "Số tiền"])
    sheet.append(["APEXMED", "QHKH", "CO.06.02.01", "11/2026", 3_000_000])
    sheet.append(["APEXMED", "XX", "CO.06.02.01", "11/2026", 1])
    data = io.BytesIO()
    upload.save(data)
    refused = world.client.post("/api/finance/budgets/import", content=data.getvalue())
    assert refused.json()["error"]["details"]["reason"] == "budget_import_invalid"
    assert "XX" in refused.json()["error"]["details"]["problems"][0]
    sheet.delete_rows(3)
    data = io.BytesIO()
    upload.save(data)
    imported = world.client.post("/api/finance/budgets/import", content=data.getvalue())
    assert imported.status_code == 200 and imported.json() == {"lines": 1}
    lines = world.client.get("/api/finance/budgets?month=2026-11").json()["lines"]
    assert lines[0]["code"] == "CO.06.02.01" and lines[0]["budget"] == 3_000_000

    # MISA F01-DNN: the file's month must match; totals set against APM.
    f01 = Workbook()
    sheet = f01.active
    assert sheet is not None
    sheet.append(["F01-DNN: BẢNG CÂN ĐỐI TÀI KHOẢN"])
    sheet.append(["Tháng 11 năm 2026"])
    sheet.append(["Số hiệu tài khoản", "Tên tài khoản", "Số dư đầu kỳ", None, "Phát sinh"])
    sheet.append([None, None, "Nợ", "Có", "Nợ", "Có", "Nợ", "Có"])
    sheet.append(["511", "Doanh thu", 0, 0, 1000, 1000, 0, 0])
    sheet.append(["5114", "Giảm giá hàng bán", 0, 0, 100, 100, 0, 0])
    sheet.append(["642", "Chi phí quản lý", 0, 0, 700, 700, 0, 0])
    sheet.append(["", "Cộng", 0, 0, 1800, 1800, 0, 0])
    data = io.BytesIO()
    f01.save(data)
    wrong = world.client.post(
        "/api/finance/misa/import",
        params={"legal_entity_id": str(entity.id), "month": "2026-10"},
        content=data.getvalue(),
    )
    assert wrong.json()["error"]["details"]["reason"] == "misa_month_mismatch"
    done = world.client.post(
        "/api/finance/misa/import",
        params={"legal_entity_id": str(entity.id), "month": "2026-11"},
        content=data.getvalue(),
    )
    assert done.status_code == 200 and done.json() == {"lines": 3}
    view = world.client.get(
        "/api/finance/misa", params={"legal_entity_id": str(entity.id), "month": "2026-11"}
    ).json()
    assert view["misa_revenue"] == 900 and view["misa_cost"] == 700
    assert [line["account"] for line in view["lines"]] == ["511", "642"]

    report = world.client.get("/api/finance/pnl?month=2026-11")
    assert report.status_code == 200, report.json()
    assert report.json()["total"]["budget"] == 3_000_000
    world.act_as(people["staff"])
    assert world.client.get("/api/finance/pnl?month=2026-11").status_code == 403
    me = world.client.get("/api/units/me").json()["finance"]
    assert me["requests"] is True and me["report"] is False
    _ = care, Decimal, uuid, pytest, FinanceRequest
