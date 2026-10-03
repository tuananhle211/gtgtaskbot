"""Delete, resync and the worker racing each other on a real PostgreSQL.

The final semantics say four operations stay separate and converge on one
ledger: the worker's projection, *Đồng bộ lại từ Nội dung*, the legacy delete
and the administrative removal of a modern result. What only a real database
answers is what happens when two of them land at once. Every scenario ends by
reading the ledger from a fresh connection and checking:

* exactly one result for the source key - never two, never a unique violation
  escaping as an error;
* the container's actual is the sum of its counted results;
* the container's contribution agrees with that sum;
* M2 names no contribution that is gone.

Scenarios (numbered against the task):

57. two manual syncs at once;
58. a manual sync and the worker;
59. an admin removal and the worker;
60. an admin removal and a manual sync;
61. an excluded result restored by two projectors at once;
62. a legacy delete and the worker.

Requires a PostgreSQL you are willing to have scratch databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_content_work_resync_pg.py -m integration
"""

from __future__ import annotations

# ruff: noqa: F811 - `db` and `people` are fixtures imported from the atomicity suite
import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_services import build_pr_services
from meobot.core.time import utcnow
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.pr.content_work import PrContentWorkKind
from meobot.domain.pr.work import PrWorkCountStatus
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL
from tests.integration.test_pr_content_work_atomicity import (  # noqa: F401 - fixtures
    People,
    _results_for,
    approved_content,
    db,
    open_month,
    people,
    project_once,
    settings,
)
from tests.integration.test_pr_work_legacy_delete_pg import _delete as _delete_legacy
from tests.integration.test_pr_work_legacy_delete_pg import _legacy

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

KIND = PrContentWorkKind.CONTENT_CREATION


# ---------------------------------------------------------------------------
# Helpers - each is one committed transaction, returning 'ok' or the error class
# ---------------------------------------------------------------------------


async def _sync(db: Database, people: People, content_id: uuid.UUID) -> str:
    """*Đồng bộ lại từ Nội dung*: the reconcile path the route uses."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.content_work.reconcile(
                actor=people.owner_actor, request_id=uuid.uuid4(), content_ids=[content_id]
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return type(exc).__name__


async def _worker(db: Database, people: People, content_id: uuid.UUID) -> str:
    try:
        await project_once(db, people, content_id)
        return "ok"
    except Exception as exc:
        return type(exc).__name__


async def _remove(db: Database, people: People, result_id: uuid.UUID) -> str:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work_maintenance.admin_remove_result(
                actor=people.owner_actor, request_id=uuid.uuid4(), result_id=result_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return type(exc).__name__


async def _counted_result(db: Database, people: People, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    content_id = await approved_content(db, people, title=title)
    await project_once(db, people, content_id)
    async with db.session() as session:
        rows = await _results_for(session, content_id)
        assert len(rows) == 1 and rows[0].status is PrWorkCountStatus.COUNTED
        return content_id, rows[0].id


async def _removed_result(db: Database, people: People, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    content_id, result_id = await _counted_result(db, people, title)
    assert await _remove(db, people, result_id) == "ok"
    async with db.session() as session:
        assert (await session.get(PrWorkResult, result_id)).status is PrWorkCountStatus.EXCLUDED  # type: ignore[union-attr]
    return content_id, result_id


async def _consistent(db: Database, content_id: uuid.UUID) -> PrWorkResult | None:
    """The invariants, read from a fresh connection. Returns the one result, if any."""
    async with db.session() as session:
        rows = await _results_for(session, content_id)
        assert len(rows) <= 1, [str(one.id) for one in rows]
        if not rows:
            return None
        result = rows[0]
        item = await session.get(PrWorkItem, result.work_item_id)
        assert item is not None and item.is_period_container
        counted = await session.scalar(
            select(func.coalesce(func.sum(PrWorkResult.quantity), 0)).where(
                PrWorkResult.work_item_id == item.id,
                PrWorkResult.status == PrWorkCountStatus.COUNTED,
            )
        )
        assert item.quantity == counted, "the actual is the sum of counted results"
        contributions = (
            (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(contributions) == 1
        assert contributions[0].count_status is (
            PrWorkCountStatus.COUNTED if counted > 0 else PrWorkCountStatus.PENDING
        )
        # M2 names no contribution that is not there.
        orphaned = await session.scalar(
            select(func.count())
            .select_from(PrWorkQuotaAllocation)
            .outerjoin(
                PrWorkContribution,
                PrWorkContribution.id == PrWorkQuotaAllocation.work_contribution_id,
            )
            .where(PrWorkContribution.id.is_(None))
        )
        assert orphaned == 0
        return result


def _all_ok(outcomes: list[str]) -> None:
    assert all(one == "ok" for one in outcomes), outcomes


# ---------------------------------------------------------------------------
# 57-58: manual syncs, and a manual sync against the worker
# ---------------------------------------------------------------------------


async def test_57_two_manual_syncs_at_once_produce_one_counted_result(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Hai lần đồng bộ")
    _all_ok(await asyncio.gather(_sync(db, people, content_id), _sync(db, people, content_id)))
    result = await _consistent(db, content_id)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED


async def test_58_a_manual_sync_racing_the_worker_produces_one_counted_result(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Đồng bộ đua worker")
    _all_ok(await asyncio.gather(_sync(db, people, content_id), _worker(db, people, content_id)))
    result = await _consistent(db, content_id)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED


# ---------------------------------------------------------------------------
# 59-60: an admin removal against the worker, and against a manual sync
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("other", ["worker", "sync"])
async def test_59_60_an_admin_removal_racing_a_projection_stays_consistent(
    db: Database, people: People, other: str
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _counted_result(db, people, f"Gỡ đua {other}")
    race = _worker(db, people, content_id) if other == "worker" else _sync(db, people, content_id)
    _all_ok(await asyncio.gather(_remove(db, people, result_id), race))
    result = await _consistent(db, content_id)
    assert result is not None and result.id == result_id
    # Whichever landed last decided the status; the ledger agrees with itself
    # either way, and the next projection - the source is still accepted -
    # counts it again.
    assert await _sync(db, people, content_id) == "ok"
    result = await _consistent(db, content_id)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED


# ---------------------------------------------------------------------------
# 61: an excluded result restored by two projectors at once
# ---------------------------------------------------------------------------


async def test_61_two_projectors_restoring_one_excluded_result_count_it_once(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _removed_result(db, people, "Khôi phục đua")
    _all_ok(
        await asyncio.gather(
            _sync(db, people, content_id),
            _worker(db, people, content_id),
            _sync(db, people, content_id),
        )
    )
    result = await _consistent(db, content_id)
    assert result is not None and result.id == result_id
    assert result.status is PrWorkCountStatus.COUNTED
    # One row, one unit: three projectors restored the same result, and
    # ``_consistent`` has already shown the container's actual is the sum of
    # its counted rows - so a double count would have to be a second row, and
    # there is none.
    assert result.quantity == Decimal("1.00"), "counted once"


# ---------------------------------------------------------------------------
# 62: a legacy delete against the worker
# ---------------------------------------------------------------------------


async def test_62_a_legacy_delete_racing_the_worker_leaves_one_grain_at_most(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Xóa cũ đua worker")
    item_id = await _legacy(db, people, content_id)
    outcomes = await asyncio.gather(
        _delete_legacy(db, people, item_id), _worker(db, people, content_id)
    )
    assert outcomes[0] == "ok" and outcomes[1] in {"ok", "PrNotFoundError"}, outcomes
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
        left = await session.scalar(
            select(func.count())
            .select_from(PrWorkContribution)
            .where(PrWorkContribution.work_item_id == item_id)
        )
        assert left == 0
    await _consistent(db, content_id)
    # The delete requested nothing; the worker's next pass - or the
    # administrator's own sync - records the still-accepted piece once.
    assert await _sync(db, people, content_id) == "ok"
    result = await _consistent(db, content_id)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED
