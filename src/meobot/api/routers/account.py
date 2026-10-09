"""The signed-in person's account: card, statistics, profile, password; and the
member list with password resets for those allowed to see it.

No ``data`` envelope (like ``/api/orders``); failures use ``{"error": {...}}``.
Not behind a unit gate: everybody has an account. Authority is decided in
:class:`~meobot.application.account.account_service.AccountService`, never here.

A session on the default password reaches only ``/me``, ``/password`` and
``GET /avatar/{user_id}`` here - see :func:`meobot.api.deps.get_current_web_actor`.

``GET /avatar/{user_id}`` (0047) is the one route that answers with raw bytes
rather than JSON; its failures still use the envelope.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

from meobot.api.deps import (
    AccountServiceDep,
    AvatarServiceDep,
    CurrentActorDep,
    CurrentWebSessionDep,
    PasswordServiceDep,
    RequestIdDep,
    SessionDep,
    SettingsDep,
    UnitMembershipDep,
)
from meobot.api.routers.web_auth import error_response
from meobot.api.schemas.account import (
    AccountMeResponse,
    AvatarResponse,
    AvatarUploadRequest,
    ChangePasswordRequest,
    ChangeRoleRequest,
    MemberListResponse,
    MemberRowResponse,
    MemberStatsResponse,
    UpdateProfileRequest,
)
from meobot.api.schemas.pr import ErrorEnvelope
from meobot.application.account.stats_service import resolve_month
from meobot.application.audit_service import AuditService
from meobot.application.user_service import UserService
from meobot.domain.account.errors import PasswordRejectedError

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/me", response_model=AccountMeResponse)
async def my_account(
    actor: CurrentActorDep,
    web_session: CurrentWebSessionDep,
    membership: UnitMembershipDep,
    accounts: AccountServiceDep,
    avatars: AvatarServiceDep,
    settings: SettingsDep,
) -> AccountMeResponse:
    """Who I am, my units, my password state and this month's figures."""
    user = await accounts.user_for(actor)
    month = resolve_month(None, tz=settings.timezone)
    return AccountMeResponse.build(
        user=user,
        membership=membership,
        must_change_password=web_session is not None and web_session.must_change_password,
        stats=await accounts.stats(actor, month),
        avatar_url=await avatars.url_for(user.id),
    )


@router.get("/me/stats", response_model=MemberStatsResponse)
async def my_stats(
    actor: CurrentActorDep,
    accounts: AccountServiceDep,
    settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
) -> MemberStatsResponse:
    """My figures for one month (``YYYY-MM``, default the current one)."""
    resolved = resolve_month(month, tz=settings.timezone)
    return MemberStatsResponse.from_domain(await accounts.stats(actor, resolved))


@router.patch("/profile", response_model=AccountMeResponse)
async def update_profile(
    body: UpdateProfileRequest,
    actor: CurrentActorDep,
    web_session: CurrentWebSessionDep,
    membership: UnitMembershipDep,
    accounts: AccountServiceDep,
    avatars: AvatarServiceDep,
    settings: SettingsDep,
    request_id: RequestIdDep,
) -> AccountMeResponse:
    """Rename myself (2-80 characters after trimming)."""
    user = await accounts.update_profile(
        actor=actor, request_id=request_id, full_name=body.full_name
    )
    month = resolve_month(None, tz=settings.timezone)
    return AccountMeResponse.build(
        user=user,
        membership=membership,
        must_change_password=web_session is not None and web_session.must_change_password,
        stats=await accounts.stats(actor, month),
        avatar_url=await avatars.url_for(user.id),
    )


#: An avatar URL carries its version, so the bytes behind it never change.
_IMMUTABLE = "private, max-age=31536000, immutable"
#: Asked for with a version that is not the current one: serve the current
#: picture, but do not let a cache pin it under the stale URL.
_REVALIDATE = "private, no-cache"


@router.put(
    "/avatar",
    response_model=AvatarResponse,
    responses={
        422: {
            "model": ErrorEnvelope,
            "description": "avatar_too_large (> 300 KB) or avatar_invalid_image.",
        },
    },
)
async def upload_avatar(
    body: AvatarUploadRequest,
    actor: CurrentActorDep,
    avatars: AvatarServiceDep,
    request_id: RequestIdDep,
) -> AvatarResponse:
    """Replace my profile picture (base64 WebP/JPEG/PNG, at most 300 KB)."""
    url = await avatars.upload(
        actor=actor, request_id=request_id, content_type=body.content_type, data=body.data
    )
    return AvatarResponse(avatar_url=url)


@router.delete("/avatar", status_code=status.HTTP_204_NO_CONTENT)
async def remove_avatar(
    actor: CurrentActorDep,
    avatars: AvatarServiceDep,
    request_id: RequestIdDep,
) -> Response:
    """Back to initials. 204 whether or not there was a picture."""
    await avatars.remove(actor=actor, request_id=request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/avatar/{user_id}",
    response_class=Response,
    responses={
        200: {
            "content": {"image/webp": {}, "image/jpeg": {}, "image/png": {}},
            "description": "The image bytes.",
        },
        404: {"model": ErrorEnvelope, "description": "avatar_not_found."},
    },
)
async def avatar_image(
    user_id: uuid.UUID,
    request: Request,
    actor: CurrentActorDep,
    avatars: AvatarServiceDep,
) -> Response:
    """Anybody's picture, for anybody signed in. Raw bytes, not JSON.

    ``?v=`` is the cache buster from ``avatar_url``; any other query is ignored.
    """
    del actor  # signed in is the whole requirement
    row = await avatars.get(user_id)
    requested = request.query_params.get("v")
    cache = _IMMUTABLE if requested is None or requested == str(row.version) else _REVALIDATE
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            "Cache-Control": cache,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            # Served from the panel's own origin: whatever the bytes are, they
            # may never run as a document.
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        422: {"model": ErrorEnvelope, "description": "A password rule failed (details.reason)."},
        429: {"model": ErrorEnvelope, "description": "login_locked."},
    },
)
async def change_password(
    body: ChangePasswordRequest,
    actor: CurrentActorDep,
    web_session: CurrentWebSessionDep,
    passwords: PasswordServiceDep,
    request_id: RequestIdDep,
) -> Response:
    """Choose a new password. This browser stays signed in; every other closes."""
    try:
        await passwords.change_password(
            actor=actor,
            request_id=request_id,
            current_password=body.current_password,
            new_password=body.new_password,
            keep_session_id=None if web_session is None else web_session.session_id,
        )
    except PasswordRejectedError as error:
        if error.code != "current_password_wrong":
            raise
        # Rendered rather than raised: a wrong current password counts towards
        # the lockout, and raising would roll that count back.
        return error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/members", response_model=MemberListResponse)
async def members(
    actor: CurrentActorDep,
    membership: UnitMembershipDep,
    accounts: AccountServiceDep,
    settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
    unit: str | None = Query(default=None, max_length=10),
    include_inactive: bool = Query(default=False),
) -> MemberListResponse:
    """The team's figures for one month. OWNER and ADMIN (everyone), ORD HEAD
    (the ORD members). ``include_inactive=true`` (OWNER/ADMIN only) lists the
    deactivated accounts too, with ``active: false``."""
    listing = await accounts.members(
        actor=actor,
        membership=membership,
        month=resolve_month(month, tz=settings.timezone),
        unit=unit,
        include_inactive=include_inactive,
    )
    return MemberListResponse(
        month=listing.month.label,
        members=[MemberRowResponse.from_view(view) for view in listing.members],
    )


@router.post(
    "/members/{user_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        409: {
            "model": ErrorEnvelope,
            "description": "password_reset_undeliverable - no private chat with the bot.",
        },
    },
)
async def reset_password(
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    membership: UnitMembershipDep,
    accounts: AccountServiceDep,
    request_id: RequestIdDep,
) -> Response:
    """A temporary password sent to the member's Telegram, lockout cleared,
    signed out everywhere. Their next password login must change it."""
    await accounts.reset_password(
        actor=actor, membership=membership, request_id=request_id, user_id=user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_STATUS_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorEnvelope,
        "description": "account_status_forbidden - not OWNER/ADMIN, oneself, or an OWNER.",
    },
    404: {"model": ErrorEnvelope, "description": "account_not_found."},
}


@router.post(
    "/members/{user_id}/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_STATUS_RESPONSES,
)
async def deactivate_member(
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    accounts: AccountServiceDep,
    request_id: RequestIdDep,
) -> Response:
    """*Vô hiệu hoá tài khoản.* OWNER and ADMIN. Signs the person out everywhere;
    their tags and history stay, and reactivating brings them back."""
    await accounts.set_active(actor=actor, request_id=request_id, user_id=user_id, active=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/members/{user_id}/reactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_STATUS_RESPONSES,
        422: {"model": ErrorEnvelope, "description": "account_revoked."},
    },
)
async def reactivate_member(
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    accounts: AccountServiceDep,
    request_id: RequestIdDep,
) -> Response:
    """*Kích hoạt lại tài khoản.* OWNER and ADMIN."""
    await accounts.set_active(actor=actor, request_id=request_id, user_id=user_id, active=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/members/{user_id}/role",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"model": ErrorEnvelope, "description": "role_change_forbidden - OWNER only."},
        404: {"model": ErrorEnvelope, "description": "member_not_found."},
    },
)
async def change_member_role(
    user_id: uuid.UUID,
    body: ChangeRoleRequest,
    actor: CurrentActorDep,
    session: SessionDep,
    request_id: RequestIdDep,
) -> Response:
    """*Đổi vai trò hệ thống.* OWNER only. Picking "Quản trị viên" in the
    stream picker lands here: an ADMIN sees every stream ("Tất cả")."""
    await UserService(session, AuditService(session)).change_role(
        actor=actor, request_id=request_id, user_id=user_id, role=body.role
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
