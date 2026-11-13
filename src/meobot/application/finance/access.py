"""Who uses Tài chính (0105). See :mod:`meobot.domain.finance.permissions`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import Access
from meobot.application.org.developing import hidden_modules
from meobot.domain.finance.errors import FinanceForbiddenError
from meobot.domain.finance.permissions import MODULE_FINANCE, FinancePermission
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMembership


@dataclass(frozen=True, slots=True)
class FinanceAccess:
    user_id: uuid.UUID | None
    revenue: bool = False
    customers: bool = False
    settings: bool = False
    #: Type the four fees on the bảng kê (đợt 2).
    fees: bool = False
    #: Đợt 3: file hồ sơ chi (everybody while the module is visible, P6 B),
    #: see every one, budget codes / roles, budgets, MISA, thu - chi.
    requests: bool = False
    view_requests: bool = False
    codes: bool = False
    budget: bool = False
    misa: bool = False
    report: bool = False

    @property
    def sees(self) -> bool:
        return self.revenue or self.settings or self.requests

    def require(self, flag: str, message: str = "Bạn chưa có quyền làm việc này.") -> None:
        if not getattr(self, flag):
            raise FinanceForbiddenError(message)

    def require_revenue(self) -> None:
        if not self.revenue:
            raise FinanceForbiddenError("Bạn chưa có quyền xem doanh thu.")

    def require_fees(self) -> None:
        if not self.fees:
            raise FinanceForbiddenError("Bạn chưa có quyền nhập phí.")

    def require_settings(self) -> None:
        if not self.settings:
            raise FinanceForbiddenError("Chỉ người có quyền cấu hình Tài chính làm được việc này.")


def finance_access(membership: UnitMembership, *, hidden: bool = False) -> FinanceAccess:
    """``hidden`` = the module is still being built and the caller is not the
    OWNER (:mod:`meobot.application.org.developing`): nothing at all."""
    if hidden:
        return FinanceAccess(membership.user_id)
    if membership.sees_all:
        return FinanceAccess(
            membership.user_id,
            revenue=True,
            customers=True,
            settings=True,
            fees=True,
            requests=True,
            view_requests=True,
            codes=True,
            budget=True,
            misa=True,
            report=True,
        )
    access = membership.access if isinstance(membership.access, Access) else None

    def holds(permission: FinancePermission) -> bool:
        return access is not None and access.scope(MODULE_FINANCE, permission.value) == "ALL"

    return FinanceAccess(
        membership.user_id,
        revenue=holds(FinancePermission.REVENUE),
        customers=holds(FinancePermission.CUSTOMERS),
        settings=holds(FinancePermission.SETTINGS),
        fees=holds(FinancePermission.FEES),
        requests=membership.user_id is not None,
        view_requests=holds(FinancePermission.VIEW_REQUESTS),
        codes=holds(FinancePermission.CODES),
        budget=holds(FinancePermission.BUDGET),
        misa=holds(FinancePermission.MISA),
        report=holds(FinancePermission.REPORT),
    )


async def load_finance_access(session: AsyncSession, membership: UnitMembership) -> FinanceAccess:
    role = Role.OWNER if membership.is_owner else None
    hidden = MODULE_FINANCE in await hidden_modules(session, role)
    return finance_access(membership, hidden=hidden)


__all__ = ["FinanceAccess", "finance_access", "load_finance_access"]
