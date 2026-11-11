"""How long background tasks wait in their queue (plan ``docs/ops/SCALING_PLAN.md`` D).

``celery_app`` stamps every message it publishes with the time it was sent
(header :data:`SENT_HEADER`, beat and API alike). Here, in the worker:

* ``task_prerun`` - the wait = now - that stamp, added to the hourly timings
  under the task's name;
* ``task_revoked`` with ``expired`` - a task that waited past its ``expires``
  and was given up (``cskh.poll_pancake`` expires after 18 s): counted as
  dropped. Many of those = the worker is too busy.

Saved at most once a minute per worker process, on a fresh database like the
job monitor does. Never breaks a task: errors are logged and swallowed. Eager
mode (tests) records nothing.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from celery import Task
from celery.signals import task_prerun, task_revoked

from meobot.application.ops.perf_stats import FLUSH_SECONDS, PerfCollector, flush
from meobot.core.config import get_settings
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.ops.perf_stats import PerfKind

logger = get_logger(__name__)

#: Header ``celery_app`` sets on every published task: ``time.time()`` at send.
SENT_HEADER = "meobot_sent_at"

COLLECTOR = PerfCollector()
_last_flush = time.monotonic()


def sent_at(task: Task[Any, Any]) -> float | None:
    request = task.request
    headers = getattr(request, "headers", None)
    raw = headers.get(SENT_HEADER) if isinstance(headers, dict) else None
    if raw is None:
        raw = getattr(request, SENT_HEADER, None)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _flush_if_due(*, force: bool = False) -> None:
    global _last_flush
    if not get_settings().perf_stats_enabled:
        return
    if not force and time.monotonic() - _last_flush < FLUSH_SECONDS:
        return
    _last_flush = time.monotonic()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:  # pragma: no cover - only eager mode runs inside a loop
        return

    async def main() -> None:
        database = Database(get_settings())
        try:
            await flush(COLLECTOR, database, prune_too=False)
        finally:
            await database.dispose()

    try:
        asyncio.run(main())
    except Exception:  # timings must never break a task
        logger.warning("perf_monitor_flush_failed", exc_info=True)


@task_prerun.connect
def _queue_wait(task: Task[Any, Any] | None = None, **_: object) -> None:
    if task is None or getattr(task.request, "is_eager", False):
        return
    sent = sent_at(task)
    if sent is None:
        return
    COLLECTOR.record(PerfKind.QUEUE, task.name, max(0.0, (time.time() - sent) * 1000))
    _flush_if_due()


@task_revoked.connect
def _dropped(request: Any = None, expired: bool = False, sender: Any = None, **_: object) -> None:
    if not expired:
        return
    name = getattr(request, "task_name", None) or getattr(request, "name", None)
    if name is None and sender is not None:
        name = getattr(sender, "name", None)
    if not name:
        return
    COLLECTOR.drop(str(name))
    _flush_if_due()


__all__ = ["COLLECTOR", "SENT_HEADER", "sent_at"]
