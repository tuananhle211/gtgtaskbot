"""Tài chính's reading of the CRM's numbers (0105; user 12/11/2026 R3/R4).

* **Doanh số** = an order's value, VAT included (``all_total``), by the day
  it was created;
* **Doanh thu** = Doanh số - VAT (``amount_before_vat``);
* **Thực thu** (tiền về) = what the customers paid, VAT included, by the day
  paid - points (``payment_type`` 3) are not money, a refund (7) counts
  negative; the four fees are kept apart and only come off in the profit
  report;
* the VAT inside a payment = its order's VAT share when the order is known,
  else the default rate of the pháp nhân (config), else ``DEFAULT_VAT_PERCENT``.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from meobot.domain.ads.crm import COUNTED_PAYMENT_TYPES

#: The VAT rate used when neither the order nor the pháp nhân says (user 12/11: 8%).
DEFAULT_VAT_PERCENT = Decimal(8)

PAYMENT_TYPE_LABELS: Mapping[int, str] = MappingProxyType(
    {
        1: "Tiền mặt",
        2: "Thẻ",
        3: "Điểm",
        4: "Chuyển khoản",
        5: "Trả góp",
        6: "Nhà xe thu hộ",
        7: "Hoàn tiền",
    }
)
ORDER_TYPE_LABELS: Mapping[int, str] = MappingProxyType(
    {0: "Đơn thường", 1: "Liệu trình", 2: "Trừ liệu trình", 3: "Bảo hành", 4: "Bảo lưu"}
)
SHARE_TYPE_LABELS: Mapping[int, str] = MappingProxyType(
    {0: "Không chọn", 1: "Bác sĩ tiêm", 2: "Bác sĩ phẫu", 3: "Bác sĩ khác", 4: "Bác sĩ khác 2"}
)
TREATMENT_TYPE_LABELS: Mapping[int, str] = MappingProxyType(
    {0: "Trừ liệu trình", 1: "Bảo hành", 2: "Bảo lưu"}
)
WORK_TYPE_LABELS: Mapping[str, str] = MappingProxyType(
    {"primary": "Công chính", "support": "Hỗ trợ"}
)
#: The people a payment names, as Tài chính's "theo người" picker lists them.
PAYMENT_ROLES: Mapping[str, str] = MappingProxyType(
    {
        "telesales": "Telesale (người phụ trách)",
        "marketing": "Marketing",
        "cskh": "CSKH",
        "carepage": "Carepage",
        "owner": "Người lên đơn",
    }
)


#: The money methods a card / instalment / collection fee can be set for.
FEE_METHODS: tuple[int, ...] = (1, 2, 4, 5, 6)
#: "Công nợ" ages (days) by default (user 12/11 R10: configurable).
DEFAULT_DEBT_MILESTONES: tuple[int, ...] = (30, 45, 60)
#: The four fees of a BCLT row, in the sheet's order.
FEE_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "card_fee": "Phí quẹt",
        "referral_fee": "HH giới thiệu",
        "car_fee": "CP xe",
        "cost": "Viện phí / giá cost",
    }
)


def debt_bucket(age_days: int, milestones: tuple[int, ...]) -> int | None:
    """The largest milestone ``age_days`` has passed, else ``None``."""
    passed = [mark for mark in milestones if age_days > mark]
    return max(passed) if passed else None


def counted(payment_type: int) -> bool:
    """Whether a payment is money (thực thu): every type but points."""
    return payment_type in COUNTED_PAYMENT_TYPES


def vat_inside(amount: Decimal, percent: Decimal) -> Decimal:
    """The VAT inside an amount that includes it (not rounded)."""
    if percent <= 0:
        return Decimal(0)
    return amount * percent / (Decimal(100) + percent)


def order_vat_percent(all_total: Decimal, vat_amount: Decimal) -> Decimal | None:
    """An order's effective VAT rate: VAT over the part before it."""
    before = all_total - vat_amount
    if all_total <= 0 or before <= 0:
        return None
    return vat_amount * Decimal(100) / before


def whole(value: Decimal) -> Decimal:
    """Rounded to the đồng, half up."""
    return value.quantize(Decimal(1), rounding=ROUND_HALF_UP)


__all__ = [
    "DEFAULT_DEBT_MILESTONES",
    "DEFAULT_VAT_PERCENT",
    "FEE_FIELDS",
    "FEE_METHODS",
    "ORDER_TYPE_LABELS",
    "PAYMENT_ROLES",
    "PAYMENT_TYPE_LABELS",
    "SHARE_TYPE_LABELS",
    "TREATMENT_TYPE_LABELS",
    "WORK_TYPE_LABELS",
    "counted",
    "debt_bucket",
    "order_vat_percent",
    "vat_inside",
    "whole",
]
