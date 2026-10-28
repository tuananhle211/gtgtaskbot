""" "Tác vụ định kỳ" (0077): how every scheduled job is doing, and "Chạy lại".

Read + re-run: the OWNER and an ADMIN; policy edits: the OWNER
(403 ``cron_forbidden``). A re-run of a job still running is 409
``cron_job_running``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, SettingsDep, get_session
from meobot.api.schemas.cron import (
    CronIncidentResponse,
    CronJobHistoryResponse,
    CronJobResponse,
    CronOverviewResponse,
    CronRerunResponse,
    CronRunResponse,
    UpdateCronJobRequest,
)
from meobot.application.cron.monitor_service import CronMonitorService
from meobot.application.cron.schedule import beat_jobs
from meobot.core.time import utcnow
from meobot.domain.identity.models import Role

router = APIRouter(prefix="/api/cron", tags=["cron"])


def get_cron_service(
    session: Annotated[AsyncSession, Depends(get_session)], settings: SettingsDep
) -> CronMonitorService:
    return CronMonitorService(session, settings, jobs={job.key: job for job in beat_jobs()})


ServiceDep = Annotated[CronMonitorService, Depends(get_cron_service)]


@router.get("", response_model=CronOverviewResponse)
async def overview(actor: CurrentActorDep, service: ServiceDep) -> CronOverviewResponse:
    views = await service.overview(actor=actor)
    return CronOverviewResponse.build(
        utcnow(),
        views,
        can_manage=actor.role is Role.OWNER,
        people=await service.people(actor=actor),
    )


@router.get("/jobs/{key}", response_model=CronJobHistoryResponse)
async def job_history(
    key: str,
    actor: CurrentActorDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CronJobHistoryResponse:
    view, runs, incidents = await service.history(actor=actor, key=key, limit=limit)
    return CronJobHistoryResponse(
        job=CronJobResponse.from_view(view),
        runs=[CronRunResponse.from_row(row) for row in runs],
        incidents=[CronIncidentResponse.from_row(row) for row in incidents],
    )


@router.post("/jobs/{key}/run", response_model=CronRerunResponse, status_code=202)
async def rerun_job(
    key: str, actor: CurrentActorDep, request_id: RequestIdDep, service: ServiceDep
) -> CronRerunResponse:
    """Queue the job now, the way the beat would. 409 while it is running."""
    result = await service.rerun(actor=actor, request_id=request_id, key=key)
    view, _runs, _incidents = await service.history(actor=actor, key=key, limit=1)
    return CronRerunResponse(task_id=result.task_id, job=CronJobResponse.from_view(view))


@router.patch("/jobs/{key}", response_model=CronJobResponse)
async def update_job(
    key: str,
    body: UpdateCronJobRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> CronJobResponse:
    await service.update(
        actor=actor,
        request_id=request_id,
        key=key,
        enabled=body.enabled,
        retries=body.retries,
        retry_delay_minutes=body.retry_delay_minutes,
        alert_after=body.alert_after,
        keep_successes=body.keep_successes,
        notify=body.notify,
        notify_user_ids=body.notify_user_ids,
    )
    view, _runs, _incidents = await service.history(actor=actor, key=key, limit=1)
    return CronJobResponse.from_view(view)


__all__ = ["router"]
