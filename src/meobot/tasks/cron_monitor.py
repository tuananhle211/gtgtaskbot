"""Recording every scheduled run, from Celery's own signals (0077).

Nothing in the jobs themselves changes: ``task_prerun`` notes the start,
``task_failure`` the exception, ``task_postrun`` hands the outcome to
:class:`~meobot.application.cron.recorder.CronRecorder` - which may answer
"retry in 30 minutes", queued here with the attempt number in the headers.

Monitored: every task on the beat, and any task sent with a ``cron_trigger``
header (a manual re-run, a retry). Recording never breaks a job: any error in
here is logged and swallowed. Eager mode (tests, ``celery_task_always_eager``)
records nothing.
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import lru_cache
from typing import Any

from celery import Task, shared_task
from celery.signals import task_failure, task_postrun, task_prerun

from meobot.application.cron.monitor_service import HEADER_ATTEMPT, HEADER_TRIGGER, HEADER_USER
from meobot.application.cron.recorder import CronRecorder, RunOutcome, outcome_of, tracks_start
from meobot.application.cron.schedule import ScheduledJob, beat_jobs, unscheduled_job
from meobot.core.config import get_settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.cron.models import CronRunStatus, CronTrigger

logger = get_logger(__name__)


#: ``task_id -> (started, trigger, attempt, user)`` between prerun and postrun.
_STARTS: dict[str, tuple[datetime, CronTrigger, int, uuid.UUID | None]] = {}
#: ``task_id -> error text`` from ``task_failure`` (fires before postrun).
_ERRORS: dict[str, str] = {}


@lru_cache(maxsize=1)
def _jobs() -> dict[str, ScheduledJob]:
    return {job.key: job for job in beat_jobs()}


def _header(task: Task[Any, Any], name: str) -> Any:
    request = task.request
    headers = getattr(request, "headers", None)
    if isinstance(headers, dict) and name in headers:
        return headers[name]
    return getattr(request, name, None)


def _context(task: Task[Any, Any]) -> tuple[CronTrigger, int, uuid.UUID | None] | None:
    """How this run was started, or ``None`` when it is not monitored."""
    settings = get_settings()
    if not settings.cron_monitor_enabled or getattr(task.request, "is_eager", False):
        return None
    raw_trigger = _header(task, HEADER_TRIGGER)
    if raw_trigger is None and task.name not in _jobs():
        return None
    try:
        trigger = CronTrigger(str(raw_trigger)) if raw_trigger else CronTrigger.SCHEDULE
    except ValueError:
        trigger = CronTrigger.SCHEDULE
    try:
        attempt = max(1, int(_header(task, HEADER_ATTEMPT) or 1))
    except (TypeError, ValueError):
        attempt = 1
    raw_user = _header(task, HEADER_USER)
    try:
        user = uuid.UUID(str(raw_user)) if raw_user else None
    except ValueError:
        user = None
    return trigger, attempt, user


def _with_recorder[T](operation: Callable[[CronRecorder], Awaitable[T]]) -> T | None:
    """Run ``operation`` against a fresh database, never raising."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:  # pragma: no cover - only eager mode runs inside a loop
        return None

    async def main() -> T:
        settings = get_settings()
        database = Database(settings)
        try:
            async with database.transaction() as session:
                return await operation(CronRecorder(session, settings, jobs=_jobs()))
        finally:
            await database.dispose()

    try:
        return asyncio.run(main())
    except Exception:  # recording must never break the job
        logger.warning("cron_monitor_record_failed", exc_info=True)
        return None


@task_prerun.connect
def _cron_prerun(
    task_id: str | None = None, task: Task[Any, Any] | None = None, **_: object
) -> None:
    if task is None or task_id is None:
        return
    context = _context(task)
    if context is None:
        return
    trigger, attempt, user = context
    started = utcnow()
    _STARTS[task_id] = (started, trigger, attempt, user)
    if tracks_start(_jobs().get(task.name) or unscheduled_job(task.name), trigger):
        _with_recorder(lambda recorder: recorder.start(key=task.name, task_id=task_id, at=started))


@task_failure.connect
def _cron_failure(
    task_id: str | None = None,
    exception: BaseException | None = None,
    einfo: Any = None,
    **_: object,
) -> None:
    if task_id is None or task_id not in _STARTS:
        return
    head = f"{type(exception).__name__}: {exception}" if exception is not None else "Lỗi"
    tail = ""
    if einfo is not None:
        tail = "\n" + "".join(traceback.format_tb(getattr(einfo, "tb", None))[-3:])
    _ERRORS[task_id] = (head + tail)[:4000]


@task_postrun.connect
def _cron_postrun(
    task_id: str | None = None,
    task: Task[Any, Any] | None = None,
    retval: Any = None,
    state: str | None = None,
    **_: object,
) -> None:
    if task is None or task_id is None:
        return
    if state == "RETRY":
        # Celery's own autoretry: the same task id comes back; record its end.
        return
    started = _STARTS.pop(task_id, None)
    error = _ERRORS.pop(task_id, None)
    if started is None:
        return
    started_at, trigger, attempt, user = started
    if state == "FAILURE":
        first = (error or "Lỗi").splitlines()[0]
        outcome = RunOutcome(status=CronRunStatus.FAILED, message=first, error=error)
    else:
        outcome = outcome_of(retval)
    result = _with_recorder(
        lambda recorder: recorder.finish(
            key=task.name,
            task_id=task_id,
            trigger=trigger,
            attempt=attempt,
            triggered_by=user,
            started_at=started_at,
            finished_at=utcnow(),
            outcome=outcome,
        )
    )
    if result is not None and result.retry_in_seconds is not None:
        _queue_retry(task, result.next_attempt, result.retry_in_seconds, user)


def _queue_retry(
    task: Task[Any, Any], attempt: int, countdown: int, user: uuid.UUID | None
) -> None:
    spec = _jobs().get(task.name)
    queue = (spec.options or {}).get("queue") if spec is not None else None
    try:
        task.app.send_task(
            task.name,
            args=list(task.request.args or ()),
            kwargs=dict(task.request.kwargs or {}),
            countdown=countdown,
            headers={
                HEADER_TRIGGER: CronTrigger.RETRY.value,
                HEADER_ATTEMPT: attempt,
                HEADER_USER: str(user) if user else "",
            },
            **({"queue": queue} if queue else {}),
        )
    except Exception:  # pragma: no cover - the alert path still reports it
        logger.warning("cron_monitor_retry_not_queued", extra={"task": task.name}, exc_info=True)


@shared_task(name="cron.watchdog")
def watchdog() -> dict[str, Any]:
    """Catalogue the beat's jobs and report the ones that did not run."""
    opened = _with_recorder(_watch)
    return {"late_opened": opened or 0}


async def _watch(recorder: CronRecorder) -> int:
    await recorder.sync_catalog()
    return await recorder.check_late()


@shared_task(name="cron.purge_runs")
def purge_runs() -> dict[str, Any]:
    """Drop history past its keep (failures 90 days, frequent successes 24 h)."""
    deleted = _with_recorder(lambda recorder: recorder.purge())
    return {"deleted": deleted or 0}


__all__ = ["purge_runs", "watchdog"]
