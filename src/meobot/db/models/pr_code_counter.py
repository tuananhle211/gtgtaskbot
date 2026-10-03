"""The counters behind ``CH-0001`` and ``CNT-2026-000001``.

Step 1C.1. One table, ``pr_code_counters``, and one rule: **the number is
allocated by the database, in one statement, or not at all.**

Why not the obvious thing
-------------------------

``MAX(code) + 1`` and ``COUNT(*) + 1`` are both wrong under concurrency and
wrong in the same way: they read, then write, with a gap in between where
another transaction does the same. Two people creating content in the same
second get the same number, and one of them loses to the unique index - or,
worse, both succeed because somebody removed the index to "fix the errors".
Step 1C deferred code generation rather than ship that. This is the fix.

A timestamp is not a substitute either - two creations in the same second
collide - and a random token is not a *human-readable numbering scheme*, which
is the whole point of a code somebody says out loud in a meeting.

How a number is taken
---------------------

One statement:

``INSERT … VALUES (namespace, year, 2) ON CONFLICT … DO UPDATE SET next_value =
pr_code_counters.next_value + 1 RETURNING next_value - 1``

The insert path yields 1 (the row starts life already pointing at the *next*
number). The conflict path increments and returns what it incremented past.
Either way the read and the write are the same statement, so there is no gap
for a second transaction to slip into: PostgreSQL takes a row lock for the
duration of the ``DO UPDATE`` and the loser waits.

Two unique indexes, not one
---------------------------

``year`` is nullable, and in PostgreSQL two ``NULL``s are *distinct* - so a
plain ``UNIQUE (namespace, year)`` would not prevent a second ``('CHANNEL',
NULL)`` row, and ``ON CONFLICT`` would never match it. The counter would
silently restart at 1 for every channel ever created.

So there are two partial unique indexes covering disjoint halves of the table:
one on ``(namespace, year)`` for the yearly namespaces and one on
``(namespace)`` for the year-less ones. Each allocation names the index that
applies to it. This is the whole reason the design does not look symmetrical.

Gaps, and why there are none
----------------------------

This is a **table row**, not a PostgreSQL sequence, and the difference decides
the question. ``nextval()`` is deliberately non-transactional - it does not
roll back, which is what makes sequences fast and gappy. An ``UPDATE`` of a row
is transactional like any other write, so a business command that takes a
number and then fails gives it back: the next creation reuses it.

**So codes are gap-free in normal operation**, which is the nicer answer for a
number people read out in meetings, and it is a consequence of the design
rather than an extra mechanism.

What it costs is the honest trade: PostgreSQL holds the row lock taken by the
``DO UPDATE`` until the transaction commits, so two creations in the same
namespace serialise on this row for as long as the *slower* of the two business
commands takes. PR creation commands are short - a handful of inserts - so that
is a queue of milliseconds, not an outage. If a future command grows long
enough for the wait to matter, the fix is to shorten the command, and only then
to trade gap-freedom for a sequence.

A gap can still appear if somebody edits ``next_value`` by hand, and nothing
here prevents that. Nothing here depends on the absence of gaps either: a code
is a label, never a key, and no query infers anything from its digits.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin
from meobot.db.models.pr import _not_empty

#: The half of the table keyed by year - content, tasks, publications, issues.
YEARLY_COUNTER = text("year IS NOT NULL")

#: The half keyed by namespace alone. Channels, and nothing else so far.
GLOBAL_COUNTER = text("year IS NULL")


class PrCodeCounter(Base, UUIDPrimaryKeyMixin):
    """The next number to hand out for one namespace, optionally per year.

    ``next_value`` is the number the *next* allocation will return, so a row
    created by the first allocation stores ``2`` and that allocation returned
    ``1``. Storing "the next one" rather than "the last one" is what lets the
    insert and the update paths of a single upsert both be correct without a
    branch: ``RETURNING next_value - 1`` is the number in both cases.

    No ``updated_at`` column is declared through ``TimestampMixin`` because
    this table has no ``created_at`` to pair it with; ``updated_at`` is
    maintained on its own, and it is genuinely useful here - it is the only
    cheap way to see when a namespace last moved.
    """

    __tablename__ = "pr_code_counters"
    __table_args__ = (
        CheckConstraint(_not_empty("namespace"), name="namespace_not_empty"),
        # A counter that has handed out nothing yet still points at 1.
        CheckConstraint("next_value >= 1", name="next_value_positive"),
        CheckConstraint("year IS NULL OR year >= 2000", name="year_plausible"),
        Index(
            "uq_pr_code_counters_namespace_year",
            "namespace",
            "year",
            unique=True,
            postgresql_where=YEARLY_COUNTER,
            sqlite_where=YEARLY_COUNTER,
        ),
        Index(
            "uq_pr_code_counters_namespace_global",
            "namespace",
            unique=True,
            postgresql_where=GLOBAL_COUNTER,
            sqlite_where=GLOBAL_COUNTER,
        ),
    )

    #: ``CHANNEL``, ``CONTENT``, ``TASK``, ``PUBLICATION``, ``ISSUE``. The
    #: vocabulary lives in :class:`~meobot.domain.pr.codes.PrCodeNamespace`;
    #: the column stores the value so a stray ``psql`` read is legible.
    namespace: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Calendar year in the application timezone, or ``NULL`` for a namespace
    #: that does not reset annually.
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The number the next allocation returns. Starts at 1, never decreases.
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__: list[str] = ["GLOBAL_COUNTER", "YEARLY_COUNTER", "PrCodeCounter"]
