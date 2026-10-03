"""Work maintenance against a real PostgreSQL: the races, and no partial cleanup.

What only a real database answers here is what happens when an administrator's
sync or rebuild lands **at the same moment** as the worker's own projection, a
second administrator's rebuild, or a removal - and whether a rebuild that fails
half-way leaves anything behind. Every scenario ends by reading the ledger from
a fresh connection and checking the one invariant the module protects: a
result's container holds exactly the sum of its counted results, and there is
exactly one result per content source key.

Scenarios:

#. sync and the worker project the same piece at once - one result, both commit;
#. rebuild and the worker race after a remap - one result, under the new type;
#. two rebuilds over one scope race - both commit, one state;
#. an administrative removal races a projection - the ledger stays consistent
   whichever won;
#. deleting a work type races a draft quota being added to it - the constraint
   keeps them consistent: never a quota on a type that is gone;
#. after a rebuild, M2's allocation names the corrected type;
#. a rebuild that fails after its removal step commits nothing.

Fixtures come from the atomicity suite: the same scratch database, the same
five people, the same explicit mapping for short-video scripts.

Requires a PostgreSQL you are willing to have scratch databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_maintenance_pg.py -m integration
"""

from __future__ import annotations

# ruff: noqa: F811 - `db` and `people` are fixtures imported from the atomicity suite
import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_work_maintenance_service import MaintenanceScope
from meobot.core.time import utcnow
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkQuota, PrWorkQuotaAllocation
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.pr.content_work import PrContentWorkKind
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.work import PrWorkCategory, PrWorkCountStatus
from meobot.domain.pr.work_results import PrWorkResultSource
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL
from tests.integration.test_pr_content_work_atomicity import (  # noqa: F401 - fixtures
    People,
    _consistent,
    _contributions_of,
    _one_result,
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
TYPE = PrContentType.SHORT_VIDEO_SCRIPT


async def _period(db: Database, people: People) -> uuid.UUID:
    return await open_month(db, people, utcnow())


async def _run(db: Database, people: People, operation: str, period_id: uuid.UUID) -> str:
    """One maintenance run in its own transaction, committed. Returns 'ok' or the error class."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        scope = MaintenanceScope(period_id=period_id)
        try:
            if operation == "sync":
                await services.work_maintenance.sync_missing(
                    actor=people.owner_actor, request_id=uuid.uuid4(), scope=scope
                )
            else:
                await services.work_maintenance.rebuild(
                    actor=people.owner_actor, request_id=uuid.uuid4(), scope=scope
                )
            await session.commit()
            return "ok"
        except Exception as exc:  # pragma: no cover - a failure is the finding
            await session.rollback()
            return type(exc).__name__


async def _project(db: Database, people: People, content_id: uuid.UUID) -> str:
    try:
        await project_once(db, people, content_id)
        return "ok"
    except Exception as exc:  # pragma: no cover - a failure is the finding
        return type(exc).__name__


async def _new_type(db: Database, people: People, code: str, name: str) -> uuid.UUID:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        row = await services.work.create_work_type(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            code=code,
            name=name,
            category=PrWorkCategory.CONTENT,
        )
        type_id = row.id
        await session.commit()
        return type_id


async def _map(db: Database, people: People, type_id: uuid.UUID) -> None:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        await services.content_work_rules.upsert_rule(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            contribution_kind=KIND,
            content_type=TYPE,
            work_type_id=type_id,
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 1: sync races the worker
# ---------------------------------------------------------------------------


async def test_sync_and_the_worker_projecting_one_piece_commit_one_result(
    db: Database, people: People
) -> None:
    period_id = await _period(db, people)
    content_id = await approved_content(db, people, title="Đồng bộ song song")
    outcomes = await asyncio.gather(
        _run(db, people, "sync", period_id), _project(db, people, content_id)
    )
    assert outcomes == ["ok", "ok"], outcomes
    async with db.session() as session:
        await _consistent(session, content_id)
        assert len(await _results_for(session, content_id)) == 1


# ---------------------------------------------------------------------------
# 2-3: rebuild races the worker, and another rebuild
# ---------------------------------------------------------------------------


async def test_rebuild_and_the_worker_race_after_a_remap(db: Database, people: People) -> None:
    """The worker converges the same piece while the administrator rebuilds.

    The rebuild excludes the counted result so it can be refiled; the worker
    may see it before or after. Either way the piece ends with one result,
    COUNTED, under the corrected type, in a container whose actual is the sum
    of its counted results.
    """
    period_id = await _period(db, people)
    content_id = await approved_content(db, people, title="Đổi ánh xạ song song")
    await project_once(db, people, content_id)
    corrected = await _new_type(db, people, "SHORT_SCRIPT_V2", "Kịch bản ngắn/Bài đăng")
    await _map(db, people, corrected)

    outcomes = await asyncio.gather(
        _run(db, people, "rebuild", period_id), _project(db, people, content_id)
    )
    assert outcomes == ["ok", "ok"], outcomes
    # One more pass from the worker, as the queue would give it, settles any
    # order the two could have interleaved in.
    await project_once(db, people, content_id)
    async with db.session() as session:
        item = await _consistent(session, content_id)
        assert item.work_type_id == corrected
        assert len(await _results_for(session, content_id)) == 1

    # Back to the fixture's type for the tests that follow.
    await _map(db, people, people.work_type)
    await _run(db, people, "rebuild", period_id)
    async with db.session() as session:
        assert (await _consistent(session, content_id)).work_type_id == people.work_type


async def test_two_rebuilds_over_one_scope_both_commit_one_state(
    db: Database, people: People
) -> None:
    period_id = await _period(db, people)
    content_id = await approved_content(db, people, title="Hai quản trị viên")
    await project_once(db, people, content_id)
    outcomes = await asyncio.gather(
        _run(db, people, "rebuild", period_id), _run(db, people, "rebuild", period_id)
    )
    assert outcomes == ["ok", "ok"], outcomes
    async with db.session() as session:
        await _consistent(session, content_id)
        assert len(await _results_for(session, content_id)) == 1


# ---------------------------------------------------------------------------
# 4: a removal races a projection
# ---------------------------------------------------------------------------


async def test_an_admin_removal_racing_a_projection_leaves_the_ledger_consistent(
    db: Database, people: People
) -> None:
    period_id = await _period(db, people)
    content_id = await approved_content(db, people, title="Gỡ song song")
    await project_once(db, people, content_id)
    async with db.session() as session:
        result_id = (await _one_result(session, content_id)).id

    async def remove() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work_maintenance.admin_remove_result(
                    actor=people.owner_actor, request_id=uuid.uuid4(), result_id=result_id
                )
                await session.commit()
                return "ok"
            except Exception as exc:  # pragma: no cover
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(remove(), _project(db, people, content_id))
    assert outcomes == ["ok", "ok"], outcomes
    async with db.session() as session:
        result = await _one_result(session, content_id)
        item = await session.get(PrWorkItem, result.work_item_id)
        assert item is not None
        counted = await session.scalar(
            select(func.coalesce(func.sum(PrWorkResult.quantity), 0)).where(
                PrWorkResult.work_item_id == item.id,
                PrWorkResult.status == PrWorkCountStatus.COUNTED,
            )
        )
        assert item.quantity == counted, "the actual is the sum, whichever write landed last"
        rows = await _contributions_of(session, item.id)
        assert rows[0].count_status is (
            PrWorkCountStatus.COUNTED if item.quantity > 0 else PrWorkCountStatus.PENDING
        )
    # And the worker's next pass - the source is still accepted - counts it.
    await project_once(db, people, content_id)
    async with db.session() as session:
        assert (await _one_result(session, content_id)).status is PrWorkCountStatus.COUNTED
    assert period_id is not None


# ---------------------------------------------------------------------------
# 5: deleting a type races a quota being added to it
# ---------------------------------------------------------------------------


async def test_deleting_a_type_racing_a_draft_quota_never_leaves_a_quota_on_a_gone_type(
    db: Database, people: People
) -> None:
    period_id = await _period(db, people)
    doomed = await _new_type(db, people, "DOOMED", "Sắp xóa")
    async with db.session() as session:
        services = build_pr_services(session, settings())
        plan = await services.work_plans.create_plan(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            user_id=people.other,
            period_id=period_id,
        )
        plan_id = plan.plan.id
        await session.commit()

    async def delete_type() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work_maintenance.delete_work_type(
                    actor=people.owner_actor, request_id=uuid.uuid4(), work_type_id=doomed
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    async def add_quota() -> str:
        async with db.session() as session:
            services = build_pr_services(session, settings())
            try:
                await services.work_plans.add_quota(
                    actor=people.owner_actor,
                    request_id=uuid.uuid4(),
                    plan_id=plan_id,
                    work_type_id=doomed,
                    target_value=Decimal("2"),
                    eligibility_cap=Decimal("2"),
                )
                await session.commit()
                return "ok"
            except Exception as exc:
                await session.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(delete_type(), add_quota())
    async with db.session() as session:
        type_row = await session.get(PrWorkType, doomed)
        quotas = (
            (await session.execute(select(PrWorkQuota).where(PrWorkQuota.work_type_id == doomed)))
            .scalars()
            .all()
        )
    # Whichever won, the database never holds a quota on a type that is gone.
    assert not (type_row is None and quotas), outcomes
    assert "ok" in outcomes, outcomes


# ---------------------------------------------------------------------------
# 6-7: M2 after a rebuild, and no partial cleanup
# ---------------------------------------------------------------------------


async def test_after_a_rebuild_m2_allocates_under_the_corrected_type(
    db: Database, people: People
) -> None:
    period_id = await _period(db, people)
    content_id = await approved_content(db, people, title="M2 sau rebuild")
    await project_once(db, people, content_id)
    corrected = await _new_type(db, people, "SHORT_SCRIPT_V3", "Kịch bản v3")
    async with db.session() as session:
        services = build_pr_services(session, settings())
        plan = await services.work_plans.create_plan(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            user_id=people.member,
            period_id=period_id,
        )
        await services.work_plans.add_quota(
            actor=people.owner_actor,
            request_id=uuid.uuid4(),
            plan_id=plan.plan.id,
            work_type_id=corrected,
            target_value=Decimal("10"),
            eligibility_cap=Decimal("10"),
        )
        await services.work_plans.approve(
            actor=people.owner_actor, request_id=uuid.uuid4(), plan_id=plan.plan.id
        )
        await session.commit()
    await _map(db, people, corrected)
    assert await _run(db, people, "rebuild", period_id) == "ok"

    async with db.session() as session:
        item = await _consistent(session, content_id)
        assert item.work_type_id == corrected
        contribution = (await _contributions_of(session, item.id))[0]
        allocations = (
            (
                await session.execute(
                    select(PrWorkQuotaAllocation).where(
                        PrWorkQuotaAllocation.work_contribution_id == contribution.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [one.work_type_id for one in allocations] == [corrected]
    await _map(db, people, people.work_type)


async def test_a_rebuild_that_fails_after_its_removal_step_commits_nothing(
    db: Database, people: People, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The removal and the reprojection are one transaction: a failure between
    them rolls the removal back, and the counted result is still counted."""
    period_id = await _period(db, people)
    content_id = await approved_content(db, people, title="Hỏng giữa chừng")
    await project_once(db, people, content_id)
    corrected = await _new_type(db, people, "SHORT_SCRIPT_V4", "Kịch bản v4")
    await _map(db, people, corrected)

    async with db.session() as session:
        services = build_pr_services(session, settings())

        async def explode(*args: object, **kwargs: object) -> object:
            raise RuntimeError("worker died between the two steps")

        monkeypatch.setattr(services.work_maintenance, "_project", explode)
        with pytest.raises(RuntimeError):
            await services.work_maintenance.rebuild(
                actor=people.owner_actor,
                request_id=uuid.uuid4(),
                scope=MaintenanceScope(period_id=period_id),
            )
        await session.rollback()

    async with db.session() as session:
        result = await _one_result(session, content_id)
        assert result.status is PrWorkCountStatus.COUNTED, "the removal did not survive alone"
        item = await _consistent(session, content_id)
        assert item.work_type_id == people.work_type, "still where it was"
        assert result.source_type is PrWorkResultSource.CONTENT
    await _map(db, people, people.work_type)
