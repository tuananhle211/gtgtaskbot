"""Resources ("Quản lý tài nguyên", đợt 1): the words and the rules.

The company's accounts and channels - Via, Page, Nhóm, AI tools, TKQC, BM,
others - as the Phòng Tài nguyên keeps them instead of an Excel inventory
(``docs/resources/RESOURCE_MANAGEMENT_PLAN.md``). What this module fixes:

* the **permissions** the departments grant (module ``RESOURCES``), with
  the defaults a department starts with by its function;
* the **pick-lists** (type, role, status, origin, two-factor, handover) and
  what they are seeded with. They are data: the Phòng Tài nguyên adds and
  renames entries on its settings screen; the stable ``code`` is what the
  Excel import and the reports match on;
* the **status groups** a status belongs to (normal / attention / lost /
  closed), so a report stays right when a status is added;
* the **alerts** a row can carry and which of them paint it red;
* the shape of a resource code (``ADS-001``) and the detection of text that
  looks like a password - the one thing this module must never store.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.org.models import DepartmentFunction, Position

#: The module name in ``department_permissions``.
MODULE_RESOURCES = "RESOURCES"


# --- permissions ----------------------------------------------------------------------


class ResourcePermission(StrEnum):
    #: See resources: ``OWN`` = what one holds (and, as a head, one's
    #: department's); ``ALL`` = every resource.
    VIEW = "VIEW"
    #: Add a resource (2026-10-21: its own permission - the Phòng Tài nguyên).
    CREATE = "CREATE"
    #: Delete a resource (0071; soft - its histories stay). The Phòng Tài nguyên.
    DELETE = "DELETE"
    #: Edit EVERY resource, change the holder, update the status, review.
    #: Without it a person still edits / updates / reviews what they hold,
    #: and a department head what their department's people hold.
    EDIT = "EDIT"
    #: Import the Excel inventory.
    IMPORT = "IMPORT"
    #: The pick-lists and the review threshold.
    SETTINGS = "SETTINGS"
    #: The summary report, the warning list, the look-up by employee.
    REPORT = "REPORT"
    #: Update a person's employment status (Đang làm / Thử việc / ...).
    HR_STATUS = "HR_STATUS"


class ResourceScope(StrEnum):
    NONE = "NONE"
    OWN = "OWN"
    ALL = "ALL"


RESOURCE_PERMISSION_LABELS: Mapping[ResourcePermission, str] = MappingProxyType(
    {
        ResourcePermission.VIEW: "Xem tài nguyên",
        ResourcePermission.CREATE: "Thêm tài nguyên",
        ResourcePermission.DELETE: "Xoá tài nguyên",
        ResourcePermission.EDIT: "Sửa mọi tài nguyên, đổi người giữ, cập nhật trạng thái, rà soát",
        ResourcePermission.IMPORT: "Nhập từ Excel",
        ResourcePermission.SETTINGS: "Cài đặt danh sách lựa chọn, ngưỡng rà soát",
        ResourcePermission.REPORT: "Báo cáo tổng hợp, tra cứu theo nhân viên",
        ResourcePermission.HR_STATUS: "Cập nhật trạng thái nhân sự",
    }
)

RESOURCE_OWN_MEANING: Mapping[ResourcePermission, str] = MappingProxyType(
    {
        ResourcePermission.VIEW: (
            "ai cũng xem, sửa, cập nhật trạng thái tài nguyên mình đang giữ; trưởng phòng"
            " thêm tài nguyên của các thành viên được gắn với mình (Trưởng quản lý)."
            ' Chọn "Toàn bộ" để xem mọi tài nguyên của công ty.'
        ),
    }
)

#: The words of a scope where the generic ones mislead (2026-10-21): VIEW's
#: OWN is the floor everybody has, not a grant one may withhold.
RESOURCE_SCOPE_LABELS: Mapping[ResourcePermission, Mapping[str, str]] = MappingProxyType(
    {
        ResourcePermission.VIEW: MappingProxyType(
            {"OWN": "Mặc định (của mình, người gắn với mình)", "ALL": "Toàn bộ"}
        ),
    }
)


def resource_allowed_scopes(permission: ResourcePermission) -> tuple[ResourceScope, ...]:
    if permission is ResourcePermission.VIEW:
        # One's own (and, as a head, one's department's) is everybody's floor:
        # there is nothing below it to choose.
        return (ResourceScope.OWN, ResourceScope.ALL)
    return (ResourceScope.NONE, ResourceScope.ALL)


_ALL = ResourceScope.ALL.value
_OWN = ResourceScope.OWN.value


#: What a department grants in this module when nothing was decided for it:
#: by its function and the seat's position. The Phòng Tài nguyên runs the
#: module; Ban Giám đốc sees everything and the reports; every other
#: department sees its own (a head their department's, a member what they hold).
#: A department created later gets these too ("phòng thêm sau tự có quyền").
def default_resource_grants(function: DepartmentFunction) -> dict[Position, dict[str, str]]:
    if function is DepartmentFunction.RESOURCES:
        return {
            Position.HEAD: {permission.value: _ALL for permission in ResourcePermission},
            Position.MEMBER: {
                ResourcePermission.VIEW.value: _ALL,
                ResourcePermission.CREATE.value: _ALL,
                ResourcePermission.DELETE.value: _ALL,
                ResourcePermission.EDIT.value: _ALL,
                ResourcePermission.REPORT.value: _ALL,
            },
        }
    if function is DepartmentFunction.BOARD:
        board = {ResourcePermission.VIEW.value: _ALL, ResourcePermission.REPORT.value: _ALL}
        return {Position.HEAD: dict(board), Position.MEMBER: dict(board)}
    return {
        Position.HEAD: {ResourcePermission.VIEW.value: _OWN},
        Position.MEMBER: {ResourcePermission.VIEW.value: _OWN},
    }


# --- pick-lists -----------------------------------------------------------------------


class OptionKind(StrEnum):
    TYPE = "TYPE"
    ROLE = "ROLE"
    STATUS = "STATUS"
    ORIGIN = "ORIGIN"
    TWO_FACTOR = "TWO_FACTOR"
    HANDOVER = "HANDOVER"


OPTION_KIND_LABELS: Mapping[OptionKind, str] = MappingProxyType(
    {
        OptionKind.TYPE: "Loại tài nguyên",
        OptionKind.ROLE: "Quyền đang có",
        OptionKind.STATUS: "Trạng thái tài nguyên",
        OptionKind.ORIGIN: "Nguồn gốc",
        OptionKind.TWO_FACTOR: "Bảo mật 2 lớp",
        OptionKind.HANDOVER: "Bàn giao về Tài nguyên",
    }
)


class StatusGroup(StrEnum):
    """What a status means to the reports, whatever it is called."""

    NORMAL = "NORMAL"
    ATTENTION = "ATTENTION"
    LOST = "LOST"
    CLOSED = "CLOSED"


STATUS_GROUP_LABELS: Mapping[StatusGroup, str] = MappingProxyType(
    {
        StatusGroup.NORMAL: "Bình thường",
        StatusGroup.ATTENTION: "Cần chú ý",
        StatusGroup.LOST: "Mất",
        StatusGroup.CLOSED: "Đã đóng",
    }
)

#: (kind, code, name, status group or None), in display order. The codes are
#: what the import matches and the reports count on; the names are what
#: people read and may rename.
SEED_OPTIONS: tuple[tuple[OptionKind, str, str, StatusGroup | None], ...] = (
    # Loại tài nguyên - Phòng Tài nguyên's catalogue sheet (2026-10-16).
    (OptionKind.TYPE, "VIA", "Via", None),
    (OptionKind.TYPE, "PAGE", "Page", None),
    (OptionKind.TYPE, "GROUP", "Nhóm", None),
    (OptionKind.TYPE, "AI", "AI", None),
    (OptionKind.TYPE, "TKQC", "TKQC", None),
    (OptionKind.TYPE, "BM", "BM", None),
    (OptionKind.TYPE, "CAPCUT", "CapCut", None),
    (OptionKind.TYPE, "ADOBE", "Adobe", None),
    (OptionKind.TYPE, "VPS", "VPS", None),
    (OptionKind.TYPE, "TIKTOK_CHANNEL", "Kênh Tiktok", None),
    (OptionKind.TYPE, "TIKTOK_BC", "BC Tiktok", None),
    (OptionKind.TYPE, "TIKTOK_TKQC", "TKQC Tiktok", None),
    (OptionKind.TYPE, "DOMAIN", "Tên Miền", None),
    (OptionKind.TYPE, "MAGNIFIC", "Magnific", None),
    (OptionKind.TYPE, "GMAIL", "Gmail", None),
    (OptionKind.TYPE, "WEBCAKE", "Webcake", None),
    (OptionKind.TYPE, "FIGMA", "Figma", None),
    (OptionKind.TYPE, "SKETCHUP", "Sketchup", None),
    (OptionKind.TYPE, "YOUTUBE", "Youtube", None),
    (OptionKind.TYPE, "OTHER", "Khác", None),
    (OptionKind.ROLE, "OWNER_ADMIN", "Chủ sở hữu / Admin", None),
    (OptionKind.ROLE, "EDITOR", "Biên tập viên", None),
    (OptionKind.ROLE, "MODERATOR", "Kiểm duyệt viên", None),
    (OptionKind.ROLE, "ADVERTISER", "Nhà quảng cáo", None),
    (OptionKind.ROLE, "ANALYST", "Phân tích", None),
    (OptionKind.ROLE, "MEMBER", "Thành viên", None),
    (OptionKind.ROLE, "ACCOUNT_OWNER", "Chủ tài khoản (Via/AI)", None),
    (OptionKind.ROLE, "SHARED", "Được chia sẻ dùng chung", None),
    (OptionKind.ROLE, "OTHER", "Khác", None),
    (OptionKind.STATUS, "ACTIVE", "Hoạt động", StatusGroup.NORMAL),
    (OptionKind.STATUS, "RESTRICTED", "Hạn chế", StatusGroup.ATTENTION),
    (OptionKind.STATUS, "CHECKPOINT", "Checkpoint", StatusGroup.ATTENTION),
    # 2026-10-22: "Die / Mất" split in two; the old entry (and its data) is "Die".
    (OptionKind.STATUS, "DIE", "Die", StatusGroup.LOST),
    (OptionKind.STATUS, "LOST", "Mất", StatusGroup.LOST),
    (OptionKind.STATUS, "RETURNED", "Đã thu hồi", StatusGroup.CLOSED),
    (OptionKind.STATUS, "STOPPED", "Ngừng dùng", StatusGroup.CLOSED),
    (OptionKind.ORIGIN, "COMPANY", "Công ty cấp", None),
    (OptionKind.ORIGIN, "SELF", "Tự tạo", None),
    (OptionKind.ORIGIN, "BOUGHT", "Mua ngoài", None),
    (OptionKind.ORIGIN, "RENTED", "Thuê", None),
    (OptionKind.ORIGIN, "INHERITED", "Nhận lại từ người khác", None),
    (OptionKind.ORIGIN, "UNKNOWN", "Không rõ", None),
    (OptionKind.TWO_FACTOR, "YES", "Có", None),
    (OptionKind.TWO_FACTOR, "NO", "Không", None),
    (OptionKind.TWO_FACTOR, "UNKNOWN", "Không rõ", None),
    (OptionKind.HANDOVER, "NOT_YET", "Chưa bàn giao", None),
    (OptionKind.HANDOVER, "IN_PROGRESS", "Đang bàn giao", None),
    (OptionKind.HANDOVER, "DONE", "Đã bàn giao", None),
)

#: The status a new resource gets, and the handover state, unless chosen.
DEFAULT_STATUS_CODE = "ACTIVE"
DEFAULT_HANDOVER_CODE = "NOT_YET"
#: The handover state that counts as "handed over" in the department x type report.
HANDED_OVER_CODE = "DONE"


# --- handovers and people -------------------------------------------------------------


class HandoverReason(StrEnum):
    NEW = "NEW"
    TRANSFER = "TRANSFER"
    RETURN = "RETURN"


HANDOVER_REASON_LABELS: Mapping[HandoverReason, str] = MappingProxyType(
    {
        HandoverReason.NEW: "Cấp mới",
        HandoverReason.TRANSFER: "Chuyển người",
        HandoverReason.RETURN: "Thu hồi",
    }
)


class EmploymentStatus(StrEnum):
    """A person's standing with the company ("trạng thái nhân sự"), on
    every account. Distinct from the account's own lifecycle
    (:class:`~meobot.domain.access.models.UserStatus`): somebody on leave
    still signs in; somebody who left is listed so what they hold can be
    recalled."""

    WORKING = "WORKING"
    PROBATION = "PROBATION"
    ON_LEAVE = "ON_LEAVE"
    #: "Chuyển ban": moved to another department - a hand-over of what they
    #: hold is due, like a leaver's, but they are still with the company.
    MOVED = "MOVED"
    LEFT = "LEFT"


EMPLOYMENT_STATUS_LABELS: Mapping[EmploymentStatus, str] = MappingProxyType(
    {
        EmploymentStatus.WORKING: "Đang làm",
        EmploymentStatus.PROBATION: "Thử việc",
        EmploymentStatus.ON_LEAVE: "Tạm nghỉ",
        EmploymentStatus.MOVED: "Chuyển ban",
        EmploymentStatus.LEFT: "Đã nghỉ việc",
    }
)


# --- alerts ---------------------------------------------------------------------------


class AlertKind(StrEnum):
    #: The holder has left the company.
    HOLDER_LEFT = "HOLDER_LEFT"
    #: The holder's account is deactivated or deleted.
    HOLDER_INACTIVE = "HOLDER_INACTIVE"
    #: The holder no longer sits in the department that manages the resource ("chuyển ban").
    HOLDER_MOVED = "HOLDER_MOVED"
    #: Not reviewed for longer than the threshold.
    REVIEW_OVERDUE = "REVIEW_OVERDUE"
    #: The status needs attention (Hạn chế, Checkpoint...).
    STATUS_ATTENTION = "STATUS_ATTENTION"
    #: The status is a loss (Die, Mất).
    STATUS_LOST = "STATUS_LOST"


ALERT_LABELS: Mapping[AlertKind, str] = MappingProxyType(
    {
        AlertKind.HOLDER_LEFT: "Người giữ đã nghỉ việc",
        AlertKind.HOLDER_INACTIVE: "Tài khoản người giữ bị vô hiệu hoặc đã xoá",
        AlertKind.HOLDER_MOVED: "Người giữ không còn ở phòng quản lý (chuyển ban)",
        AlertKind.REVIEW_OVERDUE: "Quá hạn rà soát",
        AlertKind.STATUS_ATTENTION: "Trạng thái cần chú ý",
        AlertKind.STATUS_LOST: "Tài nguyên đã mất",
    }
)

#: The alerts that paint a row red (rule 6 of the requirement, plus a loss);
#: the others are amber.
RED_ALERTS: frozenset[AlertKind] = frozenset(
    {
        AlertKind.HOLDER_LEFT,
        AlertKind.HOLDER_INACTIVE,
        AlertKind.HOLDER_MOVED,
        AlertKind.STATUS_LOST,
    }
)

#: "Quá N ngày chưa rà soát", unless the settings say otherwise.
DEFAULT_REVIEW_DAYS = 30


# --- codes and secrets ----------------------------------------------------------------


def format_resource_code(prefix: str, number: int) -> str:
    """``ADS-001``: the department's code and a counter of its own."""
    return f"{prefix}-{number:03d}"


_CODE = re.compile(r"^[A-Z0-9]{1,12}-\d{1,6}$")


def is_resource_code(value: str) -> bool:
    return bool(_CODE.match(value))


_SECRET_LIKE = re.compile(
    r"(pass\s*[:=]|password|passwd|m[aậ]t\s*kh[aẩ]u|mk\s*[:=]|"
    r"m[aã]\s*kh[oô]i\s*ph[uụ]c|recovery\s*code|backup\s*code|2fa\s*[:=]|otp\s*[:=]|secret)",
    re.IGNORECASE,
)


def looks_like_secret(text: str | None) -> bool:
    """Whether free text reads like a password, a 2FA or a recovery code -
    the one thing the module must never hold (rule 1). A warning, not a
    refusal: the person decides."""
    return bool(text) and bool(_SECRET_LIKE.search(text or ""))


__all__ = [
    "ALERT_LABELS",
    "DEFAULT_HANDOVER_CODE",
    "DEFAULT_REVIEW_DAYS",
    "DEFAULT_STATUS_CODE",
    "EMPLOYMENT_STATUS_LABELS",
    "HANDED_OVER_CODE",
    "HANDOVER_REASON_LABELS",
    "MODULE_RESOURCES",
    "OPTION_KIND_LABELS",
    "RED_ALERTS",
    "RESOURCE_OWN_MEANING",
    "RESOURCE_PERMISSION_LABELS",
    "RESOURCE_SCOPE_LABELS",
    "SEED_OPTIONS",
    "STATUS_GROUP_LABELS",
    "AlertKind",
    "EmploymentStatus",
    "HandoverReason",
    "OptionKind",
    "ResourcePermission",
    "ResourceScope",
    "StatusGroup",
    "default_resource_grants",
    "format_resource_code",
    "is_resource_code",
    "looks_like_secret",
    "resource_allowed_scopes",
]
