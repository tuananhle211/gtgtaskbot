"""The personal permission layer (0066): ``/api/permissions``.

One page per person - everything they hold in every module and where it
comes from - and the grants on top of their seats. Who may read and write is
:meth:`~meobot.application.org.access.Access.manages`, checked in the service.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, get_session
from meobot.api.schemas.permissions import (
    ChecklistRequest,
    GrantRequest,
    PersonalGrantResponse,
    PersonOverviewResponse,
)
from meobot.application.audit_service import AuditService
from meobot.application.org.permissions import (
    PermissionValidationError,
    PersonalPermissionService,
)

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def get_permissions(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PersonalPermissionService:
    return PersonalPermissionService(session, AuditService(session))


ServiceDep = Annotated[PersonalPermissionService, Depends(get_permissions)]


@router.get("/people/{user_id}", response_model=PersonOverviewResponse)
async def person(
    user_id: uuid.UUID, actor: CurrentActorDep, service: ServiceDep
) -> PersonOverviewResponse:
    """Everything one person holds, module by module, with its sources; and
    what the caller may grant them. 403 ``permission_forbidden`` for
    somebody who manages no module of theirs (one reads one's own)."""
    return PersonOverviewResponse.from_domain(await service.overview(actor=actor, user_id=user_id))


@router.post(
    "/people/{user_id}/grants",
    response_model=PersonalGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant(
    user_id: uuid.UUID,
    body: GrantRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> PersonalGrantResponse:
    """Give one permission of one module, with a scope. 403
    ``permission_forbidden``; 422 ``permission_not_grantable`` (not held by
    the granter, or wider than they hold it), ``permission_use_pr_grant``
    (a PR review gate: scoped, on the PR screen), ``permission_duplicate``."""
    row = await service.grant(
        actor=actor,
        request_id=request_id,
        user_id=user_id,
        module=body.module,
        permission=body.permission,
        scope=body.scope,
        note=body.note,
        effective_to=body.effective_to,
    )
    return PersonalGrantResponse.from_row(row)


@router.put("/people/{user_id}/grants", response_model=PersonOverviewResponse)
async def set_checklist(
    user_id: uuid.UUID,
    body: ChecklistRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> PersonOverviewResponse:
    """The checklist of one module, saved whole: a ticked permission is
    granted personally (with its scope), an unticked one revoked. What the
    person's seats give is untouched. Answers with the page as it stands."""
    for permission, scope in body.permissions.items():
        if scope is not None and scope not in ("OWN", "ALL"):
            raise PermissionValidationError(
                "Phạm vi không hợp lệ.",
                details={"reason": "permission_scope_invalid", "field": permission},
            )
    await service.set_personal(
        actor=actor,
        request_id=request_id,
        user_id=user_id,
        module=body.module,
        wanted=body.permissions,
    )
    return PersonOverviewResponse.from_domain(await service.overview(actor=actor, user_id=user_id))


@router.delete("/people/{user_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(
    user_id: uuid.UUID,
    grant_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ServiceDep,
) -> Response:
    """Withdraw a personal grant, effective at once. The row stays."""
    del user_id  # the grant names its holder; the path only reads well
    await service.revoke(actor=actor, request_id=request_id, grant_id=grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
