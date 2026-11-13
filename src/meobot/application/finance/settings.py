"""Tài chính > Cấu hình (0105): what the revenue reports need set by hand.

* each CRM branch -> its pháp nhân (``legal_entities``) and APM cơ sở
  (user 12/11: "Chi nhánh bên CRM sẽ gắn với các pháp nhân bên apm. Có màn
  config"); a new CRM branch shows "Chưa gắn" until set. No effective date
  (R2 A): changing it moves the old months too;
* each CRM person -> the APM account they are (R5 B: by hand);
* the VAT rate used when a payment's order is not known: a default and one
  per pháp nhân (R3: "có mục config").

Every write needs ``FINANCE SETTINGS`` and is audited.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import String as String_
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.application.finance.revenue import FINANCE_SETTINGS_KEY
from meobot.core.time import utcnow
from meobot.db.models.finance import (
    CrmBranchRecord,
    CrmOrderRecord,
    CrmStaffRecord,
    FinanceServiceCost,
)
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.crm import DEFAULT_DEBT_MILESTONES, DEFAULT_VAT_PERCENT, FEE_METHODS
from meobot.domain.finance.errors import FinanceInvalidError, FinanceNotFoundError
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import BRANCHES

#: The BCLT sheet's PHÍ QUẸT when "CP QUẸT THẺ" has nothing: Thẻ x 2%.
DEFAULT_CARD_FEE: dict[int, Decimal] = {2: Decimal(2)}
#: A page of the CRM people list.
STAFF_PAGE = 100


@dataclass(frozen=True, slots=True)
class StaffPage:
    rows: list[tuple[CrmStaffRecord, str | None]]
    total: int
    unmapped: int


class FinanceSettingsService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- branches ----------------------------------------------------------------

    async def branches(self) -> list[CrmBranchRecord]:
        return list(
            (
                await self._session.scalars(select(CrmBranchRecord).order_by(CrmBranchRecord.name))
            ).all()
        )

    async def entities(self) -> list[LegalEntity]:
        return list(
            (
                await self._session.scalars(
                    select(LegalEntity).order_by(LegalEntity.sort_order, LegalEntity.code)
                )
            ).all()
        )

    async def update_branch(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        branch_id: int,
        changes: dict[str, Any],
    ) -> CrmBranchRecord:
        """``changes`` holds only the keys sent: ``legal_entity_id`` and / or
        ``apm_branch`` (``None`` clears)."""
        access.require_settings()
        row = await self._session.get(CrmBranchRecord, branch_id)
        if row is None:
            raise FinanceNotFoundError("finance_branch_not_found", "Không tìm thấy chi nhánh CRM.")
        before: dict[str, object] = {
            "legal_entity_id": str(row.legal_entity_id or ""),
            "apm_branch": row.apm_branch,
        }
        if "legal_entity_id" in changes:
            value = changes["legal_entity_id"]
            if value is not None:
                entity = await self._session.get(LegalEntity, value)
                if entity is None:
                    raise FinanceInvalidError(
                        "legal_entity_invalid", "Pháp nhân không có.", field="legal_entity_id"
                    )
            row.legal_entity_id = value
        if "apm_branch" in changes:
            value = changes["apm_branch"]
            if value is not None and value not in BRANCHES:
                raise FinanceInvalidError("branch_invalid", "Cơ sở không có.", field="apm_branch")
            row.apm_branch = value
        row.updated_by_user_id = actor.user_id
        row.updated_at = utcnow()
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_BRANCH_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="crm_branch",
            entity_id=str(row.id),
            before_data=before,
            after_data={
                "legal_entity_id": str(row.legal_entity_id or ""),
                "apm_branch": row.apm_branch,
            },
        )
        return row

    # --- people --------------------------------------------------------------------

    async def staff(self, *, q: str = "", unmapped: bool = False, page: int = 1) -> StaffPage:
        statement = select(CrmStaffRecord)
        text = q.strip()
        if text:
            statement = statement.where(
                or_(
                    CrmStaffRecord.name.ilike(f"%{text}%"),
                    func.cast(CrmStaffRecord.id, String_()).ilike(f"%{text}%"),
                )
            )
        if unmapped:
            statement = statement.where(CrmStaffRecord.user_id.is_(None))
        total = int(
            await self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        missing = int(
            await self._session.scalar(select(func.count()).where(CrmStaffRecord.user_id.is_(None)))
            or 0
        )
        rows = (
            await self._session.scalars(
                statement.order_by(CrmStaffRecord.name, CrmStaffRecord.id)
                .offset((max(page, 1) - 1) * STAFF_PAGE)
                .limit(STAFF_PAGE)
            )
        ).all()
        names = {
            user.id: user.full_name
            for user in await self._session.scalars(
                select(User).where(
                    User.id.in_({row.user_id for row in rows if row.user_id} or {uuid.uuid4()})
                )
            )
        }
        return StaffPage(
            rows=[(row, names.get(row.user_id) if row.user_id else None) for row in rows],
            total=total,
            unmapped=missing,
        )

    async def people(self) -> list[User]:
        """The accounts a CRM person may be mapped to."""
        return list(
            (
                await self._session.scalars(
                    select(User)
                    .where(User.deleted_at.is_(None), User.status == UserStatus.ACTIVE)
                    .order_by(User.full_name)
                )
            ).all()
        )

    async def update_staff(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        crm_id: int,
        user_id: uuid.UUID | None,
    ) -> CrmStaffRecord:
        access.require_settings()
        row = await self._session.get(CrmStaffRecord, crm_id)
        if row is None:
            raise FinanceNotFoundError("finance_staff_not_found", "Không tìm thấy nhân viên CRM.")
        if user_id is not None:
            user = await self._session.get(User, user_id)
            if user is None or user.deleted_at is not None:
                raise FinanceInvalidError("user_invalid", "Tài khoản không có.", field="user_id")
        before = str(row.user_id or "")
        row.user_id = user_id
        row.updated_by_user_id = actor.user_id
        row.updated_at = utcnow()
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_STAFF_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="crm_staff",
            entity_id=str(row.id),
            before_data={"user_id": before},
            after_data={"user_id": str(user_id or "")},
        )
        return row

    # --- VAT -------------------------------------------------------------------------

    async def _row(self) -> SystemSetting | None:
        row: SystemSetting | None = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == FINANCE_SETTINGS_KEY)
        )
        return row

    async def vat(self) -> tuple[Decimal, dict[str, Decimal]]:
        row = await self._row()
        value = dict(row.value) if row is not None else {}
        return (
            Decimal(str(value.get("vat_default", DEFAULT_VAT_PERCENT))),
            {
                str(key): Decimal(str(rate))
                for key, rate in (value.get("vat_by_entity") or {}).items()
            },
        )

    async def set_vat(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        default: str | float,
        by_entity: dict[str, str | float | None],
    ) -> tuple[Decimal, dict[str, Decimal]]:
        """The default rate and the pháp nhân rates (``None`` = use the default)."""
        access.require_settings()

        def rate(value: object, field: str) -> Decimal:
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError) as error:
                raise FinanceInvalidError(
                    "vat_invalid", "Thuế suất phải là số.", field=field
                ) from error
            if not Decimal(0) <= number <= Decimal(100):
                raise FinanceInvalidError("vat_invalid", "Thuế suất từ 0 đến 100.", field=field)
            return number.quantize(Decimal("0.01"))

        known = {str(entity.id) for entity in await self.entities()}
        cleaned_default = rate(default, "vat_default")
        cleaned: dict[str, Decimal] = {}
        for key, given in by_entity.items():
            if key not in known:
                raise FinanceInvalidError(
                    "legal_entity_invalid", "Pháp nhân không có.", field="vat_by_entity"
                )
            if given is not None and str(given).strip() != "":
                cleaned[key] = rate(given, f"vat_by_entity.{key}")
        row = await self._row()
        before = dict(row.value) if row is not None else {}
        value = {
            **before,
            "vat_default": str(cleaned_default),
            "vat_by_entity": {key: str(item) for key, item in cleaned.items()},
        }
        if row is None:
            self._session.add(
                SystemSetting(
                    key=FINANCE_SETTINGS_KEY,
                    value=value,
                    description="Tài chính: thuế suất VAT mặc định và theo pháp nhân.",
                    updated_by=actor.user_id,
                )
            )
        else:
            row.value = value
            row.version = int(row.version) + 1
            row.updated_by = actor.user_id
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_SETTINGS_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_settings",
            entity_id=FINANCE_SETTINGS_KEY,
            before_data={
                "vat_default": before.get("vat_default"),
                "vat_by_entity": before.get("vat_by_entity"),
            },
            after_data={
                "vat_default": value["vat_default"],
                "vat_by_entity": value["vat_by_entity"],
            },
        )
        return cleaned_default, cleaned

    # --- đợt 2: fee rules, service costs, công nợ, KPI --------------------------------

    async def _value(self) -> dict[str, Any]:
        row = await self._row()
        return dict(row.value) if row is not None else {}

    async def _save(self, actor: Actor, changes: dict[str, Any]) -> dict[str, Any]:
        row = await self._row()
        value = {**(dict(row.value) if row is not None else {}), **changes}
        if row is None:
            self._session.add(
                SystemSetting(
                    key=FINANCE_SETTINGS_KEY,
                    value=value,
                    description="Tài chính: VAT, phí, công nợ.",
                    updated_by=actor.user_id,
                )
            )
        else:
            row.value = value
            row.version = int(row.version) + 1
            row.updated_by = actor.user_id
        await self._session.flush()
        return value

    async def _audit_settings(
        self, actor: Actor, request_id: uuid.UUID, what: str, after: dict[str, object]
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_SETTINGS_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_settings",
            entity_id=what,
            after_data=after,
        )

    async def fee_rules(self) -> dict[int, Decimal]:
        """Payment method -> card / instalment / collection fee (%). Until set,
        the accounting sheet's own rule: 2% of what was paid by card."""
        value = await self._value()
        if "card_fee_pct" not in value:
            return dict(DEFAULT_CARD_FEE)
        raw = value.get("card_fee_pct") or {}
        return {int(key): Decimal(str(rate)) for key, rate in raw.items()}

    async def set_fee_rules(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        rates: dict[int, str | None],
    ) -> dict[int, Decimal]:
        access.require_settings()
        cleaned: dict[int, Decimal] = {}
        for method, given in rates.items():
            if method not in FEE_METHODS:
                raise FinanceInvalidError(
                    "fee_method_invalid", "Phương thức thanh toán không có.", field="rates"
                )
            if given is None or str(given).strip() == "":
                continue
            cleaned[method] = _percent(given, f"rates.{method}")
        await self._save(actor, {"card_fee_pct": {str(k): str(v) for k, v in cleaned.items()}})
        await self._audit_settings(
            actor, request_id, "card_fee_pct", {str(k): str(v) for k, v in cleaned.items()}
        )
        return cleaned

    async def debt_settings(self) -> tuple[tuple[int, ...], list[uuid.UUID]]:
        value = await self._value()
        marks = tuple(int(day) for day in value.get("debt_milestones") or DEFAULT_DEBT_MILESTONES)
        recipients = [uuid.UUID(item) for item in value.get("debt_recipients") or []]
        return marks, recipients

    async def set_debt_settings(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        milestones: list[int],
        recipients: list[uuid.UUID],
    ) -> tuple[tuple[int, ...], list[uuid.UUID]]:
        access.require_settings()
        marks = tuple(sorted(set(milestones)))
        if not marks or len(marks) > 6 or any(not 1 <= day <= 3650 for day in marks):
            raise FinanceInvalidError(
                "debt_milestones_invalid",
                "Các mốc công nợ: 1-6 mốc, mỗi mốc từ 1 đến 3650 ngày.",
                field="milestones",
            )
        found = {
            user.id
            for user in await self._session.scalars(
                select(User).where(
                    User.id.in_(recipients or [uuid.uuid4()]), User.deleted_at.is_(None)
                )
            )
        }
        if set(recipients) - found or len(recipients) > 20:
            raise FinanceInvalidError(
                "debt_recipients_invalid", "Người nhận cảnh báo không hợp lệ.", field="recipients"
            )
        unique = list(dict.fromkeys(recipients))
        await self._save(
            actor,
            {"debt_milestones": list(marks), "debt_recipients": [str(item) for item in unique]},
        )
        await self._audit_settings(
            actor,
            request_id,
            "debt",
            {"milestones": list(marks), "recipients": [str(item) for item in unique]},
        )
        return marks, unique

    async def service_costs(self, *, q: str = "") -> list[tuple[int, str, Decimal | None]]:
        """Every service seen on an order (``crm_orders.items``) with its cost."""
        seen: dict[int, str] = {}
        for items in (await self._session.scalars(select(CrmOrderRecord.items))).all():
            for item in items or []:
                key = item.get("service_id")
                if key is not None:
                    seen.setdefault(int(key), str(item.get("service_name") or f"Dịch vụ {key}"))
        costs = {
            row.service_id: row for row in await self._session.scalars(select(FinanceServiceCost))
        }
        for key, row in costs.items():
            seen.setdefault(key, row.name)
        text = fold(q)
        return sorted(
            (
                (key, name, Decimal(costs[key].cost) if key in costs else None)
                for key, name in seen.items()
                if not text or text in fold(name) or text in str(key)
            ),
            key=lambda item: fold(item[1]),
        )

    async def set_service_cost(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        service_id: int,
        name: str,
        cost: str | None,
    ) -> Decimal | None:
        access.require_settings()
        row = await self._session.get(FinanceServiceCost, service_id)
        if cost is None or str(cost).strip() == "":
            if row is not None:
                await self._session.delete(row)
            value: Decimal | None = None
        else:
            value = _money(cost, "cost")
            if row is None:
                row = FinanceServiceCost(service_id=service_id, name=name[:300] or str(service_id))
                self._session.add(row)
            row.cost = value
            row.updated_by_user_id = actor.user_id
            row.updated_at = utcnow()
        await self._session.flush()
        await self._audit_settings(
            actor,
            request_id,
            f"service_cost:{service_id}",
            {"cost": str(value) if value is not None else None},
        )
        return value


def _percent(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as error:
        raise FinanceInvalidError("percent_invalid", "Phải là số.", field=field) from error
    if not Decimal(0) <= number <= Decimal(100):
        raise FinanceInvalidError("percent_invalid", "Từ 0 đến 100.", field=field)
    return number.quantize(Decimal("0.01"))


def _money(value: object, field: str) -> Decimal:
    text = str(value).replace(".", "").replace(",", "").replace(" ", "").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as error:
        raise FinanceInvalidError("money_invalid", "Số tiền phải là số.", field=field) from error
    if number < 0 or number > Decimal("100000000000"):
        raise FinanceInvalidError("money_invalid", "Số tiền không hợp lệ.", field=field)
    return number


def fold(text: str) -> str:
    raw = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in raw if unicodedata.category(c) != "Mn").lower().strip()


__all__ = ["STAFF_PAGE", "FinanceSettingsService", "StaffPage", "fold"]
