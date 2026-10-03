"""Shared response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from meobot.application.health_service import ComponentHealth


class ErrorResponse(BaseModel):
    """Uniform error body for every handled failure."""

    code: str = Field(description="Stable machine-readable error code.")
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LivenessResponse(BaseModel):
    """``/health/live`` - the process is up. No dependencies are touched."""

    status: str = "alive"
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """``/health/ready`` - dependencies were probed just now."""

    status: str
    healthy: bool
    checked_at: datetime
    components: list[ComponentHealth]


class SystemInfoResponse(BaseModel):
    """Non-sensitive description of the running instance."""

    app_name: str
    app_env: str
    version: str
    timezone: str
    llm_provider: str
    telegram_configured: bool
    owner_configured: bool
    registered_tools: list[str]
    server_time_utc: datetime
    server_time_local: datetime
