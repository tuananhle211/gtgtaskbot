""" "Kết nối AI" (0064): the AI services the app may call, and for what.

The OWNER's alone (403 ``ai_forbidden``). API keys go in, never out.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, SettingsDep, get_session
from meobot.api.schemas.ai import (
    AiConnectionResponse,
    AiOverviewResponse,
    AiRoutesRequest,
    AiTestResponse,
    CreateAiConnectionRequest,
    UpdateAiConnectionRequest,
)
from meobot.application.ai.connection_service import AiConnectionService
from meobot.application.audit_service import AuditService
from meobot.core.config import Settings

router = APIRouter(prefix="/api/ai", tags=["ai"])


def get_ai_service(
    session: Annotated[AsyncSession, Depends(get_session)], settings: SettingsDep
) -> AiConnectionService:
    return AiConnectionService(session, AuditService(session), settings)


ServiceDep = Annotated[AiConnectionService, Depends(get_ai_service)]


def _refresh(request: Request) -> None:
    """This process reads the routes again at once; the others within seconds."""
    refresh = getattr(getattr(request.app.state, "llm_provider", None), "refresh", None)
    if callable(refresh):
        refresh()


async def _overview(
    actor: CurrentActorDep, service: AiConnectionService, settings: Settings
) -> AiOverviewResponse:
    connections = await service.connections(actor=actor)
    routes = await service.routes(actor=actor)
    return AiOverviewResponse(
        connections=[AiConnectionResponse.from_row(row) for row in connections],
        routes=AiOverviewResponse.routes_of(routes),
        kinds=AiOverviewResponse.kind_list(),
        secret_ready=service.secret_ready(),
        env_provider=settings.llm_provider,
        env_model=settings.llm_model,
    )


@router.get("", response_model=AiOverviewResponse)
async def overview(
    actor: CurrentActorDep, service: ServiceDep, settings: SettingsDep
) -> AiOverviewResponse:
    return await _overview(actor, service, settings)


@router.post("/connections", response_model=AiConnectionResponse, status_code=201)
async def create_connection(
    body: CreateAiConnectionRequest,
    request: Request,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> AiConnectionResponse:
    row = await service.create(
        actor=actor,
        request_id=request_id,
        name=body.name,
        kind=body.kind,
        model=body.model,
        api_key=body.api_key,
        base_url=body.base_url,
    )
    _refresh(request)
    return AiConnectionResponse.from_row(row)


@router.patch("/connections/{connection_id}", response_model=AiConnectionResponse)
async def update_connection(
    connection_id: uuid.UUID,
    body: UpdateAiConnectionRequest,
    request: Request,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> AiConnectionResponse:
    """A blank ``api_key`` keeps the stored one; ``base_url: null`` goes back
    to the kind's own endpoint."""
    row = await service.update(
        actor=actor,
        request_id=request_id,
        connection_id=connection_id,
        name=body.name,
        model=body.model,
        base_url=body.base_url,
        clear_base_url="base_url" in body.model_fields_set and body.base_url is None,
        api_key=body.api_key,
        active=body.active,
    )
    _refresh(request)
    return AiConnectionResponse.from_row(row)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    request: Request,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> Response:
    await service.delete(actor=actor, request_id=request_id, connection_id=connection_id)
    _refresh(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/connections/{connection_id}/test", response_model=AiTestResponse)
async def test_connection(
    connection_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> AiTestResponse:
    """One tiny JSON request through the same client the app uses. A failure
    is an answer (``ok: false`` + why), not an error."""
    outcome = await service.test(actor=actor, request_id=request_id, connection_id=connection_id)
    row = await service.connection(connection_id)
    return AiTestResponse(
        ok=outcome.ok,
        message=outcome.message,
        latency_ms=outcome.latency_ms,
        connection=AiConnectionResponse.from_row(row),
    )


@router.put("/routes", response_model=AiOverviewResponse)
async def set_routes(
    body: AiRoutesRequest,
    request: Request,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
    settings: SettingsDep,
) -> AiOverviewResponse:
    await service.set_routes(actor=actor, request_id=request_id, routes=body.routes)
    _refresh(request)
    return await _overview(actor, service, settings)


__all__ = ["router"]
