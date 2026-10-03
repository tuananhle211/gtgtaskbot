"""Internal system information."""

from __future__ import annotations

from fastapi import APIRouter, Request

from meobot import __version__
from meobot.api.deps import SettingsDep
from meobot.api.schemas.common import SystemInfoResponse
from meobot.core.time import to_local, utcnow
from meobot.tools.base import ToolRegistry

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/info", response_model=SystemInfoResponse, summary="Runtime information")
async def system_info(request: Request, settings: SettingsDep) -> SystemInfoResponse:
    """Report non-sensitive runtime facts.

    Nothing secret is exposed: only whether a credential is *configured*, never
    its value.
    """
    registry: ToolRegistry = request.app.state.tool_registry
    now = utcnow()
    return SystemInfoResponse(
        app_name=settings.app_name,
        app_env=settings.app_env,
        version=__version__,
        timezone=settings.app_timezone,
        llm_provider=settings.llm_provider,
        telegram_configured=settings.telegram_enabled,
        owner_configured=settings.meobot_owner_telegram_id is not None,
        registered_tools=registry.names,
        server_time_utc=now,
        server_time_local=to_local(now, settings.timezone),
    )
