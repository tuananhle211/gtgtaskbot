"""Deleting a legacy content work item on a real PostgreSQL: the races.

What only a real database answers: two administrators pressing *Xóa công
việc* on the same row at once, a delete landing while the worker projects the
same content, while M2 reconciles the month, or while somebody reads the list -
and whether a failure after the child rows are gone leaves anything behind.
Every scenario ends by reading the ledger from a fresh connection and checking
the invariants the operation protects:

* the row is gone, or it is exactly as it was - never half;
* no contribution, history, evidence or allocation row points at a work item
  that no longer exists;
* at most one modern result exists for the source key, and it was written by
  a projection somebody else ran - never by the delete;
* nothing else in the month moved.

Scenarios:

#. two admins delete one row - one wins, the other gets a deterministic
   not-found, nothing is orphaned;
#. a delete races the worker's projection of the same content - the ledger is
   consistent whichever landed first, and the delete queued nothing;
#. a delete races M2's reconcile of the month - both commit, and the month
   ends with no allocation for the gone contribution;
#. a delete races a list read - the read never fails, and shows the row or
   not;
#. a failure after the child cleanup rolls the whole delete back.

Fixtures come from the atomicity suite: the same scratch database, the same
five people, the same explicit mapping for short-video scripts.

Requires a PostgreSQL you are willing to have scratch databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_legacy_delete_pg.py -m integration
"""

from __future__ import annotations

# ruff: noqa: F811 - `db` and `people` are fixtures imported from the atomicity suite
import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_work_query_service import PrWorkScope, WorkQuery
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
)
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.content_work import (
    PrContentWorkKind,
    content_work_source_key,
    is_legacy_content_work_item,
)
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkSourceType
from meobot.domain.pr.work_results import PrWorkResultSource
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
# Helpers
# ---------------------------------------------------------------------------


async def _legacy(db: Database, people: People, content_id: uuid.UUID) -> uuid.UUID:
    """The pre-``0039`` row for one approved piece, counted, **committed**."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        now = utcnow()
        item = await services.work.create_source_work(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            source_key=content_work_source_key(KIND, content_id),
            work_type_id=people.work_type,
            title="Nội dung: dữ liệu cũ",
            contributor_user_id=people.member,
            content_id=content_id,
            occurred_at=now,
        )
        await services.work.count_source_work(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            work_item_id=item.id,
            validated_by_user_id=people.head,
            effective_validation_at=now,
        )
        assert is_legacy_content_work_item(item)
        item_id = item.id
        await session.commit()
        return item_id


async def _delete(db: Database, people: People, item_id: uuid.UUID) -> str:
    """One delete in its own transaction, committed. 'ok' or the error class."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work_maintenance.admin_delete_legacy_work_item(
                actor=people.owner_actor, request_id=uuid.uuid4(), work_item_id=item_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return type(exc).__name__


async def _project(db: Database, people: People, content_id: uuid.UUID) -> str:
    try:
        await project_once(db, people, content_id)
        return "ok"
    except Exception as exc:
        return type(exc).__name__


async def _no_orphans(db: Database, item_id: uuid.UUID) -> None:
    """Nothing points at a work item that is not there."""
    async with db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        if item is not None:
            return
        for model, column in (
            (PrWorkContribution, PrWorkContribution.work_item_id),
            (PrWorkHistory, PrWorkHistory.work_item_id),
            (PrWorkEvidence, PrWorkEvidence.work_item_id),
            (PrWorkResult, PrWorkResult.work_item_id),
        ):
            left = await session.scalar(
                select(func.count()).select_from(model).where(column == item_id)
            )
            assert left == 0, (model.__name__, left)


async def _queue_rows(db: Database, content_id: uuid.UUID) -> list[tuple[object, ...]]:
    async with db.session() as session:
        rows = (
            (
                await session.execute(
                    select(PrContentWorkProjection).where(
                        PrContentWorkProjection.content_id == content_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [(row.status, row.requested_at, row.attempts) for row in rows]


# ---------------------------------------------------------------------------
# 54: two admins delete the same row
# ---------------------------------------------------------------------------


async def test_54_two_admins_deleting_one_row_leave_one_winner_and_no_orphans(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Xóa song song")
    item_id = await _legacy(db, people, content_id)

    outcomes = sorted(
        await asyncio.gather(_delete(db, people, item_id), _delete(db, people, item_id))
    )
    assert outcomes == ["PrNotFoundError", "ok"], outcomes

    await _no_orphans(db, item_id)
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
        audited = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == AuditAction.PR_WORK_ITEM_ADMIN_DELETED.value,
                AuditLog.entity_id == str(item_id),
            )
        )
        assert audited == 1, "the winner audited once; the loser wrote nothing"
        assert await _results_for(session, content_id) == [], "the delete projected nothing"


# ---------------------------------------------------------------------------
# 55: a delete races the projection worker
# ---------------------------------------------------------------------------


async def test_55_a_delete_racing_the_worker_leaves_a_consistent_ledger(
    db: Database, people: People
) -> None:
    """Part J. Whichever landed first, the ledger is whole - and any modern
    result that exists was the worker's decision, not the delete's."""
    await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Xóa khi worker đang chiếu")
    item_id = await _legacy(db, people, content_id)
    queue_before = await _queue_rows(db, content_id)

    outcomes = await asyncio.gather(_delete(db, people, item_id), _project(db, people, content_id))
    assert outcomes[0] == "ok", outcomes
    assert outcomes[1] in {"ok", "PrNotFoundError"}, outcomes

    await _no_orphans(db, item_id)
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
        results = await _results_for(session, content_id)
        assert len(results) <= 1
        for result in results:
            container = await session.get(PrWorkItem, result.work_item_id)
            assert container is not None and container.is_period_container
    # The delete queued nothing: whatever the queue holds, it held before.
    assert await _queue_rows(db, content_id) == queue_before
    # And the worker's *next* pass - somebody else's act - records the still-
    # accepted piece as a modern result, exactly once.
    assert await _project(db, people, content_id) == "ok"
    async with db.session() as session:
        results = await _results_for(session, content_id)
        assert len(results) == 1 and results[0].status is PrWorkCountStatus.COUNTED
        assert results[0].source_type is PrWorkResultSource.CONTENT


# ---------------------------------------------------------------------------
# 56: a delete races M2's reconcile
# ---------------------------------------------------------------------------


async def test_56_a_delete_racing_an_m2_reconcile_leaves_no_allocation_behind(
    db: Database, people: People
) -> None:
    period_id = await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Xóa khi M2 tính")
    item_id = await _legacy(db, people, content_id)
    async with db.session() as session:
        contribution_ids = [
            row.id
            for row in (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                )
            ).scalars()
        ]
        assert len(contribution_ids) == 1

    async def reconcile() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work_eligibility.reconcile_period(
                    actor=people.owner_actor, request_id=uuid.uuid4(), period_id=period_id
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(_delete(db, people, item_id), reconcile())
    assert outcomes[0] == "ok", outcomes
    # The reconcile may have lost the row under it; either way nothing names it.
    await _no_orphans(db, item_id)
    async with db.session() as session:
        left = await session.scalar(
            select(func.count())
            .select_from(PrWorkQuotaAllocation)
            .where(PrWorkQuotaAllocation.work_contribution_id.in_(contribution_ids))
        )
        assert left == 0
        services = build_pr_services(session, settings())
        summary = await services.work_eligibility.summary(
            actor=people.owner_actor, user_id=people.member, period_id=period_id
        )
        gone = await session.scalar(
            select(func.count())
            .select_from(PrWorkContribution)
            .where(PrWorkContribution.id.in_(contribution_ids))
        )
        assert gone == 0
        assert summary.period_id == period_id


# ---------------------------------------------------------------------------
# 57: a delete races a list read
# ---------------------------------------------------------------------------


async def test_57_a_delete_racing_a_list_read_never_fails_the_read(
    db: Database, people: People
) -> None:
    period_id = await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Xóa khi đang xem danh sách")
    item_id = await _legacy(db, people, content_id)

    async def read() -> tuple[str, bool]:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            page = await services.work_queries.page(
                actor=people.owner_actor,
                query=WorkQuery(
                    scope=PrWorkScope.ALL,
                    period_id=period_id,
                    source_type=PrWorkSourceType.CONTENT,
                    limit=100,
                ),
            )
            return "ok", any(one.id == item_id for one in page.items)

    outcomes = await asyncio.gather(_delete(db, people, item_id), read(), read(), read())
    assert outcomes[0] == "ok"
    for status, _seen in outcomes[1:]:
        assert status == "ok"
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
    # A read after the delete no longer lists it, and still succeeds.
    assert (await read()) == ("ok", False)


# ---------------------------------------------------------------------------
# 58: a failure after the child cleanup rolls everything back
# ---------------------------------------------------------------------------


async def test_58_a_failure_after_child_cleanup_commits_nothing(
    db: Database, people: People, monkeypatch: pytest.MonkeyPatch
) -> None:
    await open_month(db, people, utcnow())
    content_id = await approved_content(db, people, title="Hỏng sau khi dọn con")
    item_id = await _legacy(db, people, content_id)

    async with db.session() as session:
        services = build_pr_services(session, settings())

        async def explode(*args: object, **kwargs: object) -> object:
            raise RuntimeError("died after the child rows were deleted")

        # The performance refresh is the last step after every delete has been
        # issued; failing there is failing after the cleanup.
        monkeypatch.setattr(services.work_maintenance, "_refresh_performance", explode)
        with pytest.raises(RuntimeError):
            await services.work_maintenance.admin_delete_legacy_work_item(
                actor=people.owner_actor, request_id=uuid.uuid4(), work_item_id=item_id
            )
        await session.rollback()

    async with db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        assert item is not None, "the delete did not survive alone"
        rows = (
            (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1 and rows[0].count_status is PrWorkCountStatus.COUNTED
        history = await session.scalar(
            select(func.count())
            .select_from(PrWorkHistory)
            .where(PrWorkHistory.work_item_id == item_id)
        )
        assert history and history >= 2
        audited = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == AuditAction.PR_WORK_ITEM_ADMIN_DELETED.value,
                AuditLog.entity_id == str(item_id),
            )
        )
        assert audited == 0
    # And the same row deletes cleanly once nothing explodes.
    assert await _delete(db, people, item_id) == "ok"
    await _no_orphans(db, item_id)
