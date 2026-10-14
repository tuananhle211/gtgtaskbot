"""The departments screen (0061): departments, who sits in them, what they grant.

Read by the OWNER and an ADMIN; written by the OWNER alone (403
``department_forbidden`` otherwise).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, get_session
from meobot.api.schemas.departments import (
    CatalogResponse,
    CreateDepartmentRequest,
    DepartmentListResponse,
    DepartmentResponse,
    GrantsRequest,
    GrantsResponse,
    SeatListResponse,
    SeatRequest,
    SeatResponse,
    UpdateDepartmentRequest,
)
from meobot.application.audit_service import AuditService
from meobot.application.org.departments import DepartmentService

router = APIRouter(prefix="/api/departments", tags=["departments"])


def get_departments(session: Annotated[AsyncSession, Depends(get_session)]) -> DepartmentService:
    return DepartmentService(session, AuditService(session))


ServiceDep = Annotated[DepartmentService, Depends(get_departments)]


@router.get("", response_model=DepartmentListResponse)
async def list_departments(actor: CurrentActorDep, service: ServiceDep) -> DepartmentListResponse:
    rows = await service.departments(actor=actor)
    return DepartmentListResponse(
        departments=[DepartmentResponse.from_row(row, count) for row, count in rows]
    )


@router.get("/catalog", response_model=CatalogResponse)
async def catalog(actor: CurrentActorDep, service: ServiceDep) -> CatalogResponse:
    await service.departments(actor=actor)  # the same readers
    return CatalogResponse.build()


@router.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    body: CreateDepartmentRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> DepartmentResponse:
    row = await service.create(
        actor=actor,
        request_id=request_id,
        code=body.code,
        name=body.name,
        function=body.function,
        sort_order=body.sort_order,
    )
    return DepartmentResponse.from_row(row)


@router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: uuid.UUID,
    body: UpdateDepartmentRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> DepartmentResponse:
    """Rename, re-code, change what it is, reorder or (de)activate. There is
    no delete: a department that served its time is made inactive."""
    row = await service.update(
        actor=actor,
        request_id=request_id,
        department_id=department_id,
        code=body.code,
        name=body.name,
        function=body.function,
        active=body.active,
        sort_order=body.sort_order,
    )
    return DepartmentResponse.from_row(row)


@router.get("/{department_id}/members", response_model=SeatListResponse)
async def members(
    department_id: uuid.UUID, actor: CurrentActorDep, service: ServiceDep
) -> SeatListResponse:
    rows = await service.members(actor=actor, department_id=department_id)
    return SeatListResponse(members=[SeatResponse.from_row(row) for row in rows])


@router.put("/{department_id}/members", response_model=SeatResponse)
async def seat(
    department_id: uuid.UUID,
    body: SeatRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> SeatResponse:
    """Add a person (several departments per person are fine), or change
    their position / the head they report to. 422 ``department_manager_invalid``
    when the manager is not an active head of this department."""
    row = await service.seat(
        actor=actor,
        request_id=request_id,
        department_id=department_id,
        user_id=body.user_id,
        position=body.position,
        manager_user_id=body.manager_user_id,
        clear_manager="manager_user_id" in body.model_fields_set and body.manager_user_id is None,
    )
    return SeatResponse.from_row(row)


@router.delete("/{department_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unseat(
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> Response:
    """422 ``member_has_open_work`` when it would take somebody out of the
    order flow while they still own an open order or hold a step."""
    await service.unseat(
        actor=actor, request_id=request_id, department_id=department_id, user_id=user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{department_id}/grants", response_model=GrantsResponse)
async def grants(
    department_id: uuid.UUID, actor: CurrentActorDep, service: ServiceDep
) -> GrantsResponse:
    return GrantsResponse(cells=await service.grants(actor=actor, department_id=department_id))


@router.put("/{department_id}/grants", response_model=GrantsResponse)
async def set_grants(
    department_id: uuid.UUID,
    body: GrantsRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> GrantsResponse:
    """What the department's heads and members may do in the order flow.
    Cells left out keep their value; ``NONE`` takes a permission away."""
    cells = await service.set_grants(
        actor=actor, request_id=request_id, department_id=department_id, cells=body.cells
    )
    return GrantsResponse(cells=cells)
