"""The points reports' permissions and vocabulary, with no I/O.

Who sees whose points (2026-10-23, ``docs/perf/PERFORMANCE_REPORTS_PLAN.md``):

* everybody their own;
* a Trưởng phòng also the people who report to them ("Trưởng quản lý" = them,
  in a department they head - ``org/departments.reports_of``);
* the whole company: the OWNER and an ADMIN - and whoever is granted
  ``VIEW ALL`` later (nobody by default, not even Ban Giám đốc);
* the Excel export: the OWNER and an ADMIN, or whoever holds ``EXPORT``;
* each member's points coefficient ("hệ số điểm", 2026-10-26): the OWNER
  alone for now - ``COEFFICIENT`` is OWNER-only: an ADMIN does not hold it by
  role and only the OWNER may grant it.

Points are counted by the department of the step's phase (a Dựng step in the
Dựng department), into the month the step was first done.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.org.models import DepartmentFunction, Position

MODULE_PERFORMANCE = "PERFORMANCE"


class PerformancePermission(StrEnum):
    #: See points: ``OWN`` = one's own and one's direct reports' (everybody's
    #: floor), ``ALL`` = the whole company.
    VIEW = "VIEW"
    #: Download the month's report as an Excel file.
    EXPORT = "EXPORT"
    #: Set a member's points coefficient ("hệ số điểm"). OWNER-only.
    COEFFICIENT = "COEFFICIENT"


#: Held by the OWNER alone by role, and granted by the OWNER alone.
PERFORMANCE_OWNER_ONLY: frozenset[str] = frozenset({PerformancePermission.COEFFICIENT.value})

#: A coefficient's bounds and the default when none is set.
COEFFICIENT_DEFAULT = "1"
COEFFICIENT_MIN = "0.01"
COEFFICIENT_MAX = "10"


PERFORMANCE_PERMISSION_LABELS: Mapping[PerformancePermission, str] = MappingProxyType(
    {
        PerformancePermission.VIEW: "Xem điểm hiệu suất",
        PerformancePermission.EXPORT: "Xuất Excel báo cáo điểm",
        PerformancePermission.COEFFICIENT: "Cấu hình hệ số điểm",
    }
)

PERFORMANCE_OWN_MEANING: Mapping[PerformancePermission, str] = MappingProxyType(
    {
        PerformancePermission.VIEW: (
            "ai cũng xem điểm của mình; trưởng phòng thêm nhân viên báo cáo trực tiếp cho mình"
            ' (Trưởng quản lý). Chọn "Toàn bộ" để xem điểm toàn công ty.'
        ),
    }
)

PERFORMANCE_SCOPE_LABELS: Mapping[PerformancePermission, Mapping[str, str]] = MappingProxyType(
    {
        PerformancePermission.VIEW: MappingProxyType(
            {"OWN": "Mặc định (của mình, nhân viên của mình)", "ALL": "Toàn công ty"}
        ),
    }
)


def performance_allowed_scopes(permission: PerformancePermission) -> tuple[str, ...]:
    if permission is PerformancePermission.VIEW:
        # One's own (and one's reports') is the floor: nothing below it.
        return ("OWN", "ALL")
    return ("NONE", "ALL")


def default_performance_grants(function: DepartmentFunction) -> dict[Position, dict[str, str]]:
    """Nothing by default (2026-10-23): the company view is the OWNER's and an
    ADMIN's until somebody is granted it."""
    del function
    return {}


__all__ = [
    "COEFFICIENT_DEFAULT",
    "COEFFICIENT_MAX",
    "COEFFICIENT_MIN",
    "MODULE_PERFORMANCE",
    "PERFORMANCE_OWNER_ONLY",
    "PERFORMANCE_OWN_MEANING",
    "PERFORMANCE_PERMISSION_LABELS",
    "PERFORMANCE_SCOPE_LABELS",
    "PerformancePermission",
    "default_performance_grants",
    "performance_allowed_scopes",
]
