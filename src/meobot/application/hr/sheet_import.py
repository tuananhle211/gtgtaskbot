"""Load the HR sheet's "Nhân sự" tab into the profiles (HR đợt 2, 0096).

Re-runnable. A row is matched to its account by member code (accounts come
from đợt 1, ``hr_org_import``); its profile gets gender, birthday, phone, ID
number (sealed) + issue date / place, the three e-mails, join / official dates
and grade; its contracts: the probation one ("Số HĐLĐ thử việc", when it is a
contract number) and the official one ("Số HĐLĐ chính thức"), each once by
number. The official contract's length is not in the sheet: it is typed
``HDLD`` ("HĐ lao động - chưa rõ thời hạn") for HCNS to correct.

Nothing is printed but counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.hr.profiles import HrProfileService
from meobot.application.org.hr_import import fold, parse_day
from meobot.db.models.hr_profile import HrContract, HrContractType, HrProfile
from meobot.db.models.user import User
from meobot.domain.hr.profiles import add_months, gender_of
from meobot.domain.units.member_code import normalise_member_code

UNKNOWN_TYPE = ("HDLD", "HĐ lao động (chưa rõ thời hạn)")
_CONTRACT_NUMBER = re.compile(r"\d")

#: header words (folded) -> the field they fill.
COLUMNS: dict[str, tuple[str, ...]] = {
    "code": ("ma", "nhan vien"),
    "gender": ("gioi tinh",),
    "birth_date": ("ngay", "sinh"),
    "phone": ("dt di dong",),
    "id_number": ("so cccd",),
    "id_issued_on": ("ngay cap",),
    "id_issued_by": ("noi cap",),
    "personal_email": ("email ca nhan",),
    "work_email": ("email noi bo",),
    "drive_email": ("email drive",),
    "joined_on": ("ngay tham gia",),
    "official_on": ("ngay chinh thuc",),
    "grade": ("cap bac",),
    "probation_signed_on": ("ngay ky hdtv",),
    "probation_number": ("so hdld thu viec",),
    "probation_signed": ("hdtv",),
    "official_signed_on": ("ngay ky hdct",),
    "official_number": ("so hdld chinh thuc",),
    "official_signed": ("hdct",),
}


@dataclass
class ProfileImportReport:
    profiles: int = 0
    contracts: int = 0
    unknown_codes: list[str] = field(default_factory=list)
    bad_dates: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "profiles_updated": self.profiles,
            "contracts_created": self.contracts,
            "codes_not_found": self.unknown_codes,
            "unreadable_dates": self.bad_dates,
        }


def _columns(header: list[str]) -> dict[str, int]:
    folded = [" ".join(fold(cell).split()) for cell in header]
    found: dict[str, int] = {}
    for name, words in COLUMNS.items():
        for index, cell in enumerate(folded):
            if index in found.values():
                continue
            if name in ("probation_signed", "official_signed"):
                if cell == words[0]:
                    found[name] = index
                    break
            elif all(word in cell for word in words):
                found[name] = index
                break
    return found


def _phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    if len(digits) == 9:
        # The sheet lost the leading 0 ("376618533").
        digits = "0" + digits
    return digits


def _yes(value: str) -> bool:
    return value.strip().upper() in ("TRUE", "X", "1", "CÓ", "CO", "ĐÃ KÝ", "DA KY")


class HrProfileImport:
    def __init__(self, session: AsyncSession, service: HrProfileService) -> None:
        self._session = session
        self._service = service
        self._report = ProfileImportReport()

    async def run(self, table: list[list[str]]) -> ProfileImportReport:
        header_at = next(
            index
            for index, row in enumerate(table)
            if any("ma" in fold(cell) and "nhan vien" in fold(cell) for cell in row[:4])
        )
        at = _columns(table[header_at])
        await self._service.contract_types()
        await self._unknown_type()
        for raw in table[header_at + 1 :]:
            cells = [*raw, *([""] * (len(table[header_at]) - len(raw)))]

            def cell(name: str, cells: list[str] = cells) -> str:
                index = at.get(name)
                return "" if index is None else str(cells[index] or "").strip()

            code = normalise_member_code(cell("code"))
            if not code or code == "TEST":
                continue
            user = await self._session.scalar(
                select(User).where(User.member_code == code, User.deleted_at.is_(None))
            )
            if user is None:
                self._report.unknown_codes.append(code)
                continue
            await self._profile(user, cell)
            await self._contracts(user, cell)
        await self._session.flush()
        return self._report

    def _day(self, text: str) -> date | None:
        if not text:
            return None
        day = parse_day(text)
        if day is None:
            self._report.bad_dates += 1
        return day

    async def _profile(self, user: User, cell: Any) -> None:
        profile = await self._session.get(HrProfile, user.id)
        if profile is None:
            profile = HrProfile(user_id=user.id)
            self._session.add(profile)
            await self._session.flush()
        profile.gender = gender_of(cell("gender")) or profile.gender
        profile.birth_date = self._day(cell("birth_date")) or profile.birth_date
        profile.phone = _phone(cell("phone")) or profile.phone
        profile.id_issued_on = self._day(cell("id_issued_on")) or profile.id_issued_on
        profile.id_issued_by = cell("id_issued_by")[:200] or profile.id_issued_by
        profile.personal_email = cell("personal_email")[:200] or profile.personal_email
        profile.work_email = cell("work_email")[:200] or profile.work_email
        profile.drive_email = cell("drive_email")[:200] or profile.drive_email
        profile.joined_on = self._day(cell("joined_on")) or profile.joined_on
        profile.official_on = self._day(cell("official_on")) or profile.official_on
        profile.grade = cell("grade")[:20] or profile.grade
        id_number = re.sub(r"\s+", "", cell("id_number"))
        if id_number:
            secret = self._service._open_secret(profile) or {}
            secret["id_number"] = id_number
            self._service._seal_secret(profile, secret)
            profile.id_last4 = id_number[-4:]
        self._report.profiles += 1

    async def _contracts(self, user: User, cell: Any) -> None:
        existing = {
            (row.number or "")
            for row in (
                await self._session.scalars(select(HrContract).where(HrContract.user_id == user.id))
            ).all()
        }
        profile = await self._session.get(HrProfile, user.id)
        joined = profile.joined_on if profile else None
        official = profile.official_on if profile else None
        probation = cell("probation_number")
        if probation and _CONTRACT_NUMBER.search(probation) and probation not in existing:
            start = self._day(cell("probation_signed_on")) or joined
            end = (
                official - timedelta(days=1)
                if official and start and official > start
                else (add_months(start, 2) - timedelta(days=1) if start else None)
            )
            self._session.add(
                HrContract(
                    user_id=user.id,
                    number=probation[:80],
                    type_code="TV",
                    start_on=start,
                    end_on=end,
                    signed=_yes(cell("probation_signed")),
                )
            )
            self._report.contracts += 1
        number = cell("official_number")
        if number and _CONTRACT_NUMBER.search(number) and number not in existing:
            self._session.add(
                HrContract(
                    user_id=user.id,
                    number=number[:80],
                    type_code=UNKNOWN_TYPE[0],
                    start_on=self._day(cell("official_signed_on")) or official,
                    end_on=None,
                    signed=_yes(cell("official_signed")),
                    note="Nhập từ file nhân sự: kiểm tra lại loại / thời hạn hợp đồng.",
                )
            )
            self._report.contracts += 1

    async def _unknown_type(self) -> None:
        code, name = UNKNOWN_TYPE
        if await self._session.scalar(select(HrContractType.id).where(HrContractType.code == code)):
            return
        orders = (await self._session.scalars(select(HrContractType.sort_order))).all()
        self._session.add(
            HrContractType(code=code, name=name, months=0, sort_order=max(orders, default=-1) + 1)
        )
        await self._session.flush()


__all__ = ["HrProfileImport", "ProfileImportReport"]
