"""Nhân sự đợt 3-4 (0100): đơn từ (nghỉ / muộn / sớm) duyệt Trưởng phòng → Giám
đốc khối → Giám đốc → HCNS, phép năm, ca, ngày lễ, file máy chấm công, bảng công."""

from __future__ import annotations

import io
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from meobot.application.org.departments import ensure_default_departments, seat_person
from meobot.db.models.department import Department, DepartmentPermission
from meobot.db.models.hr_attendance import HrAbsenceRequest, HrAttendanceDay, HrLeaveType
from meobot.db.models.hr_profile import HrProfile
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.hr.attendance import (
    LEAVE_TYPE_SEED,
    AbsenceKind,
    DayPart,
    LeaveCover,
    StepLevel,
    annual_balance,
    default_shift,
    evaluate_day,
    leave_days,
    level_skipped,
    plan_levels,
)
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from tests.unit.pr_world import World

TODAY = date(2026, 11, 5)


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meobot.api.routers.hr_attendance.vn_today", lambda: TODAY)


async def _company(world: World) -> dict[str, Any]:
    """BOD (giám đốc), Khối vận hành = VPKVH (giám đốc khối) + QHKH (a head,
    two staff), HCNS (TIMESHEET), the default leave types."""
    session = world.session
    await ensure_default_departments(session)
    bod = Department(code="BOD", name="Ban giám đốc", function=DepartmentFunction.BOARD)
    office = Department(code="VPKVH", name="Văn phòng khối vận hành", block="Khối vận hành")
    care = Department(code="QHKH", name="Phòng QHKH", block="Khối vận hành")
    hcns = Department(code="HCNS", name="Phòng HCNS", block="Khối nhân sự")
    session.add_all([bod, office, care, hcns])
    await session.flush()
    for position in (Position.HEAD, Position.MEMBER):
        for permission in ("TIMESHEET", "VIEW"):
            session.add(
                DepartmentPermission(
                    department_id=hcns.id,
                    position=position,
                    module="HR",
                    permission=permission,
                    scope="ALL",
                )
            )
    session.add(
        DepartmentPermission(
            department_id=care.id,
            position=Position.HEAD,
            module="HR",
            permission="VIEW",
            scope="OWN",
        )
    )
    for index, seed in enumerate(LEAVE_TYPE_SEED):
        session.add(
            HrLeaveType(
                code=seed.code,
                name=seed.name,
                paid=seed.paid,
                annual=seed.annual,
                max_days=seed.max_days,
                symbol=seed.symbol,
                sort_order=index,
            )
        )
    people: dict[str, User] = {}
    for key, name, code in (
        ("director", "Trần Hoàng Liên", "B002LIEN"),
        ("block", "Nguyễn Thị Tuyết", "Q001TUYET"),
        ("head", "Đỗ Thị Chinh", "Q002CHINH"),
        ("staff", "Phạm Hồng Sơn", "D094SON"),
        ("staff2", "Lê Thu Hà", "Q010HA"),
        ("hr", "Ngô Tuyết Nhung", "H005NHUNG"),
    ):
        user = User(full_name=name, role=Role.EMPLOYEE, member_code=code)
        session.add(user)
        people[key] = user
    await session.flush()
    await seat_person(session, bod.id, people["director"].id, Position.HEAD)
    await seat_person(session, office.id, people["block"].id, Position.HEAD)
    await seat_person(session, care.id, people["head"].id, Position.HEAD)
    await seat_person(session, care.id, people["staff"].id, Position.MEMBER)
    await seat_person(session, care.id, people["staff2"].id, Position.MEMBER)
    await seat_person(session, hcns.id, people["hr"].id, Position.MEMBER)
    for key in ("staff", "head", "staff2"):
        session.add(
            HrProfile(
                user_id=people[key].id,
                joined_on=date(2020, 3, 1),
                official_on=date(2020, 4, 1),
            )
        )
    await session.flush()
    return people


class _Caller:
    """The test client acting as one person on every call."""

    def __init__(self, world: World, user: User) -> None:
        self._world = world
        self._user = user

    def __getattr__(self, method: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self._world.act_as(self._user)
            return getattr(self._world.client, method)(*args, **kwargs)

        return call


def as_(world: World, user: User) -> Any:
    return _Caller(world, user)


# --- the maths ------------------------------------------------------------------------------


def test_01_the_rules() -> None:
    shift = default_shift()

    def same(_day: date) -> Any:
        return shift

    # Mon 02/11 - Sat 07/11: 5 full days + Saturday morning; 2/11 a holiday.
    assert leave_days(date(2026, 11, 2), date(2026, 11, 7), DayPart.FULL, same) == Decimal("5.5")
    assert leave_days(
        date(2026, 11, 2), date(2026, 11, 7), DayPart.FULL, same, [date(2026, 11, 2)]
    ) == Decimal("4.5")
    assert leave_days(date(2026, 11, 3), date(2026, 11, 3), DayPart.MORNING, same) == Decimal("0.5")
    assert leave_days(date(2026, 11, 8), date(2026, 11, 8), DayPart.FULL, same) == 0  # Sunday

    # Law: 12 days, +1 per 5 years; earned month by month from the official date.
    balance = annual_balance(
        year=2026,
        today=date(2026, 11, 5),
        joined_on=date(2020, 3, 1),
        official_on=date(2020, 4, 1),
        left_on=None,
        used=Decimal(2),
    )
    assert balance.entitlement == 13 and balance.months == 11
    assert balance.earned == Decimal("11.9") and balance.remaining == Decimal("9.9")
    newcomer = annual_balance(
        year=2026,
        today=date(2026, 11, 5),
        joined_on=date(2026, 7, 1),
        official_on=date(2026, 9, 20),
        left_on=None,
        used=Decimal(0),
    )
    assert newcomer.months == 2 and newcomer.earned == Decimal("2.0")  # October, November

    # The chain: a leave all four levels; a late one the first manager level + HCNS.
    chain = {StepLevel.MANAGER: ["h"], StepLevel.BLOCK: ["b"], StepLevel.DIRECTOR: ["d"]}
    assert plan_levels(AbsenceKind.LEAVE, chain, "x") == [
        StepLevel.MANAGER,
        StepLevel.BLOCK,
        StepLevel.DIRECTOR,
        StepLevel.HR,
    ]
    assert plan_levels(AbsenceKind.LATE, chain, "x") == [StepLevel.MANAGER, StepLevel.HR]
    assert plan_levels(AbsenceKind.LATE, chain | {StepLevel.MANAGER: ["x"]}, "x") == [
        StepLevel.BLOCK,
        StepLevel.HR,
    ]
    assert level_skipped(["x"], "x", [])
    assert level_skipped(["h", "b"], "x", ["h"])
    assert not level_skipped(["b"], "x", ["h"])

    # A day: late beyond the 5 minutes' grace, an approved late request covers it.
    monday = date(2026, 10, 5)
    day = evaluate_day(
        day=monday, shift=shift, holiday=False, check_in=time(8, 17), check_out=time(18, 5)
    )
    assert day.symbol == "X" and day.late == 17 and day.late_counted == 17
    covered = evaluate_day(
        day=monday,
        shift=shift,
        holiday=False,
        check_in=time(8, 17),
        check_out=time(18, 5),
        late_allowance=30,
    )
    assert covered.late_counted == 0
    assert (
        evaluate_day(
            day=monday, shift=shift, holiday=False, check_in=time(8, 3), check_out=time(17, 31)
        ).late
        == 0
    )
    morning_off = evaluate_day(
        day=monday,
        shift=shift,
        holiday=False,
        check_in=time(13, 25),
        check_out=time(17, 40),
        leaves=[LeaveCover(("morning",), True, "P", "Nghỉ phép năm")],
    )
    assert morning_off.worked == Decimal("0.5") and morning_off.paid_leave == Decimal("0.5")
    assert morning_off.late == 0 and morning_off.symbol == "P/X"
    missing = evaluate_day(
        day=monday, shift=shift, holiday=False, check_in=time(8, 0), check_out=None
    )
    assert missing.missing and missing.worked == 0
    absent = evaluate_day(day=monday, shift=shift, holiday=False, check_in=None, check_out=None)
    assert absent.symbol == "V" and absent.absent == 1
    saturday = evaluate_day(
        day=date(2026, 10, 10),
        shift=shift,
        holiday=False,
        check_in=time(8, 0),
        check_out=time(12, 1),
    )
    assert saturday.expected == Decimal("0.5") and saturday.symbol == "X"
    assert (
        evaluate_day(day=monday, shift=shift, holiday=True, check_in=None, check_out=None).symbol
        == "L"
    )


# --- requests -------------------------------------------------------------------------------


async def test_02_a_leave_goes_up_four_levels(world: World) -> None:
    people = await _company(world)
    staff = as_(world, people["staff"])
    form = staff.get("/api/hr/absences/form").json()
    assert [step["label"] for step in form["chain"]["LEAVE"]] == [
        "Trưởng phòng",
        "Giám đốc khối",
        "Giám đốc",
        "HCNS xác nhận",
    ]
    assert [step["level"] for step in form["chain"]["LATE"]] == ["MANAGER", "HR"]
    assert form["balance"]["entitlement"] == 13

    sent = staff.post(
        "/api/hr/absences",
        json={
            "kind": "LEAVE",
            "leave_type": "ANNUAL",
            "start_on": "2026-11-09",
            "end_on": "2026-11-10",
            "reason": "Việc gia đình",
        },
    )
    assert sent.status_code == 201, sent.json()
    request = sent.json()
    assert request["days"] == 2 and request["current_level"] == "MANAGER"
    assert [p["name"] for p in request["waiting_on"]] == ["Đỗ Thị Chinh"]
    # The same days again: refused.
    again = staff.post(
        "/api/hr/absences",
        json={"kind": "LEAVE", "leave_type": "ANNUAL", "start_on": "2026-11-10", "reason": "x"},
    )
    assert (
        again.status_code == 422
        and again.json()["error"]["details"]["reason"] == "hr_absence_overlap"
    )

    # Nobody else decides the head's step; the requester never decides.
    assert (
        as_(world, people["block"])
        .post(f"/api/hr/absences/{request['id']}/decide", json={"approve": True})
        .status_code
        == 403
    )
    head = as_(world, people["head"])
    assert head.get("/api/hr/absences?tab=awaiting").json()["awaiting"] == 1
    step = head.post(f"/api/hr/absences/{request['id']}/decide", json={"approve": True}).json()
    assert step["current_level"] == "BLOCK"
    # An old screen deciding the step already decided: 409.
    stale = head.post(
        f"/api/hr/absences/{request['id']}/decide",
        json={"approve": True, "step_id": request["current_step_id"]},
    )
    assert stale.status_code in (403, 409)
    step = (
        as_(world, people["block"])
        .post(f"/api/hr/absences/{request['id']}/decide", json={"approve": True})
        .json()
    )
    assert step["current_level"] == "DIRECTOR"
    step = (
        as_(world, people["director"])
        .post(f"/api/hr/absences/{request['id']}/decide", json={"approve": True})
        .json()
    )
    assert step["current_level"] == "HR"
    done = (
        as_(world, people["hr"])
        .post(f"/api/hr/absences/{request['id']}/decide", json={"approve": True, "note": "OK"})
        .json()
    )
    assert done["status"] == "APPROVED"
    assert [item["status"] for item in done["steps"]] == ["APPROVED"] * 4

    told = (
        await world.session.scalars(
            select(UserNotification.event_type).where(
                UserNotification.recipient_user_id == people["staff"].id
            )
        )
    ).all()
    assert "hr_absence_approved" in told
    mine = as_(world, people["staff"]).get("/api/hr/leave/me").json()["balance"]
    assert mine["used"] == 2 and mine["remaining"] == pytest.approx(9.9)


async def test_03_late_early_and_refusals(world: World) -> None:
    people = await _company(world)
    # The head is late: no Trưởng phòng above them -> Giám đốc khối, then HCNS.
    head = as_(world, people["head"])
    late = head.post(
        "/api/hr/absences",
        json={"kind": "LATE", "start_on": "2026-11-09", "at_time": "09:00", "reason": "Kẹt xe"},
    ).json()
    assert late["minutes"] == 60
    assert [step["level"] for step in late["steps"]] == ["BLOCK", "HR"]
    # Before the shift starts: refused; on a Sunday: no shift.
    bad = head.post(
        "/api/hr/absences",
        json={"kind": "EARLY", "start_on": "2026-11-09", "at_time": "18:00", "reason": "x"},
    )
    assert bad.json()["error"]["details"]["reason"] == "hr_absence_time_invalid"
    sunday = head.post(
        "/api/hr/absences",
        json={"kind": "EARLY", "start_on": "2026-11-08", "at_time": "15:00", "reason": "x"},
    )
    assert sunday.json()["error"]["details"]["reason"] == "hr_absence_no_shift"

    # A refusal needs a reason and ends the request.
    block = as_(world, people["block"])
    no_reason = block.post(f"/api/hr/absences/{late['id']}/decide", json={"approve": False})
    assert no_reason.json()["error"]["details"]["reason"] == "hr_absence_note_required"
    refused = block.post(
        f"/api/hr/absences/{late['id']}/decide", json={"approve": False, "note": "Không hợp lý"}
    ).json()
    assert refused["status"] == "REJECTED"
    assert [item["status"] for item in refused["steps"]] == ["REJECTED", "CLOSED"]

    # The staff's early leave: the head, then HCNS; withdrawn while open.
    staff = as_(world, people["staff"])
    early = staff.post(
        "/api/hr/absences",
        json={"kind": "EARLY", "start_on": "2026-11-11", "at_time": "16:30", "reason": "Đón con"},
    ).json()
    assert early["minutes"] == 60 and [s["level"] for s in early["steps"]] == ["MANAGER", "HR"]
    gone = staff.post(f"/api/hr/absences/{early['id']}/cancel", json={}).json()
    assert gone["status"] == "CANCELLED" and not gone["can_cancel"]

    # Another staff member does not see it; the head does.
    assert as_(world, people["staff2"]).get(f"/api/hr/absences/{early['id']}").status_code == 404
    assert head.get(f"/api/hr/absences/{early['id']}").status_code == 200


async def test_04_annual_leave_list_and_adjustments(world: World) -> None:
    people = await _company(world)
    hr = as_(world, people["hr"])
    added = hr.post(
        "/api/hr/leave/adjustments",
        json={
            "user_id": str(people["staff"].id),
            "year": 2026,
            "days": -3,
            "note": "Đã nghỉ trước khi dùng APM",
        },
    )
    assert added.status_code == 201, added.json()
    rows = hr.get("/api/hr/leave/balances?year=2026").json()["items"]
    son = next(row for row in rows if row["member_code"] == "D094SON")
    assert son["adjustment"] == -3 and son["remaining"] == pytest.approx(8.9)
    # The staff cannot list or adjust; the head lists their department.
    staff = as_(world, people["staff"])
    assert staff.get("/api/hr/leave/balances").status_code == 403
    assert (
        staff.post(
            "/api/hr/leave/adjustments",
            json={"user_id": str(people["staff"].id), "year": 2026, "days": 1, "note": "x"},
        ).status_code
        == 403
    )
    codes = {
        row["member_code"]
        for row in as_(world, people["head"]).get("/api/hr/leave/balances").json()["items"]
    }
    assert codes == {"Q002CHINH", "D094SON", "Q010HA"}


# --- the time clock + timesheet ---------------------------------------------------------------


def _workbook() -> bytes:
    book = Workbook()
    codes = book.active
    assert codes is not None
    codes.title = "MCC CBNV"
    codes.append(["CN", "MÃ NHÂN VIÊN", "HỌ VÀ TÊN", "NGÀY THAM GIA"])
    codes.append(["VP", "D094SON", "Phạm Hồng Sơn", date(2022, 1, 1)])
    sheet = book.create_sheet("VP - T10")
    sheet.append(["VP - Hà Nội", None, None, "CHI TIẾT CHẤM CÔNG"])
    sheet.append([None, None, None, "Từ ngày 01/10/2026"])
    sheet.append(
        ["Mã nhân viên", "Mã chấm công", "Tên nhân viên", "Phòng ban", "Ngày", "Thứ", "Vào", "Ra"]
    )
    sheet.append(
        [
            "Đ094SON",
            "182-5",
            "PHẠM HỒNG SƠN",
            "HCNS",
            date(2026, 10, 1),
            "Năm",
            "07:35:00",
            "18:12:00",
        ]
    )
    sheet.append(
        ["Đ094SON", "182-5", "PHẠM HỒNG SƠN", "HCNS", date(2026, 10, 2), "Sáu", "08:13", "18:33"]
    )
    sheet.append(
        ["Đ094SON", "182-5", "PHẠM HỒNG SƠN", "HCNS", date(2026, 10, 5), "Hai", time(8, 2), None]
    )
    sheet.append(["X999NOBODY", "1", "NGƯỜI LẠ", "?", date(2026, 10, 1), "Năm", "08:00", "17:30"])
    other = book.create_sheet("CS HN - T10")
    other.append(["CS - Hà Nội"])
    other.append([])
    other.append(
        [
            "Mã nhân viên",
            "Mã chấm công",
            "Tên nhân viên",
            "Phòng ban",
            "Ngày",
            "Thứ",
            "Vào 1",
            "Ra 1",
        ]
    )
    other.append(["Q010HA", "100", "LÊ THU HÀ", "QHKH", "06/10/2026", "Ba", "07:43", "17:31"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def test_05_the_time_clock_file_and_the_month(world: World) -> None:
    people = await _company(world)
    hr = as_(world, people["hr"])
    data = _workbook()
    preview = hr.post("/api/hr/attendance/import", content=data).json()
    assert [(s["name"], s["read"]) for s in preview["sheets"]] == [
        ("MCC CBNV", False),
        ("VP - T10", True),
        ("CS HN - T10", True),
    ]
    assert preview["people"] == 2 and preview["days"] == 4 and not preview["committed"]
    assert preview["unmatched"] == [{"code": "X999NOBODY", "rows": 1}]
    assert await world.session.scalar(select(HrAttendanceDay.id)) is None
    done = hr.post("/api/hr/attendance/import?commit=true", content=data).json()
    assert done["committed"] and done["new_days"] == 4

    # HCNS types a holiday and approves a morning off for 06/10 ... (Ha's on 06/10 here)
    assert (
        hr.post(
            "/api/hr/holidays", json={"start": "2026-10-09", "name": "Nghỉ công ty"}
        ).status_code
        == 201
    )
    son = people["staff"]
    sheet = hr.get(f"/api/hr/timesheet/people/{son.id}?month=2026-10").json()
    by_day = {item["day"]: item for item in sheet["days"]}
    assert by_day["2026-10-01"]["symbol"] == "X" and by_day["2026-10-01"]["late"] == 0
    assert by_day["2026-10-02"]["late"] == 13
    assert by_day["2026-10-05"]["missing"] and by_day["2026-10-05"]["symbol"] == "!"
    assert by_day["2026-10-09"]["symbol"] == "L"
    assert by_day["2026-10-04"]["symbol"] == "-"  # Sunday
    assert sheet["totals"]["late_count"] == 1 and sheet["totals"]["missing"] == 1

    # HCNS fixes the missing punch-out; a re-import leaves the typed day alone.
    fixed = hr.put(
        f"/api/hr/attendance/{son.id}/2026-10-05",
        json={"check_in": "08:02", "check_out": "17:35", "note": "Quên chấm ra"},
    ).json()
    assert next(d for d in fixed["days"] if d["day"] == "2026-10-05")["symbol"] == "X"
    again = hr.post("/api/hr/attendance/import?commit=true", content=data).json()
    assert again["kept_manual"] == 1

    # The month: the whole company for HCNS, the department for its head, oneself otherwise.
    month = hr.get("/api/hr/timesheet?month=2026-10").json()
    assert {"D094SON", "Q010HA"} <= {row["member_code"] for row in month["people"]}
    head_view = as_(world, people["head"]).get("/api/hr/timesheet?month=2026-10").json()
    assert {row["member_code"] for row in head_view["people"]} == {"Q002CHINH", "D094SON", "Q010HA"}
    staff = as_(world, people["staff"])
    assert staff.get("/api/hr/timesheet?month=2026-10").status_code == 403
    assert staff.get("/api/hr/timesheet/people/me?month=2026-10").status_code == 200
    assert staff.get(f"/api/hr/timesheet/people/{people['staff2'].id}").status_code == 404

    # The Excel file.
    exported = hr.get("/api/hr/timesheet/export.xlsx?month=2026-10")
    assert exported.status_code == 200
    book = load_workbook(io.BytesIO(exported.content))
    assert book.sheetnames == ["Bảng công", "Chi tiết", "Ký hiệu"]

    # Locked: no import, no edit, no request in October.
    assert (
        hr.put("/api/hr/timesheet/lock", json={"month": "2026-10", "locked": True}).status_code
        == 200
    )
    blocked = hr.post("/api/hr/attendance/import?commit=true", content=data)
    assert blocked.json()["error"]["details"]["reason"] == "hr_month_locked"
    late = staff.post(
        "/api/hr/absences",
        json={"kind": "LATE", "start_on": "2026-10-07", "at_time": "09:00", "reason": "x"},
    )
    assert late.json()["error"]["details"]["reason"] == "hr_month_locked"
    # Only HCNS imports.
    assert staff.post("/api/hr/attendance/import", content=data).status_code == 403


async def test_06_an_approved_leave_shows_on_the_timesheet(world: World) -> None:
    people = await _company(world)
    staff = as_(world, people["staff2"])
    request = staff.post(
        "/api/hr/absences",
        json={
            "kind": "LEAVE",
            "leave_type": "UNPAID",
            "start_on": "2026-11-04",
            "part": "AFTERNOON",
            "reason": "Khám bệnh",
        },
    ).json()
    assert request["days"] == 0.5
    for key in ("head", "block", "director", "hr"):
        as_(world, people[key]).post(
            f"/api/hr/absences/{request['id']}/decide", json={"approve": True}
        )
    stored = await world.session.get(HrAbsenceRequest, __import__("uuid").UUID(request["id"]))
    assert stored is not None and stored.status == "APPROVED"
    world.session.add(
        HrAttendanceDay(
            user_id=people["staff2"].id,
            day=date(2026, 11, 4),
            check_in=time(7, 55),
            check_out=time(12, 2),
        )
    )
    await world.session.flush()
    sheet = staff.get("/api/hr/timesheet/people/me?month=2026-11").json()
    day = next(item for item in sheet["days"] if item["day"] == "2026-11-04")
    assert day["symbol"] == "KL/X" and day["worked"] == 0.5 and day["unpaid_leave"] == 0.5
    assert day["early"] == 0
    # Days after today are blank, not absent.
    assert next(item for item in sheet["days"] if item["day"] == "2026-11-20")["symbol"] == ""


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def test_07_a_forgotten_punch_is_explained_with_pictures(world: World) -> None:
    people = await _company(world)
    staff = as_(world, people["staff"])
    world.session.add(
        HrAttendanceDay(user_id=people["staff"].id, day=date(2026, 11, 4), check_in=time(8, 0))
    )
    await world.session.flush()
    before = staff.get("/api/hr/timesheet/people/me?month=2026-11").json()
    assert next(d for d in before["days"] if d["day"] == "2026-11-04")["symbol"] == "!"

    # Only pictures; another person never sees a picture not sent yet.
    refused = staff.post("/api/hr/absence-files?name=x.pdf", content=b"%PDF-1.4 x")
    assert refused.json()["error"]["details"]["reason"] == "hr_absence_file_invalid"
    picture = staff.post("/api/hr/absence-files?name=lich-su-web.png", content=PNG).json()
    assert picture["content_type"] == "image/png"
    assert (
        as_(world, people["head"]).get(f"/api/hr/absence-files/{picture['id']}").status_code == 404
    )

    body = {
        "kind": "EXPLAIN",
        "start_on": "2026-11-04",
        "check_out": "17:35",
        "reason": "Quên chấm ra, có lịch sử duyệt web đến 17:35",
    }
    no_picture = staff.post("/api/hr/absences", json=body)
    assert no_picture.json()["error"]["details"]["reason"] == "hr_absence_files_required"
    future = staff.post(
        "/api/hr/absences", json=body | {"start_on": "2026-11-09", "file_ids": [picture["id"]]}
    )
    assert future.json()["error"]["details"]["reason"] == "hr_absence_time_invalid"
    sent = staff.post("/api/hr/absences", json=body | {"file_ids": [picture["id"]]})
    assert sent.status_code == 201, sent.json()
    request = sent.json()
    assert request["kind_label"] == "Giải trình chấm công" and request["check_out"] == "17:35"
    assert [step["level"] for step in request["steps"]] == ["MANAGER", "HR"]
    assert [item["name"] for item in request["files"]] == ["lich-su-web.png"]
    # The approvers open the picture; it is no longer the uploader's to drop.
    shown = as_(world, people["head"]).get(f"/api/hr/absence-files/{picture['id']}")
    assert shown.status_code == 200 and shown.content == PNG
    assert shown.headers["content-security-policy"] == "sandbox"
    assert staff.delete(f"/api/hr/absence-files/{picture['id']}").status_code == 404
    assert (
        as_(world, people["staff2"]).get(f"/api/hr/absence-files/{picture['id']}").status_code
        == 404
    )

    for key in ("head", "hr"):
        as_(world, people[key]).post(
            f"/api/hr/absences/{request['id']}/decide", json={"approve": True}
        )
    after = staff.get("/api/hr/timesheet/people/me?month=2026-11").json()
    day = next(d for d in after["days"] if d["day"] == "2026-11-04")
    assert day["symbol"] == "X" and day["explained"] and day["out"] == "17:35"
    assert not day["missing"]
