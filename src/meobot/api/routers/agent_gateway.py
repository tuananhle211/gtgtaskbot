"""APM figures for the Hermes agents (2026-11-09).

* ``GET  /api/agents/gateway/areas`` - what the calling agent may read;
* ``POST /api/agents/gateway/reports/{area}`` - one report (``{month, day,
  day_to, person, department, state}`` as the area takes them).

No web session: brain-mcp presents ``Authorization: Bearer <AGENT_GATEWAY_TOKEN>``
and ``X-Agent: <slug>``; the client IP (``CF-Connecting-IP`` behind Cloudflare)
must be in ``AGENT_GATEWAY_ALLOWED_IPS`` when that is set. No token configured
= 404, as if the door did not exist. The OWNER ticks what each agent reads:
``GET /api/agents/admin/data``, ``PUT /api/agents/admin/data/{slug}``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SettingsDep,
    UnitMembershipDep,
    get_session,
)
from meobot.application.agents.access import agent_access
from meobot.application.agents.data_gateway import (
    AgentDataService,
    GatewayAreaError,
    GatewayClosedError,
    GatewayRefusedError,
    allowed_ip,
    token_ok,
)
from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.db.models.agent_chat import AgentProfile
from meobot.domain.agents.data import DATA_AREA_INFO, DataArea

router = APIRouter(prefix="/api/agents", tags=["agents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _error(
    status: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": {"reason": code, **(details or {})},
            }
        },
    )


def _area_rows(areas: tuple[DataArea, ...] | list[DataArea]) -> list[dict[str, str]]:
    return [
        {
            "key": area.value,
            "label": DATA_AREA_INFO[area][0],
            "description": DATA_AREA_INFO[area][1],
        }
        for area in areas
    ]


def client_ip(request: Request) -> str | None:
    """Cloudflare's view of the caller, else the socket's (local work)."""
    forwarded = request.headers.get("cf-connecting-ip")
    if forwarded:
        return forwarded.strip()
    return request.client.host if request.client else None


async def _caller(
    request: Request, session: AsyncSession, settings: Settings
) -> AgentProfile | JSONResponse:
    try:
        if not token_ok(settings, request.headers.get("authorization")):
            return _error(401, "gateway_unauthorized", "Sai khoá.")
    except GatewayClosedError:
        return _error(404, "not_found", "Not Found")
    if not allowed_ip(settings, client_ip(request)):
        return _error(403, "gateway_ip_forbidden", "Địa chỉ này không được gọi.")
    try:
        return await AgentDataService(session, settings).caller(request.headers.get("x-agent"))
    except GatewayRefusedError as exc:
        return _error(403, exc.code, str(exc))


@router.get("/gateway/areas")
async def gateway_areas(request: Request, session: SessionDep, settings: SettingsDep) -> Any:
    agent = await _caller(request, session, settings)
    if isinstance(agent, JSONResponse):
        return agent
    areas = await AgentDataService(session, settings).areas_of(agent.slug)
    return {"agent": agent.slug, "name": agent.name, "areas": _area_rows(areas)}


@router.post("/gateway/reports/{area}")
async def gateway_report(
    area: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    request_id: RequestIdDep,
    arguments: Annotated[dict[str, Any] | None, Body()] = None,
) -> Any:
    agent = await _caller(request, session, settings)
    if isinstance(agent, JSONResponse):
        return agent
    service = AgentDataService(session, settings, AuditService(session))
    try:
        return await service.report(agent, area, arguments or {}, request_id=request_id)
    except GatewayAreaError as exc:
        status = 403 if exc.code == "data_area_forbidden" else 422
        return _error(status, exc.code, str(exc), exc.details)


class AgentDataAreasRequest(BaseModel):
    areas: list[str] = Field(default_factory=list, max_length=20)


@router.get("/admin/data")
async def admin_data(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    """(OWNER) what each agent reads of APM."""
    agent_access(membership).require_manage()
    book = await AgentDataService(session, settings).book()
    return {
        "areas": _area_rows(list(DataArea)),
        "agents": {slug: [area.value for area in areas] for slug, areas in book.items()},
        "gateway_configured": bool(
            settings.agent_gateway_token and settings.agent_gateway_token.get_secret_value()
        ),
    }


@router.put("/admin/data/{slug}")
async def admin_set_data(
    slug: str,
    body: AgentDataAreasRequest,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """(OWNER) tick the areas an agent reads."""
    service = AgentDataService(session, settings, AuditService(session))
    areas = await service.set_areas(
        agent_access(membership), actor, slug, body.areas, request_id=request_id
    )
    return {"agent": slug, "areas": [area.value for area in areas]}


__all__ = ["client_ip", "router"]
