"""Password login and password change for the web panel.

The username is the person's Telegram numeric id (``users.telegram_user_id``);
every account starts on the default password from settings, and a session
opened with it - or with a temporary password a reset sent by Telegram
(``users.password_temporary``, see ``password_reset_service``) - must choose a
new one before anything else (enforced in
:func:`meobot.api.deps.get_current_web_actor`). The only rules for a new
password: not empty, not the default.

What a caller can and cannot learn
----------------------------------

Every failure - unknown id, wrong password, deactivated or suspended account,
malformed id - is the **same** outcome, and every attempt costs **one** scrypt
(a dummy hash is verified when there is no real one), so neither the answer nor
its timing tells a stranger which ids have accounts. The one exception the
contract asks for is the lockout: after ``web_login_max_failures`` consecutive
failures the account answers ``login_locked`` for ``web_login_lockout_seconds``.
Only an existing, active account can be locked.

The Telegram link is untouched: it never reads the password, the counter or the
lock, so a person locked out here can still ask the bot for a link.

Nothing secret is written anywhere: the password, the hash and the default are
never logged and never put in an audit payload.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.passwords import (
    burn_dummy_hash_async,
    check_new_password,
    hash_password_async,
    matches_default,
    verify_password_async,
)
from meobot.application.audit_service import AuditService
from meobot.application.pr_support import supports_row_locks
from meobot.application.web_auth_service import (
    IssuedSession,
    WebAuthService,
    password_change_due,
)
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSessionAuthMethod
from meobot.domain.account.errors import (
    AccountNotFoundError,
    LoginLockedError,
    PasswordRejectedError,
)
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

#: A Telegram user id: digits only, at most 19 of them (BIGINT).
_TELEGRAM_ID = re.compile(r"^\d{1,19}$")
_BIGINT_MAX = 2**63 - 1


class LoginStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    LOCKED = "locked"


@dataclass(frozen=True)
class LoginOutcome:
    """What the route renders. ``issued`` only on success."""

    status: LoginStatus
    issued: IssuedSession | None = None
    must_change_password: bool = False

    def __str__(self) -> str:  # pragma: no cover - defensive
        """Never render the session token by accident."""
        return f"LoginOutcome(status={self.status.value})"


def parse_username(username: str) -> int | None:
    """The Telegram id typed as the username, or ``None`` when it is not one."""
    value = username.strip()
    if not _TELEGRAM_ID.match(value):
        return None
    number = int(value)
    return number if 0 < number <= _BIGINT_MAX else None


class PasswordService:
    """Sign in with a password, and change it."""

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

    # --- login ----------------------------------------------------------------

    async def login(
        self,
        *,
        username: str,
        password: str,
        request_id: uuid.UUID,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> LoginOutcome:
        """Check the credentials; on success mint a ``PASSWORD`` session.

        Never raises for bad credentials: the failure counter must be written
        in the same transaction the caller commits, so the outcome is returned
        and the route renders it.
        """
        telegram_id = parse_username(username)
        user = await self._locked_user_by_telegram_id(telegram_id)
        if user is not None and not user.may_use_meobot:
            # A deactivated or suspended account is indistinguishable from an
            # unknown id: no lock, no counter, the same answer.
            user = None
        now = utcnow()

        if user is not None and self._is_locked(user, now):
            await burn_dummy_hash_async(password)
            await self._audit_login(request_id, user, telegram_id, AuditResult.DENIED, "locked")
            return LoginOutcome(status=LoginStatus.LOCKED)

        password_ok = await self._password_matches(user, password)
        if user is None:
            await self._audit_login(request_id, None, telegram_id, AuditResult.FAILED, "rejected")
            return LoginOutcome(status=LoginStatus.FAILED)
        if not password_ok:
            self._register_failure(user, now)
            await self._audit_login(request_id, user, telegram_id, AuditResult.FAILED, "rejected")
            return LoginOutcome(status=LoginStatus.FAILED)

        user.failed_login_count = 0
        user.locked_until = None
        issued = await self._auth.issue_session(
            user=user, auth_method=WebSessionAuthMethod.PASSWORD, user_agent=user_agent, ip=ip
        )
        must_change = password_change_due(user)
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_user_id,
                action=AuditAction.AUTH_PASSWORD_LOGIN_SUCCEEDED,
                result=AuditResult.SUCCESS,
                entity_type="user",
                entity_id=str(user.id),
                after_data={"must_change_password": must_change},
            )
        )
        logger.info("web_password_login", extra={"user_id": str(user.id)})
        return LoginOutcome(status=LoginStatus.OK, issued=issued, must_change_password=must_change)

    # --- change -----------------------------------------------------------------

    async def change_password(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        current_password: str,
        new_password: str,
        keep_session_id: uuid.UUID | None,
    ) -> int:
        """Replace the password. Returns how many other sessions were signed out.

        Raises:
            LoginLockedError: the account is locked (429).
            PasswordRejectedError: ``password_empty`` / ``password_too_long`` /
                ``password_is_default`` / ``current_password_wrong`` (422). A
                wrong current password counts towards the lockout, and the
                counter is flushed **before** the raise - the route commits it.
                The new password may equal the current one (product decision:
                no rules beyond "not empty, not the default"); choosing it
                still ends a temporary password.
        """
        user = await self._locked_user_by_id(actor.user_id)
        if user is None or not user.may_use_meobot:
            raise AccountNotFoundError()
        now = utcnow()
        if self._is_locked(user, now):
            raise LoginLockedError()

        default = self._settings.web_default_password.get_secret_value()
        check_new_password(new_password, default=default)

        if not await self._password_matches(user, current_password):
            self._register_failure(user, now)
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.AUTH_PASSWORD_CHANGE_FAILED,
                result=AuditResult.FAILED,
                entity_type="user",
                entity_id=str(user.id),
                after_data={"reason": "current_password_wrong"},
            )
            await self._session.flush()
            raise PasswordRejectedError(
                "current_password_wrong",
                "Mật khẩu hiện tại không đúng.",
                field="current_password",
            )

        user.password_hash = await hash_password_async(new_password)
        user.password_changed_at = now
        user.password_temporary = False
        user.failed_login_count = 0
        user.locked_until = None
        await self._session.flush()
        revoked = await self._auth.revoke_all_for_user(
            user_id=user.id, except_session_id=keep_session_id
        )
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.AUTH_PASSWORD_CHANGED,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user.id),
            after_data={"other_sessions_revoked": revoked},
        )
        logger.info("web_password_changed", extra={"user_id": str(user.id)})
        return revoked

    # --- internals ----------------------------------------------------------------

    async def _password_matches(self, user: User | None, password: str) -> bool:
        """Exactly one scrypt per call, whoever ``user`` is.

        A stored hash is verified; otherwise a dummy hash is, and the password
        is compared with the default in constant time (only when there is a
        real account - an unknown id matches nothing).
        """
        if user is not None and user.password_hash is not None:
            return await verify_password_async(password, user.password_hash)
        await burn_dummy_hash_async(password)
        if user is None:
            return False
        return matches_default(password, self._settings.web_default_password.get_secret_value())

    def _is_locked(self, user: User, now: datetime) -> bool:
        return user.locked_until is not None and ensure_utc(user.locked_until) > now

    def _register_failure(self, user: User, now: datetime) -> None:
        """Count one failure; lock (and restart the count) at the limit."""
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= self._settings.web_login_max_failures:
            user.locked_until = now + timedelta(seconds=self._settings.web_login_lockout_seconds)
            user.failed_login_count = 0
            logger.warning("web_password_login_locked", extra={"user_id": str(user.id)})

    async def _locked_user_by_telegram_id(self, telegram_id: int | None) -> User | None:
        """The account behind the id, held against a concurrent attempt.

        ``FOR UPDATE`` so two parallel guesses cannot both read the counter at
        four and both write five - which would let a script take ten guesses
        per lockout window instead of five.
        """
        if telegram_id is None:
            return None
        statement = select(User).where(User.telegram_user_id == telegram_id)
        if supports_row_locks(self._session):
            statement = statement.with_for_update()
        user: User | None = (await self._session.execute(statement)).scalar_one_or_none()
        return user

    async def _locked_user_by_id(self, user_id: uuid.UUID | None) -> User | None:
        if user_id is None:
            return None
        statement = select(User).where(User.id == user_id)
        if supports_row_locks(self._session):
            statement = statement.with_for_update()
        user: User | None = (await self._session.execute(statement)).scalar_one_or_none()
        return user

    async def _audit_login(
        self,
        request_id: uuid.UUID,
        user: User | None,
        telegram_id: int | None,
        result: AuditResult,
        reason: str,
    ) -> None:
        """One audit row per failed attempt. The password is never in it."""
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                actor_user_id=None if user is None else user.id,
                actor_telegram_id=telegram_id,
                action=AuditAction.AUTH_PASSWORD_LOGIN_FAILED,
                result=result,
                entity_type="user",
                entity_id=None if user is None else str(user.id),
                after_data={"reason": reason, "known_account": user is not None},
            )
        )


__all__ = ["LoginOutcome", "LoginStatus", "PasswordService", "parse_username"]
