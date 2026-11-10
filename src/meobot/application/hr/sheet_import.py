"""Load the HR sheet's "Nhân sự" tab into the profiles (HR đợt 2, 0096).

Also reads HCNS's own workbook ("DANH SÁCH NHÂN SỰ", tab DS NHÂN SỰ, 0097):
team, both bank accounts (numbers sealed), time-clock id / day / floor, the
scan folder link, the paper documents ticked, the job title + salary on the
social-insurance file (salary sealed), the BHXH number and the notes. Accounts
(name, department, title, branch) are never changed here; the company is only
filled where the account has none.

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

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.hr.profiles import HrProfileService
from meobot.application.org.hr_import import entity_code_of, fold, parse_day
from meobot.db.models.hr_profile import HrContract, HrContractType, HrProfile, HrScanLink
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.hr.profiles import (
    DOCUMENT_KINDS,
    PAPER_DOCUMENTS,
    add_months,
    gender_of,
    split_scan_cell,
)
from meobot.domain.units.member_code import normalise_member_code

UNKNOWN_TYPE = ("HDLD", "HĐ lao động (chưa rõ thời hạn)")
_CONTRACT_NUMBER = re.compile(r"\d")

#: header words (folded) -> the field they fill. A header named twice ("STK",
#: "Ngân hàng", "Tên tài khoản") fills the first name, then the second.
#: Names in ``EXACT`` match the whole header only.
EXACT = frozenset(
    {
        "probation_signed",
        "official_signed",
        "bank_account",
        "bank_account_2",
        "team",
        "fingerprint_id",
        "paper:CCCD",
        "paper:DEGREE",
    }
)
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
    # DANH SÁCH NHÂN SỰ (0097)
    "team": ("team",),
    "left_on": ("ngay lam viec cuoi",),
    "leave_reason": ("ly do dung viec",),
    "fingerprint_id": ("van tay",),
    "fingerprint_on": ("ngay lay van tay",),
    "fingerprint_floor": ("tang lay van tay",),
    "bank_name": ("ngan hang",),
    "bank_account": ("stk",),
    "bank_holder": ("ten tai khoan",),
    "bank_account_2": ("stk",),
    "bank2_name": ("ngan hang",),
    "bank2_holder": ("ten tai khoan",),
    "scan": ("ho so scan",),
    "paper:CV": ("so yeu ly lich",),
    "paper:CCCD": ("cccd",),
    "paper:BIRTH": ("giay khai sinh",),
    "paper:VSSID": ("vssid",),
    "paper:ETAX": ("etax",),
    "paper:RESIDENCE": ("xac nhan cu tru",),
    "paper:HEALTH": ("giay kham suc khoe",),
    "paper:DEGREE": ("bang cap",),
    "paper:LICENSE": ("chung chi hanh nghe",),
    "note": ("ghi chu",),
    "insurance_entity": ("phap nhan dong bhxh",),
    "insurance_note": ("note li do",),
    "insurance_title": ("chuc danh tren bhxh",),
    "insurance_salary": ("muc luong", "bhxh"),
    "social_insurance_no": ("ma so bhxh",),
}


@dataclass
class ProfileImportReport:
    profiles: int = 0
    contracts: int = 0
    unknown_codes: list[str] = field(default_factory=list)
    bad_dates: int = 0
    columns: list[str] = field(default_factory=list)
    filled: dict[str, int] = field(default_factory=dict)
    companies_filled: int = 0

    def count(self, name: str) -> None:
        self.filled[name] = self.filled.get(name, 0) + 1

    def summary(self) -> dict[str, Any]:
        return {
            "profiles_updated": self.profiles,
            "contracts_created": self.contracts,
            "codes_not_found": self.unknown_codes,
            "unreadable_dates": self.bad_dates,
            "columns_read": len(self.columns),
            "companies_filled": self.companies_filled,
            "filled": dict(sorted(self.filled.items())),
        }


def _columns(header: list[str]) -> dict[str, int]:
    folded = [" ".join(fold(cell).split()) for cell in header]
    found: dict[str, int] = {}
    for name, words in COLUMNS.items():
        for index, cell in enumerate(folded):
            if index in found.values():
                continue
            if name in EXACT:
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


def _id_number(value: str) -> str:
    digits = re.sub(r"\s+", "", value)
    if digits.isdigit() and len(digits) == 11:
        # A 12-digit CCCD typed as a number lost its leading 0.
        digits = "0" + digits
    return digits


def _amount(value: str) -> int | None:
    digits = re.sub(r"[.,\s]", "", value)
    return int(digits) if digits.isdigit() else None


def cell_text(value: object) -> str:
    """A typed workbook value as the sheet would show it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime | date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def table_from_xlsx(data: bytes, sheet: str) -> list[list[str]]:
    """Every row of ``sheet`` as text (header rows included)."""
    # Not read-only: a cell's hidden hyperlink ("Link" pointing at Drive) only
    # shows in full mode; its target is added after the text when missing.
    book = load_workbook(io.BytesIO(data), data_only=True)
    try:
        table: list[list[str]] = []
        for row in book[sheet].iter_rows():
            texts: list[str] = []
            for cell in row:
                text = cell_text(cell.value)
                target = getattr(cell.hyperlink, "target", None) if cell.hyperlink else None
                if target and target not in text:
                    text = f"{text}\n{target}" if text else target
                texts.append(text)
            table.append(texts)
        return table
    finally:
        book.close()


class HrProfileImport:
    def __init__(self, session: AsyncSession, service: HrProfileService) -> None:
        self._session = session
        self._service = service
        self._report = ProfileImportReport()
        self._entities: dict[str, Any] = {}

    async def run(self, table: list[list[str]]) -> ProfileImportReport:
        header_at = next(
            index
            for index, row in enumerate(table)
            if any("ma" in fold(cell) and "nhan vien" in fold(cell) for cell in row[:4])
        )
        at = _columns(table[header_at])
        self._report.columns = sorted(at)
        self._entities = {
            row.code: row.id for row in (await self._session.scalars(select(LegalEntity))).all()
        }
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
            await self._extras(user, cell, at)
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
        id_number = _id_number(cell("id_number"))
        if id_number:
            secret = self._service._open_secret(profile) or {}
            secret["id_number"] = id_number
            self._service._seal_secret(profile, secret)
            profile.id_last4 = id_number[-4:]
        self._report.profiles += 1

    async def _extras(self, user: User, cell: Any, at: dict[str, int]) -> None:
        """The DANH SÁCH NHÂN SỰ columns (0097); a missing column changes nothing."""
        profile = await self._session.get(HrProfile, user.id)
        assert profile is not None
        limits = {
            "team": 120,
            "leave_reason": 1000,
            "fingerprint_id": 30,
            "fingerprint_floor": 30,
            "bank_name": 100,
            "bank_holder": 200,
            "bank2_name": 100,
            "bank2_holder": 200,
            "insurance_title": 200,
            "social_insurance_no": 30,
        }
        for name, limit in limits.items():
            if name not in at:
                continue
            value = cell(name)[:limit]
            if value:
                self._report.count(name)
                setattr(profile, name, value)
        for name in ("left_on", "fingerprint_on"):
            if name in at:
                day = self._day(cell(name))
                if day is not None:
                    self._report.count(name)
                    setattr(profile, name, day)
        if "scan" in at:
            await self._scan_links(user, profile, cell("scan"))
        papers = [code for code in PAPER_DOCUMENTS if f"paper:{code}" in at]
        if papers:
            ticked = {code for code in papers if _yes(cell(f"paper:{code}"))}
            kept = {code for code in (profile.paper_documents or []) if code not in papers}
            profile.paper_documents = [
                code for code in DOCUMENT_KINDS if code in ticked or code in kept
            ]
            for code in ticked:
                self._report.count(f"paper:{code}")
        if "note" in at or "insurance_note" in at or "insurance_entity" in at:
            lines = [
                cell("note"),
                f"Pháp nhân đóng BHXH: {cell('insurance_entity')}"
                if cell("insurance_entity")
                else "",
                f"Ghi chú BHXH: {cell('insurance_note')}" if cell("insurance_note") else "",
            ]
            note = "\n".join(line for line in lines if line)[:2000]
            if note:
                profile.note = note
        sealed: dict[str, object] = {}
        for name in ("bank_account", "bank_account_2"):
            if name in at:
                sealed[name] = re.sub(r"\s+", "", cell(name))[:40] or None
        if "insurance_salary" in at:
            sealed["insurance_salary"] = _amount(cell("insurance_salary"))
        if sealed:
            secret = self._service._open_secret(profile) or {}
            for name, value in sealed.items():
                if value is not None:
                    self._report.count(name)
                    secret[name] = value
            self._service._seal_secret(profile, secret)
            profile.bank_last4 = _last4(secret.get("bank_account"))
            profile.bank2_last4 = _last4(secret.get("bank_account_2"))
        if user.legal_entity_id is None:
            entity = entity_code_of(cell("insurance_entity"))
            if entity and entity in self._entities:
                user.legal_entity_id = self._entities[entity]
                self._report.companies_filled += 1

    async def _scan_links(self, user: User, profile: HrProfile, text: str) -> None:
        """0099: each link of "Hồ sơ scan" is a row (added once, never removed
        here) named by the words before it on its line; the rest is the note."""
        links, rest = split_scan_cell(text)
        if rest:
            profile.scan_note = rest[:1000]
        if not links:
            return
        existing = (
            await self._session.scalars(select(HrScanLink).where(HrScanLink.user_id == user.id))
        ).all()
        known = {row.url for row in existing}
        order = max((row.sort_order for row in existing), default=-1)
        for label, url in links:
            if url in known or len(url) > 2000:
                continue
            order += 1
            self._session.add(
                HrScanLink(
                    user_id=user.id,
                    label=None if label is None else label[:200],
                    url=url,
                    sort_order=order,
                )
            )
            known.add(url)
            self._report.count("scan_links")

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


def _last4(value: object) -> str | None:
    text = re.sub(r"\s+", "", str(value or ""))
    return text[-4:] if text else None


__all__ = ["HrProfileImport", "ProfileImportReport", "cell_text", "table_from_xlsx"]
