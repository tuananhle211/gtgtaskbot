"""Validator decisions racing the projector, each other and the administrator.

``0041`` says a validator's rejection outranks the source, and an
administrator's removal does not. What only a real PostgreSQL answers is what
happens when two of the acts that write ``EXCLUDED`` - or release it - land at
once. Every scenario ends by reading the ledger from a fresh connection and
checking the invariants ``test_pr_content_work_resync_pg`` established:

* exactly one result for the source key - never two, no unique violation
  escaping as an error;
* the container's actual is the sum of its counted results, and its
  contribution agrees;
* M2 names no contribution that is gone.

And the one this task adds: **no validation decision is lost** - a rejection
that landed is still a rejection afterwards, whatever landed beside it.

Scenarios (numbered against the task's concurrency list):

1. validator accept vs validator reject;
2. reject vs projector;  3. reject vs manual sync;  4. reject vs worker;
5. reconsider vs projector;  6. reconsider vs another reject;
7. admin remove vs reject;  8. admin remove vs sync;
9. two validators reject at once;  10. two validators accept at once.

Requires a PostgreSQL you are willing to have scratch databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_result_exclusion_pg.py -m integration
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
from meobot.db.models.pr_work import PrWorkHistory
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkEventType
from meobot.domain.pr.work_results import PrWorkExclusionKind
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
from tests.integration.test_pr_content_work_resync_pg import (
    _all_ok,
    _consistent,
    _counted_result,
    _remove,
    _sync,
    _worker,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

REASON = "Không đủ minh chứng"


# ---------------------------------------------------------------------------
# Helpers - each is one committed transaction, returning 'ok' or the error class
# ---------------------------------------------------------------------------


async def _reject(db: Database, people: People, result_id: uuid.UUID, *, by: str = "lead") -> str:
    """*Từ chối / Không ghi nhận* by a validator who is not the subject."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        actor = people.lead_actor if by == "lead" else people.head_actor
        try:
            await services.work_results.exclude_result(
                actor=actor, request_id=uuid.uuid4(), result_id=result_id, reason=REASON
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _project(db: Database, people: People, content_id: uuid.UUID) -> str:
    """The projector called directly, as the batch sync and rebuild call it."""
    try:
        await project_once(db, people, content_id)
        return "ok"
    except Exception as exc:
        return _describe(exc)


def _describe(exc: BaseException) -> str:
    """The class, and for a database error the sentence - a race that fails
    has to say which constraint, not merely that one did."""
    return (
        f"{type(exc).__name__}: {str(exc)[:240]}"
        if "Error" in type(exc).__name__
        else type(exc).__name__
    )


async def _reconsider(db: Database, people: People, result_id: uuid.UUID) -> str:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work_results.reconsider_result(
                actor=people.head_actor, request_id=uuid.uuid4(), result_id=result_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _validate(db: Database, people: People, result: PrWorkResult, *, by: str) -> str:
    """*Xác nhận* for this one result. Named, because the stream is shared
    across the module and "every pending result" would count other tests'."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        actor = people.lead_actor if by == "lead" else people.head_actor
        try:
            await services.work_results.validate_results(
                actor=actor,
                request_id=uuid.uuid4(),
                work_item_id=result.work_item_id,
                result_ids=[result.id],
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _pending_manual(db: Database, people: People) -> PrWorkResult:
    """One pending manual result reported by the member into their own stream."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        result = await services.work_results.report_result(
            actor=people.member_actor,
            request_id=uuid.uuid4(),
            quantity=Decimal("3"),
            work_type_id=people.work_type,
            label="Khách tuần 2",
        )
        await session.commit()
        await session.refresh(result)
        session.expunge(result)
        return result


async def _rejected_result(db: Database, people: People, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    content_id, result_id = await _counted_result(db, people, title)
    assert await _reject(db, people, result_id) == "ok"
    async with db.session() as session:
        row = await session.get(PrWorkResult, result_id)
        assert row is not None and row.exclusion_kind is PrWorkExclusionKind.VALIDATOR_REJECTED
    return content_id, result_id


async def _row(db: Database, result_id: uuid.UUID) -> PrWorkResult:
    async with db.session() as session:
        row = await session.get(PrWorkResult, result_id)
        assert row is not None
        session.expunge(row)
        return row


async def _history_count(db: Database, result_id: uuid.UUID, event: PrWorkEventType) -> int:
    """Timeline rows about **this** result. The stream is shared across the
    module - one member, one type, one month - so the container's timeline
    accumulates and the count has to be per result."""
    async with db.session() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(PrWorkHistory)
                .where(
                    PrWorkHistory.event_type == event,
                    PrWorkHistory.event_metadata["result_id"].as_string() == str(result_id),
                )
            )
            or 0
        )


async def _actual(db: Database, work_item_id: uuid.UUID) -> Decimal:
    from meobot.db.models.pr_work import PrWorkItem

    async with db.session() as session:
        item = await session.get(PrWorkItem, work_item_id)
        assert item is not None
        return Decimal(item.quantity or 0)


def _rejected(row: PrWorkResult) -> None:
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.VALIDATOR_REJECTED
    assert row.excluded_reason == REASON
    assert row.counted_at is None


# ---------------------------------------------------------------------------
# 1: accept vs reject on one pending result
# ---------------------------------------------------------------------------


async def test_01_accept_and_reject_at_once_end_rejected_and_uncounted(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    result = await _pending_manual(db, people)
    before = await _actual(db, result.work_item_id)
    outcomes = await asyncio.gather(
        _validate(db, people, result, by="lead"),
        _reject(db, people, result.id, by="head"),
    )
    _all_ok(list(outcomes))
    # Either order ends here: counted-then-rejected is the correction flow,
    # rejected-then-counted counts nothing because nothing is pending.
    _rejected(await _row(db, result.id))
    assert await _actual(db, result.work_item_id) == before, "nothing of it counted"


# ---------------------------------------------------------------------------
# 2-4: a rejection against every projection path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("other", ["projector", "sync", "worker"])
async def test_02_04_a_rejection_racing_a_projection_is_never_lost(
    db: Database, people: People, other: str
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _counted_result(db, people, f"Từ chối đua {other}")
    race = (
        _project(db, people, content_id)
        if other == "projector"
        else _sync(db, people, content_id)
        if other == "sync"
        else _worker(db, people, content_id)
    )
    _all_ok(await asyncio.gather(_reject(db, people, result_id), race))
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    _rejected(row)
    # And the next projection, whichever path, holds it.
    assert await _sync(db, people, content_id) == "ok"
    assert await _worker(db, people, content_id) == "ok"
    _rejected(await _row(db, result_id))


# ---------------------------------------------------------------------------
# 5-6: a reconsideration against the projector, and against another rejection
# ---------------------------------------------------------------------------


async def test_05_reconsider_racing_the_projector_converges_on_counted(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _rejected_result(db, people, "Xem xét lại đua projector")
    _all_ok(
        await asyncio.gather(_reconsider(db, people, result_id), _worker(db, people, content_id))
    )
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    # Reconsider-then-project counts it (the source is independently
    # validated); project-then-reconsider holds, then releases to pending.
    assert row.status in {PrWorkCountStatus.PENDING, PrWorkCountStatus.COUNTED}
    assert row.exclusion_kind is None
    # Whichever it was, the source's next word settles it the same way.
    assert await _sync(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.status is PrWorkCountStatus.COUNTED
    assert await _history_count(db, row.id, PrWorkEventType.RESULT_RECONSIDERED) == 1
    assert await _history_count(db, row.id, PrWorkEventType.RESULT_REJECTED) == 1


async def test_06_reconsider_racing_another_rejection_keeps_one_consistent_decision(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _rejected_result(db, people, "Xem xét lại đua từ chối")
    _all_ok(
        await asyncio.gather(
            _reconsider(db, people, result_id), _reject(db, people, result_id, by="head")
        )
    )
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    reconsidered = await _history_count(db, row.id, PrWorkEventType.RESULT_RECONSIDERED)
    rejected = await _history_count(db, row.id, PrWorkEventType.RESULT_REJECTED)
    assert reconsidered == 1
    if row.status is PrWorkCountStatus.PENDING:
        # The rejection landed first on an already-rejected row: a no-op,
        # then the release. One rejection in the story, and it is the first.
        assert rejected == 1 and row.exclusion_kind is None
    else:
        # The release landed first, then the head rejected the pending row
        # again: the second rejection is a second event, and it stands.
        _rejected(row)
        assert rejected == 2


# ---------------------------------------------------------------------------
# 7-8: the administrator against a rejection, and against a sync
# ---------------------------------------------------------------------------


async def test_07_admin_remove_racing_a_rejection_never_turns_it_resyncable(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _counted_result(db, people, "Gỡ đua từ chối")
    outcomes = await asyncio.gather(_remove(db, people, result_id), _reject(db, people, result_id))
    assert outcomes[1] == "ok", outcomes
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    assert row.status is PrWorkCountStatus.EXCLUDED
    if outcomes[0] == "ok":
        # The removal landed first; the rejection found an excluded row and
        # left it. The administrator's removal is what stands - and it is
        # honest about being resyncable.
        assert row.exclusion_kind is PrWorkExclusionKind.ADMIN_REMOVED
    else:
        # The rejection landed first; the removal was refused, structurally.
        assert outcomes[0] == "PrConflictError", outcomes
        _rejected(row)
        assert await _sync(db, people, content_id) == "ok"
        _rejected(await _row(db, result_id))


async def test_08_admin_remove_racing_a_sync_is_resyncable_afterwards(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _counted_result(db, people, "Gỡ đua đồng bộ")
    _all_ok(await asyncio.gather(_remove(db, people, result_id), _sync(db, people, content_id)))
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    assert row.exclusion_kind in {None, PrWorkExclusionKind.ADMIN_REMOVED}
    assert row.status is (
        PrWorkCountStatus.EXCLUDED if row.exclusion_kind is not None else PrWorkCountStatus.COUNTED
    )
    # Never a validator's decision, so the next sync restores it.
    assert await _sync(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.status is PrWorkCountStatus.COUNTED
    assert row.exclusion_kind is None


# ---------------------------------------------------------------------------
# 9-10: two validators at once
# ---------------------------------------------------------------------------


async def test_09_two_validators_rejecting_at_once_write_one_rejection(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    result = await _pending_manual(db, people)
    before = await _actual(db, result.work_item_id)
    _all_ok(
        await asyncio.gather(
            _reject(db, people, result.id, by="lead"), _reject(db, people, result.id, by="head")
        )
    )
    row = await _row(db, result.id)
    _rejected(row)
    assert row.excluded_by_user_id in {people.lead, people.head}
    assert await _history_count(db, result.id, PrWorkEventType.RESULT_REJECTED) == 1
    assert await _actual(db, result.work_item_id) == before


async def test_10_two_validators_accepting_at_once_count_it_once(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    result = await _pending_manual(db, people)
    before = await _actual(db, result.work_item_id)
    _all_ok(
        await asyncio.gather(
            _validate(db, people, result, by="lead"),
            _validate(db, people, result, by="head"),
        )
    )
    row = await _row(db, result.id)
    assert row.status is PrWorkCountStatus.COUNTED
    assert row.counted_by_user_id in {people.lead, people.head}
    assert await _history_count(db, result.id, PrWorkEventType.RESULT_COUNTED) == 1
    assert await _actual(db, result.work_item_id) == before + Decimal("3.00"), "counted once"


# ---------------------------------------------------------------------------
# 11-19: SOURCE TRUTH WINS - withdrawal against the validator and the projector
# ---------------------------------------------------------------------------


async def _withdraw(db: Database, people: People, content_id: uuid.UUID) -> str:
    """The head approval undone, committed. The milestone is not one any more."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.undo.undo_last(
                actor=people.head_actor, request_id=uuid.uuid4(), content_id=content_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _reapprove(
    db: Database, people: People, content_id: uuid.UUID, *, by: uuid.UUID | None = None
) -> None:
    """Approved again at the head gate - by the head, or by ``by`` (the writer
    themselves, for the self-approved case that must come back *pending*)."""
    from meobot.domain.pr.models import PrApprovalStage
    from tests.integration.test_pr_content_work_atomicity import _decide

    async with db.session() as session:
        services = build_pr_services(session, settings())
        await _decide(
            services,
            actor=people.actor(by) if by is not None else people.head_actor,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
        await session.commit()


async def _pending_content(db: Database, people: People, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Writer and head are one person: projected, and pending an independent validator."""
    content_id = await approved_content(
        db, people, title=title, head=people.member, writer_id=people.member
    )
    await project_once(db, people, content_id)
    async with db.session() as session:
        rows = await _results_for(session, content_id)
        assert len(rows) == 1 and rows[0].status is PrWorkCountStatus.PENDING
        return content_id, rows[0].id


def _source_reversed(row: PrWorkResult) -> None:
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    assert row.counted_at is None


async def test_11_a_pending_row_is_swept_when_its_source_is_withdrawn(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _pending_content(db, people, "Rút duyệt rồi quét")
    assert await _withdraw(db, people, content_id) == "ok"
    assert await _project(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    _source_reversed(row)
    # 18-19. the source returns. Self-approved again: canonical rules put it
    # back to *pending*, not counted; a validator counts it from there.
    await _reapprove(db, people, content_id, by=people.member)
    assert await _sync(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.status is PrWorkCountStatus.PENDING
    assert row.exclusion_kind is None
    assert await _validate(db, people, row, by="head") == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.status is PrWorkCountStatus.COUNTED


async def test_12_a_confirm_before_the_projector_is_refused(db: Database, people: People) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _pending_content(db, people, "Xác nhận trước projector")
    assert await _withdraw(db, people, content_id) == "ok"
    outcome = await _validate(db, people, await _row(db, result_id), by="head")
    assert outcome.startswith("PrConflictError"), outcome
    row = await _row(db, result_id)
    assert row.status is PrWorkCountStatus.PENDING and row.counted_at is None, "never counted"
    assert await _sync(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None
    _source_reversed(row)


async def test_13_a_withdrawal_racing_a_confirm_never_ends_counted(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _pending_content(db, people, "Rút duyệt đua xác nhận")
    outcomes = await asyncio.gather(
        _withdraw(db, people, content_id),
        _validate(db, people, await _row(db, result_id), by="head"),
    )
    assert outcomes[0] == "ok", outcomes
    assert outcomes[1] == "ok" or outcomes[1].startswith("PrConflictError"), outcomes
    # Whichever landed first, the ledger agrees with itself, and the next
    # projection - which the withdrawal queued - takes the row out.
    await _consistent(db, content_id)
    assert await _sync(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.id == result_id
    _source_reversed(row)
    # And it stays out on every later pass.
    assert await _worker(db, people, content_id) == "ok"
    _source_reversed(await _row(db, result_id))


async def test_14_a_withdrawal_racing_a_reject_never_counts_anything(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _pending_content(db, people, "Rút duyệt đua từ chối")
    _all_ok(await asyncio.gather(_withdraw(db, people, content_id), _reject(db, people, result_id)))
    row = await _consistent(db, content_id)
    assert row is not None and row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.VALIDATOR_REJECTED
    assert await _sync(db, people, content_id) == "ok"
    row = await _row(db, result_id)
    assert row.status is PrWorkCountStatus.EXCLUDED and row.counted_at is None


async def test_15_17_a_rejection_is_not_released_while_the_source_is_withdrawn(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _rejected_result(db, people, "Từ chối rồi rút duyệt")
    assert await _withdraw(db, people, content_id) == "ok"
    outcome = await _reconsider(db, people, result_id)
    assert outcome.startswith("PrConflictError"), outcome
    _rejected(await _row(db, result_id))
    # Nothing - not the projector, not a sync - can count it from here.
    assert await _sync(db, people, content_id) == "ok"
    _rejected(await _row(db, result_id))
    # 18-19. the source returns: the release works and the source counts it.
    await _reapprove(db, people, content_id)
    assert await _reconsider(db, people, result_id) == "ok"
    assert await _sync(db, people, content_id) == "ok"
    row = await _consistent(db, content_id)
    assert row is not None and row.status is PrWorkCountStatus.COUNTED
