"""Deleting a terminal (cancelled or rejected) work item on a real PostgreSQL: the races.

What only a real database answers: two administrators pressing *Xóa công
việc* on the same cancelled row at once; a delete landing while somebody
reads the list, attaches evidence, reports a result, reconciles M2 or changes
the row's status; and whether a failure after the child rows are gone leaves
anything behind. Every scenario ends by reading the ledger from a fresh
connection and checking the invariants the operation protects:

* the row is gone, or it is exactly as it was - never half;
* no contribution, history, evidence or result row points at a work item
  that no longer exists;
* the winner audited exactly once and the loser wrote nothing;
* nothing else in the month moved - no result, no counted contribution.

Scenarios, numbered against the task:

41. two admins delete one row - one wins, the other gets a deterministic
    not-found, nothing is orphaned;
42. a delete races a result report on the same person's month - both land
    or the report lands, and the result lives in the container, never the
    deleted row;
43. a delete races an evidence write on the cancelled row - the evidence is
    refused, or it was written and went with the row; never orphaned;
44. a delete races a list read - the read never fails, and shows the row or
    not;
45. a failure after the child cleanup rolls the whole delete back;
46. a delete races a status mutation - the mutation finds a locked row, and
    ends with either a refusal or a not-found, never a half state;
47. a delete races M2's reconcile of the month - both commit; the row's
    contributions were never counted, so no allocation ever named them.

Requires a PostgreSQL you are willing to have scratch databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_cancelled_delete_pg.py -m integration
"""

from __future__ import annotations

# ruff: noqa: F811 - `db` and `people` are fixtures imported from the atomicity suite
import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_work_query_service import PrWorkScope, WorkQuery
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
)
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkStatus
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL
from tests.integration.test_pr_content_work_atomicity import (  # noqa: F401 - fixtures
    People,
    db,
    open_month,
    people,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _cancelled(db: Database, people: People, title: str) -> uuid.UUID:
    """A manual job the owner assigned and then cancelled, **committed**."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        item = await services.work.assign_work(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                title=title,
                work_type_id=people.work_type,
                contributor_user_ids=(people.member,),
                quantity=Decimal("1"),
            ),
        )
        await services.work.add_evidence_text(
            actor=people.member_actor,
            request_id=uuid.uuid4(),
            work_item_id=item.id,
            text="Link: https://example.com/evidence",
        )
        await services.work.cancel(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            work_item_id=item.id,
            reason="Khách hủy lịch",
        )
        item_id = item.id
        await session.commit()
        return item_id


async def _rejected(db: Database, people: People, title: str) -> uuid.UUID:
    """A proposal the member filed and the owner rejected, **committed**."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        item = await services.work.propose_work(
            actor=people.member_actor,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                title=title,
                work_type_id=people.work_type,
                contributor_user_ids=(people.member,),
                quantity=Decimal("1"),
            ),
        )
        await services.work.add_evidence_text(
            actor=people.member_actor,
            request_id=uuid.uuid4(),
            work_item_id=item.id,
            text="Link: https://example.com/evidence",
        )
        await services.work.reject(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            work_item_id=item.id,
            reason="Không phù hợp",
        )
        item_id = item.id
        await session.commit()
        return item_id


MAKERS = {"cancelled": _cancelled, "rejected": _rejected}


async def _delete(db: Database, people: People, item_id: uuid.UUID) -> str:
    """One delete in its own transaction, committed. 'ok' or the error class."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work_maintenance.admin_delete_terminal_work_item(
                actor=people.owner_actor, request_id=uuid.uuid4(), work_item_id=item_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
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


async def _whole(db: Database, item_id: uuid.UUID) -> dict[str, int]:
    """The row's own children, counted, for a before/after comparison."""
    async with db.session() as session:
        out: dict[str, int] = {}
        for name, model, column in (
            ("contributions", PrWorkContribution, PrWorkContribution.work_item_id),
            ("history", PrWorkHistory, PrWorkHistory.work_item_id),
            ("evidence", PrWorkEvidence, PrWorkEvidence.work_item_id),
        ):
            out[name] = int(
                await session.scalar(
                    select(func.count()).select_from(model).where(column == item_id)
                )
                or 0
            )
        return out


async def _audited(db: Database, item_id: uuid.UUID) -> int:
    async with db.session() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == AuditAction.PR_WORK_ITEM_ADMIN_DELETED.value,
                    AuditLog.entity_id == str(item_id),
                )
            )
            or 0
        )


async def _results_count(db: Database) -> int:
    async with db.session() as session:
        return int(await session.scalar(select(func.count()).select_from(PrWorkResult)) or 0)


# ---------------------------------------------------------------------------
# 41: two admins delete the same row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["cancelled", "rejected"])
async def test_41_two_admins_deleting_one_row_leave_one_winner_and_no_orphans(
    kind: str, db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    item_id = await MAKERS[kind](db, people, "Xóa song song")

    outcomes = sorted(
        await asyncio.gather(_delete(db, people, item_id), _delete(db, people, item_id))
    )
    assert outcomes == ["PrNotFoundError", "ok"], outcomes

    await _no_orphans(db, item_id)
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
    assert await _audited(db, item_id) == 1, "the winner audited once; the loser wrote nothing"


# ---------------------------------------------------------------------------
# 42: a delete races a result report
# ---------------------------------------------------------------------------


async def test_42_a_delete_racing_a_result_report_leaves_the_result_in_its_container(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    item_id = await _cancelled(db, people, "Xóa khi đang báo kết quả")
    results_before = await _results_count(db)

    async def report() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work_results.report_result(
                    actor=people.member_actor,
                    request_id=uuid.uuid4(),
                    quantity=Decimal("2"),
                    work_type_id=people.work_type,
                    subject_user_id=people.member,
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(_delete(db, people, item_id), report())
    assert outcomes == ["ok", "ok"], outcomes
    await _no_orphans(db, item_id)
    assert await _results_count(db) == results_before + 1
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
        newest = (
            (await session.execute(select(PrWorkResult).order_by(PrWorkResult.created_at.desc())))
            .scalars()
            .first()
        )
        assert newest is not None
        container = await session.get(PrWorkItem, newest.work_item_id)
        assert container is not None and container.is_period_container


# ---------------------------------------------------------------------------
# 43: a delete races an evidence write on the same row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["cancelled", "rejected"])
async def test_43_a_delete_racing_an_evidence_write_never_orphans_the_evidence(
    kind: str, db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    item_id = await MAKERS[kind](db, people, "Xóa khi đang thêm minh chứng")

    async def attach() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work.add_evidence_text(
                    actor=people.member_actor,
                    request_id=uuid.uuid4(),
                    work_item_id=item_id,
                    text="Link: https://example.com/late",
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(_delete(db, people, item_id), attach())
    assert outcomes[0] == "ok", outcomes
    # Evidence may still be attached to a cancelled row, so the write either
    # got the row lock first - it was written, and the delete that followed
    # took it with the row - or waited on the lock and found no row. Both
    # are legal; what is not is evidence naming a row that is gone.
    assert outcomes[1] in {"ok", "PrNotFoundError"}, outcomes
    await _no_orphans(db, item_id)
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
        left = await session.scalar(
            select(func.count())
            .select_from(PrWorkEvidence)
            .where(PrWorkEvidence.work_item_id == item_id)
        )
        assert left == 0


# ---------------------------------------------------------------------------
# 44: a delete races a list read
# ---------------------------------------------------------------------------


async def test_44_a_delete_racing_a_list_read_never_fails_the_read(
    db: Database, people: People
) -> None:
    period_id = await open_month(db, people, utcnow())
    item_id = await _cancelled(db, people, "Xóa khi đang xem danh sách")

    async def read(status: PrWorkStatus | None) -> tuple[str, bool]:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            page = await services.work_queries.page(
                actor=people.owner_actor,
                query=WorkQuery(
                    scope=PrWorkScope.ALL, period_id=period_id, status=status, limit=200
                ),
            )
            return "ok", any(one.id == item_id for one in page.items)

    # The default view never lists it, deleted or not.
    assert (await read(None)) == ("ok", False)
    assert (await read(PrWorkStatus.CANCELLED)) == ("ok", True)

    outcomes = await asyncio.gather(
        _delete(db, people, item_id),
        read(PrWorkStatus.CANCELLED),
        read(None),
        read(PrWorkStatus.CANCELLED),
    )
    assert outcomes[0] == "ok"
    for status, _seen in outcomes[1:]:
        assert status == "ok"
    assert outcomes[2] == ("ok", False), "the default view showed it at no point"
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
    assert (await read(PrWorkStatus.CANCELLED)) == ("ok", False)


# ---------------------------------------------------------------------------
# 45: a failure after the child cleanup rolls everything back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["cancelled", "rejected"])
async def test_45_a_failure_after_child_cleanup_commits_nothing(
    kind: str, db: Database, people: People, monkeypatch: pytest.MonkeyPatch
) -> None:
    await open_month(db, people, utcnow())
    item_id = await MAKERS[kind](db, people, "Hỏng sau khi dọn con")
    before = await _whole(db, item_id)
    assert before["evidence"] == 1 and before["contributions"] == 1

    async with db.session() as session:
        services = build_pr_services(session, settings())
        audit = services.work_maintenance._audit  # the seam under test

        async def explode(*args: object, **kwargs: object) -> object:
            raise RuntimeError("died after the child rows were deleted")

        # The audit row is written after every child delete and before the
        # row itself goes; failing there is failing after the cleanup.
        monkeypatch.setattr(audit, "record_action", explode)
        with pytest.raises(RuntimeError):
            await services.work_maintenance.admin_delete_terminal_work_item(
                actor=people.owner_actor, request_id=uuid.uuid4(), work_item_id=item_id
            )
        await session.rollback()

    async with db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        assert item is not None, "the delete did not survive alone"
        assert item.status is (
            PrWorkStatus.CANCELLED if kind == "cancelled" else PrWorkStatus.REJECTED
        )
    assert await _whole(db, item_id) == before
    assert await _audited(db, item_id) == 0
    # And the same row deletes cleanly once nothing explodes.
    assert await _delete(db, people, item_id) == "ok"
    await _no_orphans(db, item_id)


# ---------------------------------------------------------------------------
# 46: a delete races a status mutation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["cancelled", "rejected"])
async def test_46_a_delete_racing_a_status_mutation_leaves_no_half_state(
    kind: str, db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    item_id = await MAKERS[kind](db, people, "Xóa khi đang đổi trạng thái")

    async def mutate() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work.start(
                    actor=people.member_actor, request_id=uuid.uuid4(), work_item_id=item_id
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(_delete(db, people, item_id), mutate())
    assert outcomes[0] == "ok", outcomes
    # A cancelled row cannot be started; whichever got the lock first, the
    # mutation ends in a refusal or a not-found, and the row is gone.
    assert outcomes[1] != "ok", outcomes
    await _no_orphans(db, item_id)
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None


# ---------------------------------------------------------------------------
# 47: a delete races M2's reconcile
# ---------------------------------------------------------------------------


async def test_47_a_delete_racing_an_m2_reconcile_leaves_no_accounting_behind(
    db: Database, people: People
) -> None:
    period_id = await open_month(db, people, utcnow())
    item_id = await _cancelled(db, people, "Xóa khi M2 tính")
    async with db.session() as session:
        rows = (
            (
                await session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1 and rows[0].count_status is PrWorkCountStatus.EXCLUDED

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
    assert outcomes == ["ok", "ok"], outcomes
    await _no_orphans(db, item_id)
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None
        services = build_pr_services(session, settings())
        summary = await services.work_eligibility.summary(
            actor=people.owner_actor, user_id=people.member, period_id=period_id
        )
        assert summary.period_id == period_id


# ---------------------------------------------------------------------------
# 27-28 (rejected): a delete racing a result report, and a detail read
# ---------------------------------------------------------------------------


async def test_r27_a_rejected_delete_racing_a_result_report_leaves_the_result_in_its_container(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    item_id = await _rejected(db, people, "Xóa đề xuất khi đang báo kết quả")
    results_before = await _results_count(db)

    async def report() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work_results.report_result(
                    actor=people.member_actor,
                    request_id=uuid.uuid4(),
                    quantity=Decimal("2"),
                    work_type_id=people.work_type,
                    subject_user_id=people.member,
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(_delete(db, people, item_id), report())
    assert outcomes == ["ok", "ok"], outcomes
    await _no_orphans(db, item_id)
    assert await _results_count(db) == results_before + 1
    async with db.session() as session:
        assert await session.get(PrWorkItem, item_id) is None


async def test_r28_a_rejected_delete_racing_a_detail_read_never_fails_the_read(
    db: Database, people: People
) -> None:
    item_id = await _rejected(db, people, "Xóa đề xuất khi đang xem chi tiết")

    async def read() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                detail = await services.work.detail(actor=people.owner_actor, work_item_id=item_id)
                eligibility = await services.work_maintenance.terminal_delete_eligibility(
                    detail.item
                )
                return "seen" if eligibility.deletable else "blocked"
            except Exception as exc:
                return type(exc).__name__

    outcomes = await asyncio.gather(_delete(db, people, item_id), read(), read())
    assert outcomes[0] == "ok"
    for status in outcomes[1:]:
        assert status in {"seen", "PrNotFoundError"}, outcomes
    await _no_orphans(db, item_id)
    assert (await read()) == "PrNotFoundError"
