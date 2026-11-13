"""Tài chính > Doanh thu (0105): a month of the CRM's numbers, three ways.

Read from what the one CRM run keeps (:mod:`meobot.application.crm.sync`):

* thực thu (tiền về) from the payments of the month, by the day paid, and the
  VAT inside it (:func:`meobot.domain.finance.crm.vat_inside`);
* doanh số / doanh thu / VAT / còn nợ from the orders created in the month;
* each split by pháp nhân and APM cơ sở (the CRM branch's mapping on
  "Cấu hình"), by CRM branch, by Marketing team (HN / SG: the region of the
  payment's marketing account, Phòng Ads' own), and by person (telesale, MKT,
  CSKH, carepage, order owner);
* the revenue shared to doctors / TVV / KTV / nurses and the công tua.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.ads import AdsCrmAccount, AdsCrmPayment
from meobot.db.models.finance import (
    CrmBranchRecord,
    CrmOrderRecord,
    CrmShareRecord,
    CrmStaffRecord,
    CrmTourRecord,
)
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.ads.crm import CrmRegion
from meobot.domain.finance.crm import (
    DEFAULT_VAT_PERCENT,
    PAYMENT_ROLES,
    counted,
    order_vat_percent,
    vat_inside,
    whole,
)
from meobot.domain.org.models import BRANCHES

#: ``system_settings`` key of Tài chính's own settings (VAT by pháp nhân).
FINANCE_SETTINGS_KEY = "finance"
#: The label of a line nobody mapped.
UNMAPPED = "Chưa gắn"
TEAM_LABELS = {CrmRegion.HN.value: "Đội Hà Nội", CrmRegion.HCM.value: "Đội Sài Gòn"}


def month_span(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first, following - timedelta(days=1)


@dataclass(slots=True)
class Figures:
    """One line's numbers (money in whole đồng)."""

    collected: Decimal = Decimal(0)
    collected_vat: Decimal = Decimal(0)
    refunds: Decimal = Decimal(0)
    payments: int = 0
    sales: Decimal = Decimal(0)
    revenue: Decimal = Decimal(0)
    sales_vat: Decimal = Decimal(0)
    owed: Decimal = Decimal(0)
    orders: int = 0

    @property
    def collected_net(self) -> Decimal:
        return self.collected - self.collected_vat

    def rounded(self) -> Figures:
        return Figures(
            collected=whole(self.collected),
            collected_vat=whole(self.collected_vat),
            refunds=whole(self.refunds),
            payments=self.payments,
            sales=whole(self.sales),
            revenue=whole(self.revenue),
            sales_vat=whole(self.sales_vat),
            owed=whole(self.owed),
            orders=self.orders,
        )


@dataclass(slots=True)
class Line:
    key: str
    label: str
    figures: Figures = field(default_factory=Figures)


@dataclass(frozen=True, slots=True)
class PersonLine:
    crm_id: int
    name: str
    #: The APM account the CRM person is, when mapped.
    user_id: uuid.UUID | None
    user_name: str | None
    collected: Decimal
    payments: int


@dataclass(frozen=True, slots=True)
class ShareLine:
    crm_id: int | None
    name: str
    department: str | None
    user_name: str | None
    shared: Decimal
    lines: int


@dataclass(frozen=True, slots=True)
class TourLine:
    crm_id: int | None
    name: str
    user_name: str | None
    primary: int
    support: int


@dataclass(slots=True)
class MonthRevenue:
    month: date
    figures: Figures
    #: The month before - only its first days while ``month`` is the
    #: current one ("cùng kỳ"), so a month half done is not set against a whole.
    previous: Figures
    previous_from: date
    previous_to: date
    by_entity: list[Line]
    by_site: list[Line]
    by_branch: list[Line]
    by_team: list[Line]
    days: list[tuple[date, Decimal]]
    people: dict[str, list[PersonLine]]
    shares: list[ShareLine]
    tours: list[TourLine]
    unmapped_branches: int
    coverage: dict[str, Any]
    #: The four fees of the month's bảng kê rows (typed, else automatic).
    fees: Decimal = Decimal(0)
    #: KPI (thực thu) by ``(dimension, key)``: ``("total", "")``, ``("site", code)``,
    #: ``("team", "HN"|"HCM")``.
    kpi: dict[tuple[str, str], Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Branch:
    entity_key: str
    entity_label: str
    site_key: str
    site_label: str
    name: str


class FinanceRevenueService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def settings(self) -> dict[str, Any]:
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == FINANCE_SETTINGS_KEY)
        )
        return dict(row.value) if row is not None else {}

    async def _branches(self) -> dict[int, _Branch]:
        entities = {row.id: row for row in await self._session.scalars(select(LegalEntity))}
        found: dict[int, _Branch] = {}
        for row in await self._session.scalars(select(CrmBranchRecord)):
            entity = entities.get(row.legal_entity_id) if row.legal_entity_id else None
            found[row.id] = _Branch(
                entity_key=str(entity.id) if entity else "",
                entity_label=f"{entity.code} - {entity.name}" if entity else UNMAPPED,
                site_key=row.apm_branch or "",
                site_label=BRANCHES.get(row.apm_branch or "", UNMAPPED),
                name=row.name,
            )
        return found

    async def vat_by_entity(self) -> tuple[Decimal, dict[str, Decimal]]:
        value = await self.settings()
        default = Decimal(str(value.get("vat_default", DEFAULT_VAT_PERCENT)))
        by_entity = {
            str(key): Decimal(str(rate)) for key, rate in (value.get("vat_by_entity") or {}).items()
        }
        return default, by_entity

    async def figures(self, since: date, until: date) -> Figures:
        """The headline numbers of a span (for the month before, and reports)."""
        branches = await self._branches()
        default, by_entity = await self.vat_by_entity()
        payments = await self._payments(since, until)
        rates = await self._order_rates(payments)
        total = Figures()
        for payment in payments:
            self._add_payment(total, payment, branches, rates, default, by_entity)
        for order in await self._orders(since, until):
            self._add_order(total, order)
        return total.rounded()

    async def _payments(self, since: date, until: date) -> list[AdsCrmPayment]:
        return list(
            (
                await self._session.scalars(
                    select(AdsCrmPayment).where(AdsCrmPayment.payment_date.between(since, until))
                )
            ).all()
        )

    async def _orders(self, since: date, until: date) -> list[CrmOrderRecord]:
        return list(
            (
                await self._session.scalars(
                    select(CrmOrderRecord).where(CrmOrderRecord.created_on.between(since, until))
                )
            ).all()
        )

    async def _order_rates(self, payments: Iterable[AdsCrmPayment]) -> dict[int, Decimal]:
        ids = {payment.order_id for payment in payments if payment.order_id is not None}
        rates: dict[int, Decimal] = {}
        if not ids:
            return rates
        rows = await self._session.execute(
            select(CrmOrderRecord.id, CrmOrderRecord.all_total, CrmOrderRecord.vat_amount).where(
                CrmOrderRecord.id.in_(ids)
            )
        )
        for order_id, all_total, vat in rows.all():
            rate = order_vat_percent(Decimal(all_total), Decimal(vat))
            if rate is not None:
                rates[int(order_id)] = rate
        return rates

    @staticmethod
    def _add_payment(
        figures: Figures,
        payment: AdsCrmPayment,
        branches: dict[int, _Branch],
        rates: dict[int, Decimal],
        default: Decimal,
        by_entity: dict[str, Decimal],
    ) -> None:
        if not counted(payment.payment_type):
            return
        price = Decimal(payment.price)
        branch = branches.get(payment.branch_id) if payment.branch_id is not None else None
        rate = rates.get(payment.order_id) if payment.order_id is not None else None
        if rate is None:
            rate = by_entity.get(branch.entity_key, default) if branch else default
        figures.collected += price
        figures.collected_vat += vat_inside(price, rate)
        if price < 0:
            figures.refunds += price
        figures.payments += 1

    @staticmethod
    def _add_order(figures: Figures, order: CrmOrderRecord) -> None:
        figures.sales += Decimal(order.all_total)
        figures.revenue += Decimal(order.amount_before_vat)
        figures.sales_vat += Decimal(order.vat_amount)
        figures.owed += Decimal(order.the_rest)
        figures.orders += 1

    async def month(self, month: date, *, today: date | None = None) -> MonthRevenue:
        since, until = month_span(month)
        branches = await self._branches()
        default, by_entity = await self.vat_by_entity()
        payments = await self._payments(since, until)
        orders = await self._orders(since, until)
        rates = await self._order_rates(payments)
        regions = {
            row.crm_id: row.region.value if row.region else ""
            for row in await self._session.scalars(select(AdsCrmAccount))
        }

        total = Figures()
        groups: dict[str, dict[str, Line]] = {
            "entity": {},
            "site": {},
            "branch": {},
            "team": {},
        }

        def lines_of(branch_id: int | None, marketing_id: int | None) -> list[Line]:
            branch = branches.get(branch_id) if branch_id is not None else None
            keys = [
                (
                    "entity",
                    branch.entity_key if branch else "",
                    branch.entity_label if branch else UNMAPPED,
                ),
                (
                    "site",
                    branch.site_key if branch else "",
                    branch.site_label if branch else UNMAPPED,
                ),
                (
                    "branch",
                    str(branch_id) if branch_id is not None else "",
                    branch.name if branch else UNMAPPED,
                ),
            ]
            if marketing_id is not None:
                region = regions.get(marketing_id, "")
                keys.append(("team", region, TEAM_LABELS.get(region, "Chưa rõ đội")))
            found = []
            for group, key, label in keys:
                line = groups[group].get(key)
                if line is None:
                    line = groups[group][key] = Line(key, label)
                found.append(line)
            return found

        days: dict[date, Decimal] = defaultdict(Decimal)
        people: dict[str, dict[int, list[Any]]] = {role: {} for role in PAYMENT_ROLES}
        for payment in payments:
            if not counted(payment.payment_type):
                continue
            self._add_payment(total, payment, branches, rates, default, by_entity)
            for line in lines_of(payment.branch_id, payment.marketing_id):
                self._add_payment(line.figures, payment, branches, rates, default, by_entity)
            days[payment.payment_date] += Decimal(payment.price)
            for role, crm_id in (
                ("telesales", payment.telesales_id),
                ("marketing", payment.marketing_id),
                ("cskh", payment.cskh_id),
                ("carepage", payment.carepage_id),
                ("owner", payment.owner_id),
            ):
                if crm_id is None:
                    continue
                slot = people[role].setdefault(crm_id, [Decimal(0), 0])
                slot[0] += Decimal(payment.price)
                slot[1] += 1
        for order in orders:
            self._add_order(total, order)
            for line in lines_of(order.branch_id, None):
                self._add_order(line.figures, order)

        shares = list(
            (
                await self._session.scalars(
                    select(CrmShareRecord).where(CrmShareRecord.payment_date.between(since, until))
                )
            ).all()
        )
        tours = list(
            (
                await self._session.scalars(
                    select(CrmTourRecord).where(CrmTourRecord.performed_on.between(since, until))
                )
            ).all()
        )
        staff_ids = (
            {key for role in people.values() for key in role}
            | {row.employee_id for row in shares if row.employee_id is not None}
            | {row.employee_id for row in tours if row.employee_id is not None}
        )
        staff = {
            row.id: row
            for row in await self._session.scalars(
                select(CrmStaffRecord).where(CrmStaffRecord.id.in_(staff_ids or {-1}))
            )
        }
        user_ids = {row.user_id for row in staff.values() if row.user_id is not None}
        users = {
            row.id: row.full_name or ""
            for row in await self._session.scalars(
                select(User).where(User.id.in_(user_ids or {uuid.uuid4()}))
            )
        }

        def who(crm_id: int | None) -> tuple[str, uuid.UUID | None, str | None]:
            row = staff.get(crm_id) if crm_id is not None else None
            if row is None:
                return (f"CRM {crm_id}" if crm_id is not None else "Không rõ", None, None)
            return row.name, row.user_id, users.get(row.user_id) if row.user_id else None

        person_lines: dict[str, list[PersonLine]] = {}
        for role, found in people.items():
            person_lines[role] = sorted(
                (
                    PersonLine(
                        crm_id=crm_id,
                        name=who(crm_id)[0],
                        user_id=who(crm_id)[1],
                        user_name=who(crm_id)[2],
                        collected=whole(amount),
                        payments=count,
                    )
                    for crm_id, (amount, count) in found.items()
                ),
                key=lambda line: (-line.collected, line.name),
            )

        share_totals: dict[int | None, list[Any]] = {}
        for row in shares:
            slot = share_totals.setdefault(row.employee_id, [Decimal(0), 0])
            slot[0] += Decimal(row.shared_revenue)
            slot[1] += 1
        share_lines = sorted(
            (
                ShareLine(
                    crm_id=crm_id,
                    name=who(crm_id)[0],
                    department=staff[crm_id].department_name if crm_id in staff else None,
                    user_name=who(crm_id)[2],
                    shared=whole(amount),
                    lines=count,
                )
                for crm_id, (amount, count) in share_totals.items()
            ),
            key=lambda line: (-line.shared, line.name),
        )
        tour_totals: dict[int | None, list[int]] = {}
        for tour in tours:
            counts = tour_totals.setdefault(tour.employee_id, [0, 0])
            counts[0 if tour.work_type == "primary" else 1] += 1
        tour_lines = sorted(
            (
                TourLine(
                    crm_id=crm_id,
                    name=who(crm_id)[0],
                    user_name=who(crm_id)[2],
                    primary=primary,
                    support=support,
                )
                for crm_id, (primary, support) in tour_totals.items()
            ),
            key=lambda line: (-(line.primary + line.support), line.name),
        )

        previous_from, previous_to = month_span((since - timedelta(days=1)).replace(day=1))
        if today is not None and since <= today <= until:
            previous_to = min(previous_to, previous_from + (today - since))
        coverage_row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == "finance_crm")
        )

        def ordered(group: str) -> list[Line]:
            lines = [
                Line(line.key, line.label, line.figures.rounded())
                for line in groups[group].values()
            ]
            return sorted(lines, key=lambda line: (line.key == "", -line.figures.collected))

        from meobot.application.audit_service import AuditService
        from meobot.application.finance.kpi import FinanceKpiService
        from meobot.application.finance.ledger import FinanceLedgerService

        fees = await FinanceLedgerService(self._session).fees_total(
            since, until, today=today or until
        )
        kpi = await FinanceKpiService(self._session, AuditService(self._session)).month(since)
        return MonthRevenue(
            fees=whole(fees),
            kpi=kpi,
            month=since,
            figures=total.rounded(),
            previous=await self.figures(previous_from, previous_to),
            previous_from=previous_from,
            previous_to=previous_to,
            by_entity=ordered("entity"),
            by_site=ordered("site"),
            by_branch=ordered("branch"),
            by_team=ordered("team"),
            days=[(day, whole(days.get(day, Decimal(0)))) for day in _each_day(since, until)],
            people=person_lines,
            shares=share_lines,
            tours=tour_lines,
            unmapped_branches=sum(
                1 for branch in branches.values() if not branch.entity_key or not branch.site_key
            ),
            coverage=dict(coverage_row.value) if coverage_row is not None else {},
        )


def _each_day(since: date, until: date) -> list[date]:
    return [since + timedelta(days=offset) for offset in range((until - since).days + 1)]


__all__ = [
    "FINANCE_SETTINGS_KEY",
    "UNMAPPED",
    "Figures",
    "FinanceRevenueService",
    "Line",
    "MonthRevenue",
    "PersonLine",
    "ShareLine",
    "TourLine",
    "month_span",
]
