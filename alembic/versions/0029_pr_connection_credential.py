"""Step 1F.2.4c: the connection secret stops being called a refresh token.

One column rename. No data is moved, no row is touched, and nothing else in the
schema changes.

Why this revision exists
------------------------

Step 1F.2.4b built the connector architecture around the only provider it had,
and Google's OAuth has a refresh token - a credential whose entire purpose is to
be exchanged for a short-lived access token. So the column was called
``encrypted_refresh_token``, which was accurate for exactly one provider.

Meta has no refresh token. Its durable credential is a **long-lived Page access
token**: obtained by exchanging a short-lived user token for a long-lived one
and then reading ``/me/accounts``, and used *directly* as the bearer credential.
It is never exchanged for anything, and when derived from a long-lived user
token it does not expire at all.

Storing that in a column named ``encrypted_refresh_token`` would be a lie in the
schema, and the lie would spread: the provider port would keep declaring
``refresh_access(refresh_token=…)`` for a provider that has nothing of the kind,
and every provider added after Meta would inherit the confusion. Step 1F.2.4c's
own instructions call this out and say to rename rather than to store a Meta
token under a Google name.

So the column becomes ``encrypted_credential`` and the contract becomes:

    the durable credential is whatever this provider needs in order to obtain a
    usable access token without a person present

For Google that is a refresh token, and it gets exchanged. For Meta it is a
long-lived Page token, and it is returned as-is. Both are true statements about
``encrypted_credential``; only one was a true statement about the old name.

What is deliberately **not** in this revision
----------------------------------------------

No ``credential_expires_at``. The stored Meta credential does not expire, and a
credential that stops working is already discovered the way every other auth
failure is - the sync fails with ``AUTH_REQUIRED`` and the connection moves to
``ACTION_REQUIRED``. A column that would be ``NULL`` for both existing providers
is a column nothing reads.

No new state column either. Step 1F.2.4c adds a ``PENDING_SELECTION`` connection
state - the interval after Meta consent and before a Page is chosen - and that
needs no DDL: ``status`` is a plain ``VARCHAR(20)`` with no check constraint, so
a new member of the Python enum is a new string in a column that already accepts
it.

Downgrade
---------

Renames the column back. Lossless in both directions: the ciphertext is
unchanged and is still decryptable, because the AES-GCM envelope is bound to the
connection's **id** rather than to the column's name.

A downgrade does become wrong in one respect, and it is worth stating: any Meta
connection created after this revision would be sitting in a column called
``encrypted_refresh_token``, holding something that is not a refresh token. That
is a naming problem in a schema nobody should be running Meta on, not a data
loss, and no token is printed or logged either way.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONNECTIONS = "pr_channel_connections"


def upgrade() -> None:
    op.alter_column(CONNECTIONS, "encrypted_refresh_token", new_column_name="encrypted_credential")


def downgrade() -> None:
    op.alter_column(CONNECTIONS, "encrypted_credential", new_column_name="encrypted_refresh_token")
