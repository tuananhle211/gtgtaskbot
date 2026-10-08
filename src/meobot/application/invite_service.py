"""Invite-code creation and redemption.

The plaintext code exists in exactly two places: the Telegram message that
delivers it to its creator, and the message in which an employee types it back.
The database only ever holds a hash.

Redemption is the one path that creates a ``users`` row, so it is also the one
path that hands out a role - which is why it re-checks expiry, use count and
the creator's authority instead of trusting the caller.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.invite import InviteCode
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.invites import (
    InviteFacts,
    InviteRejection,
    can_invite_role,
    check_invite,
    generate_code,
    hash_code,
    normalize_code,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role

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


class InviteService:
    """Creates, lists, disables and redeems invite codes.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

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
    ) -> tuple[InviteCode, str]:
        """Create an invite and return ``(row, plaintext_code)``.

        The plaintext is returned once and never stored; the caller shows it to
        the creator and then forgets it.

        Raises:
            AuthorizationError: When the actor may not grant ``role``.
            ValidationError: When expiry or use count is out of range.
        """
        if not can_invite_role(actor.role, role):
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
