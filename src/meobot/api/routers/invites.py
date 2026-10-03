"""Invite endpoints.

Redemption is deliberately *not* here: an invite is redeemed by a Telegram
account, through ``/join``, so there is no HTTP path that creates a user.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from meobot.api.deps import ActorDep, InviteServiceDep, RequestIdDep
from meobot.api.schemas.invites import (
    CreatedInviteResponse,
    CreateInviteRequest,
    InviteListResponse,
    InviteResponse,
)

router = APIRouter(prefix="/api/v1/invites", tags=["invites"])


@router.get("", response_model=InviteListResponse, summary="List invite codes")
async def list_invites(
    service: InviteServiceDep,
    active_only: bool = Query(default=True, description="Only return usable invites."),
) -> InviteListResponse:
    """List invites. The codes themselves are not stored and never returned."""
    invites = await service.list_invites(active_only=active_only)
    items = [InviteResponse.from_model(item) for item in invites]
    return InviteListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=CreatedInviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an invite code",
    responses={403: {"description": "The actor may not grant that role"}},
)
async def create_invite(
    payload: CreateInviteRequest,
    service: InviteServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> CreatedInviteResponse:
    """Create an invite. The plaintext code is returned exactly once."""
    invite, code = await service.create(
        actor=actor,
        request_id=request_id,
        role=payload.role,
        scope=payload.scope,
        note=payload.note,
        expires_in_days=payload.expires_in_days,
        max_uses=payload.max_uses,
    )
    return CreatedInviteResponse.from_created(invite, code)


@router.post(
    "/{invite_id}/disable",
    response_model=InviteResponse,
    summary="Revoke an invite code",
    responses={404: {"description": "No such invite"}},
)
async def disable_invite(
    invite_id: uuid.UUID,
    service: InviteServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> InviteResponse:
    """Make an invite unusable without deleting its history."""
    invite = await service.disable(
        actor=actor,
        request_id=request_id,
        invite_id=invite_id,
    )
    return InviteResponse.from_model(invite)
