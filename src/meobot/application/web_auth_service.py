"""Authenticating a browser for the PR web admin.

Step 1E. This is the module that replaces ``get_current_system_actor``'s
synthetic OWNER with a real person.

The shape of it
---------------

Two secrets, both single-purpose:

* a **login token** - one use, minutes long, delivered by Telegram DM;
* a **session token** - the cookie, hours long, revocable.

Redeeming the first mints the second. Neither is ever stored; only
``sha256(token)`` goes in the database, so a dump of ``web_sessions`` contains
nothing that can be replayed. Hashing rather than encrypting is the point -
there is no key to lose, and no way for this service to recover a token it
issued.

Why Telegram delivers the login token
-------------------------------------

Telegram will only deliver a private message to somebody who started a
conversation with the bot, and this deployment already knows which ``users`` row
each Telegram account belongs to. So a DM to that account is a message only that
person can read, which is exactly what a login link needs. Enrolment, not
authentication: **the Telegram id is never accepted as a web credential.** A
request carrying one in a header proves nothing, because ids are public. The
cookie is the credential, and it exists only because somebody opened a link that
was sent to them and no longer works.

What this service will not do
-----------------------------

It does not decide anything about PR. It resolves a request to an
:class:`Actor`; every question about what that actor may do is answered inside
the ``Pr*`` services, against the ``Role`` and the capability grants. An
authenticated stranger has exactly the authority their ``users`` row gives them,
which for a new EMPLOYEE is nearly none.

Failure is silent on purpose
----------------------------

:meth:`resolve_session` returns ``None`` for an unknown, expired, revoked or
malformed token, without distinguishing them. The caller turns that into one
401. Telling a caller *why* their token failed tells somebody probing the
endpoint which of their guesses was closer.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_support import supports_row_locks
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConfigurationError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

#: Bytes of entropy per token. ``token_urlsafe(32)`` is 43 characters and 256
#: bits - far past guessable, and short enough to survive being pasted into a
#: browser bar by somebody on a phone.
_TOKEN_BYTES = 32

#: The cookie name. ``__Host-`` would be stricter still, but it forbids
#: ``Domain`` and requires HTTPS, which would break local development outright;
#: the ``Secure``/``HttpOnly``/``SameSite=Strict`` triple is set explicitly by
#: the router instead.
SESSION_COOKIE = "meobot_web_session"

#: Query parameter carrying the login token in the magic link.
LOGIN_TOKEN_PARAM = "t"  # noqa: S105 - a query-parameter name, not a secret

#: Stored ``user_agent`` is truncated to this. Enough to tell a phone from a
#: laptop; not enough to be a fingerprint worth keeping.
_USER_AGENT_MAX = 200


def hash_token(token: str) -> str:
    """Return the stored form of a token.

    SHA-256 with no salt and no stretching, deliberately: these are 256-bit
    random strings, not passwords. There is no dictionary to attack, so the cost
    of bcrypt would buy nothing while adding latency to every request that
    presents a cookie. The unsalted hash also lets the lookup be a single
    indexed equality - which is what keeps session resolution one query.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedLogin:
    """A magic link, returned once and unrecoverable afterwards."""

    url: str
    expires_at: datetime

    def __str__(self) -> str:  # pragma: no cover - defensive
        """Never render the URL by accident.

        The token is in :attr:`url`. Anything that logs this object by
        interpolation would otherwise log a working credential.
        """
        return f"IssuedLogin(expires_at={self.expires_at.isoformat()})"


@dataclass(frozen=True)
class IssuedSession:
    """A new browser session. The token is the cookie value to set."""

    token: str
    expires_at: datetime
    actor: Actor

    def __str__(self) -> str:  # pragma: no cover - defensive
        """Same reasoning as :meth:`IssuedLogin.__str__`."""
        return f"IssuedSession(user_id={self.actor.user_id}, expires_at={self.expires_at})"


class WebAuthService:
    """Issues, redeems, resolves and revokes web sessions.

    Owns no transaction: it ``flush()``es and lets the caller commit, like every
    other application service here. That matters for redemption in particular -
    marking the login token used and inserting the session must land together or
    not at all, and the caller's transaction is what guarantees that.

    Args:
        session: Caller-owned session.
        settings: TTLs and the public base URL.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Issuing ----------------------------------------------------------

    async def issue_login_link(self, *, user_id: uuid.UUID) -> IssuedLogin:
        """Mint a single-use login link for a registered, active user.

        Any login token this user has not yet used is revoked first. Somebody
        asking for a second link has usually lost the first - leaving it live
        would mean an unused credential sitting in a chat history with nothing
        to invalidate it.

        Args:
            user_id: Row id in ``users``. Not a Telegram id: the caller has
                already resolved identity, and accepting a Telegram id here
                would make this method the thing that trusts one.

        Raises:
            ConfigurationError: ``WEB_BASE_URL`` is unset. Refusing beats
                emitting a link to an unknown host.
            AuthorizationError: no such user, or the user is deactivated.
        """
        base = self._settings.web_base_url.strip().rstrip("/")
        if not base:
            raise ConfigurationError(
                "Web admin chưa được cấu hình (WEB_BASE_URL trống).",
                details={"setting": "WEB_BASE_URL"},
            )

        user = await self._session.get(User, user_id)
        if user is None or not user.active:
            # One message for both. A caller learning "that user exists but is
            # deactivated" learns something they were not asking for.
            raise AuthorizationError("Tài khoản này không thể đăng nhập web.")

        await self._revoke_open_login_tokens(user_id=user_id)

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = utcnow()
        expires_at = now + timedelta(seconds=self._settings.web_login_token_ttl_seconds)
        self._session.add(
            WebSession(
                user_id=user_id,
                kind=WebSessionKind.LOGIN_TOKEN,
                token_hash=hash_token(token),
                expires_at=expires_at,
            )
        )
        await self._session.flush()
        logger.info(
            "web_login_link_issued",
            extra={"user_id": str(user_id), "expires_at": expires_at.isoformat()},
        )
        return IssuedLogin(
            url=f"{base}/auth/login?{LOGIN_TOKEN_PARAM}={token}", expires_at=expires_at
        )

    # --- Redeeming --------------------------------------------------------

    async def redeem_login_token(
        self,
        *,
        token: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> IssuedSession:
        """Turn a login token into a browser session.

        Single-use, and single-use under a race
        ---------------------------------------

        The row is taken with ``SELECT … FOR UPDATE`` before ``redeemed_at`` is
        checked. Without the lock, two requests carrying the same link both read
        the row while it is still unredeemed, both pass the check, and **both mint
        a session** - one link, two credentials. That is not a theoretical race:
        it is what a double-click on a link in a Telegram desktop client does, and
        what a link-preview fetcher followed by the human does.

        With the lock, the second transaction blocks, then reads the committed
        ``redeemed_at`` and refuses. Marking the token used and inserting the
        session happen in the caller's one transaction, so they land together or
        not at all.

        Raises:
            ValidationError: the token is missing, unknown, expired or already
                used. One error for all four, with no detail about which.
        """
        row = await self._lookup(token, kind=WebSessionKind.LOGIN_TOKEN, for_update=True)
        now = utcnow()
        if row is None or not row.is_live(now=now):
            logger.info("web_login_rejected", extra={"reason": "invalid_or_expired"})
            raise ValidationError("Liên kết đăng nhập không hợp lệ hoặc đã hết hạn.")

        user = await self._session.get(User, row.user_id)
        if user is None or not user.active:
            # Deactivation between issuing and clicking. The link was valid; the
            # person is not, and the row is burnt so it cannot be retried.
            row.redeemed_at = now
            await self._session.flush()
            logger.info("web_login_rejected", extra={"reason": "user_inactive"})
            raise ValidationError("Liên kết đăng nhập không hợp lệ hoặc đã hết hạn.")

        row.redeemed_at = now
        session_token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = now + timedelta(seconds=self._settings.web_session_ttl_seconds)
        self._session.add(
            WebSession(
                user_id=user.id,
                kind=WebSessionKind.SESSION,
                token_hash=hash_token(session_token),
                expires_at=expires_at,
                user_agent=(user_agent or "")[:_USER_AGENT_MAX] or None,
                created_ip=ip,
            )
        )
        await self._session.flush()
        logger.info("web_login_redeemed", extra={"user_id": str(user.id)})
        return IssuedSession(
            token=session_token, expires_at=expires_at, actor=self._actor_for(user)
        )

    # --- Resolving --------------------------------------------------------

    async def resolve_session(self, *, token: str | None) -> Actor | None:
        """Return the actor behind a session cookie, or ``None``.

        ``None`` covers every failure - no cookie, unknown, expired, revoked,
        or belonging to somebody since deactivated. The caller answers all of
        them with one 401.

        The actor is rebuilt from the ``users`` row on **every** request rather
        than cached in the session: a role change or a deactivation then takes
        effect on the next click, not whenever the cookie happens to expire.
        Somebody demoted at 09:00 stops being a TEAM_LEAD at 09:00.
        """
        if not token:
            return None
        row = await self._lookup(token, kind=WebSessionKind.SESSION)
        if row is None or not row.is_live(now=utcnow()):
            return None
        user = await self._session.get(User, row.user_id)
        if user is None or not user.active:
            return None
        return self._actor_for(user)

    # --- Revoking ---------------------------------------------------------

    async def revoke_session(self, *, token: str | None) -> bool:
        """Sign one browser out. True when a live session was revoked.

        Idempotent, and quiet about an unknown token: logout is not a place to
        tell a caller whether the thing they presented was real.
        """
        if not token:
            return False
        row = await self._lookup(token, kind=WebSessionKind.SESSION)
        now = utcnow()
        if row is None or not row.is_live(now=now):
            return False
        row.revoked_at = now
        await self._session.flush()
        logger.info("web_session_revoked", extra={"user_id": str(row.user_id)})
        return True

    async def revoke_all_for_user(self, *, user_id: uuid.UUID) -> int:
        """Sign a person out everywhere. Returns how many sessions closed.

        The operation somebody needs after losing a laptop, and the one an admin
        needs after deactivating an account. Login tokens go too - an unredeemed
        one is a session waiting to happen.
        """
        now = utcnow()
        # Loaded and stamped one by one rather than a bulk UPDATE ... RETURNING:
        # the count is the useful part of the answer ("you were signed out of 3
        # places"), and ``rowcount`` after a bulk update is a driver detail this
        # would rather not depend on. The row count per person is small - a
        # phone, a laptop, a stale token.
        rows = (
            (
                await self._session.execute(
                    select(WebSession).where(
                        WebSession.user_id == user_id,
                        WebSession.revoked_at.is_(None),
                        WebSession.expires_at > now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.revoked_at = now
        await self._session.flush()
        logger.info("web_sessions_revoked_all", extra={"user_id": str(user_id), "count": len(rows)})
        return len(rows)

    # --- Internals --------------------------------------------------------

    async def _lookup(
        self, token: str, *, kind: WebSessionKind, for_update: bool = False
    ) -> WebSession | None:
        """Find a row by token hash, constrained to the expected kind.

        The ``kind`` filter is not decoration: without it a login token pasted
        into the session cookie would resolve as a session, turning a
        short-lived single-use secret into a long-lived one.

        ``for_update`` takes ``SELECT … FOR UPDATE`` on the row - see
        :meth:`redeem_login_token` for why redemption needs it. Guarded by the
        dialect, as everywhere else in this codebase: SQLite has no row locks, so
        the offline tests fall back to a plain read and the concurrency guarantee
        is proven against real PostgreSQL in
        ``tests/integration/test_web_auth_concurrency.py``.
        """
        stmt = select(WebSession).where(
            WebSession.token_hash == hash_token(token), WebSession.kind == kind
        )
        if for_update and supports_row_locks(self._session):
            # No ``skip_locked``: the second transaction must *wait* and then see
            # the committed ``redeemed_at``, not skip the row and conclude the
            # token does not exist.
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _revoke_open_login_tokens(self, *, user_id: uuid.UUID) -> None:
        """Invalidate this user's unused login tokens."""
        now = utcnow()
        await self._session.execute(
            update(WebSession)
            .where(
                WebSession.user_id == user_id,
                WebSession.kind == WebSessionKind.LOGIN_TOKEN,
                WebSession.redeemed_at.is_(None),
                WebSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    @staticmethod
    def _actor_for(user: User) -> Actor:
        """Build the domain actor from a ``users`` row.

        Mirrors :meth:`IdentityService.resolve_actor`'s registered-user branch
        and deliberately omits its bootstrap-owner branch: ``is_bootstrap_owner``
        exists for somebody talking to the bot before their row is written, and
        a web session cannot exist without a row. There is no path here that
        grants OWNER from configuration.
        """
        return Actor(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            telegram_username=user.telegram_username,
            full_name=user.full_name,
            role=user.role,
            active=user.active,
        )


__all__: list[str] = [
    "LOGIN_TOKEN_PARAM",
    "SESSION_COOKIE",
    "IssuedLogin",
    "IssuedSession",
    "WebAuthService",
    "hash_token",
]
