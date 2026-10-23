"""The task-code catalogue ("Danh mục điểm") of the ORD unit, and the check
every picked list goes through.

0058 made it the Design team's codes; since 0074 each production phase has
its own - Biên kịch N…, Design T…, Dựng D…, plus one "Việc khác" per phase -
each with the points one unit scores. Reading is any ORD member's; keeping it
is the OWNER's alone (the points are pay). A code is never deleted: an order
that carries it keeps its snapshot, and a retired code stops being offered.
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
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import OrderActorContext
from meobot.domain.orders.task_codes import (
    DEFAULT_FORM,
    TASK_PHASES,
    TaskFormSpec,
    form_specs,
    forms_for,
    is_single,
    phase_label,
)
from meobot.domain.units.models import UnitCode, UnitMemberRole, UnitMembership, UnitSettings

#: The Design code every birthday order carries, quantity 1 (2026-10-23).
BIRTHDAY_CODE = "T1"

#: The most points one "Việc khác" line may carry - only a guard against a
#: number nobody meant (no business limit yet).
MAX_FREE_POINTS = 1_000_000


def manages_task_codes(membership: UnitMembership, phase: OrderNodeType) -> bool:
    """Who keeps "Danh mục điểm": the OWNER alone (2026-10-23) - the points
    are pay, so no Trưởng phòng (nor an ADMIN) edits them. ``phase`` is kept
    so the rule can widen per step again without touching the callers."""
    del phase
    return membership.is_owner


def managed_phases(membership: UnitMembership) -> tuple[OrderNodeType, ...]:
    """The phases whose codes this person keeps."""
    if not membership.has(UnitCode.ADS) and not membership.sees_all:
        return ()
    return tuple(phase for phase in TASK_PHASES if manages_task_codes(membership, phase))


def manages_design_codes(membership: UnitMembership) -> bool:
    """The Design Leader, the OWNER, an ADMIN - for the Design team's own
    screens (the library, editing a birthday order), no longer the catalogue."""
    if membership.sees_all:
        return True
    entry = membership.entry(UnitCode.ADS)
    return entry is not None and entry.role is UnitMemberRole.THIET_KE and entry.is_lead


def in_design_team(membership: UnitMembership) -> bool:
    entry = membership.entry(UnitCode.ADS)
    return entry is not None and entry.role is UnitMemberRole.THIET_KE


def has_birthday_orders(membership: UnitMembership, settings: UnitSettings) -> bool:
    """Who the birthday-order board is for (0062): whoever holds the order
    flow's ``BIRTHDAY`` permission - granted per department and position like
    every other one (Thiết Kế by default), the OWNER and an ADMIN - and whoever
    sees every order (Ban Giám đốc)."""
    if not membership.has(UnitCode.ADS):
        return False
    may = OrderActorContext.from_membership(membership, settings).permissions
    return may.holds(AdsPermission.BIRTHDAY) or may.allows(AdsPermission.VIEW)


class DesignCodeService:
    def __init__(
        self, session: AsyncSession, *, directory: UnitDirectoryService, audit: AuditService
    ) -> None:
        self._session = session
        self._directory = directory
        self._audit = audit

    # --- the catalogue ---------------------------------------------------------

    async def catalogue(
        self,
        *,
        actor: Actor,
        include_inactive: bool = False,
        phase: OrderNodeType | None = None,
    ) -> list[UnitDesignCode]:
        membership = await self._directory.require(actor, UnitCode.ADS)
        if include_inactive:
            allowed = (
                manages_task_codes(membership, phase)
                if phase is not None
                else bool(managed_phases(membership))
            )
            if not allowed:
                raise _forbidden()
        unit = await self._directory.unit(UnitCode.ADS)
        statement = select(UnitDesignCode).where(UnitDesignCode.unit_id == unit.id)
        if phase is not None:
            statement = statement.where(UnitDesignCode.phase == phase.value)
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
        name: str | None = None,
        phase: OrderNodeType = OrderNodeType.THIET_KE,
        points: int = 0,
        unit_label: str | None = None,
        examples: str | None = None,
        note: str | None = None,
        free_points: bool = False,
        active: bool = True,
        sort_order: int | None = None,
    ) -> UnitDesignCode:
        unit_id = await self._require_manager(actor, phase)
        cleaned_code = _code(code)
        cleaned_name = _name(name)
        if cleaned_name is None and not _text(examples):
            raise OrderValidationError(
                "Cần nhập tên hoặc ví dụ áp dụng.",
                details={"reason": "design_code_name_missing", "field": "name"},
            )
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
            phase=phase.value,
            code=cleaned_code,
            name=cleaned_name,
            points=_points(points),
            unit_label=_short(unit_label, 40),
            examples=_text(examples),
            note=_text(note),
            free_points=free_points,
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
        points: int | None = None,
        unit_label: str | None = None,
        examples: str | None = None,
        note: str | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UnitDesignCode:
        """Change a code. A text field sent as ``""`` is cleared; ``None``
        leaves it as it is."""
        membership = await self._directory.require(actor, UnitCode.ADS)
        unit = await self._directory.unit(UnitCode.ADS)
        row = await self._session.get(UnitDesignCode, code_id)
        if row is None or row.unit_id != unit.id:
            raise OrderNotFoundError(
                "Không tìm thấy mã task.", details={"reason": "design_code_not_found"}
            )
        if not manages_task_codes(membership, _phase_of(row)):
            raise _forbidden()
        before = _snapshot(row)
        if code is not None:
            cleaned = _code(code)
            await self._check_code(unit.id, cleaned, exclude=row.id)
            row.code = cleaned
        if name is not None:
            row.name = _name(name)
        if examples is not None:
            row.examples = _text(examples)
        if row.name is None and not row.examples:
            raise OrderValidationError(
                "Cần nhập tên hoặc ví dụ áp dụng.",
                details={"reason": "design_code_name_missing", "field": "name"},
            )
        if points is not None:
            row.points = _points(points)
        if unit_label is not None:
            row.unit_label = _short(unit_label, 40)
        if note is not None:
            row.note = _text(note)
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
        phase: OrderNodeType = OrderNodeType.THIET_KE,
        forms: Sequence[TaskFormSpec] | None = None,
    ) -> tuple[DesignItem, ...]:
        """The picked list as snapshots, with the points they score.

        Every code must be an active catalogue row of ``phase`` - or one the
        order already carries (``keep``): a code retired after it was picked
        may stay on the order, with its old snapshot. Its form must be one the
        phase offers. A "Việc khác" line needs what was done and its points.
        Biên kịch and Dựng take exactly one code, quantity 1."""
        if len(picks) > MAX_ITEMS:
            raise OrderValidationError(
                f"Mỗi order tối đa {MAX_ITEMS} mã task.",
                details={"reason": "design_code_invalid", "field": "design_codes"},
            )
        if is_single(phase) and len(picks) > 1:
            raise OrderValidationError(
                f"{phase_label(phase)} chỉ chọn một mã task.",
                details={"reason": "task_code_single", "field": "task_codes"},
            )
        offered = {spec.key: spec for spec in forms_for(forms or form_specs(()), phase)}
        kept = {item.code_id: item for item in keep}
        wanted = list(dict.fromkeys(pick.code_id for pick in picks))
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
            row = rows.get(pick.code_id)
            old = kept.get(pick.code_id)
            if row is None or _phase_of(row) is not phase or not (row.active or old is not None):
                raise OrderValidationError(
                    "Mã task không hợp lệ hoặc đã ngừng dùng.",
                    details={"reason": "design_code_invalid", "field": "design_codes"},
                )
            legacy = old is not None and not row.active
            if row.free_points:
                items.append(self._free_item(row, pick))
                continue
            quantity = pick.quantity
            if is_single(phase):
                if quantity != 1:
                    raise OrderValidationError(
                        f"{phase_label(phase)} chỉ chọn một mã, số lượng 1.",
                        details={"reason": "task_code_single", "field": "task_codes"},
                    )
            elif not 1 <= quantity <= MAX_QUANTITY:
                raise OrderValidationError(
                    f"Số lượng mỗi mã phải từ 1 đến {MAX_QUANTITY}.",
                    details={"reason": "design_quantity_invalid", "field": "design_codes"},
                )
            form_key = pick.form or DEFAULT_FORM
            form = offered.get(form_key)
            if form is None and legacy and old is not None and old.form == form_key:
                form = TaskFormSpec(old.form, old.form_label, old.form_pct, "", (phase,))
            if form is None:
                raise OrderValidationError(
                    f"Hình thức này không áp dụng cho {phase_label(phase)}.",
                    details={"reason": "task_form_invalid", "field": "task_codes"},
                )
            snapshot = old if legacy and old is not None else None
            items.append(
                DesignItem(
                    code_id=row.id,
                    code=snapshot.code if snapshot else row.code,
                    name=snapshot.name if snapshot else (row.name or ""),
                    quantity=quantity,
                    form=form.key,
                    form_label=form.label,
                    form_pct=form.pct,
                    unit_points=snapshot.unit_points if snapshot else row.points,
                    note=_text(pick.note),
                    examples=snapshot.examples if snapshot else row.examples,
                    unit_label=snapshot.unit_label if snapshot else row.unit_label,
                )
            )
        seen = [item.key for item in items if not item.free]
        if len(set(seen)) != len(seen):
            raise OrderValidationError(
                "Mỗi mã task chỉ chọn một lần cho mỗi hình thức; tăng số lượng nếu cần nhiều hơn.",
                details={"reason": "design_code_invalid", "field": "design_codes"},
            )
        return tuple(items)

    async def birthday_items(self, unit_id: uuid.UUID) -> tuple[DesignItem, ...]:
        """What a birthday order carries (2026-10-23): Design's ``T1`` x1 -
        the card is a "mẫu đơn" - so the designer is scored for it. Nothing
        when the catalogue has no live T1."""
        row = await self._session.scalar(
            select(UnitDesignCode).where(
                UnitDesignCode.unit_id == unit_id,
                UnitDesignCode.phase == OrderNodeType.THIET_KE.value,
                func.lower(UnitDesignCode.code) == BIRTHDAY_CODE.lower(),
                UnitDesignCode.active.is_(True),
                UnitDesignCode.free_points.is_(False),
            )
        )
        if row is None:
            return ()
        return (
            DesignItem(
                code_id=row.id,
                code=row.code,
                name=row.name or "",
                quantity=1,
                unit_points=row.points,
                examples=row.examples,
                unit_label=row.unit_label,
            ),
        )

    @staticmethod
    def _free_item(row: UnitDesignCode, pick: DesignPick) -> DesignItem:
        """A "Việc khác" line: what was done and the points typed in."""
        note = _text(pick.note)
        if note is None or pick.points is None or not 0 < pick.points <= MAX_FREE_POINTS:
            raise OrderValidationError(
                "Việc khác cần ghi rõ làm gì và số điểm (lớn hơn 0).",
                details={"reason": "task_other_invalid", "field": "task_codes"},
            )
        return DesignItem(
            code_id=row.id,
            code=row.code,
            name=row.name or "Việc khác",
            quantity=1,
            unit_points=int(pick.points),
            note=note,
            free=True,
        )

    # --- helpers -----------------------------------------------------------------

    async def _require_manager(self, actor: Actor, phase: OrderNodeType) -> uuid.UUID:
        membership = await self._directory.require(actor, UnitCode.ADS)
        if not manages_task_codes(membership, phase):
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
                "Mã này đã có trong danh mục.",
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


def _phase_of(row: UnitDesignCode) -> OrderNodeType:
    try:
        return OrderNodeType(row.phase)
    except ValueError:
        return OrderNodeType.THIET_KE


def _forbidden() -> OrderActionNotAllowedError:
    return OrderActionNotAllowedError(
        "Chỉ chủ sở hữu (OWNER) mới sửa được danh mục điểm.",
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


def _name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > 200:
        raise OrderValidationError(
            "Tên task tối đa 200 ký tự.",
            details={"reason": "design_code_name_missing", "field": "name"},
        )
    return cleaned


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _short(value: str | None, limit: int) -> str | None:
    cleaned = _text(value)
    return None if cleaned is None else cleaned[:limit]


def _points(value: int) -> int:
    if not 0 <= value <= MAX_FREE_POINTS:
        raise OrderValidationError(
            "Điểm phải là số không âm.",
            details={"reason": "task_code_points_invalid", "field": "points"},
        )
    return int(value)


def _snapshot(row: UnitDesignCode) -> dict[str, Any]:
    return {
        "unit_id": str(row.unit_id),
        "phase": row.phase,
        "code": row.code,
        "name": row.name,
        "points": row.points,
        "unit_label": row.unit_label,
        "examples": row.examples,
        "note": row.note,
        "free_points": row.free_points,
        "active": row.active,
        "sort_order": row.sort_order,
    }


__all__ = [
    "DesignCodeService",
    "has_birthday_orders",
    "in_design_team",
    "managed_phases",
    "manages_design_codes",
    "manages_task_codes",
]
