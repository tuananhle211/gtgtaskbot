"""Tài chính > KPI doanh thu (đợt 2, R7: typed on APM per cơ sở / đội / month).

A cell is ``(month, dimension, key)``: ``total`` / ``""`` = the company,
``site`` / an APM cơ sở code (``BRANCHES``), ``team`` / ``HN`` or ``HCM`` (the
Marketing team, Phòng Ads' region). The amount is a thực thu target; the
revenue screen and the bảng kê's "Tổng hợp" set thực thu against it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.core.time import utcnow
from meobot.db.models.finance import FinanceKpi
from meobot.domain.ads.crm import CrmRegion
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.errors import FinanceInvalidError
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import BRANCHES

DIMENSIONS = ("total", "site", "team")


def kpi_keys() -> dict[str, list[str]]:
    """Every cell a month may hold."""
    return {
        "total": [""],
        "site": list(BRANCHES),
        "team": [region.value for region in CrmRegion],
    }


class FinanceKpiService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def month(self, month: date) -> dict[tuple[str, str], Decimal]:
        rows = await self._session.scalars(
            select(FinanceKpi).where(FinanceKpi.month == month.replace(day=1))
        )
        return {(row.dimension, row.key): Decimal(row.amount) for row in rows}

    async def save(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        month: date,
        entries: dict[str, str | None],
    ) -> dict[tuple[str, str], Decimal]:
        """``entries`` = ``{"dimension:key": amount | null}`` (only the sent cells
        change; ``null`` / blank removes one)."""
        access.require_settings()
        first = month.replace(day=1)
        allowed = kpi_keys()
        stored = {
            (row.dimension, row.key): row
            for row in await self._session.scalars(
                select(FinanceKpi).where(FinanceKpi.month == first)
            )
        }
        for cell, given in entries.items():
            dimension, _, key = cell.partition(":")
            if dimension not in allowed or key not in allowed[dimension]:
                raise FinanceInvalidError("kpi_cell_invalid", f"Ô KPI {cell!r} không có.")
            row = stored.get((dimension, key))
            if given is None or str(given).strip() == "":
                if row is not None:
                    await self._session.delete(row)
                continue
            text = str(given).replace(".", "").replace(",", "").replace(" ", "")
            try:
                amount = Decimal(text)
            except (InvalidOperation, ValueError) as error:
                raise FinanceInvalidError(
                    "kpi_invalid", "KPI phải là số tiền.", field=cell
                ) from error
            if amount < 0 or amount > Decimal("1000000000000"):
                raise FinanceInvalidError("kpi_invalid", "KPI không hợp lệ.", field=cell)
            if row is None:
                row = FinanceKpi(month=first, dimension=dimension, key=key, amount=amount)
                self._session.add(row)
            row.amount = amount
            row.updated_by_user_id = actor.user_id
            row.updated_at = utcnow()
        await self._session.flush()
        after = await self.month(first)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_SETTINGS_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_kpi",
            entity_id=first.isoformat(),
            after_data={f"{d}:{k}": str(v) for (d, k), v in after.items()},
        )
        return after


__all__ = ["DIMENSIONS", "FinanceKpiService", "kpi_keys"]
