"""The "Tài khoản QC" screen (0078): every ad account synced from the BMs, and
who runs it.

An account is given to a marketer by its id - not guessed from its name like
the Sheet's keyword list - by a Trưởng phòng ADS or the OWNER / an ADMIN; only
people seated in the ADS department can receive one. A new account starts
"Chưa gán": its spend counts for the department, not for anybody.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.access import AdsAccess
from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsAccount, AdsChargeDaily, AdsConnection, AdsSpendDaily
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.user import User
from meobot.domain.ads.models import ADS_DEPARTMENT_CODE, AdsBrand, AdsPlatform, Period
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor


class AdsAccountForbiddenError(AuthorizationError):
    code = "ads_account_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ Trưởng phòng ADS (hoặc chủ sở hữu, quản trị viên) gán được tài khoản.",
            details={"reason": "ads_account_forbidden"},
        )


class AdsAccountNotFoundError(NotFoundError):
    code = "ads_account_not_found"

    def __init__(self) -> None:
        super().__init__(
            "Không tìm thấy tài khoản quảng cáo.", details={"reason": "ads_account_not_found"}
        )


class AdsAssigneeInvalidError(ValidationError):
    code = "ads_assignee_invalid"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ gán được cho người trong Phòng ADS.", details={"reason": "ads_assignee_invalid"}
        )


@dataclass(frozen=True, slots=True)
class AccountView:
    account: AdsAccount
    #: ``None`` for a typed-in Google / TikTok account (0081).
    connection: AdsConnection | None
    user: User | None
    spend: float
    charged: float


async def ads_people(session: AsyncSession) -> list[User]:
    """Everybody with an open seat in the ADS department, by name."""
    rows = await session.scalars(
        select(User)
        .join(DepartmentMember, DepartmentMember.user_id == User.id)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(
            Department.code == ADS_DEPARTMENT_CODE,
            DepartmentMember.left_at.is_(None),
            User.deleted_at.is_(None),
        )
        .distinct()
    )
    return sorted(rows, key=lambda user: user.full_name.casefold())


class AdsAccountService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def accounts(
        self,
        access: AdsAccess,
        *,
        period: Period,
        connection_id: uuid.UUID | None = None,
        unassigned: bool = False,
        query: str | None = None,
        platform: AdsPlatform | None = None,
    ) -> list[AccountView]:
        access.require_view()
        statement = select(AdsAccount, AdsConnection).outerjoin(
            AdsConnection, AdsConnection.id == AdsAccount.connection_id
        )
        if platform is not None:
            statement = statement.where(AdsAccount.platform == platform)
        if not access.view_all:
            statement = statement.where(AdsAccount.user_id == access.user_id)
        if connection_id is not None:
            statement = statement.where(AdsAccount.connection_id == connection_id)
        if unassigned:
            statement = statement.where(AdsAccount.user_id.is_(None))
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    AdsAccount.name.ilike(pattern),
                    AdsAccount.account_id.ilike(pattern),
                    AdsAccount.external_id.ilike(pattern),
                )
            )
        rows = list(await self._session.execute(statement.order_by(AdsAccount.name)))
        ids = [account.account_id for account, _ in rows]
        spend = await self._sum(AdsSpendDaily, AdsSpendDaily.spend, ids, period)
        charged = await self._sum(AdsChargeDaily, AdsChargeDaily.amount, ids, period)
        user_ids = {account.user_id for account, _ in rows if account.user_id}
        users = (
            {
                user.id: user
                for user in await self._session.scalars(select(User).where(User.id.in_(user_ids)))
            }
            if user_ids
            else {}
        )
        return [
            AccountView(
                account=account,
                connection=connection,
                user=users.get(account.user_id) if account.user_id else None,
                spend=spend.get(account.account_id, 0.0),
                charged=charged.get(account.account_id, 0.0),
            )
            for account, connection in rows
        ]

    async def _sum(
        self, model: Any, column: Any, ids: list[str], period: Period
    ) -> dict[str, float]:
        """``account -> total`` of ``column`` over the period."""
        if not ids:
            return {}
        statement = (
            select(model.account_id, func.sum(column))
            .where(model.account_id.in_(ids), model.day >= period.start, model.day <= period.end)
            .group_by(model.account_id)
        )
        return {
            account: float(total or 0) for account, total in await self._session.execute(statement)
        }

    async def assign(
        self,
        *,
        actor: Actor,
        access: AdsAccess,
        request_id: uuid.UUID,
        account_id: str,
        user_id: uuid.UUID | None,
        clear_user: bool = False,
        brand: AdsBrand | None = None,
    ) -> AdsAccount:
        access.require_view()
        if not access.manage_accounts:
            raise AdsAccountForbiddenError()
        account = await self._session.get(AdsAccount, account_id)
        if account is None:
            raise AdsAccountNotFoundError()
        before: dict[str, object] = {
            "user_id": str(account.user_id) if account.user_id else None,
            "brand": account.brand.value,
        }
        if clear_user:
            account.user_id = None
            account.assigned_by_user_id = actor.user_id
            account.assigned_at = utcnow()
        elif user_id is not None:
            if user_id not in {user.id for user in await ads_people(self._session)}:
                raise AdsAssigneeInvalidError()
            account.user_id = user_id
            account.assigned_by_user_id = actor.user_id
            account.assigned_at = utcnow()
        if brand is not None:
            account.brand = brand
            account.brand_custom = True
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_ACCOUNT_ASSIGNED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_account",
            entity_id=account.account_id,
            before_data=before,
            after_data={
                "user_id": str(account.user_id) if account.user_id else None,
                "brand": account.brand.value,
            },
        )
        return account


def month_of(day: date) -> Period:
    start = day.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return Period(start=start, end=next_month - timedelta(days=1))


__all__ = [
    "AccountView",
    "AdsAccountForbiddenError",
    "AdsAccountNotFoundError",
    "AdsAccountService",
    "AdsAssigneeInvalidError",
    "ads_people",
    "month_of",
]
