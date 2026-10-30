"""Units: what the caller is in which unit, and the admin page behind it.

``GET /api/units/me`` is what the shell asks on every page load to draw the
stream switch and the navigation; ``GET /api/units/untagged`` lists the
accounts waiting for a stream; everything else is the administration of tags,
roles and settings. Authority is decided inside
:class:`~meobot.application.units.admin.UnitAdminService`, never here.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    UnitDirectoryDep,
    UnitMembershipDep,
    get_session,
)
from meobot.api.schemas.performance import PerformanceAccessResponse
from meobot.api.schemas.resources import ResourceAccessResponse
from meobot.api.schemas.seeding import SeedingAccessResponse
from meobot.api.schemas.units import (
    AdsAccessResponse,
    BanStatsResponse,
    CreateDesignCodeRequest,
    CreateDurationRequest,
    CreatePlatformRequest,
    CreateVideoKindRequest,
    DepartmentSeatResponse,
    DesignAccessResponse,
    DesignCodeListResponse,
    DesignCodeResponse,
    DirectoryUserResponse,
    DurationListResponse,
    DurationResponse,
    EffortGridResponse,
    LibraryLinkResponse,
    LibraryResponse,
    PlatformListResponse,
    PlatformResponse,
    RoleOptionResponse,
    TagMemberRequest,
    TaskFormResponse,
    TaskFormsRequest,
    TaskFormsResponse,
    UnitHealthResponse,
    UnitHealthWarning,
    UnitMemberListResponse,
    UnitMemberResponse,
    UnitMeResponse,
    UnitSettingsResponse,
    UntaggedUserResponse,
    UntaggedUsersResponse,
    UpdateDesignCodeRequest,
    UpdateDurationRequest,
    UpdateLibraryRequest,
    UpdateMemberRequest,
    UpdatePlatformRequest,
    UpdateUnitSettingsRequest,
    UpdateVideoKindRequest,
    VideoKindListResponse,
    VideoKindResponse,
    unit_entry,
)
from meobot.application.account.avatar_service import avatar_urls
from meobot.application.ads.access import ads_access
from meobot.application.audit_service import AuditService
from meobot.application.orders.design_code_service import (
    DesignCodeService,
    has_birthday_orders,
    in_design_team,
    managed_phases,
    manages_design_codes,
)
from meobot.application.orders.design_library_service import (
    DesignLibraryService,
    LibraryEntry,
    manages_library,
    sees_library,
)
from meobot.application.orders.effort_service import EffortService, ban_stats
from meobot.application.org import departments
from meobot.application.org.seats import load_seats
from meobot.application.performance.access import performance_access
from meobot.application.performance.coefficients import may_set_coefficients
from meobot.application.performance.pr_points import reviews_pr
from meobot.application.resources.access import resource_access
from meobot.application.resources.requests import ResourceRequestService
from meobot.application.resources.service import ResourceService
from meobot.application.seeding.service import seeding_access
from meobot.application.units.admin import ROLES_BY_UNIT, UnitAdminService
from meobot.application.units.directory import NOT_VISIBLE, UnitDirectoryService
from meobot.core.errors import ValidationError
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.orders.task_codes import DEFAULT_FORM, TASK_PHASES
from meobot.domain.units.errors import UnitAccessDeniedError, UnitNotFoundError, UnitValidationError
from meobot.domain.units.labels import (
    LEAD_ROLE_LABELS,
    unit_label,
    unit_role_label,
    unit_short_label,
)
from meobot.domain.units.models import (
    LibraryLink,
    TaskFormSetting,
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitSettings,
)

router = APIRouter(prefix="/api/units", tags=["units"])


def get_unit_admin(session: Annotated[AsyncSession, Depends(get_session)]) -> UnitAdminService:
    return UnitAdminService(session, AuditService(session))


AdminDep = Annotated[UnitAdminService, Depends(get_unit_admin)]


def get_design_codes(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DesignCodeService:
    return DesignCodeService(
        session, directory=UnitDirectoryService(session), audit=AuditService(session)
    )


DesignCodesDep = Annotated[DesignCodeService, Depends(get_design_codes)]


def get_design_library(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DesignLibraryService:
    return DesignLibraryService(
        session, directory=UnitDirectoryService(session), audit=AuditService(session)
    )


LibraryDep = Annotated[DesignLibraryService, Depends(get_design_library)]


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
    """The positions, heads first: Trưởng phòng ADS, then each function's
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
        matrix = (
            await departments.order_matrix(directory.session, settings.permissions)
            if code is UnitCode.ADS
            else None
        )
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
                    matrix=matrix,
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
                    matrix=matrix,
                )
            )
    can_admin = [code.value for code in await admin.admin_units(actor)]
    # 0061: placing people (stream, role, own Leader) is the OWNER's alone;
    # editing the rest of a member's row stays with whoever could tag before.
    can_edit_members = [code.value for code in admin.tag_units(membership, actor)]
    can_tag = [code.value for code in UnitCode] if admin.assigns(actor) else []
    library: tuple[LibraryLink, ...] = ()
    if sees_library(membership):
        try:
            library = (await directory.settings(UnitCode.ADS)).design_library
        except UnitNotFoundError:
            library = ()
    design = DesignAccessResponse(
        library=[LibraryLinkResponse.model_validate(link) for link in library],
        manage_library=manages_library(membership),
        view=in_design_team(membership) and not membership.sees_all,
        manage_codes=membership.has(UnitCode.ADS) and manages_design_codes(membership),
        task_phases=[phase.value for phase in managed_phases(membership)],
        manage_forms=membership.is_owner,
        birthday=membership.has(UnitCode.ADS)
        and has_birthday_orders(membership, await directory.settings(UnitCode.ADS)),
    )
    return UnitMeResponse.from_domain(
        membership,
        entries,
        can_admin=can_admin,
        can_tag=can_tag,
        can_edit_members=can_edit_members,
        design=design,
        seats=[DepartmentSeatResponse.from_domain(seat) for seat in membership.seats],
        resources=await _resources_block(directory.session, actor, membership),
        performance=PerformanceAccessResponse.from_access(
            await performance_access(directory.session, membership),
            manage_coefficients=may_set_coefficients(membership),
            review_pr=reviews_pr(membership),
        ),
        seeding=SeedingAccessResponse.from_access(
            await seeding_access(directory.session, membership)
        ),
        ads=_ads_block(membership),
    )


def _ads_block(membership: UnitMembership) -> AdsAccessResponse:
    access = ads_access(membership)
    return AdsAccessResponse(
        view=access.view,
        view_all=access.view_all,
        manage_accounts=access.manage_accounts,
        manage_connections=access.manage_connections,
        edit_revenue=access.edit_revenue,
    )


async def _resources_block(
    session: AsyncSession, actor: Actor, membership: UnitMembership
) -> ResourceAccessResponse:
    """The resources module's flags, and how many "Đề xuất tài nguyên" wait on
    the caller (the menu's badge)."""
    access = await resource_access(session, membership)
    requests = ResourceRequestService(ResourceService(session, AuditService(session)))
    return ResourceAccessResponse.from_access(
        access, requests_awaiting=await requests.awaiting_count(actor, access)
    )


@router.get("/directory", response_model=list[DirectoryUserResponse])
async def directory_users(actor: CurrentActorDep, admin: AdminDep) -> list[DirectoryUserResponse]:
    """Every account with its tags, for the "add member" picker."""
    rows = await admin.directory(actor=actor)
    seats = await load_seats(admin.session, [user.id for user, _ in rows])
    return [
        DirectoryUserResponse(
            user_id=user.id,
            full_name=user.full_name,
            base_role=user.role.value,
            base_role_label=role_label(user.role),
            active=user.active,
            units=[code.value for code in codes],
            departments=[
                DepartmentSeatResponse.from_domain(seat) for seat in seats.get(user.id, ())
            ],
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
    ids = [row.user.id for row in rows]
    avatars = await avatar_urls(session, ids)
    seats = await load_seats(session, ids)
    return UnitMemberListResponse(
        unit=unit_code.value,
        unit_label=unit_label(unit_code),
        unit_short_label=unit_short_label(unit_code),
        members=[
            UnitMemberResponse.from_row(row, avatars.get(row.user.id), seats.get(row.user.id, ()))
            for row in rows
        ],
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
        daily_tokens=body.daily_tokens,
        clear_daily_tokens="daily_tokens" in fields and body.daily_tokens is None,
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
    unit_code = _parse_unit(code)
    settings = await admin.update_settings(
        actor=actor, request_id=request_id, code=unit_code, patch=patch
    )
    matrix = (
        await departments.order_matrix(admin.session, settings.permissions)
        if unit_code is UnitCode.ADS
        else None
    )
    return UnitSettingsResponse.from_domain(settings, matrix)


@router.get("/{code}/effort", response_model=EffortGridResponse)
async def unit_effort(
    code: str,
    actor: CurrentActorDep,
    membership: UnitMembershipDep,
    directory: UnitDirectoryDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> EffortGridResponse:
    """ORD token effort, people by days (0053). The OWNER, an ADMIN and the
    heads see everyone; a ban's Leader their ban; anybody else themselves.
    Defaults to this week; at most 62 days (422 ``range_too_long``)."""
    unit_code = _parse_unit(code)
    if unit_code is not UnitCode.ADS:
        raise UnitNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
    await directory.require(actor, unit_code)
    unit = await directory.unit(unit_code)
    settings = UnitSettings.model_validate(unit.settings or {})
    if not settings.effort_enabled:
        raise UnitNotFoundError(
            "Chế độ tính effort (token) đang tắt.", details={"reason": "effort_disabled"}
        )
    grid = await EffortService(session).grid(
        actor=actor,
        membership=membership,
        unit_id=unit.id,
        settings=settings,
        date_from=date_from,
        date_to=date_to,
        user_id=user_id,
    )
    return EffortGridResponse.from_domain(grid)


@router.get("/{code}/ban-stats", response_model=BanStatsResponse)
async def unit_ban_stats(
    code: str,
    actor: CurrentActorDep,
    membership: UnitMembershipDep,
    directory: UnitDirectoryDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> BanStatsResponse:
    """The dashboard's "Theo ban": each ban's tokens (budget, used, left, held)
    and work (done, in progress, overdue, late) over the range, a row per
    member. Every ORD member reads it; ``my_ban`` is the viewer's default."""
    unit_code = _parse_unit(code)
    if unit_code is not UnitCode.ADS:
        raise UnitNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
    await directory.require(actor, unit_code)
    unit = await directory.unit(unit_code)
    stats = await ban_stats(
        session,
        membership=membership,
        unit_id=unit.id,
        settings=UnitSettings.model_validate(unit.settings or {}),
        date_from=date_from,
        date_to=date_to,
    )
    return BanStatsResponse.from_domain(stats)


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


# --- design codes ("mã task design", 0058) ---------------------------------------------


def _design_unit(code: str) -> None:
    """The catalogue is ORD's: any other unit has none (404)."""
    if _parse_unit(code) is not UnitCode.ADS:
        raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})


@router.get("/{code}/design-codes", response_model=DesignCodeListResponse)
@router.get("/{code}/task-codes", response_model=DesignCodeListResponse)
async def design_codes(
    code: str,
    request: Request,
    actor: CurrentActorDep,
    catalogue: DesignCodesDep,
    directory: UnitDirectoryDep,
    include_inactive: Annotated[bool, Query()] = False,
    phase: Annotated[str | None, Query()] = None,
) -> DesignCodeListResponse:
    """The task-code catalogue ("Danh mục điểm"), with the forms. Every ORD
    member reads the active codes (``phase`` narrows it to one step); the
    retired ones (``include_inactive``) are for whoever keeps that phase - its
    Trưởng phòng, the OWNER, an ADMIN (403 ``design_code_forbidden``).
    ``/design-codes`` is the older name and defaults to Design."""
    _design_unit(code)
    wanted = _phase(phase)
    if wanted is None and request.url.path.endswith("/design-codes"):
        wanted = OrderNodeType.THIET_KE
    rows = await catalogue.catalogue(actor=actor, include_inactive=include_inactive, phase=wanted)
    settings = await directory.settings(UnitCode.ADS)
    return DesignCodeListResponse(
        codes=[DesignCodeResponse.from_row(row) for row in rows],
        forms=[TaskFormResponse.model_validate(form) for form in settings.task_forms],
    )


@router.post(
    "/{code}/design-codes",
    response_model=DesignCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/{code}/task-codes",
    response_model=DesignCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_design_code(
    code: str,
    body: CreateDesignCodeRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    catalogue: DesignCodesDep,
) -> DesignCodeResponse:
    _design_unit(code)
    phase = _phase(body.phase)
    row = await catalogue.create(
        actor=actor,
        request_id=request_id,
        code=body.code,
        name=body.name,
        phase=phase or OrderNodeType.THIET_KE,
        points=body.points,
        unit_label=body.unit_label,
        examples=body.examples,
        note=body.note,
        free_points=body.free_points,
        active=body.active,
        sort_order=body.sort_order,
    )
    return DesignCodeResponse.from_row(row)


@router.patch("/{code}/design-codes/{code_id}", response_model=DesignCodeResponse)
@router.patch("/{code}/task-codes/{code_id}", response_model=DesignCodeResponse)
async def update_design_code(
    code: str,
    code_id: uuid.UUID,
    body: UpdateDesignCodeRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    catalogue: DesignCodesDep,
) -> DesignCodeResponse:
    """Change any field, reorder or (de)activate. There is no delete."""
    _design_unit(code)
    row = await catalogue.update(
        actor=actor,
        request_id=request_id,
        code_id=code_id,
        code=body.code,
        name=body.name,
        points=body.points,
        unit_label=body.unit_label,
        examples=body.examples,
        note=body.note,
        active=body.active,
        sort_order=body.sort_order,
    )
    return DesignCodeResponse.from_row(row)


@router.get("/{code}/task-forms", response_model=TaskFormsResponse)
async def task_forms(
    code: str, actor: CurrentActorDep, directory: UnitDirectoryDep
) -> TaskFormsResponse:
    """0074: the forms ("hình thức") a task-code line may take, per phase."""
    _design_unit(code)
    await directory.require(actor, UnitCode.ADS)
    settings = await directory.settings(UnitCode.ADS)
    return TaskFormsResponse(
        forms=[TaskFormResponse.model_validate(form) for form in settings.task_forms]
    )


@router.put("/{code}/task-forms", response_model=TaskFormsResponse)
async def update_task_forms(
    code: str,
    body: TaskFormsRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    admin: AdminDep,
    membership: UnitMembershipDep,
) -> TaskFormsResponse:
    """0074: replace the forms - the OWNER alone (403 ``task_form_forbidden``).
    "Làm mới" (``NEW``, 100%) stays and stays on; keys are unique; phases are
    node types that carry task codes (422 ``task_form_invalid``)."""
    _design_unit(code)
    if not membership.is_owner:
        raise UnitAccessDeniedError(
            "Chỉ chủ sở hữu (OWNER) mới sửa được bảng hình thức.",
            details={"reason": "task_form_forbidden"},
        )
    forms = _checked_forms(body.forms)
    updated = await admin.update_settings(
        actor=actor,
        request_id=request_id,
        code=UnitCode.ADS,
        patch={
            "task_forms": tuple(TaskFormSetting.model_validate(form.model_dump()) for form in forms)
        },
    )
    return TaskFormsResponse(
        forms=[TaskFormResponse.model_validate(form) for form in updated.task_forms]
    )


def _phase(value: str | None) -> OrderNodeType | None:
    if value is None or not value.strip():
        return None
    try:
        phase = OrderNodeType(value.strip().upper())
    except ValueError as error:
        raise UnitValidationError(
            "Pha không hợp lệ.", details={"reason": "invalid_phase", "field": "phase"}
        ) from error
    if phase not in TASK_PHASES:
        raise UnitValidationError(
            "Pha không hợp lệ.", details={"reason": "invalid_phase", "field": "phase"}
        )
    return phase


def _checked_forms(forms: list[TaskFormResponse]) -> list[TaskFormResponse]:
    def invalid(message: str) -> UnitValidationError:
        return UnitValidationError(
            message, details={"reason": "task_form_invalid", "field": "forms"}
        )

    phases = {phase.value for phase in TASK_PHASES}
    keys = [form.key.strip().upper() for form in forms]
    if len(set(keys)) != len(keys):
        raise invalid("Mỗi hình thức một mã riêng.")
    cleaned: list[TaskFormResponse] = []
    for key, form in zip(keys, forms, strict=True):
        if not key or not form.label.strip():
            raise invalid("Hình thức cần mã và tên.")
        if any(phase not in phases for phase in form.phases):
            raise invalid("Pha áp dụng không hợp lệ.")
        cleaned.append(form.model_copy(update={"key": key, "label": form.label.strip()}))
    new = next((form for form in cleaned if form.key == DEFAULT_FORM), None)
    if new is None or not new.active or new.pct != 100:
        raise invalid("“Làm mới” (100%) luôn có và luôn bật.")
    return cleaned


# --- the Design team's library ("Thư viện") ----------------------------------------


@router.get("/{code}/library", response_model=LibraryResponse)
async def design_library(
    code: str, actor: CurrentActorDep, membership: UnitMembershipDep, library: LibraryDep
) -> LibraryResponse:
    """The named links in the Design team's menu. The Design team reads it;
    anybody else in ORD gets 403 ``library_forbidden``."""
    _design_unit(code)
    links = await library.links(actor=actor)
    return LibraryResponse(
        links=[LibraryLinkResponse.model_validate(link) for link in links],
        can_manage=manages_library(membership),
    )


@router.put("/{code}/library", response_model=LibraryResponse)
async def save_design_library(
    code: str,
    body: UpdateLibraryRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    library: LibraryDep,
) -> LibraryResponse:
    """Save the whole library, in menu order: the Design Leader, the OWNER or
    an ADMIN. 422 ``library_name_missing`` / ``library_url_invalid`` (only
    http(s) links) / ``library_too_many``."""
    _design_unit(code)
    links = await library.replace(
        actor=actor,
        request_id=request_id,
        entries=[
            LibraryEntry(id=item.id, name=item.name, url=item.url, icon=item.icon)
            for item in body.links
        ],
    )
    return LibraryResponse(
        links=[LibraryLinkResponse.model_validate(link) for link in links], can_manage=True
    )


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
