"""Step 1F.2.4b: channel connectors, OAuth state, and sync idempotency.

Two new tables and one new column. 0020 through 0027 are untouched, and so is
every row in them.

Why a connection is not a column on ``pr_channels``
----------------------------------------------------

Because a credential is not channel master data. ``pr_channels`` is read by the
content form, the publication form, the board and the assistant context, and a
refresh token on that table would mean every one of those queries loads a secret
it has no use for - and every response model built from it becomes somewhere a
secret can leak by accident. The secret lives in its own table, one service
reads it, and nothing a client sees joins to it.

``pr_channel_connections``
--------------------------

One row per ``(channel, provider)``, enforced by a **partial** unique index over
the live ones. A channel that was disconnected and reconnected keeps both rows;
what is refused is two live grants racing to sync the same channel, which is not
a state anybody should have to debug.

``encrypted_refresh_token`` is text, and it is ciphertext: AES-256-GCM from
``meobot.core.secrets``, with the connection's own id as additional
authenticated data. That last part is why the column can be text without being
a liability - a value copied into another row fails to decrypt rather than
handing that channel somebody else's YouTube account.

**There is no access-token column.** Access tokens live about an hour and are
minted from the refresh token when a sync needs one. Storing them would put a
second secret at rest to save one HTTP round trip a day.

``last_sync_*`` and ``status`` are deliberately separate. A connection whose
last five syncs failed on a provider outage is ``CONNECTED`` with
``sync_status = FAILED``; one string for both would have had to choose which of
those two true things to say.

``pr_channel_oauth_states``
---------------------------

The CSRF device. Only ``state_hash`` is stored - the same decision
``web_sessions`` makes about login tokens, and for the same reason: a state
token is a bearer value while it lives, so the database holds something that can
be compared and not something that can be replayed.

It binds an authorization attempt to a channel **and** to the user who started
it, which is what stops a callback from naming a channel of its own choosing.
``expires_at`` makes it short-lived and ``consumed_at`` makes it single-use.
Redeemed rows are kept rather than deleted, so a replay attempt is visibly a
replay rather than indistinguishable from an unknown token.

``pr_channel_metric_snapshots.provider_reading_key``
-----------------------------------------------------

Sync idempotency, and the only change to a Step 1F.2.4a table. A scheduler
retry, two overlapping sweeps or a manager pressing "Đồng bộ ngay" twice must
not leave three identical rows in a time series.

The key is a hash over the account, the **effective reporting period** and the
metric values - not over ``observed_at``, which would make every key unique and
the whole mechanism a no-op. The period is what makes it correct rather than
merely convenient: two readings a week apart that both report 124,812 followers
are different readings that happen to agree, they have different period bounds,
and both belong in the timeline.

The unique index is **partial**, over rows that have a key at all. Manual
entries carry none - a person typing numbers in is not a repeatable fetch - so
the many manual rows do not collide with each other on a shared ``NULL``. Same
shape, same reason, as 0013's ``platform_post_id`` index.

Nothing is backfilled. Every existing snapshot keeps ``NULL`` and is unaffected
by the index.

Downgrade
---------

Drops the column, the index and both tables. **What is lost is every channel
connection and every stored refresh token**, which cannot be recovered from
anywhere else: the platform issued them once and will not reissue them without a
person consenting again. After a downgrade, every connected channel must be
reconnected by hand through the OAuth flow.

What survives is everything that matters historically: every metric snapshot,
manual and API alike, with all of its values. Losing ``provider_reading_key``
costs only the ability to recognise a re-fetch of a period already recorded.

Nothing here prints, logs or returns token material, on the way down or up.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CHANNELS = "pr_channels"
SNAPSHOTS = "pr_channel_metric_snapshots"
CONNECTIONS = "pr_channel_connections"
OAUTH_STATES = "pr_channel_oauth_states"

RESTRICT = "RESTRICT"

LIVE_CONNECTION_INDEX = "uq_pr_channel_connections_live"
DUE_INDEX = "ix_pr_channel_connections_due"
CHANNEL_INDEX = "ix_pr_channel_connections_channel_id"
STATE_HASH_INDEX = "ix_pr_channel_oauth_states_state_hash"
STATE_CHANNEL_INDEX = "ix_pr_channel_oauth_states_channel_id"
STATE_EXPIRES_INDEX = "ix_pr_channel_oauth_states_expires"
READING_KEY_INDEX = "uq_pr_channel_metric_snapshots_reading_key"

#: Matches ``LIVE_CONNECTION`` in ``meobot.db.models.pr_channel_connection``.
#: Spelled in SQL both PostgreSQL and SQLite accept - the offline suite builds
#: the same schema from the models, and the two must agree.
LIVE_CONNECTION = "status <> 'DISCONNECTED'"

#: Matches ``KNOWN_READING_KEY`` in ``meobot.db.models.pr_reporting``.
KNOWN_READING_KEY = "provider_reading_key IS NOT NULL"


def upgrade() -> None:
    op.create_table(
        CONNECTIONS,
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="CONNECTED"),
        sa.Column("provider_account_id", sa.String(length=200), nullable=False),
        sa.Column("provider_account_name", sa.String(length=300), nullable=True),
        sa.Column("provider_account_handle", sa.String(length=200), nullable=True),
        # Ciphertext, never a token. See the module docstring.
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("granted_scopes", sa.Text(), nullable=True),
        sa.Column("connected_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "sync_status", sa.String(length=20), nullable=False, server_default="NEVER_SYNCED"
        ),
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error_code", sa.String(length=30), nullable=True),
        sa.Column("last_sync_error_message", sa.String(length=500), nullable=True),
        sa.Column("consecutive_failures", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_channel_connections"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{CHANNELS}.id"],
            name="fk_channel_connection_channel",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["connected_by_user_id"],
            [f"{USERS}.id"],
            name="fk_channel_connection_connected_by",
            ondelete=RESTRICT,
        ),
        # Bare names - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("length(trim(provider_account_id)) > 0", name="account_id_not_empty"),
        sa.CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_not_negative"),
    )
    op.create_index(CHANNEL_INDEX, CONNECTIONS, ["channel_id"])
    # At most one *live* connection per channel and provider.
    op.create_index(
        LIVE_CONNECTION_INDEX,
        CONNECTIONS,
        ["channel_id", "provider"],
        unique=True,
        postgresql_where=sa.text(LIVE_CONNECTION),
        sqlite_where=sa.text(LIVE_CONNECTION),
    )
    op.create_index(
        DUE_INDEX, CONNECTIONS, ["status", "auto_sync_enabled", "last_sync_succeeded_at"]
    )

    op.create_table(
        OAUTH_STATES,
        # The hash, never the token.
        sa.Column("state_hash", sa.String(length=128), nullable=False),
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("is_reconnect", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_channel_oauth_states"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{CHANNELS}.id"],
            name="fk_channel_oauth_state_channel",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{USERS}.id"], name="fk_channel_oauth_state_user", ondelete=RESTRICT
        ),
        sa.CheckConstraint("length(trim(state_hash)) > 0", name="state_hash_not_empty"),
    )
    # A **unique index** rather than a unique constraint plus a plain index:
    # the model declares ``unique=True, index=True`` on this column, which
    # SQLAlchemy renders as exactly one unique index, and the two descriptions
    # have to match - ``test_pr_core_migrations`` runs Alembic's own comparator
    # over the migrated schema against the model metadata.
    op.create_index(STATE_HASH_INDEX, OAUTH_STATES, ["state_hash"], unique=True)
    op.create_index(STATE_CHANNEL_INDEX, OAUTH_STATES, ["channel_id"])
    op.create_index(STATE_EXPIRES_INDEX, OAUTH_STATES, ["expires_at"])

    # Sync idempotency on the Step 1F.2.4a timeline. Nullable, unbackfilled.
    op.add_column(SNAPSHOTS, sa.Column("provider_reading_key", sa.String(length=64), nullable=True))
    op.create_index(
        READING_KEY_INDEX,
        SNAPSHOTS,
        ["channel_id", "provider_reading_key"],
        unique=True,
        postgresql_where=sa.text(KNOWN_READING_KEY),
        sqlite_where=sa.text(KNOWN_READING_KEY),
    )


def downgrade() -> None:
    """The exact inverse, by the names that actually exist.

    Destroys every connection and every stored refresh token - see the module
    docstring. Metric history is untouched.
    """
    op.drop_index(READING_KEY_INDEX, table_name=SNAPSHOTS)
    op.drop_column(SNAPSHOTS, "provider_reading_key")

    op.drop_index(STATE_EXPIRES_INDEX, table_name=OAUTH_STATES)
    op.drop_index(STATE_CHANNEL_INDEX, table_name=OAUTH_STATES)
    op.drop_index(STATE_HASH_INDEX, table_name=OAUTH_STATES)
    op.drop_table(OAUTH_STATES)

    op.drop_index(DUE_INDEX, table_name=CONNECTIONS)
    op.drop_index(LIVE_CONNECTION_INDEX, table_name=CONNECTIONS)
    op.drop_index(CHANNEL_INDEX, table_name=CONNECTIONS)
    op.drop_table(CONNECTIONS)
