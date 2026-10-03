"""Script Type Registry endpoints.

``POST`` is a development convenience (the API is localhost-only in milestone 1)
but it is not a back door: it validates the rubric through the same domain rules
the bot would use, and it writes an audit entry.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from meobot.api.deps import ActorDep, RequestIdDep, ScriptTypeServiceDep
from meobot.api.schemas.script_types import (
    CreateScriptTypeRequest,
    ScriptTypeListResponse,
    ScriptTypeResponse,
)

router = APIRouter(prefix="/api/v1/script-types", tags=["script-types"])


@router.get("", response_model=ScriptTypeListResponse, summary="List script types")
async def list_script_types(
    service: ScriptTypeServiceDep,
    active_only: bool = Query(default=True, description="Only return active script types."),
) -> ScriptTypeListResponse:
    """List script types in the registry."""
    script_types = await service.list_script_types(active_only=active_only)
    items = [ScriptTypeResponse.from_model(item) for item in script_types]
    return ScriptTypeListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=ScriptTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a script type with its first rubric version",
    responses={
        409: {"description": "The code is already used"},
        422: {"description": "The rubric violates a domain invariant"},
    },
)
async def create_script_type(
    payload: CreateScriptTypeRequest,
    service: ScriptTypeServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> ScriptTypeResponse:
    """Create a script type. Rubric weights must sum to exactly 100."""
    script_type = await service.create_script_type(
        actor=actor,
        request_id=request_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        review_rubric=payload.review_rubric,
        configuration=payload.configuration,
        prompt_template=payload.prompt_template,
    )
    return ScriptTypeResponse.from_model(script_type)
