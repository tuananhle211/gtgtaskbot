"""One CRM run for Phòng Ads and Tài chính (user 13/11/2026: "gộp ... cùng call
1 lần thôi, để kiểm soát").

``ads.sync_crm_revenue`` (0h, 5h, 12h, 18h Vietnam, and "Đồng bộ ngay") reads,
for the days of the window (this month; from the 1st of last month on the
1st-5th):

1. the marketing users, the branches and the payments - Phòng Ads' revenue
   (:meth:`AdsCrmSyncService.apply`, unchanged) and Tài chính's thực thu, with
   the CRM branches and the people the payments name kept here;
2. the orders created in the window (doanh số, VAT, còn nợ);
3. the revenue shared to each CRM department (Bác sĩ, TVV, KTV, Y tá);
4. the công tua.

The 5h run (``debts=True``) also re-reads the orders of the last year that
are still owed, so "còn nợ" stays right for old orders: one that is not owed
any more is set to 0.

Each part is kept in its own savepoint: a part the CRM refuses leaves the
others saved, and the run reports FAILED so the job monitor runs it again.
What was read is noted in ``system_settings`` ``finance_crm``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.crm_service import AdsCrmSyncService, CrmSyncReport
from meobot.core.time import utcnow
from meobot.db.models.finance import (
    CrmBranchRecord,
    CrmOrderRecord,
    CrmShareRecord,
    CrmStaffRecord,
    CrmTourRecord,
)
from meobot.db.models.system_setting import SystemSetting
from meobot.integrations.crm.client import (
    CrmBranch,
    CrmClient,
    CrmError,
    CrmOrder,
    CrmSharedRevenue,
    CrmTour,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
SETTINGS_KEY = "finance_crm"
#: How far back the 5h run looks for orders still owed.
DEBT_DAYS = 365

PART_LABELS = {
    "orders": "đơn hàng",
    "debts": "công nợ cũ",
    "shares": "doanh thu chia",
    "tours": "công tua",
}


def _vn(moment: datetime) -> datetime:
    """The CRM's naive Vietnam time as an aware instant."""
    return moment.replace(tzinfo=VN) if moment.tzinfo is None else moment


@dataclass(slots=True)
class PartReport:
    name: str
    rows: int = 0
    error: str | None = None

    @property
    def text(self) -> str:
        label = PART_LABELS[self.name]
        return f"{label}: lỗi ({self.error})" if self.error else f"{self.rows} {label}"


@dataclass(slots=True)
class CrmRunReport:
    """Phòng Ads' report and Tài chính's parts, as one job result."""

    ads: CrmSyncReport
    parts: list[PartReport] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.ads.error or any(part.error for part in self.parts):
            return "FAILED"
        return "SUCCESS"

    @property
    def message(self) -> str:
        if not self.ads.configured:
            return self.ads.message
        finance = ", ".join(part.text for part in self.parts)
        return f"{self.ads.message} Tài chính: {finance}." if finance else self.ads.message

    def as_task_result(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message[:480],
            "payments": self.ads.payments,
            "users": self.ads.users,
            **{part.name: part.rows for part in self.parts},
        }


class CrmSyncService:
    def __init__(self, session: AsyncSession, client: CrmClient) -> None:
        self._session = session
        self._client = client

    async def run(
        self, since: date, until: date, *, today: date, debts: bool = False
    ) -> CrmRunReport:
        ads = AdsCrmSyncService(self._session, self._client)
        try:
            users = await self._client.marketing_users()
            branches = await self._client.branches()
            payments = await self._client.payments(since, until)
        except CrmError as error:
            # Nothing else is asked: the key or the CRM is down for everybody.
            report = CrmRunReport(await ads.failed(since, until, error.message))
            await self._note(since, until, report)
            return report
        report = CrmRunReport(await ads.apply(since, until, users, branches, payments))
        await self._branches(branches)
        await self._staff(
            [(user.id, user.name, "marketing") for user in users]
            + [person for payment in payments for person in payment.people()]
        )
        await self._session.flush()

        report.parts.append(await self._part("orders", lambda: self._orders(since, until)))
        if debts:
            start = today - timedelta(days=DEBT_DAYS)
            if start < since:
                report.parts.append(
                    await self._part("debts", lambda: self._debts(start, since - timedelta(days=1)))
                )
        report.parts.append(await self._part("shares", lambda: self._shares(since, until)))
        report.parts.append(await self._part("tours", lambda: self._tours(since, until)))
        await self._note(since, until, report)
        await self._session.flush()
        return report

    async def _part(self, name: str, work: Callable[[], Awaitable[int]]) -> PartReport:
        part = PartReport(name)
        try:
            async with self._session.begin_nested():
                part.rows = await work()
        except CrmError as error:
            part.error = error.message
        return part

    # --- what one part keeps -----------------------------------------------------------

    async def _branches(self, branches: list[CrmBranch]) -> None:
        stored = {row.id: row for row in await self._session.scalars(select(CrmBranchRecord))}
        now = utcnow()
        for branch in branches:
            row = stored.get(branch.id)
            if row is None:
                row = CrmBranchRecord(id=branch.id)
                self._session.add(row)
            row.name = branch.name[:200]
            row.location_id = branch.location_id
            row.seen_at = now

    async def _staff(
        self,
        people: Iterable[tuple[int, str, str]],
        *,
        department: tuple[int, str] | None = None,
    ) -> None:
        found: dict[int, tuple[str, set[str]]] = {}
        for key, name, role in people:
            seen = found.setdefault(key, (name, set()))
            seen[1].add(role)
        if not found:
            return
        stored = {
            row.id: row
            for row in await self._session.scalars(
                select(CrmStaffRecord).where(CrmStaffRecord.id.in_(found))
            )
        }
        now = utcnow()
        for key, (name, roles) in found.items():
            row = stored.get(key)
            if row is None:
                row = CrmStaffRecord(id=key, roles=[])
                self._session.add(row)
            row.name = name[:200]
            row.roles = sorted(set(row.roles or []) | roles)
            if department is not None:
                row.department_id, row.department_name = department[0], department[1][:200]
            row.seen_at = now

    def _order_row(self, order: CrmOrder) -> CrmOrderRecord:
        return CrmOrderRecord(
            id=order.id,
            code=order.code,
            created_at=_vn(order.created_at),
            created_on=order.created_at.date(),
            branch_id=order.branch_id,
            customer_id=order.customer_id,
            customer_code=order.customer_code,
            customer_name=order.customer_name,
            source_id=order.source_id,
            all_total=order.all_total,
            amount_before_vat=order.amount_before_vat,
            vat_amount=order.vat_amount,
            vat_percent=order.vat_percent,
            gross_revenue=order.gross_revenue,
            the_rest=order.the_rest,
            discount=order.discount,
            payment_type=order.payment_type,
            order_type=order.order_type,
            role_type=order.role_type,
            owner_id=order.owner_id,
            items=[
                {
                    "service_id": item.service_id,
                    "service_name": item.service_name,
                    "quantity": str(item.quantity),
                    "total_price": str(item.total_price),
                }
                for item in order.items
            ],
            synced_at=utcnow(),
        )

    async def _save_orders(
        self, orders: list[CrmOrder], *, window: tuple[date, date] | None
    ) -> None:
        unique = {order.id: order for order in orders}
        ids = list(unique)
        if window is not None:
            await self._session.execute(
                delete(CrmOrderRecord).where(CrmOrderRecord.created_on.between(*window))
            )
        if ids:
            await self._session.execute(delete(CrmOrderRecord).where(CrmOrderRecord.id.in_(ids)))
        self._session.add_all(self._order_row(order) for order in unique.values())
        await self._staff(
            (order.owner_id, order.owner_name or f"CRM {order.owner_id}", "owner")
            for order in unique.values()
            if order.owner_id is not None
        )
        await self._session.flush()

    async def _orders(self, since: date, until: date) -> int:
        orders = await self._client.orders(since, until)
        await self._save_orders(orders, window=(since, until))
        return len(orders)

    async def _debts(self, since: date, until: date) -> int:
        """Orders of ``since``-``until`` still owed: kept as read; the stored
        ones of those days no longer listed are owed nothing any more."""
        owed = await self._client.orders(since, until, unpaid=True)
        await self._save_orders(owed, window=None)
        await self._session.execute(
            update(CrmOrderRecord)
            .where(
                CrmOrderRecord.created_on.between(since, until),
                CrmOrderRecord.the_rest > 0,
                CrmOrderRecord.id.not_in([order.id for order in owed] or [-1]),
            )
            .values(the_rest=0)
        )
        return len(owed)

    async def _shares(self, since: date, until: date) -> int:
        count = 0
        for department in await self._client.share_departments():
            rows: list[CrmSharedRevenue] = await self._client.shared_revenues(
                since, until, department.id
            )
            await self._session.execute(
                delete(CrmShareRecord).where(
                    CrmShareRecord.department_id == department.id,
                    CrmShareRecord.payment_date.between(since, until),
                )
            )
            now = utcnow()
            self._session.add_all(
                CrmShareRecord(
                    payment_date=row.payment_date,
                    department_id=department.id,
                    order_code=row.order_code,
                    customer_code=row.customer_code,
                    customer_name=row.customer_name,
                    services=row.services,
                    payment_amount=row.payment_amount,
                    payment_type=row.payment_type,
                    employee_id=row.employee_id,
                    share_type=row.share_type,
                    shared_revenue=row.shared_revenue,
                    branch_name=row.branch_name,
                    synced_at=now,
                )
                for row in rows
            )
            await self._staff(
                (
                    (row.employee_id, row.employee_name or f"CRM {row.employee_id}", "share")
                    for row in rows
                    if row.employee_id is not None
                ),
                department=(department.id, department.name),
            )
            await self._session.flush()
            count += len(rows)
        return count

    async def _tours(self, since: date, until: date) -> int:
        tours: list[CrmTour] = await self._client.tours(since, until)
        await self._session.execute(
            delete(CrmTourRecord).where(CrmTourRecord.performed_on.between(since, until))
        )
        now = utcnow()
        self._session.add_all(
            CrmTourRecord(
                performed_at=_vn(tour.performed_at),
                performed_on=tour.performed_at.date(),
                order_code=tour.order_code,
                customer_code=tour.customer_code,
                customer_name=tour.customer_name,
                service_name=tour.service_name,
                treatment_type=tour.treatment_type,
                employee_id=tour.employee_id,
                work_type=tour.work_type,
                procedure_name=tour.procedure_name,
                branch_name=tour.branch_name,
                note=tour.note,
                synced_at=now,
            )
            for tour in tours
        )
        await self._staff(
            (tour.employee_id, tour.employee_name or f"CRM {tour.employee_id}", "tour")
            for tour in tours
            if tour.employee_id is not None
        )
        await self._session.flush()
        return len(tours)

    # --- what was read ---------------------------------------------------------------------

    async def _note(self, since: date, until: date, report: CrmRunReport) -> None:
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        value: dict[str, Any] = dict(row.value) if row is not None else {}
        now = utcnow()
        value["at"] = now.isoformat()
        value["status"] = report.status
        value["message"] = report.message[:480]
        if not report.ads.error:
            low, high = value.get("synced_from"), value.get("synced_to")
            value["synced_from"] = min(filter(None, (low, since.isoformat())))
            value["synced_to"] = max(filter(None, (high, until.isoformat())))
            parts = dict(value.get("parts") or {})
            for part in report.parts:
                parts[part.name] = {"rows": part.rows, "error": part.error, "at": now.isoformat()}
            value["parts"] = parts
        if row is None:
            self._session.add(
                SystemSetting(
                    key=SETTINGS_KEY,
                    value=value,
                    description="Tài chính: lần đồng bộ CRM gần nhất.",
                )
            )
        else:
            row.value = value
            row.version = int(row.version) + 1


__all__ = [
    "DEBT_DAYS",
    "SETTINGS_KEY",
    "CrmRunReport",
    "CrmSyncService",
    "PartReport",
]
