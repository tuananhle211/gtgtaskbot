"""A PR channel's link to a platform account, and the OAuth attempt that made it.

Step 1F.2.4b. Two tables, and the reason they are two rather than columns on
``pr_channels`` is the first thing worth saying: **a credential is not channel
master data.**

``pr_channels`` is read by the content form, the publication form, the board and
the assistant context. Putting a refresh token on it would mean every one of
those queries loads a secret it has no use for, and every future response model
becomes a place a secret could leak by accident. So the secret lives in its own
table, is read by exactly one service, and is never joined into anything a
client sees.

``pr_channel_connections``
--------------------------

One row per ``(channel, provider)``, unique. Two live OAuth grants racing to
sync one channel is not a state anybody wants to debug, and the index makes it
unrepresentable rather than merely unlikely.

The row carries three separable things, and the separation is the design:

* **the credential** - ``encrypted_credential`` and ``state``. Whether MeoBot
  can talk to the platform at all;
* **the identity** - ``provider_account_id`` and ``provider_account_name``.
  Which account it is talking to;
* **the health** - the ``last_sync_*`` columns. How the most recent attempt went.

A connection that has failed its last five syncs on a provider outage is
``CONNECTED`` with ``sync_status = FAILED``. One string for both would have had
to pick which of those to say.

``encrypted_credential``
------------------------

Ciphertext from :mod:`meobot.core.secrets` - AES-256-GCM, with the connection's
own id as additional authenticated data, so a value copied into another row
fails to decrypt rather than handing that channel somebody else's account. Text
rather than bytes because the envelope is self-describing ASCII and a DBA
looking at the column should be able to see at a glance that it is not a token.

**Provider-neutral by name and by contract**, since Step 1F.2.4c. What it holds
is *whatever this provider needs in order to obtain a usable access token
without a person present*:

* **Google** - a refresh token, which is exchanged for an access token on every
  sync;
* **Meta** - a long-lived Page access token, which is used directly and is never
  exchanged for anything.

It was called ``encrypted_refresh_token`` until migration 0029, which was
accurate while YouTube was the only connector and would have been a lie the
moment Meta arrived - Meta has no refresh token at all. See that revision.

**No short-lived access token is stored.** Google's live about an hour and are
minted per sync; Meta's durable Page token *is* the access token. Either way
there is one secret at rest per connection, not two.

``pr_channel_oauth_states``
---------------------------

The CSRF device, and the same shape ``web_sessions`` already uses for login
tokens: **only the hash is stored**. A state token is a bearer value for the
few minutes it lives - anybody holding it can complete an authorization against
the channel it names - so the database holds something that can be compared and
not something that can be replayed.

It binds the attempt to a channel *and* to the user who started it, which is
what stops a callback from naming a channel of its own choosing. Single-use
(``consumed_at``) and short-lived (``expires_at``), because a state token that
works twice is not a state token.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.domain.pr.channel_connections import (
    PrChannelConnectionState,
    PrChannelSyncErrorCode,
    PrChannelSyncStatus,
)
from meobot.domain.pr.channel_metrics import PrChannelPlatform

#: What makes a connection **live**: it has not been taken down. Written once
#: and used by both the model's partial unique index and the migration that
#: creates it, so the two cannot drift. Spelled in SQL that PostgreSQL and
#: SQLite both accept, because the offline suite builds this schema from the
#: models - the same reason ``OPEN_ASSIGNMENT`` in ``db.models.pr`` is a
#: ``text()`` clause.
LIVE_CONNECTION = text("status <> 'DISCONNECTED'")


class PrChannelConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One channel's link to one platform account."""

    __tablename__ = "pr_channel_connections"
    __table_args__ = (
        CheckConstraint(_not_empty("provider_account_id"), name="account_id_not_empty"),
        CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_not_negative"),
        # At most one live connection per channel and provider. Partial, so a
        # channel that was disconnected and reconnected keeps its history rather
        # than colliding with itself - the same shape as the open-assignment
        # index in ``db.models.pr``, and for the same reason.
        Index(
            "uq_pr_channel_connections_live",
            "channel_id",
            "provider",
            unique=True,
            postgresql_where=LIVE_CONNECTION,
            sqlite_where=LIVE_CONNECTION,
        ),
        # "Which connections are due for a sweep": the scheduler's only query.
        # Ordered so its leftmost prefix answers "the live ones" on its own.
        Index(
            "ix_pr_channel_connections_due",
            "status",
            "auto_sync_enabled",
            "last_sync_succeeded_at",
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: The canonical platform this connection speaks to. Stored rather than
    #: derived from the channel so that changing a channel's platform cannot
    #: silently repoint a live OAuth grant at a different API.
    provider: Mapped[PrChannelPlatform] = mapped_column(
        value_enum(PrChannelPlatform, name="pr_channel_platform", length=20), nullable=False
    )
    status: Mapped[PrChannelConnectionState] = mapped_column(
        value_enum(PrChannelConnectionState, name="pr_channel_connection_state", length=20),
        nullable=False,
        default=PrChannelConnectionState.CONNECTED,
        server_default=PrChannelConnectionState.CONNECTED.value,
    )

    #: The platform's own id for the authorized account. **This is what the
    #: connector binds to** - not ``pr_channels.external_id``, which a person
    #: types and may have typed wrong. See the design doc on precedence.
    provider_account_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The account's display name at connect time, for a person to recognise.
    #: Refreshed on every sync; never used as identity.
    provider_account_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    provider_account_handle: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: AES-256-GCM ciphertext from :mod:`meobot.core.secrets`, bound to this
    #: row's id. Never returned by an API, never logged, never audited. Null
    #: once the connection is taken down - disconnecting drops the secret and
    #: keeps the row, because "this was connected and then was not" is history.
    #:
    #: Provider-neutral: a Google refresh token, a Meta long-lived Page token,
    #: or whatever the next connector needs. See the class docstring.
    encrypted_credential: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Space-separated scope names as the provider granted them. Scope *names*
    #: are not secrets - they are what the operator consented to, and seeing
    #: that a grant is missing one is how ``INSUFFICIENT_SCOPE`` gets diagnosed.
    granted_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)

    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Whether the scheduler may pick this up. On by default: somebody who
    #: connects a channel wants it synced, and a switch that defaults to off
    #: makes the feature look broken.
    auto_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # --- Health -----------------------------------------------------------
    sync_status: Mapped[PrChannelSyncStatus] = mapped_column(
        value_enum(PrChannelSyncStatus, name="pr_channel_sync_status", length=20),
        nullable=False,
        default=PrChannelSyncStatus.NEVER_SYNCED,
        server_default=PrChannelSyncStatus.NEVER_SYNCED.value,
    )
    #: Set when a worker claims this connection and cleared when it settles.
    #: Together with ``sync_status = SYNCING`` this **is** the concurrency lock:
    #: claiming is a conditional UPDATE, so two workers cannot both win. See
    #: ``PrChannelSyncService.claim``.
    last_sync_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The cadence is measured from here, not from the last *attempt*: a
    #: connection failing every cycle must still become due.
    last_sync_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: One of :class:`~meobot.domain.pr.channel_connections.PrChannelSyncErrorCode`.
    #: A closed vocabulary, never a provider message.
    last_sync_error_code: Mapped[PrChannelSyncErrorCode | None] = mapped_column(
        value_enum(PrChannelSyncErrorCode, name="pr_channel_sync_error", length=30), nullable=True
    )
    #: A short Vietnamese sentence MeoBot wrote, for a person to read. **Never**
    #: a provider payload, a stack trace or a response body: those are somebody
    #: else's prose about somebody else's system, they change without notice,
    #: and this column is rendered on a screen.
    last_sync_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Drives backoff. Reset to zero by any success.
    consecutive_failures: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )


class PrChannelOAuthState(Base, UUIDPrimaryKeyMixin):
    """One in-flight authorization attempt.

    No ``updated_at``: the only mutation is stamping ``consumed_at``, which is a
    dated fact rather than an edit. Rows are kept after use - "this state was
    redeemed at 14:02" is what makes a replay attempt visible rather than
    indistinguishable from an unknown token.
    """

    __tablename__ = "pr_channel_oauth_states"
    __table_args__ = (
        CheckConstraint(_not_empty("state_hash"), name="state_hash_not_empty"),
        Index("ix_pr_channel_oauth_states_expires", "expires_at"),
    )

    #: SHA-256 of the state token. The token itself is in the URL Google was
    #: sent to and is never written down - the same decision ``web_sessions``
    #: makes about login tokens, for the same reason.
    state_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False, index=True
    )
    provider: Mapped[PrChannelPlatform] = mapped_column(
        value_enum(PrChannelPlatform, name="pr_channel_platform", length=20), nullable=False
    )
    #: Who started it. The callback is checked against the session that arrives,
    #: so a link completed in somebody else's browser is refused.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: Whether this attempt is a reconnect of an existing binding, so the
    #: callback knows whether changing account is a rebind worth announcing.
    is_reconnect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Stamped the first time it is redeemed. Single use is enforced by checking
    #: this, so a second callback with the same state is refused rather than
    #: quietly establishing a second connection.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__: list[str] = ["LIVE_CONNECTION", "PrChannelConnection", "PrChannelOAuthState"]
