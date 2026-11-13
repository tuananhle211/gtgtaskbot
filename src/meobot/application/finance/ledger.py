"""Tài chính > Bảng kê (đợt 2): the accounting sheet "KÉO DỮ LIỆU KẾ TOÁN"'s
BCLT and Tổng hợp, built from what the CRM run keeps.

A **row** is one CRM order on one day, as in the BCLT tab:

* ``NEW`` ("Đơn mới") - the day the order was created: its doanh số, what it
  still owes, and the payments made that day;
* ``TTCN`` ("Thanh toán công nợ") - a later day a payment came in for it.

Each row carries the payments by method (trả góp, thẻ, nhà xe thu hộ, chuyển
khoản, tiền mặt), the refunds, the VAT inside, the four fees and thực thu sau
VAT & phí, the people on the customer's file (sale, MKT, carepage), the TVV /
KTV / nurses and doctors the revenue was shared to, and how old the debt is.

**Fees** (user 12/11 R4: thực thu = tiền về, the four fees kept apart): a row's
fee is what Kế toán typed (``finance_fees``, ``FEES``) else the automatic one -
card fee = the day's payments x the method's % ("Cấu hình > Phí"), cost = the
order's lines x their services' cost price (on the ``NEW`` row only),
referral and car = 0.

The **day summary** is the Tổng hợp tab: doanh số, còn nợ, DT dịch vụ / sản
phẩm, thực thu, hoàn tiền, thực thu thuần, phí, thực thu thuần sau VAT & phí,
the KPI of the day (the month's KPI / its days) and % về (month to date).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.application.finance.kpi import FinanceKpiService
from meobot.application.finance.revenue import FinanceRevenueService, month_span
from meobot.application.finance.settings import FinanceSettingsService
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsCrmPayment
from meobot.db.models.finance import (
    CrmBranchRecord,
    CrmOrderRecord,
    CrmShareRecord,
    CrmStaffRecord,
    CrmTourRecord,
    FinanceFee,
    FinanceServiceCost,
)
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.crm import (
    FEE_FIELDS,
    PAYMENT_TYPE_LABELS,
    SHARE_TYPE_LABELS,
    TREATMENT_TYPE_LABELS,
    WORK_TYPE_LABELS,
    counted,
    order_vat_percent,
    vat_inside,
    whole,
)
from meobot.domain.finance.errors import FinanceInvalidError, FinanceNotFoundError
from meobot.domain.identity.models import Actor

#: The longest span the screen asks for at once.
MAX_LEDGER_DAYS = 31
#: The CRM departments whose shares fill "NV bán" (TVV, KTV, Y tá) - by name.
SELLER_DEPARTMENT_WORDS = ("tư vấn", "kỹ thuật", "y tá")
#: The CRM's "Bác Sĩ" department id (``/raw-data/shared-revenues`` default).
DOCTOR_DEPARTMENT = 10
#: The money methods, as the BCLT orders them.
METHOD_ORDER: tuple[int, ...] = (5, 2, 6, 4, 1)
REFUND = 7
PRODUCT_ROLE = 2


@dataclass(slots=True)
class Fees:
    card_fee: Decimal = Decimal(0)
    referral_fee: Decimal = Decimal(0)
    car_fee: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return self.card_fee + self.referral_fee + self.car_fee + self.cost


@dataclass(slots=True)
class LedgerRow:
    order_id: int | None
    day: date
    kind: str
    order_code: str | None
    product: bool
    customer_code: str | None
    customer_name: str | None
    branch: str | None
    sales: Decimal = Decimal(0)
    owed: Decimal = Decimal(0)
    owed_days: int | None = None
    collected: Decimal = Decimal(0)
    methods: dict[int, Decimal] = field(default_factory=dict)
    refunds: Decimal = Decimal(0)
    vat: Decimal = Decimal(0)
    auto: Fees = field(default_factory=Fees)
    fees: Fees = field(default_factory=Fees)
    typed: set[str] = field(default_factory=set)
    note: str | None = None
    sellers: list[tuple[str, Decimal]] = field(default_factory=list)
    doctors: dict[int, tuple[str, Decimal]] = field(default_factory=dict)
    sale: str | None = None
    marketing: str | None = None
    carepage: str | None = None
    #: The order's services ("DIỄN GIẢI").
    services: str | None = None

    @property
    def net(self) -> Decimal:
        """Thực thu sau VAT & phí."""
        return self.collected - self.vat - self.fees.total

    @property
    def seller_check(self) -> Decimal:
        """BCLT "CHECK DOANH THU TVV.KTV.Y TÁ" = DT 1 + DT 2 + DT 3 - thực thu."""
        return sum((amount for _who, amount in self.sellers), Decimal(0)) - self.collected

    @property
    def doctor_check(self) -> Decimal | None:
        """BCLT "CHECK DT BS" = DT phẫu + tiêm + khác - thực thu (services only)."""
        if self.product:
            return None
        return sum((amount for _who, amount in self.doctors.values()), Decimal(0)) - self.collected


@dataclass(slots=True)
class DaySummary:
    day: date
    sales: Decimal = Decimal(0)
    owed: Decimal = Decimal(0)
    service_sales: Decimal = Decimal(0)
    product_sales: Decimal = Decimal(0)
    received: Decimal = Decimal(0)
    refunds: Decimal = Decimal(0)
    vat: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    kpi: Decimal | None = None

    @property
    def collected(self) -> Decimal:
        """Thực thu thuần (= tiền về - hoàn tiền)."""
        return self.received + self.refunds

    @property
    def net(self) -> Decimal:
        return self.collected - self.vat - self.fees


@dataclass(frozen=True, slots=True)
class Person:
    crm_id: int | None
    name: str
    user_name: str | None


@dataclass(frozen=True, slots=True)
class RoleLine:
    """One role someone plays on an order (BCLT: Sale, MKT, Chat page, NV bán
    1-3, Bác sĩ phẫu / tiêm / khác, KTV)."""

    key: str
    label: str
    person: Person
    amount: Decimal | None
    day: date | None


@dataclass(frozen=True, slots=True)
class PaymentDetail:
    id: int
    day: date
    amount: Decimal
    method: str
    counted: bool
    branch: str | None
    sale: Person | None
    marketing: Person | None
    cskh: Person | None
    carepage: Person | None


@dataclass(frozen=True, slots=True)
class ShareDetail:
    day: date
    person: Person
    department: str
    kind: str
    doctor: bool
    payment_amount: Decimal
    shared: Decimal
    payment_type: str


@dataclass(frozen=True, slots=True)
class TourDetail:
    at: datetime
    person: Person
    work_type: str
    treatment: str
    service: str | None
    procedure: str | None
    note: str | None


@dataclass(slots=True)
class OrderDetail:
    code: str
    order: CrmOrderRecord | None
    customer_code: str | None
    customer_name: str | None
    branch: str | None
    entity: str | None
    owner: Person | None
    payments: list[PaymentDetail]
    shares: list[ShareDetail]
    tours: list[TourDetail]
    rows: list[LedgerRow]
    roles: list[RoleLine]


@dataclass(slots=True)
class Ledger:
    since: date
    until: date
    rows: list[LedgerRow]
    days: list[DaySummary]
    kpi_month: Decimal | None


class FinanceLedgerService:
    def __init__(self, session: AsyncSession, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit or AuditService(session)

    async def build(self, since: date, until: date, *, today: date) -> Ledger:
        orders = {
            row.id: row
            for row in await self._session.scalars(
                select(CrmOrderRecord).where(CrmOrderRecord.created_on.between(since, until))
            )
        }
        payments = list(
            (
                await self._session.scalars(
                    select(AdsCrmPayment).where(AdsCrmPayment.payment_date.between(since, until))
                )
            ).all()
        )
        missing = {
            p.order_id for p in payments if p.order_id is not None and p.order_id not in orders
        }
        if missing:
            for extra in await self._session.scalars(
                select(CrmOrderRecord).where(CrmOrderRecord.id.in_(missing))
            ):
                orders[extra.id] = extra
        branches = {
            row.id: row.name for row in await self._session.scalars(select(CrmBranchRecord))
        }
        settings = FinanceSettingsService(self._session, self._audit)
        rates = await settings.fee_rules()
        costs = {
            row.service_id: Decimal(row.cost)
            for row in await self._session.scalars(select(FinanceServiceCost))
        }
        revenue = FinanceRevenueService(self._session)
        vat_default, vat_by_entity = await revenue.vat_by_entity()
        entity_of = {
            row.id: str(row.legal_entity_id or "")
            for row in await self._session.scalars(select(CrmBranchRecord))
        }

        rows: dict[tuple[int | None, date, int], LedgerRow] = {}

        def key_of(
            order_id: int | None, day: date, payment_id: int
        ) -> tuple[int | None, date, int]:
            return (order_id, day, 0) if order_id is not None else (None, day, payment_id)

        for order in orders.values():
            if not since <= order.created_on <= until:
                continue
            rows[(order.id, order.created_on, 0)] = LedgerRow(
                order_id=order.id,
                day=order.created_on,
                kind="NEW",
                order_code=order.code,
                product=order.role_type == PRODUCT_ROLE,
                customer_code=order.customer_code,
                customer_name=order.customer_name,
                branch=branches.get(order.branch_id) if order.branch_id is not None else None,
                sales=Decimal(order.all_total),
                owed=Decimal(order.the_rest),
                owed_days=(today - order.created_on).days if order.the_rest > 0 else None,
                services=_services(order),
            )
        staff_ids: set[int] = set()
        people: dict[tuple[int | None, date, int], tuple[int | None, int | None, int | None]] = {}
        for payment in payments:
            key = key_of(payment.order_id, payment.payment_date, payment.id)
            row = rows.get(key)
            paid_order = orders.get(payment.order_id) if payment.order_id is not None else None
            if row is None:
                row = rows[key] = LedgerRow(
                    order_id=payment.order_id,
                    day=payment.payment_date,
                    kind="TTCN",
                    order_code=payment.order_code or (paid_order.code if paid_order else None),
                    product=bool(paid_order and paid_order.role_type == PRODUCT_ROLE),
                    customer_code=payment.customer_code,
                    customer_name=payment.customer_name,
                    branch=branches.get(payment.branch_id)
                    if payment.branch_id is not None
                    else None,
                    services=_services(paid_order) if paid_order else None,
                )
            if row.customer_name is None:
                row.customer_name = payment.customer_name
            if not counted(payment.payment_type):
                continue
            price = Decimal(payment.price)
            row.collected += price
            if payment.payment_type == REFUND:
                row.refunds += price
            else:
                row.methods[payment.payment_type] = (
                    row.methods.get(payment.payment_type, Decimal(0)) + price
                )
                rate = rates.get(payment.payment_type)
                if rate:
                    row.auto.card_fee += price * rate / Decimal(100)
            percent = (
                order_vat_percent(Decimal(paid_order.all_total), Decimal(paid_order.vat_amount))
                if paid_order
                else None
            )
            if percent is None:
                entity = entity_of.get(payment.branch_id or -1, "")
                percent = vat_by_entity.get(entity, vat_default)
            row.vat += vat_inside(price, percent)
            people[key] = (payment.telesales_id, payment.marketing_id, payment.carepage_id)
            staff_ids |= {x for x in people[key] if x is not None}

        # The order's cost price on its NEW row.
        for row in rows.values():
            if row.kind != "NEW" or row.order_id is None:
                continue
            order = orders[row.order_id]
            for item in order.items or []:
                service = item.get("service_id")
                if service is not None and int(service) in costs:
                    row.auto.cost += costs[int(service)] * Decimal(str(item.get("quantity") or 0))

        # Revenue shared (by order code + payment day).
        shares = list(
            (
                await self._session.scalars(
                    select(CrmShareRecord).where(CrmShareRecord.payment_date.between(since, until))
                )
            ).all()
        )
        staff_ids |= {row.employee_id for row in shares if row.employee_id is not None}
        staff = {
            row.id: row
            for row in await self._session.scalars(
                select(CrmStaffRecord).where(CrmStaffRecord.id.in_(staff_ids or {-1}))
            )
        }

        def name(crm_id: int | None) -> str | None:
            if crm_id is None:
                return None
            row = staff.get(crm_id)
            return row.name if row else f"CRM {crm_id}"

        for key, (sale, marketing, carepage) in people.items():
            row = rows[key]
            row.sale, row.marketing, row.carepage = name(sale), name(marketing), name(carepage)
        by_code: dict[tuple[str, date], LedgerRow] = {
            (row.order_code, row.day): row for row in rows.values() if row.order_code
        }
        for share in shares:
            row = by_code.get((share.order_code or "", share.payment_date))
            if row is None:
                continue
            who = name(share.employee_id) or "Không rõ"
            amount = Decimal(share.shared_revenue)
            if share.department_id == DOCTOR_DEPARTMENT:
                kind = share.share_type or 0
                before = row.doctors.get(kind)
                row.doctors[kind] = (
                    who if before is None else f"{before[0]}, {who}",
                    amount + (before[1] if before else Decimal(0)),
                )
            else:
                row.sellers.append((who, amount))

        # Fees typed by Kế toán.
        ids = {row.order_id for row in rows.values() if row.order_id is not None}
        typed = {
            (fee.order_id, fee.day): fee
            for fee in await self._session.scalars(
                select(FinanceFee).where(
                    FinanceFee.order_id.in_(ids or {-1}), FinanceFee.day.between(since, until)
                )
            )
        }
        for row in rows.values():
            fee = typed.get((row.order_id, row.day)) if row.order_id is not None else None
            for name_ in FEE_FIELDS:
                value = getattr(fee, name_) if fee is not None else None
                if value is not None:
                    setattr(row.fees, name_, Decimal(value))
                    row.typed.add(name_)
                else:
                    setattr(row.fees, name_, getattr(row.auto, name_))
            row.note = fee.note if fee is not None else None

        ordered = sorted(
            rows.values(),
            key=lambda row: (row.day, row.kind != "NEW", row.order_code or "", row.order_id or 0),
        )
        kpis = await FinanceKpiService(self._session, self._audit).month(since)
        kpi_month = kpis.get(("total", ""))
        return Ledger(
            since=since,
            until=until,
            rows=ordered,
            days=self._days(since, until, ordered, kpi_month),
            kpi_month=kpi_month,
        )

    @staticmethod
    def _days(
        since: date, until: date, rows: Sequence[LedgerRow], kpi_month: Decimal | None
    ) -> list[DaySummary]:
        days = {
            since + timedelta(days=n): DaySummary(since + timedelta(days=n))
            for n in range((until - since).days + 1)
        }
        first, last = month_span(since)
        per_day = kpi_month / Decimal((last - first).days + 1) if kpi_month else None
        for row in rows:
            summary = days[row.day]
            summary.sales += row.sales
            summary.owed += row.owed
            if row.product:
                summary.product_sales += row.sales
            else:
                summary.service_sales += row.sales
            summary.received += row.collected - row.refunds
            summary.refunds += row.refunds
            summary.vat += row.vat
            summary.fees += row.fees.total
        for summary in days.values():
            summary.kpi = per_day
        return list(days.values())

    # --- typing a fee ---------------------------------------------------------------------

    async def set_fees(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        order_id: int,
        day: date,
        changes: dict[str, Any],
    ) -> FinanceFee | None:
        """``changes`` = the sent keys among the four fees (``None`` = back to
        automatic) and ``note``. A row left with nothing typed is removed."""
        access.require_fees()
        order = await self._session.get(CrmOrderRecord, order_id)
        paid = await self._session.scalar(
            select(AdsCrmPayment.id)
            .where(AdsCrmPayment.order_id == order_id, AdsCrmPayment.payment_date == day)
            .limit(1)
        )
        if (order is None or order.created_on != day) and paid is None:
            raise FinanceInvalidError(
                "fee_row_invalid", "Không có dòng bảng kê cho đơn này vào ngày này."
            )
        row = await self._session.get(FinanceFee, (order_id, day))
        before: dict[str, object] = (
            {name: str(getattr(row, name)) for name in FEE_FIELDS} if row is not None else {}
        )
        if row is None:
            row = FinanceFee(order_id=order_id, day=day)
            self._session.add(row)
        for name, given in changes.items():
            if name == "note":
                row.note = (str(given).strip()[:500] or None) if given is not None else None
                continue
            if name not in FEE_FIELDS:
                raise FinanceInvalidError("fee_field_invalid", f"Không có khoản phí {name!r}.")
            setattr(row, name, _amount(given, name))
        row.updated_by_user_id = actor.user_id
        row.updated_at = utcnow()
        kept: FinanceFee | None = row
        if all(getattr(row, name) is None for name in FEE_FIELDS) and not row.note:
            await self._session.delete(row)
            kept = None
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_FEES_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="crm_order",
            entity_id=f"{order_id}:{day.isoformat()}",
            before_data=before,
            after_data=(
                {name: str(getattr(kept, name)) for name in FEE_FIELDS} if kept is not None else {}
            ),
        )
        return kept

    async def order(self, code: str, *, today: date) -> OrderDetail:
        """Everything about one CRM order: its lines, every payment and who is
        on the customer's file, the revenue shared to each person (TVV / KTV /
        y tá, doctors by kind), the công tua, and its BCLT row of each day."""
        order = await self._session.scalar(
            select(CrmOrderRecord).where(CrmOrderRecord.code == code)
        )
        payments = list(
            (
                await self._session.scalars(
                    select(AdsCrmPayment)
                    .where(
                        or_(
                            AdsCrmPayment.order_code == code,
                            AdsCrmPayment.order_id == (order.id if order else -1),
                        )
                    )
                    .order_by(AdsCrmPayment.payment_date, AdsCrmPayment.id)
                )
            ).all()
        )
        if order is None and not payments:
            raise FinanceNotFoundError("finance_order_not_found", "Không tìm thấy đơn hàng này.")
        shares = list(
            (
                await self._session.scalars(
                    select(CrmShareRecord)
                    .where(CrmShareRecord.order_code == code)
                    .order_by(CrmShareRecord.payment_date, CrmShareRecord.id)
                )
            ).all()
        )
        tours = list(
            (
                await self._session.scalars(
                    select(CrmTourRecord)
                    .where(CrmTourRecord.order_code == code)
                    .order_by(CrmTourRecord.performed_at)
                )
            ).all()
        )
        ids = {
            crm_id
            for payment in payments
            for crm_id in (
                payment.telesales_id,
                payment.marketing_id,
                payment.cskh_id,
                payment.carepage_id,
                payment.owner_id,
            )
            if crm_id is not None
        }
        ids |= {row.employee_id for row in shares if row.employee_id is not None}
        ids |= {row.employee_id for row in tours if row.employee_id is not None}
        if order is not None and order.owner_id is not None:
            ids.add(order.owner_id)
        staff = {
            row.id: row
            for row in await self._session.scalars(
                select(CrmStaffRecord).where(CrmStaffRecord.id.in_(ids or {-1}))
            )
        }
        departments = {
            row.department_id: row.department_name
            for row in await self._session.scalars(
                select(CrmStaffRecord).where(CrmStaffRecord.department_id.is_not(None))
            )
            if row.department_id is not None and row.department_name
        }
        users = {
            user.id: user.full_name or ""
            for user in await self._session.scalars(
                select(User).where(
                    User.id.in_(
                        {row.user_id for row in staff.values() if row.user_id} or {uuid.uuid4()}
                    )
                )
            )
        }

        def person(crm_id: int | None) -> Person | None:
            if crm_id is None:
                return None
            row = staff.get(crm_id)
            return Person(
                crm_id=crm_id,
                name=row.name if row else f"CRM {crm_id}",
                user_name=users.get(row.user_id) if row and row.user_id else None,
            )

        branches = {row.id: row for row in await self._session.scalars(select(CrmBranchRecord))}
        branch_id = order.branch_id if order else (payments[0].branch_id if payments else None)
        branch = branches.get(branch_id) if branch_id is not None else None
        entity = (
            await self._session.get(LegalEntity, branch.legal_entity_id)
            if branch and branch.legal_entity_id
            else None
        )
        days = sorted(
            {payment.payment_date for payment in payments}
            | ({order.created_on} if order else set())
        )
        rows: list[LedgerRow] = []
        for day in days:
            built = await self.build(day, day, today=today)
            rows += [
                row
                for row in built.rows
                if row.order_code == code or (order is not None and row.order_id == order.id)
            ]
        roles: list[RoleLine] = []
        owner = person(order.owner_id) if order else None
        if owner:
            roles.append(RoleLine("owner", "Người lên đơn", owner, None, None))
        for key, label, attribute in (
            ("telesales", "Sale (người phụ trách)", "telesales_id"),
            ("marketing", "MKT (nguồn)", "marketing_id"),
            ("cskh", "CSKH", "cskh_id"),
            ("carepage", "Chat page / Carepage", "carepage_id"),
        ):
            seen: set[int] = set()
            for payment in payments:
                crm_id = getattr(payment, attribute)
                if crm_id is None or crm_id in seen:
                    continue
                seen.add(crm_id)
                found = person(crm_id)
                assert found is not None
                roles.append(RoleLine(key, label, found, None, None))
        share_lines: list[ShareDetail] = []
        for row in shares:
            found = person(row.employee_id) or Person(None, "Không rõ", None)
            doctor = row.department_id == DOCTOR_DEPARTMENT
            department = departments.get(row.department_id) or (
                "Bác sĩ" if doctor else f"Phòng CRM {row.department_id}"
            )
            kind = (
                SHARE_TYPE_LABELS.get(row.share_type or 0, "Bác sĩ")
                if doctor
                else f"NV bán ({department})"
            )
            share_lines.append(
                ShareDetail(
                    day=row.payment_date,
                    person=found,
                    department=department,
                    kind=kind,
                    doctor=doctor,
                    payment_amount=Decimal(row.payment_amount),
                    shared=Decimal(row.shared_revenue),
                    payment_type=PAYMENT_TYPE_LABELS.get(row.payment_type or 0, ""),
                )
            )
            roles.append(
                RoleLine("share", kind, found, Decimal(row.shared_revenue), row.payment_date)
            )
        tour_lines = [
            TourDetail(
                at=row.performed_at,
                person=person(row.employee_id) or Person(None, "Không rõ", None),
                work_type=WORK_TYPE_LABELS.get(row.work_type or "", row.work_type or ""),
                treatment=TREATMENT_TYPE_LABELS.get(row.treatment_type or 0, ""),
                service=row.service_name,
                procedure=row.procedure_name,
                note=row.note,
            )
            for row in tours
        ]
        for line in tour_lines:
            roles.append(
                RoleLine(
                    "tour", f"KTV làm tua ({line.work_type})", line.person, None, line.at.date()
                )
            )
        payment_lines = [
            PaymentDetail(
                id=payment.id,
                day=payment.payment_date,
                amount=Decimal(payment.price),
                method=PAYMENT_TYPE_LABELS.get(payment.payment_type, str(payment.payment_type)),
                counted=counted(payment.payment_type),
                branch=branches[payment.branch_id].name if payment.branch_id in branches else None,
                sale=person(payment.telesales_id),
                marketing=person(payment.marketing_id),
                cskh=person(payment.cskh_id),
                carepage=person(payment.carepage_id),
            )
            for payment in payments
        ]
        return OrderDetail(
            code=code,
            order=order,
            customer_code=(order.customer_code if order else None)
            or next((p.customer_code for p in payments if p.customer_code), None),
            customer_name=(order.customer_name if order else None)
            or next((p.customer_name for p in payments if p.customer_name), None),
            branch=branch.name if branch else None,
            entity=f"{entity.code} - {entity.name}" if entity else None,
            owner=owner,
            payments=payment_lines,
            shares=share_lines,
            tours=tour_lines,
            rows=rows,
            roles=roles,
        )

    async def fees_total(self, since: date, until: date, *, today: date) -> Decimal:
        ledger = await self.build(since, until, today=today)
        return sum((row.fees.total for row in ledger.rows), Decimal(0))


def _services(order: CrmOrderRecord) -> str | None:
    names = [
        f"{item.get('service_name') or 'Dịch vụ'}"
        + (
            f" x {_qty(item.get('quantity'))}"
            # the CRM sends 0 when nobody typed a quantity (seen 13/11)
            if _qty(item.get("quantity")) not in ("", "0", "1")
            else ""
        )
        for item in order.items or []
    ]
    return ", ".join(names) or None


def _qty(value: object) -> str:
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return ""
    return f"{number.normalize():f}"


def _amount(value: object, field: str) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).replace(".", "").replace(",", "").replace(" ", "")
    try:
        number = Decimal(text)
    except ArithmeticError as error:
        raise FinanceInvalidError("money_invalid", "Số tiền phải là số.", field=field) from error
    if number < 0 or number > Decimal("100000000000"):
        raise FinanceInvalidError("money_invalid", "Số tiền không hợp lệ.", field=field)
    return number


def check_span(since: date, until: date) -> None:
    if until < since:
        raise FinanceInvalidError("period_invalid", "Ngày kết thúc phải sau ngày bắt đầu.")
    if (until - since).days + 1 > MAX_LEDGER_DAYS:
        raise FinanceInvalidError("period_invalid", f"Mỗi lần xem tối đa {MAX_LEDGER_DAYS} ngày.")


# --- the workbook (BCLT + Tổng hợp) -----------------------------------------------------

BCLT_HEADERS: tuple[str, ...] = (
    "NGÀY",
    "PHÂN LOẠI",
    "Mã ĐH",
    "SP/DV",
    "TÊN KH",
    "MÃ KH",
    "CHI NHÁNH",
    "DIỄN GIẢI",
    "DOANH SỐ",
    "THỰC THU",
    "NỢ",
    *(PAYMENT_TYPE_LABELS[method] for method in METHOD_ORDER),
    "Hoàn tiền",
    "VAT",
    *FEE_FIELDS.values(),
    "TỔNG HỢP PHÍ",
    "Thực thu sau VAT & phí",
    "CHECK DOANH THU TVV.KTV.Y TÁ",
    "CHECK DT BS",
    "NV BÁN 1",
    "DT 1",
    "NV BÁN 2",
    "DT 2",
    "NV BÁN 3",
    "DT 3",
    *(
        part
        for kind in (2, 1, 3, 4)
        for part in (SHARE_TYPE_LABELS[kind], f"DT {SHARE_TYPE_LABELS[kind].lower()}")
    ),
    "SALE (NGƯỜI PHỤ TRÁCH)",
    "MKT (NGUỒN)",
    "CAREPAGE",
    "Ghi chú",
    "Công nợ (số ngày)",
    "Công nợ quá 45 ngày",
)
SUMMARY_LINES: tuple[str, ...] = (
    "Doanh số",
    "Còn nợ",
    "DT Dịch vụ",
    "DT sản phẩm",
    "Tổng thực thu",
    "Hoàn tiền",
    "Thực thu thuần (= Thực thu - Hoàn tiền)",
    "VAT trong thực thu",
    "Phí (quẹt + HH + xe + giá cost)",
    "Thực thu thuần sau VAT & phí",
    "KPI",
)
_HEAD = Font(bold=True)
_FILL = PatternFill("solid", fgColor="DCE6F5")


def _money(value: Decimal) -> int:
    return int(whole(value))


def ledger_workbook(ledger: Ledger, *, names: bool) -> bytes:
    """The BCLT + Tổng hợp workbook. ``names`` = the customer's name column
    (FINANCE CUSTOMERS), else blank. Synchronous: call it in a thread."""
    import io

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "BCLT"
    sheet.append(list(BCLT_HEADERS))
    for cell in sheet[1]:
        cell.font, cell.fill = _HEAD, _FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for row in ledger.rows:
        sellers = (row.sellers + [("", Decimal(0))] * 3)[:3]
        doctors = [row.doctors.get(kind, ("", Decimal(0))) for kind in (2, 1, 3, 4)]
        sheet.append(
            [
                row.day,
                "Đơn mới" if row.kind == "NEW" else "TTCN",
                row.order_code or "",
                "SP" if row.product else "DV",
                (row.customer_name or "") if names else "",
                row.customer_code or "",
                row.branch or "",
                row.services or "",
                _money(row.sales),
                _money(row.collected),
                _money(row.owed),
                *(_money(row.methods.get(method, Decimal(0))) for method in METHOD_ORDER),
                _money(row.refunds),
                _money(row.vat),
                *(_money(getattr(row.fees, name)) for name in FEE_FIELDS),
                _money(row.fees.total),
                _money(row.net),
                _money(row.seller_check),
                _money(row.doctor_check) if row.doctor_check is not None else None,
                *(
                    value
                    for who, amount in sellers
                    for value in (who, _money(amount) if who else None)
                ),
                *(
                    value
                    for who, amount in doctors
                    for value in (who, _money(amount) if who else None)
                ),
                row.sale or "",
                row.marketing or "",
                row.carepage or "",
                row.note or "",
                row.owed_days if row.owed_days is not None else None,
                "QUÁ" if row.owed and (row.owed_days or 0) > 45 else "",
            ]
        )
    for index in range(2, sheet.max_row + 1):
        sheet.cell(index, 1).number_format = "dd/mm/yyyy"
        for column in range(9, len(BCLT_HEADERS) + 1):
            cell = sheet.cell(index, column)
            if isinstance(cell.value, int):
                cell.number_format = "#,##0"
    sheet.freeze_panes = "D2"
    for column in range(1, len(BCLT_HEADERS) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 16

    summary = book.create_sheet("Tổng hợp")
    summary.append(["", "TỔNG", *(day.day for day in ledger.days)])
    totals = DaySummary(ledger.since)
    for day in ledger.days:
        for name in (
            "sales",
            "owed",
            "service_sales",
            "product_sales",
            "received",
            "refunds",
            "vat",
            "fees",
        ):
            setattr(totals, name, getattr(totals, name) + getattr(day, name))
    lines = [
        ("sales",),
        ("owed",),
        ("service_sales",),
        ("product_sales",),
        ("received",),
        ("refunds",),
        ("collected",),
        ("vat",),
        ("fees",),
        ("net",),
    ]
    for label, (attribute,) in zip(SUMMARY_LINES, lines, strict=False):
        summary.append(
            [
                label,
                _money(getattr(totals, attribute)),
                *(_money(getattr(day, attribute)) for day in ledger.days),
            ]
        )
    summary.append(
        [
            "KPI",
            _money(ledger.kpi_month) if ledger.kpi_month else None,
            *(_money(day.kpi) if day.kpi else None for day in ledger.days),
        ]
    )
    if ledger.kpi_month:
        summary.append(["% VỀ", float(totals.collected / ledger.kpi_month)])
        summary.cell(summary.max_row, 2).number_format = "0.00%"
    for cell in summary[1]:
        cell.font, cell.fill = _HEAD, _FILL
        if isinstance(cell.value, date):
            cell.number_format = "dd/mm"
    for row_cells in summary.iter_rows(min_row=2, min_col=2):
        for cell in row_cells:
            if isinstance(cell.value, int):
                cell.number_format = "#,##0"
    summary.column_dimensions["A"].width = 40
    summary.freeze_panes = "C2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def flatten(values: Iterable[tuple[str, Decimal]]) -> list[dict[str, object]]:
    return [{"name": who, "amount": int(whole(amount))} for who, amount in values]


__all__ = [
    "BCLT_HEADERS",
    "MAX_LEDGER_DAYS",
    "METHOD_ORDER",
    "DaySummary",
    "Fees",
    "FinanceLedgerService",
    "Ledger",
    "LedgerRow",
    "check_span",
    "flatten",
    "ledger_workbook",
]
