"""Hiệu năng: hourly API response times and queue waits (plan docs/ops/SCALING_PLAN.md D)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ops.perf_stats import (
    PerfCollector,
    api_verdict,
    prune,
    queue_verdict,
    save_tallies,
)
from meobot.db.models.perf_stat import PerfStatHourly
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.ops.perf_stats import (
    PerfKind,
    Tally,
    bucket_of,
    hour_of,
    is_peak,
    percentile_ms,
)
from meobot.tasks import perf_monitor
from meobot.tasks.celery_app import _stamp_sent_at
from tests.unit.pr_world import World

NOON_VN = datetime(2026, 11, 11, 5, 20, tzinfo=UTC)  # 12:20 in Vietnam


def test_01_buckets_percentiles_and_peak_hours() -> None:
    assert bucket_of(PerfKind.HTTP, 10) == 0
    assert bucket_of(PerfKind.HTTP, 50) == 1  # bounds are exclusive upper limits
    assert bucket_of(PerfKind.HTTP, 999) == 5
    assert bucket_of(PerfKind.HTTP, 60_000) == 8
    assert bucket_of(PerfKind.QUEUE, 45_000) == 5

    tally = Tally()
    for _ in range(95):
        tally.add(PerfKind.HTTP, 40)
    for _ in range(5):
        tally.add(PerfKind.HTTP, 1500)
    assert tally.count == 100 and tally.slow == 5 and tally.max_ms == 1500
    assert percentile_ms(PerfKind.HTTP, tally.buckets) == 50
    assert percentile_ms(PerfKind.HTTP, tally.buckets, 0.99) == 2000
    assert percentile_ms(PerfKind.HTTP, [0] * 9) is None
    # Beyond the last bound answers the last bound.
    assert percentile_ms(PerfKind.HTTP, [0] * 8 + [3]) == 5000

    assert hour_of(NOON_VN) == datetime(2026, 11, 11, 5, tzinfo=UTC)
    assert is_peak(datetime(2026, 11, 11, 1, tzinfo=UTC))  # 08:00 VN
    assert is_peak(datetime(2026, 11, 11, 10, tzinfo=UTC))  # 17:00 VN
    assert not is_peak(datetime(2026, 11, 11, 11, tzinfo=UTC))  # 18:00 VN
    assert not is_peak(datetime(2026, 11, 10, 20, tzinfo=UTC))  # 03:00 VN


async def test_02_saving_twice_adds_up_and_old_hours_are_pruned(session: AsyncSession) -> None:
    first, second = PerfCollector(), PerfCollector()
    first.record(PerfKind.HTTP, "/api/x/{id}", 120, method="GET", at=NOON_VN)
    first.record(PerfKind.HTTP, "/api/x/{id}", 1200, method="GET", failed=True, at=NOON_VN)
    second.record(PerfKind.HTTP, "/api/x/{id}", 30, method="GET", at=NOON_VN)
    second.record(PerfKind.HTTP, "/api/x/{id}", 2500, method="GET", at=NOON_VN)
    second.drop("cskh.poll_pancake", at=NOON_VN)
    await save_tallies(session, first.take())
    await save_tallies(session, second.take())
    assert first.take() == {}

    row = await session.scalar(select(PerfStatHourly).where(PerfStatHourly.kind == "HTTP"))
    assert row is not None
    assert (row.count, row.total_ms, row.max_ms, row.slow, row.failed) == (4, 3850, 2500, 2, 1)
    assert (row.b0, row.b2, row.b6, row.b7) == (1, 1, 1, 1)
    dropped = await session.scalar(select(PerfStatHourly).where(PerfStatHourly.kind == "QUEUE"))
    assert dropped is not None and dropped.dropped == 1 and dropped.count == 0

    await prune(session, NOON_VN + timedelta(days=31))
    assert (await session.scalars(select(PerfStatHourly))).all() == []


async def test_03_the_middleware_counts_route_templates_not_paths(world: World) -> None:
    collector: PerfCollector = world.client.app.state.perf_collector  # type: ignore[attr-defined]
    collector.take()
    world.act_as(world.owner)
    world.client.get(f"/api/account/members/{world.member.id}/profile")
    world.client.get("/api/khong-co-route-nay")
    names = {(method, name) for (_, kind, method, name) in collector.take() if kind == "HTTP"}
    assert ("GET", "/api/account/members/{user_id}/profile") in names
    assert not any(str(world.member.id) in name for _, name in names)
    assert not any("khong-co-route-nay" in name for _, name in names)


async def test_04_only_owner_and_admin_read_the_report(world: World) -> None:
    hot = Tally()
    for _ in range(250):
        hot.add(PerfKind.HTTP, 420)
    now = datetime.now(UTC)
    peak_hour = now.replace(hour=3, minute=0, second=0, microsecond=0)  # 10:00 VN
    if peak_hour > now:
        peak_hour -= timedelta(days=1)
    await save_tallies(
        world.session,
        {
            (peak_hour, "HTTP", "GET", "/api/board/tasks"): hot,
            (peak_hour, "QUEUE", "", "cskh.poll_pancake"): Tally(dropped=3),
        },
    )
    await world.session.flush()

    world.act_as(world.owner)
    page = world.client.get("/api/cron/performance?days=2").json()
    assert page["days"] == 2
    assert page["api"]["count"] == 250 and page["api"]["peak_p95_ms"] == 500
    assert page["api"]["verdict"]["level"] == "bad"
    assert page["routes"][0]["name"] == "/api/board/tasks"
    assert page["queue"][0] == {
        "method": "",
        "name": "cskh.poll_pancake",
        "count": 0,
        "average_ms": None,
        "p95_ms": None,
        "max_ms": 0,
        "slow": 0,
        "failed": 0,
        "dropped": 3,
    }
    assert page["queue_verdict"]["level"] == "bad"

    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    world.act_as(admin)
    assert world.client.get("/api/cron/performance").status_code == 200
    world.act_as(world.member)
    refused = world.client.get("/api/cron/performance")
    assert refused.status_code == 403 and refused.json()["code"] == "perf_forbidden"


def test_05_verdicts() -> None:
    quiet = Tally()
    for _ in range(10):
        quiet.add(PerfKind.HTTP, 20)
    assert api_verdict(quiet).level == "none"  # too few to judge
    fast = Tally()
    for _ in range(300):
        fast.add(PerfKind.HTTP, 60)
    assert api_verdict(fast).level == "good" and "100 ms" in api_verdict(fast).message
    assert queue_verdict([]).level == "none"
    line: dict[str, Any] = {"dropped": 0, "slow": 0}
    assert queue_verdict([line]).level == "good"
    assert queue_verdict([{**line, "slow": 2}]).level == "warn"
    assert queue_verdict([{**line, "dropped": 1}]).level == "bad"


def test_06_the_worker_measures_the_wait_from_the_publish_stamp() -> None:
    headers: dict[str, Any] = {}
    _stamp_sent_at(headers=headers)
    assert abs(headers["meobot_sent_at"] - time.time()) < 5
    kept = {"meobot_sent_at": 1.0}
    _stamp_sent_at(headers=kept)  # a retry keeps the first stamp
    assert kept["meobot_sent_at"] == 1.0

    perf_monitor.COLLECTOR.take()
    task = SimpleNamespace(
        name="cskh.poll_pancake",
        request=SimpleNamespace(is_eager=False, headers={"meobot_sent_at": time.time() - 4}),
    )
    perf_monitor._last_flush = time.monotonic()  # no flush inside the test
    perf_monitor._queue_wait(task=task)  # type: ignore[arg-type]
    perf_monitor._dropped(request=SimpleNamespace(task_name="ads.sync_spend"), expired=True)
    perf_monitor._dropped(request=SimpleNamespace(task_name="ads.sync_spend"), expired=False)
    cells = {name: tally for (_, _, _, name), tally in perf_monitor.COLLECTOR.take().items()}
    assert cells["cskh.poll_pancake"].count == 1
    assert 3500 <= cells["cskh.poll_pancake"].max_ms <= 10_000
    assert cells["ads.sync_spend"].dropped == 1 and cells["ads.sync_spend"].count == 0

    eager = SimpleNamespace(name="x", request=SimpleNamespace(is_eager=True, headers={}))
    perf_monitor._queue_wait(task=eager)  # type: ignore[arg-type]
    assert perf_monitor.COLLECTOR.take() == {}
