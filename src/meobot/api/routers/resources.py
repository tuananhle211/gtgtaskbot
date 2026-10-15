"""Resources ("Quản lý tài nguyên", 0065): ``/api/resources``.

Who may do what is the departments' grants in the ``RESOURCES`` module,
resolved once per request (:class:`~meobot.api.deps.ResourceAccessDep`) and
checked inside the service: a resource outside the caller's scope is a 404,
a write they may not make a 403 ``resource_forbidden``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    ResourceAccessDep,
    ResourceServiceDep,
    get_app_settings,
    get_session,
)
from meobot.api.schemas.resources import (
    ChangeHolderRequest,
    CreateOptionRequest,
    CreateResourceRequest,
    ImportFileRequest,
    ImportPreviewResponse,
    OptionResponse,
    OptionsResponse,
    PeopleResponse,
    PersonLookupResponse,
    PersonResponse,
    ReorderOptionsRequest,
    ResourceAccessResponse,
    ResourceBody,
    ResourceDetailResponse,
    ResourceListResponse,
    ReviewRequest,
    SettingsResponse,
    SummaryResponse,
    UpdateOptionRequest,
    UpdateSettingsRequest,
    UpdateStatusRequest,
    WarningsResponse,
)
from meobot.application.resources.access import ResourceValidationError
from meobot.application.resources.excel import ResourceImportService, export_rows
from meobot.application.resources.reports import ResourceReportService, previous_month
from meobot.application.resources.service import ResourceFilters, ResourceInput
from meobot.core.config import Settings
from meobot.db.models.department import Department
from meobot.domain.resources.models import EmploymentStatus, OptionKind

router = APIRouter(prefix="/api/resources", tags=["resources"])

SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _filters(
    *,
    department_id: uuid.UUID | None,
    type_id: uuid.UUID | None,
    holder: str | None,
    status_id: uuid.UUID | None,
    handover_id: uuid.UUID | None,
    employment_status: str | None,
    q: str | None,
    alerts: bool,
    mine: bool,
    sort: str,
    desc: bool,
    limit: int,
    offset: int,
) -> ResourceFilters:
    employment: EmploymentStatus | None = None
    if employment_status:
        try:
            employment = EmploymentStatus(employment_status.strip().upper())
        except ValueError as error:
            raise ResourceValidationError(
                "Trạng thái nhân viên không hợp lệ.",
                details={"reason": "employment_status_invalid", "field": "employment_status"},
            ) from error
    return ResourceFilters(
        department_id=department_id,
        type_id=type_id,
        holder=holder,
        status_id=status_id,
        handover_id=handover_id,
        employment_status=employment,
        q=q,
        alerts_only=alerts,
        mine=mine,
        sort=sort,
        desc=desc,
        limit=limit,
        offset=offset,
    )


# --- access and vocabularies ----------------------------------------------------------


@router.get("/access", response_model=ResourceAccessResponse)
async def access(access: ResourceAccessDep) -> ResourceAccessResponse:
    """What the caller may do here. Never refused: the flags say."""
    return ResourceAccessResponse.from_access(access)


@router.get("/options", response_model=OptionsResponse)
async def options(
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    session: SessionDep,
    include_inactive: bool = Query(default=False),
) -> OptionsResponse:
    """The pick-lists and the departments the forms and filters need.
    ``include_inactive`` (settings only) lists the retired entries too."""
    access.require_view()
    await service.ensure_seed_options()
    catalog = await service.catalog()
    show_inactive = include_inactive and access.settings
    kinds = {kind: catalog.of_kind(kind, include_inactive=show_inactive) for kind in OptionKind}
    departments = (
        await session.scalars(
            select(Department)
            .where(Department.active.is_(True))
            .order_by(Department.sort_order, Department.code)
        )
    ).all()
    return OptionsResponse.build(
        kinds=kinds,
        departments=list(departments),
        review_days=await service.review_days(),
        can_manage=access.settings,
    )


@router.post("/options", response_model=OptionResponse, status_code=status.HTTP_201_CREATED)
async def create_option(
    body: CreateOptionRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> OptionResponse:
    row = await service.create_option(
        access=access,
        actor=actor,
        request_id=request_id,
        kind=body.kind,
        name=body.name,
        status_group=body.status_group,
    )
    return OptionResponse.from_row(row)


@router.patch("/options/{option_id}", response_model=OptionResponse)
async def update_option(
    option_id: uuid.UUID,
    body: UpdateOptionRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> OptionResponse:
    """Rename, reorder or retire an entry (never deleted)."""
    row = await service.update_option(
        access=access,
        actor=actor,
        request_id=request_id,
        option_id=option_id,
        name=body.name,
        active=body.active,
        sort_order=body.sort_order,
        status_group=body.status_group,
    )
    return OptionResponse.from_row(row)


@router.put("/options/order", response_model=list[OptionResponse])
async def reorder_options(
    body: ReorderOptionsRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> list[OptionResponse]:
    rows = await service.reorder_options(
        access=access, actor=actor, request_id=request_id, kind=body.kind, ids=body.ids
    )
    return [OptionResponse.from_row(row) for row in rows]


@router.get("/settings", response_model=SettingsResponse)
async def settings(access: ResourceAccessDep, service: ResourceServiceDep) -> SettingsResponse:
    access.require_view()
    return SettingsResponse(review_days=await service.review_days())


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: UpdateSettingsRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> SettingsResponse:
    days = await service.set_review_days(
        access=access, actor=actor, request_id=request_id, days=body.review_days
    )
    return SettingsResponse(review_days=days)


# --- people ---------------------------------------------------------------------------


@router.get("/people", response_model=PeopleResponse)
async def people(
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    q: str | None = Query(default=None, max_length=200),
) -> PeopleResponse:
    """Active accounts with their departments and employment status: the
    holder pickers and the look-up's list."""
    rows = await service.people(access, q=q)
    return PeopleResponse(people=[PersonResponse.from_domain(row) for row in rows])


@router.get("/people/{user_id}", response_model=PersonLookupResponse)
async def person(
    user_id: uuid.UUID, access: ResourceAccessDep, service: ResourceServiceDep
) -> PersonLookupResponse:
    """ "Tra cứu theo nhân viên": department, employment status, what they
    hold (by type) and held. ``REPORT`` holders, everyone for themselves."""
    return PersonLookupResponse.from_domain(await service.person_lookup(access, user_id))


# --- the inventory ----------------------------------------------------------------------


@router.get("", response_model=ResourceListResponse)
async def list_resources(
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    department_id: uuid.UUID | None = Query(default=None),
    type_id: uuid.UUID | None = Query(default=None),
    holder: str | None = Query(default=None, max_length=40),
    status_id: uuid.UUID | None = Query(default=None),
    handover_id: uuid.UUID | None = Query(default=None),
    employment_status: str | None = Query(default=None, max_length=20),
    q: str | None = Query(default=None, max_length=200),
    alerts: bool = Query(default=False),
    mine: bool = Query(default=False),
    sort: str = Query(default="code", max_length=20),
    desc: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ResourceListResponse:
    """The listing, inside what the caller may see. ``holder=none`` = held
    by nobody (at the Phòng Tài nguyên); ``alerts=true`` = rows with a
    warning; ``mine=true`` = "Tài nguyên của tôi"."""
    filters = _filters(
        department_id=department_id,
        type_id=type_id,
        holder=holder,
        status_id=status_id,
        handover_id=handover_id,
        employment_status=employment_status,
        q=q,
        alerts=alerts,
        mine=mine,
        sort=sort,
        desc=desc,
        limit=limit,
        offset=offset,
    )
    page = await service.list_resources(access, filters)
    return ResourceListResponse.from_page(page, limit=limit, offset=offset)


@router.get("/export.xlsx")
async def export(
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    app_settings: SettingsDep,
    department_id: uuid.UUID | None = Query(default=None),
    type_id: uuid.UUID | None = Query(default=None),
    holder: str | None = Query(default=None, max_length=40),
    status_id: uuid.UUID | None = Query(default=None),
    handover_id: uuid.UUID | None = Query(default=None),
    employment_status: str | None = Query(default=None, max_length=20),
    q: str | None = Query(default=None, max_length=200),
    alerts: bool = Query(default=False),
    mine: bool = Query(default=False),
    sort: str = Query(default="code", max_length=20),
    desc: bool = Query(default=False),
) -> Response:
    """The same listing as an ``.xlsx``: the current filter, the caller's
    scope, ids and phones as text."""
    filters = _filters(
        department_id=department_id,
        type_id=type_id,
        holder=holder,
        status_id=status_id,
        handover_id=handover_id,
        employment_status=employment_status,
        q=q,
        alerts=alerts,
        mine=mine,
        sort=sort,
        desc=desc,
        limit=100000,
        offset=0,
    )
    page = await service.list_resources(access, filters)
    content = export_rows(page.rows, tz=app_settings.timezone)
    name = f"tai-nguyen-{date.today().isoformat()}.xlsx"  # noqa: DTZ011 - a file name
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("", response_model=ResourceDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_resource(
    body: CreateResourceRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> ResourceDetailResponse:
    """422 ``resource_id_taken`` (``details.field`` ``external_id``) for an
    id already used in the type; ``resource_option_invalid`` /
    ``resource_holder_invalid`` / ``resource_department_invalid`` name their field."""
    data = ResourceInput(**body.model_dump())
    row = await service.create(access=access, actor=actor, request_id=request_id, data=data)
    return ResourceDetailResponse.from_detail(await service.get(access, row.id))


# --- reports --------------------------------------------------------------------------


@router.get("/reports/summary", response_model=SummaryResponse)
async def summary(
    access: ResourceAccessDep,
    service: ResourceServiceDep,
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> SummaryResponse:
    """Department x type with the handover rate, and the statuses at the end
    of ``month`` (``YYYY-MM``, the current month unless said)."""
    chosen = month or date.today().strftime("%Y-%m")  # noqa: DTZ011 - a calendar month
    try:
        result = await ResourceReportService(service).summary(access, chosen)
    except ValueError as error:
        raise ResourceValidationError(
            "Tháng không hợp lệ.", details={"reason": "month_invalid", "field": "month"}
        ) from error
    return SummaryResponse.from_domain(result)


@router.get("/reports/warnings", response_model=WarningsResponse)
async def warnings(access: ResourceAccessDep, service: ResourceServiceDep) -> WarningsResponse:
    """Every resource with a warning: at a leaver or a mover, past the review
    threshold, in a status that needs attention or is a loss."""
    return WarningsResponse.from_domain(await ResourceReportService(service).warnings(access))


@router.get("/reports/previous-month")
async def previous(access: ResourceAccessDep) -> dict[str, str]:
    access.require_view()
    return {"month": previous_month(date.today())}  # noqa: DTZ011 - a calendar month


# --- Excel import -----------------------------------------------------------------------


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def import_preview(
    body: ImportFileRequest, access: ResourceAccessDep, service: ResourceServiceDep
) -> ImportPreviewResponse:
    """Step 1: read the file, write nothing, report every row and its errors."""
    preview = await ResourceImportService(service).preview(access, body.data)
    return ImportPreviewResponse.from_preview(preview)


@router.post("/import/commit", response_model=ImportPreviewResponse)
async def import_commit(
    body: ImportFileRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> ImportPreviewResponse:
    """Step 2: write every row at once - only when none has an error (422
    ``import_has_errors``)."""
    preview = await ResourceImportService(service).commit(
        access, actor=actor, request_id=request_id, data=body.data
    )
    return ImportPreviewResponse.from_preview(preview, written=True)


# --- one resource (literal paths above go first: FastAPI matches in order) ---


@router.get("/{resource_id}", response_model=ResourceDetailResponse)
async def resource(
    resource_id: uuid.UUID, access: ResourceAccessDep, service: ResourceServiceDep
) -> ResourceDetailResponse:
    """The profile: every field, the two histories and the change log, newest first."""
    return ResourceDetailResponse.from_detail(await service.get(access, resource_id))


@router.patch("/{resource_id}", response_model=ResourceDetailResponse)
async def update_resource(
    resource_id: uuid.UUID,
    body: ResourceBody,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> ResourceDetailResponse:
    """Edit the fields sent (``null`` clears an optional one). The holder and
    the status have their own routes; the code never changes."""
    changes = body.model_dump(exclude_unset=True)
    await service.update(
        access=access, actor=actor, request_id=request_id, resource_id=resource_id, changes=changes
    )
    return ResourceDetailResponse.from_detail(await service.get(access, resource_id))


@router.post("/{resource_id}/holder", response_model=ResourceDetailResponse)
async def change_holder(
    resource_id: uuid.UUID,
    body: ChangeHolderRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> ResourceDetailResponse:
    """ "Đổi người được bàn giao" with a reason (Cấp mới / Chuyển người / Thu
    hồi); ``holder_user_id: null`` = back at the Phòng Tài nguyên."""
    await service.change_holder(
        access=access,
        actor=actor,
        request_id=request_id,
        resource_id=resource_id,
        holder_user_id=body.holder_user_id,
        reason=body.reason,
        note=body.note,
        handover_id=body.handover_id,
    )
    return ResourceDetailResponse.from_detail(await service.get(access, resource_id))


@router.post("/{resource_id}/status", response_model=ResourceDetailResponse)
async def update_status(
    resource_id: uuid.UUID,
    body: UpdateStatusRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> ResourceDetailResponse:
    await service.update_status(
        access=access,
        actor=actor,
        request_id=request_id,
        resource_id=resource_id,
        status_id=body.status_id,
        note=body.note,
    )
    return ResourceDetailResponse.from_detail(await service.get(access, resource_id))


@router.post("/{resource_id}/review", response_model=ResourceDetailResponse)
async def mark_reviewed(
    resource_id: uuid.UUID,
    body: ReviewRequest,
    access: ResourceAccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    service: ResourceServiceDep,
) -> ResourceDetailResponse:
    """ "Đánh dấu đã rà soát": a history line, the status unchanged."""
    await service.mark_reviewed(
        access=access, actor=actor, request_id=request_id, resource_id=resource_id, note=body.note
    )
    return ResourceDetailResponse.from_detail(await service.get(access, resource_id))


__all__ = ["router"]
