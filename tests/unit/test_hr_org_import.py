"""HR đợt 1 (0095, ``docs/hr/HR_MODULE_PLAN.md``): org chart + staff load,
member-code login, work info, legal entities."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from meobot.application.account.passwords import hash_password
from meobot.application.org.departments import ensure_default_departments, seat_person
from meobot.application.org.hr_import import HrOrgImport, StaffRow, staff_rows
from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from tests.unit.pr_world import World

PASSWORD = "Mk-rieng-123"


def login(world: World, username: str, password: str) -> Any:
    return world.client.post(
        "/api/auth/password-login", json={"username": username, "password": password}
    )


async def test_01_member_code_signs_in_with_own_password_only(world: World) -> None:
    person = User(
        full_name="Trần Thị Thùy Duyên",
        role=Role.EMPLOYEE,
        member_code="D019DUYEN",
        password_hash=hash_password(PASSWORD),
    )
    untouched = User(full_name="Chưa đặt mật khẩu", role=Role.EMPLOYEE, member_code="D020HONG")
    world.session.add_all([person, untouched])
    await world.session.flush()

    # "Đ" folds to "D", case does not matter.
    for username in ("D019DUYEN", "Đ019DUYEN", " d019duyen "):
        answer = login(world, username, PASSWORD)
        assert answer.status_code == 200, answer.json()
    assert login(world, "D019DUYEN", "sai").status_code == 401
    # The default password never opens an account by its code.
    default = world.settings.web_default_password.get_secret_value()
    refused = login(world, "D020HONG", default)
    assert refused.status_code == 401
    assert refused.json()["error"]["message"].startswith("Sai mã nhân viên")


async def test_02_work_info_by_admins_only(world: World) -> None:
    gpi = LegalEntity(code="GPI", name="Công ty TNHH GPI Tech")
    world.session.add(gpi)
    await world.session.flush()
    owner = world.client
    world.act_as(world.owner)
    path = f"/api/account/members/{world.member.id}/work-info"
    done = owner.patch(
        path,
        json={
            "job_title": "  Chuyên viên  Telesales ",
            "branch": "CS_SG",
            "legal_entity_id": str(gpi.id),
            "telegram_user_id": "777001",
        },
    )
    assert done.status_code == 200, done.json()
    assert done.json() == {
        "job_title": "Chuyên viên Telesales",
        "branch": "CS_SG",
        "branch_label": "CS - Sài Gòn",
        "legal_entity_id": str(gpi.id),
        "telegram_user_id": 777001,
    }
    # Only the keys sent change; null clears.
    cleared = owner.patch(path, json={"telegram_user_id": None})
    assert cleared.json()["telegram_user_id"] is None
    assert cleared.json()["job_title"] == "Chuyên viên Telesales"
    # A Telegram id is one account's.
    assert world.lead.telegram_user_id is None
    world.lead.telegram_user_id = 888002
    await world.session.flush()
    taken = owner.patch(path, json={"telegram_user_id": "888002"})
    assert taken.status_code == 422 and taken.json()["error"]["code"] == "telegram_id_taken"
    for body, reason in (
        ({"telegram_user_id": "12ab"}, "telegram_id_invalid"),
        ({"branch": "HN9"}, "branch_invalid"),
        ({"legal_entity_id": "00000000-0000-0000-0000-000000000001"}, "legal_entity_invalid"),
    ):
        bad = owner.patch(path, json=body)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == reason
    # The row says it, and the job title is what they are.
    rows = owner.get("/api/account/members").json()["members"]
    row = next(item for item in rows if item["user_id"] == str(world.member.id))
    assert row["job_title"] == row["role_label"] == "Chuyên viên Telesales"
    assert row["branch_label"] == "CS - Sài Gòn" and row["legal_entity_code"] == "GPI"
    # Nobody else edits it.
    world.act_as(world.lead)
    refused = world.client.patch(path, json={"job_title": "x"})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "work_info_forbidden"


async def test_03_legal_entities_list_and_edit(world: World) -> None:
    world.act_as(world.owner)
    made = world.client.post(
        "/api/legal-entities", json={"code": "hkd ap", "name": "Hộ kinh doanh AP"}
    )
    assert made.status_code == 201, made.json()
    assert made.json()["code"] == "HKD-AP"
    again = world.client.post("/api/legal-entities", json={"code": "HKD-AP", "name": "x"})
    assert again.status_code == 422
    assert again.json()["error"]["code"] == "legal_entity_code_taken"
    off = world.client.patch(f"/api/legal-entities/{made.json()['id']}", json={"active": False})
    assert off.json()["active"] is False
    listed = world.client.get("/api/legal-entities").json()
    assert listed["can_edit"] is True and listed["entities"] == []
    assert {"code": "CS_HN2", "label": "CS - Hà Nội 2"} in listed["branches"]
    world.act_as(world.member)
    assert world.client.get("/api/legal-entities").json()["can_edit"] is False
    refused = world.client.post("/api/legal-entities", json={"code": "X", "name": "X"})
    assert refused.status_code == 403


def test_04_sheet_rows_read_only_the_needed_columns() -> None:
    table = [
        [],
        [
            "Chi nhánh",
            "Mã \nnhân viên",
            "Họ và tên",
            "Giới tính",
            "MÃ phòng ban",
            "Chức danh nội bộ",
            "Số CCCD",
            "Ngày chính thức",
            "Pháp nhân đóng BHXH",
        ],
        [
            *("CS - Sài Gòn", "Đ019DUYEN", "Trần Thị Thùy Duyên", "Nữ", "TDD"),
            *("Trưởng bộ phận Tiếp đón", "0123", "01/01/2024", "CMG PVC"),
        ],
        ["", "", "", "", "", "", "", "", ""],
    ]
    assert staff_rows(table) == [
        StaffRow(
            code="Đ019DUYEN",
            full_name="Trần Thị Thùy Duyên",
            department="TDD",
            job_title="Trưởng bộ phận Tiếp đón",
            branch="CS - Sài Gòn",
            entity="CMG PVC",
            official_on="01/01/2024",
        )
    ]


async def _department(world: World, code: str, *, active: bool = True) -> Department:
    row = Department(code=code, name=code, function=DepartmentFunction.NONE, active=active)
    world.session.add(row)
    await world.session.flush()
    return row


async def _person(world: World, name: str, code: str) -> User:
    user = User(full_name=name, role=Role.EMPLOYEE, member_code=code, telegram_user_id=None)
    world.session.add(user)
    await world.session.flush()
    return user


async def _open(world: World, user: User) -> dict[str, str]:
    rows = (
        await world.session.execute(
            select(Department.code, DepartmentMember.position)
            .join(Department, Department.id == DepartmentMember.department_id)
            .where(DepartmentMember.user_id == user.id, DepartmentMember.left_at.is_(None))
        )
    ).all()
    return {code: position.value for code, position in rows}


async def test_05_the_sheet_loads_departments_and_people(world: World) -> None:
    session = world.session
    defaults = await ensure_default_departments(session)
    for code in ("APEXMED", "GPI", "CMG", "APM"):
        session.add(LegalEntity(code=code, name=code))
    sale = await _department(world, "SALE")
    cs = await _department(world, "CS")
    await _department(world, "KHAC")
    await _department(world, "TCKT")
    await _department(world, "MEDIA", active=False)

    dung = await _person(world, "Dũng ADS", "M001DUNG")
    await seat_person(session, defaults["ADS"].id, dung.id, Position.HEAD)
    luong = await _person(world, "Lương", "E018LUONG")
    await seat_person(session, defaults["BT"].id, luong.id, Position.HEAD)
    long_ = await _person(world, "Long", "E051LONG")
    await seat_person(session, defaults["TK"].id, long_.id, Position.HEAD)
    nhung = await _person(world, "Nhung Trần", "T001NHUNG")
    await seat_person(session, cs.id, nhung.id, Position.HEAD)
    tiep = await _person(world, "Tiep Tran", "K050TIEP")
    await seat_person(session, defaults["BGD"].id, tiep.id, Position.MEMBER)
    phuong = await _person(world, "Phương", "D050PHUONG")
    await seat_person(session, sale.id, phuong.id, Position.MEMBER)

    def row(code: str, name: str, dept: str, title: str, branch: str = "VP - Hà Nội") -> StaffRow:
        return StaffRow(code, name, dept, title, branch, "GPI Tech", "01/01/2024")

    rows = [
        row("TEST", "Nguyễn Văn A", "BOD", "Giám đốc điều hành"),
        row("M001DUNG", "Nguyễn Xuân Dũng", "ADS", "Trưởng phòng Ads"),
        row("E018LUONG", "Nguyễn Đoàn Hiền Lương", "MEDIA (BT)", "Trưởng nhóm Biên tập"),
        row("E051LONG", "Mai Tùng Long", "MEDIA", "Trưởng phòng Media"),
        row("T001NHUNG", "Trần Thị Quỳnh Nhung", "TVV", "Quản lý kinh doanh", "CS - Hà Nội"),
        row("K050TIEP", "Trần Đức Tiệp", "VPTCKT", "Giám đốc khối tài chính kế toán"),
        row("D050PHUONG", "Phạm Thị Phương", "TELE", "Trưởng nhóm Telesales"),
        row("Đ075QUYET", "Cao Văn Quyết", "TDD", "Trưởng bộ phận Tiếp đón", "CS - Hà Nội"),
        row("Đ002TRANG", "Phạm Thu Trang", "TDD", "Chuyên viên Lễ tân", "CS - Hà Nội"),
        row("Đ020HONG", "Phạm Thị Bích Hồng", "TDD", "Chuyên viên Lễ tân", "CS - Sài Gòn"),
    ]
    report = await HrOrgImport(session, today=date(2026, 11, 9)).run(rows)

    codes = {row.code: row for row in (await session.scalars(select(Department))).all()}
    assert {"BOD", "MEDIA-TK", "MEDIA-BT", "MEDIA-QD", "TELE", "KT", "TT", "TDD"} <= set(codes)
    assert not {"BGD", "BT", "TK", "D", "SALE", "TCKT", "KHAC", "CS"} & set(codes)
    assert codes["MEDIA-TK"].id == defaults["TK"].id
    assert codes["MEDIA-TK"].parent_id == codes["MEDIA"].id and codes["MEDIA"].active
    assert codes["MEDIA-TK"].function is DepartmentFunction.THIET_KE
    assert codes["TDD"].parent_id == codes["TT"].id
    assert codes["TDD"].block == "Khối vận hành"
    assert sorted(report.removed) == ["CS", "KHAC"]
    assert report.skipped == ["TEST"]

    # New departments order like SALE did (D1); VPTCKT holds what BGD granted (D3).
    grants = {
        (row.department_id, row.module, row.position, row.permission): row.scope
        for row in (await session.scalars(select(DepartmentPermission))).all()
    }
    assert grants[(codes["TDD"].id, "ORDER", Position.MEMBER, "ORDER_CREATE")] == "ALL"
    assert grants[(codes["TDD"].id, "ORDER", Position.HEAD, "ORDER_APPROVE")] == "OWN"
    board = {key[1:]: scope for key, scope in grants.items() if key[0] == codes["BOD"].id}
    assert board
    for (module, position, permission), scope in board.items():
        assert grants[(codes["VPTCKT"].id, module, position, permission)] in (scope, "ALL")

    # People: names from the sheet, heads by title, an existing head stays one.
    assert dung.full_name == "Nguyễn Xuân Dũng" and await _open(world, dung) == {"ADS": "HEAD"}
    assert await _open(world, luong) == {"MEDIA-BT": "HEAD"}
    assert await _open(world, long_) == {"MEDIA": "HEAD", "MEDIA-TK": "HEAD"}
    assert await _open(world, nhung) == {"TVV": "MEMBER"}
    assert await _open(world, tiep) == {"VPTCKT": "HEAD"}
    assert await _open(world, phuong) == {"TELE": "MEMBER"}
    assert phuong.job_title == "Trưởng nhóm Telesales" and phuong.branch == "VP_HN"
    gpi = await session.scalar(select(LegalEntity).where(LegalEntity.code == "GPI"))
    assert gpi is not None and phuong.legal_entity_id == gpi.id

    # New accounts: code with D, no Telegram, a temporary password to change.
    made = {account.member_code: account for account in report.new_accounts}
    assert set(made) == {"D075QUYET", "D002TRANG", "D020HONG"}
    quyet = await session.scalar(select(User).where(User.member_code == "D075QUYET"))
    trang = await session.scalar(select(User).where(User.member_code == "D002TRANG"))
    hong = await session.scalar(select(User).where(User.member_code == "D020HONG"))
    assert quyet is not None and trang is not None and hong is not None
    assert quyet.telegram_user_id is None and quyet.password_temporary is True
    assert await _open(world, quyet) == {"TDD": "HEAD"}
    # D6: the member at the head's branch reports to them; with one head the
    # other branch does too.
    seats = {
        seat.user_id: seat
        for seat in (
            await session.scalars(
                select(DepartmentMember).where(
                    DepartmentMember.department_id == codes["TDD"].id,
                    DepartmentMember.left_at.is_(None),
                )
            )
        ).all()
    }
    assert seats[trang.id].manager_user_id == quyet.id
    assert seats[hong.id].manager_user_id == quyet.id
    assert login(world, "Đ002TRANG", made["D002TRANG"].password).json() == {
        "must_change_password": True
    }

    # Nothing personal leaves in the summary.
    summary = str(report.summary())
    assert "Phạm" not in summary and made["D002TRANG"].password not in summary

    # Again: nothing new.
    second = await HrOrgImport(session, today=date(2026, 11, 9)).run(rows)
    assert second.new_accounts == [] and second.created == [] and second.renamed == []
    assert second.seats_opened == 0 and second.seats_closed == 0
