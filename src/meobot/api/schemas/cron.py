"""Wire shapes for "Tác vụ định kỳ" (``/api/cron``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meobot.application.cron.monitor_service import JobView
from meobot.db.models.cron import CronJobIncident, CronJobRun
from meobot.domain.cron.models import (
    RUN_STATUS_LABELS,
    STATE_LABELS,
    TRIGGER_LABELS,
    CronJobState,
)


class CronIncidentResponse(BaseModel):
    id: uuid.UUID
    kind: str
    opened_at: datetime
    closed_at: datetime | None
    failures: int
    last_error: str | None
    alerted_at: datetime | None
    recovery_alerted_at: datetime | None
    rerun_requested_at: datetime | None

    @classmethod
    def from_row(cls, row: CronJobIncident) -> CronIncidentResponse:
        return cls(
            id=row.id,
            kind=row.kind.value,
            opened_at=row.opened_at,
            closed_at=row.closed_at,
            failures=row.failures,
            last_error=row.last_error,
            alerted_at=row.alerted_at,
            recovery_alerted_at=row.recovery_alerted_at,
            rerun_requested_at=row.rerun_requested_at,
        )


class CronJobResponse(BaseModel):
    key: str
    label: str
    group: str
    description: str
    schedule_label: str
    state: str
    state_label: str
    enabled: bool
    retries: int
    retry_delay_minutes: int
    alert_after: int
    keep_successes: bool
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_status: str | None
    last_status_label: str | None
    last_message: str | None
    last_duration_ms: int | None
    last_success_at: datetime | None
    consecutive_failures: int
    running_since: datetime | None
    open_incident: CronIncidentResponse | None

    @classmethod
    def from_view(cls, view: JobView) -> CronJobResponse:
        job = view.job
        return cls(
            key=job.key,
            label=view.spec.label,
            group=view.spec.group,
            description=view.spec.description,
            schedule_label=view.spec.schedule_label,
            state=view.state.value,
            state_label=STATE_LABELS[view.state],
            enabled=job.enabled,
            retries=job.retries,
            retry_delay_minutes=job.retry_delay_seconds // 60,
            alert_after=job.alert_after,
            keep_successes=job.keep_successes,
            last_started_at=job.last_started_at,
            last_finished_at=job.last_finished_at,
            last_status=job.last_status.value if job.last_status else None,
            last_status_label=RUN_STATUS_LABELS[job.last_status] if job.last_status else None,
            last_message=job.last_message,
            last_duration_ms=job.last_duration_ms,
            last_success_at=job.last_success_at,
            consecutive_failures=job.consecutive_failures,
            running_since=job.running_since if view.state is CronJobState.RUNNING else None,
            open_incident=CronIncidentResponse.from_row(view.open_incident)
            if view.open_incident
            else None,
        )


class CronSummaryResponse(BaseModel):
    total: int
    ok: int
    failing: int
    late: int
    running: int
    off: int


class CronOverviewResponse(BaseModel):
    now: datetime
    summary: CronSummaryResponse
    jobs: list[CronJobResponse]
    #: The OWNER may change policies; an ADMIN reads and re-runs.
    can_manage: bool

    @classmethod
    def build(
        cls, now: datetime, views: list[JobView], *, can_manage: bool
    ) -> CronOverviewResponse:
        def count(*states: CronJobState) -> int:
            return sum(1 for view in views if view.state in states)

        return cls(
            now=now,
            summary=CronSummaryResponse(
                total=len(views),
                ok=count(CronJobState.OK),
                failing=count(CronJobState.FAILING, CronJobState.PARTIAL),
                late=count(CronJobState.LATE),
                running=count(CronJobState.RUNNING),
                off=count(CronJobState.OFF),
            ),
            jobs=[CronJobResponse.from_view(view) for view in views],
            can_manage=can_manage,
        )


class CronRunResponse(BaseModel):
    id: uuid.UUID
    trigger: str
    trigger_label: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    status: str
    status_label: str
    message: str | None
    error: str | None

    @classmethod
    def from_row(cls, row: CronJobRun) -> CronRunResponse:
        return cls(
            id=row.id,
            trigger=row.trigger.value,
            trigger_label=TRIGGER_LABELS[row.trigger],
            attempt=row.attempt,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            status=row.status.value,
            status_label=RUN_STATUS_LABELS[row.status],
            message=row.message,
            error=row.error,
        )


class CronJobHistoryResponse(BaseModel):
    job: CronJobResponse
    runs: list[CronRunResponse]
    incidents: list[CronIncidentResponse]


class CronRerunResponse(BaseModel):
    task_id: str
    job: CronJobResponse


class UpdateCronJobRequest(BaseModel):
    enabled: bool | None = None
    retries: int | None = Field(default=None, ge=0, le=5)
    retry_delay_minutes: int | None = Field(default=None, ge=1, le=720)
    alert_after: int | None = Field(default=None, ge=1, le=100)
    keep_successes: bool | None = None


__all__ = [
    "CronJobHistoryResponse",
    "CronJobResponse",
    "CronOverviewResponse",
    "CronRerunResponse",
    "UpdateCronJobRequest",
]
