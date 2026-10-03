"""Handing out ``CH-0001`` and ``CNT-2026-000001``, safely.

One method per namespace, one statement per allocation, and no way for a caller
to touch a counter directly - :class:`PrCodeService` returns a rendered code and
nothing else. That is the point: if ``next_value`` were reachable, "just read it
and add one" would eventually be written somewhere.

The allocation
--------------

On PostgreSQL, one statement::

    INSERT INTO pr_code_counters (id, namespace, year, next_value)
    VALUES (:id, :namespace, :year, 2)
    ON CONFLICT (namespace, year) WHERE year IS NOT NULL
    DO UPDATE SET next_value = pr_code_counters.next_value + 1,
                  updated_at = now()
    RETURNING next_value - 1

The insert path returns 1; the conflict path returns whatever it incremented
past. Read and write are the same statement, so there is no window for a second
transaction to read the same number - the loser blocks on the row lock
PostgreSQL holds for the ``DO UPDATE`` and then sees the new value.

``ON CONFLICT`` names a *partial* index, and which one depends on the
namespace, because ``year`` is nullable and PostgreSQL treats two ``NULL``s as
distinct. See :mod:`meobot.db.models.pr_code_counter` for why that asymmetry
exists rather than a single unique constraint.

The offline path
----------------

On any other dialect - which in practice means the in-memory SQLite the
behavioural tests run on - the same work is done as a locked read followed by a
write, exactly as :func:`~meobot.application.pr_support.lock_row` degrades.
SQLite serialises writers, so it is correct there; it is *not* the production
path and is never claimed to be. Concurrency is proved against PostgreSQL in
``tests/integration/test_pr_code_allocation.py``.

Which year
----------

The calendar year in the **application timezone**, from
:attr:`~meobot.core.config.Settings.timezone`, applied to an explicit business
instant supplied by the caller. Not the database server's clock and not bare
UTC: a publication at 02:00 on 1 January in Ho Chi Minh City is 19:00 on 31
December in UTC, and filing it as ``PUB-2025-…`` would put it in the wrong
year of the wrong report. The repository already stores UTC and converts at
boundaries; a business code is such a boundary.

Gaps
----

**There are none in normal operation.** The counter is a table row rather than
a PostgreSQL sequence, so the increment rolls back with the transaction that
took it and a failed command hands its number back. The cost is that the
counter row stays locked until the command commits, so two creations in one
namespace serialise on it. See :mod:`meobot.db.models.pr_code_counter` for the
full trade.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_support import supports_row_locks
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import to_local
from meobot.db.models.pr_code_counter import GLOBAL_COUNTER, YEARLY_COUNTER, PrCodeCounter
from meobot.domain.pr.codes import PrCodeNamespace, format_code, is_year_scoped

logger = get_logger(__name__)


class PrCodeService:
    """Allocates the next human-readable code in each PR namespace.

    Args:
        session: Unit of work. **The same session as the entity being
            created** - the number and the row it names commit together, and a
            separate session would let one exist without the other.
        settings: Supplies the application timezone the calendar year is taken
            in.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- One method per namespace ----------------------------------------
    async def allocate_channel_code(self) -> str:
        """``CH-0001``. Not year-scoped: channel seven is the seventh ever."""
        return await self._allocate(PrCodeNamespace.CHANNEL, at=None)

    async def allocate_content_code(self, *, at: datetime) -> str:
        """``CNT-2026-000001``, in the year ``at`` falls in locally."""
        return await self._allocate(PrCodeNamespace.CONTENT, at=at)

    async def allocate_task_code(self, *, at: datetime) -> str:
        """``TSK-2026-000001``."""
        return await self._allocate(PrCodeNamespace.TASK, at=at)

    async def allocate_work_code(self, *, at: datetime) -> str:
        """``WRK-2026-000001``. M1's Work Ledger.

        Year-scoped, like content and tasks: a department that reports monthly
        and yearly wants "the four hundredth job of 2026" rather than a number
        that has been running since the system was installed.
        """
        return await self._allocate(PrCodeNamespace.WORK, at=at)

    async def allocate_publication_code(self, *, at: datetime) -> str:
        """``PUB-2026-000001``.

        ``at`` is normally the publication's own ``published_at`` - the
        explicit business instant - rather than now, so a post recorded in
        January that went out in December is filed under the year it went out.
        """
        return await self._allocate(PrCodeNamespace.PUBLICATION, at=at)

    async def allocate_issue_code(self, *, at: datetime) -> str:
        """``ISS-2026-000001``.

        No issue-management service exists yet, so nothing in this repository
        calls this in a business command. It is here because the allocator is
        the part that has to be right, and adding it now costs one row in a
        table while inventing an issue workflow to justify it would have been
        the scope creep this step was told to avoid.
        """
        return await self._allocate(PrCodeNamespace.ISSUE, at=at)

    def business_year(self, at: datetime) -> int:
        """The calendar year ``at`` falls in, in the application timezone."""
        return to_local(at, self._settings.timezone).year

    # --- Internals --------------------------------------------------------
    async def _allocate(self, namespace: PrCodeNamespace, *, at: datetime | None) -> str:
        year: int | None = None
        if is_year_scoped(namespace):
            if at is None:
                raise ValueError(f"{namespace.value} codes need a business instant")
            year = self.business_year(at)

        value = await self._next_value(namespace, year)
        code = format_code(namespace, value, year=year)
        logger.info(
            "pr_code_allocated",
            extra={"pr_code_namespace": namespace.value, "pr_code": code},
        )
        return code

    async def _next_value(self, namespace: PrCodeNamespace, year: int | None) -> int:
        if supports_row_locks(self._session):
            return await self._next_value_upsert(namespace, year)
        return await self._next_value_locked(namespace, year)

    async def _next_value_upsert(self, namespace: PrCodeNamespace, year: int | None) -> int:
        """The production path: one atomic statement, no read-then-write gap."""
        statement = pg_insert(PrCodeCounter).values(
            id=uuid.uuid4(),
            namespace=namespace.value,
            year=year,
            next_value=2,
        )
        # Which partial unique index this conflicts against depends on whether
        # the namespace is year-scoped - the two cover disjoint halves of the
        # table and neither would match the other's rows.
        if year is None:
            statement = statement.on_conflict_do_update(
                index_elements=[PrCodeCounter.namespace],
                index_where=GLOBAL_COUNTER,
                set_={"next_value": PrCodeCounter.next_value + 1},
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[PrCodeCounter.namespace, PrCodeCounter.year],
                index_where=YEARLY_COUNTER,
                set_={"next_value": PrCodeCounter.next_value + 1},
            )
        result = await self._session.execute(statement.returning(PrCodeCounter.next_value))
        return int(result.scalar_one()) - 1

    async def _next_value_locked(self, namespace: PrCodeNamespace, year: int | None) -> int:
        """The offline path. Correct on a single-writer database, and no more.

        Kept deliberately simple: SQLite has no ``ON CONFLICT`` target for a
        partial index expressed through SQLAlchemy's PostgreSQL dialect, and
        the offline suite exists to check *formatting and wiring*, not
        concurrency. Anything this path got clever about would be untested
        cleverness.
        """
        result = await self._session.execute(self._counter_query(namespace, year))
        row = result.scalars().one_or_none()
        if row is None:
            self._session.add(PrCodeCounter(namespace=namespace.value, year=year, next_value=2))
            await self._session.flush()
            return 1
        value = int(row.next_value)
        await self._session.execute(
            update(PrCodeCounter).where(PrCodeCounter.id == row.id).values(next_value=value + 1)
        )
        await self._session.flush()
        return value

    @staticmethod
    def _counter_query(
        namespace: PrCodeNamespace, year: int | None
    ) -> Select[tuple[PrCodeCounter]]:
        statement = select(PrCodeCounter).where(PrCodeCounter.namespace == namespace.value)
        if year is None:
            return statement.where(PrCodeCounter.year.is_(None))
        return statement.where(PrCodeCounter.year == year)


__all__: list[str] = ["PrCodeService"]
