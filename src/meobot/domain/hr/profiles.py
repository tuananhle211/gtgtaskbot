"""Hồ sơ nhân sự (HR đợt 2, ``docs/hr/HR_MODULE_PLAN.md`` §5): the vocabulary.

Pure - no database. The permission module ``HR`` ("Nhân sự"):

* ``VIEW`` - see profiles' work information. ``OWN`` = the people seated in
  the departments one heads (every Trưởng phòng by default), ``ALL`` = every
  account (Phòng HCNS).
* ``EDIT`` - add and edit profiles, contracts, documents, bulk edits, decide
  the staff's own change requests.
* ``SENSITIVE`` - ID number, phone, birthday, personal email, addresses, bank
  account, salary, insurance and tax (Q11: HCNS + the OWNER + oneself). The
  OWNER grants it alone and an ADMIN does not hold it by role.
* ``EXPORT`` - the Excel file.

Everybody always sees their own whole profile.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.org.models import DepartmentFunction, Position

MODULE_HR = "HR"


class HrPermission(StrEnum):
    VIEW = "VIEW"
    EDIT = "EDIT"
    SENSITIVE = "SENSITIVE"
    EXPORT = "EXPORT"


HR_PERMISSION_LABELS: Mapping[HrPermission, str] = MappingProxyType(
    {
        HrPermission.VIEW: "Xem hồ sơ nhân sự",
        HrPermission.EDIT: "Thêm / sửa hồ sơ, hợp đồng, tài liệu; duyệt yêu cầu sửa",
        HrPermission.SENSITIVE: "Xem dữ liệu nhạy cảm (CCCD, SĐT, ngày sinh, lương, bảo hiểm)",
        HrPermission.EXPORT: "Xuất Excel hồ sơ",
    }
)
HR_OWN_MEANING: Mapping[HrPermission, str] = MappingProxyType(
    {HrPermission.VIEW: "Người trong các phòng mình làm trưởng phòng"}
)
HR_OWNER_ONLY: frozenset[str] = frozenset({HrPermission.SENSITIVE.value})


def hr_allowed_scopes(permission: HrPermission) -> tuple[str, ...]:
    if permission is HrPermission.VIEW:
        return ("NONE", "OWN", "ALL")
    return ("NONE", "ALL")


def default_hr_grants(function: DepartmentFunction) -> dict[Position, dict[str, str]]:
    """Every Trưởng phòng sees the work information of their departments'
    people. The HCNS department's full set is seeded by 0096 by its code."""
    del function
    return {Position.HEAD: {HrPermission.VIEW.value: "OWN"}}


#: The department whose people run the module (seeded with every HR grant).
HR_DEPARTMENT_CODE = "HCNS"

# --- the profile's words -----------------------------------------------------------------

GENDERS: Mapping[str, str] = MappingProxyType({"MALE": "Nam", "FEMALE": "Nữ", "OTHER": "Khác"})


def gender_of(text: str | None) -> str | None:
    value = (text or "").strip().casefold()
    if not value:
        return None
    if value in ("nam", "male", "m"):
        return "MALE"
    if value in ("nữ", "nu", "female", "f"):
        return "FEMALE"
    return "OTHER" if value in ("khác", "khac", "other") else None


MARITAL: tuple[str, ...] = ("Độc thân", "Đã kết hôn", "Khác")
WORK_FORMS: tuple[str, ...] = ("Toàn thời gian", "Bán thời gian", "Cộng tác viên", "Thực tập")
SALARY_TYPES: tuple[str, ...] = ("Lương tháng", "Lương ngày", "Lương giờ", "Lương khoán")

DOCUMENT_KINDS: Mapping[str, str] = MappingProxyType(
    {
        "CV": "Sơ yếu lý lịch / CV",
        "CCCD": "CCCD / CMND",
        "BIRTH": "Giấy khai sinh",
        "VSSID": "VSSID (quá trình đóng BHXH)",
        "ETAX": "ETAX (thông tin thuế TNCN)",
        "RESIDENCE": "Xác nhận cư trú / VNeID",
        "HEALTH": "Giấy khám sức khoẻ",
        "DEGREE": "Bằng cấp",
        "LICENSE": "Chứng chỉ hành nghề",
        "CONTRACT": "Hợp đồng lao động",
        "PHOTO": "Ảnh thẻ",
        "OTHER": "Khác",
    }
)
#: The paper file HCNS keeps per person (the master file's "HỒ SƠ NHÂN SỰ"
#: ticks, 0097) - a kind counts as handed in when ticked or uploaded.
PAPER_DOCUMENTS: tuple[str, ...] = (
    "CV",
    "CCCD",
    "BIRTH",
    "VSSID",
    "ETAX",
    "RESIDENCE",
    "HEALTH",
    "DEGREE",
    "LICENSE",
)
#: What a complete file holds by default ("thiếu tài liệu"); editable on "Danh mục".
DEFAULT_REQUIRED_DOCUMENTS: tuple[str, ...] = (
    "CV",
    "CCCD",
    "BIRTH",
    "RESIDENCE",
    "HEALTH",
    "DEGREE",
)
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

#: Q13: what a person asks HCNS to change on their own profile.
REQUEST_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "phone": "Điện thoại",
        "personal_email": "Email cá nhân",
        "current_address": "Chỗ ở hiện tại",
        "permanent_address": "Hộ khẩu thường trú",
    }
)


class RequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


REQUEST_STATUS_LABELS: Mapping[RequestStatus, str] = MappingProxyType(
    {
        RequestStatus.PENDING: "Chờ duyệt",
        RequestStatus.APPROVED: "Đã duyệt",
        RequestStatus.REJECTED: "Từ chối",
        RequestStatus.CANCELLED: "Đã huỷ",
    }
)

# --- contracts --------------------------------------------------------------------------

#: (code, name, months - 0 = indefinite) the mock's contract types.
CONTRACT_TYPE_SEED: tuple[tuple[str, str, int], ...] = (
    ("TV", "HĐ thử việc", 2),
    ("XD12", "HĐ xác định thời hạn 12 tháng", 12),
    ("XD36", "HĐ xác định thời hạn 36 tháng", 36),
    ("KXD", "HĐ không xác định thời hạn", 0),
    ("KV", "HĐ khoán việc", 6),
    ("CTV", "HĐ cộng tác viên", 12),
)
DEFAULT_WARN_DAYS = 30


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


def contract_end(start: date, months: int) -> date | None:
    """The last day of a contract of ``months`` from ``start``; ``None`` = indefinite."""
    if months <= 0:
        return None
    return add_months(start, months) - timedelta(days=1)


class ContractState(StrEnum):
    UPCOMING = "UPCOMING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    ENDED = "ENDED"


CONTRACT_STATE_LABELS: Mapping[ContractState, str] = MappingProxyType(
    {
        ContractState.UPCOMING: "Chưa hiệu lực",
        ContractState.ACTIVE: "Đang hiệu lực",
        ContractState.EXPIRED: "Đã hết hạn",
        ContractState.ENDED: "Đã chấm dứt",
    }
)


def contract_state(
    start: date | None, end: date | None, ended_on: date | None, today: date
) -> ContractState:
    if ended_on is not None and ended_on <= today:
        return ContractState.ENDED
    if start is not None and start > today:
        return ContractState.UPCOMING
    if end is not None and end < today:
        return ContractState.EXPIRED
    return ContractState.ACTIVE


def seniority(joined: date | None, until: date) -> str | None:
    """ "2 năm 3 tháng" from the day they joined."""
    if joined is None or joined > until:
        return None
    months = (until.year - joined.year) * 12 + until.month - joined.month
    if until.day < joined.day:
        months -= 1
    years, rest = divmod(max(months, 0), 12)
    parts = ([f"{years} năm"] if years else []) + ([f"{rest} tháng"] if rest else [])
    return " ".join(parts) or "Dưới 1 tháng"


def age(birth: date | None, today: date) -> int | None:
    if birth is None:
        return None
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


__all__ = [
    "CONTRACT_STATE_LABELS",
    "CONTRACT_TYPE_SEED",
    "DEFAULT_REQUIRED_DOCUMENTS",
    "DEFAULT_WARN_DAYS",
    "DOCUMENT_KINDS",
    "GENDERS",
    "HR_DEPARTMENT_CODE",
    "HR_OWNER_ONLY",
    "HR_OWN_MEANING",
    "HR_PERMISSION_LABELS",
    "MARITAL",
    "MAX_DOCUMENT_BYTES",
    "MODULE_HR",
    "PAPER_DOCUMENTS",
    "REQUEST_FIELDS",
    "REQUEST_STATUS_LABELS",
    "SALARY_TYPES",
    "WORK_FORMS",
    "ContractState",
    "HrPermission",
    "RequestStatus",
    "add_months",
    "age",
    "contract_end",
    "contract_state",
    "default_hr_grants",
    "gender_of",
    "hr_allowed_scopes",
    "seniority",
]
