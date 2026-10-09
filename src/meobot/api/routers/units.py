"""Units: what the caller is in which unit, and the admin page behind it.

``GET /api/units/me`` is what the shell asks on every page load to draw the
stream switch and the navigation; ``GET /api/units/untagged`` lists the
accounts waiting for a stream; everything else is the administration of tags,
roles and settings. Authority is decided inside
:class:`~meobot.application.units.admin.UnitAdminService`, never here.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    UnitDirectoryDep,
    UnitMembershipDep,
    get_session,
)
from meobot.api.schemas.units import (
    CreateDurationRequest,
    CreatePlatformRequest,
    CreateVideoKindRequest,
    DirectoryUserResponse,
    DurationListResponse,
    DurationResponse,
    PlatformListResponse,
    PlatformResponse,
    RoleOptionResponse,
    TagMemberRequest,
    UnitHealthResponse,
    UnitHealthWarning,
    UnitMemberListResponse,
    UnitMemberResponse,
    UnitMeResponse,
    UnitSettingsResponse,
    UntaggedUserResponse,
    UntaggedUsersResponse,
    UpdateDurationRequest,
    UpdateMemberRequest,
    UpdatePlatformRequest,
    UpdateUnitSettingsRequest,
    UpdateVideoKindRequest,
    VideoKindListResponse,
    VideoKindResponse,
    unit_entry,
)
from meobot.application.account.avatar_service import avatar_urls
from meobot.application.audit_service import AuditService
from meobot.application.units.admin import ROLES_BY_UNIT, UnitAdminService
from meobot.core.errors import ValidationError
from meobot.domain.identity.labels import role_label
from meobot.domain.units.errors import UnitNotFoundError
from meobot.domain.units.labels import (
    LEAD_ROLE_LABELS,
    unit_label,
    unit_role_label,
    unit_short_label,
)
from meobot.domain.units.models import UnitCode, UnitMemberRole

router = APIRouter(prefix="/api/units", tags=["units"])


def get_unit_admin(session: Annotated[AsyncSession, Depends(get_session)]) -> UnitAdminService:
    return UnitAdminService(session, AuditService(session))


AdminDep = Annotated[UnitAdminService, Depends(get_unit_admin)]


def _parse_unit(value: str) -> UnitCode:
    try:
        return UnitCode(value.strip().upper())
    except ValueError as error:
        raise UnitNotFoundError(
            "Không tìm thấy.", details={"reason": "unit_not_visible"}
        ) from error


def _parse_role(value: str) -> UnitMemberRole:
    try:
        return UnitMemberRole(value.strip().upper())
    except ValueError as error:
        raise ValidationError(
            f"Vai trò không hợp lệ: {value!r}.",
            details={"reason": "invalid_role", "field": "role", "value": value},
        ) from error


def _roles_for(code: UnitCode) -> list[RoleOptionResponse]:
    """The positions, heads first: Trưởng phòng ORD, then each function's
    head (Biên kịch, Design, Dựng), then Marketing and the function staff."""
    allowed = [role for role in UnitMemberRole if role in ROLES_BY_UNIT[code]]
    heads = [
        RoleOptionResponse(role=role.value, label=unit_role_label(role, True), is_lead=True)
        for role in allowed
        if role in LEAD_ROLE_LABELS
    ]
    top = [
        RoleOptionResponse(role=role.value, label=unit_role_label(role))
        for role in allowed
        if role is UnitMemberRole.HEAD
    ]
    rest = [
        RoleOptionResponse(role=role.value, label=unit_role_label(role))
        for role in allowed
        if role is not UnitMemberRole.HEAD
    ]
    return top + heads + rest


@router.get("/me", response_model=UnitMeResponse)
async def my_units(
    actor: CurrentActorDep,
    membership: UnitMembershipDep,
    directory: UnitDirectoryDep,
    admin: AdminDep,
) -> UnitMeResponse:
    """The caller's units, roles and settings - the shell's first question."""
    entries = []
    for code in membership.visible_units():
        entry = membership.entry(code)
        try:
            settings = await directory.settings(code)
        except UnitNotFoundError:
            # The OWNER sees a unit whose row is missing only on a database
            # that never ran 0042's seed; show defaults rather than fail.
            from meobot.domain.units.models import UnitSettings

            settings = UnitSettings()
        if entry is None:
            # The OWNER or the ADMIN, in a unit they are not tagged into:
            # acts as its head.
            role = UnitMemberRole.HEAD if code is UnitCode.ADS else UnitMemberRole.MEMBER
            entries.append(
                unit_entry(
                    code.value,
                    role,
                    is_lead=False,
                    member_code=None,
                    personal_nas_url=None,
                    settings=settings,
                )
            )
        else:
            entries.append(
                unit_entry(
                    code.value,
                    entry.role,
                    is_lead=entry.is_lead,
                    member_code=entry.member_code,
                    personal_nas_url=entry.personal_nas_url,
                    settings=settings,
                )
            )
    can_admin = [code.value for code in await admin.admin_units(actor)]
    can_tag = [code.value for code in admin.tag_units(membership, actor)]
    return UnitMeResponse.from_domain(membership, entries, can_admin=can_admin, can_tag=can_tag)


@router.get("/directory", response_model=list[DirectoryUserResponse])
async def directory_users(actor: CurrentActorDep, admin: AdminDep) -> list[DirectoryUserResponse]:
    """Every account with its tags, for the "add member" picker."""
    rows = await admin.directory(actor=actor)
    return [
        DirectoryUserResponse(
            user_id=user.id,
            full_name=user.full_name,
            base_role=user.role.value,
            base_role_label=role_label(user.role),
            active=user.active,
            units=[code.value for code in codes],
        )
        for user, codes in rows
    ]


@router.get("/untagged", response_model=UntaggedUsersResponse)
async def untagged_users(
    actor: CurrentActorDep,
    admin: AdminDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UntaggedUsersResponse:
    """Active accounts with no open tag, newest first. OWNER, ADMIN, and a
    team lead tagged in some stream; anybody else 403 ``unit_tag_forbidden``."""
    users = await admin.untagged(actor=actor)
    avatars = await avatar_urls(session, [user.id for user in users])
    return UntaggedUsersResponse(
        users=[
            UntaggedUserResponse(
                user_id=user.id,
                full_name=user.full_name,
                telegram_username=user.telegram_username,
                role=user.role.value,
                role_label=role_label(user.role),
                created_at=user.created_at,
                avatar_url=avatars.get(user.id),
            )
            for user in users
        ]
    )


@router.get("/{code}/members", response_model=UnitMemberListResponse)
async def unit_members(
    code: str,
    actor: CurrentActorDep,
    directory: UnitDirectoryDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UnitMemberListResponse:
    """The unit's open tags. Visible to its members, the OWNER and the ADMIN."""
    unit_code = _parse_unit(code)
    await directory.require(actor, unit_code)
    unit = await directory.unit(unit_code)
    rows = [
        row
        for row in await directory.members(unit.id, active_only=False)
        if row.membership.left_at is None
    ]
    avatars = await avatar_urls(session, [row.user.id for row in rows])
    return UnitMemberListResponse(
        unit=unit_code.value,
        unit_label=unit_label(unit_code),
        unit_short_label=unit_short_label(unit_code),
        members=[UnitMemberResponse.from_row(row, avatars.get(row.user.id)) for row in rows],
        assignable_roles=_roles_for(unit_code),
    )


@router.post(
    "/{code}/members", response_model=UnitMemberResponse, status_code=status.HTTP_201_CREATED
)
async def tag_member(
    code: str,
    body: TagMemberRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> UnitMemberResponse:
    row = await admin.tag_member(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        user_id=body.user_id,
        role=_parse_role(body.role),
        is_lead=body.is_lead,
        member_code=body.member_code,
        personal_nas_url=body.personal_nas_url,
    )
    return UnitMemberResponse.from_row(row)


@router.patch("/{code}/members/{user_id}", response_model=UnitMemberResponse)
async def update_member(
    code: str,
    user_id: uuid.UUID,
    body: UpdateMemberRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> UnitMemberResponse:
    fields = body.model_fields_set
    row = await admin.update_member(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        user_id=user_id,
        role=None if body.role is None else _parse_role(body.role),
        is_lead=body.is_lead,
        member_code=body.member_code,
        clear_member_code="member_code" in fields and body.member_code is None,
        personal_nas_url=body.personal_nas_url,
        clear_personal_nas_url="personal_nas_url" in fields and body.personal_nas_url is None,
        manager_user_id=body.manager_user_id,
        clear_manager="manager_user_id" in fields and body.manager_user_id is None,
    )
    return UnitMemberResponse.from_row(row)


@router.delete("/{code}/members/{user_id}", response_model=UnitMemberResponse)
async def untag_member(
    code: str,
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> UnitMemberResponse:
    """Close the tag. The row stays; the person leaves the unit."""
    row = await admin.untag_member(
        actor=actor, request_id=request_id, code=_parse_unit(code), user_id=user_id
    )
    return UnitMemberResponse.from_row(row)


@router.patch("/{code}/settings", response_model=UnitSettingsResponse)
async def update_settings(
    code: str,
    body: UpdateUnitSettingsRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> UnitSettingsResponse:
    patch = body.model_dump(exclude_unset=True)
    if "btd_link_attacher" in patch and patch["btd_link_attacher"] is not None:
        patch["btd_link_attacher"] = _parse_role(patch["btd_link_attacher"])
    settings = await admin.update_settings(
        actor=actor, request_id=request_id, code=_parse_unit(code), patch=patch
    )
    return UnitSettingsResponse.from_domain(settings)


@router.get("/{code}/video-kinds", response_model=VideoKindListResponse)
async def video_kinds(
    code: str,
    actor: CurrentActorDep,
    admin: AdminDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> VideoKindListResponse:
    """The unit's "Loại video" catalogue. Members read the active kinds; the
    retired ones (``include_inactive``) are for the unit's administrators."""
    rows = await admin.video_kinds(
        actor=actor, code=_parse_unit(code), include_inactive=include_inactive
    )
    return VideoKindListResponse(kinds=[VideoKindResponse.from_row(row) for row in rows])


@router.post(
    "/{code}/video-kinds", response_model=VideoKindResponse, status_code=status.HTTP_201_CREATED
)
async def create_video_kind(
    code: str,
    body: CreateVideoKindRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> VideoKindResponse:
    row = await admin.create_video_kind(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        name=body.name,
        points=body.points,
        active=body.active,
        sort_order=body.sort_order,
    )
    return VideoKindResponse.from_row(row)


@router.patch("/{code}/video-kinds/{kind_id}", response_model=VideoKindResponse)
async def update_video_kind(
    code: str,
    kind_id: uuid.UUID,
    body: UpdateVideoKindRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> VideoKindResponse:
    """Rename, re-price, reorder or (de)activate. There is no delete."""
    row = await admin.update_video_kind(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        kind_id=kind_id,
        name=body.name,
        points=body.points,
        active=body.active,
        sort_order=body.sort_order,
    )
    return VideoKindResponse.from_row(row)


# --- platforms ----------------------------------------------------------------


@router.get("/{code}/platforms", response_model=PlatformListResponse)
async def list_platforms(
    code: str,
    actor: CurrentActorDep,
    admin: AdminDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> PlatformListResponse:
    rows = await admin.platforms(
        actor=actor, code=_parse_unit(code), include_inactive=include_inactive
    )
    return PlatformListResponse(platforms=[PlatformResponse.from_row(row) for row in rows])


@router.post(
    "/{code}/platforms", response_model=PlatformResponse, status_code=status.HTTP_201_CREATED
)
async def create_platform(
    code: str,
    body: CreatePlatformRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> PlatformResponse:
    row = await admin.create_platform(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        name=body.name,
        active=body.active,
        sort_order=body.sort_order,
    )
    return PlatformResponse.from_row(row)


@router.patch("/{code}/platforms/{item_id}", response_model=PlatformResponse)
async def update_platform(
    code: str,
    item_id: uuid.UUID,
    body: UpdatePlatformRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> PlatformResponse:
    row = await admin.update_platform(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        item_id=item_id,
        name=body.name,
        active=body.active,
        sort_order=body.sort_order,
    )
    return PlatformResponse.from_row(row)


# --- durations ----------------------------------------------------------------


@router.get("/{code}/durations", response_model=DurationListResponse)
async def list_durations(
    code: str,
    actor: CurrentActorDep,
    admin: AdminDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> DurationListResponse:
    rows = await admin.durations(
        actor=actor, code=_parse_unit(code), include_inactive=include_inactive
    )
    return DurationListResponse(durations=[DurationResponse.from_row(row) for row in rows])


@router.post(
    "/{code}/durations", response_model=DurationResponse, status_code=status.HTTP_201_CREATED
)
async def create_duration(
    code: str,
    body: CreateDurationRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> DurationResponse:
    row = await admin.create_duration(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        name=body.name,
        points=body.points,
        active=body.active,
        sort_order=body.sort_order,
    )
    return DurationResponse.from_row(row)


@router.patch("/{code}/durations/{item_id}", response_model=DurationResponse)
async def update_duration(
    code: str,
    item_id: uuid.UUID,
    body: UpdateDurationRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
) -> DurationResponse:
    row = await admin.update_duration(
        actor=actor,
        request_id=request_id,
        code=_parse_unit(code),
        item_id=item_id,
        name=body.name,
        points=body.points,
        active=body.active,
        sort_order=body.sort_order,
    )
    return DurationResponse.from_row(row)


@router.get("/{code}/health", response_model=UnitHealthResponse)
async def unit_health(code: str, actor: CurrentActorDep, admin: AdminDep) -> UnitHealthResponse:
    unit_code = _parse_unit(code)
    warnings = await admin.health(actor=actor, code=unit_code)
    return UnitHealthResponse(
        unit=unit_code.value,
        warnings=[UnitHealthWarning(code=key, message=message) for key, message in warnings],
    )
