"""Doanh thu Phòng Ads từ CRM (0083; plan ``docs/ads-report/CRM_REVENUE_PLAN.md``).

The revenue is what customers actually paid (``/raw-data/order-payments``),
on the day they paid, summed per CRM marketing account. A person of the app
holds several CRM accounts. Two regions per payment (2026-10-30): the
account's (which marketing team brought the customer) and the payment's
branch (where they were served - "Doanh số SG" / "Doanh số HN"); HN team
served in HCM = "HN chuyển SG", HCM team served in HN = "SG chuyển HN".
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


class HotlineKind(StrEnum):
    """Which hotline a ◆ Hotline CRM account is (2026-10-30)."""

    FB = "FB"
    TIKTOK = "TIKTOK"
    GOOGLE = "GOOGLE"


HOTLINE_KIND_LABELS: dict[HotlineKind, str] = {
    HotlineKind.FB: "Facebook",
    HotlineKind.TIKTOK: "TikTok",
    HotlineKind.GOOGLE: "Google",
}


def hotline_kind_of(name: str | None) -> HotlineKind | None:
    """From the account's name: "Hotline FB" -> FB, "Hotline tiktok" -> TIKTOK."""
    words = set("".join(c if c.isalnum() else " " for c in _fold(name or "")).split())
    if words & {"FB", "FACEBOOK", "MESS", "MESSENGER"}:
        return HotlineKind.FB
    if words & {"TIKTOK", "TT"}:
        return HotlineKind.TIKTOK
    if words & {"GOOGLE", "GG"}:
        return HotlineKind.GOOGLE
    return None


def branch_site(name: str | None) -> CrmRegion | None:
    """A CRM branch's region from its name: "HN1", "HN2" -> HN, "SG" -> HCM."""
    folded = " ".join("".join(c if c.isalnum() else " " for c in _fold(name or "")).split())
    if folded.startswith("HN") or folded.startswith("HA NOI"):
        return CrmRegion.HN
    if folded.startswith(("SG", "HCM", "SAI GON", "HO CHI MINH")):
        return CrmRegion.HCM
    return None


#: The CRM's branches as of 30/10/2026 (``/raw-data/filters/orders``), used
#: until a sync has read them: 12 HN1, 1 HN2, 9 SG.
DEFAULT_BRANCH_SITES: dict[int, CrmRegion] = {
    12: CrmRegion.HN,
    1: CrmRegion.HN,
    9: CrmRegion.HCM,
}


__all__ = [
    "COUNTED_PAYMENT_TYPES",
    "DEFAULT_BRANCH_SITES",
    "HOTLINE_KIND_LABELS",
    "PAYMENT_POINTS",
    "REGION_LABELS",
    "CrmRegion",
    "HotlineKind",
    "branch_site",
    "hotline_kind_of",
    "region_of",
]
