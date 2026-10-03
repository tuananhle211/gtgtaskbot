"""Post-M4: when the work was performed, and which firing produced it.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-04

**Two nullable columns on ``pr_work_items``, and nothing else.** No table, no
constraint on anything that already existed, no enum widened.

Why this is not derivable
--------------------------

Every timestamp M1 already carries answers a different question, and none of
them answers *"on what day was this work done, or is it scheduled to be done"*:

* ``created_at`` - when the row was written. For content-derived work that is
  when a sweeper got round to projecting it, which can be days later;
* ``due_at`` - when it must be finished. A deadline is not a performance;
* ``accepted_at`` - when it entered somebody's workload;
* ``completed_at`` - when somebody said it was finished;
* ``counted_at`` (on the contribution) - when it was independently validated.

The near-miss was ``completed_at``, which for content-derived work is written
from the canonical source milestone and *is* the right instant. It was rejected
because **``reopen`` sets it back to null**: a validator sending finished work
back would erase the day the writer delivered, and a screen that had been
showing "Thực hiện 04/09" would silently start showing nothing. A fact that a
lifecycle action can destroy is not a fact that a read model can be built on.

What writes it
---------------

Only the two paths that genuinely know the answer, and each writes an instant it
was already given rather than one it invents:

* **content-derived work** - the canonical source milestone, which M3.1 already
  computes: the head's approval decision for ``CONTENT_CREATION`` and the
  submission of the cut for ``PRODUCTION``. The projector was already passing
  it as ``occurred_at``;
* **recurring work** - the occurrence's ``scheduled_for``, which M4B already
  passes as the creation instant.

**Manual work leaves it null**, because manual work has no execution-date
concept: a person files a job with a title and a deadline, and there is no field
anywhere in which they said which day they would do it. Inventing one from
``due_at`` would put a deadline under a heading that says "performed".

``recurring_occurrence_id``
----------------------------

The second column, and it exists because the alternative was **parsing a source
key**. M4B's recurring key is ``recurring:{occurrence-uuid}:{SUBJECT}``, and the
whole module is written on the rule that a source key is *compared for equality
and nothing else* - a key somebody starts taking substrings of is a key that
will one day be taken apart wrongly.

A unified work list has to offer *"đi tới công việc định kỳ"*, which needs the
template. The occurrence ledger already links occurrence to template; the edge
that was missing is work to occurrence, and it is many-to-one - one firing in
``SEPARATE_PER_ASSIGNEE`` mode produces one item per assignee. So it belongs
here, as a plain nullable foreign key, and not as a string anybody has to decode.

``RESTRICT``, like every other PR foreign key: an occurrence is the provenance of
the work it produced.

The backfill
-------------

Unusually for this repository, this migration **does** backfill - and the reason
it is not the thing the no-backfill rule exists to prevent is that it invents no
date. Each source type is given the instant its own canonical fact already
holds, and a row whose fact cannot be found is left null rather than guessed at.

**Recurring work is joined to its occurrence.** The occurrence row is the
canonical statement of when a routine job was scheduled - ``scheduled_for`` - and
after ``0037`` a work item points at it directly. For rows generated before this
migration that pointer has to be recovered, and the only thing that carries it is
the source key: ``recurring:{occurrence-uuid}:{SUBJECT}``.

So this migration decodes it, **once**, here, and nowhere else. That is a
deliberate and bounded exception to the module's "a source key is compared and
never parsed" rule: a one-time recovery inside a migration is a different act
from runtime code that takes a key apart on every read, and the whole point of
the new column is that no runtime path ever needs to. The uuid segment is
validated against the same shape ``SOURCE_KEY_PATTERN`` requires before it is
cast, so a malformed key cannot raise and cannot match.

A recurring row whose key is malformed, or whose occurrence no longer exists,
gets **neither** column filled. That is the honest outcome: there is no
occurrence to point at and therefore no scheduled instant to report, and
falling back to ``accepted_at`` would put a plausible-looking wrong date on a row
nobody could later distinguish from a right one. The anomaly stays visible.

**Content work takes ``assigned_at``.** ``create_source_work`` writes the M3.1
milestone instant into three columns at once, and ``assigned_at`` is the one
nothing afterwards can rewrite: ``accept`` fills it only when it is null - and it
is never null on source work - while ``completed_at``, the obvious candidate, is
set to ``now()`` by ``complete`` after a ``reopen``. A content item that was sent
back and re-completed therefore carries a ``completed_at`` that is *not* the
milestone, and copying it would have written the validator's afternoon into a
column claiming the writer delivered then.

**Manual work is not touched at all.** There is no column a manual execution date
could be copied from, because a person filing work never states one.

No manual row is touched, no number is derived, and a row whose source column is
already null stays null. The alternative - shipping the column empty - would
leave every existing row without a date the system demonstrably knows.

Downgrade
----------

Drops both columns. It loses the distinction between "performed on" and the four
timestamps above, and the link from a generated job back to the firing that
produced it. Nothing else: no work item, contribution, allocation or score is
removed in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_ITEMS = "pr_work_items"
RECURRING_OCCURRENCES = "pr_work_recurring_occurrences"
RESTRICT = "RESTRICT"


def upgrade() -> None:
    op.add_column(
        WORK_ITEMS,
        sa.Column("execution_at", sa.DateTime(timezone=True), nullable=True),
    )
    # "What happened in September, oldest first" - the unified monthly work
    # list's own query, answered from the index.
    op.create_index("ix_pr_work_items_execution_at", WORK_ITEMS, ["execution_at"])

    op.add_column(WORK_ITEMS, sa.Column("recurring_occurrence_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_pr_work_items_recurring_occurrence_id_pr_work_recurring_occurrences"),
        WORK_ITEMS,
        RECURRING_OCCURRENCES,
        ["recurring_occurrence_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(
        op.f("ix_pr_work_items_recurring_occurrence_id"), WORK_ITEMS, ["recurring_occurrence_id"]
    )

    # --- content: the milestone instant, from the column nothing rewrites ---
    #
    # ``assigned_at`` rather than ``completed_at``. Both were written from
    # ``SourceMilestone.occurred_at`` at creation; only the first is safe from
    # ``reopen`` followed by ``complete``, which stamps ``now()``. See the
    # module docstring.
    op.execute(
        sa.text(
            "UPDATE pr_work_items SET execution_at = assigned_at "
            "WHERE source_type = 'CONTENT' AND assigned_at IS NOT NULL"
        )
    )

    # --- recurring: joined to the occurrence, through the key, once ---------
    #
    # ``split_part(source_key, ':', 2)`` is the uuid segment of
    # ``recurring:{occurrence-uuid}:{SUBJECT}``. The regex guard is not
    # decoration: without it a malformed key would raise on the cast and take
    # the whole migration down, and with it such a row simply fails to match and
    # is left null - which is the outcome the docstring promises.
    op.execute(
        sa.text(
            "UPDATE pr_work_items AS w "
            "SET recurring_occurrence_id = o.id, execution_at = o.scheduled_for "
            "FROM pr_work_recurring_occurrences AS o "
            "WHERE w.source_type = 'RECURRING' "
            "  AND w.source_key IS NOT NULL "
            "  AND split_part(w.source_key, ':', 2) ~* "
            "      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
            "  AND o.id = split_part(w.source_key, ':', 2)::uuid"
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pr_work_items_recurring_occurrence_id"), table_name=WORK_ITEMS)
    op.drop_constraint(
        op.f("fk_pr_work_items_recurring_occurrence_id_pr_work_recurring_occurrences"),
        WORK_ITEMS,
        type_="foreignkey",
    )
    op.drop_column(WORK_ITEMS, "recurring_occurrence_id")
    op.drop_index("ix_pr_work_items_execution_at", table_name=WORK_ITEMS)
    op.drop_column(WORK_ITEMS, "execution_at")
