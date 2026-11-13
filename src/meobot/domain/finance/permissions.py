"""Tài chính ("FINANCE"): its permission module (0105, 2026-11-13).

* ``REVENUE`` - "Doanh thu": the revenue reports read from the CRM (thực thu,
  doanh số, doanh thu, còn nợ; by pháp nhân, cơ sở, đội, người);
* ``CUSTOMERS`` - the customer's name on those lines (user 12/11 R8: kế toán,
  Phòng Tài chính, Ban giám đốc); without it a line shows the customer code;
* ``SETTINGS`` - "Cấu hình": which pháp nhân / cơ sở a CRM branch belongs to,
  which APM account a CRM person is, VAT, fee rules, service costs, KPI, công
  nợ milestones, and "Đồng bộ ngay";
* ``FEES`` - "Nhập phí" (đợt 2, 0106): type the four fees of a BCLT row
  (quẹt thẻ, hoa hồng giới thiệu, chi phí xe, viện phí / giá cost) over the
  automatic ones;
* đợt 3 (0107): ``VIEW_REQUESTS`` every hồ sơ chi (else one's own, one's
  department's as its head, and what waits on one), ``CODES`` budget codes +
  routes + finance roles + document storage, ``BUDGET``, ``MISA``, ``REPORT``
  (thu - chi). Anybody may file a hồ sơ chi (P6 B).

``NONE`` / ``ALL`` each. OWNER and ADMIN hold everything by role. 0105 seeds
the Phòng Kế toán (``KT``: HEAD all three, MEMBER revenue + customers), the
Văn phòng khối Tài chính - Kế toán (``VPTCKT`` HEAD all three) and the Ban
giám đốc (``BOD`` HEAD revenue + customers).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.org.models import DepartmentFunction, Position

MODULE_FINANCE = "FINANCE"


class FinancePermission(StrEnum):
    REVENUE = "REVENUE"
    CUSTOMERS = "CUSTOMERS"
    SETTINGS = "SETTINGS"
    FEES = "FEES"
    #: Đợt 3 (0107).
    VIEW_REQUESTS = "VIEW_REQUESTS"
    CODES = "CODES"
    BUDGET = "BUDGET"
    MISA = "MISA"
    REPORT = "REPORT"


FINANCE_PERMISSION_LABELS: Mapping[FinancePermission, str] = MappingProxyType(
    {
        FinancePermission.REVENUE: "Xem doanh thu (thực thu, doanh số, còn nợ) từ CRM",
        FinancePermission.CUSTOMERS: "Xem tên khách hàng trên các dòng doanh thu",
        FinancePermission.SETTINGS: (
            "Cấu hình: gắn chi nhánh / nhân viên CRM, VAT, phí, KPI, công nợ, đồng bộ"
        ),
        FinancePermission.FEES: "Nhập 4 khoản phí trên bảng kê (quẹt thẻ, HH, xe, giá cost)",
        FinancePermission.VIEW_REQUESTS: "Xem mọi hồ sơ chi",
        FinancePermission.CODES: "Mã chi & tuyến duyệt, vai trò tài chính, lưu chứng từ",
        FinancePermission.BUDGET: "Ngân sách: nhập, sửa, nhập file Excel",
        FinancePermission.MISA: "Nhập file MISA (F01-DNN) và đối soát",
        FinancePermission.REPORT: "Báo cáo thu - chi",
    }
)

FINANCE_OWNER_ONLY: frozenset[str] = frozenset()


def finance_allowed_scopes(_permission: FinancePermission) -> tuple[str, ...]:
    return ("NONE", "ALL")


def default_finance_grants(_function: DepartmentFunction) -> dict[Position, dict[str, str]]:
    """New departments start with nothing here (0105 seeds KT / VPTCKT / BOD)."""
    return {}


__all__ = [
    "FINANCE_OWNER_ONLY",
    "FINANCE_PERMISSION_LABELS",
    "MODULE_FINANCE",
    "FinancePermission",
    "default_finance_grants",
    "finance_allowed_scopes",
]
