"""Phòng Ads's permissions (2026-11-02): who sees its dashboard.

The department works as before (``application/ads/access.py``): its Trưởng
phòng sees every marketer, a member their own numbers, the OWNER / an ADMIN
everything. On top of that the OWNER ticks ``DASHBOARD`` for anybody else -
per person on their "Quyền" page, or per department on "Phòng ban" - and they
see the whole Phòng Ads dashboard (every marketer, "Tổng team", Tài khoản QC)
**read only**: no revenue, KPI or budget typing, no account changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

MODULE_ADS = "ADS"


class AdsDepartmentPermission(StrEnum):
    #: See the Phòng Ads dashboard - every marketer's numbers, read only.
    DASHBOARD = "DASHBOARD"


ADS_PERMISSION_LABELS: Mapping[AdsDepartmentPermission, str] = MappingProxyType(
    {AdsDepartmentPermission.DASHBOARD: "Xem dashboard Phòng Ads (toàn phòng, chỉ xem)"}
)


def ads_allowed_scopes(_permission: AdsDepartmentPermission) -> tuple[str, ...]:
    """Ticked = the whole department's numbers: a member's own is their seat's."""
    return ("NONE", "ALL")


__all__ = [
    "ADS_PERMISSION_LABELS",
    "MODULE_ADS",
    "AdsDepartmentPermission",
    "ads_allowed_scopes",
]
