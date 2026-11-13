"""Tài chính > Ngân sách (đợt 3; B1, B2, B4, B5).

A budget line = pháp nhân x phòng ban x mã chi x tháng (B1, no cơ sở). A
department with sub-departments is budgeted at the parent: what its children
spend counts against it (B2). Spending (B5): a request is **committed** from
the day it is sent until it ends; **realized** at acceptance for a purchase
(the execution step done) and when paid for everything else. Lines are typed
on the screen or imported from the Excel template (B4); ``FINANCE BUDGET``.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.core.time import utcnow
from meobot.db.models.department import Department
from meobot.db.models.finance_spend import FinanceBudget, FinanceCode, FinanceRequest
from meobot.db.models.legal_entity import LegalEntity
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.errors import FinanceInvalidError
from meobot.domain.finance.spend import RequestStatus
from meobot.domain.identity.models import Actor

TEMPLATE_HEADERS = ("Pháp nhân (mã)", "Mã phòng ban", "Mã ngân sách", "Tháng (MM/YYYY)", "Số tiền")
MAX_IMPORT_BYTES = 5 * 1024 * 1024
LIVE = (RequestStatus.PENDING.value, RequestStatus.RETURNED.value, RequestStatus.PAID.value)


@dataclass(slots=True)
class BudgetLine:
    legal_entity_id: uuid.UUID
    department_id: uuid.UUID
    code: str
    budget: Decimal = Decimal(0)
    committed: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)

    @property
    def remaining(self) -> Decimal:
        return self.budget - self.committed - self.realized


class FinanceBudgetService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def _parents(self) -> dict[uuid.UUID, uuid.UUID]:
        """department -> the department it is budgeted at (B2: the parent)."""
        rows = (await self._session.scalars(select(Department))).all()
        return {row.id: row.parent_id or row.id for row in rows}

    async def month(
        self, month: date, *, legal_entity_id: uuid.UUID | None = None
    ) -> list[BudgetLine]:
        first = month.replace(day=1)
        parents = await self._parents()
        lines: dict[tuple[uuid.UUID, uuid.UUID, str], BudgetLine] = {}

        def line(entity: uuid.UUID, department: uuid.UUID, code: str) -> BudgetLine:
            key = (entity, department, code)
            if key not in lines:
                lines[key] = BudgetLine(entity, department, code)
            return lines[key]

        budgets = select(FinanceBudget).where(FinanceBudget.month == first)
        if legal_entity_id is not None:
            budgets = budgets.where(FinanceBudget.legal_entity_id == legal_entity_id)
        for row in await self._session.scalars(budgets):
            line(row.legal_entity_id, row.department_id, row.code).budget += Decimal(row.amount)
        requests = select(FinanceRequest).where(
            FinanceRequest.budget_month == first, FinanceRequest.status.in_(LIVE)
        )
        if legal_entity_id is not None:
            requests = requests.where(FinanceRequest.legal_entity_id == legal_entity_id)
        for request in await self._session.scalars(requests):
            if request.department_id is None:
                continue
            target = line(
                request.legal_entity_id,
                parents.get(request.department_id, request.department_id),
                request.budget_code,
            )
            if request.realized_at is not None:
                target.realized += Decimal(request.amount)
            else:
                target.committed += Decimal(request.amount)
        return sorted(
            lines.values(),
            key=lambda item: (str(item.legal_entity_id), str(item.department_id), item.code),
        )

    async def check(
        self,
        *,
        month: date,
        legal_entity_id: uuid.UUID,
        department_id: uuid.UUID | None,
        code: str,
    ) -> BudgetLine | None:
        """The line a request draws on (its department's parent), if budgeted."""
        if department_id is None:
            return None
        parent = (await self._parents()).get(department_id, department_id)
        for item in await self.month(month, legal_entity_id=legal_entity_id):
            if item.department_id == parent and item.code == code:
                return item
        return None

    async def set_cell(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        month: date,
        legal_entity_id: uuid.UUID,
        department_id: uuid.UUID,
        code: str,
        amount: str | None,
    ) -> None:
        access.require("budget", "Chỉ người có quyền ngân sách sửa được.")
        await self._check_keys(legal_entity_id, department_id, code)
        await self._write(actor, month.replace(day=1), legal_entity_id, department_id, code, amount)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_BUDGET_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_budget",
            entity_id=f"{month:%Y-%m}:{legal_entity_id}:{department_id}:{code}",
            after_data={"amount": amount},
        )

    async def _check_keys(
        self, legal_entity_id: uuid.UUID, department_id: uuid.UUID, code: str
    ) -> None:
        if await self._session.get(LegalEntity, legal_entity_id) is None:
            raise FinanceInvalidError("legal_entity_invalid", "Pháp nhân không có.")
        if await self._session.get(Department, department_id) is None:
            raise FinanceInvalidError("department_invalid", "Phòng ban không có.")
        row = await self._session.get(FinanceCode, code)
        if row is None or not row.leaf:
            raise FinanceInvalidError("finance_code_invalid", f"Mã ngân sách {code} không có.")

    async def _write(
        self,
        actor: Actor,
        first: date,
        legal_entity_id: uuid.UUID,
        department_id: uuid.UUID,
        code: str,
        amount: str | Decimal | None,
    ) -> None:
        row = await self._session.scalar(
            select(FinanceBudget).where(
                FinanceBudget.month == first,
                FinanceBudget.legal_entity_id == legal_entity_id,
                FinanceBudget.department_id == department_id,
                FinanceBudget.code == code,
            )
        )
        value = _money(amount) if amount not in (None, "") else None
        if value is None:
            if row is not None:
                await self._session.delete(row)
            await self._session.flush()
            return
        if row is None:
            row = FinanceBudget(
                month=first, legal_entity_id=legal_entity_id, department_id=department_id, code=code
            )
            self._session.add(row)
        row.amount = value
        row.updated_by_user_id = actor.user_id
        row.updated_at = utcnow()
        await self._session.flush()

    # --- Excel (B4) ----------------------------------------------------------------------------

    async def template(self, month: date) -> bytes:
        entities = (
            await self._session.scalars(select(LegalEntity).order_by(LegalEntity.code))
        ).all()
        departments = (
            await self._session.scalars(
                select(Department).where(Department.active.is_(True)).order_by(Department.code)
            )
        ).all()
        codes = (
            await self._session.scalars(
                select(FinanceCode)
                .where(FinanceCode.leaf.is_(True), FinanceCode.active.is_(True))
                .order_by(FinanceCode.sort_order)
            )
        ).all()
        existing = (
            await self._session.scalars(
                select(FinanceBudget).where(FinanceBudget.month == month.replace(day=1))
            )
        ).all()
        entity_code = {row.id: row.code for row in entities}
        department_code = {row.id: row.code for row in departments}
        rows = [
            (
                entity_code.get(item.legal_entity_id, ""),
                department_code.get(item.department_id, ""),
                item.code,
                f"{month:%m/%Y}",
                int(item.amount),
            )
            for item in existing
        ]
        lists = {
            "Pháp nhân": [(row.code, row.name) for row in entities],
            "Phòng ban": [(row.code, row.name) for row in departments],
            "Mã ngân sách": [(row.code, row.name) for row in codes],
        }
        return await asyncio.to_thread(_template_book, rows, lists, month)

    async def import_file(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        data: bytes,
    ) -> dict[str, Any]:
        """Every line or nothing: any bad line -> 422 ``budget_import_invalid``
        with ``details.problems``. A blank amount removes the cell."""
        access.require("budget", "Chỉ người có quyền ngân sách nhập được.")
        if len(data) > MAX_IMPORT_BYTES or not data.startswith(b"PK"):
            raise FinanceInvalidError("budget_import_invalid", "File phải là Excel (.xlsx) ≤ 5 MB.")
        table = await asyncio.to_thread(_read_book, data)
        entities = {
            row.code.upper(): row.id for row in await self._session.scalars(select(LegalEntity))
        }
        departments = {
            row.code.upper(): row.id for row in await self._session.scalars(select(Department))
        }
        codes = {
            row.code
            for row in await self._session.scalars(
                select(FinanceCode).where(FinanceCode.leaf.is_(True))
            )
        }
        problems: list[str] = []
        cells: dict[tuple[date, uuid.UUID, uuid.UUID, str], Decimal | None] = {}
        for number, values in table:
            entity, department, code, month_text, amount = (list(values) + [None] * 5)[:5]
            where = f"Dòng {number}"
            entity_id = entities.get(str(entity or "").strip().upper())
            department_id = departments.get(str(department or "").strip().upper())
            code_text = str(code or "").strip()
            month = _month(month_text)
            if entity_id is None:
                problems.append(f"{where}: pháp nhân {entity!r} không có.")
            if department_id is None:
                problems.append(f"{where}: mã phòng ban {department!r} không có.")
            if code_text not in codes:
                problems.append(f"{where}: mã ngân sách {code!r} không có.")
            if month is None:
                problems.append(f"{where}: tháng {month_text!r} phải dạng MM/YYYY.")
            try:
                value = _money(amount) if amount not in (None, "") else None
            except FinanceInvalidError:
                problems.append(f"{where}: số tiền {amount!r} không hợp lệ.")
                continue
            if entity_id and department_id and month and code_text in codes:
                cells[(month, entity_id, department_id, code_text)] = value
        if problems:
            raise FinanceInvalidError(
                "budget_import_invalid",
                f"File có {len(problems)} lỗi, chưa nhập dòng nào.",
                extra={"problems": problems[:50]},
            )
        for (month, entity_id, department_id, code_text), value in cells.items():
            await self._write(actor, month, entity_id, department_id, code_text, value)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_BUDGET_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_budget",
            entity_id="import",
            after_data={"lines": len(cells)},
        )
        return {"lines": len(cells)}


def _money(value: object) -> Decimal:
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
    else:
        text = str(value).replace(".", "").replace(",", "").replace(" ", "").strip()
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError) as error:
            raise FinanceInvalidError("money_invalid", "Số tiền không hợp lệ.") from error
    if number < 0 or number > Decimal("1000000000000"):
        raise FinanceInvalidError("money_invalid", "Số tiền không hợp lệ.")
    return number


def _month(value: object) -> date | None:
    if isinstance(value, date):
        return value.replace(day=1)
    text = str(value or "").strip().replace("-", "/")
    parts = text.split("/")
    try:
        if len(parts) == 2:
            month, year = int(parts[0]), int(parts[1])
            if month > 12:
                year, month = month, year
            return date(year, month, 1)
    except ValueError:
        return None
    return None


def _template_book(
    rows: list[tuple[Any, ...]], lists: dict[str, list[tuple[str, str]]], month: date
) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Ngân sách"
    sheet.append(list(TEMPLATE_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DCE6F5")
    for row in rows or [("APEXMED", "KT", "CO.06.02.01", f"{month:%m/%Y}", 0)]:
        sheet.append(list(row))
    for column, width in zip("ABCDE", (16, 16, 16, 16, 18), strict=True):
        sheet.column_dimensions[column].width = width
    for title, values in lists.items():
        tab = book.create_sheet(title)
        tab.append(["Mã", "Tên"])
        for code, name in values:
            tab.append([code, name])
        tab.column_dimensions["B"].width = 60
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _read_book(data: bytes) -> list[tuple[int, tuple[Any, ...]]]:
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = book["Ngân sách"] if "Ngân sách" in book.sheetnames else book.worksheets[0]
    rows: list[tuple[int, tuple[Any, ...]]] = []
    for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if values and any(value not in (None, "") for value in values[:5]):
            rows.append((number, tuple(values[:5])))
    return rows


def totals_by(lines: list[BudgetLine], key: str) -> dict[Any, BudgetLine]:
    found: dict[Any, BudgetLine] = defaultdict(
        lambda: BudgetLine(uuid.UUID(int=0), uuid.UUID(int=0), "")
    )
    for item in lines:
        target = found[getattr(item, key)]
        target.budget += item.budget
        target.committed += item.committed
        target.realized += item.realized
    return dict(found)


__all__ = ["TEMPLATE_HEADERS", "BudgetLine", "FinanceBudgetService", "totals_by"]
