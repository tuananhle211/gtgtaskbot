"""Tài chính > Vai trò tài chính (đợt 3; P1, P2, P4, F7).

``DEPT_HEAD`` / ``BLOCK_DIRECTOR`` are read from the org chart for the
requester (the same chain the HR requests use: their head - the one they
report to, else the department's -, their block's office head);
``REQUESTER`` is the requester. Every other role is an assignment made on
"Cấu hình > Vai trò tài chính": a person, optionally for one pháp nhân and / or
one APM cơ sở, with a stand-in. A step gets ONE approver (F7): the assignment
that fits the request best (pháp nhân and cơ sở > pháp nhân > cơ sở > neither);
its stand-in may act too. Writes need ``FINANCE CODES``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.application.hr.absences import HrAbsenceService
from meobot.core.config import Settings
from meobot.db.models.finance_spend import FinanceRoleAssignment
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.errors import FinanceInvalidError, FinanceNotFoundError
from meobot.domain.finance.spend import ASSIGNABLE, FinanceRole
from meobot.domain.hr.attendance import StepLevel
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import BRANCHES


@dataclass(frozen=True, slots=True)
class Holder:
    approver: uuid.UUID | None
    standin: uuid.UUID | None = None


class FinanceRoleService:
    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._chains: dict[uuid.UUID, dict[StepLevel, list[uuid.UUID]]] = {}

    async def assignments(self) -> list[FinanceRoleAssignment]:
        return list(
            (
                await self._session.scalars(
                    select(FinanceRoleAssignment).order_by(
                        FinanceRoleAssignment.role, FinanceRoleAssignment.id
                    )
                )
            ).all()
        )

    async def add(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        legal_entity_id: uuid.UUID | None,
        apm_branch: str | None,
        standin_user_id: uuid.UUID | None,
        note: str | None,
    ) -> FinanceRoleAssignment:
        access.require("codes", "Chỉ người có quyền cấu hình tuyến duyệt gán được vai trò.")
        try:
            finance_role = FinanceRole(role)
        except ValueError as error:
            raise FinanceInvalidError("role_invalid", "Vai trò không có.", field="role") from error
        if finance_role not in ASSIGNABLE:
            raise FinanceInvalidError(
                "role_from_org_chart",
                "Vai trò này lấy theo sơ đồ tổ chức, không gán tay.",
                field="role",
            )
        for person in (user_id, standin_user_id):
            if person is None:
                continue
            user = await self._session.get(User, person)
            if user is None or user.deleted_at is not None:
                raise FinanceInvalidError("user_invalid", "Tài khoản không có.", field="user_id")
        if standin_user_id == user_id:
            raise FinanceInvalidError(
                "standin_invalid", "Người duyệt thay phải là người khác.", field="standin_user_id"
            )
        if (
            legal_entity_id is not None
            and await self._session.get(LegalEntity, legal_entity_id) is None
        ):
            raise FinanceInvalidError(
                "legal_entity_invalid", "Pháp nhân không có.", field="legal_entity_id"
            )
        if apm_branch is not None and apm_branch not in BRANCHES:
            raise FinanceInvalidError("branch_invalid", "Cơ sở không có.", field="apm_branch")
        row = FinanceRoleAssignment(
            role=finance_role.value,
            user_id=user_id,
            legal_entity_id=legal_entity_id,
            apm_branch=apm_branch,
            standin_user_id=standin_user_id,
            note=(note or "").strip()[:300] or None,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._record(actor, request_id, "added", row)
        return row

    async def remove(
        self, *, actor: Actor, access: FinanceAccess, request_id: uuid.UUID, assignment_id: int
    ) -> None:
        access.require("codes", "Chỉ người có quyền cấu hình tuyến duyệt gán được vai trò.")
        row = await self._session.get(FinanceRoleAssignment, assignment_id)
        if row is None:
            raise FinanceNotFoundError("finance_role_not_found", "Không tìm thấy phân vai trò.")
        await self._record(actor, request_id, "removed", row)
        await self._session.delete(row)
        await self._session.flush()

    async def _record(
        self, actor: Actor, request_id: uuid.UUID, what: str, row: FinanceRoleAssignment
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_ROLE_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_role",
            entity_id=str(row.id),
            after_data={
                "change": what,
                "role": row.role,
                "user_id": str(row.user_id),
                "legal_entity_id": str(row.legal_entity_id or ""),
                "apm_branch": row.apm_branch,
                "standin_user_id": str(row.standin_user_id or ""),
            },
        )

    # --- who acts --------------------------------------------------------------------------

    async def _chain(self, requester: uuid.UUID) -> dict[StepLevel, list[uuid.UUID]]:
        if requester not in self._chains:
            self._chains[requester] = await HrAbsenceService(
                self._session, self._settings, self._audit
            ).chain_for(requester)
        return self._chains[requester]

    async def board(self, requester: uuid.UUID) -> list[uuid.UUID]:
        """The Ban giám đốc's heads but the requester (F6's last resort)."""
        return [
            user for user in (await self._chain(requester))[StepLevel.DIRECTOR] if user != requester
        ]

    async def holder(
        self,
        role: FinanceRole,
        *,
        requester: uuid.UUID,
        legal_entity_id: uuid.UUID | None,
        apm_branch: str | None,
    ) -> Holder:
        if role is FinanceRole.REQUESTER:
            return Holder(requester)
        if role in (FinanceRole.DEPT_HEAD, FinanceRole.BLOCK_DIRECTOR):
            level = StepLevel.MANAGER if role is FinanceRole.DEPT_HEAD else StepLevel.BLOCK
            people = (await self._chain(requester))[level]
            return Holder(people[0] if people else None, people[1] if len(people) > 1 else None)
        rows = [
            row
            for row in await self.assignments()
            if row.role == role.value
            and (row.legal_entity_id is None or row.legal_entity_id == legal_entity_id)
            and (row.apm_branch is None or row.apm_branch == apm_branch)
        ]
        if not rows:
            return Holder(None)
        best = max(
            rows,
            key=lambda row: (row.legal_entity_id is not None) * 2 + (row.apm_branch is not None),
        )
        return Holder(best.user_id, best.standin_user_id)


__all__ = ["FinanceRoleService", "Holder"]
