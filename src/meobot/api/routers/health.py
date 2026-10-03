"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from meobot import __version__
from meobot.api.deps import HealthServiceDep, SettingsDep
from meobot.api.schemas.common import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def live(settings: SettingsDep) -> LivenessResponse:
    """Return 200 while the process is running.

    Touches no dependency on purpose: a database outage must not cause the
    container to be killed and restarted in a loop.
    """
    return LivenessResponse(status="alive", service=settings.app_name, version=__version__)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "A required dependency is unavailable"}},
)
async def ready(health_service: HealthServiceDep, response: Response) -> ReadinessResponse:
    """Probe PostgreSQL and Redis; report Celery workers as informational.

    Returns HTTP 503 when a required dependency is down so a load balancer or
    ``docker compose`` healthcheck can act on it.
    """
    report = await health_service.check()
    if not report.healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if report.healthy else "degraded",
        healthy=report.healthy,
        checked_at=report.checked_at,
        components=report.components,
    )
