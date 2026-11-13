"""Tài chính > Thu - chi (đợt 3, báo cáo quản trị): a month's money in and out
by pháp nhân, set against the budget and the revenue KPI.

* **Thu** = thực thu sau VAT & phí from the CRM (the bảng kê), split by the
  pháp nhân of each CRM branch ("Chưa gắn" when a branch is not mapped);
* **Chi** = hồ sơ chi realized that month (B5), and those still committed;
* **Ngân sách** = the month's budget lines of the pháp nhân.

Per phòng ban: spending realized / committed and the budget (B2: at the
parent). ``FINANCE REPORT``.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.budgets import FinanceBudgetService
from meobot.application.finance.ledger import FinanceLedgerService
from meobot.application.finance.revenue import month_span
from meobot.db.models.ads import AdsCrmPayment
from meobot.db.models.department import Department
from meobot.db.models.finance import CrmBranchRecord
from meobot.db.models.legal_entity import LegalEntity
from meobot.domain.finance.crm import whole

NONE_KEY = "none"


@dataclass(slots=True)
class PnlLine:
    key: str
    label: str
    income: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)
    committed: Decimal = Decimal(0)
    budget: Decimal = Decimal(0)

    @property
    def result(self) -> Decimal:
        return self.income - self.realized


@dataclass(slots=True)
class Pnl:
    month: date
    entities: list[PnlLine]
    departments: list[PnlLine] = field(default_factory=list)


class FinancePnlService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def month(self, month: date, *, today: date) -> Pnl:
        first, last = month_span(month)
        entities = {row.id: row for row in await self._session.scalars(select(LegalEntity))}
        branch_entity = {
            row.id: row.legal_entity_id
            for row in await self._session.scalars(select(CrmBranchRecord))
        }
        lines: dict[str, PnlLine] = {}

        def entity_line(entity_id: uuid.UUID | None) -> PnlLine:
            key = str(entity_id) if entity_id else NONE_KEY
            if key not in lines:
                entity = entities.get(entity_id) if entity_id else None
                label = f"{entity.code} - {entity.name}" if entity else "Chưa gắn pháp nhân"
                lines[key] = PnlLine(key, label)
            return lines[key]

        ledger = await FinanceLedgerService(self._session, self._audit).build(
            first, last, today=today
        )
        orders_branch: dict[int, int | None] = {}
        payments = await self._session.execute(
            select(AdsCrmPayment.order_id, AdsCrmPayment.branch_id).where(
                AdsCrmPayment.payment_date.between(first, last)
            )
        )
        for order_id, branch_id in payments.all():
            if order_id is not None:
                orders_branch[int(order_id)] = branch_id
        branch_of_name = {
            row.name: row.id for row in await self._session.scalars(select(CrmBranchRecord))
        }
        for row in ledger.rows:
            branch = orders_branch.get(row.order_id or -1) or branch_of_name.get(row.branch or "")
            entity_line(branch_entity.get(branch) if branch is not None else None).income += row.net

        budgets = await FinanceBudgetService(self._session, self._audit).month(first)
        departments = {row.id: row for row in await self._session.scalars(select(Department))}
        by_department: dict[uuid.UUID, PnlLine] = {}
        for item in budgets:
            target = entity_line(item.legal_entity_id)
            target.budget += item.budget
            target.realized += item.realized
            target.committed += item.committed
            department = departments.get(item.department_id)
            if item.department_id not in by_department:
                by_department[item.department_id] = PnlLine(
                    str(item.department_id), department.name if department else "Không rõ"
                )
            slot = by_department[item.department_id]
            slot.budget += item.budget
            slot.realized += item.realized
            slot.committed += item.committed
        rounded = [
            PnlLine(
                line.key,
                line.label,
                whole(line.income),
                whole(line.realized),
                whole(line.committed),
                whole(line.budget),
            )
            for line in lines.values()
        ]
        rounded.sort(key=lambda line: (line.key == NONE_KEY, line.label))
        return Pnl(
            month=first,
            entities=rounded,
            departments=sorted(by_department.values(), key=lambda line: line.label),
        )


def totals(lines: list[PnlLine]) -> PnlLine:
    total: defaultdict[str, Decimal] = defaultdict(Decimal)
    for line in lines:
        for name in ("income", "realized", "committed", "budget"):
            total[name] += getattr(line, name)
    return PnlLine("total", "Tổng", **total)


__all__ = ["FinancePnlService", "Pnl", "PnlLine", "totals"]
