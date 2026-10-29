"""Doanh thu Phòng Ads từ CRM (0083; plan ``docs/ads-report/CRM_REVENUE_PLAN.md``).

The revenue is what customers actually paid (``/raw-data/order-payments``),
on the day they paid, summed per CRM marketing account. A person of the app
holds several CRM accounts; each account's region says which "Doanh số" part
it fills: HCM -> SG, HN -> HN.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum


class CrmRegion(StrEnum):
    HN = "HN"
    HCM = "HCM"


REGION_LABELS: dict[CrmRegion, str] = {CrmRegion.HN: "Hà Nội", CrmRegion.HCM: "HCM"}

#: CRM payment types (``/raw-data/filters/order-payments``): 1 Tiền mặt, 2 Thẻ,
#: 3 Điểm, 4 Chuyển khoản, 5 Trả góp, 6 Nhà xe thu hộ, 7 Hoàn tiền (negative).
PAYMENT_POINTS = 3
#: Real money only: points are not cash. A refund is already negative.
COUNTED_PAYMENT_TYPES = frozenset({1, 2, 4, 5, 6, 7})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").upper()


def region_of(name: str | None) -> CrmRegion | None:
    """The region from the account name's first letters: "HN - MKT ..." -> HN,
    "SG - MKT ..." / "HCM-FB-..." -> HCM (after a leading "Đã nghỉ"); anything
    else -> unknown."""
    head = "".join(c if c.isalnum() else " " for c in _fold(name or "")).split()
    # "Đã nghỉ - HN-FB-..." keeps its region.
    if head[:2] == ["DA", "NGHI"]:
        head = head[2:]
    if not head:
        return None
    if head[0] == "HN":
        return CrmRegion.HN
    if head[0] in ("SG", "HCM"):
        return CrmRegion.HCM
    return None


__all__ = [
    "COUNTED_PAYMENT_TYPES",
    "PAYMENT_POINTS",
    "REGION_LABELS",
    "CrmRegion",
    "region_of",
]
