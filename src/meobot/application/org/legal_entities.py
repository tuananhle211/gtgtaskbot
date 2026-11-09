"""Legal entities ("Pháp nhân", 0095): the list, and who may change it.

Anybody signed in reads the list (the account screens name each person's
company); the OWNER and an ADMIN add, rename or switch an entry off. An entry
is never deleted: people who name it keep it.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role


class LegalEntityForbiddenError(AuthorizationError):
    code = "legal_entity_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ quản trị viên mới sửa được danh sách pháp nhân.", details={"reason": self.code}
        )


class LegalEntityNotFoundError(NotFoundError):
    code = "legal_entity_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy pháp nhân.", details={"reason": self.code})


class LegalEntityValidationError(ValidationError):
    code = "legal_entity_invalid"

    def __init__(self, reason: str, message: str, *, field: str) -> None:
        super().__init__(message, details={"reason": reason, "field": field})
        self.code = reason


def normalise_entity_code(value: str | None) -> str:
    """``APEXMED``, ``HKD-AP``: upper case letters, digits, inner hyphens, ≤20."""
    code = re.sub(r"[^A-Z0-9]+", "-", (value or "").upper()).strip("-")
    return code[:20].rstrip("-")


def may_edit_entities(actor: Actor) -> bool:
    return actor.role in (Role.OWNER, Role.ADMIN)


class LegalEntityService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def entities(self, *, include_inactive: bool = False) -> list[tuple[LegalEntity, int]]:
        """Each entity with how many living accounts name it."""
        people = (
            select(User.legal_entity_id, func.count(User.id).label("people"))
            .where(User.deleted_at.is_(None), User.legal_entity_id.is_not(None))
            .group_by(User.legal_entity_id)
            .subquery()
        )
        statement = (
            select(LegalEntity, func.coalesce(people.c.people, 0))
            .outerjoin(people, people.c.legal_entity_id == LegalEntity.id)
            .order_by(LegalEntity.sort_order, LegalEntity.code)
        )
        if not include_inactive:
            statement = statement.where(LegalEntity.active.is_(True))
        return [(row, int(count)) for row, count in (await self._session.execute(statement)).all()]

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: str,
        name: str,
        tax_code: str | None = None,
    ) -> LegalEntity:
        if not may_edit_entities(actor):
            raise LegalEntityForbiddenError()
        orders = (await self._session.scalars(select(LegalEntity.sort_order))).all()
        row = LegalEntity(
            code=await self._code(code, exclude=None),
            name=_name(name),
            tax_code=_tax_code(tax_code),
            sort_order=max(orders, default=-1) + 1,
        )
        self._session.add(row)
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.LEGAL_ENTITY_CREATED, row, None)
        return row

    async def update(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        entity_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> LegalEntity:
        """``fields`` = only the keys the caller sent (code, name, tax_code, active)."""
        if not may_edit_entities(actor):
            raise LegalEntityForbiddenError()
        row = await self._session.get(LegalEntity, entity_id)
        if row is None:
            raise LegalEntityNotFoundError()
        before = _snap(row)
        if "code" in fields:
            row.code = await self._code(fields["code"], exclude=row.id)
        if "name" in fields:
            row.name = _name(fields["name"])
        if "tax_code" in fields:
            row.tax_code = _tax_code(fields["tax_code"])
        if "active" in fields and fields["active"] is not None:
            row.active = bool(fields["active"])
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.LEGAL_ENTITY_UPDATED, row, before)
        return row

    async def _code(self, value: str, *, exclude: uuid.UUID | None) -> str:
        code = normalise_entity_code(value)
        if not code:
            raise LegalEntityValidationError(
                "legal_entity_code_missing", "Cần nhập mã pháp nhân.", field="code"
            )
        statement = select(LegalEntity.id).where(LegalEntity.code == code)
        if exclude is not None:
            statement = statement.where(LegalEntity.id != exclude)
        if await self._session.scalar(statement) is not None:
            raise LegalEntityValidationError(
                "legal_entity_code_taken", "Mã pháp nhân này đã có.", field="code"
            )
        return code

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        row: LegalEntity,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="legal_entity",
            entity_id=str(row.id),
            before_data=before,
            after_data=_snap(row),
        )


def _name(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned or len(cleaned) > 200:
        raise LegalEntityValidationError(
            "legal_entity_name_invalid", "Tên pháp nhân từ 1 đến 200 ký tự.", field="name"
        )
    return cleaned


def _tax_code(value: str | None) -> str | None:
    cleaned = "".join((value or "").split())
    if not cleaned:
        return None
    if len(cleaned) > 20:
        raise LegalEntityValidationError(
            "legal_entity_tax_code_invalid", "Mã số thuế tối đa 20 ký tự.", field="tax_code"
        )
    return cleaned


def _snap(row: LegalEntity) -> dict[str, Any]:
    return {"code": row.code, "name": row.name, "tax_code": row.tax_code, "active": row.active}


__all__ = [
    "LegalEntityForbiddenError",
    "LegalEntityNotFoundError",
    "LegalEntityService",
    "LegalEntityValidationError",
    "may_edit_entities",
    "normalise_entity_code",
]
