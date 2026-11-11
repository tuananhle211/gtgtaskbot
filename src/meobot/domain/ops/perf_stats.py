"""How fast the system answers, counted per hour (plan ``docs/ops/SCALING_PLAN.md`` D).

Two kinds of timing, kept the same way:

* ``HTTP`` - one API request, from arrival to the response starting (a file
  download or an SSE stream counts until its first byte). Named by the route's
  template (``/api/hr/profiles/{user_id}``), never the real path, so no id or
  query string is ever kept.
* ``QUEUE`` - how long a background task waited in its Celery queue before a
  worker picked it up (named by the task, ``cskh.poll_pancake``). A task that
  waited past its ``expires`` and was dropped counts in ``dropped``.

Each (hour, kind, method, name) keeps a count, the summed and the longest time,
how many were slow, failed / dropped, and a histogram over fixed bounds - so a
p95 can be read back without keeping every timing. Nothing about who asked or
what was sent is kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class PerfKind(StrEnum):
    HTTP = "HTTP"
    QUEUE = "QUEUE"


#: Upper bounds (ms) of the histogram buckets; the last bucket is open-ended.
BOUNDS: dict[PerfKind, tuple[int, ...]] = {
    PerfKind.HTTP: (50, 100, 200, 300, 500, 1000, 2000, 5000),
    PerfKind.QUEUE: (1000, 2000, 5000, 10_000, 30_000, 60_000, 120_000, 300_000),
}
BUCKETS = len(BOUNDS[PerfKind.HTTP]) + 1

#: At or above this a timing is "slow": a request the person notices, a task
#: that waited long enough for its work to be late.
SLOW_MS: dict[PerfKind, int] = {PerfKind.HTTP: 1000, PerfKind.QUEUE: 60_000}

#: Rows older than this are deleted.
KEEP_DAYS = 30

#: Working hours in Vietnam (UTC+7), for "giờ cao điểm": 08:00-18:00.
PEAK_HOURS_VN = range(8, 18)


def _utc(at: datetime) -> datetime:
    # A stored time comes back without its zone from SQLite: it is UTC.
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def hour_of(at: datetime) -> datetime:
    """The UTC hour a timing belongs to."""
    return _utc(at).replace(minute=0, second=0, microsecond=0)


def bucket_of(kind: PerfKind, ms: float) -> int:
    for index, bound in enumerate(BOUNDS[kind]):
        if ms < bound:
            return index
    return len(BOUNDS[kind])


@dataclass
class Tally:
    """One (hour, kind, method, name) cell."""

    count: int = 0
    total_ms: int = 0
    max_ms: int = 0
    slow: int = 0
    failed: int = 0
    dropped: int = 0
    buckets: list[int] = field(default_factory=lambda: [0] * BUCKETS)

    def add(self, kind: PerfKind, ms: float, *, failed: bool = False) -> None:
        whole = max(0, round(ms))
        self.count += 1
        self.total_ms += whole
        self.max_ms = max(self.max_ms, whole)
        if whole >= SLOW_MS[kind]:
            self.slow += 1
        if failed:
            self.failed += 1
        self.buckets[bucket_of(kind, whole)] += 1

    def merge(self, other: Tally) -> None:
        self.count += other.count
        self.total_ms += other.total_ms
        self.max_ms = max(self.max_ms, other.max_ms)
        self.slow += other.slow
        self.failed += other.failed
        self.dropped += other.dropped
        self.buckets = [a + b for a, b in zip(self.buckets, other.buckets, strict=True)]

    @property
    def average_ms(self) -> int | None:
        return round(self.total_ms / self.count) if self.count else None


def percentile_ms(kind: PerfKind, buckets: Sequence[int], share: float = 0.95) -> int | None:
    """The upper bound of the bucket holding the ``share`` quantile (an
    over-estimate by at most one bucket). ``None`` with no timings; the
    open-ended last bucket answers its lower bound."""
    total = sum(buckets)
    if total == 0:
        return None
    wanted = share * total
    running = 0
    bounds = BOUNDS[kind]
    for index, count in enumerate(buckets):
        running += count
        if running >= wanted:
            return bounds[index] if index < len(bounds) else bounds[-1]
    return bounds[-1]


def is_peak(hour: datetime) -> bool:
    """Whether a UTC hour falls in Vietnam working hours."""
    return (_utc(hour).hour + 7) % 24 in PEAK_HOURS_VN


__all__ = [
    "BOUNDS",
    "BUCKETS",
    "KEEP_DAYS",
    "PEAK_HOURS_VN",
    "SLOW_MS",
    "PerfKind",
    "Tally",
    "bucket_of",
    "hour_of",
    "is_peak",
    "percentile_ms",
]
