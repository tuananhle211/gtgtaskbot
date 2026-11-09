"""Load the HR sheet's org chart and staff (2026-11-09, ``docs/hr/HR_MODULE_PLAN.md``).

One run, re-runnable, decided by the plan's answers:

* **Departments** (§3, D1, D3, D9): the sheet's 4-level chart. Existing
  departments are renamed in place (``BGD`` -> ``BOD``, ``BT``/``TK``/``D`` ->
  ``MEDIA-BT``/``MEDIA-TK``/``MEDIA-QD`` under ``MEDIA``, ``SALE`` -> ``TELE``,
  ``CSKH`` -> ``QHKH``, ``TCKT`` -> ``KT``) so their seats, grants, points and
  resources stay; missing ones are created with every module's defaults plus
  the basic order grants (:data:`BASIC_ORDER_GRANTS`); ``VPTCKT`` also gets what
  ``BGD`` grants. ``KHAC`` and ``CS`` go (deleted when nothing points at them,
  else switched off).
* **People** (§0, §4, D5, D6): a sheet row whose member code is a living
  account updates that account (full name, job title, branch, company); any
  other row becomes a new account - no Telegram id, a random temporary
  password the person must change. Everybody sits in the sheet's department
  (an account's other seats close - the plan's exceptions aside), heads by
  job title (an account keeps a head seat it already holds), and a member
  reports to the head of their own branch (else the department's only head).

Nothing personal beyond name, code, branch, title and company is read: the
sheet's ID numbers, phones and birthdays never reach this module.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from meobot.application.account.passwords import generate_temporary_password, hash_password
from meobot.application.org.departments import (
    close_seat,
    holds_open_order_work,
    seat_person,
    seed_module_defaults,
    sync_stream_tags,
)
from meobot.db.models.agent_chat import AgentProfile
from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.db.models.invite import InviteCode
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.order import OrderNodePoints
from meobot.db.models.resource import Resource
from meobot.db.models.user import User
from meobot.db.models.work_points import WorkPoint
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Role
from meobot.domain.org.models import (
    BASIC_ORDER_GRANTS,
    MODULE_ORDER,
    PRODUCTION_FUNCTIONS,
    DepartmentFunction,
    Position,
    branch_of,
    normalise_department_code,
)
from meobot.domain.resources.models import EmploymentStatus
from meobot.domain.units.member_code import normalise_member_code

# --- the plan's decisions -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DepartmentPlan:
    code: str
    name: str
    block: str
    parent: str | None = None
    #: The code the department carries today, when it is renamed in place.
    was: str | None = None


BOARD = "Ban giám đốc"
MKT = "Khối marketing"
KD = "Khối kinh doanh"
VH = "Khối vận hành"
NS = "Khối nhân sự"
TCKT = "Khối tài chính kế toán"

#: The sheet's "Mã phòng ban", in its order (D9: codes with hyphens).
DEPARTMENTS: tuple[DepartmentPlan, ...] = (
    DepartmentPlan("BOD", "Ban giám đốc", BOARD, was="BGD"),
    DepartmentPlan("VPMKT", "BOD Khối marketing", MKT),
    DepartmentPlan("ADS", "Phòng Ads", MKT, was="ADS"),
    DepartmentPlan("TN", "Phòng Tài nguyên", MKT, was="TN"),
    DepartmentPlan("MEDIA", "Phòng Media (Quảng cáo - Tiếp thị)", MKT, was="MEDIA"),
    DepartmentPlan("MEDIA-TK", "Phòng Media (Thiết kế)", MKT, parent="MEDIA", was="TK"),
    DepartmentPlan("MEDIA-BT", "Phòng Media (Biên tập)", MKT, parent="MEDIA", was="BT"),
    DepartmentPlan("MEDIA-QD", "Phòng Media (Quay dựng)", MKT, parent="MEDIA", was="D"),
    DepartmentPlan("PR", "Phòng PR", MKT, was="PR"),
    DepartmentPlan("TVOL", "Phòng Tư vấn online", MKT),
    DepartmentPlan("VPKKD", "BOD Khối kinh doanh", KD),
    DepartmentPlan("TELE", "Phòng Telesales + Tư vấn online", KD, was="SALE"),
    DepartmentPlan("PTTT", "Phòng Phát triển thị trường", KD),
    DepartmentPlan("VPKVH", "BOD Khối vận hành", VH),
    DepartmentPlan("QHKH", "Phòng Quan hệ khách hàng", VH, was="CSKH"),
    DepartmentPlan("TT", "Trung tâm", VH),
    DepartmentPlan("TDD", "Bộ phận Tiếp đón", VH, parent="TT"),
    DepartmentPlan("TVV", "Bộ phận Tư vấn viên", VH, parent="TT"),
    DepartmentPlan("BS", "Bác sĩ", VH, parent="TT"),
    DepartmentPlan("DD", "Điều dưỡng", VH, parent="TT"),
    DepartmentPlan("KTV", "Kỹ thuật viên", VH, parent="TT"),
    DepartmentPlan("BPHT", "Bộ phận Hỗ trợ (Hành chính, kho, thu ngân)", VH, parent="TT"),
    DepartmentPlan("VPNS", "BOD Khối nhân sự", NS),
    DepartmentPlan("HCNS", "Phòng Hành chính nhân sự", NS, was="HCNS"),
    DepartmentPlan("TD", "Phòng Tuyển dụng", NS),
    DepartmentPlan("DT-TTNB", "Phòng Đào tạo và TTNB", NS),
    DepartmentPlan("HCMS", "Phòng Hành chính mua sắm", NS),
    DepartmentPlan("PC", "Phòng Pháp chế", NS),
    DepartmentPlan("GSAN", "Phòng Giám sát an ninh", NS),
    DepartmentPlan("CNTT", "Phòng Công nghệ thông tin", NS),
    DepartmentPlan("VPTCKT", "BOD Khối TCKT", TCKT),
    DepartmentPlan("KT", "Phòng Kế toán", TCKT, was="TCKT"),
    DepartmentPlan("TC", "Phòng Tài chính", TCKT),
)

#: Departments that go (Q5: "Bỏ phòng cơ sở"; "Xoá phòng KHAC").
REMOVED: tuple[str, ...] = ("KHAC", "CS")
#: D3: ``VPTCKT`` takes over what Ban giám đốc granted Tiệp.
COPY_GRANTS: tuple[tuple[str, str], ...] = (("VPTCKT", "BOD"),)
#: Seats an account keeps besides the sheet's one (Q6: Mai Tùng Long leads
#: Media and still the Design team the order flow routes to him).
KEEP_SEATS: dict[str, tuple[str, ...]] = {"E051LONG": ("MEDIA-TK",)}

#: D5: a job title starting with one of these makes a head.
_HEAD_TITLE = re.compile(
    r"^(truong phong|tp |giam doc|truong bo phan|ke toan truong|dieu duong truong|pho tgd)"
)
SKIPPED_CODES = frozenset({"TEST"})


def fold(text: str) -> str:
    value = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return " ".join("".join(c for c in value if unicodedata.category(c) != "Mn").lower().split())


def is_head_title(title: str | None) -> bool:
    return bool(title) and bool(_HEAD_TITLE.match(fold(title or "")))


def entity_code_of(text: str | None) -> str | None:
    """The sheet's "Pháp nhân đóng BHXH" (``GPI Tech``, ``APEX``, ``CMG PVC``,
    ``APM SG``) as a :class:`LegalEntity` code."""
    value = fold(text or "").upper()
    if not value:
        return None
    for needle, code in (("GPI", "GPI"), ("APEX", "APEXMED"), ("CMG", "CMG")):
        if needle in value:
            return code
    if "COREMIND" in value:
        return "CMG"
    if "APM" in value:
        return "APM"
    return None


def parse_day(text: str | None) -> date | None:
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", text or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --- input / output -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaffRow:
    """One line of the sheet - only what this load reads."""

    code: str
    full_name: str
    department: str
    job_title: str | None = None
    branch: str | None = None
    entity: str | None = None
    official_on: str | None = None


@dataclass(frozen=True, slots=True)
class NewAccount:
    member_code: str
    full_name: str
    department: str
    branch: str | None
    password: str


@dataclass
class ImportReport:
    renamed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    switched_off: list[str] = field(default_factory=list)
    updated_people: int = 0
    renamed_people: int = 0
    new_accounts: list[NewAccount] = field(default_factory=list)
    seats_opened: int = 0
    seats_closed: int = 0
    heads: int = 0
    managers_set: int = 0
    no_manager: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """Counts and codes only - never a name or a password."""
        return {
            "departments_renamed": self.renamed,
            "departments_created": self.created,
            "departments_removed": self.removed,
            "departments_switched_off": self.switched_off,
            "people_updated": self.updated_people,
            "people_renamed": self.renamed_people,
            "accounts_created": len(self.new_accounts),
            "seats_opened": self.seats_opened,
            "seats_closed": self.seats_closed,
            "heads": self.heads,
            "managers_set": self.managers_set,
            "members_without_manager": self.no_manager,
            "rows_skipped": self.skipped,
            "warnings": self.warnings,
        }


# --- the load ---------------------------------------------------------------------------


class HrOrgImport:
    def __init__(self, session: AsyncSession, *, today: date) -> None:
        self._session = session
        self._today = today
        self._report = ImportReport()
        self._removed_ids_pending: list[Department] = []

    async def run(self, rows: list[StaffRow]) -> ImportReport:
        departments = await self._departments()
        await self._remove_departments(departments)
        users = await self._people(rows, departments)
        await self._managers(rows, departments, users)
        await sync_stream_tags(self._session, sorted({user.id for user in users.values()}))
        await self._session.flush()
        return self._report

    # departments ---------------------------------------------------------------------

    async def _departments(self) -> dict[str, Department]:
        existing = {row.code: row for row in (await self._session.scalars(select(Department)))}
        result: dict[str, Department] = {}
        created: list[Department] = []
        for order, plan in enumerate(DEPARTMENTS):
            row = existing.get(plan.code) or (existing.get(plan.was) if plan.was else None)
            if row is None:
                row = Department(
                    code=plan.code,
                    name=plan.name,
                    function=DepartmentFunction.NONE,
                    sort_order=order,
                )
                self._session.add(row)
                await self._session.flush()
                seed_module_defaults(self._session, row)
                created.append(row)
                self._report.created.append(plan.code)
            elif row.code != plan.code:
                self._report.renamed.append(f"{row.code}->{plan.code}")
            if plan.code == "MEDIA" and not row.active:
                # D1: the parent of the Media teams is a department like the new ones.
                created.append(row)
            row.code = plan.code
            row.name = plan.name
            row.block = plan.block
            row.active = True
            row.sort_order = order
            result[plan.code] = row
        await self._session.flush()
        for plan in DEPARTMENTS:
            result[plan.code].parent_id = result[plan.parent].id if plan.parent else None
        for row in created:
            await self._grant(row, BASIC_ORDER_GRANTS)
        for target, source in COPY_GRANTS:
            await self._copy_grants(result[target], result[source])
        await self._session.flush()
        return result

    async def _grant(
        self,
        row: Department,
        cells: Mapping[Position, Mapping[str, str]],
        *,
        module: str = MODULE_ORDER,
    ) -> None:
        for position, permissions in cells.items():
            for permission, scope in permissions.items():
                await self._put_grant(row.id, module, position, permission, scope)

    async def _copy_grants(self, target: Department, source: Department) -> None:
        grants = (
            await self._session.scalars(
                select(DepartmentPermission).where(DepartmentPermission.department_id == source.id)
            )
        ).all()
        for grant in grants:
            await self._put_grant(
                target.id, grant.module, grant.position, grant.permission, grant.scope
            )

    async def _put_grant(
        self,
        department_id: uuid.UUID,
        module: str,
        position: Position,
        permission: str,
        scope: str,
    ) -> None:
        row = await self._session.scalar(
            select(DepartmentPermission).where(
                DepartmentPermission.department_id == department_id,
                DepartmentPermission.module == module,
                DepartmentPermission.position == position,
                DepartmentPermission.permission == permission,
            )
        )
        if row is None:
            self._session.add(
                DepartmentPermission(
                    department_id=department_id,
                    module=module,
                    position=position,
                    permission=permission,
                    scope=scope,
                )
            )
        elif row.scope != "ALL" and scope == "ALL":
            row.scope = scope
        await self._session.flush()

    async def _remove_departments(self, keep: dict[str, Department]) -> None:
        wanted = {row.id for row in keep.values()}
        for code in REMOVED:
            row = await self._session.scalar(select(Department).where(Department.code == code))
            if row is None or row.id in wanted:
                continue
            self._removed_ids_pending.append(row)

    async def _drop_removed(self) -> None:
        for row in self._removed_ids_pending:
            if await self._open_seats(row.id):
                row.active = False
                self._report.switched_off.append(row.code)
                self._report.warnings.append(f"{row.code}: còn người ngồi, chỉ tắt phòng")
                continue
            referenced = any(
                [
                    await self._count(Resource.department_id, row.id),
                    await self._count(OrderNodePoints.department_id, row.id),
                    await self._count(WorkPoint.department_id, row.id),
                    await self._count(InviteCode.department_id, row.id),
                ]
            )
            if referenced:
                row.active = False
                self._report.switched_off.append(row.code)
                continue
            for agent in (await self._session.scalars(select(AgentProfile))).all():
                ids = list(agent.allowed_department_ids or [])
                if str(row.id) in [str(item) for item in ids]:
                    agent.allowed_department_ids = [
                        item for item in ids if str(item) != str(row.id)
                    ]
            await self._session.execute(
                delete(DepartmentPermission).where(DepartmentPermission.department_id == row.id)
            )
            await self._session.execute(
                delete(DepartmentMember).where(DepartmentMember.department_id == row.id)
            )
            await self._session.flush()
            await self._session.delete(row)
            self._report.removed.append(row.code)
        await self._session.flush()

    async def _open_seats(self, department_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count(DepartmentMember.id)).where(
                    DepartmentMember.department_id == department_id,
                    DepartmentMember.left_at.is_(None),
                )
            )
            or 0
        )

    async def _count(self, column: InstrumentedAttribute[Any], department_id: uuid.UUID) -> int:
        found = await self._session.scalar(select(func.count()).where(column == department_id))
        return int(found or 0)

    # people ------------------------------------------------------------------------------

    async def _people(
        self, rows: list[StaffRow], departments: dict[str, Department]
    ) -> dict[str, User]:
        entities = {
            row.code: row.id for row in (await self._session.scalars(select(LegalEntity))).all()
        }
        by_id = {row.id: row for row in departments.values()}
        users: dict[str, User] = {}
        for line in rows:
            code = normalise_member_code(line.code)
            if not code or code in SKIPPED_CODES:
                self._report.skipped.append(line.code or "(trống)")
                continue
            department = departments.get(normalise_department_code(line.department))
            if department is None:
                self._report.skipped.append(code)
                self._report.warnings.append(f"{code}: không rõ phòng {line.department!r}")
                continue
            user = await self._session.scalar(
                select(User).where(User.member_code == code, User.deleted_at.is_(None))
            )
            entity = entities.get(entity_code_of(line.entity) or "")
            branch = branch_of(line.branch)
            title = " ".join((line.job_title or "").split()) or None
            name = " ".join(line.full_name.split())
            if user is None:
                password = generate_temporary_password()
                official = parse_day(line.official_on)
                user = User(
                    telegram_user_id=None,
                    full_name=name,
                    role=Role.EMPLOYEE,
                    status=UserStatus.ACTIVE,
                    active=True,
                    member_code=code,
                    job_title=title,
                    branch=branch,
                    legal_entity_id=entity,
                    employment_status=EmploymentStatus.WORKING
                    if official is not None and official <= self._today
                    else EmploymentStatus.PROBATION,
                    password_hash=hash_password(password),
                    password_temporary=True,
                )
                self._session.add(user)
                await self._session.flush()
                self._report.new_accounts.append(
                    NewAccount(code, name, department.code, branch, password)
                )
            else:
                if user.full_name != name:
                    self._report.renamed_people += 1
                user.full_name = name
                user.job_title = title
                user.branch = branch
                user.legal_entity_id = entity
                self._report.updated_people += 1
            users[code] = user
            await self._seat(user, code, department, title, by_id)
        await self._drop_removed()
        return users

    async def _seat(
        self,
        user: User,
        code: str,
        department: Department,
        title: str | None,
        by_id: dict[uuid.UUID, Department],
    ) -> None:
        seats = (
            await self._session.scalars(
                select(DepartmentMember).where(
                    DepartmentMember.user_id == user.id, DepartmentMember.left_at.is_(None)
                )
            )
        ).all()
        current = next((seat for seat in seats if seat.department_id == department.id), None)
        head = (current is not None and current.position is Position.HEAD) or is_head_title(title)
        if current is None:
            self._report.seats_opened += 1
            await seat_person(
                self._session,
                department.id,
                user.id,
                Position.HEAD if head else Position.MEMBER,
            )
        elif head and current.position is not Position.HEAD:
            await seat_person(self._session, department.id, user.id, Position.HEAD)
        keep = {department.id} | {
            dept.id for dept in by_id.values() if dept.code in KEEP_SEATS.get(code, ())
        }
        for seat in seats:
            if seat.department_id in keep:
                continue
            other = by_id.get(seat.department_id)
            if other is None:
                other = await self._session.get(Department, seat.department_id)
            if (
                other is not None
                and other.function in PRODUCTION_FUNCTIONS
                and await holds_open_order_work(self._session, user.id)
            ):
                self._report.warnings.append(
                    f"{code}: còn việc đang làm ở {other.code}, giữ ghế cũ"
                )
                continue
            await close_seat(self._session, seat.department_id, user.id)
            self._report.seats_closed += 1

    async def _managers(
        self, rows: list[StaffRow], departments: dict[str, Department], users: dict[str, User]
    ) -> None:
        """D6: a member reports to the head of their own branch, else to the
        department's only head; a manager they already have and who still
        heads there stays."""
        for department in departments.values():
            seats = (
                await self._session.scalars(
                    select(DepartmentMember).where(
                        DepartmentMember.department_id == department.id,
                        DepartmentMember.left_at.is_(None),
                    )
                )
            ).all()
            heads = [seat.user_id for seat in seats if seat.position is Position.HEAD]
            self._report.heads += len(heads)
            if not heads:
                continue
            head_users = {
                user.id: user
                for user in (
                    await self._session.scalars(select(User).where(User.id.in_(heads)))
                ).all()
            }
            for seat in seats:
                if seat.position is Position.HEAD:
                    continue
                if seat.manager_user_id in heads:
                    continue
                person = await self._session.get(User, seat.user_id)
                branch = None if person is None else person.branch
                same = [uid for uid in heads if head_users[uid].branch == branch and branch]
                choice = (
                    same[0]
                    if len(same) == 1
                    else (heads[0] if len(heads) == 1 and not same else None)
                )
                if choice is None:
                    self._report.no_manager += 1
                    continue
                seat.manager_user_id = choice
                self._report.managers_set += 1
        await self._session.flush()


def staff_rows(table: list[list[str]]) -> list[StaffRow]:
    """The sheet's "Nhân sự" tab (header row included) as :class:`StaffRow` -
    only the columns this load reads."""
    header_at = next(
        index for index, row in enumerate(table) if any("ma" in fold(cell) for cell in row[:3])
    )
    header = [fold(cell) for cell in table[header_at]]

    def column(*words: str) -> int:
        return next(
            index for index, name in enumerate(header) if all(word in name for word in words)
        )

    at = {
        "code": column("ma", "nhan vien"),
        "name": column("ho va ten"),
        "department": column("ma phong ban"),
        "title": column("chuc danh"),
        "branch": column("chi nhanh"),
        "entity": column("phap nhan"),
        "official": column("ngay chinh thuc"),
    }
    lines: list[StaffRow] = []
    for raw in table[header_at + 1 :]:
        cells = [*raw, *([""] * (len(header) - len(raw)))]
        if not any(cell.strip() for cell in cells):
            continue
        lines.append(
            StaffRow(
                code=cells[at["code"]].strip(),
                full_name=cells[at["name"]].strip(),
                department=cells[at["department"]].strip(),
                job_title=cells[at["title"]].strip() or None,
                branch=cells[at["branch"]].strip() or None,
                entity=cells[at["entity"]].strip() or None,
                official_on=cells[at["official"]].strip() or None,
            )
        )
    return lines


__all__ = [
    "DEPARTMENTS",
    "HrOrgImport",
    "ImportReport",
    "NewAccount",
    "StaffRow",
    "entity_code_of",
    "is_head_title",
    "staff_rows",
]
