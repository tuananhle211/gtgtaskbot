"""The rules of the Phòng Ads module (0078, plan ``docs/ads-report/ADS_DEPARTMENT_PLAN.md``).

Ad spend, messages and phone numbers come from the Meta Marketing API per ad
account and day; Meta's billing charges ("hoá đơn") per account and day too.
Everything a screen shows is computed here from those rows, with the rules the
department's Google Sheet used (``docs/report/BaoCaoCaNhan.gs``):

* the **brand** ("nguồn ngân sách") of an account is read from its name -
  COREMIND, then GPI, then APEX, else other (``_nguonTK``);
* **"Số tiền lập hoá đơn"** = Meta's charge x VAT (10% from 07/2026 for the
  three brands) x the card / rental fee by account group (``heSoVATLHD``,
  ``_phiTheLHD``);
* a charge **matches** the spend when they differ by at most 1% (or 10.000 ₫).

Pure: no database.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

#: The department whose people see the module (``departments.code``).
ADS_DEPARTMENT_CODE = "ADS"

#: Meta action types the Sheet counted (``Mã.gs``): "SĐT" was the purchase
#: event the forms send, "Mess" the conversations started.
ACTION_PHONES = "omni_purchase"
ACTION_MESSAGES = "onsite_conversion.messaging_conversation_started_7d"


class AdsBrand(StrEnum):
    COREMIND = "COREMIND"
    APEX = "APEX"
    GPI = "GPI"
    OTHER = "OTHER"


BRAND_LABELS: dict[AdsBrand, str] = {
    AdsBrand.COREMIND: "COREMIND",
    AdsBrand.APEX: "APEX",
    AdsBrand.GPI: "GPI",
    AdsBrand.OTHER: "Khác",
}

#: A charge matches the spend within 1%, or 10.000 ₫ for small accounts.
MATCH_RATIO = Decimal("0.01")
MATCH_FLOOR = Decimal("10000")
#: "Kéo lại khoảng ngày" goes back at most this far in one go.
MAX_SYNC_DAYS = 93


def fold(text: str | None) -> str:
    """Upper case, no diacritics, single spaces - the Sheet's ``chuanHoaTxt``."""
    value = unicodedata.normalize("NFD", (text or "").upper().replace("Đ", "D"))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return " ".join(value.split())


def brand_of(account_name: str | None) -> AdsBrand:
    folded = fold(account_name)
    if "COREMIND" in folded:
        return AdsBrand.COREMIND
    if "GPI" in folded:
        return AdsBrand.GPI
    if "APEX" in folded:
        return AdsBrand.APEX
    return AdsBrand.OTHER


def invoice_factor(account_name: str | None, brand: AdsBrand, day: date) -> Decimal:
    """What a Meta charge is multiplied by to give "Số tiền lập hoá đơn":
    1 (chốt 27/10: no VAT, no card fee - the amount Meta charged, by day and
    by month alike). Kept as the one place to change should a factor return."""
    return Decimal(1)


def matches(spend: Decimal, charged: Decimal) -> bool:
    tolerance = max(spend * MATCH_RATIO, MATCH_FLOOR)
    return abs(charged - spend) <= tolerance


def ratio(numerator: Decimal | int | float, denominator: Decimal | int | float) -> float | None:
    """``None`` instead of a division by zero: the screen shows "—"."""
    if not denominator:
        return None
    return float(numerator) / float(denominator)


def change(current: float | None, previous: float | None) -> float | None:
    """Relative change against the previous period, ``None`` when there is none."""
    if current is None or not previous:
        return None
    return current / previous - 1


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    #: Inclusive.
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        """The same number of days just before."""
        end = self.start - timedelta(days=1)
        return Period(start=end - timedelta(days=self.days - 1), end=end)


class RevenueSource(StrEnum):
    """Revenue the Trưởng phòng enters that is nobody's ad account (the
    Sheet's ◆ rows): it counts for the team, not for a marketer."""

    HOTLINE = "HOTLINE"
    HN = "HN"
    GOOGLE = "GOOGLE"
    TIKTOK = "TIKTOK"
    SEEDING = "SEEDING"
    UNASSIGNED = "UNASSIGNED"


REVENUE_SOURCE_LABELS: dict[RevenueSource, str] = {
    RevenueSource.HOTLINE: "Hotline",
    RevenueSource.HN: "HN chuyển HCM",
    RevenueSource.GOOGLE: "Google",
    RevenueSource.TIKTOK: "Tiktok",
    RevenueSource.SEEDING: "Seeding",
    RevenueSource.UNASSIGNED: "Chưa gán Marketing",
}

#: Revenue is entered as the reconciliation sheet shows it, VAT included
#: ("Doanh số xét KPI"). From 07/2026 the commission revenue ("Doanh thu hoa
#: hồng", what Ads / Doanh thu divides by) is that figure without the 8% VAT.
REVENUE_VAT_FROM = date(2026, 7, 1)
REVENUE_VAT = Decimal("1.08")


def commission_revenue(gross: Decimal, day: date) -> Decimal:
    """ "Doanh thu hoa hồng" from the entered ("xét KPI") revenue of ``day``."""
    return gross / REVENUE_VAT if day >= REVENUE_VAT_FROM else gross


#: Ads / Doanh thu, the colour bands of the design: ≤ 40% good, ≤ 60% watch,
#: above that act now.
ADS_RATIO_GOOD = 0.40
ADS_RATIO_WATCH = 0.60


def ads_ratio_tone(value: float | None) -> str | None:
    """``good`` / ``watch`` / ``bad`` for an Ads / Doanh thu ratio."""
    if value is None:
        return None
    if value <= ADS_RATIO_GOOD:
        return "good"
    if value <= ADS_RATIO_WATCH:
        return "watch"
    return "bad"


def subject_key(*, user_id: object | None = None, source: RevenueSource | None = None) -> str:
    """One revenue / KPI line: a marketer (``u:<id>``), a ◆ source
    (``s:HOTLINE``) or, for a KPI, the whole team (``team``)."""
    if user_id is not None:
        return f"u:{user_id}"
    if source is not None:
        return f"s:{source.value}"
    return "team"


class AdsGrouping(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


def bucket(day: date, grouping: AdsGrouping) -> date:
    """The first day of the bucket ``day`` falls in (weeks start on Monday)."""
    if grouping is AdsGrouping.WEEK:
        return day - timedelta(days=day.weekday())
    if grouping is AdsGrouping.MONTH:
        return day.replace(day=1)
    return day


__all__ = [
    "ACTION_MESSAGES",
    "ACTION_PHONES",
    "ADS_DEPARTMENT_CODE",
    "ADS_RATIO_GOOD",
    "ADS_RATIO_WATCH",
    "BRAND_LABELS",
    "MAX_SYNC_DAYS",
    "REVENUE_SOURCE_LABELS",
    "REVENUE_VAT",
    "AdsBrand",
    "AdsGrouping",
    "Period",
    "RevenueSource",
    "ads_ratio_tone",
    "brand_of",
    "bucket",
    "change",
    "commission_revenue",
    "fold",
    "invoice_factor",
    "matches",
    "ratio",
    "subject_key",
]
