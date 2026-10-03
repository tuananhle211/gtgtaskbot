"""Step 1F.2.3d: the web notification inbox, and the priority vocabulary.

Two unrelated-looking things in one revision because they ship as one step. One
new table, ``user_notifications``, and one data migration over ``priority``.

**Nothing existing is altered.** No column is added to, dropped from or retyped
on a Step 1A-1F table; the two ``priority`` columns keep the type, nullability
and server default 0012 gave them; and 0020, 0021 and 0022 are untouched.

``user_notifications``
----------------------

A per-person inbox with read state, which the system did not have. The only
durable notification record until now was ``outbound_messages`` - a Telegram
delivery outbox whose ``telegram_chat_id`` is ``NOT NULL`` and which gets no row
at all when a recipient has no reachable private chat. A web notification centre
read from that table would be permanently empty for anybody who has never opened
the bot, which is precisely the audience it exists for.

This table is a second table, not a second notification system: the same service
writes both, in the same transaction, from the same event. See
``src/meobot/db/models/user_notification.py`` for the full reasoning, including
why the rendered text is stored rather than re-rendered from a template.

Two indexes, both earning their place: ``(recipient_user_id, created_at)`` for
the list, and ``(recipient_user_id, read_at)`` for the unread count that runs on
every page load.

Why the priority change needs no ``ALTER``
-------------------------------------------

The obvious expectation for "add a priority level" is a widened constraint, and
there is nothing to widen. ``pr_content_items.priority`` and
``pr_tasks.priority`` were created by 0012 as::

    sa.Enum(*PRIORITIES, name="pr_priority", native_enum=False, length=20)

Since SQLAlchemy 1.4, ``sa.Enum`` defaults to ``create_constraint=False``, so
``native_enum=False`` emits a bare ``VARCHAR(20)`` - no PostgreSQL ``ENUM``
type, and **no vocabulary ``CHECK``**. The database has never enforced which
priority strings are legal; :func:`~meobot.db.base.value_enum` and the API's
``_enum`` parser do, in Python, and that is where the enforcement stays.

So ``'CRITICAL'`` is eight characters into a column that already accepts twenty,
and it needs no schema change to be storable. This is the same finding Step 1A1
recorded when ``AI_REVIEW`` joined ``pr_workflow_stage``, and the same
conclusion: widen the Python enum, add a parity test, leave the column alone.

Stating it in a revision rather than shipping the enum change with no migration
is deliberate. "Which vocabulary was legal when" is a question the revision
history should answer, and the ``LOW`` backfill below has to happen somewhere
regardless.

What this actually does: ``LOW`` stops existing
------------------------------------------------

``LOW`` was in the vocabulary from 0012 and was never used. No service ever set
it, no Telegram tool ever offered it, the web panel had no control that produced
it, and ``NORMAL`` is both the Python default and the server default - so every
row in both tables should already be ``NORMAL`` or above.

*Should* is not *is*, and a hand-written ``UPDATE`` against production is
exactly the kind of thing that is not in anybody's migration history. The two
statements below are therefore written to be correct whether they match zero
rows or a thousand:

* they are idempotent - re-running changes nothing the second time;
* they name the replacement explicitly rather than relying on the default;
* ``LOW`` maps to ``NORMAL`` because ``NORMAL`` is the new bottom of the scale.
  There is nowhere else for it to go, and leaving the rows behind would strand
  them under a code no enum member matches - which turns every read of that row
  into a ``LookupError`` the moment SQLAlchemy tries to coerce it.

That last point is what makes this a *required* migration rather than a tidy-up:
removing an enum member without moving its rows breaks reads of those rows.

Priority indexes
----------------

**None added.** The work queue orders by priority, but it orders by a ``CASE``
expression built from the enum rather than by the column - see
:mod:`meobot.domain.pr.priority` - and an index on ``priority`` cannot serve an
expression it does not match. An index on the expression itself would help only
once the board's other predicates stop being selective enough to carry the
query, and at roughly fifty new items a day they are, by a wide margin. The
filter is a single equality against a four-value column, which is the shape an
index is least useful for.

The decision is recorded rather than deferred silently: revisit it when a
``priority``-filtered board query shows up in slow-query logs, and add the
expression index then, with the plan that justified it.

Downgrade
---------

Drops ``user_notifications``, losing the inbox and its read state. Nothing else
goes with it - the outbox, delivery attempts and every Telegram message are
separate rows in separate tables, and no workflow record points here.

The priority backfill restores nothing, and cannot. ``LOW`` rows became ``NORMAL`` rows and the
information that they were once ``LOW`` was not recorded anywhere - there was no
audit event for a value nothing ever set. The downgrade is a no-op with a
comment saying so, rather than a statement that pretends to reverse this.

Rolling back the *code* is safe regardless: an older deployment reading a
``CRITICAL`` row would fail to coerce it, so the deploy order matters in one
direction only. See the deployment section of
``docs/pr/STEP_1F23D_NOTIFICATIONS_AND_PRIORITY.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"
TASKS = "pr_tasks"
NOTIFICATIONS = "user_notifications"

#: Literals, not an import from ``meobot.domain.pr``: a migration has to keep
#: meaning what it meant on the day it ran. A unit test asserts this tuple still
#: matches ``PrPriority``.
PRIORITIES = ("NORMAL", "HIGH", "URGENT", "CRITICAL")

#: The value 0012 created that this revision retires, and what its rows become.
RETIRED_PRIORITY = "LOW"
REPLACEMENT_PRIORITY = "NORMAL"


def _retire_low(table: str) -> None:
    """Move every ``LOW`` row in ``table`` to ``NORMAL``.

    Parameterised rather than interpolated even though both values are module
    constants: a migration is the last place to establish a habit of formatting
    SQL by hand.
    """
    op.execute(
        sa.text(f"UPDATE {table} SET priority = :replacement WHERE priority = :retired").bindparams(
            replacement=REPLACEMENT_PRIORITY, retired=RETIRED_PRIORITY
        )
    )


def upgrade() -> None:
    # Both tables, because one enum backs both columns. Doing only the content
    # one would leave `pr_tasks` holding a value `PrPriority` no longer has.
    _retire_low(CONTENT_ITEMS)
    _retire_low(TASKS)

    op.create_table(
        NOTIFICATIONS,
        sa.Column("recipient_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_notifications"),
        # Short explicit name: the generated one runs past PostgreSQL's 63-byte
        # identifier limit. Same reason as 0022's foreign keys.
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            [f"{USERS}.id"],
            name="fk_user_notifications_recipient",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_user_notifications_idempotency_key"),
    )
    # The list: one person's notifications, newest first.
    op.create_index(
        "ix_user_notifications_recipient_created",
        NOTIFICATIONS,
        ["recipient_user_id", "created_at"],
    )
    # The badge. Its own index because the unread count runs on every page load;
    # see the model's ``__table_args__`` for why it is not folded into the one
    # above.
    op.create_index(
        "ix_user_notifications_recipient_unread",
        NOTIFICATIONS,
        ["recipient_user_id", "read_at"],
    )


def downgrade() -> None:
    """Drop the inbox. The ``LOW`` backfill is not reversed - see the docstring.

    Dropping ``user_notifications`` loses read state and the notification
    history. Nothing else goes with it: the outbox, its delivery attempts and
    every Telegram message are separate rows in separate tables, and no workflow
    record points here.
    """
    op.drop_index("ix_user_notifications_recipient_unread", table_name=NOTIFICATIONS)
    op.drop_index("ix_user_notifications_recipient_created", table_name=NOTIFICATIONS)
    op.drop_table(NOTIFICATIONS)
