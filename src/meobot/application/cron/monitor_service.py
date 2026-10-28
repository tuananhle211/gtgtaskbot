"""The "Tác vụ định kỳ" screen and the "Chạy lại" button (web and Telegram).

Reading and re-running: the OWNER and an ADMIN. Changing a job's policy or
switching its monitoring off: the OWNER. Anybody else: 403 ``cron_forbidden``.

A re-run is queued through Celery like the beat would queue it (same queue,
same arguments), tagged ``MANUAL`` with who asked. It is refused while the
job is already running (409 ``cron_job_running``) so a double press - or a
press on two devices - never runs it twice at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.cron.recorder import CronRecorder
from meobot.application.cron.schedule import ScheduledJob
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cron import CronJob, CronJobIncident, CronJobRun
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.cron.models import (
    RUNNING_STALE_SECONDS,
    CronJobState,
    CronTrigger,
    job_state,
)
from meobot.domain.identity.models import Actor, Role

#: Celery message headers the worker reads back (:mod:`meobot.tasks.cron_monitor`).
HEADER_TRIGGER = "cron_trigger"
HEADER_ATTEMPT = "cron_attempt"
HEADER_USER = "cron_user"

#: Sends a task: ``(name, args, kwargs, headers, options) -> task id``.
Sender = Callable[[str, tuple[Any, ...], Mapping[str, Any], dict[str, Any], dict[str, Any]], str]


class CronForbiddenError(AuthorizationError):
    code = "cron_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu và quản trị viên xem được tác vụ định kỳ.",
            details={"reason": "cron_forbidden"},
        )


class CronJobNotFoundError(NotFoundError):
    code = "cron_job_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy tác vụ.", details={"reason": "cron_job_not_found"})


class CronJobRunningError(ConflictError):
    code = "cron_job_running"

    def __init__(self) -> None:
        super().__init__(
            "Tác vụ đang chạy, đợi lượt này xong rồi chạy lại.",
            details={"reason": "cron_job_running"},
        )


class CronPolicyError(ValidationError):
    code = "cron_policy_invalid"

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message, details={"reason": "cron_policy_invalid", "field": field})


#: How many extra people one job may alert.
MAX_RECIPIENTS = 20


@dataclass(frozen=True, slots=True)
class JobView:
    job: CronJob
    spec: ScheduledJob
    state: CronJobState
    late: bool
    open_incident: CronJobIncident | None
    #: The job's extra alert recipients still active, in the order chosen.
    recipients: tuple[User, ...] = ()


@dataclass(frozen=True, slots=True)
class RerunResult:
    task_id: str
    job: CronJob


def celery_sender(
    name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    headers: dict[str, Any],
    options: dict[str, Any],
) -> str:
    from meobot.tasks.celery_app import celery_app

    result = celery_app.send_task(
        name, args=list(args), kwargs=dict(kwargs), headers=headers, **options
    )
    return str(result.id)


def may_view(actor: Actor) -> bool:
    return actor.role in (Role.OWNER, Role.ADMIN)


class CronMonitorService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        jobs: Mapping[str, ScheduledJob],
        sender: Sender = celery_sender,
    ) -> None:
        self._session = session
        self._settings = settings
        self._jobs = dict(jobs)
        self._recorder = CronRecorder(session, settings, jobs=jobs)
        self._sender = sender

    @staticmethod
    def _require_view(actor: Actor) -> None:
        if not may_view(actor):
            raise CronForbiddenError()

    @staticmethod
    def _require_owner(actor: Actor) -> None:
        if actor.role is not Role.OWNER:
            raise CronForbiddenError()

    # --- reading ------------------------------------------------------------

    async def overview(self, *, actor: Actor) -> list[JobView]:
        self._require_view(actor)
        rows = await self._recorder.sync_catalog()
        known = {row.key for row in rows}
        # Jobs seen running that are not (or no longer) on the beat.
        rows.extend(row for row in await self._recorder.jobs() if row.key not in known)
        now = utcnow()
        late = self._recorder.late_keys(rows, now)
        incident_ids = [row.open_incident_id for row in rows if row.open_incident_id]
        incidents = (
            {
                incident.id: incident
                for incident in await self._session.scalars(
                    select(CronJobIncident).where(CronJobIncident.id.in_(incident_ids))
                )
            }
            if incident_ids
            else {}
        )
        wanted = {value for row in rows for value in row.notify_user_ids or []}
        people = {
            str(user.id): user
            for user in await self._session.scalars(
                select(User).where(
                    User.id.in_(_uuids(wanted)),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        }
        views = [
            JobView(
                recipients=tuple(
                    people[value] for value in row.notify_user_ids or [] if value in people
                ),
                job=row,
                spec=self._recorder.spec(row.key),
                state=job_state(
                    enabled=row.enabled,
                    last_status=row.last_status,
                    running_since=_utc(row.running_since),
                    late=row.key in late,
                    now=now,
                ),
                late=row.key in late,
                open_incident=incidents.get(row.open_incident_id) if row.open_incident_id else None,
            )
            for row in rows
        ]
        views.sort(key=lambda view: (view.spec.group, view.spec.label))
        return views

    async def history(
        self, *, actor: Actor, key: str, limit: int = 50
    ) -> tuple[JobView, list[CronJobRun], list[CronJobIncident]]:
        self._require_view(actor)
        view = await self._view(key)
        runs = list(
            await self._session.scalars(
                select(CronJobRun)
                .where(CronJobRun.job_key == key)
                .order_by(CronJobRun.started_at.desc())
                .limit(limit)
            )
        )
        incidents = list(
            await self._session.scalars(
                select(CronJobIncident)
                .where(CronJobIncident.job_key == key)
                .order_by(CronJobIncident.opened_at.desc())
                .limit(20)
            )
        )
        return view, runs, incidents

    async def _view(self, key: str) -> JobView:
        for view in await self.overview(actor=_SYSTEM):
            if view.job.key == key:
                return view
        raise CronJobNotFoundError()

    # --- re-running ---------------------------------------------------------

    async def rerun(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        key: str,
        incident_id: uuid.UUID | None = None,
    ) -> RerunResult:
        self._require_view(actor)
        job = await self._session.get(CronJob, key)
        if job is None and key not in self._jobs:
            raise CronJobNotFoundError()
        if job is None:
            job = await self._recorder.ensure(key)
        now = utcnow()
        running = _utc(job.running_since)
        if running is not None and now - running < timedelta(seconds=RUNNING_STALE_SECONDS):
            raise CronJobRunningError()
        spec = self._recorder.spec(key)
        options = {name: value for name, value in (spec.options or {}).items() if name == "queue"}
        task_id = self._sender(
            key,
            spec.args,
            spec.kwargs or {},
            {
                HEADER_TRIGGER: CronTrigger.MANUAL.value,
                HEADER_ATTEMPT: 1,
                HEADER_USER: str(actor.user_id) if actor.user_id else "",
            },
            options,
        )
        job.running_since = now
        job.running_task_id = task_id
        target = incident_id or job.open_incident_id
        if target is not None:
            incident = await self._session.get(CronJobIncident, target)
            if incident is not None and incident.job_key == key:
                incident.rerun_requested_at = now
                incident.rerun_requested_by_user_id = actor.user_id
        await self._session.flush()
        await AuditService(self._session).record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.CRON_JOB_RERUN.value,
            result=AuditResult.SUCCESS,
            entity_type="cron_job",
            entity_id=key,
            after_data={"task_id": task_id},
        )
        return RerunResult(task_id=task_id, job=job)

    async def incident_job(self, incident_id: uuid.UUID) -> str | None:
        incident = await self._session.get(CronJobIncident, incident_id)
        return incident.job_key if incident is not None else None

    # --- the policy ---------------------------------------------------------

    async def update(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        key: str,
        enabled: bool | None = None,
        retries: int | None = None,
        retry_delay_minutes: int | None = None,
        alert_after: int | None = None,
        keep_successes: bool | None = None,
        notify: bool | None = None,
        notify_user_ids: list[uuid.UUID] | None = None,
    ) -> CronJob:
        self._require_owner(actor)
        job = await self._session.get(CronJob, key)
        if job is None:
            raise CronJobNotFoundError()
        before = _snap(job)
        if retries is not None:
            if not 0 <= retries <= 5:
                raise CronPolicyError("Số lần tự thử lại từ 0 đến 5.", "retries")
            job.retries = retries
        if retry_delay_minutes is not None:
            if not 1 <= retry_delay_minutes <= 720:
                raise CronPolicyError(
                    "Khoảng cách thử lại từ 1 đến 720 phút.", "retry_delay_minutes"
                )
            job.retry_delay_seconds = retry_delay_minutes * 60
        if alert_after is not None:
            if not 1 <= alert_after <= 100:
                raise CronPolicyError("Báo sau từ 1 đến 100 lần lỗi liên tiếp.", "alert_after")
            job.alert_after = alert_after
        if keep_successes is not None:
            job.keep_successes = keep_successes
        if notify is not None:
            job.notify = notify
        if notify_user_ids is not None:
            job.notify_user_ids = await self._recipients(notify_user_ids)
        if any(value is not None for value in (retries, retry_delay_minutes, alert_after)):
            job.policy_custom = True
        if enabled is not None:
            job.enabled = enabled
            if not enabled:
                job.running_since = None
                job.running_task_id = None
        await self._session.flush()
        await AuditService(self._session).record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.CRON_JOB_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="cron_job",
            entity_id=key,
            before_data=before,
            after_data=_snap(job),
        )
        return job

    async def _recipients(self, user_ids: list[uuid.UUID]) -> list[str]:
        """The chosen extra recipients, deduplicated, each an active account
        (else 422 ``cron_policy_invalid`` on ``notify_user_ids``)."""
        chosen = list(dict.fromkeys(user_ids))
        if len(chosen) > MAX_RECIPIENTS:
            raise CronPolicyError(
                f"Tối đa {MAX_RECIPIENTS} người nhận thông báo.", "notify_user_ids"
            )
        if not chosen:
            return []
        found = set(
            await self._session.scalars(
                select(User.id).where(
                    User.id.in_(chosen),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        )
        if len(found) != len(chosen):
            raise CronPolicyError("Người nhận phải là tài khoản đang hoạt động.", "notify_user_ids")
        return [str(user_id) for user_id in chosen]

    async def people(self, *, actor: Actor) -> list[User]:
        """Who may be chosen as a recipient: every active account (the OWNER's
        screen only)."""
        if actor.role is not Role.OWNER:
            return []
        return list(
            await self._session.scalars(
                select(User)
                .where(User.status == UserStatus.ACTIVE, User.deleted_at.is_(None))
                .order_by(User.full_name)
            )
        )


def _uuids(values: object) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    for value in values if isinstance(values, list | set | tuple) else []:
        try:
            found.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return found


def _snap(job: CronJob) -> dict[str, object]:
    return {
        "notify": job.notify,
        "notify_user_ids": list(job.notify_user_ids or []),
        "enabled": job.enabled,
        "retries": job.retries,
        "retry_delay_seconds": job.retry_delay_seconds,
        "alert_after": job.alert_after,
        "keep_successes": job.keep_successes,
    }


def _utc(value: datetime | None) -> datetime | None:
    return ensure_utc(value) if value is not None else None


#: The service's own reads (``_view``) - never a person.
_SYSTEM = Actor(user_id=None, full_name="cron-monitor", role=Role.OWNER)


__all__ = [
    "HEADER_ATTEMPT",
    "HEADER_TRIGGER",
    "HEADER_USER",
    "CronForbiddenError",
    "CronJobNotFoundError",
    "CronJobRunningError",
    "CronMonitorService",
    "CronPolicyError",
    "JobView",
    "RerunResult",
    "celery_sender",
    "may_view",
]
