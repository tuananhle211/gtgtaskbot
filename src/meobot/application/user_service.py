"""Adding a team member directly, without an invite code.

The invite flow (:mod:`meobot.application.invite_service`) is self-service: the
new person types the code themselves. This is the other half - somebody with
``user.manage`` creating the row on their behalf, from a Telegram id they
already have.

Both paths hand out a role, so both enforce the same rule:
:func:`~meobot.domain.identity.invites.can_invite_role`. Nobody can create an
account at or above their own level, and ``OWNER`` is configuration, never
something a command can grant.

The role that reaches this service is always the authoritative enum. Aliases
("MEMBER", "trưởng nhóm") are folded back by
:mod:`meobot.domain.identity.labels` at the transport boundary, before any
permission is evaluated.

This module also owns the **lifecycle**: suspend, enable, revoke and change
role. Three rules hold across all of them.

* **Nothing is ever deleted.** "Xoá người này khỏi hệ thống" means
  :attr:`~meobot.domain.access.models.UserStatus.REVOKED`. The row stays, so the
  scripts they wrote, the approvals they gave and the audit entries naming them
  all keep pointing at a real user.
* **The owner is out of reach.** ``OWNER`` comes from
  ``MEOBOT_OWNER_TELEGRAM_ID``; no command may suspend, revoke, demote or
  recreate it. A system whose administrator can be locked out by an
  administrator has no administrator.
* **Blocking is global.** Suspension and revocation are properties of the
  account, so no per-group ``allow`` can put a blocked account back on the air.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_support import lock_row
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.invites import can_invite_role
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

MAX_NAME_LENGTH = 200

#: Refusal used wherever somebody aims a lifecycle command at the owner.
OWNER_IS_PROTECTED = (
    f"Không thể thay đổi trạng thái hoặc vai trò của {role_label(Role.OWNER)}. "
    "Vai trò này đến từ cấu hình hệ thống."
)


class UserService:
    """Creates and lists ``users`` rows.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def add_user(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        telegram_user_id: int,
        role: Role,
        full_name: str = "",
        telegram_username: str | None = None,
    ) -> User:
        """Register somebody by Telegram id and return the new row.

        Raises:
            AuthorizationError: When the actor lacks ``user.manage``, or may not
                grant ``role``.
            ValidationError: When the Telegram id is not a plausible id.
            ConflictError: When that Telegram account is already registered.
        """
        if not has_permission(actor.role, Permission.USER_MANAGE):
            raise AuthorizationError(
                f"Chỉ {role_label(Role.OWNER)} và {role_label(Role.ADMIN)} "
                "mới thêm được thành viên.",
                # The audit trail and the logs speak the enum, not the label.
                # ``reason`` is the stable code the web panel branches on; the
                # sentence is what Telegram prints. Both are kept.
                details={
                    "reason": "member_add_forbidden",
                    "actor_role": actor.role.value,
                    "target_role": role.value,
                },
            )
        if not can_invite_role(actor.role, role):
            raise AuthorizationError(
                f"Vai trò {role_label(actor.role)} không thể thêm "
                f"thành viên với vai trò {role_label(role)}.",
                details={
                    "reason": "invalid_role",
                    "actor_role": actor.role.value,
                    "target_role": role.value,
                },
            )
        if telegram_user_id <= 0:
            raise ValidationError(
                "Telegram ID phải là một số nguyên dương.",
                details={"reason": "invalid_telegram_id", "field": "telegram_user_id"},
            )

        existing = await self.by_telegram_id(telegram_user_id)
        if existing is not None:
            # A revoked account is not resurrected by adding them again: that
            # would make revocation reversible by anyone who can add a user.
            if existing.status is UserStatus.REVOKED:
                raise ConflictError(
                    "Thành viên này đã bị loại khỏi PR. "
                    "Không thể đăng ký lại tài khoản Telegram này.",
                    details={"reason": "member_revoked", "user_id": str(existing.id)},
                )
            raise ConflictError(
                "Tài khoản Telegram này đã được đăng ký.",
                details={
                    "reason": "member_already_registered",
                    "user_id": str(existing.id),
                    "status": existing.status.value,
                },
            )

        now = utcnow()
        user = User(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            full_name=(full_name.strip() or telegram_username or f"user-{telegram_user_id}")[
                :MAX_NAME_LENGTH
            ],
            role=role,
            active=True,
            status=UserStatus.ACTIVE,
            last_status_changed_at=now,
            added_by_user_id=actor.user_id,
        )
        self._session.add(user)
        try:
            await self._session.flush()
        except Exception as exc:  # pragma: no cover - concurrent add of one id
            raise ConflictError(
                "Tài khoản Telegram này đã được đăng ký.",
                details={"reason": "member_already_registered"},
            ) from exc

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_REGISTERED.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            # ``role`` is the authoritative enum and stays that way; the label
            # rides along so an audit reader sees what the user was told.
            after_data={
                "role": role.value,
                "role_label": role_label(role),
                "telegram_user_id": telegram_user_id,
                "via": "add_user",
            },
        )
        logger.info(
            "user_added",
            extra={"user_id": str(user.id), "role": role.value, "via": "add_user"},
        )
        return user

    # --- Lookup -----------------------------------------------------------
    async def by_telegram_id(self, telegram_user_id: int) -> User | None:
        """Fetch a user by Telegram id, whatever their status."""
        result = await self._session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch a user by primary key."""
        return await self._session.get(User, user_id)

    async def list_users(self, *, include_blocked: bool = True) -> Sequence[User]:
        """Every registered user, newest first."""
        statement = select(User).order_by(User.created_at.desc())
        if not include_blocked:
            statement = statement.where(User.status == UserStatus.ACTIVE)
        result = await self._session.execute(statement)
        return result.scalars().all()

    # --- Lifecycle --------------------------------------------------------
    def _guard_target(self, actor: Actor, target: User) -> None:
        """Refuse the operations nobody may perform on anybody.

        Two invariants, checked before any lifecycle write:
        the owner is untouchable, and nobody may act on an account at or above
        their own level - which is the same rule that governs invitations.
        """
        if not has_permission(actor.role, Permission.USER_STATUS_MANAGE):
            raise AuthorizationError(
                f"Chỉ {role_label(Role.OWNER)} được thay đổi trạng thái thành viên.",
                details={"reason": "member_manage_forbidden", "actor_role": actor.role.value},
            )
        if target.role is Role.OWNER:
            # The one configured owner is untouchable by every command, which
            # is also what makes "the last owner" a rule that needs no counting:
            # nobody can demote, suspend or revoke an owner, and nobody can be
            # promoted to one - see ``can_invite_role``.
            raise AuthorizationError(
                OWNER_IS_PROTECTED,
                details={"reason": "owner_protected", "target_role": target.role.value},
            )
        if actor.user_id is not None and target.id == actor.user_id:
            # Acting on oneself is refused by the rank rule below as well (a
            # role never outranks itself); named separately so a screen can say
            # "not your own account" rather than "not your rank".
            raise AuthorizationError(
                "Bạn không thể tự thay đổi trạng thái hoặc vai trò của chính mình.",
                details={"reason": "self_change_forbidden", "actor_role": actor.role.value},
            )
        if not actor.role.outranks(target.role):
            raise AuthorizationError(
                f"Vai trò {role_label(actor.role)} không thể thao tác với "
                f"{role_label(target.role)}.",
                details={
                    "reason": "target_outranks_actor",
                    "actor_role": actor.role.value,
                    "target_role": target.role.value,
                },
            )

    async def _record_status_change(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user: User,
        action: str,
        before: UserStatus,
        reason: str | None,
    ) -> None:
        """One audit row per lifecycle change, enum first, label alongside."""
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            before_data={"status": before.value},
            after_data={
                "status": user.status.value,
                "role": user.role.value,
                "role_label": role_label(user.role),
                "target_telegram_id": user.telegram_user_id,
                "reason": reason,
            },
        )

    async def suspend(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        reason: str | None = None,
    ) -> User:
        """Block an account globally, reversibly.

        Raises:
            NotFoundError: When the user does not exist.
            AuthorizationError: When the actor may not do this to this target.
        """
        user = await self._require(user_id)
        self._guard_target(actor, user)
        before = user.status
        now = utcnow()
        user.status = UserStatus.SUSPENDED
        user.active = False
        user.suspended_at = now
        user.suspended_by_user_id = actor.user_id
        user.status_reason = reason
        user.last_status_changed_at = now
        await self._session.flush()
        await self._record_status_change(
            actor=actor,
            request_id=request_id,
            user=user,
            action=AuditAction.USER_SUSPENDED.value,
            before=before,
            reason=reason,
        )
        logger.info("user_suspended", extra={"user_id": str(user.id)})
        return user

    async def enable(self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID) -> User:
        """Put a suspended account back on the air.

        A revoked account is *not* reachable from here: revocation is meant to
        be final, and undoing it is a deliberate, separately named act.
        """
        user = await self._require(user_id)
        self._guard_target(actor, user)
        if user.status is UserStatus.REVOKED:
            raise ValidationError(
                "Thành viên này đã bị loại khỏi PR và không thể kích hoạt lại.",
                details={"reason": "member_revoked", "user_id": str(user.id)},
            )
        before = user.status
        now = utcnow()
        user.status = UserStatus.ACTIVE
        user.active = True
        user.suspended_at = None
        user.suspended_by_user_id = None
        user.status_reason = None
        user.last_status_changed_at = now
        await self._session.flush()
        await self._record_status_change(
            actor=actor,
            request_id=request_id,
            user=user,
            action=AuditAction.USER_ENABLED.value,
            before=before,
            reason=None,
        )
        logger.info("user_enabled", extra={"user_id": str(user.id)})
        return user

    async def revoke(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        reason: str | None = None,
    ) -> User:
        """End an account's access for good, without deleting anything.

        This is what "xoá khỏi hệ thống" means here. Every row that names this
        user - scripts, approvals, audit entries - keeps pointing at them.
        """
        user = await self._require(user_id)
        self._guard_target(actor, user)
        before = user.status
        now = utcnow()
        user.status = UserStatus.REVOKED
        user.active = False
        user.revoked_at = now
        user.revoked_by_user_id = actor.user_id
        user.status_reason = reason
        user.last_status_changed_at = now
        await self._session.flush()
        await self._record_status_change(
            actor=actor,
            request_id=request_id,
            user=user,
            action=AuditAction.USER_REVOKED.value,
            before=before,
            reason=reason,
        )
        logger.info("user_revoked", extra={"user_id": str(user.id)})
        return user

    async def restore(self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID) -> User:
        """Undo a revocation. Deliberately separate from :meth:`enable`."""
        user = await self._require(user_id)
        self._guard_target(actor, user)
        before = user.status
        now = utcnow()
        user.status = UserStatus.ACTIVE
        user.active = True
        user.revoked_at = None
        user.revoked_by_user_id = None
        user.suspended_at = None
        user.suspended_by_user_id = None
        user.status_reason = None
        user.last_status_changed_at = now
        await self._session.flush()
        await self._record_status_change(
            actor=actor,
            request_id=request_id,
            user=user,
            action=AuditAction.USER_ENABLED.value,
            before=before,
            reason="restored",
        )
        return user

    async def change_role(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        role: Role,
    ) -> User:
        """Move somebody to a different authoritative role.

        The target role must be one the actor could have granted in the first
        place, so nobody can promote a colleague past themselves - and ``OWNER``
        is not grantable at all.
        """
        user = await self._require(user_id)
        self._guard_target(actor, user)
        if not has_permission(actor.role, Permission.USER_ROLE_MANAGE):
            raise AuthorizationError(
                f"Chỉ {role_label(Role.OWNER)} được đổi vai trò thành viên.",
                details={"reason": "role_change_forbidden", "actor_role": actor.role.value},
            )
        if not can_invite_role(actor.role, role):
            raise AuthorizationError(
                f"Không thể đặt vai trò {role_label(role)}.",
                details={
                    "reason": "invalid_role",
                    "actor_role": actor.role.value,
                    "target_role": role.value,
                },
            )
        if user.role is role:
            raise ConflictError(
                f"{user.full_name} đã ở vai trò {role_label(role)}.",
                details={"reason": "role_unchanged", "role": role.value},
            )
        before_role = user.role
        user.role = role
        user.last_status_changed_at = utcnow()
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_ROLE_CHANGED.value,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            before_data={"role": before_role.value, "role_label": role_label(before_role)},
            after_data={"role": role.value, "role_label": role_label(role)},
        )
        logger.info(
            "user_role_changed",
            extra={"user_id": str(user.id), "before": before_role.value, "after": role.value},
        )
        return user

    async def _require(self, user_id: uuid.UUID) -> User:
        """The row, **locked** where the backend can lock it.

        Every lifecycle write goes through here, so two administrators moving
        the same account at once - a suspension racing a role change, or two
        reactivations - serialise on the row rather than interleave. A no-op
        on SQLite, where the offline suite runs.
        """
        user = await lock_row(self._session, User, user_id)
        if user is None:
            raise NotFoundError(
                f"Không tìm thấy người dùng {user_id}",
                details={"reason": "member_not_found", "user_id": str(user_id)},
            )
        return user


def status_label(status: UserStatus) -> str:
    """Vietnamese wording for a lifecycle status."""
    return {
        UserStatus.PENDING: "Chờ kích hoạt",
        UserStatus.ACTIVE: "Đang hoạt động",
        UserStatus.SUSPENDED: "Tạm khoá",
        UserStatus.REVOKED: "Đã loại khỏi PR",
    }[status]


def status_changed_at(user: User) -> datetime | None:
    """When this account's status last moved, if it ever has."""
    return user.last_status_changed_at
