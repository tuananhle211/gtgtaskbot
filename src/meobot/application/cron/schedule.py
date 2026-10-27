"""What the beat runs, read from Celery's own ``beat_schedule``.

The schedule is code (:mod:`meobot.tasks.celery_app`); the monitor reads it
rather than keeping a second copy that could drift. An entry's ``schedule`` is
seconds, a ``timedelta`` or a ``crontab``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from meobot.domain.cron.catalog import label_for

#: A job only ever started by hand (or by another job): judged like a daily one.
UNSCHEDULED_PERIOD_SECONDS = 86400
_VN = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    key: str
    label: str
    group: str
    description: str
    #: Roughly how often it runs; decides the default policy.
    period_seconds: int
    #: "mỗi 20 giây", "hằng ngày 08:00 · 23:00".
    schedule_label: str
    #: The Celery schedule object (``None`` for a job not on the beat).
    schedule: Any = None
    #: The beat options (queue, expires) and arguments - a manual run is sent
    #: the same way.
    options: Mapping[str, Any] | None = None
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] | None = None


def _seconds(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    run_every = getattr(value, "run_every", None)
    if isinstance(run_every, timedelta) and not hasattr(value, "hour"):
        return run_every.total_seconds()
    return None


def every_label(seconds: float) -> str:
    whole = int(seconds)
    if whole < 60:
        return f"mỗi {whole} giây"
    if whole < 3600:
        minutes = whole // 60
        return f"mỗi {minutes} phút"
    if whole % 86400 == 0:
        days = whole // 86400
        return "mỗi ngày" if days == 1 else f"mỗi {days} ngày"
    hours = whole / 3600
    return "mỗi giờ" if hours == 1 else f"mỗi {hours:g} giờ"


def _crontab_label(value: Any) -> str:
    """Daily crontabs read as Vietnam times ("hằng ngày 08:00 · 23:00"); the
    beat runs in UTC."""
    hours = sorted(getattr(value, "hour", []) or [])
    minutes = sorted(getattr(value, "minute", []) or [])
    if not hours or not minutes or len(hours) * len(minutes) > 6:
        return "theo lịch (crontab)"
    base = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC"))
    times = sorted(
        (base.replace(hour=h, minute=m).astimezone(_VN).strftime("%H:%M"))
        for h in hours
        for m in minutes
    )
    return "hằng ngày " + " · ".join(times)


def _crontab_period(value: Any) -> int:
    hours = getattr(value, "hour", None) or []
    return max(3600, 86400 // max(len(hours), 1))


def scheduled_jobs(beat_schedule: Mapping[str, Mapping[str, Any]]) -> list[ScheduledJob]:
    jobs: dict[str, ScheduledJob] = {}
    for entry in beat_schedule.values():
        key = str(entry.get("task") or "")
        if not key:
            continue
        schedule = entry.get("schedule")
        seconds = _seconds(schedule)
        if seconds is not None:
            period = int(seconds)
            label_text = every_label(seconds)
        else:
            period = _crontab_period(schedule)
            label_text = _crontab_label(schedule)
        label = label_for(key)
        jobs[key] = ScheduledJob(
            key=key,
            label=label.label,
            group=label.group,
            description=label.description,
            period_seconds=period,
            schedule_label=label_text,
            schedule=schedule,
            options=entry.get("options") or {},
            args=tuple(entry.get("args") or ()),
            kwargs=dict(entry.get("kwargs") or {}),
        )
    return list(jobs.values())


def unscheduled_job(key: str) -> ScheduledJob:
    label = label_for(key)
    return ScheduledJob(
        key=key,
        label=label.label,
        group=label.group,
        description=label.description,
        period_seconds=UNSCHEDULED_PERIOD_SECONDS,
        schedule_label="chạy khi được gọi",
    )


def expected_after(job: ScheduledJob, last_started_at: datetime, now: datetime) -> datetime | None:
    """When the run after ``last_started_at`` was due. ``None``: not on the beat."""
    if job.schedule is None:
        return None
    seconds = _seconds(job.schedule)
    if seconds is not None:
        return last_started_at + timedelta(seconds=seconds)
    estimate = getattr(job.schedule, "remaining_estimate", None)
    if callable(estimate):
        remaining = estimate(last_started_at)
        if isinstance(remaining, timedelta):
            return now + remaining
    return None


def beat_jobs() -> list[ScheduledJob]:
    """The live schedule. Imports the Celery app lazily (it reads settings)."""
    from meobot.tasks.celery_app import celery_app

    return scheduled_jobs(celery_app.conf.beat_schedule or {})


__all__ = [
    "ScheduledJob",
    "beat_jobs",
    "every_label",
    "expected_after",
    "scheduled_jobs",
    "unscheduled_job",
]
