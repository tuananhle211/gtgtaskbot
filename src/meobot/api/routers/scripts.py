"""Script endpoints.

Every route delegates to :class:`~meobot.application.script_service.ScriptService`
or :class:`~meobot.application.script_review_service.ScriptReviewService`; no
business rule is implemented here. In particular, ``approve-production`` grants
production permission and nothing else - there is no publishing endpoint in
this milestone, by design.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from meobot.api.deps import (
    ActorDep,
    LLMProviderDep,
    RequestIdDep,
    ScriptReviewServiceDep,
    ScriptServiceDep,
)
from meobot.api.schemas.scripts import (
    ApproveProductionRequest,
    RequestRevisionRequest,
    ScriptApprovalResponse,
    ScriptDetailResponse,
    ScriptListResponse,
    ScriptResponse,
    ScriptReviewResponse,
    ScriptVersionResponse,
)

router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])


@router.get("", response_model=ScriptListResponse, summary="List pending scripts")
async def list_scripts(
    service: ScriptServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ScriptListResponse:
    """List scripts waiting for a review or a human decision."""
    scripts = await service.list_pending(limit=limit, offset=offset)
    total = await service.count_pending()
    return ScriptListResponse(
        items=[ScriptResponse.from_model(item) for item in scripts],
        total=total,
    )


@router.get(
    "/{script_id}",
    response_model=ScriptDetailResponse,
    summary="Get one script with its current version and latest review",
    responses={404: {"description": "No such script"}},
)
async def get_script(script_id: uuid.UUID, service: ScriptServiceDep) -> ScriptDetailResponse:
    """Return a script together with the version and review currently on file."""
    detail = await service.detail(script_id)
    return ScriptDetailResponse.from_detail(detail)


@router.get(
    "/{script_id}/versions",
    response_model=list[ScriptVersionResponse],
    summary="List every version of a script",
)
async def list_versions(
    script_id: uuid.UUID,
    service: ScriptServiceDep,
) -> list[ScriptVersionResponse]:
    """Full version history, oldest first. Versions are never rewritten."""
    await service.get(script_id)
    versions = await service.list_versions(script_id)
    return [ScriptVersionResponse.from_model(version) for version in versions]


@router.get(
    "/{script_id}/reviews",
    response_model=list[ScriptReviewResponse],
    summary="List every review of a script",
)
async def list_reviews(
    script_id: uuid.UUID,
    service: ScriptServiceDep,
    reviews: ScriptReviewServiceDep,
) -> list[ScriptReviewResponse]:
    """Review history, newest first. Each review names the version it judged."""
    await service.get(script_id)
    stored = await reviews.list_reviews(script_id)
    return [ScriptReviewResponse.from_model(review) for review in stored]


@router.get(
    "/{script_id}/approvals",
    response_model=list[ScriptApprovalResponse],
    summary="List approval history",
)
async def list_approvals(
    script_id: uuid.UUID,
    service: ScriptServiceDep,
) -> list[ScriptApprovalResponse]:
    """Approval and revision history, newest first."""
    await service.get(script_id)
    approvals = await service.list_approvals(script_id)
    return [ScriptApprovalResponse.from_model(approval) for approval in approvals]


@router.post(
    "/{script_id}/review",
    response_model=ScriptReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Review the current version now",
    responses={
        404: {"description": "No such script"},
        400: {"description": "The LLM provider failed"},
    },
)
async def review_script(
    script_id: uuid.UUID,
    service: ScriptServiceDep,
    reviews: ScriptReviewServiceDep,
    llm: LLMProviderDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> ScriptReviewResponse:
    """Run a review synchronously.

    The Telegram path enqueues ``scripts.review_script`` instead; this endpoint
    exists for development and for scripting a backfill.
    """
    await service.queue_for_review(script_id=script_id)
    review = await reviews.review_script(
        actor=actor,
        request_id=request_id,
        script_id=script_id,
    )
    return ScriptReviewResponse.from_model(review)


@router.post(
    "/{script_id}/approve-production",
    response_model=ScriptApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve the current version for production (not for publishing)",
    responses={
        403: {"description": "The actor may not approve scripts"},
        404: {"description": "No such script"},
        409: {"description": "The version changed since it was read"},
        400: {"description": "The workflow state forbids approval"},
    },
)
async def approve_production(
    script_id: uuid.UUID,
    payload: ApproveProductionRequest,
    service: ScriptServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> ScriptApprovalResponse:
    """Grant permission to film. It grants nothing else."""
    approval = await service.approve_for_production(
        actor=actor,
        request_id=request_id,
        script_id=script_id,
        expected_version_id=payload.expected_version_id,
        comment=payload.comment,
        require_review=payload.require_review,
    )
    return ScriptApprovalResponse.from_model(approval)


@router.post(
    "/{script_id}/request-revision",
    response_model=ScriptApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a script back for revision",
    responses={
        403: {"description": "The actor may not decide on scripts"},
        404: {"description": "No such script"},
        400: {"description": "The workflow state forbids the transition"},
    },
)
async def request_revision(
    script_id: uuid.UUID,
    payload: RequestRevisionRequest,
    service: ScriptServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> ScriptApprovalResponse:
    """Record a revision request with an optional comment."""
    record = await service.request_revision(
        actor=actor,
        request_id=request_id,
        script_id=script_id,
        comment=payload.comment,
        expected_version_id=payload.expected_version_id,
    )
    return ScriptApprovalResponse.from_model(record)
