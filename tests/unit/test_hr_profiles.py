"""HR đợt 2 (0096): hồ sơ nhân sự - who sees what, sealed fields, contracts,
documents, change requests, new employees, bulk edits, the export."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from meobot.application.ads.sync_service import vn_today
from meobot.application.org.departments import ensure_default_departments, seat_person
from meobot.core.secrets import build_secret_box, generate_key
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.department import Department, DepartmentPermission
from meobot.db.models.hr_profile import HrProfile
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from tests.unit.pr_world import World

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"


@pytest.fixture(autouse=True)
def _sealing(monkeypatch: pytest.MonkeyPatch) -> None:
    box = build_secret_box(
        type(
            "S",
            (),
            {
                "pr_secret_encryption_key": SecretStr(generate_key()),
                "pr_secret_encryption_key_id": "t",
            },
        )()
    )
    monkeypatch.setattr("meobot.application.hr.profiles.build_secret_box", lambda _settings: box)


async def _hr_world(world: World) -> dict[str, Any]:
    """HCNS (the lead sits there with every HR grant), a sales department the
    member heads, and two more people."""
    session = world.session
    await ensure_default_departments(session)
    hcns = Department(code="HCNS", name="Phòng HCNS", function=DepartmentFunction.NONE)
    sale = Department(code="TELE", name="Phòng Telesales", function=DepartmentFunction.NONE)
    other = Department(code="KT", name="Phòng Kế toán", function=DepartmentFunction.NONE)
    session.add_all([hcns, sale, other])
    await session.flush()
    for permission in ("VIEW", "EDIT", "SENSITIVE", "EXPORT"):
        session.add(
            DepartmentPermission(
                department_id=hcns.id,
                position=Position.MEMBER,
                module="HR",
                permission=permission,
                scope="ALL",
            )
        )
    session.add(
        DepartmentPermission(
            department_id=sale.id,
            position=Position.HEAD,
            module="HR",
            permission="VIEW",
            scope="OWN",
        )
    )
    seller = User(full_name="Phạm Thị Phương", role=Role.EMPLOYEE, member_code="D050PHUONG")
    accountant = User(full_name="Trịnh Thị Hằng", role=Role.EMPLOYEE, member_code="K001HANG")
    admin = User(full_name="Quản trị", role=Role.ADMIN, member_code="ADM01")
    session.add_all([seller, accountant, admin])
    await session.flush()
    for user in (world.lead, world.member):
        user.member_code = "HR01" if user is world.lead else "HEAD01"
    await seat_person(session, hcns.id, world.lead.id, Position.MEMBER)
    await seat_person(session, sale.id, world.member.id, Position.HEAD)
    await seat_person(session, sale.id, seller.id, Position.MEMBER)
    await seat_person(session, other.id, accountant.id, Position.MEMBER)
    await session.flush()
    return {"seller": seller, "accountant": accountant, "admin": admin, "sale": sale}


def as_(world: World, user: User) -> Any:
    world.act_as(user)
    return world.client


async def test_01_who_sees_whom(world: World) -> None:
    people = await _hr_world(world)
    seller, accountant = people["seller"], people["accountant"]
    hr = as_(world, world.lead)
    done = hr.patch(
        f"/api/hr/profiles/{seller.id}",
        json={"phone": "0912 345 678", "id_number": "001 300 020 738", "base_salary": 9_000_000},
    )
    assert done.status_code == 200, done.json()
    assert done.json()["personal"]["id_number"] == "001300020738"
    assert done.json()["pay"]["base_salary"] == 9_000_000

    # Sealed at rest; only the last four digits in the clear.
    stored = await world.session.get(HrProfile, seller.id)
    assert stored is not None and stored.id_last4 == "0738"
    assert stored.secret and "001300020738" not in stored.secret and "9000000" not in stored.secret

    # The head of Telesales sees their people's work information only.
    head = as_(world, world.member)
    listed = head.get("/api/hr/profiles").json()["items"]
    assert {row["member_code"] for row in listed} == {"HEAD01", "D050PHUONG"}
    seen = head.get(f"/api/hr/profiles/{seller.id}").json()
    assert seen["personal"] is None and seen["pay"] is None and seen["documents"] is None
    assert "phone" not in next(row for row in listed if row["member_code"] == "D050PHUONG")
    assert head.get(f"/api/hr/profiles/{accountant.id}").status_code == 404
    assert head.get("/api/hr/overview").status_code == 403

    # An employee: only themselves, all of it.
    me = as_(world, seller).get("/api/hr/profiles/me").json()
    assert me["personal"]["phone"] == "0912 345 678" and me["pay"]["base_salary"] == 9_000_000
    assert as_(world, seller).get("/api/hr/profiles").status_code == 403

    # An ADMIN sees every profile but no sensitive part unless granted (Q11).
    admin = as_(world, people["admin"]).get(f"/api/hr/profiles/{seller.id}").json()
    assert admin["work"] is not None and admin["personal"] is None

    # HCNS reading somebody else's sensitive part is audited.
    hr.get(f"/api/hr/profiles/{seller.id}")
    audited = (
        await world.session.scalars(
            select(AuditLog).where(AuditLog.action == "hr.profile.sensitive_viewed")
        )
    ).all()
    assert audited and all("001300020738" not in str(row.after_data) for row in audited)

    # The head cannot edit.
    refused = as_(world, world.member).patch(f"/api/hr/profiles/{seller.id}", json={"grade": "T1"})
    assert refused.status_code == 403


async def test_02_contracts_and_overview(world: World) -> None:
    people = await _hr_world(world)
    seller, accountant = people["seller"], people["accountant"]
    hr = as_(world, world.lead)
    start = vn_today() - timedelta(days=300)
    made = hr.post(
        f"/api/hr/profiles/{seller.id}/contracts",
        json={"type_code": "XD12", "number": "01/HĐLĐ", "start_on": start.isoformat()},
    )
    assert made.status_code == 201, made.json()
    profile = hr.get(f"/api/hr/profiles/{seller.id}").json()
    contract = profile["contracts"][0]
    # 12 months from the start, the day before.
    assert contract["end_on"] is not None and contract["state"] == "ACTIVE"
    assert 0 < contract["days_left"] < 70

    # Renew both: a new contract the day after the current one ends (or today).
    renewed = hr.post(
        "/api/hr/contracts/renew",
        json={"user_ids": [str(seller.id), str(accountant.id)], "type_code": "KXD"},
    )
    assert renewed.json() == {"created": 2}
    again = hr.get(f"/api/hr/profiles/{seller.id}").json()["contracts"]
    assert len(again) == 2 and again[0]["type_code"] == "KXD" and again[0]["end_on"] is None

    overview = hr.get("/api/hr/overview").json()
    assert overview["active"] >= 3 and overview["expiring_count"] >= 0
    assert "payroll" in overview


async def test_03_documents(world: World) -> None:
    people = await _hr_world(world)
    seller = people["seller"]
    hr = as_(world, world.lead)
    up = hr.post(
        f"/api/hr/profiles/{seller.id}/documents?kind=CCCD&name=cccd.pdf",
        content=PDF,
        headers={"content-type": "application/octet-stream"},
    )
    assert up.status_code == 201, up.json()
    bad = hr.post(
        f"/api/hr/profiles/{seller.id}/documents?kind=CCCD&name=x.exe",
        content=b"MZ\x90\x00not a pdf",
        headers={"content-type": "application/octet-stream"},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "hr_document_invalid"
    document = up.json()["id"]
    mine = as_(world, seller).get(f"/api/hr/documents/{document}")
    assert mine.status_code == 200 and mine.content == PDF
    assert mine.headers["x-content-type-options"] == "nosniff"
    assert as_(world, world.member).get(f"/api/hr/documents/{document}").status_code == 404
    profile = as_(world, seller).get("/api/hr/profiles/me").json()
    assert [item["kind"] for item in profile["documents"]] == ["CCCD"]
    assert "CCCD" not in profile["missing_documents"]


async def test_04_change_requests(world: World) -> None:
    people = await _hr_world(world)
    seller = people["seller"]
    asked = as_(world, seller).post(
        "/api/hr/requests", json={"field": "phone", "value": "0988 000 111"}
    )
    assert asked.status_code == 201
    assert (
        as_(world, seller)
        .post("/api/hr/requests", json={"field": "base_salary", "value": "1"})
        .status_code
        == 422
    )
    hr = as_(world, world.lead)
    pending = hr.get("/api/hr/requests?status=PENDING").json()["items"]
    assert [item["field"] for item in pending] == ["phone"]
    refused = hr.post(f"/api/hr/requests/{pending[0]['id']}/decide", json={"approve": False})
    assert refused.json()["error"]["code"] == "hr_request_note_missing"
    done = hr.post(f"/api/hr/requests/{pending[0]['id']}/decide", json={"approve": True})
    assert done.json()["status"] == "APPROVED"
    assert (
        as_(world, seller).get("/api/hr/profiles/me").json()["personal"]["phone"] == "0988 000 111"
    )
    # The seller cannot decide.
    assert as_(world, seller).get("/api/hr/requests").json()["can_decide"] is False


async def test_05_new_employee_bulk_and_export(world: World) -> None:
    people = await _hr_world(world)
    hr = as_(world, world.lead)
    made = hr.post(
        "/api/hr/profiles",
        json={
            "member_code": "Đ200MOI",
            "full_name": "Người Mới",
            "department_id": str(people["sale"].id),
            "job_title": "Chuyên viên Telesales",
            "joined_on": "2026-11-01",
        },
    )
    assert made.status_code == 201, made.json()
    body = made.json()
    assert body["member_code"] == "D200MOI" and len(body["password"]) >= 8
    login = world.client.post(
        "/api/auth/password-login", json={"username": "D200MOI", "password": body["password"]}
    )
    assert login.json() == {"must_change_password": True}
    taken = hr.post(
        "/api/hr/profiles",
        json={"member_code": "D200MOI", "full_name": "X", "department_id": str(people["sale"].id)},
    )
    assert taken.json()["error"]["code"] == "member_code_taken"

    bulk = hr.post(
        "/api/hr/profiles/bulk",
        json={
            "user_ids": [body["user_id"], str(people["seller"].id)],
            "fields": {"branch": "CS_SG", "employment_status": "MATERNITY"},
        },
    )
    assert bulk.json() == {"changed": 2}
    seller = hr.get(f"/api/hr/profiles/{people['seller'].id}").json()
    assert seller["branch_label"] == "CS - Sài Gòn"
    assert seller["employment_status_label"] == "Nghỉ thai sản"

    exported = hr.get("/api/hr/export.xlsx")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert as_(world, world.member).get("/api/hr/export.xlsx").status_code == 403

    menu = as_(world, world.lead).get("/api/units/me").json()["hr"]
    assert menu["lists"] and menu["edit"] and menu["sensitive"] and menu["export"]
    head_menu = as_(world, world.member).get("/api/units/me").json()["hr"]
    assert head_menu["lists"] and not head_menu["edit"] and not head_menu["overview"]


async def test_06_the_sheet_fills_profiles(world: World) -> None:
    from meobot.application.audit_service import AuditService
    from meobot.application.hr.profiles import HrProfileService
    from meobot.application.hr.sheet_import import HrProfileImport

    people = await _hr_world(world)
    seller = people["seller"]
    table = [
        [],
        [
            "Chi nhánh",
            "Mã \nnhân viên",
            "Họ và tên",
            "Giới tính",
            "Ngày \nsinh",
            "Khối",
            "MÃ phòng ban",
            "Phòng ban",
            "Chức danh nội bộ",
            "ĐT di động",
            "Số CCCD",
            "Ngày cấp",
            "Nơi cấp",
            "Email cá nhân",
            "Email nội bộ",
            "Email drive",
            "Ngày tham gia",
            "Ngày chính thức",
            "Pháp nhân đóng BHXH",
            "Cấp bậc",
            "Ngày ký HĐTV",
            "Số HĐLĐ thử việc",
            "HĐTV",
            "Ngày ký HĐCT",
            "Số HĐLĐ chính thức",
            "HĐCT",
        ],
        [
            "VP - Hà Nội",
            "D050PHUONG",
            "Phạm Thị Phương",
            "Nữ",
            "30/12/2001",
            "KD",
            "TELE",
            "Phòng Telesales",
            "Trưởng nhóm",
            "376618533",
            "038301009918",
            "16/12/2021",
            "Cục CS",
            "a@gmail.com",
            "phuong@apmtech.vn",
            "",
            "03/11/2022",
            "03/12/2022",
            "APEX",
            "T3",
            "",
            "02/2026/HĐTV-GPITECH",
            "TRUE",
            "03/09/2025",
            "0309/2025/HĐLĐ-APEXMED",
            "FALSE",
        ],
        ["", "NOBODY", "x", "Nam", "05/04/199x"],
    ]
    service = HrProfileService(world.session, world.settings, AuditService(world.session))
    report = await HrProfileImport(world.session, service).run(table)
    summary = report.summary()
    assert summary["profiles_updated"] == 1 and summary["contracts_created"] == 2
    assert summary["codes_not_found"] == ["NOBODY"] and summary["unreadable_dates"] == 0
    profile = as_(world, world.lead).get(f"/api/hr/profiles/{seller.id}").json()
    assert profile["personal"]["phone"] == "0376618533"
    assert profile["personal"]["id_number"] == "038301009918"
    assert profile["personal"]["birth_date"] == "2001-12-30"
    assert profile["work"]["joined_on"] == "2022-11-03" and profile["grade"] == "T3"
    types = sorted(item["type_code"] for item in profile["contracts"])
    assert types == ["HDLD", "TV"]
    # Again: nothing doubled.
    again = await HrProfileImport(world.session, service).run(table)
    assert again.contracts == 0


async def test_07_the_hcns_workbook(world: World) -> None:
    """0097: HCNS's own workbook (typed cells, the doubled bank columns, the
    paper checklist) and the paper checklist edited on the profile."""
    from datetime import UTC, date, datetime

    from meobot.application.audit_service import AuditService
    from meobot.application.hr.profiles import HrProfileService
    from meobot.application.hr.sheet_import import HrProfileImport, cell_text
    from meobot.db.models.legal_entity import LegalEntity

    people = await _hr_world(world)
    seller = people["seller"]
    if await world.session.scalar(select(LegalEntity).where(LegalEntity.code == "GPI")) is None:
        world.session.add(LegalEntity(code="GPI", name="GPI Tech"))
        await world.session.flush()
    header = [
        "Chi nhánh", "Mã \nnhân viên", "Họ và tên", "Giới tính", "Ngày \nsinh",
        "ĐT di động", "Số CCCD", "Team", "Ngày tham gia", "Vân tay", "Ngày lấy vân tay",
        "Tầng lấy vân tay", "Ngân hàng", "STK", "Tên tài khoản", "STK", "Ngân hàng",
        "Tên tài khoản", "Hồ sơ scan", "Sơ yếu lý lịch", "CCCD", "Giấy khai sinh",
        "Bằng cấp", "Ghi chú các trường hợp phát sinh", "Pháp nhân đóng BHXH",
        "Chức danh trên BHXH", "Mức lương cơ bản đóng BHXH", "Mã số BHXH",
    ]  # fmt: skip
    row: list[object] = [
        "VP - Hà Nội", "D050PHUONG", "Phạm Thị Phương", "Nữ", datetime(2001, 12, 30, tzinfo=UTC),
        376618533.0, 38301009918.0, "Team 1", datetime(2022, 11, 3, tzinfo=UTC), 112.0,
        datetime(2022, 11, 4, tzinfo=UTC), "T5", "Techcombank", "19034567891234", "PHAM THI PHUONG",
        "0011223344", "VCB", "PHAM THI PHUONG", "https://drive.google.com/x", True, True,
        False, True, "Đã nộp đủ", "GPI Tech", "Nhân viên kinh doanh", 5310000.0,
        "0123456789",
    ]  # fmt: skip
    table = [[], [cell_text(v) for v in header], [cell_text(v) for v in row]]
    assert cell_text(True) == "TRUE" and cell_text(date(2026, 1, 2)) == "02/01/2026"
    service = HrProfileService(world.session, world.settings, AuditService(world.session))
    report = await HrProfileImport(world.session, service).run(table)
    summary = report.summary()
    assert summary["profiles_updated"] == 1 and summary["companies_filled"] == 1
    assert summary["filled"]["paper:CV"] == 1 and "paper:BIRTH" not in summary["filled"]
    hr = as_(world, world.lead)
    profile = hr.get(f"/api/hr/profiles/{seller.id}").json()
    assert profile["personal"]["id_number"] == "038301009918"
    assert profile["personal"]["phone"] == "0376618533"
    assert profile["personal"]["bank_account"] == "19034567891234"
    assert profile["personal"]["bank_account_2"] == "0011223344"
    assert profile["personal"]["bank2_name"] == "VCB"
    assert profile["work"]["team"] == "Team 1"
    assert profile["work"]["fingerprint_id"] == "112"
    assert profile["work"]["fingerprint_on"] == "2022-11-04"
    assert profile["insurance"]["insurance_title"] == "Nhân viên kinh doanh"
    assert profile["insurance"]["insurance_salary"] == 5310000
    assert profile["insurance"]["social_insurance_no"] == "0123456789"
    assert profile["legal_entity_code"] == "GPI"
    assert "Pháp nhân đóng BHXH: GPI Tech" in profile["work"]["note"]
    papers = profile["papers"]
    assert papers["scan_link"] == "https://drive.google.com/x"
    handed = [item["code"] for item in papers["items"] if item["handed"]]
    assert handed == ["CV", "CCCD", "DEGREE"]
    # Required by default: CV, CCCD, BIRTH, RESIDENCE, HEALTH, DEGREE.
    assert profile["missing_documents"] == ["BIRTH", "RESIDENCE", "HEALTH"]
    stored = await world.session.get(HrProfile, seller.id)
    assert stored is not None and stored.bank_last4 == "1234" and stored.bank2_last4 == "3344"
    assert "19034567891234" not in (stored.secret or "")

    # HCNS ticks the birth certificate on the profile.
    done = hr.patch(
        f"/api/hr/profiles/{seller.id}",
        json={"paper_documents": ["BIRTH", "CV", "CCCD", "DEGREE"]},
    )
    assert done.status_code in (200, 204), done.json()
    after = hr.get(f"/api/hr/profiles/{seller.id}").json()
    assert after["missing_documents"] == ["RESIDENCE", "HEALTH"]
    bad = hr.patch(f"/api/hr/profiles/{seller.id}", json={"paper_documents": ["NOPE"]})
    assert bad.status_code == 422
    # A head sees no papers.
    head = as_(world, world.member).get(f"/api/hr/profiles/{seller.id}").json()
    assert head["papers"] is None


async def test_08_the_filter_lists_every_department(world: World) -> None:
    """2026-11-10: "Phòng ban" on Quản lý hồ sơ lists every active department,
    whatever the reader manages on the Phòng ban page (HCNS staff are no
    department's head there)."""
    await _hr_world(world)
    names = {
        item["code"]
        for item in as_(world, world.lead).get("/api/hr/access").json()["choices"]["departments"]
    }
    assert {"HCNS", "TELE", "KT"} <= names
