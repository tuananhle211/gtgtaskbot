"""Writes on units: tagging people, their roles, the unit's settings.

Who may administer a unit
-------------------------

The OWNER, any unit. An ADMIN, only a unit they are tagged into - the ADMIN
role widens what somebody may do *inside* a unit, never which units they can
see. Everyone else is refused with a 403 that names the unit, because they
are inside it and already know it exists.

What the writes guarantee
-------------------------

* A tag row is never deleted. Leaving sets ``left_at``; re-tagging the same
  person reopens the row with the new role, so the history of who could see
  what stays readable.
* The PR unit only ever holds ``MEMBER`` rows; the Ads roles only ever go on
  the Ads unit. A role on the wrong unit is a validation error, not a quiet
  no-op.
* A member code is upper-case, short and unique while the tag is open; the
  partial unique index is the final word and its refusal is translated.
* Untagging somebody who still owns an open Ads order, or holds an active
  node on one, is refused: the order would lose the only person who can act
  on it. Finish or reassign first.

* A video kind is never deleted: it is deactivated. Its name is unique in the
  unit whatever the case, checked before the write so the refusal is a
  sentence, with the unique index as the final word.

Every write is audited with before/after data.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.units.directory import ROLE_FOR_NODE, UnitDirectoryService, UnitMemberRow
from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember, UnitVideoKind
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.labels import node_type_label
from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, TERMINAL_STAGES, OrderNodeType
from meobot.domain.orders.permissions import AdsPermissionMatrixError, normalise_matrix
from meobot.domain.units.errors import (
    UnitAccessDeniedError,
    UnitNotFoundError,
    UnitValidationError,
)
from meobot.domain.units.labels import LEAD_ROLE_LABELS
from meobot.domain.units.models import (
    FUNCTION_ROLES,
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitSettings,
)

MEMBER_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,12}$")

#: Roles a unit of each kind may hold.
ROLES_BY_UNIT: dict[UnitCode, frozenset[UnitMemberRole]] = {
    UnitCode.PR: frozenset({UnitMemberRole.MEMBER}),
    UnitCode.ADS: frozenset({UnitMemberRole.ORDERER, UnitMemberRole.HEAD, *FUNCTION_ROLES}),
}


class UnitAdminService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit
        self._directory = UnitDirectoryService(session)

    # --- authority ----------------------------------------------------------

    @staticmethod
    def administers(membership: UnitMembership, actor: Actor, code: UnitCode) -> bool:
        """Whether ``actor`` may administer ``code``. OWNER: any; ADMIN: tagged ones."""
        if actor.role is Role.OWNER:
            return True
        return actor.role is Role.ADMIN and membership.entry(code) is not None

    async def admin_units(self, actor: Actor) -> list[UnitCode]:
        membership = await self._directory.membership_for(actor)
        return [code for code in UnitCode if self.administers(membership, actor, code)]

    async def _require_admin(self, actor: Actor, code: UnitCode) -> OrgUnit:
        membership = await self._directory.membership_for(actor)
        if not membership.has(code):
            # An outsider learns nothing, the same as on every other route.
            raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})
        if not self.administers(membership, actor, code):
            raise UnitAccessDeniedError(
                "Bạn không có quyền quản trị ban này.",
                details={"reason": "unit_admin_forbidden", "unit": code.value},
            )
        return await self._directory.unit(code)

    # --- members ------------------------------------------------------------

    async def tag_member(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        user_id: uuid.UUID,
        role: UnitMemberRole,
        is_lead: bool = False,
        member_code: str | None = None,
        personal_nas_url: str | None = None,
    ) -> UnitMemberRow:
        unit = await self._require_admin(actor, code)
        user = await self._session.get(User, user_id)
        if user is None:
            raise UnitValidationError(
                "Không tìm thấy thành viên này.",
                details={"reason": "user_not_found", "field": "user_id"},
            )
        self._check_role(code, role)
        normalised_code = self._normalise_member_code(member_code)

        row = await self._directory.member(unit.id, user_id)
        before: dict[str, Any] | None = None
        if row is None:
            row = OrgUnitMember(
                unit_id=unit.id,
                user_id=user_id,
                role=role,
                is_lead=is_lead and role in FUNCTION_ROLES,
                member_code=normalised_code,
                personal_nas_url=personal_nas_url,
                joined_at=utcnow(),
            )
            self._session.add(row)
        else:
            if row.left_at is None:
                raise UnitValidationError(
                    "Thành viên này đã thuộc ban.",
                    details={"reason": "already_tagged", "unit": code.value},
                )
            before = self._snapshot(row)
            row.role = role
            row.is_lead = is_lead and role in FUNCTION_ROLES
            row.member_code = normalised_code
            row.personal_nas_url = personal_nas_url
            row.joined_at = utcnow()
            row.left_at = None
        await self._flush_translating(code, row)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.UNIT_MEMBER_TAGGED.value,
            result=AuditResult.SUCCESS,
            entity_type="org_unit_member",
            entity_id=str(row.id),
            before_data=before,
            after_data=self._snapshot(row),
        )
        return UnitMemberRow(membership=row, user=user)

    async def update_member(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        user_id: uuid.UUID,
        role: UnitMemberRole | None = None,
        is_lead: bool | None = None,
        member_code: str | None = None,
        clear_member_code: bool = False,
        personal_nas_url: str | None = None,
        clear_personal_nas_url: bool = False,
    ) -> UnitMemberRow:
        unit = await self._require_admin(actor, code)
        row = await self._directory.member(unit.id, user_id)
        if row is None or row.left_at is not None:
            raise UnitNotFoundError(
                "Thành viên không thuộc ban này.", details={"reason": "member_not_tagged"}
            )
        before = self._snapshot(row)
        if role is not None:
            self._check_role(code, role)
            row.role = role
        if is_lead is not None:
            row.is_lead = is_lead
        # Only a function (Biên kịch, Design, Dựng) has a head of its own.
        row.is_lead = row.is_lead and row.role in FUNCTION_ROLES
        if clear_member_code:
            row.member_code = None
        elif member_code is not None:
            row.member_code = self._normalise_member_code(member_code)
        if clear_personal_nas_url:
            row.personal_nas_url = None
        elif personal_nas_url is not None:
            row.personal_nas_url = personal_nas_url
        await self._flush_translating(code, row)
        user = await self._session.get_one(User, user_id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.UNIT_MEMBER_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="org_unit_member",
            entity_id=str(row.id),
            before_data=before,
            after_data=self._snapshot(row),
        )
        return UnitMemberRow(membership=row, user=user)

    async def untag_member(
        self, *, actor: Actor, request_id: uuid.UUID, code: UnitCode, user_id: uuid.UUID
    ) -> UnitMemberRow:
        unit = await self._require_admin(actor, code)
        row = await self._directory.member(unit.id, user_id)
        if row is None or row.left_at is not None:
            raise UnitNotFoundError(
                "Thành viên không thuộc ban này.", details={"reason": "member_not_tagged"}
            )
        if actor.user_id == user_id:
            raise UnitValidationError(
                "Bạn không thể tự gỡ mình khỏi ban.", details={"reason": "self_untag"}
            )
        if await self._holds_open_work(unit.id, user_id):
            raise UnitValidationError(
                "Thành viên này còn đơn hoặc công đoạn đang mở. Bàn giao xong rồi gỡ.",
                details={"reason": "member_has_open_work", "unit": code.value},
            )
        before = self._snapshot(row)
        row.left_at = utcnow()
        await self._session.flush()
        user = await self._session.get_one(User, user_id)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.UNIT_MEMBER_UNTAGGED.value,
            result=AuditResult.SUCCESS,
            entity_type="org_unit_member",
            entity_id=str(row.id),
            before_data=before,
            after_data=self._snapshot(row),
        )
        return UnitMemberRow(membership=row, user=user)

    async def directory(self, *, actor: Actor) -> list[tuple[User, list[UnitCode]]]:
        """Every account with its open tags, for the add-member picker.

        Only for somebody who administers at least one unit.
        """
        if not await self.admin_units(actor):
            raise UnitAccessDeniedError(
                "Bạn không có quyền quản trị ban nào.", details={"reason": "unit_admin_forbidden"}
            )
        users = (await self._session.scalars(select(User).order_by(User.full_name, User.id))).all()
        tags = (
            await self._session.execute(
                select(OrgUnitMember.user_id, OrgUnit.code)
                .join(OrgUnit, OrgUnit.id == OrgUnitMember.unit_id)
                .where(OrgUnitMember.left_at.is_(None))
            )
        ).all()
        by_user: dict[uuid.UUID, list[UnitCode]] = {}
        for user_id, unit_code in tags:
            by_user.setdefault(user_id, []).append(UnitCode(unit_code))
        return [(user, sorted(by_user.get(user.id, []))) for user in users]

    # --- video kinds -----------------------------------------------------------

    async def video_kinds(
        self, *, actor: Actor, code: UnitCode, include_inactive: bool = False
    ) -> list[UnitVideoKind]:
        """The unit's catalogue, in display order. Any member reads the active
        kinds; the retired ones are for the unit's administrators."""
        if include_inactive:
            unit = await self._require_admin(actor, code)
        else:
            await self._directory.require(actor, code)
            unit = await self._directory.unit(code)
        statement = select(UnitVideoKind).where(UnitVideoKind.unit_id == unit.id)
        if not include_inactive:
            statement = statement.where(UnitVideoKind.active.is_(True))
        statement = statement.order_by(
            UnitVideoKind.sort_order, func.lower(UnitVideoKind.name), UnitVideoKind.id
        )
        return list((await self._session.scalars(statement)).all())

    async def create_video_kind(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        name: str,
        points: Decimal,
        active: bool = True,
        sort_order: int | None = None,
    ) -> UnitVideoKind:
        unit = await self._require_admin(actor, code)
        cleaned = self._video_kind_name(name)
        await self._check_video_kind_name(unit.id, cleaned, exclude=None)
        if sort_order is None:
            last = await self._session.scalar(
                select(func.max(UnitVideoKind.sort_order)).where(UnitVideoKind.unit_id == unit.id)
            )
            sort_order = 0 if last is None else int(last) + 1
        row = UnitVideoKind(
            unit_id=unit.id,
            name=cleaned,
            points=self._video_kind_points(points),
            active=active,
            sort_order=sort_order,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit_video_kind(
            actor, request_id, AuditAction.UNIT_VIDEO_KIND_CREATED, row, before=None
        )
        return row

    async def update_video_kind(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        kind_id: uuid.UUID,
        name: str | None = None,
        points: Decimal | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UnitVideoKind:
        unit = await self._require_admin(actor, code)
        row = await self._session.get(UnitVideoKind, kind_id)
        if row is None or row.unit_id != unit.id:
            raise UnitNotFoundError(
                "Không tìm thấy loại video.", details={"reason": "video_kind_not_found"}
            )
        before = self._video_kind_snapshot(row)
        if name is not None:
            cleaned = self._video_kind_name(name)
            await self._check_video_kind_name(unit.id, cleaned, exclude=row.id)
            row.name = cleaned
        if points is not None:
            row.points = self._video_kind_points(points)
        if active is not None:
            row.active = active
        if sort_order is not None:
            row.sort_order = sort_order
        await self._session.flush()
        await self._audit_video_kind(
            actor, request_id, AuditAction.UNIT_VIDEO_KIND_UPDATED, row, before=before
        )
        return row

    @staticmethod
    def _video_kind_name(value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise UnitValidationError(
                "Cần nhập tên loại video.",
                details={"reason": "video_kind_name_missing", "field": "name"},
            )
        if len(cleaned) > 120:
            raise UnitValidationError(
                "Tên loại video tối đa 120 ký tự.",
                details={"reason": "video_kind_name_too_long", "field": "name"},
            )
        return cleaned

    @staticmethod
    def _video_kind_points(value: Decimal) -> Decimal:
        points = Decimal(value)
        if not points.is_finite() or points < 0 or points >= Decimal("10000"):
            raise UnitValidationError(
                "Điểm phải từ 0 đến 9999.99.",
                details={"reason": "video_kind_points_invalid", "field": "points"},
            )
        return points.quantize(Decimal("0.01"))

    async def _check_video_kind_name(
        self, unit_id: uuid.UUID, name: str, *, exclude: uuid.UUID | None
    ) -> None:
        statement = select(UnitVideoKind.id).where(
            UnitVideoKind.unit_id == unit_id,
            func.lower(UnitVideoKind.name) == name.lower(),
        )
        if exclude is not None:
            statement = statement.where(UnitVideoKind.id != exclude)
        if await self._session.scalar(statement.limit(1)) is not None:
            raise UnitValidationError(
                "Tên loại video này đã có trong ban.",
                details={"reason": "video_kind_name_taken", "field": "name"},
            )

    async def _audit_video_kind(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        row: UnitVideoKind,
        *,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="unit_video_kind",
            entity_id=str(row.id),
            before_data=before,
            after_data=self._video_kind_snapshot(row),
        )

    @staticmethod
    def _video_kind_snapshot(row: UnitVideoKind) -> dict[str, Any]:
        return {
            "unit_id": str(row.unit_id),
            "name": row.name,
            "points": str(row.points),
            "active": row.active,
            "sort_order": row.sort_order,
        }

    # --- settings and health ------------------------------------------------

    async def update_settings(
        self, *, actor: Actor, request_id: uuid.UUID, code: UnitCode, patch: dict[str, Any]
    ) -> UnitSettings:
        unit = await self._require_admin(actor, code)
        current = UnitSettings.model_validate(unit.settings or {})
        try:
            updated = current.model_copy(update=patch)
            updated = UnitSettings.model_validate(updated.model_dump())
        except ValueError as error:
            raise UnitValidationError(
                "Thiết lập không hợp lệ.", details={"reason": "invalid_settings"}
            ) from error
        try:
            # Stored complete, so what the page shows is what the engine reads.
            updated = updated.model_copy(
                update={"permissions": normalise_matrix(updated.permissions)}
            )
        except AdsPermissionMatrixError as error:
            raise UnitValidationError(
                "Bảng quyền không hợp lệ.",
                details={"reason": "invalid_settings", "field": "permissions"},
            ) from error
        if updated.btd_link_attacher not in (UnitMemberRole.DUNG, UnitMemberRole.BIEN_TAP):
            raise UnitValidationError(
                "Người gắn link loại BTD phải là Dựng hoặc Biên tập.",
                details={"reason": "invalid_settings", "field": "btd_link_attacher"},
            )
        before = current.model_dump(mode="json")
        unit.settings = updated.model_dump(mode="json")
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.UNIT_SETTINGS_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="org_unit",
            entity_id=str(unit.id),
            before_data=before,
            after_data=unit.settings,
        )
        return updated

    async def health(self, *, actor: Actor, code: UnitCode) -> list[tuple[str, str]]:
        """What an administrator should fix before the unit can run."""
        membership = await self._directory.membership_for(actor)
        if not membership.has(code):
            raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})
        unit = await self._directory.unit(code)
        warnings: list[tuple[str, str]] = []
        if code is not UnitCode.ADS:
            return warnings
        if not await self._directory.heads(unit.id):
            warnings.append(("no_head", "Ban chưa có Trưởng phòng Ads: không ai duyệt được order."))
        for node_type in (OrderNodeType.BIEN_TAP, OrderNodeType.THIET_KE, OrderNodeType.DUNG):
            members = await self._directory.function_members(unit.id, node_type)
            if members and not any(row.membership.is_lead for row in members):
                warnings.append(
                    (
                        f"no_lead_{node_type.value.lower()}",
                        f"Ban {node_type_label(node_type)} có thành viên nhưng chưa có "
                        f"{LEAD_ROLE_LABELS[ROLE_FOR_NODE[node_type]]}: việc tới sẽ dồn về "
                        "Trưởng phòng Ads.",
                    )
                )
        orderers = await self._directory.members(unit.id, role=UnitMemberRole.ORDERER)
        missing = [row.user.full_name for row in orderers if not row.membership.member_code]
        if missing:
            warnings.append(
                (
                    "orderer_without_code",
                    "Marketing chưa có mã thành viên nên chưa tạo được order: "
                    + ", ".join(missing),
                )
            )
        return warnings

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _check_role(code: UnitCode, role: UnitMemberRole) -> None:
        if role not in ROLES_BY_UNIT[code]:
            raise UnitValidationError(
                f"Vai trò {role.value} không dùng cho ban {code.value}.",
                details={"reason": "role_not_for_unit", "field": "role", "unit": code.value},
            )

    @staticmethod
    def _normalise_member_code(value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not code:
            return None
        if not MEMBER_CODE_PATTERN.match(code):
            raise UnitValidationError(
                "Mã thành viên gồm từ 2 đến 12 chữ cái hoặc số, không dấu.",
                details={"reason": "invalid_member_code", "field": "member_code"},
            )
        return code

    async def _flush_translating(self, code: UnitCode, row: OrgUnitMember) -> None:
        """Refuse a taken member code with a sentence, then flush.

        The check is a read before the write, so the ordinary case never
        trips the partial unique index and the session stays usable after a
        refusal. The index remains the final word under a race; its
        ``IntegrityError`` then surfaces as the 409 every other race does.
        """
        if row.member_code is not None:
            taken = await self._session.scalar(
                select(OrgUnitMember.id).where(
                    OrgUnitMember.unit_id == row.unit_id,
                    OrgUnitMember.member_code == row.member_code,
                    OrgUnitMember.left_at.is_(None),
                    OrgUnitMember.user_id != row.user_id,
                )
            )
            if taken is not None:
                raise UnitValidationError(
                    "Mã thành viên này đã có người dùng trong ban.",
                    details={
                        "reason": "member_code_taken",
                        "field": "member_code",
                        "unit": code.value,
                    },
                )
        await self._session.flush()

    async def _holds_open_work(self, unit_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        open_order = Order.stage.notin_(list(TERMINAL_STAGES))
        owns = select(Order.id).where(
            Order.unit_id == unit_id, Order.owner_user_id == user_id, open_order
        )
        holds_node = (
            select(OrderNode.id)
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                Order.unit_id == unit_id,
                open_order,
                OrderNode.status.in_(list(ACTIVE_NODE_STATUSES)),
                or_(
                    OrderNode.assignee_user_id == user_id,
                    OrderNode.preassigned_user_id == user_id,
                ),
            )
        )
        found = await self._session.scalar(select(or_(exists(owns), exists(holds_node))))
        return bool(found)

    @staticmethod
    def _snapshot(row: OrgUnitMember) -> dict[str, Any]:
        return {
            "unit_id": str(row.unit_id),
            "user_id": str(row.user_id),
            "role": row.role.value,
            "is_lead": row.is_lead,
            "member_code": row.member_code,
            "personal_nas_url": row.personal_nas_url,
            "left_at": None if row.left_at is None else row.left_at.isoformat(),
        }


__all__ = ["MEMBER_CODE_PATTERN", "ROLES_BY_UNIT", "UnitAdminService"]
