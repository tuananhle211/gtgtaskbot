"""0.6.0a3.post1: server defaults for the dispatch timestamp columns.

What broke
----------

In production, writing the first multi-group draft failed with::

    sqlalchemy.exc.IntegrityError: null value in column "created_at"
    of relation "message_dispatch_drafts" violates not-null constraint

``TimestampMixin`` declares ``created_at`` and ``updated_at`` with
``server_default=func.now()`` and **no Python-side default**, on purpose: the
database is the one clock every writer agrees on. So the ORM deliberately omits
both columns from its ``INSERT`` and expects PostgreSQL to fill them.

Migration ``0010`` created the six dispatch tables with ``nullable=False`` and
*no* ``server_default``. Migrations ``0008`` and ``0009`` had got this right for
every ``TimestampMixin`` table they created; ``0010`` did not, and nothing
caught it - the offline tests build their schema from ``Base.metadata``, which
carries the defaults, so the only place the two definitions disagreed was the
one place no test looked: a real, Alembic-migrated PostgreSQL.

This revision closes that gap. See
``tests/integration/test_dispatch_migrations.py``, which migrates a blank
database through head and inserts the whole dispatch chain without passing a
single timestamp - the test that would have caught the original defect.

What this does
--------------

``ALTER COLUMN ... SET DEFAULT now()`` on eleven columns. That is all it does.

* **No data is read, rewritten, backfilled or deleted.** ``SET DEFAULT`` changes
  the catalog entry for future inserts and does not touch a single existing
  row. It is also not a table rewrite, so it takes an ``ACCESS EXCLUSIVE`` lock
  only for the instant it needs the catalog update.
* **No backfill is needed.** Every one of these columns is already
  ``NOT NULL``, so no existing row can be missing a value. The rows that failed
  never made it in.
* **No column is added, dropped, retyped or renamed**, and ``0010`` is not
  edited.

Ten of the eleven columns come from ``TimestampMixin`` and genuinely require the
default. The eleventh - ``message_dispatch_parts.created_at`` - is passed
explicitly by :class:`~meobot.application.dispatch_service.DispatchService` and
has never been null in practice; it is included so that every timestamp column
in this package is defined the same way in the model and in the schema. A column
that is *usually* supplied is exactly the shape of defect this revision exists
to fix.

Downgrade
---------

``DROP DEFAULT`` on the same eleven columns, restoring precisely the state
``0010`` left behind. No data is lost, because none is touched. Note that
running the downgrade re-introduces the production defect, so it is useful only
for stepping back past this revision on the way to an older one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every timestamp column the dispatch tables expect the database to fill.
#: Written out rather than derived from ``Base.metadata`` on purpose: a
#: migration has to keep meaning what it meant when it ran, and a model that
#: changes next month must not silently change what this revision did.
TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("message_dispatch_drafts", "created_at"),
    ("message_dispatch_drafts", "updated_at"),
    ("message_dispatch_draft_recipients", "created_at"),
    ("message_dispatch_draft_recipients", "updated_at"),
    ("message_dispatches", "created_at"),
    ("message_dispatches", "updated_at"),
    ("message_dispatch_recipients", "created_at"),
    ("message_dispatch_recipients", "updated_at"),
    ("message_dispatch_parts", "created_at"),
    ("message_dispatch_recipient_parts", "created_at"),
    ("message_dispatch_recipient_parts", "updated_at"),
)


def upgrade() -> None:
    """Give every dispatch timestamp column the default its model assumes."""
    for table, column in TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.text("now()"),
        )


def downgrade() -> None:
    """Remove only the defaults this revision added. Nothing else changes."""
    for table, column in TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
