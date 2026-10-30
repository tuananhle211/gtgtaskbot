"""Revenue and KPIs entered by hand (0079, chốt 27/10: the Trưởng phòng enters
revenue only).

* **Nhập doanh thu** - one day at a time: a line per person seated in the ADS
  department, and the ◆ sources that are nobody's ad account (Hotline, HN
  chuyển HCM, Google, Tiktok, Chưa gán). A blank cell is "not entered" (the
  row is removed), not 0. Every change is kept in ``ads_revenue_changes``.
* **KPI** - a month's target for the team and for each marketer.

Who: a Trưởng phòng ADS and the OWNER / an ADMIN (``AdsAccess.edit_revenue``).
The amounts are what the reconciliation sheet shows (VAT included), so the
KPI completion is revenue ÷ KPI and Ads / Doanh thu is spend ÷ revenue.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.access import AdsAccess
from meobot.application.ads.accounts_service import ads_people
from meobot.application.ads.crm_revenue import crm_book
from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsKpi, AdsRevenueChange, AdsRevenueDaily
from meobot.domain.ads.models import REVENUE_SOURCE_LABELS, RevenueSource, subject_key
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor

#: Ten thousand billion: a typo guard, not a business limit.
MAX_AMOUNT = Decimal("10000000000000")
TEAM_KEY = "team"


class AdsRevenueForbiddenError(AuthorizationError):
    code = "ads_revenue_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ Trưởng phòng ADS (hoặc chủ sở hữu, quản trị viên) nhập được doanh thu và KPI.",
            details={"reason": "ads_revenue_forbidden"},
        )


class AdsRevenueFromCrmError(ValidationError):
    code = "ads_revenue_from_crm"

    def __init__(self, key: str) -> None:
        super().__init__(
            "Dòng này lấy thực thu từ CRM, không nhập tay được.",
            details={"reason": "ads_revenue_from_crm", "key": key},
        )


class AdsRevenueInvalidError(ValidationError):
    code = "ads_revenue_invalid"

    def __init__(self, message: str, key: str) -> None:
        super().__init__(message, details={"reason": "ads_revenue_invalid", "key": key})


@dataclass(frozen=True, slots=True)
class EntryLine:
    key: str
    label: str
    user_id: uuid.UUID | None
    source: RevenueSource | None
    member_code: str | None
    #: "Doanh số SG" for a marketer, the amount for a ◆ source / a KPI.
    amount: float | None
    #: A marketer's "Doanh số HN" (Sài Gòn customers served in Hà Nội).
    amount_hn: float | None = None
    #: The day's line comes from the CRM (0083): read-only on "Nhập doanh thu".
    from_crm: bool = False


#: The four customer flows (2026-10-30): which marketing team brought the
#: customer (the CRM account's region) -> where they were served (the
#: payment's branch). ``hn_hcm`` = "HN chuyển SG", ``hcm_hn`` = "SG chuyển HN".
FLOWS = ("hcm_hcm", "hn_hn", "hn_hcm", "hcm_hn")
FLOW_LABELS = {
    "hcm_hcm": "SG (chạy HCM · làm HCM)",
    "hn_hn": "HN (chạy HN · làm HN)",
    "hn_hcm": "HN chuyển SG",
    "hcm_hn": "SG chuyển HN",
}
#: "Miền" on the dashboards: the flows each choice keeps. ``mkt_*`` = by the
#: marketing team (their KPI), ``site_*`` = by the branch that served (the
#: cơ sở's revenue); ``hcm`` / ``hn`` = the old links, read as the cơ sở.
REGION_FLOWS: dict[str, tuple[str, ...]] = {
    "all": FLOWS,
    "mkt_hcm": ("hcm_hcm", "hcm_hn"),
    "mkt_hn": ("hn_hn", "hn_hcm"),
    "site_hcm": ("hcm_hcm", "hn_hcm"),
    "site_hn": ("hn_hn", "hcm_hn"),
    **{flow: (flow,) for flow in FLOWS},
    "hcm": ("hcm_hcm", "hn_hcm"),
    "hn": ("hn_hn", "hcm_hn"),
}
REGIONS = tuple(REGION_FLOWS)


@dataclass(frozen=True, slots=True)
class RevenueRow:
    day: date
    key: str
    user_id: uuid.UUID | None
    source: RevenueSource | None
    #: Served at the HCM site ("Doanh số SG", or the ◆ source's amount).
    amount: Decimal
    #: Served at a Hà Nội branch ("Doanh số HN").
    amount_hn: Decimal = Decimal(0)
    #: Of ``amount`` / ``amount_hn``: what the HN marketing team brought (HN
    #: CRM accounts); the rest is the HCM team's. Typed lines: 0.
    hn_team_hcm: Decimal = Decimal(0)
    hn_team_hn: Decimal = Decimal(0)
    #: A ◆ Hotline line from the CRM: its hotline (FB / TIKTOK / GOOGLE).
    kind: str | None = None

    @property
    def gross(self) -> Decimal:
        """A marketer's KPI revenue: SG + HN."""
        return self.amount + self.amount_hn

    def flows(self) -> dict[str, Decimal]:
        """The line split into the four flows (see :data:`FLOWS`)."""
        if self.source is RevenueSource.HN and not (self.hn_team_hcm or self.hn_team_hn):
            # Typed "HN chuyển HCM": Hà Nội's customers served in HCM.
            return {**dict.fromkeys(FLOWS, Decimal(0)), "hn_hcm": self.gross}
        return {
            "hcm_hcm": self.amount - self.hn_team_hcm,
            "hn_hn": self.hn_team_hn,
            "hn_hcm": self.hn_team_hcm,
            "hcm_hn": self.amount_hn - self.hn_team_hn,
        }


def region_amount(row: RevenueRow, region: str) -> Decimal:
    """A revenue line's amount for a "Miền" choice (:data:`REGION_FLOWS`)."""
    flows = row.flows()
    return sum((flows[flow] for flow in REGION_FLOWS.get(region, FLOWS)), Decimal(0))


def in_region(row: RevenueRow, region: str) -> RevenueRow | None:
    """The line cut to the flows of a "Miền" choice, ``None`` when nothing of
    it is left - for screens that split SG / HN themselves."""
    keep = REGION_FLOWS.get(region, FLOWS)
    if keep == FLOWS:
        return row
    flows = {flow: value if flow in keep else Decimal(0) for flow, value in row.flows().items()}
    if not any(flows.values()):
        return None
    return replace(
        row,
        amount=flows["hcm_hcm"] + flows["hn_hcm"],
        amount_hn=flows["hn_hn"] + flows["hcm_hn"],
        hn_team_hcm=flows["hn_hcm"],
        hn_team_hn=flows["hn_hn"],
    )


#: The save payload names a marketer's HN part ``u:<id>:hn``.
HN_SUFFIX = ":hn"


def month_start(day: date) -> date:
    return day.replace(day=1)


def _amount(value: object, key: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if isinstance(value, int | float | Decimal):
            amount = Decimal(str(value))
        else:
            # Typed text in the Vietnamese format: "1.500.000" (or "1,500,000").
            amount = Decimal(str(value).replace(".", "").replace(",", "").strip())
    except InvalidOperation as error:
        raise AdsRevenueInvalidError("Số tiền không hợp lệ.", key) from error
    if amount < 0 or amount > MAX_AMOUNT:
        raise AdsRevenueInvalidError("Số tiền phải từ 0 đến 10.000 tỷ.", key)
    return amount


class AdsRevenueService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    @staticmethod
    def _require_edit(access: AdsAccess) -> None:
        access.require_view()
        if not access.edit_revenue:
            raise AdsRevenueForbiddenError()

    async def _lines(self, *, with_sources: bool, with_team: bool) -> list[EntryLine]:
        lines: list[EntryLine] = []
        if with_team:
            lines.append(EntryLine(TEAM_KEY, "Cả phòng", None, None, None, None))
        for user in await ads_people(self._session):
            lines.append(
                EntryLine(
                    subject_key(user_id=user.id),
                    user.full_name,
                    user.id,
                    None,
                    user.member_code,
                    None,
                )
            )
        if with_sources:
            for source in RevenueSource:
                lines.append(
                    EntryLine(
                        subject_key(source=source),
                        f"◆ {REVENUE_SOURCE_LABELS[source]}",
                        None,
                        source,
                        None,
                        None,
                    )
                )
        return lines

    # --- revenue ------------------------------------------------------------

    async def day(self, access: AdsAccess, day: date) -> list[EntryLine]:
        self._require_edit(access)
        stored = {
            row.subject_key: row
            for row in await self._session.scalars(
                select(AdsRevenueDaily).where(AdsRevenueDaily.day == day)
            )
        }
        lines = await self._lines(with_sources=True, with_team=False)
        known = {line.key for line in lines}
        book = await crm_book(self._session, day, day)
        crm = book.totals(day)
        # A person who left the department keeps a line while it has a number;
        # so does somebody outside it holding a CRM account.
        extra = [
            EntryLine(key, "Người đã rời phòng", row.user_id, None, None, None)
            for key, row in stored.items()
            if key not in known
        ]
        extra += [
            EntryLine(
                key,
                f"{book.labels[key]} (đã nghỉ)" if key in book.labels else "Ngoài Phòng Ads (CRM)",
                line.user_id,
                line.source,
                None,
                None,
            )
            for key, line in crm.items()
            if key not in known and key not in stored
        ]

        def number(value: Decimal | None) -> float | None:
            return float(value) if value is not None else None

        def entry(line: EntryLine) -> EntryLine:
            if book.covers(day, line.key):
                got = crm.get(line.key)
                return EntryLine(
                    line.key,
                    line.label,
                    line.user_id,
                    line.source,
                    line.member_code,
                    number(got.amount) if got else 0.0,
                    (number(got.amount_hn) if got else 0.0) if line.user_id else None,
                    from_crm=True,
                )
            return EntryLine(
                line.key,
                line.label,
                line.user_id,
                line.source,
                line.member_code,
                number(stored[line.key].amount) if line.key in stored else None,
                number(stored[line.key].amount_hn) if line.key in stored else None,
            )

        return [entry(line) for line in [*lines, *extra]]

    async def save(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        day: date,
        entries: Mapping[str, object],
    ) -> list[EntryLine]:
        self._require_edit(access)
        lines = {line.key: line for line in await self._lines(with_sources=True, with_team=False)}
        stored = {
            row.subject_key: row
            for row in await self._session.scalars(
                select(AdsRevenueDaily).where(AdsRevenueDaily.day == day)
            )
        }
        now = utcnow()
        changed = 0
        book = await crm_book(self._session, day, day)
        for raw_key, raw in entries.items():
            hn = raw_key.endswith(HN_SUFFIX)
            key = raw_key[: -len(HN_SUFFIX)] if hn else raw_key
            if book.covers(day, key):
                raise AdsRevenueFromCrmError(raw_key)
            if key not in lines and key not in stored:
                raise AdsRevenueInvalidError("Dòng doanh thu không hợp lệ.", raw_key)
            line = lines.get(key)
            if hn and (line is None or line.user_id is None):
                raise AdsRevenueInvalidError("Chỉ doanh số của một người mới tách HN.", raw_key)
            amount = _amount(raw, raw_key)
            row = stored.get(key)
            old = (row.amount_hn if hn else row.amount) if row is not None else None
            if old == amount:
                continue
            if row is None:
                assert line is not None
                row = AdsRevenueDaily(
                    day=day,
                    subject_key=key,
                    user_id=line.user_id,
                    source=line.source,
                )
                self._session.add(row)
                stored[key] = row
            if hn:
                row.amount_hn = amount
            else:
                row.amount = amount
            row.updated_by_user_id = actor.user_id
            row.updated_at = now
            self._session.add(
                AdsRevenueChange(
                    day=day,
                    subject_key=key,
                    part="hn" if hn else "sg",
                    old_amount=old,
                    new_amount=amount,
                    changed_by_user_id=actor.user_id,
                    changed_at=now,
                )
            )
            changed += 1
        # A line with neither part left is "not entered" again.
        for key, row in list(stored.items()):
            if row.amount is None and row.amount_hn is None:
                if row in self._session.new:
                    self._session.expunge(row)
                else:
                    await self._session.delete(row)
                del stored[key]
        await self._session.flush()
        if changed:
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.ADS_REVENUE_SAVED.value,
                result=AuditResult.SUCCESS,
                entity_type="ads_revenue",
                entity_id=day.isoformat(),
                after_data={"changed_lines": changed},
            )
        return await self.day(access, day)

    async def rows(self, start: date, end: date) -> list[RevenueRow]:
        """Every line in the days, for the dashboards: the CRM's thực thu
        (0083) for whoever holds a CRM account on the days already synced,
        what was typed by hand for everybody / every day else."""
        book = await crm_book(self._session, start, end)
        typed = [
            RevenueRow(
                row.day,
                row.subject_key,
                row.user_id,
                row.source,
                row.amount or Decimal(0),
                row.amount_hn or Decimal(0),
            )
            for row in await self._session.scalars(
                select(AdsRevenueDaily).where(
                    AdsRevenueDaily.day >= start, AdsRevenueDaily.day <= end
                )
            )
            if not book.covers(row.day, row.subject_key)
        ]
        return typed + [
            RevenueRow(
                line.day,
                line.key,
                line.user_id,
                line.source,
                line.amount,
                line.amount_hn,
                line.hn_team_hcm,
                line.hn_team_hn,
                line.kind,
            )
            for line in book.lines
        ]

    # --- KPIs ---------------------------------------------------------------

    async def kpis(self, access: AdsAccess, month: date) -> list[EntryLine]:
        access.require_view()
        stored = await self.kpi_amounts(month_start(month))
        return [
            EntryLine(
                line.key,
                line.label,
                line.user_id,
                None,
                line.member_code,
                float(stored[line.key]) if line.key in stored else None,
            )
            for line in await self._lines(with_sources=False, with_team=True)
        ]

    async def kpi_amounts(self, month: date) -> dict[str, Decimal]:
        return {
            row.subject_key: row.amount
            for row in await self._session.scalars(
                select(AdsKpi).where(AdsKpi.month == month_start(month))
            )
        }

    async def save_kpis(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        month: date,
        entries: Mapping[str, object],
    ) -> list[EntryLine]:
        self._require_edit(access)
        first = month_start(month)
        lines = {line.key: line for line in await self._lines(with_sources=False, with_team=True)}
        stored = {
            row.subject_key: row
            for row in await self._session.scalars(select(AdsKpi).where(AdsKpi.month == first))
        }
        now = utcnow()
        for key, raw in entries.items():
            if key not in lines:
                raise AdsRevenueInvalidError("Dòng KPI không hợp lệ.", key)
            amount = _amount(raw, key)
            row = stored.get(key)
            if amount is None:
                if row is not None:
                    await self._session.delete(row)
            elif row is None:
                self._session.add(
                    AdsKpi(
                        month=first,
                        subject_key=key,
                        user_id=lines[key].user_id,
                        amount=amount,
                        updated_by_user_id=actor.user_id,
                        updated_at=now,
                    )
                )
            elif row.amount != amount:
                row.amount = amount
                row.updated_by_user_id = actor.user_id
                row.updated_at = now
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_KPI_SAVED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_kpi",
            entity_id=first.isoformat(),
            after_data={key: str(value) for key, value in entries.items()},
        )
        return await self.kpis(access, first)


__all__ = [
    "FLOWS",
    "FLOW_LABELS",
    "REGIONS",
    "REGION_FLOWS",
    "TEAM_KEY",
    "AdsRevenueForbiddenError",
    "AdsRevenueFromCrmError",
    "AdsRevenueInvalidError",
    "AdsRevenueService",
    "EntryLine",
    "RevenueRow",
    "in_region",
    "month_start",
    "region_amount",
]
