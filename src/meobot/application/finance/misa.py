"""Tài chính > Đối soát MISA (đợt 3; M4, user 12/11: chỉ đối chiếu tổng tháng).

Kế toán uploads MISA's "F01-DNN: Bảng cân đối tài khoản" of one pháp nhân and
one month (the file names neither the company nor - reliably - the month, so
both are chosen on upload; a "Tháng MM năm YYYY" line that disagrees is
refused). Its account lines replace that month's. The reconciliation sets
the month's totals side by side:

* **Doanh thu** - MISA: PS Có 511 - PS Có 5114 (giảm giá); APM: the CRM orders
  created that month in the pháp nhân's branches, before VAT;
* **Chi phí** - MISA: PS Nợ 632 + 642 + 811; APM: hồ sơ chi realized for
  that pháp nhân that month (B5).

``FINANCE MISA``.
"""

from __future__ import annotations

import asyncio
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.application.finance.revenue import month_span
from meobot.db.models.finance import CrmBranchRecord, CrmOrderRecord
from meobot.db.models.finance_spend import FinanceMisaBalance, FinanceRequest
from meobot.db.models.legal_entity import LegalEntity
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.errors import FinanceInvalidError
from meobot.domain.finance.spend import RequestStatus

MAX_BYTES = 5 * 1024 * 1024
REVENUE_ACCOUNT = "511"
DISCOUNT_ACCOUNT = "5114"
COST_ACCOUNTS = ("632", "642", "811")
_MONTH = re.compile(r"tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MisaLine:
    account: str
    name: str
    opening_debit: Decimal
    opening_credit: Decimal
    debit: Decimal
    credit: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


@dataclass(slots=True)
class Reconciliation:
    legal_entity_id: uuid.UUID
    month: date
    imported: bool
    misa_revenue: Decimal
    apm_revenue: Decimal
    misa_cost: Decimal
    apm_cost: Decimal
    lines: list[FinanceMisaBalance]

    @property
    def revenue_gap(self) -> Decimal:
        return self.misa_revenue - self.apm_revenue

    @property
    def cost_gap(self) -> Decimal:
        return self.misa_cost - self.apm_cost


def _number(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = str(value).replace(".", "").replace(",", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal(0)


def parse_f01(data: bytes) -> tuple[date | None, list[MisaLine]]:
    """The month the file says (if it does) and its account lines.
    Synchronous (openpyxl): call it in a thread."""
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    month: date | None = None
    lines: list[MisaLine] = []
    for values in sheet.iter_rows(values_only=True):
        cells = list(values) + [None] * 8
        first = cells[0]
        if month is None:
            for cell in cells[:3]:
                found = _MONTH.search(str(cell or ""))
                if found:
                    month = date(int(found.group(2)), int(found.group(1)), 1)
        account = str(first).strip() if first is not None else ""
        if account.endswith(".0"):
            account = account[:-2]
        if not account.isdigit():
            continue
        lines.append(
            MisaLine(
                account=account,
                name=str(cells[1] or "").strip()[:300],
                opening_debit=_number(cells[2]),
                opening_credit=_number(cells[3]),
                debit=_number(cells[4]),
                credit=_number(cells[5]),
                closing_debit=_number(cells[6]),
                closing_credit=_number(cells[7]),
            )
        )
    return month, lines


class FinanceMisaService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def import_file(
        self,
        *,
        actor: Any,
        access: FinanceAccess,
        request_id: uuid.UUID,
        legal_entity_id: uuid.UUID,
        month: date,
        data: bytes,
    ) -> int:
        access.require("misa", "Chỉ người có quyền MISA nhập được.")
        if await self._session.get(LegalEntity, legal_entity_id) is None:
            raise FinanceInvalidError("legal_entity_invalid", "Chọn pháp nhân.")
        if len(data) > MAX_BYTES or not data.startswith(b"PK"):
            raise FinanceInvalidError("misa_file_invalid", "File phải là Excel (.xlsx) ≤ 5 MB.")
        try:
            said, lines = await asyncio.to_thread(parse_f01, data)
        except Exception as error:
            raise FinanceInvalidError("misa_file_invalid", "Không đọc được file MISA.") from error
        first = month.replace(day=1)
        if said is not None and said != first:
            raise FinanceInvalidError(
                "misa_month_mismatch",
                f"File ghi tháng {said:%m/%Y}, khác tháng đã chọn {first:%m/%Y}.",
            )
        if not lines:
            raise FinanceInvalidError(
                "misa_file_invalid", "Không thấy dòng tài khoản nào (mẫu F01-DNN)."
            )
        await self._session.execute(
            delete(FinanceMisaBalance).where(
                FinanceMisaBalance.legal_entity_id == legal_entity_id,
                FinanceMisaBalance.month == first,
            )
        )
        seen: set[str] = set()
        for line in lines:
            if line.account in seen:
                continue
            seen.add(line.account)
            self._session.add(
                FinanceMisaBalance(
                    legal_entity_id=legal_entity_id,
                    month=first,
                    account=line.account,
                    name=line.name,
                    opening_debit=line.opening_debit,
                    opening_credit=line.opening_credit,
                    debit=line.debit,
                    credit=line.credit,
                    closing_debit=line.closing_debit,
                    closing_credit=line.closing_credit,
                    imported_by_user_id=actor.user_id,
                )
            )
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_MISA_IMPORTED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_misa",
            entity_id=f"{legal_entity_id}:{first:%Y-%m}",
            after_data={"lines": len(seen)},
        )
        return len(seen)

    async def reconcile(self, legal_entity_id: uuid.UUID, month: date) -> Reconciliation:
        first, last = month_span(month)
        lines = list(
            (
                await self._session.scalars(
                    select(FinanceMisaBalance)
                    .where(
                        FinanceMisaBalance.legal_entity_id == legal_entity_id,
                        FinanceMisaBalance.month == first,
                    )
                    .order_by(FinanceMisaBalance.account)
                )
            ).all()
        )
        by = {line.account: line for line in lines}
        misa_revenue = (
            Decimal(by[REVENUE_ACCOUNT].credit) if REVENUE_ACCOUNT in by else Decimal(0)
        ) - (Decimal(by[DISCOUNT_ACCOUNT].credit) if DISCOUNT_ACCOUNT in by else Decimal(0))
        misa_cost = sum(
            (Decimal(by[account].debit) for account in COST_ACCOUNTS if account in by), Decimal(0)
        )
        branches = list(
            (
                await self._session.scalars(
                    select(CrmBranchRecord.id).where(
                        CrmBranchRecord.legal_entity_id == legal_entity_id
                    )
                )
            ).all()
        )
        apm_revenue = Decimal(
            await self._session.scalar(
                select(func.coalesce(func.sum(CrmOrderRecord.amount_before_vat), 0)).where(
                    CrmOrderRecord.created_on.between(first, last),
                    CrmOrderRecord.branch_id.in_(branches or [-1]),
                )
            )
            or 0
        )
        apm_cost = Decimal(
            await self._session.scalar(
                select(func.coalesce(func.sum(FinanceRequest.amount), 0)).where(
                    FinanceRequest.legal_entity_id == legal_entity_id,
                    FinanceRequest.budget_month == first,
                    FinanceRequest.realized_at.is_not(None),
                    FinanceRequest.status.in_(
                        (RequestStatus.PENDING.value, RequestStatus.PAID.value)
                    ),
                )
            )
            or 0
        )
        return Reconciliation(
            legal_entity_id=legal_entity_id,
            month=first,
            imported=bool(lines),
            misa_revenue=misa_revenue,
            apm_revenue=apm_revenue,
            misa_cost=misa_cost,
            apm_cost=apm_cost,
            lines=lines,
        )


__all__ = ["FinanceMisaService", "MisaLine", "Reconciliation", "parse_f01"]
