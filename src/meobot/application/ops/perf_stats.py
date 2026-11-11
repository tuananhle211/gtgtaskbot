"""Collecting, saving and reading the hourly timings (plan ``docs/ops/SCALING_PLAN.md`` D).

* :class:`PerfCollector` - in memory, one per process; ``record`` costs a dict
  lookup under a lock, so it may sit on every request.
* :func:`save_tallies` - adds a batch to ``perf_stats_hourly`` (an upsert that
  sums), so several API processes / worker children can each write theirs.
* :class:`PerfFlusher` - the API's background loop: every minute it saves what
  was collected and, once an hour, deletes rows older than 30 days. It never
  raises: losing a minute of figures is better than a broken request.
* :class:`PerfReportService` - the "Hiệu năng" screen (OWNER / ADMIN, like the
  job monitor beside it).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.cron.monitor_service import may_view
from meobot.core.errors import AuthorizationError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.perf_stat import BUCKET_COLUMNS, PerfStatHourly
from meobot.db.session import Database
from meobot.domain.identity.models import Actor
from meobot.domain.ops.perf_stats import (
    KEEP_DAYS,
    PerfKind,
    Tally,
    hour_of,
    is_peak,
    percentile_ms,
)

logger = get_logger(__name__)

#: (hour, kind, method, name)
Key = tuple[datetime, str, str, str]

FLUSH_SECONDS = 60
PRUNE_SECONDS = 3600
MAX_NAME = 200
#: Peak-hour p95 of API requests above this = more API processes are worth it
#: (plan đợt 2 threshold).
API_P95_LIMIT_MS = 300


class PerfForbiddenError(AuthorizationError):
    code = "perf_forbidden"

    def __init__(self) -> None:
        super().__init__("Chỉ chủ sở hữu và quản trị viên xem được hiệu năng hệ thống.")


class PerfCollector:
    """Thread-safe tallies waiting to be saved."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cells: dict[Key, Tally] = {}

    def record(
        self,
        kind: PerfKind,
        name: str,
        ms: float,
        *,
        method: str = "",
        failed: bool = False,
        at: datetime | None = None,
    ) -> None:
        key = (hour_of(at or utcnow()), kind.value, method[:10], name[:MAX_NAME])
        with self._lock:
            self._cells.setdefault(key, Tally()).add(kind, ms, failed=failed)

    def drop(self, name: str, *, at: datetime | None = None) -> None:
        """A queued task given up because it waited past its ``expires``."""
        key = (hour_of(at or utcnow()), PerfKind.QUEUE.value, "", name[:MAX_NAME])
        with self._lock:
            self._cells.setdefault(key, Tally()).dropped += 1

    def take(self) -> dict[Key, Tally]:
        with self._lock:
            cells, self._cells = self._cells, {}
        return cells

    def give_back(self, cells: Mapping[Key, Tally]) -> None:
        """Put a batch that could not be saved back, to try again next time."""
        with self._lock:
            for key, tally in cells.items():
                self._cells.setdefault(key, Tally()).merge(tally)


async def save_tallies(session: AsyncSession, cells: Mapping[Key, Tally]) -> None:
    """Add every tally to its row (created when missing)."""
    if not cells:
        return
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    rows: list[dict[str, Any]] = []
    for (hour, kind, method, name), tally in cells.items():
        row: dict[str, Any] = {
            "hour": hour,
            "kind": kind,
            "method": method,
            "name": name,
            "count": tally.count,
            "total_ms": tally.total_ms,
            "max_ms": tally.max_ms,
            "slow": tally.slow,
            "failed": tally.failed,
            "dropped": tally.dropped,
        }
        row.update(dict(zip(BUCKET_COLUMNS, tally.buckets, strict=True)))
        rows.append(row)
    table = PerfStatHourly.__table__
    statement = insert(PerfStatHourly).values(rows)
    excluded = statement.excluded
    summed = ("count", "total_ms", "slow", "failed", "dropped", *BUCKET_COLUMNS)
    values: dict[str, Any] = {name: table.c[name] + excluded[name] for name in summed}
    values["max_ms"] = case(
        (excluded.max_ms > table.c.max_ms, excluded.max_ms), else_=table.c.max_ms
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["hour", "kind", "method", "name"], set_=values
        )
    )


async def prune(session: AsyncSession, now: datetime | None = None) -> None:
    cutoff = hour_of((now or utcnow()) - timedelta(days=KEEP_DAYS))
    await session.execute(
        delete(PerfStatHourly)
        .where(PerfStatHourly.hour < cutoff)
        .execution_options(synchronize_session=False)
    )


async def flush(collector: PerfCollector, database: Database, *, prune_too: bool) -> None:
    """Save what was collected; on failure keep it for the next try."""
    cells = collector.take()
    if not cells and not prune_too:
        return
    try:
        async with database.transaction() as session:
            await save_tallies(session, cells)
            if prune_too:
                await prune(session)
    except Exception:
        collector.give_back(cells)
        logger.warning("perf_stats_flush_failed", exc_info=True)


class PerfFlusher:
    """The API process's minute loop (started / stopped by the app lifespan)."""

    def __init__(self, collector: PerfCollector, database: Database) -> None:
        self._collector = collector
        self._database = database
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="perf-stats-flush")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await flush(self._collector, self._database, prune_too=False)

    async def _run(self) -> None:
        since_prune = PRUNE_SECONDS  # prune once soon after start
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            since_prune += FLUSH_SECONDS
            due = since_prune >= PRUNE_SECONDS
            await flush(self._collector, self._database, prune_too=due)
            if due:
                since_prune = 0


# -- reading -----------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    level: str  # good | warn | bad | none
    message: str


def _line(kind: PerfKind, method: str, name: str, tally: Tally) -> dict[str, Any]:
    return {
        "method": method,
        "name": name,
        "count": tally.count,
        "average_ms": tally.average_ms,
        "p95_ms": percentile_ms(kind, tally.buckets),
        "max_ms": tally.max_ms,
        "slow": tally.slow,
        "failed": tally.failed,
        "dropped": tally.dropped,
    }


def _summary(kind: PerfKind, tally: Tally) -> dict[str, Any]:
    return _line(kind, "", "", tally)


def api_verdict(peak: Tally) -> Verdict:
    p95 = percentile_ms(PerfKind.HTTP, peak.buckets)
    if p95 is None or peak.count < 200:
        return Verdict("none", "Chưa đủ số liệu giờ cao điểm (cần vài ngày làm việc).")
    if p95 > API_P95_LIMIT_MS:
        return Verdict(
            "bad",
            f"Giờ cao điểm, 95% yêu cầu xong trong {p95} ms - trên ngưỡng {API_P95_LIMIT_MS} ms: "
            "nên làm đợt 2 (nhiều tiến trình API).",
        )
    return Verdict(
        "good",
        f"Giờ cao điểm, 95% yêu cầu xong trong {p95} ms - dưới ngưỡng {API_P95_LIMIT_MS} ms: "
        "chưa cần đợt 2.",
    )


def queue_verdict(lines: list[dict[str, Any]]) -> Verdict:
    dropped = sum(line["dropped"] for line in lines)
    slow = sum(line["slow"] for line in lines)
    if not lines:
        return Verdict("none", "Chưa có số liệu hàng đợi.")
    if dropped:
        return Verdict(
            "bad",
            f"{dropped} lượt tác vụ bị bỏ vì chờ quá lâu - worker đang quá tải.",
        )
    if slow:
        return Verdict("warn", f"{slow} lượt tác vụ phải chờ trên 1 phút mới được chạy.")
    return Verdict("good", "Tác vụ nền được chạy ngay, không bị dồn.")


class PerfReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def report(
        self, *, actor: Actor, days: int, now: datetime | None = None
    ) -> dict[str, Any]:
        if not may_view(actor):
            raise PerfForbiddenError()
        days = max(1, min(days, KEEP_DAYS))
        current = now or utcnow()
        since = hour_of(current - timedelta(days=days))
        rows = (
            await self._session.scalars(select(PerfStatHourly).where(PerfStatHourly.hour >= since))
        ).all()
        by_line: dict[tuple[str, str, str], Tally] = {}
        http_all, http_peak, queue_all = Tally(), Tally(), Tally()
        for row in rows:
            tally = Tally(
                count=row.count,
                total_ms=row.total_ms,
                max_ms=row.max_ms,
                slow=row.slow,
                failed=row.failed,
                dropped=row.dropped,
                buckets=[getattr(row, column) for column in BUCKET_COLUMNS],
            )
            by_line.setdefault((row.kind, row.method, row.name), Tally()).merge(tally)
            if row.kind == PerfKind.HTTP.value:
                http_all.merge(tally)
                if is_peak(row.hour):
                    http_peak.merge(tally)
            else:
                queue_all.merge(tally)
        routes = [
            _line(PerfKind.HTTP, method, name, tally)
            for (kind, method, name), tally in by_line.items()
            if kind == PerfKind.HTTP.value
        ]
        routes.sort(key=lambda line: (-line["slow"], -(line["p95_ms"] or 0), -line["count"]))
        queue = [
            _line(PerfKind.QUEUE, method, name, tally)
            for (kind, method, name), tally in by_line.items()
            if kind == PerfKind.QUEUE.value
        ]
        queue.sort(key=lambda line: (-line["dropped"], -(line["p95_ms"] or 0), line["name"]))
        api, waits = api_verdict(http_peak), queue_verdict(queue)
        return {
            "days": days,
            "since": since.isoformat(),
            "api": {
                **_summary(PerfKind.HTTP, http_all),
                "peak_p95_ms": percentile_ms(PerfKind.HTTP, http_peak.buckets),
                "peak_count": http_peak.count,
                "limit_ms": API_P95_LIMIT_MS,
                "verdict": {"level": api.level, "message": api.message},
            },
            "routes": routes[:25],
            "queue": queue,
            "queue_verdict": {"level": waits.level, "message": waits.message},
        }


__all__ = [
    "API_P95_LIMIT_MS",
    "FLUSH_SECONDS",
    "PerfCollector",
    "PerfFlusher",
    "PerfForbiddenError",
    "PerfReportService",
    "api_verdict",
    "flush",
    "prune",
    "queue_verdict",
    "save_tallies",
]
