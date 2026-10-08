"""Password reset: a random temporary password, sent to the person's Telegram.

Two doors, one mechanism (:meth:`PasswordResetService.issue_temporary`):

* **self-service** - "Quên mật khẩu?" on ``/login`` posts a Telegram id to
  ``POST /api/auth/password-reset``. Anybody may ask, so the answer is always
  the same 202 and the same sentence, whether the id exists, is active, can be
  messaged, or was reset a minute ago. Per account at most one reset per
  :data:`RESET_INTERVAL` - a request inside the window is silently ignored;
* **admin** - ``POST /api/account/members/{id}/reset-password``, authorised in
  :class:`~meobot.application.account.account_service.AccountService`.

What a reset does
-----------------

A fresh password from :func:`~meobot.application.account.passwords.generate_temporary_password`
is hashed (scrypt, like any other) and stored with ``password_temporary`` set,
so a password session on it must choose a new one before anything else (the
same gate as the default password). The lockout is cleared and **every** web
session of the account is revoked - the old password, and whoever held it, is
out. The password itself goes to the person's private chat through the
existing transactional outbox (:class:`~meobot.application.notification_router.NotificationRouter`
→ the Celery ``notifications.drain_outbox`` worker → ``sendMessage``), in the
same transaction as the new hash: either both land or neither does. The
outbox blanks the password in the stored payload once the row is settled.

Somebody MeoBot cannot message privately (no Telegram id, never pressed Start
in the bot) is left exactly as they were: there is nowhere to send a password,
and changing it would only lock them out.

Never logged, never audited: the temporary password exists in this process,
the outbox payload until delivery, and the person's Telegram chat.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.password_service import parse_username
from meobot.application.account.passwords import (
    burn_dummy_hash_async,
    generate_temporary_password,
    hash_password_async,
)
from meobot.application.audit_service import AuditService
from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.outbox_service import OutboxService
from meobot.application.pr_support import supports_row_locks
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.web_auth_service import WebAuthService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.notifications.models import NotificationEvent, OutboxStatus

logger = get_logger(__name__)

#: The one answer to every self-service request.
RESET_REQUESTED_MESSAGE = "Nếu ID tồn tại, mật khẩu tạm đã được gửi qua Telegram."
#: At most one self-service reset per account in this window.
RESET_INTERVAL = timedelta(minutes=5)
TEMPLATE_KEY = "account.temporary_password"


class ResetOutcome(StrEnum):
    """What a self-service request did. Audited; never told to the caller."""

    SENT = "sent"
    UNKNOWN = "unknown_or_inactive"
    RATE_LIMITED = "rate_limited"
    UNDELIVERABLE = "undeliverable"


class PasswordResetService:
    """Issue temporary passwords and send them by Telegram."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditService,
        auth: WebAuthService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._auth = auth
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)

    # --- self-service -----------------------------------------------------------

    async def request_reset(self, *, username: str, request_id: uuid.UUID) -> ResetOutcome:
        """Handle "Quên mật khẩu?". The caller answers 202 whatever this returns.

        Every branch costs about one scrypt (a dummy hash where no real one is
        made), so the response time does not say which branch ran either.
        """
        telegram_id = parse_username(username)
        user = await self._locked_user(telegram_id)
        if user is not None and not user.may_use_meobot:
            user = None
        now = utcnow()

        outcome: ResetOutcome
        if user is None:
            outcome = ResetOutcome.UNKNOWN
        elif self._recently_reset(user, now):
            outcome = ResetOutcome.RATE_LIMITED
        else:
            chat_id = await self.private_chat_of(user)
            if chat_id is None:
                outcome = ResetOutcome.UNDELIVERABLE
            else:
                await self.issue_temporary(user, chat_id=chat_id, created_by_user_id=None)
                outcome = ResetOutcome.SENT
        if outcome is not ResetOutcome.SENT:
            await burn_dummy_hash_async(username)

        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                actor_user_id=None,
                actor_telegram_id=telegram_id,
                action=AuditAction.AUTH_PASSWORD_RESET_REQUESTED,
                result=(
                    AuditResult.SUCCESS if outcome is ResetOutcome.SENT else AuditResult.DENIED
                ),
                entity_type="user",
                entity_id=None if user is None else str(user.id),
                after_data={"outcome": outcome.value},
            )
        )
        logger.info("web_password_reset_requested", extra={"outcome": outcome.value})
        return outcome

    # --- the shared mechanism -------------------------------------------------------

    async def private_chat_of(self, user: User) -> int | None:
        """Where MeoBot may DM ``user``, or ``None`` (no Telegram id, no Start)."""
        if user.telegram_user_id is None:
            return None
        resolved = await self._resolver.private_destination(user_id=user.id)
        return resolved.telegram_chat_id if resolved.is_resolved else None

    async def issue_temporary(
        self,
        user: User,
        *,
        chat_id: int,
        created_by_user_id: uuid.UUID | None,
    ) -> int:
        """Give ``user`` a new temporary password and queue it to ``chat_id``.

        Returns how many web sessions were revoked. Rides the caller's
        transaction: the hash, the revocations and the outbox row commit
        together.
        """
        temporary = generate_temporary_password()
        now = utcnow()
        user.password_hash = await hash_password_async(temporary)
        user.password_temporary = True
        user.password_changed_at = None
        user.password_reset_at = now
        user.failed_login_count = 0
        user.locked_until = None
        await self._session.flush()
        revoked = await self._auth.revoke_all_for_user(user_id=user.id)
        await self._cancel_unsent(user.id)

        base = self._settings.web_base_url.strip().rstrip("/")
        result = await self._router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.ACCOUNT_TEMPORARY_PASSWORD,
                    template_key=TEMPLATE_KEY,
                    payload={"password": temporary, "login_url": f"{base}/login"},
                    # Every reset is its own message; nothing to deduplicate.
                    idempotency_key=f"account_temporary_password:{user.id}:{uuid.uuid4()}",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    recipient_user_id=user.id,
                    private_chat_id=chat_id,
                    created_by_user_id=created_by_user_id,
                    business_summary="Mật khẩu tạm TasksBot",
                )
            ]
        )
        if not result.any_queued:  # pragma: no cover - a private chat is always allowed
            logger.error("web_password_reset_not_queued", extra={"user_id": str(user.id)})
        logger.info(
            "web_password_reset_issued",
            extra={"user_id": str(user.id), "sessions_revoked": revoked},
        )
        return revoked

    # --- internals ----------------------------------------------------------------------

    def _recently_reset(self, user: User, now: datetime) -> bool:
        return (
            user.password_reset_at is not None
            and ensure_utc(user.password_reset_at) > now - RESET_INTERVAL
        )

    async def _cancel_unsent(self, user_id: uuid.UUID) -> None:
        """An older temporary password still waiting in the outbox is void now."""
        rows = (
            await self._session.scalars(
                select(OutboundMessage).where(
                    OutboundMessage.recipient_user_id == user_id,
                    OutboundMessage.template_key == TEMPLATE_KEY,
                    OutboundMessage.status.in_([OutboxStatus.PENDING, OutboxStatus.RETRY_WAIT]),
                )
            )
        ).all()
        outbox = OutboxService(self._session, self._settings)
        for row in rows:
            await outbox.cancel(row)

    async def _locked_user(self, telegram_id: int | None) -> User | None:
        """The account behind the id, held so two requests cannot both reset it."""
        if telegram_id is None:
            return None
        statement = select(User).where(User.telegram_user_id == telegram_id)
        if supports_row_locks(self._session):
            statement = statement.with_for_update()
        user: User | None = (await self._session.execute(statement)).scalar_one_or_none()
        return user


__all__ = [
    "RESET_INTERVAL",
    "RESET_REQUESTED_MESSAGE",
    "TEMPLATE_KEY",
    "PasswordResetService",
    "ResetOutcome",
]
