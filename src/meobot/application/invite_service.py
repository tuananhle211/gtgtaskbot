"""Invite-code creation and redemption.

The plaintext code exists in exactly two places: the Telegram message that
delivers it to its creator, and the message in which an employee types it back.
The database only ever holds a hash.

Redemption is the one path that creates a ``users`` row, so it is also the one
path that hands out a role - which is why it re-checks expiry, use count and
the creator's authority instead of trusting the caller.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.org import departments
from meobot.application.org.seats import load_seats
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.department import Department
from meobot.db.models.invite import InviteCode
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.invites import (
    InviteFacts,
    InviteRejection,
    can_invite_role,
    check_invite,
    generate_code,
    hash_code,
    may_create_invites,
    normalize_code,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.models import (
    PRODUCTION_FUNCTIONS,
    DepartmentFunction,
    Position,
    Seat,
    seat_label,
)
from meobot.domain.units.labels import unit_label, unit_role_label
from meobot.domain.units.models import FUNCTION_ROLES, UnitCode, UnitMemberRole, unit_seed_id

logger = get_logger(__name__)

DEFAULT_EXPIRY_DAYS = 7
MAX_EXPIRY_DAYS = 90
MAX_USES = 50

#: Human-facing refusal messages, keyed by domain reason.
REJECTION_MESSAGES: dict[InviteRejection, str] = {
    InviteRejection.UNKNOWN: "Mã mời không hợp lệ.",
    InviteRejection.DISABLED: "Mã mời đã bị vô hiệu hoá.",
    InviteRejection.EXPIRED: "Mã mời đã hết hạn.",
    InviteRejection.EXHAUSTED: "Mã mời đã được dùng hết lượt.",
    InviteRejection.ALREADY_REGISTERED: "Tài khoản Telegram này đã được đăng ký.",
}


@dataclass(frozen=True, slots=True)
class StreamPlacement:
    """Where a stream lead's invitee lands: the stream, their role in it and
    the Leader (head) they report to - the inviter. PR has no own Leader."""

    unit_id: uuid.UUID
    unit_code: UnitCode
    role: UnitMemberRole
    manager_user_id: uuid.UUID | None

    def describe(self, manager_name: str | None) -> str:
        """``"Luồng Order (ORD) · Dựng · Trưởng quản lý: Quỳnh"``."""
        parts = [unit_label(self.unit_code), unit_role_label(self.role)]
        if manager_name:
            parts.append(f"Trưởng quản lý: {manager_name}")
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class DepartmentPlacement:
    """Where a Trưởng phòng's invitee lands (0088): a member's seat in the
    department they head, reporting to them."""

    department_id: uuid.UUID
    name: str
    manager_user_id: uuid.UUID | None

    def describe(self, manager_name: str | None) -> str:
        """``"Nhân viên Tài Chính-Kế Toán · Trưởng quản lý: Hằng"``."""
        seat = Seat(self.department_id, "", self.name, DepartmentFunction.NONE, Position.MEMBER)
        parts = [seat_label(seat)]
        if manager_name:
            parts.append(f"Trưởng quản lý: {manager_name}")
        return " · ".join(parts)


class InviteDepartmentError(ValidationError):
    code = "invite_department_invalid"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ mời được vào phòng bạn đang làm trưởng phòng.",
            details={"reason": self.code, "field": "department_id"},
        )


class InviteForbiddenError(AuthorizationError):
    code = "invite_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ Trưởng phòng, Quản trị viên và Chủ sở hữu mới tạo được mã mời.",
            details={"reason": self.code},
        )


class InviteService:
    """Creates, lists, disables and redeems invite codes.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit
        self._directory = UnitDirectoryService(session)

    async def placement_for(self, actor: Actor) -> StreamPlacement | None:
        """Where ``actor``'s invitees land, from the actor's own tags.

        A Leader of Biên kịch / Design / Dựng brings staff of that ban, a
        Trưởng phòng ADS an orderer - both reporting to the actor; a PR team
        lead a PR member. The OWNER and an ADMIN place nobody: their invitees
        join untagged and are tagged by hand. Several leads: the ORD ban
        first, then the ORD head, then PR.
        """
        if actor.role in (Role.OWNER, Role.ADMIN) or actor.user_id is None:
            return None
        membership = await self._directory.membership_for(actor)
        entries = membership.entries
        for entry in entries:
            if entry.unit_code is UnitCode.ADS and entry.role in FUNCTION_ROLES and entry.is_lead:
                return StreamPlacement(entry.unit_id, UnitCode.ADS, entry.role, actor.user_id)
        for entry in entries:
            if entry.unit_code is UnitCode.ADS and entry.role is UnitMemberRole.HEAD:
                return StreamPlacement(
                    entry.unit_id, UnitCode.ADS, UnitMemberRole.ORDERER, actor.user_id
                )
        if may_create_invites(actor.role):
            for entry in entries:
                if entry.unit_code is UnitCode.PR:
                    return StreamPlacement(entry.unit_id, UnitCode.PR, UnitMemberRole.MEMBER, None)
        return None

    async def department_for(
        self, actor: Actor, department_id: uuid.UUID | None = None
    ) -> DepartmentPlacement | None:
        """The department a Trưởng phòng's invitee joins (0088): the one asked
        for among those they head, else their first headed department - a
        production one first, as the 0051 order put a ban's Leader first. The
        OWNER and an ADMIN place nobody, as before (unless they head one)."""
        if actor.user_id is None:
            return None
        seats = (await load_seats(self._session, [actor.user_id])).get(actor.user_id, ())
        heads = [seat for seat in seats if seat.position is Position.HEAD]
        if department_id is not None:
            chosen = next((seat for seat in heads if seat.department_id == department_id), None)
            if chosen is None:
                raise InviteDepartmentError()
        else:
            chosen = next(
                (seat for seat in heads if seat.function in PRODUCTION_FUNCTIONS),
                heads[0] if heads else None,
            )
        if chosen is None:
            return None
        return DepartmentPlacement(chosen.department_id, chosen.name, actor.user_id)

    async def may_invite(self, actor: Actor) -> bool:
        """A team lead or above, a Trưởng phòng / Leader of a ban in ORD, or
        (0088) the head of any department - whatever their system role."""
        return (
            may_create_invites(actor.role)
            or await self.department_for(actor) is not None
            or await self.placement_for(actor) is not None
        )

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        role: Role = Role.EMPLOYEE,
        scope: str | None = None,
        note: str | None = None,
        expires_in_days: int = DEFAULT_EXPIRY_DAYS,
        max_uses: int = 1,
        department_id: uuid.UUID | None = None,
    ) -> tuple[InviteCode, str]:
        """Create an invite and return ``(row, plaintext_code)``.

        The plaintext is returned once and never stored; the caller shows it to
        the creator and then forgets it.

        Raises:
            AuthorizationError: When the actor may not grant ``role``.
            ValidationError: When expiry or use count is out of range.
        """
        if not await self.may_invite(actor):
            raise InviteForbiddenError()
        # 0088: a department head's code seats its redeemer in their
        # department; the 0051 stream placement only when they head none.
        department = await self.department_for(actor, department_id)
        placement = None if department is not None else await self.placement_for(actor)
        # A head may be a plain employee: they invite their staff.
        staff_invite = (placement is not None or department is not None) and role is Role.EMPLOYEE
        if not (can_invite_role(actor.role, role) or staff_invite):
            raise AuthorizationError(
                f"Vai trò {role_label(actor.role)} không thể tạo mã mời cho {role_label(role)}.",
                # Details feed the audit trail: authoritative enum, always.
                details={"actor_role": actor.role.value, "target_role": role.value},
            )
        if not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
            raise ValidationError(f"Hạn dùng phải từ 1 đến {MAX_EXPIRY_DAYS} ngày.")
        if not 1 <= max_uses <= MAX_USES:
            raise ValidationError(f"Số lượt dùng phải từ 1 đến {MAX_USES}.")

        code = generate_code()
        invite = InviteCode(
            code_hash=hash_code(code),
            role=role,
            scope=scope,
            note=note,
            created_by=actor.user_id,
            created_by_telegram_id=actor.telegram_user_id,
            expires_at=utcnow() + timedelta(days=expires_in_days),
            max_uses=max_uses,
            use_count=0,
            active=True,
            unit_id=None if placement is None else placement.unit_id,
            unit_role=None if placement is None else placement.role,
            manager_user_id=(
                department.manager_user_id
                if department is not None
                else None
                if placement is None
                else placement.manager_user_id
            ),
            department_id=None if department is None else department.department_id,
        )
        self._session.add(invite)
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.INVITE_CREATED.value,
            result=AuditResult.SUCCESS,
            entity_type="invite_code",
            entity_id=str(invite.id),
            # The code itself is deliberately absent from the audit trail.
            after_data={
                # Authoritative enum first; the label is extra context for a
                # human reading the trail, never the thing that is queried.
                "role": role.value,
                "role_label": role_label(role),
                "scope": scope,
                "max_uses": max_uses,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
                "department_id": None if department is None else str(department.department_id),
                "unit": None if placement is None else placement.unit_code.value,
                "unit_role": None if placement is None else placement.role.value,
                "manager_user_id": (
                    None if invite.manager_user_id is None else str(invite.manager_user_id)
                ),
            },
        )
        logger.info(
            "invite_created",
            extra={"invite_id": str(invite.id), "role": role.value, "max_uses": max_uses},
        )
        return invite, code

    async def list_invites(
        self, *, active_only: bool = True, created_by: uuid.UUID | None = None
    ) -> Sequence[InviteCode]:
        """List invites, newest first; ``created_by`` narrows to one creator's."""
        statement = select(InviteCode).order_by(InviteCode.created_at.desc())
        if active_only:
            statement = statement.where(InviteCode.active.is_(True))
        if created_by is not None:
            statement = statement.where(InviteCode.created_by == created_by)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def get(self, invite_id: uuid.UUID) -> InviteCode | None:
        return await self._session.get(InviteCode, invite_id)

    async def disable(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        invite_id: uuid.UUID,
    ) -> InviteCode:
        """Revoke an invite so it can never be redeemed.

        Raises:
            NotFoundError: When the invite does not exist.
        """
        invite = await self._session.get(InviteCode, invite_id)
        if invite is None:
            raise NotFoundError(f"Không tìm thấy mã mời {invite_id}")
        invite.active = False
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.INVITE_DISABLED.value,
            result=AuditResult.SUCCESS,
            entity_type="invite_code",
            entity_id=str(invite.id),
            after_data={"active": False},
        )
        return invite

    async def redeem(
        self,
        *,
        request_id: uuid.UUID,
        code: str,
        telegram_user_id: int,
        telegram_username: str | None = None,
        full_name: str = "",
    ) -> User:
        """Register a Telegram account using an invite code.

        Raises:
            ValidationError: For any refusal - unknown, expired, disabled,
                exhausted, or an account that is already registered. The
                message is the same shape for every case so a stranger cannot
                probe which codes exist.
            ConflictError: When the account was registered concurrently.
        """
        normalized = normalize_code(code)
        if not normalized:
            raise ValidationError(REJECTION_MESSAGES[InviteRejection.UNKNOWN])

        existing = await self._session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise ValidationError(REJECTION_MESSAGES[InviteRejection.ALREADY_REGISTERED])

        result = await self._session.execute(
            select(InviteCode).where(InviteCode.code_hash == hash_code(normalized))
        )
        invite = result.scalar_one_or_none()
        if invite is None:
            await self._audit_rejection(request_id, telegram_user_id, InviteRejection.UNKNOWN)
            raise ValidationError(REJECTION_MESSAGES[InviteRejection.UNKNOWN])

        verdict = check_invite(
            InviteFacts(
                role=invite.role,
                active=invite.active,
                expires_at=ensure_utc(invite.expires_at) if invite.expires_at else None,
                max_uses=invite.max_uses,
                use_count=invite.use_count,
            ),
            now=utcnow(),
        )
        if not verdict.valid:
            reason = verdict.reason or InviteRejection.UNKNOWN
            await self._audit_rejection(request_id, telegram_user_id, reason)
            raise ValidationError(REJECTION_MESSAGES[reason])

        user = User(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            full_name=(full_name or telegram_username or f"user-{telegram_user_id}")[:200],
            role=invite.role,
            active=True,
        )
        self._session.add(user)
        invite.use_count += 1
        if invite.use_count >= invite.max_uses:
            invite.active = False

        try:
            await self._session.flush()
        except Exception as exc:  # pragma: no cover - concurrent /join with the same account
            raise ConflictError(REJECTION_MESSAGES[InviteRejection.ALREADY_REGISTERED]) from exc
        tag = await self._place(invite, user)

        joined = Actor(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            full_name=user.full_name,
            role=user.role,
            active=True,
        )
        await self._audit.record_action(
            request_id=request_id,
            actor=joined,
            action=AuditAction.INVITE_REDEEMED.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            after_data={
                "invite_id": str(invite.id),
                "role": user.role.value,
                "role_label": role_label(user.role),
                "scope": invite.scope,
                "use_count": invite.use_count,
                "unit_member_id": None if tag is None else str(tag.id),
                "department_id": (
                    None if invite.department_id is None else str(invite.department_id)
                ),
            },
        )
        await self._audit.record_action(
            request_id=request_id,
            actor=joined,
            action=AuditAction.USER_REGISTERED.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            after_data={
                "role": user.role.value,
                "role_label": role_label(user.role),
                "via": "invite_code",
            },
        )
        logger.info(
            "invite_redeemed",
            extra={"user_id": str(user.id), "role": user.role.value},
        )
        return user

    async def placement_of(self, invite: InviteCode) -> StreamPlacement | None:
        """The stored placement of ``invite``, if it carries one."""
        if invite.unit_id is None or invite.unit_role is None:
            return None
        unit = await self._session.get(OrgUnit, invite.unit_id)
        if unit is None:
            return None
        return StreamPlacement(
            invite.unit_id, UnitCode(unit.code), invite.unit_role, invite.manager_user_id
        )

    async def joins_label(self, invite: InviteCode) -> str | None:
        """Where the redeemer of ``invite`` will land, in words; None = untagged."""
        placement: StreamPlacement | DepartmentPlacement | None = None
        if invite.department_id is not None:
            department = await self._session.get(Department, invite.department_id)
            if department is not None:
                placement = DepartmentPlacement(
                    department.id, department.name, invite.manager_user_id
                )
        if placement is None:
            placement = await self.placement_of(invite)
        if placement is None:
            return None
        manager = (
            None
            if placement.manager_user_id is None
            else await self._session.get(User, placement.manager_user_id)
        )
        return placement.describe(None if manager is None else manager.full_name)

    async def _place(self, invite: InviteCode, user: User) -> OrgUnitMember | None:
        """Tag the redeemer where the invite says. The Leader link is kept only
        if it still fits (the inviter may have stopped leading since)."""
        if invite.department_id is not None:
            await self._seat(invite, user)
            ads = await self._session.get(OrgUnit, unit_seed_id(UnitCode.ADS))
            return None if ads is None else await self._directory.member(ads.id, user.id)
        if invite.unit_id is None or invite.unit_role is None:
            return None
        unit = await self._session.get(OrgUnit, invite.unit_id)
        if unit is None:
            return None
        # 0061: a seat in the matching department; the tag follows from it.
        await departments.place(self._session, unit.code, user.id, invite.unit_role)
        if invite.manager_user_id is not None:
            # The inviter may no longer head that department: then no link.
            with contextlib.suppress(departments.DepartmentValidationError):
                await departments.set_unit_manager(
                    self._session, unit.code, user.id, invite.manager_user_id, invite.unit_role
                )
        return await self._directory.member(invite.unit_id, user.id)

    async def _seat(self, invite: InviteCode, user: User) -> None:
        """0088: a member's seat in the invite's department, reporting to the
        inviter while they still head it; the stream tags follow the seat."""
        department = await self._session.get(Department, invite.department_id)
        if department is None or not department.active:
            return
        manager = invite.manager_user_id
        if manager is not None:
            try:
                await departments.check_manager(self._session, department.id, user.id, manager)
            except departments.DepartmentValidationError:
                manager = None
        await departments.seat_person(
            self._session, department.id, user.id, Position.MEMBER, manager_user_id=manager
        )
        await departments.sync_stream_tags(self._session, [user.id])

    async def _audit_rejection(
        self,
        request_id: uuid.UUID,
        telegram_user_id: int,
        reason: InviteRejection,
    ) -> None:
        """Record a refused redemption without naming the code."""
        await self._audit.record_action(
            request_id=request_id,
            actor=Actor(telegram_user_id=telegram_user_id, full_name="unknown"),
            action=AuditAction.INVITE_REJECTED.value,
            result=AuditResult.DENIED,
            entity_type="invite_code",
            entity_id=None,
            after_data={"reason": reason.value},
        )
