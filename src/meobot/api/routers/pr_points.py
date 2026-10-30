""" "Điểm PR" (0085): task codes for a PR piece's Nội dung / Sản xuất, and the
Trưởng phòng PR's decisions. Under ``/api/performance/pr`` - not a PR route:
the PR workflow is only read (see :mod:`meobot.application.performance.pr_points`)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, UnitMembershipDep, get_session
from meobot.api.schemas.pr_points import (
    PrCatalogResponse,
    PrClaimRequest,
    PrClaimResponse,
    PrContentPointsResponse,
    PrDecisionRequest,
)
from meobot.api.schemas.units import DesignCodeResponse, TaskFormResponse
from meobot.application.audit_service import AuditService
from meobot.application.performance.pr_points import PrPointService, in_pr
from meobot.application.units.directory import UnitDirectoryService
from meobot.domain.performance.errors import PrPointsForbiddenError
from meobot.domain.units.models import UnitCode

router = APIRouter(prefix="/api/performance/pr", tags=["performance"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> PrPointService:
    return PrPointService(session, AuditService(session))


@router.get("/catalog", response_model=PrCatalogResponse)
async def catalog(session: SessionDep, membership: UnitMembershipDep) -> PrCatalogResponse:
    if not in_pr(membership):
        raise PrPointsForbiddenError("Chỉ thành viên PR xem được danh mục điểm PR.")
    found = await _service(session).catalogue()
    settings = await UnitDirectoryService(session).settings(UnitCode.ADS)
    return PrCatalogResponse(
        phases={
            phase: [DesignCodeResponse.from_row(row) for row in rows]
            for phase, rows in found.codes.items()
        },
        forms=[TaskFormResponse.model_validate(form) for form in settings.task_forms],
    )


@router.get("/contents/{content_id}", response_model=PrContentPointsResponse)
async def content_points(
    content_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep
) -> PrContentPointsResponse:
    """A PR piece's two stages: open or not, who may type codes, the claims."""
    view = await _service(session).content(content_id, membership)
    return PrContentPointsResponse.from_domain(view)


@router.post("/contents/{content_id}/claims", response_model=PrClaimResponse)
async def submit_claim(
    content_id: uuid.UUID,
    body: PrClaimRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> PrClaimResponse:
    """One's own codes for one stage (sent again after a refusal). 422
    ``pr_points_not_open`` / ``pr_points_codes_required`` / the catalogue's
    ``design_code_invalid`` …, 403 ``pr_points_forbidden``, 409 ``pr_claim_decided``."""
    view = await _service(session).submit(
        actor=actor,
        membership=membership,
        request_id=request_id,
        content_id=content_id,
        stage=body.stage,
        picks={
            phase: [pick.to_pick() for pick in picks] for phase, picks in body.task_codes.items()
        },
        note=body.note,
    )
    return PrClaimResponse.from_domain(view)


@router.get("/claims", response_model=list[PrClaimResponse])
async def claims(
    session: SessionDep,
    membership: UnitMembershipDep,
    status: str | None = Query(default=None, max_length=10),
) -> list[PrClaimResponse]:
    """A Trưởng phòng PR: everybody's claims; anybody else: their own."""
    views = await _service(session).queue(membership, status=status)
    return [PrClaimResponse.from_domain(view) for view in views]


@router.post("/claims/{claim_id}/decide", response_model=PrClaimResponse)
async def decide_claim(
    claim_id: uuid.UUID,
    body: PrDecisionRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> PrClaimResponse:
    view = await _service(session).decide(
        actor=actor,
        membership=membership,
        request_id=request_id,
        claim_id=claim_id,
        approve=body.approve,
        reason=body.return_reason,
        note=body.note,
    )
    return PrClaimResponse.from_domain(view)


__all__ = ["router"]
