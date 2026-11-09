"""``/api/legal-entities``: the companies people are employed by ("Pháp nhân", 0095).

Read by anybody signed in (the account screens name each person's company,
and the branch list rides along); added / renamed / switched off by the
OWNER and an ADMIN (403 ``legal_entity_forbidden``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from meobot.api.deps import CurrentActorDep, RequestIdDep, SessionDep
from meobot.api.schemas.account import (
    LegalEntityCreateRequest,
    LegalEntityListResponse,
    LegalEntityResponse,
    LegalEntityUpdateRequest,
)
from meobot.api.schemas.pr import ErrorEnvelope
from meobot.application.audit_service import AuditService
from meobot.application.org.legal_entities import LegalEntityService, may_edit_entities
from meobot.db.models.legal_entity import LegalEntity
from meobot.domain.org.models import BRANCHES

router = APIRouter(prefix="/api/legal-entities", tags=["legal-entities"])


def _row(entity: LegalEntity, people: int = 0) -> LegalEntityResponse:
    return LegalEntityResponse(
        id=entity.id,
        code=entity.code,
        name=entity.name,
        tax_code=entity.tax_code,
        active=entity.active,
        people=people,
    )


@router.get("", response_model=LegalEntityListResponse)
async def list_entities(
    actor: CurrentActorDep,
    session: SessionDep,
    include_inactive: bool = Query(default=False),
) -> LegalEntityListResponse:
    service = LegalEntityService(session, AuditService(session))
    can_edit = may_edit_entities(actor)
    rows = await service.entities(include_inactive=include_inactive and can_edit)
    return LegalEntityListResponse(
        entities=[_row(entity, people) for entity, people in rows],
        branches=[{"code": code, "label": label} for code, label in BRANCHES.items()],
        can_edit=can_edit,
    )


@router.post(
    "",
    response_model=LegalEntityResponse,
    status_code=status.HTTP_201_CREATED,
    responses={403: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
)
async def create_entity(
    body: LegalEntityCreateRequest,
    actor: CurrentActorDep,
    session: SessionDep,
    request_id: RequestIdDep,
) -> LegalEntityResponse:
    service = LegalEntityService(session, AuditService(session))
    entity = await service.create(
        actor=actor, request_id=request_id, code=body.code, name=body.name, tax_code=body.tax_code
    )
    return _row(entity)


@router.patch(
    "/{entity_id}",
    response_model=LegalEntityResponse,
    responses={
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
async def update_entity(
    entity_id: uuid.UUID,
    body: LegalEntityUpdateRequest,
    actor: CurrentActorDep,
    session: SessionDep,
    request_id: RequestIdDep,
) -> LegalEntityResponse:
    service = LegalEntityService(session, AuditService(session))
    fields = {key: getattr(body, key) for key in body.model_fields_set}
    entity = await service.update(
        actor=actor, request_id=request_id, entity_id=entity_id, fields=fields
    )
    return _row(entity)
