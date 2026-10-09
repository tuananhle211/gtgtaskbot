"""Invite codes for the web panel (``/api/invites``): the "Mời thành viên" panel.

The same :class:`~meobot.application.invite_service.InviteService` as the
Telegram ``/create_invite`` command and the internal ``/api/v1/invites``
surface, behind the signed-in session. Only a system team lead
("Trưởng nhóm"), a Trưởng phòng / Leader of a ban in ORD, an ADMIN or the
OWNER may invite; anybody else is refused with a 403 ``invite_forbidden``.
A stream lead's invite tags its redeemer into the lead's stream, reporting to
them (``joins_label`` says where); an ADMIN/OWNER invite tags nobody. Which
role an invite may carry is the service's rule (``can_invite_role``: strictly
below the creator's own; a ban's Leader invites employees).

* ``GET`` lists the caller's own open invites (active, not expired, uses left);
* ``POST`` creates one and returns the code exactly once;
* ``POST /{id}/disable`` revokes one - a team lead only their own, an
  ADMIN/OWNER any (an invite they may not touch reads 404).

Redeeming stays a Telegram act (``/join``).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status

from meobot.api.deps import CurrentActorDep, InviteServiceDep, RequestIdDep
from meobot.api.schemas.invites import (
    CreatedInviteResponse,
    CreateInviteRequest,
    InviteListResponse,
    InviteResponse,
)
from meobot.api.schemas.pr import ErrorEnvelope
from meobot.application.invite_service import InviteForbiddenError, InviteService
from meobot.core.errors import NotFoundError
from meobot.core.time import ensure_utc, utcnow
from meobot.domain.identity.models import Actor, Role

router = APIRouter(prefix="/api/invites", tags=["invites"])

_FORBIDDEN: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorEnvelope,
        "description": "invite_forbidden - only a team lead, an ADMIN or the OWNER invites.",
    }
}


async def _require_inviter(actor: Actor, service: InviteService) -> None:
    if not await service.may_invite(actor):
        raise InviteForbiddenError()


@router.get("", response_model=InviteListResponse, responses=_FORBIDDEN)
async def my_invites(actor: CurrentActorDep, service: InviteServiceDep) -> InviteListResponse:
    """The caller's open invites, newest first. The codes are never readable again."""
    await _require_inviter(actor, service)
    if actor.user_id is None:
        return InviteListResponse(items=[], total=0)
    now = utcnow()
    items = [
        InviteResponse.from_model(invite, await service.joins_label(invite))
        for invite in await service.list_invites(active_only=True, created_by=actor.user_id)
        if (invite.expires_at is None or ensure_utc(invite.expires_at) > now)
        and invite.use_count < invite.max_uses
    ]
    return InviteListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=CreatedInviteResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_FORBIDDEN,
)
async def create_invite(
    payload: CreateInviteRequest,
    actor: CurrentActorDep,
    service: InviteServiceDep,
    request_id: RequestIdDep,
) -> CreatedInviteResponse:
    """A new code (role ``EMPLOYEE`` by default), shown exactly once."""
    await _require_inviter(actor, service)
    invite, code = await service.create(
        actor=actor,
        request_id=request_id,
        role=payload.role,
        scope=payload.scope,
        note=payload.note,
        expires_in_days=payload.expires_in_days,
        max_uses=payload.max_uses,
    )
    return CreatedInviteResponse.from_created(invite, code, await service.joins_label(invite))


@router.post(
    "/{invite_id}/disable",
    response_model=InviteResponse,
    responses={**_FORBIDDEN, 404: {"model": ErrorEnvelope, "description": "No such invite."}},
)
async def disable_invite(
    invite_id: uuid.UUID,
    actor: CurrentActorDep,
    service: InviteServiceDep,
    request_id: RequestIdDep,
) -> InviteResponse:
    """Revoke an invite. A team lead revokes only their own."""
    await _require_inviter(actor, service)
    if actor.role.rank < Role.ADMIN.rank:  # a team lead: only their own
        found = await service.get(invite_id)
        if found is None or found.created_by is None or found.created_by != actor.user_id:
            raise NotFoundError("Không tìm thấy mã mời.", details={"reason": "invite_not_found"})
    invite = await service.disable(actor=actor, request_id=request_id, invite_id=invite_id)
    return InviteResponse.from_model(invite)


__all__ = ["InviteForbiddenError", "router"]
