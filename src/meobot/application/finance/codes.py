"""Tài chính > Mã chi & tuyến duyệt (đợt 3; F1, F2, F4, M2).

The budget codes come from the Sổ tay (seeded by 0107); a ``FINANCE CODES``
holder changes a code's route (purchase approvals, executor, payment
approvals, who pays, papers) or name, each change appended to
``finance_code_changes`` ("có lưu lịch sử"). A request keeps the route it was
sent with. Also here: the optional amount above which PTGĐ / CEO is added
(F2, off by default) and the Google Drive folder the papers go to (M2).
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.application.finance.revenue import FINANCE_SETTINGS_KEY
from meobot.core.time import utcnow
from meobot.db.models.finance_spend import FinanceCode, FinanceCodeChange
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.errors import FinanceInvalidError, FinanceNotFoundError
from meobot.domain.finance.spend import (
    ACCOUNTANTS,
    FinanceRole,
    Route,
)
from meobot.domain.identity.models import Actor

#: Who may sit in the purchase / payment approvals and who may pay.
PURCHASE_ROLES = (
    FinanceRole.DEPT_HEAD,
    FinanceRole.BLOCK_DIRECTOR,
    FinanceRole.CFO,
    FinanceRole.CHIEF_ACCOUNTANT,
    FinanceRole.CEO,
)
PAYMENT_ROLES = (*PURCHASE_ROLES, *sorted(ACCOUNTANTS))
EXECUTOR_ROLES = (
    FinanceRole.REQUESTER,
    FinanceRole.EXEC_HCMS,
    FinanceRole.EXEC_HR,
    FinanceRole.EXEC_DRIVER,
    FinanceRole.EXEC_SECRETARY,
    FinanceRole.ACC_WAREHOUSE,
)
CASH_ROLES = (FinanceRole.FUND_BRANCH, FinanceRole.CASHIER_VP)


class FinanceCodeService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def codes(self, *, active_only: bool = False) -> list[FinanceCode]:
        statement = select(FinanceCode).order_by(FinanceCode.sort_order)
        if active_only:
            statement = statement.where(FinanceCode.active.is_(True))
        return list((await self._session.scalars(statement)).all())

    async def code(self, code: str) -> FinanceCode:
        row = await self._session.get(FinanceCode, code)
        if row is None:
            raise FinanceNotFoundError("finance_code_not_found", "Không có mã chi này.")
        return row

    async def history(self, code: str) -> list[FinanceCodeChange]:
        return list(
            (
                await self._session.scalars(
                    select(FinanceCodeChange)
                    .where(FinanceCodeChange.code == code)
                    .order_by(FinanceCodeChange.id.desc())
                )
            ).all()
        )

    async def update(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        code: str,
        name: str | None,
        active: bool | None,
        route: dict[str, Any] | None,
    ) -> FinanceCode:
        access.require("codes", "Chỉ người có quyền cấu hình tuyến duyệt sửa được mã chi.")
        row = await self.code(code)
        before = {"name": row.name, "active": row.active, "route": dict(row.route or {})}
        if name is not None:
            text = name.strip()
            if not text or len(text) > 300:
                raise FinanceInvalidError("code_name_invalid", "Tên mã chi 1-300 ký tự.")
            row.name = text
        if active is not None:
            row.active = active
        if route is not None:
            if not row.leaf:
                raise FinanceInvalidError(
                    "code_is_group", "Mã nhóm không có tuyến duyệt; sửa từng mã con."
                )
            row.route = _check_route(route).as_value()
        row.updated_by_user_id = actor.user_id
        row.updated_at = utcnow()
        after = {"name": row.name, "active": row.active, "route": dict(row.route or {})}
        if after != before:
            self._session.add(
                FinanceCodeChange(
                    code=row.code, before=before, after=after, changed_by_user_id=actor.user_id
                )
            )
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_CODE_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_code",
            entity_id=row.code,
            before_data=before,
            after_data=after,
        )
        return row

    # --- F2 / M2 settings ----------------------------------------------------------------

    async def _value(self) -> tuple[SystemSetting | None, dict[str, Any]]:
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == FINANCE_SETTINGS_KEY)
        )
        return row, (dict(row.value) if row is not None else {})

    async def spend_settings(self) -> dict[str, Any]:
        _, value = await self._value()
        threshold = value.get("ceo_threshold")
        return {
            "ceo_threshold": Decimal(str(threshold)) if threshold not in (None, "") else None,
            "drive_folder_id": value.get("docs_drive_folder_id") or None,
        }

    async def set_spend_settings(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        ceo_threshold: str | None,
        drive_folder_id: str | None,
    ) -> dict[str, Any]:
        access.require("codes", "Chỉ người có quyền cấu hình tuyến duyệt sửa được.")
        threshold: Decimal | None = None
        if ceo_threshold not in (None, ""):
            text = str(ceo_threshold).replace(".", "").replace(",", "").replace(" ", "")
            try:
                threshold = Decimal(text)
            except (InvalidOperation, ValueError) as error:
                raise FinanceInvalidError(
                    "threshold_invalid", "Ngưỡng phải là số tiền.", field="ceo_threshold"
                ) from error
            if threshold <= 0:
                raise FinanceInvalidError(
                    "threshold_invalid", "Ngưỡng phải lớn hơn 0.", field="ceo_threshold"
                )
        folder = (drive_folder_id or "").strip() or None
        if folder is not None and (
            len(folder) > 200 or not folder.replace("-", "").replace("_", "").isalnum()
        ):
            raise FinanceInvalidError(
                "drive_folder_invalid",
                "Mã thư mục Google Drive không hợp lệ.",
                field="drive_folder_id",
            )
        row, value = await self._value()
        value = {
            **value,
            "ceo_threshold": str(threshold) if threshold is not None else None,
            "docs_drive_folder_id": folder,
        }
        if row is None:
            self._session.add(
                SystemSetting(
                    key=FINANCE_SETTINGS_KEY,
                    value=value,
                    description="Tài chính: cài đặt.",
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
            entity_id="spend",
            after_data={"ceo_threshold": value["ceo_threshold"], "drive_folder_id": folder},
        )
        return await self.spend_settings()


def _check_route(value: dict[str, Any]) -> Route:
    try:
        route = Route.from_value(value)
    except ValueError as error:
        raise FinanceInvalidError(
            "route_invalid", "Tuyến duyệt có vai trò không hợp lệ."
        ) from error
    if any(role not in PURCHASE_ROLES for role in route.purchase):
        raise FinanceInvalidError("route_invalid", "Bước duyệt mua sắm không hợp lệ.")
    if any(role not in PAYMENT_ROLES for role in route.payment):
        raise FinanceInvalidError("route_invalid", "Bước duyệt thanh toán không hợp lệ.")
    if route.executor is not None and route.executor not in EXECUTOR_ROLES:
        raise FinanceInvalidError("route_invalid", "Bộ phận thực hiện mua sắm không hợp lệ.")
    if any(role not in CASH_ROLES for role in route.cash):
        raise FinanceInvalidError("route_invalid", "Người chi tiền không hợp lệ.")
    if len(set(route.purchase)) != len(route.purchase) or len(set(route.payment)) != len(
        route.payment
    ):
        raise FinanceInvalidError("route_invalid", "Một vai trò chỉ một lần trong mỗi giai đoạn.")
    return route


__all__ = [
    "CASH_ROLES",
    "EXECUTOR_ROLES",
    "PAYMENT_ROLES",
    "PURCHASE_ROLES",
    "FinanceCodeService",
]
