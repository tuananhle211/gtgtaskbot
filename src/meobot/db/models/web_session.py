"""Browser sessions for the PR web admin.

Step 1E. One table, ``web_sessions``.

Why this exists
---------------

Before it, ``meobot.api.deps.get_current_system_actor`` returned a synthetic
``OWNER`` for **every** HTTP request - a development stand-in whose own docstring
said "replace with a real mechanism … and stop granting OWNER unconditionally".
That is survivable for an API bound to localhost and read mostly by its author.
It is not survivable for a PR admin panel, where the same actor can grant Head
Review to anybody and then approve with it.

So a request now has to carry a session that resolves to a real ``users`` row,
and every PR authorization decision is made against *that* person.

How a session is created
------------------------

There is no password, and no Telegram identity is used as the web credential:

1. a registered user asks the bot for a link;
2. the bot mints a **one-time login token**, stores only its hash, and sends the
   link privately - to a chat Telegram will only open because that person
   started the bot, which is what makes the delivery itself a proof of identity;
3. opening the link redeems the token and mints a **session token**, returned as
   an ``HttpOnly`` cookie. Only its hash is stored.

The cookie is the credential from then on. Telegram was the enrolment channel,
not the authenticator - which matters, because a Telegram id in a header would
be a bearer token anybody could copy.

Why one table for both
----------------------

A login token and a session are the same shape - a hashed secret with an expiry
and a subject - and the login token *becomes* the session's parent. Two tables
would duplicate the expiry and revocation logic; ``kind`` separates them, and
``redeemed_at`` records the moment one turned into the other.

What is never stored
--------------------

**The tokens themselves.** Only ``token_hash``. A database dump therefore does
not contain anything that can be replayed as a login, which is the whole reason
to hash rather than encrypt: there is no key to lose.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import ensure_utc
from meobot.db.base import Base, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE


class WebSessionKind(StrEnum):
    """What a row is for.

    ``LOGIN_TOKEN`` is single-use and short-lived; ``SESSION`` is the cookie a
    browser sends. Separate values rather than a boolean because "has this been
    redeemed" and "is this a session" are different questions and a boolean
    would answer neither clearly.
    """

    # S105 fires on the value resembling a credential name. It is an enum member
    # naming a row kind; the credential itself is never stored at all.
    LOGIN_TOKEN = "LOGIN_TOKEN"  # noqa: S105
    SESSION = "SESSION"


class WebSessionAuthMethod(StrEnum):
    """How a session came to exist (0045).

    ``TELEGRAM_LINK`` - redeemed from the bot's login link, the original and
    still the default path. ``PASSWORD`` - ``POST /api/auth/password-login``.
    Only the second is held to "change the default password first".
    """

    TELEGRAM_LINK = "TELEGRAM_LINK"
    PASSWORD = "PASSWORD"  # noqa: S105 - a login method's name, not a secret


class WebSession(Base, UUIDPrimaryKeyMixin):
    """One login token or one browser session.

    No ``updated_at``: the only mutations are stamping ``redeemed_at`` or
    ``revoked_at``, and each is its own dated fact. Rows are never deleted -
    "this session was revoked at 14:02" is the answer to a question somebody
    will eventually ask, and a missing row answers nothing.
    """

    __tablename__ = "web_sessions"
    __table_args__ = (
        CheckConstraint("length(trim(token_hash)) > 0", name="token_hash_not_empty"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        # The lookup every authenticated request makes: hash to row. Unique
        # because a hash collision would mean two subjects sharing a credential.
        Index("uq_web_sessions_token_hash", "token_hash", unique=True),
        # "This person's live sessions", for logout-everywhere and for showing
        # somebody where they are signed in.
        Index("ix_web_sessions_user_kind", "user_id", "kind", "revoked_at"),
        CheckConstraint("auth_method IN ('TELEGRAM_LINK', 'PASSWORD')", name="auth_method_known"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    kind: Mapped[WebSessionKind] = mapped_column(
        value_enum(WebSessionKind, name="web_session_kind", length=20), nullable=False
    )
    #: How the session was created. Every row before 0045 is a Telegram link.
    auth_method: Mapped[WebSessionAuthMethod] = mapped_column(
        value_enum(WebSessionAuthMethod, name="web_session_auth_method", length=20),
        nullable=False,
        default=WebSessionAuthMethod.TELEGRAM_LINK,
        server_default=WebSessionAuthMethod.TELEGRAM_LINK.value,
    )
    #: SHA-256 of the token. The token itself is shown once and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When a login token became a session. Null on a session row, and on a
    #: login token nobody has used yet.
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set by logout, by an admin, or by redeeming the login token that created
    #: this session's predecessor. Never deleted.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Truncated. Enough to tell a phone from a laptop when somebody asks "what
    #: is signed in", and deliberately not a fingerprint.
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The address the session was created from. Stored for the same reason and
    #: with the same limits.
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def is_live(self, *, now: datetime) -> bool:
        """True when this row may still be used.

        One predicate rather than three checks scattered across callers: a
        session is usable only if nobody revoked it, nothing has redeemed it,
        and it has not expired. Getting one of those wrong in one caller is how
        a revoked session keeps working.

        ``expires_at`` goes through :func:`ensure_utc` because a driver can hand
        back a naive datetime even from a ``timestamptz`` column - SQLite always
        does, which is what the offline tests run on. Comparing a naive value to
        an aware one raises, and an exception here would read as "session
        invalid" only by accident. The same helper is used for the same reason in
        ``access_request_service`` and ``group_policy_service``.
        """
        return (
            self.revoked_at is None
            and self.redeemed_at is None
            and ensure_utc(self.expires_at) > ensure_utc(now)
        )


__all__: list[str] = ["WebSession", "WebSessionAuthMethod", "WebSessionKind"]
