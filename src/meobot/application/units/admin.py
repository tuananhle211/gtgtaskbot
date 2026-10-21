"""Writes on units: tagging people, their roles, the unit's settings.

Who may administer a unit
-------------------------

The OWNER and the ADMIN, any unit: settings, the permission matrix, video
kinds. Everyone else is refused with a 403 that names the unit when they are
inside it (an outsider reads the usual 404).

Who may tag
-----------

Tagging (add to a stream, change the role or lead flag there, remove from it)
is wider than administering:

* the OWNER and the ADMIN tag anyone into any stream;
* a ``TEAM_LEAD`` ("Trưởng nhóm") with an open tag in stream X tags people
  into X only - an untagged person or one from the other stream - and changes
  or closes tags in X. Never in the other stream, never an OWNER or ADMIN
  account, never themselves;
* everybody else: 403 ``unit_tag_forbidden``.

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

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.member_code import write_member_code
from meobot.application.audit_service import AuditService
from meobot.application.org import departments
from meobot.application.org.access import Access
from meobot.application.org.seats import load_seats
from meobot.application.units.directory import (
    ROLE_FOR_NODE,
    UnitDirectoryService,
    UnitMemberRow,
    manager_fits,
)
from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import (
    OrgUnit,
    OrgUnitMember,
    UnitDuration,
    UnitPlatform,
    UnitVideoKind,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.labels import node_type_label
from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, TERMINAL_STAGES, OrderNodeType
from meobot.domain.orders.permissions import AdsPermissionMatrixError, normalise_matrix
from meobot.domain.org.models import MODULE_PR, Position
from meobot.domain.pr.policy import PrCapability
from meobot.domain.units.errors import (
    UnitAccessDeniedError,
    UnitNotFoundError,
    UnitTagForbiddenError,
    UnitValidationError,
)
from meobot.domain.units.labels import LEAD_ROLE_LABELS
from meobot.domain.units.member_code import normalise_member_code
from meobot.domain.units.models import (
    FUNCTION_ROLES,
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitSettings,
)

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

    @property
    def session(self) -> AsyncSession:
        return self._session

    # --- authority ----------------------------------------------------------

    @staticmethod
    def administers(membership: UnitMembership, actor: Actor, code: UnitCode) -> bool:
        """Whether ``actor`` may administer ``code``: the OWNER and the ADMIN, any unit."""
        del membership, code  # every unit, for both
        return actor.role in (Role.OWNER, Role.ADMIN)

    async def admin_units(self, actor: Actor) -> list[UnitCode]:
        membership = await self._directory.membership_for(actor)
        return [code for code in UnitCode if self.administers(membership, actor, code)]

    @staticmethod
    def tags_in(membership: UnitMembership, actor: Actor, code: UnitCode) -> bool:
        """Whether ``actor`` may tag people into (and out of) ``code``."""
        if actor.role in (Role.OWNER, Role.ADMIN):
            return True
        if actor.role is Role.TEAM_LEAD and membership.entry(code) is not None:
            return True
        # 0067: whoever holds ``PR_MEMBERS_MANAGE`` (the PR head by default)
        # brings people into PR Thương Hiệu.
        access = membership.access
        return (
            code is UnitCode.PR
            and isinstance(access, Access)
            and access.allows(MODULE_PR, PrCapability.PR_MEMBERS_MANAGE.value)
        )

    @staticmethod
    def assigns(actor: Actor) -> bool:
        """Who places people in departments - and so in streams, roles and
        under a Leader: the OWNER alone (0061)."""
        return actor.role is Role.OWNER

    async def _require_placer(
        self, actor: Actor, code: UnitCode, role: UnitMemberRole | None
    ) -> None:
        """Who may put somebody into (or take them out of) a stream: the
        OWNER anywhere; 0067: a ``PR_MEMBERS_MANAGE`` holder (the PR head)
        into PR Thương Hiệu as a member, and out of it."""
        if self.assigns(actor):
            return
        membership = await self._directory.membership_for(actor)
        access = membership.access
        if (
            code is UnitCode.PR
            and role in (None, UnitMemberRole.MEMBER)
            and isinstance(access, Access)
            and access.allows(MODULE_PR, PrCapability.PR_MEMBERS_MANAGE.value)
        ):
            return
        self._require_assigner(actor, code)

    def _require_assigner(self, actor: Actor, code: UnitCode) -> None:
        if not self.assigns(actor):
            raise UnitTagForbiddenError(
                code, "Chỉ chủ sở hữu mới gắn người vào phòng ban, vai trò và người quản lý."
            )

    @classmethod
    def tag_units(cls, membership: UnitMembership, actor: Actor) -> list[UnitCode]:
        """The streams ``actor`` may tag in (``can_tag`` on ``/api/units/me``)."""
        return [code for code in UnitCode if cls.tags_in(membership, actor, code)]

    async def _require_tagger(self, actor: Actor, code: UnitCode, target_id: uuid.UUID) -> OrgUnit:
        """The unit, when ``actor`` may tag ``target_id`` in it.

        An outsider of the unit who tags nowhere reads the usual 404; anybody
        else who may not tag here (an employee of the unit, a team lead of the
        other stream, a team lead aiming at an OWNER/ADMIN account or at
        themselves) a 403 ``unit_tag_forbidden``.
        """
        membership = await self._directory.membership_for(actor)
        if not membership.has(code) and not self.tag_units(membership, actor):
            # Somebody who tags nowhere is told nothing about another stream;
            # a team lead of the other stream already knows it exists.
            raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})
        if not self.tags_in(membership, actor, code):
            raise UnitTagForbiddenError(code)
        if actor.role not in (Role.OWNER, Role.ADMIN):
            if actor.user_id is not None and target_id == actor.user_id:
                raise UnitTagForbiddenError(
                    code, "Bạn không thể tự gắn hoặc gỡ luồng của chính mình."
                )
            target = await self._session.get(User, target_id)
            if target is not None and target.role in (Role.OWNER, Role.ADMIN):
                raise UnitTagForbiddenError(
                    code, "Trưởng nhóm không gắn hoặc gỡ luồng cho Quản trị viên hay Chủ sở hữu."
                )
        return await self._directory.unit(code)

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
        unit = await self._require_tagger(actor, code, user_id)
        await self._require_placer(actor, code, role)
        user = await self._session.get(User, user_id)
        if user is None:
            raise UnitValidationError(
                "Không tìm thấy thành viên này.",
                details={"reason": "user_not_found", "field": "user_id"},
            )
        self._check_role(code, role)
        normalised_code = self._normalise_member_code(member_code)

        existing = await self._directory.member(unit.id, user_id)
        if existing is not None and existing.left_at is None:
            raise UnitValidationError(
                "Thành viên này đã thuộc ban.",
                details={"reason": "already_tagged", "unit": code.value},
            )
        before = None if existing is None else self._snapshot(existing)
        # A seat in the matching department; the tag follows from it (0061).
        await departments.place(
            self._session, code, user_id, role, is_lead=is_lead and role in FUNCTION_ROLES
        )
        row = await self._directory.member(unit.id, user_id)
        assert row is not None
        if normalised_code is not None:
            await write_member_code(self._session, user, normalised_code)
            await self._session.refresh(row)
        row.personal_nas_url = personal_nas_url
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
        manager_user_id: uuid.UUID | None = None,
        clear_manager: bool = False,
        daily_tokens: Decimal | None = None,
        clear_daily_tokens: bool = False,
    ) -> UnitMemberRow:
        unit = await self._require_tagger(actor, code, user_id)
        row = await self._directory.member(unit.id, user_id)
        if row is None or row.left_at is not None:
            raise UnitNotFoundError(
                "Thành viên không thuộc ban này.", details={"reason": "member_not_tagged"}
            )
        before = self._snapshot(row)
        restructures = (
            (role is not None and role is not row.role)
            or (is_lead is not None and is_lead is not row.is_lead)
            or clear_manager
            or manager_user_id is not None
        )
        if restructures:
            # Roles and own-Leader links are seats in departments now (0061).
            self._require_assigner(actor, code)
        if (role is not None and role is not row.role) or (
            is_lead is not None and is_lead is not row.is_lead
        ):
            target = role or row.role
            self._check_role(code, target)
            await departments.place(
                self._session,
                code,
                user_id,
                target,
                is_lead=bool(is_lead if is_lead is not None else row.is_lead)
                and target in FUNCTION_ROLES,
            )
            await self._session.refresh(row)
        if clear_member_code or member_code is not None:
            wanted = None if clear_member_code else self._normalise_member_code(member_code)
            if wanted != row.member_code:
                # 0063: the person's code, everywhere - the OWNER's and the ADMIN's.
                if actor.role not in (Role.OWNER, Role.ADMIN):
                    raise UnitAccessDeniedError(
                        "Chỉ quản trị viên mới sửa được mã nhân viên.",
                        details={"reason": "member_code_forbidden"},
                    )
                person = await self._session.get(User, user_id)
                assert person is not None
                await write_member_code(self._session, person, wanted)
                await self._session.refresh(row)
        if clear_personal_nas_url:
            row.personal_nas_url = None
        elif personal_nas_url is not None:
            row.personal_nas_url = personal_nas_url
        if clear_daily_tokens:
            row.daily_tokens = None
        elif daily_tokens is not None:
            row.daily_tokens = daily_tokens
        if clear_manager or manager_user_id is not None:
            try:
                await departments.set_unit_manager(
                    self._session,
                    code,
                    user_id,
                    None if clear_manager else manager_user_id,
                    row.role,
                )
            except departments.DepartmentValidationError as error:
                raise UnitValidationError(
                    "Trưởng quản lý phải là Trưởng phòng của đúng phòng này.",
                    details={"reason": "unit_manager_invalid"},
                ) from error
            await self._session.refresh(row)
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
        unit = await self._require_tagger(actor, code, user_id)
        await self._require_placer(actor, code, None)
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
        await departments.unplace(self._session, code, user_id, guard_open_work=False)
        await self._session.refresh(row)
        if row.left_at is None:
            # Still in the stream through a seat elsewhere (e.g. another
            # department that orders): close it here all the same.
            row.left_at = utcnow()
            row.manager_user_id = None
        # A grant belongs to the tag: it does not survive it.
        row.birthday_orders = False
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

        Only for somebody who may tag in at least one unit.
        """
        membership = await self._directory.membership_for(actor)
        # 2026-10-17: a department head picks people for their own department
        # from the same directory (Quản lý tài khoản).
        heads = any(seat.position is Position.HEAD for seat in membership.seats)
        tagger = bool(self.tag_units(membership, actor))
        if not tagger and not heads:
            raise UnitAccessDeniedError(
                "Bạn không có quyền quản trị luồng nào.",
                details={"reason": "unit_admin_forbidden"},
            )
        users = list(
            (
                await self._session.scalars(
                    select(User).where(User.deleted_at.is_(None)).order_by(User.full_name, User.id)
                )
            ).all()
        )
        if not tagger:
            # A head takes in only somebody seated nowhere yet.
            seated = await load_seats(self._session, [user.id for user in users])
            users = [user for user in users if not seated.get(user.id)]
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

    async def untagged(self, *, actor: Actor) -> list[User]:
        """Active accounts with no open tag, newest first - the people waiting
        for a stream. For the OWNER, the ADMIN and any team lead tagged
        somewhere (``unit_tag_forbidden`` otherwise)."""
        membership = await self._directory.membership_for(actor)
        if not self.tag_units(membership, actor):
            raise UnitTagForbiddenError()
        open_tag = exists(
            select(OrgUnitMember.id).where(
                OrgUnitMember.user_id == User.id, OrgUnitMember.left_at.is_(None)
            )
        )
        statement = (
            select(User)
            .where(User.active.is_(True), ~open_tag)
            .order_by(User.created_at.desc(), User.full_name, User.id)
        )
        return list((await self._session.scalars(statement)).all())

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

    # --- platforms ------------------------------------------------------------

    async def platforms(
        self, *, actor: Actor, code: UnitCode, include_inactive: bool = False
    ) -> list[UnitPlatform]:
        if include_inactive:
            unit = await self._require_admin(actor, code)
        else:
            await self._directory.require(actor, code)
            unit = await self._directory.unit(code)
        statement = select(UnitPlatform).where(UnitPlatform.unit_id == unit.id)
        if not include_inactive:
            statement = statement.where(UnitPlatform.active.is_(True))
        statement = statement.order_by(
            UnitPlatform.sort_order, func.lower(UnitPlatform.name), UnitPlatform.id
        )
        return list((await self._session.scalars(statement)).all())

    async def create_platform(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        name: str,
        active: bool = True,
        sort_order: int | None = None,
    ) -> UnitPlatform:
        unit = await self._require_admin(actor, code)
        cleaned = self._catalogue_name(name, "nền tảng")
        await self._check_catalogue_name(
            UnitPlatform, unit.id, cleaned, exclude=None, label="nền tảng"
        )
        if sort_order is None:
            last = await self._session.scalar(
                select(func.max(UnitPlatform.sort_order)).where(UnitPlatform.unit_id == unit.id)
            )
            sort_order = 0 if last is None else int(last) + 1
        row = UnitPlatform(unit_id=unit.id, name=cleaned, active=active, sort_order=sort_order)
        self._session.add(row)
        await self._session.flush()
        await self._audit_catalogue(
            actor, request_id, AuditAction.UNIT_PLATFORM_CREATED, "unit_platform", row, before=None
        )
        return row

    async def update_platform(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        item_id: uuid.UUID,
        name: str | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UnitPlatform:
        unit = await self._require_admin(actor, code)
        row = await self._session.get(UnitPlatform, item_id)
        if row is None or row.unit_id != unit.id:
            raise UnitNotFoundError(
                "Không tìm thấy nền tảng.", details={"reason": "platform_not_found"}
            )
        before = self._catalogue_snapshot(row)
        if name is not None:
            cleaned = self._catalogue_name(name, "nền tảng")
            await self._check_catalogue_name(
                UnitPlatform, unit.id, cleaned, exclude=row.id, label="nền tảng"
            )
            row.name = cleaned
        if active is not None:
            row.active = active
        if sort_order is not None:
            row.sort_order = sort_order
        await self._session.flush()
        await self._audit_catalogue(
            actor,
            request_id,
            AuditAction.UNIT_PLATFORM_UPDATED,
            "unit_platform",
            row,
            before=before,
        )
        return row

    # --- durations ------------------------------------------------------------

    async def durations(
        self, *, actor: Actor, code: UnitCode, include_inactive: bool = False
    ) -> list[UnitDuration]:
        if include_inactive:
            unit = await self._require_admin(actor, code)
        else:
            await self._directory.require(actor, code)
            unit = await self._directory.unit(code)
        statement = select(UnitDuration).where(UnitDuration.unit_id == unit.id)
        if not include_inactive:
            statement = statement.where(UnitDuration.active.is_(True))
        statement = statement.order_by(
            UnitDuration.sort_order, func.lower(UnitDuration.name), UnitDuration.id
        )
        return list((await self._session.scalars(statement)).all())

    async def create_duration(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        name: str,
        points: Decimal,
        active: bool = True,
        sort_order: int | None = None,
    ) -> UnitDuration:
        unit = await self._require_admin(actor, code)
        cleaned = self._catalogue_name(name, "thời lượng")
        await self._check_catalogue_name(
            UnitDuration, unit.id, cleaned, exclude=None, label="thời lượng"
        )
        if sort_order is None:
            last = await self._session.scalar(
                select(func.max(UnitDuration.sort_order)).where(UnitDuration.unit_id == unit.id)
            )
            sort_order = 0 if last is None else int(last) + 1
        row = UnitDuration(
            unit_id=unit.id,
            name=cleaned,
            points=self._video_kind_points(points),
            active=active,
            sort_order=sort_order,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit_catalogue(
            actor, request_id, AuditAction.UNIT_DURATION_CREATED, "unit_duration", row, before=None
        )
        return row

    async def update_duration(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: UnitCode,
        item_id: uuid.UUID,
        name: str | None = None,
        points: Decimal | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UnitDuration:
        unit = await self._require_admin(actor, code)
        row = await self._session.get(UnitDuration, item_id)
        if row is None or row.unit_id != unit.id:
            raise UnitNotFoundError(
                "Không tìm thấy thời lượng.", details={"reason": "duration_not_found"}
            )
        before = self._catalogue_snapshot(row)
        if name is not None:
            cleaned = self._catalogue_name(name, "thời lượng")
            await self._check_catalogue_name(
                UnitDuration, unit.id, cleaned, exclude=row.id, label="thời lượng"
            )
            row.name = cleaned
        if points is not None:
            row.points = self._video_kind_points(points)
        if active is not None:
            row.active = active
        if sort_order is not None:
            row.sort_order = sort_order
        await self._session.flush()
        await self._audit_catalogue(
            actor,
            request_id,
            AuditAction.UNIT_DURATION_UPDATED,
            "unit_duration",
            row,
            before=before,
        )
        return row

    # --- settings and health ------------------------------------------------

    async def update_settings(
        self, *, actor: Actor, request_id: uuid.UUID, code: UnitCode, patch: dict[str, Any]
    ) -> UnitSettings:
        unit = await self._require_admin(actor, code)
        current = UnitSettings.model_validate(unit.settings or {})
        wanted = patch.get("effort_enabled")
        if wanted is not None and wanted != current.effort_enabled and actor.role is not Role.OWNER:
            # Effort (token) mode is the owner's own switch.
            raise UnitAccessDeniedError(
                "Chỉ chủ sở hữu bật/tắt được chế độ tính effort (token).",
                details={"reason": "effort_setting_forbidden"},
            )
        if patch.get("permissions") is not None:
            # 0061: the matrix's role columns are departments' grants now -
            # the OWNER's to decide; only the Admin column stays here.
            if actor.role is not Role.OWNER:
                raise UnitAccessDeniedError(
                    "Chỉ chủ sở hữu mới phân quyền.",
                    details={"reason": "permissions_forbidden"},
                )
            try:
                admin_column = await departments.apply_order_matrix(
                    self._session, patch["permissions"]
                )
            except AdsPermissionMatrixError as error:
                raise UnitValidationError(
                    "Bảng quyền không hợp lệ.",
                    details={"reason": "invalid_settings", "field": "permissions"},
                ) from error
            patch = {**patch, "permissions": {**current.permissions, **admin_column}}
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
            warnings.append(
                ("no_head", "Luồng ORD chưa có Trưởng phòng ADS: không ai duyệt được order.")
            )
        for node_type in (OrderNodeType.BIEN_TAP, OrderNodeType.THIET_KE, OrderNodeType.DUNG):
            members = await self._directory.function_members(unit.id, node_type)
            if members and not any(row.membership.is_lead for row in members):
                warnings.append(
                    (
                        f"no_lead_{node_type.value.lower()}",
                        f"Ban {node_type_label(node_type)} có thành viên nhưng chưa có "
                        f"{LEAD_ROLE_LABELS[ROLE_FOR_NODE[node_type]]}: việc tới sẽ chờ giao, "
                        "chưa có ai phân công.",
                    )
                )
        # No warning for a missing member code: the first order derives one
        # from the name (``derive_member_code``). Rules for codes come later.
        return warnings

    # --- shared catalogue helpers (platforms, durations) ---------------------

    @staticmethod
    def _catalogue_name(value: str, label: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise UnitValidationError(
                f"Cần nhập tên {label}.",
                details={"reason": f"{label}_name_missing", "field": "name"},
            )
        if len(cleaned) > 120:
            raise UnitValidationError(
                f"Tên {label} tối đa 120 ký tự.",
                details={"reason": f"{label}_name_too_long", "field": "name"},
            )
        return cleaned

    async def _check_catalogue_name(
        self,
        model: type[UnitPlatform] | type[UnitDuration],
        unit_id: uuid.UUID,
        name: str,
        *,
        exclude: uuid.UUID | None,
        label: str,
    ) -> None:
        statement = select(model.id).where(
            model.unit_id == unit_id,
            func.lower(model.name) == name.lower(),
        )
        if exclude is not None:
            statement = statement.where(model.id != exclude)
        if await self._session.scalar(statement.limit(1)) is not None:
            raise UnitValidationError(
                f"Tên {label} này đã có trong ban.",
                details={"reason": f"{label}_name_taken", "field": "name"},
            )

    async def _audit_catalogue(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        entity_type: str,
        row: UnitPlatform | UnitDuration,
        *,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type=entity_type,
            entity_id=str(row.id),
            before_data=before,
            after_data=self._catalogue_snapshot(row),
        )

    @staticmethod
    def _catalogue_snapshot(row: UnitPlatform | UnitDuration) -> dict[str, Any]:
        data: dict[str, Any] = {
            "unit_id": str(row.unit_id),
            "name": row.name,
            "active": row.active,
            "sort_order": row.sort_order,
        }
        if hasattr(row, "points"):
            data["points"] = str(row.points)
        return data

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
        """See ``domain.units.member_code.normalise_member_code``."""
        return normalise_member_code(value)

    async def _flush_translating(self, code: UnitCode, row: OrgUnitMember) -> None:
        """Flush the tag. Two members may share a member code for now (0052
        dropped the unique index): their orders share one counter per day, so
        order codes stay unique."""
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
            "manager_user_id": None if row.manager_user_id is None else str(row.manager_user_id),
            "birthday_orders": bool(row.birthday_orders),
            "left_at": None if row.left_at is None else row.left_at.isoformat(),
        }

    async def _refit_managers(self, unit_id: uuid.UUID, row: OrgUnitMember) -> None:
        """After ``row`` changed (role, Leader flag, untag): drop every manager
        link that no longer fits - ``row``'s own, and those of the people who
        reported to ``row`` - so their work falls back to the whole ban."""
        if row.manager_user_id is not None:
            boss = await self._directory.member(unit_id, row.manager_user_id)
            if boss is None or not manager_fits(boss, row):
                row.manager_user_id = None
        reports = (
            await self._session.scalars(
                select(OrgUnitMember).where(
                    OrgUnitMember.unit_id == unit_id,
                    OrgUnitMember.manager_user_id == row.user_id,
                )
            )
        ).all()
        for report in reports:
            if not manager_fits(row, report):
                report.manager_user_id = None


__all__ = ["ROLES_BY_UNIT", "UnitAdminService"]
