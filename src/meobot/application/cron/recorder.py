"""Writing down what each run did, and deciding what follows.

Called from the worker's Celery signals (:mod:`meobot.tasks.cron_monitor`)
and from the watchdog. It never talks to Celery itself: :meth:`finish` returns
whether to queue a retry, and the caller queues it - which keeps this testable
with a plain database session.

The rules (chốt 27/10):

* a failed (or ``PARTIAL``) run opens an incident, or adds to the open one;
* the job's policy may queue a retry (a daily job: twice, 30 minutes apart);
* with nothing left to retry and enough failures in a row (a frequent job: 5)
  and the job's alerts on (``CronJob.notify``, 0080), the OWNER and the job's
  extra recipients are told - once per incident, the OWNER with a "Chạy lại"
  button;
* the next good run closes the incident quietly (only failures are sent,
  chốt 28/10);
* a job that has not run when it should have opens a ``LATE`` incident,
  reported the same way.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.cron.alerts import CronAlerts, department_heads
from meobot.application.cron.schedule import (
    ScheduledJob,
    expected_after,
    unscheduled_job,
)
from meobot.core.config import Settings
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cron import CronJob, CronJobIncident, CronJobRun
from meobot.domain.cron.models import (
    FREQUENT_SECONDS,
    CronIncidentKind,
    CronPolicy,
    CronRunStatus,
    CronTrigger,
    after_failure,
    default_policy,
    is_late,
    short_error,
)

#: History kept: failures (and slow jobs' successes) this long.
KEEP_RUN_DAYS = 90
#: A frequent job's successes, when it keeps them at all.
KEEP_FREQUENT_SUCCESS_HOURS = 24
#: A result bigger than this is not stored, only its status and message.
MAX_RESULT_CHARS = 4000


@dataclass(frozen=True, slots=True)
class RunOutcome:
    status: CronRunStatus
    message: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FinishResult:
    """What the caller should do next."""

    retry_in_seconds: int | None = None
    next_attempt: int = 1
    alerted: bool = False
    recovered: bool = False


def outcome_of(retval: object) -> RunOutcome:
    """A task that returned normally: ``SUCCESS``, unless it returned
    ``{"status": "FAILED" | "PARTIAL", "message": ...}``."""
    result = retval if isinstance(retval, dict) else None
    status = CronRunStatus.SUCCESS
    message: str | None = None
    if result is not None:
        raw = str(result.get("status") or "").upper()
        if raw in (CronRunStatus.FAILED.value, CronRunStatus.PARTIAL.value):
            status = CronRunStatus(raw)
        if result.get("message"):
            message = str(result["message"])
    if result is not None and len(repr(result)) > MAX_RESULT_CHARS:
        result = None
    return RunOutcome(
        status=status,
        message=message,
        error=message if status is not CronRunStatus.SUCCESS else None,
        result=_jsonable(result),
    )


def _jsonable(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        str(key): item if isinstance(item, str | int | float | bool) or item is None else str(item)
        for key, item in value.items()
    }


def tracks_start(job: ScheduledJob, trigger: CronTrigger) -> bool:
    """Slow jobs, retries and manual runs show "Đang chạy"; a job every 20
    seconds would only add a write per run for nothing."""
    return trigger is not CronTrigger.SCHEDULE or job.period_seconds >= 300


def _policy(job: CronJob) -> CronPolicy:
    return CronPolicy(
        retries=job.retries,
        retry_delay_seconds=job.retry_delay_seconds,
        alert_after=job.alert_after,
        keep_successes=job.keep_successes,
    )


class CronRecorder:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        jobs: Mapping[str, ScheduledJob] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._jobs = dict(jobs or {})

    def spec(self, key: str) -> ScheduledJob:
        return self._jobs.get(key) or unscheduled_job(key)

    # --- the job rows -------------------------------------------------------

    async def ensure(self, key: str) -> CronJob:
        job = await self._session.get(CronJob, key)
        if job is not None:
            return job
        spec = self.spec(key)
        policy = default_policy(spec.period_seconds)
        recipients = (
            await department_heads(self._session, spec.notify_department)
            if spec.notify_department
            else []
        )
        job = CronJob(
            key=key,
            enabled=True,
            retries=policy.retries,
            retry_delay_seconds=policy.retry_delay_seconds,
            alert_after=policy.alert_after,
            keep_successes=policy.keep_successes,
            consecutive_failures=0,
            notify=spec.notify,
            notify_user_ids=[str(user_id) for user_id in recipients],
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def sync_catalog(self) -> list[CronJob]:
        """A row for every job on the beat; the default policy follows a
        schedule change unless the OWNER set one by hand."""
        rows: list[CronJob] = []
        for key, spec in self._jobs.items():
            job = await self.ensure(key)
            if not job.policy_custom:
                policy = default_policy(spec.period_seconds)
                job.retries = policy.retries
                job.retry_delay_seconds = policy.retry_delay_seconds
                job.alert_after = policy.alert_after
                job.keep_successes = policy.keep_successes
            rows.append(job)
        await self._session.flush()
        return rows

    # --- one run ------------------------------------------------------------

    async def start(self, *, key: str, task_id: str | None, at: datetime) -> None:
        job = await self.ensure(key)
        if not job.enabled:
            return
        job.running_since = at
        job.running_task_id = task_id
        await self._session.flush()

    async def finish(
        self,
        *,
        key: str,
        task_id: str | None,
        trigger: CronTrigger,
        attempt: int,
        triggered_by: uuid.UUID | None,
        started_at: datetime,
        finished_at: datetime,
        outcome: RunOutcome,
    ) -> FinishResult:
        job = await self.ensure(key)
        if not job.enabled:
            return FinishResult()
        spec = self.spec(key)
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
        failed = outcome.status is not CronRunStatus.SUCCESS
        if job.running_task_id in (None, task_id):
            job.running_since = None
            job.running_task_id = None
        job.last_started_at = started_at
        job.last_finished_at = finished_at
        job.last_status = outcome.status
        job.last_duration_ms = duration_ms
        job.last_message = short_error(outcome.message or outcome.error) or None

        keep = failed or trigger is not CronTrigger.SCHEDULE or job.keep_successes
        if keep:
            self._session.add(
                CronJobRun(
                    job_key=key,
                    task_id=task_id,
                    trigger=trigger,
                    attempt=attempt,
                    triggered_by_user_id=triggered_by,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status=outcome.status,
                    message=(outcome.message or None) and outcome.message[:500],
                    error=(outcome.error or None) and outcome.error[:4000],
                    result=outcome.result,
                )
            )

        if not failed:
            job.consecutive_failures = 0
            job.last_success_at = finished_at
            recovered = await self._close_incident(job, spec, finished_at)
            await self._session.flush()
            return FinishResult(recovered=recovered)

        job.consecutive_failures += 1
        incident = await self._open_incident(job, CronIncidentKind.FAILED, finished_at)
        incident.failures += 1
        incident.last_error = short_error(outcome.error or outcome.message) or None
        decision = after_failure(
            policy=_policy(job), attempt=attempt, consecutive=job.consecutive_failures
        )
        alerted = False
        if decision.alert and incident.alerted_at is None and job.notify:
            await self._alert_failed(job, spec, incident, outcome, attempt)
            alerted = True
        await self._session.flush()
        return FinishResult(
            retry_in_seconds=decision.retry_in_seconds,
            next_attempt=decision.next_attempt,
            alerted=alerted,
        )

    # --- lateness -----------------------------------------------------------

    async def check_late(self, now: datetime | None = None) -> int:
        """Open (and report) a ``LATE`` incident for every enabled job whose
        next run is overdue. Returns how many were opened."""
        moment = now or utcnow()
        opened = 0
        for key, spec in self._jobs.items():
            job = await self.ensure(key)
            if not job.enabled or job.open_incident_id is not None or job.last_started_at is None:
                continue
            last = ensure_utc(job.last_started_at)
            if not is_late(
                last_started_at=last,
                expected_at=expected_after(spec, last, moment),
                now=moment,
            ):
                continue
            incident = await self._open_incident(job, CronIncidentKind.LATE, moment)
            incident.last_error = "Không chạy đúng lịch."
            if job.notify:
                await CronAlerts(self._session, self._settings).failed(
                    incident,
                    recipients=job.notify_user_ids,
                    job_label=spec.label,
                    problem=f"Không chạy đúng lịch ({spec.schedule_label})",
                    detail=f"Lần chạy gần nhất: {_vn(last)}. Có thể worker hoặc beat đang dừng.",
                )
                incident.alerted_at = moment
            opened += 1
        await self._session.flush()
        return opened

    def late_keys(self, jobs: list[CronJob], now: datetime) -> set[str]:
        """For the screen: computed on read, so a dead beat still shows."""
        late: set[str] = set()
        for job in jobs:
            spec = self._jobs.get(job.key)
            if spec is None or not job.enabled or job.last_started_at is None:
                continue
            last = ensure_utc(job.last_started_at)
            if is_late(last_started_at=last, expected_at=expected_after(spec, last, now), now=now):
                late.add(job.key)
        return late

    # --- housekeeping -------------------------------------------------------

    async def purge(self, now: datetime | None = None) -> int:
        moment = now or utcnow()
        old = await self._session.execute(
            delete(CronJobRun).where(CronJobRun.started_at < moment - timedelta(days=KEEP_RUN_DAYS))
        )
        frequent = [
            key for key, spec in self._jobs.items() if spec.period_seconds < FREQUENT_SECONDS
        ]
        recent = 0
        if frequent:
            result = await self._session.execute(
                delete(CronJobRun).where(
                    CronJobRun.job_key.in_(frequent),
                    CronJobRun.status == CronRunStatus.SUCCESS,
                    CronJobRun.trigger == CronTrigger.SCHEDULE,
                    CronJobRun.started_at < moment - timedelta(hours=KEEP_FREQUENT_SUCCESS_HOURS),
                )
            )
            recent = int(getattr(result, "rowcount", 0) or 0)
        return int(getattr(old, "rowcount", 0) or 0) + recent

    # --- incidents ----------------------------------------------------------

    async def _open_incident(
        self, job: CronJob, kind: CronIncidentKind, at: datetime
    ) -> CronJobIncident:
        if job.open_incident_id is not None:
            existing = await self._session.get(CronJobIncident, job.open_incident_id)
            if existing is not None and existing.closed_at is None:
                return existing
        incident = CronJobIncident(
            id=uuid.uuid4(), job_key=job.key, kind=kind, opened_at=at, failures=0
        )
        self._session.add(incident)
        job.open_incident_id = incident.id
        await self._session.flush()
        return incident

    async def _close_incident(self, job: CronJob, spec: ScheduledJob, at: datetime) -> bool:
        if job.open_incident_id is None:
            return False
        incident = await self._session.get(CronJobIncident, job.open_incident_id)
        job.open_incident_id = None
        if incident is None or incident.closed_at is not None:
            return False
        incident.closed_at = at
        # Only failures are sent (chốt 28/10): a reported incident closes
        # quietly, the screen shows it closed.
        return incident.alerted_at is not None

    async def _alert_failed(
        self,
        job: CronJob,
        spec: ScheduledJob,
        incident: CronJobIncident,
        outcome: RunOutcome,
        attempt: int,
    ) -> None:
        tried = f" · đã tự thử lại {attempt - 1} lần" if attempt > 1 else ""
        problem = (
            "Lỗi một phần" if outcome.status is CronRunStatus.PARTIAL else "Lỗi"
        ) + f" lúc {_vn(job.last_finished_at or utcnow())}"
        if job.consecutive_failures > 1:
            problem += f" · {job.consecutive_failures} lần liên tiếp"
        await CronAlerts(self._session, self._settings).failed(
            incident,
            recipients=job.notify_user_ids,
            job_label=spec.label,
            problem=problem + tried,
            detail=short_error(outcome.error or outcome.message, 500),
        )
        incident.alerted_at = utcnow()

    # --- the screen ---------------------------------------------------------

    async def jobs(self) -> list[CronJob]:
        return list(await self._session.scalars(select(CronJob).order_by(CronJob.key)))


_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _vn(moment: datetime) -> str:
    return ensure_utc(moment).astimezone(_VN).strftime("%d/%m/%Y %H:%M")


__all__ = ["CronRecorder", "FinishResult", "RunOutcome", "outcome_of", "tracks_start"]
