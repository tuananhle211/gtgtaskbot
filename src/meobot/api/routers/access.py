"""Localhost-only endpoints for users, quota, group policies and requests.

These sit behind the same bind as every other MeoBot route: the container
publishes the API on ``127.0.0.1`` only (see ``docker-compose.yml``), and it is
unauthenticated, so exposing it publicly would hand anybody the ability to
suspend a colleague. That constraint is a deployment fact, restated here
because these particular routes are the ones where it matters most.

Two API-shape decisions worth naming:

* ``DELETE /group-policies/...`` means **reset to inherit**, never "delete the
  user". There is no route in MeoBot that deletes a person.
* Quota is a ``PUT`` on the override rather than a ``DELETE`` on the user:
  ``daily_limit: null`` is how a standing limit is removed.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status

from meobot.api.deps import ActorDep, RequestIdDep, SessionDep, SettingsDep
from meobot.api.schemas.access import (
    AddUserRequest,
    GroupPolicyListResponse,
    GroupPolicyResponse,
    PendingAccessListResponse,
    PendingAccessResponse,
    QuotaOverrideRequest,
    QuotaResponse,
    RoleChangeRequest,
    SetGroupPolicyRequest,
    StatusChangeRequest,
    UserListResponse,
    UserResponse,
)
from meobot.application.access_request_service import AccessRequestService
from meobot.application.audit_service import AuditService
from meobot.application.group_policy_service import GroupPolicyService
from meobot.application.quota_service import QuotaService
from meobot.application.user_service import UserService
from meobot.core.errors import NotFoundError
from meobot.domain.access.models import (
    GUEST_DEFAULT_DURATION,
    GUEST_DEFAULT_QUESTION_LIMIT,
    GroupPolicyMode,
)
from meobot.domain.access.quota import QuotaOutcome

router = APIRouter(prefix="/api/v1", tags=["access"])


def _users(session: SessionDep) -> UserService:
    return UserService(session, AuditService(session))


# --- Users ------------------------------------------------------------------
@router.get("/users", response_model=UserListResponse, summary="List system users")
async def list_users(
    session: SessionDep,
    include_blocked: bool = Query(default=True, description="Include suspended and revoked."),
) -> UserListResponse:
    """Every registered user, newest first."""
    rows = await _users(session).list_users(include_blocked=include_blocked)
    items = [UserResponse.from_model(row) for row in rows]
    return UserListResponse(items=items, total=len(items))


@router.get("/users/{user_id}", response_model=UserResponse, summary="Read one system user")
async def read_user(user_id: uuid.UUID, session: SessionDep) -> UserResponse:
    """One user by id."""
    row = await _users(session).by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return UserResponse.from_model(row)


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a user directly",
    responses={403: {"description": "The actor may not grant that role"}},
)
async def add_user(
    payload: AddUserRequest,
    session: SessionDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> UserResponse:
    """Create an account without an invite code."""
    user = await _users(session).add_user(
        actor=actor,
        request_id=request_id,
        telegram_user_id=payload.telegram_user_id,
        role=payload.role,
        full_name=payload.full_name,
        telegram_username=payload.telegram_username,
    )
    return UserResponse.from_model(user)


@router.post("/users/{user_id}/suspend", response_model=UserResponse, summary="Suspend a user")
async def suspend_user(
    user_id: uuid.UUID,
    payload: StatusChangeRequest,
    session: SessionDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> UserResponse:
    """Block an account globally. Reversible; nothing is deleted."""
    user = await _users(session).suspend(
        actor=actor, request_id=request_id, user_id=user_id, reason=payload.reason
    )
    return UserResponse.from_model(user)


@router.post("/users/{user_id}/enable", response_model=UserResponse, summary="Enable a user")
async def enable_user(
    user_id: uuid.UUID,
    session: SessionDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> UserResponse:
    """Put a suspended account back on the air."""
    user = await _users(session).enable(actor=actor, request_id=request_id, user_id=user_id)
    return UserResponse.from_model(user)


@router.post("/users/{user_id}/revoke", response_model=UserResponse, summary="Revoke a user")
async def revoke_user(
    user_id: uuid.UUID,
    payload: StatusChangeRequest,
    session: SessionDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> UserResponse:
    """End an account's access for good.

    This is what "remove from the system" means. The row, and every script,
    approval and audit entry pointing at it, stays exactly where it is.
    """
    user = await _users(session).revoke(
        actor=actor, request_id=request_id, user_id=user_id, reason=payload.reason
    )
    return UserResponse.from_model(user)


@router.patch("/users/{user_id}/role", response_model=UserResponse, summary="Change a role")
async def change_role(
    user_id: uuid.UUID,
    payload: RoleChangeRequest,
    session: SessionDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> UserResponse:
    """Move somebody to a different authoritative role."""
    user = await _users(session).change_role(
        actor=actor, request_id=request_id, user_id=user_id, role=payload.role
    )
    return UserResponse.from_model(user)


# --- Quota ------------------------------------------------------------------
@router.get("/users/{user_id}/quota", response_model=QuotaResponse, summary="Read chat quota")
async def read_quota(
    user_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> QuotaResponse:
    """One member's allowance for the current local day."""
    user = await _users(session).by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    quota = QuotaService(session, settings)
    verdict = await quota.inspect_member(user_id=user.id, role=user.role)
    return QuotaResponse(
        user_id=user.id,
        quota_date=quota.today(),
        limit=verdict.limit,
        used=verdict.used,
        reserved=verdict.reserved,
        remaining=max(0, verdict.remaining),
        unlimited=verdict.outcome is QuotaOutcome.UNLIMITED,
    )


@router.put("/users/{user_id}/quota", response_model=QuotaResponse, summary="Override chat quota")
async def override_quota(
    user_id: uuid.UUID,
    payload: QuotaOverrideRequest,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> QuotaResponse:
    """Set, clear or top up a member's allowance.

    ``daily_limit: null`` removes a standing override. ``bonus_today`` adds to
    today only; the two compose, so "50 a day, plus 10 more today" is one call
    or two, and means the same either way.
    """
    user = await _users(session).by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    quota = QuotaService(session, settings)
    if payload.reset_today:
        await quota.reset_today(user_id=user.id)
    if payload.daily_limit is None:
        await quota.clear_daily_limit(user_id=user.id)
    else:
        await quota.set_daily_limit(
            user_id=user.id,
            limit=payload.daily_limit,
            actor_user_id=actor.user_id,
            actor_telegram_id=actor.telegram_user_id,
            reason=payload.reason,
        )
    if payload.bonus_today:
        await quota.add_bonus(user_id=user.id, amount=payload.bonus_today)

    verdict = await quota.inspect_member(user_id=user.id, role=user.role)
    return QuotaResponse(
        user_id=user.id,
        quota_date=quota.today(),
        limit=verdict.limit,
        used=verdict.used,
        reserved=verdict.reserved,
        remaining=max(0, verdict.remaining),
        unlimited=verdict.outcome is QuotaOutcome.UNLIMITED,
    )


# --- Group policies ---------------------------------------------------------
@router.get(
    "/group-policies/{chat_id}",
    response_model=GroupPolicyListResponse,
    summary="List response policies in one chat",
)
async def list_group_policies(
    chat_id: int, session: SessionDep, bot_id: int = Query(default=0)
) -> GroupPolicyListResponse:
    """Every stored policy for one Telegram chat."""
    service = GroupPolicyService(session)
    rows = await service.list_for_chat(bot_id=bot_id, chat_id=chat_id)
    items = [
        GroupPolicyResponse.from_model(row, effective=GroupPolicyService.effective_mode(row))
        for row in rows
    ]
    return GroupPolicyListResponse(items=items, total=len(items))


@router.put(
    "/group-policies/{chat_id}/{telegram_user_id}",
    response_model=GroupPolicyResponse,
    summary="Set a response policy",
)
async def set_group_policy(
    chat_id: int,
    telegram_user_id: int,
    payload: SetGroupPolicyRequest,
    session: SessionDep,
    actor: ActorDep,
) -> GroupPolicyResponse:
    """Allow, ignore, mute, or grant Guest access in one chat."""
    service = GroupPolicyService(session)
    if payload.mode is GroupPolicyMode.GUEST:
        row = await service.grant_guest(
            actor=actor,
            bot_id=payload.bot_id,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            question_limit=payload.guest_question_limit or GUEST_DEFAULT_QUESTION_LIMIT,
            duration=(
                timedelta(hours=payload.guest_duration_hours)
                if payload.guest_duration_hours
                else GUEST_DEFAULT_DURATION
            ),
            reason=payload.reason,
        )
    else:
        row = await service.set_mode(
            actor=actor,
            bot_id=payload.bot_id,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            mode=payload.mode,
            muted_until=payload.muted_until,
            reason=payload.reason,
        )
    return GroupPolicyResponse.from_model(row, effective=GroupPolicyService.effective_mode(row))


@router.delete(
    "/group-policies/{chat_id}/{telegram_user_id}",
    response_model=GroupPolicyResponse,
    summary="Reset a response policy to inherit",
)
async def reset_group_policy(
    chat_id: int,
    telegram_user_id: int,
    session: SessionDep,
    actor: ActorDep,
    bot_id: int = Query(default=0),
) -> GroupPolicyResponse:
    """Return this person to the default rules in this chat.

    ``DELETE`` here means *reset to inherit*. The policy row is kept and marked
    revoked - who granted what, and who took it away, is what an audit needs -
    and the user themselves is untouched.
    """
    service = GroupPolicyService(session)
    row = await service.reset_to_inherit(
        actor=actor, bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id
    )
    return GroupPolicyResponse.from_model(row, effective=GroupPolicyService.effective_mode(row))


@router.get(
    "/group-policies/{chat_id}/{telegram_user_id}",
    response_model=GroupPolicyResponse,
    summary="Read one Guest or policy status",
)
async def read_group_policy(
    chat_id: int,
    telegram_user_id: int,
    session: SessionDep,
    bot_id: int = Query(default=0),
) -> GroupPolicyResponse:
    """One person's policy - and Guest counters - in one chat."""
    service = GroupPolicyService(session)
    row = await service.get(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
    if row is None:
        raise NotFoundError("Không có chính sách riêng cho người này trong group này")
    return GroupPolicyResponse.from_model(row, effective=GroupPolicyService.effective_mode(row))


# --- Pending access requests ------------------------------------------------
@router.get(
    "/access-requests",
    response_model=PendingAccessListResponse,
    summary="List pending access requests",
)
async def list_access_requests(session: SessionDep) -> PendingAccessListResponse:
    """Strangers currently waiting for the owner to decide."""
    rows = await AccessRequestService(session).list_open()
    items = [PendingAccessResponse.from_model(row) for row in rows]
    return PendingAccessListResponse(items=items, total=len(items))
