""" "Báo cáo công việc/Seeding" (0085): the PR seeding group's KPI, phone
numbers and other work. The KPI and the people: the group and the Trưởng
phòng PR; handing in: anybody seated in PR too (2026-10-31). Else 403
``seeding_forbidden``."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    UnitMembershipDep,
    get_app_settings,
    get_session,
)
from meobot.api.schemas.seeding import (
    SeedingAccessResponse,
    SeedingDecisionRequest,
    SeedingDecisionResponse,
    SeedingLineResponse,
    SeedingMemberRequest,
    SeedingOtherRequest,
    SeedingOtherResponse,
    SeedingOverviewResponse,
    SeedingPersonResponse,
    SeedingPhoneResponse,
    SeedingPhonesRequest,
    SeedingSubmitResponse,
    SeedingTargetRequest,
)
from meobot.application.audit_service import AuditService
from meobot.application.performance.review_notifications import ReviewNotifier
from meobot.application.seeding.service import SeedingAccess, SeedingService, seeding_access
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.domain.seeding.phones import VALID

router = APIRouter(prefix="/api/seeding", tags=["seeding"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def _context(
    session: AsyncSession, membership: UnitMembershipDep, app_settings: Settings
) -> tuple[SeedingService, SeedingAccess]:
    access = await seeding_access(session, membership)
    service = SeedingService(
        session,
        AuditService(session),
        tz=app_settings.timezone,
        notifier=ReviewNotifier(session, app_settings),
    )
    return service, access


@router.get("/access", response_model=SeedingAccessResponse)
async def access_flags(
    session: SessionDep, membership: UnitMembershipDep, app_settings: SettingsDep
) -> SeedingAccessResponse:
    """What the caller may do here, and what waits for them ("Duyệt (N)")."""
    service, access = await _context(session, membership, app_settings)
    return SeedingAccessResponse.from_access(access, await service.awaiting(access))


@router.get("/overview", response_model=SeedingOverviewResponse)
async def overview(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    day: date | None = Query(default=None),
) -> SeedingOverviewResponse:
    """The team KPI for the week and the month of ``day`` (today), and each
    person's numbers."""
    service, access = await _context(session, membership, app_settings)
    today = utcnow().astimezone(app_settings.timezone).date()
    view = await service.overview(access, day or today)
    return SeedingOverviewResponse.from_domain(view, access)


@router.put("/targets", status_code=status.HTTP_204_NO_CONTENT)
async def set_target(
    body: SeedingTargetRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> Response:
    service, access = await _context(session, membership, app_settings)
    await service.set_target(
        actor=actor,
        access=access,
        request_id=request_id,
        kind=body.kind,
        day=body.day,
        target=body.target,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/members", response_model=list[SeedingPersonResponse])
async def members(
    session: SessionDep, membership: UnitMembershipDep, app_settings: SettingsDep
) -> list[SeedingPersonResponse]:
    service, access = await _context(session, membership, app_settings)
    return [SeedingPersonResponse.from_domain(person) for person in await service.members(access)]


@router.get("/candidates", response_model=list[SeedingPersonResponse])
async def candidates(
    session: SessionDep, membership: UnitMembershipDep, app_settings: SettingsDep
) -> list[SeedingPersonResponse]:
    service, access = await _context(session, membership, app_settings)
    people = await service.candidates(access)
    return [SeedingPersonResponse.from_domain(person) for person in people]


@router.post("/members", status_code=status.HTTP_204_NO_CONTENT)
async def add_member(
    body: SeedingMemberRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> Response:
    service, access = await _context(session, membership, app_settings)
    await service.add_member(
        actor=actor, access=access, request_id=request_id, user_id=body.user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> Response:
    service, access = await _context(session, membership, app_settings)
    await service.remove_member(actor=actor, access=access, request_id=request_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/phones", response_model=SeedingSubmitResponse)
async def submit_phones(
    body: SeedingPhonesRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> SeedingSubmitResponse:
    """One number per line; each line says NEW (waiting for the Trưởng phòng),
    DUPLICATE (already in the system), REPEATED (twice here) or INVALID."""
    service, access = await _context(session, membership, app_settings)
    lines = await service.submit_phones(
        actor=actor, access=access, request_id=request_id, text=body.text
    )
    return SeedingSubmitResponse(
        accepted=sum(1 for line in lines if line.status == VALID),
        lines=[SeedingLineResponse.from_domain(line) for line in lines],
    )


@router.get("/phones", response_model=list[SeedingPhoneResponse])
async def phones(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    status_filter: str | None = Query(default=None, alias="status", max_length=10),
    mine: bool = Query(default=False),
) -> list[SeedingPhoneResponse]:
    service, access = await _context(session, membership, app_settings)
    views = await service.phones(access, status=status_filter, mine=mine)
    return [SeedingPhoneResponse.from_domain(view) for view in views]


@router.post("/phones/decide", response_model=SeedingDecisionResponse)
async def decide_phones(
    body: SeedingDecisionRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> SeedingDecisionResponse:
    service, access = await _context(session, membership, app_settings)
    decided = await service.decide_phones(
        actor=actor,
        access=access,
        request_id=request_id,
        ids=body.ids,
        approve=body.approve,
        note=body.note,
    )
    return SeedingDecisionResponse(decided=decided)


@router.post("/other", response_model=SeedingOtherResponse)
async def submit_other(
    body: SeedingOtherRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> SeedingOtherResponse:
    service, access = await _context(session, membership, app_settings)
    view = await service.submit_other(
        actor=actor,
        access=access,
        request_id=request_id,
        description=body.description,
        points=body.points,
    )
    return SeedingOtherResponse.from_domain(view)


@router.get("/other", response_model=list[SeedingOtherResponse])
async def others(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    status_filter: str | None = Query(default=None, alias="status", max_length=10),
    mine: bool = Query(default=False),
) -> list[SeedingOtherResponse]:
    service, access = await _context(session, membership, app_settings)
    views = await service.others(access, status=status_filter, mine=mine)
    return [SeedingOtherResponse.from_domain(view) for view in views]


@router.post("/other/decide", response_model=SeedingDecisionResponse)
async def decide_others(
    body: SeedingDecisionRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> SeedingDecisionResponse:
    service, access = await _context(session, membership, app_settings)
    decided = await service.decide_others(
        actor=actor,
        access=access,
        request_id=request_id,
        ids=body.ids,
        approve=body.approve,
        note=body.note,
    )
    return SeedingDecisionResponse(decided=decided)


__all__ = ["router"]
