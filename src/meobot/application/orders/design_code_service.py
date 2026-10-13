"""The Design team's catalogue of design codes ("mã video"), and the check
every picked list goes through.

Reading is any ORD member's; keeping the catalogue is the Design Leader's
(Trưởng phòng Design) - and the OWNER's and an ADMIN's, as everywhere. A code
is never deleted: an order that carries it keeps its snapshot, and a retired
code simply stops being offered.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.units.directory import UnitDirectoryService
from meobot.db.models.org_unit import UnitDesignCode
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.orders.design_codes import (
    MAX_ITEMS,
    MAX_QUANTITY,
    DesignItem,
    DesignPick,
)
from meobot.domain.orders.errors import (
    OrderActionNotAllowedError,
    OrderNotFoundError,
    OrderValidationError,
)
from meobot.domain.units.models import UnitCode, UnitMemberRole, UnitMembership


def manages_design_codes(membership: UnitMembership) -> bool:
    """The Design Leader, the OWNER, an ADMIN."""
    if membership.sees_all:
        return True
    entry = membership.entry(UnitCode.ADS)
    return entry is not None and entry.role is UnitMemberRole.THIET_KE and entry.is_lead


def in_design_team(membership: UnitMembership) -> bool:
    entry = membership.entry(UnitCode.ADS)
    return entry is not None and entry.role is UnitMemberRole.THIET_KE


class DesignCodeService:
    def __init__(
        self, session: AsyncSession, *, directory: UnitDirectoryService, audit: AuditService
    ) -> None:
        self._session = session
        self._directory = directory
        self._audit = audit

    # --- the catalogue ---------------------------------------------------------

    async def catalogue(
        self, *, actor: Actor, include_inactive: bool = False
    ) -> list[UnitDesignCode]:
        membership = await self._directory.require(actor, UnitCode.ADS)
        if include_inactive and not manages_design_codes(membership):
            raise _forbidden()
        unit = await self._directory.unit(UnitCode.ADS)
        statement = select(UnitDesignCode).where(UnitDesignCode.unit_id == unit.id)
        if not include_inactive:
            statement = statement.where(UnitDesignCode.active.is_(True))
        statement = statement.order_by(
            UnitDesignCode.sort_order, func.lower(UnitDesignCode.code), UnitDesignCode.id
        )
        return list((await self._session.scalars(statement)).all())

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: str,
        name: str,
        active: bool = True,
        sort_order: int | None = None,
    ) -> UnitDesignCode:
        unit_id = await self._require_manager(actor)
        cleaned_code, cleaned_name = _code(code), _name(name)
        await self._check_code(unit_id, cleaned_code, exclude=None)
        if sort_order is None:
            # Display order only, not an identifier: the next free place.
            places = (
                await self._session.scalars(
                    select(UnitDesignCode.sort_order).where(UnitDesignCode.unit_id == unit_id)
                )
            ).all()
            sort_order = max((int(place) for place in places), default=-1) + 1
        row = UnitDesignCode(
            unit_id=unit_id,
            code=cleaned_code,
            name=cleaned_name,
            active=active,
            sort_order=sort_order,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit_row(
            actor, request_id, AuditAction.UNIT_DESIGN_CODE_CREATED, row, before=None
        )
        return row

    async def update(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code_id: uuid.UUID,
        code: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UnitDesignCode:
        unit_id = await self._require_manager(actor)
        row = await self._session.get(UnitDesignCode, code_id)
        if row is None or row.unit_id != unit_id:
            raise OrderNotFoundError(
                "Không tìm thấy mã video.", details={"reason": "design_code_not_found"}
            )
        before = _snapshot(row)
        if code is not None:
            cleaned = _code(code)
            await self._check_code(unit_id, cleaned, exclude=row.id)
            row.code = cleaned
        if name is not None:
            row.name = _name(name)
        if active is not None:
            row.active = active
        if sort_order is not None:
            row.sort_order = sort_order
        await self._session.flush()
        await self._audit_row(
            actor, request_id, AuditAction.UNIT_DESIGN_CODE_UPDATED, row, before=before
        )
        return row

    # --- a picked list -----------------------------------------------------------

    async def resolve(
        self,
        unit_id: uuid.UUID,
        picks: Sequence[DesignPick],
        *,
        keep: Sequence[DesignItem] = (),
    ) -> tuple[DesignItem, ...]:
        """The picked list as snapshots. Every code must be an active catalogue
        row - or one the order already carries (``keep``): a code retired after
        it was picked may stay on the order, with its old snapshot."""
        if len(picks) > MAX_ITEMS:
            raise OrderValidationError(
                f"Mỗi order tối đa {MAX_ITEMS} mã video.",
                details={"reason": "design_code_invalid", "field": "design_codes"},
            )
        kept = {item.code_id: item for item in keep}
        wanted = [pick.code_id for pick in picks]
        if len(set(wanted)) != len(wanted):
            raise OrderValidationError(
                "Mỗi mã video chỉ chọn một lần; tăng số lượng nếu cần nhiều hơn.",
                details={"reason": "design_code_invalid", "field": "design_codes"},
            )
        rows = (
            {
                row.id: row
                for row in (
                    await self._session.scalars(
                        select(UnitDesignCode).where(
                            UnitDesignCode.unit_id == unit_id, UnitDesignCode.id.in_(wanted)
                        )
                    )
                ).all()
            }
            if wanted
            else {}
        )
        items: list[DesignItem] = []
        for pick in picks:
            if not 1 <= pick.quantity <= MAX_QUANTITY:
                raise OrderValidationError(
                    f"Số lượng mỗi mã phải từ 1 đến {MAX_QUANTITY}.",
                    details={"reason": "design_quantity_invalid", "field": "design_codes"},
                )
            row = rows.get(pick.code_id)
            old = kept.get(pick.code_id)
            if row is not None and (row.active or old is not None):
                items.append(
                    DesignItem(
                        code_id=row.id,
                        code=old.code if old is not None and not row.active else row.code,
                        name=old.name if old is not None and not row.active else row.name,
                        quantity=pick.quantity,
                    )
                )
            else:
                raise OrderValidationError(
                    "Mã video không hợp lệ hoặc đã ngừng dùng.",
                    details={"reason": "design_code_invalid", "field": "design_codes"},
                )
        return tuple(items)

    # --- helpers -----------------------------------------------------------------

    async def _require_manager(self, actor: Actor) -> uuid.UUID:
        membership = await self._directory.require(actor, UnitCode.ADS)
        if not manages_design_codes(membership):
            raise _forbidden()
        return (await self._directory.unit(UnitCode.ADS)).id

    async def _check_code(
        self, unit_id: uuid.UUID, code: str, *, exclude: uuid.UUID | None
    ) -> None:
        statement = select(UnitDesignCode.id).where(
            UnitDesignCode.unit_id == unit_id, func.lower(UnitDesignCode.code) == code.lower()
        )
        if exclude is not None:
            statement = statement.where(UnitDesignCode.id != exclude)
        if await self._session.scalar(statement.limit(1)) is not None:
            raise OrderValidationError(
                "Mã này đã có trong bảng mã video.",
                details={"reason": "design_code_taken", "field": "code"},
            )

    async def _audit_row(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        row: UnitDesignCode,
        *,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="unit_design_code",
            entity_id=str(row.id),
            before_data=before,
            after_data=_snapshot(row),
        )


def _forbidden() -> OrderActionNotAllowedError:
    return OrderActionNotAllowedError(
        "Chỉ Trưởng phòng Design mới quản lý bảng mã video.",
        details={"reason": "design_code_forbidden"},
    )


def _code(value: str) -> str:
    cleaned = "".join(value.split()).upper()
    if not cleaned:
        raise OrderValidationError(
            "Cần nhập mã.", details={"reason": "design_code_missing", "field": "code"}
        )
    if len(cleaned) > 30:
        raise OrderValidationError(
            "Mã tối đa 30 ký tự.", details={"reason": "design_code_missing", "field": "code"}
        )
    return cleaned


def _name(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise OrderValidationError(
            "Cần nhập tên task.", details={"reason": "design_code_name_missing", "field": "name"}
        )
    if len(cleaned) > 200:
        raise OrderValidationError(
            "Tên task tối đa 200 ký tự.",
            details={"reason": "design_code_name_missing", "field": "name"},
        )
    return cleaned


def _snapshot(row: UnitDesignCode) -> dict[str, Any]:
    return {
        "unit_id": str(row.unit_id),
        "code": row.code,
        "name": row.name,
        "active": row.active,
        "sort_order": row.sort_order,
    }


__all__ = ["DesignCodeService", "in_design_team", "manages_design_codes"]
