"""The points reports ("Hiệu suất", ``docs/perf/PERFORMANCE_REPORTS_PLAN.md``).

* ``/people/{id|me}`` - one person's month: everybody their own, a Trưởng
  phòng their direct reports, the company viewers anybody;
* ``/team`` - "Phòng của tôi": a head's direct reports, or any department
  (or everybody) for the company viewers;
* ``/overview`` - "Toàn công ty": departments, top 10, codes, the trend;
* ``/export.xlsx`` - the same people as ``/team``, as the department's sheet;
* ``/timeline`` - "Tiến độ" (Gantt): each person's steps in the month, the
  same people as ``/team`` plus the caller (everybody: their own);
* ``/coefficients`` - "Hệ số điểm" (0076): each member's coefficient by
  month; read and set by the OWNER (``PERFORMANCE COEFFICIENT``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import fields
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    UnitMembershipDep,
    get_app_settings,
    get_session,
)
from meobot.api.schemas.performance import (
    CodeRowResponse,
    CoefficientChangeResponse,
    CoefficientRowResponse,
    CoefficientsResponse,
    DepartmentOptionResponse,
    FiguresResponse,
    OverviewResponse,
    PerformanceAccessResponse,
    PersonReportResponse,
    SetCoefficientRequest,
    TeamResponse,
    TeamRowResponse,
    TimelineGroupResponse,
    TimelineResponse,
    TrendPointResponse,
)
from meobot.application.account.stats_service import Month, resolve_month
from meobot.application.audit_service import AuditService
from meobot.application.org.seats import load_seats
from meobot.application.performance.access import PerformanceAccess, performance_access
from meobot.application.performance.coefficients import (
    CoefficientService,
    may_set_coefficients,
)
from meobot.application.performance.excel import report_workbook
from meobot.application.performance.report_service import Figures, PerformanceReportService
from meobot.core.config import Settings
from meobot.db.models.department import Department
from meobot.db.models.user import User
from meobot.domain.org.models import Position
from meobot.domain.performance.errors import (
    PerformanceCoefficientForbiddenError,
    PerformanceForbiddenError,
    PerformancePersonNotFoundError,
)

router = APIRouter(prefix="/api/performance", tags=["performance"])

SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _access(session: AsyncSession, membership: UnitMembershipDep) -> PerformanceAccess:
    return await performance_access(session, membership)


@router.get("/access", response_model=PerformanceAccessResponse)
async def access_flags(
    session: SessionDep, membership: UnitMembershipDep
) -> PerformanceAccessResponse:
    return PerformanceAccessResponse.from_access(
        await _access(session, membership),
        manage_coefficients=may_set_coefficients(membership),
    )


@router.get("/coefficients", response_model=CoefficientsResponse)
async def coefficients(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
) -> CoefficientsResponse:
    """Every active account with its coefficient for ``month`` (default 1)."""
    if not may_set_coefficients(membership):
        raise PerformanceCoefficientForbiddenError()
    period = resolve_month(month, tz=app_settings.timezone)
    rows = await CoefficientService(session, AuditService(session)).overview(period)
    return CoefficientsResponse(
        month=period.label, rows=[CoefficientRowResponse.from_domain(row) for row in rows]
    )


@router.get("/coefficients/{user_id}", response_model=list[CoefficientChangeResponse])
async def coefficient_history(
    user_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep
) -> list[CoefficientChangeResponse]:
    if not may_set_coefficients(membership):
        raise PerformanceCoefficientForbiddenError()
    changes = await CoefficientService(session, AuditService(session)).history(user_id)
    return [CoefficientChangeResponse.from_domain(change) for change in changes]


@router.put("/coefficients/{user_id}", response_model=CoefficientChangeResponse)
async def set_coefficient(
    user_id: uuid.UUID,
    body: SetCoefficientRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    app_settings: SettingsDep,
) -> CoefficientChangeResponse:
    """From ``month`` on, ``user_id``'s points count ``coefficient`` times.
    403 ``performance_coefficient_forbidden``, 422 ``coefficient_invalid``."""
    change = await CoefficientService(session, AuditService(session)).set(
        actor=actor,
        membership=membership,
        request_id=request_id,
        user_id=user_id,
        coefficient=body.coefficient,
        month=resolve_month(body.month, tz=app_settings.timezone),
        note=body.note,
    )
    return CoefficientChangeResponse.from_domain(change)


@router.get("/people/{person}", response_model=PersonReportResponse)
async def person_report(
    person: str,
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
) -> PersonReportResponse:
    """One person's month: the figures, the weeks, the phases and every step
    counted (or still under way)."""
    access = await _access(session, membership)
    user_id = _person_id(person, access)
    if not access.may_view(user_id):
        raise PerformanceForbiddenError()
    exists = await session.scalar(
        select(User.id).where(User.id == user_id, User.deleted_at.is_(None))
    )
    if exists is None:
        raise PerformancePersonNotFoundError()
    service = PerformanceReportService(session, tz=app_settings.timezone)
    report = await service.person(user_id, service.month(month))
    return PersonReportResponse.from_domain(report)


@router.get("/team", response_model=TeamResponse)
async def team_report(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
    department_id: uuid.UUID | None = Query(default=None),
) -> TeamResponse:
    access = await _access(session, membership)
    if not access.view_team:
        raise PerformanceForbiddenError("Chỉ Trưởng phòng xem được điểm của phòng.")
    service = PerformanceReportService(session, tz=app_settings.timezone)
    period = service.month(month)
    people, options = await _team_people(session, service, access, department_id, period)
    rows = await service.team(people, period)
    whole_company = access.view_all and department_id is None
    trend = await service.trend(period, user_ids=None if whole_company else people)
    codes = await service.codes(
        None if whole_company else people, period, department_id=department_id
    )
    return TeamResponse(
        month=period.label,
        department_id=department_id,
        departments=options,
        rows=[TeamRowResponse.from_domain(row) for row in rows],
        totals=FiguresResponse.from_domain(_sum(row.figures for row in rows)),
        trend=[TrendPointResponse.from_domain(point) for point in trend],
        codes=[CodeRowResponse.from_domain(row) for row in codes],
        view_all=access.view_all,
        export=access.export,
    )


@router.get("/timeline", response_model=TimelineResponse)
async def timeline_report(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
    department_id: uuid.UUID | None = Query(default=None),
) -> TimelineResponse:
    """The Gantt chart: everybody their own steps; a Trưởng phòng also their
    direct reports; a company viewer a department, or everybody who has a
    step in the month."""
    access = await _access(session, membership)
    service = PerformanceReportService(session, tz=app_settings.timezone)
    period = service.month(month)
    options: list[DepartmentOptionResponse] = []
    people: list[uuid.UUID] = [access.user_id] if access.user_id is not None else []
    whole_company = False
    if access.view_team:
        team, options = await _team_people(session, service, access, department_id, period)
        whole_company = access.view_all and department_id is None
        people = team if access.view_all else list(dict.fromkeys([*people, *team]))
    elif department_id is not None:
        raise PerformanceForbiddenError("Chỉ Trưởng phòng xem được tiến độ của phòng.")
    groups = await service.timeline(people, period)
    if whole_company:
        groups = tuple(group for group in groups if group.bars)
    return TimelineResponse(
        month=period.label,
        department_id=department_id,
        departments=options,
        groups=[TimelineGroupResponse.from_domain(group) for group in groups],
        view_all=access.view_all,
    )


@router.get("/overview", response_model=OverviewResponse)
async def overview_report(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
) -> OverviewResponse:
    access = await _access(session, membership)
    if not access.view_all:
        raise PerformanceForbiddenError("Chỉ ban giám đốc xem được báo cáo toàn công ty.")
    service = PerformanceReportService(session, tz=app_settings.timezone)
    period = service.month(month)
    overview = await service.overview(period)
    trend = await service.trend(period)
    return OverviewResponse.from_domain(overview, trend=trend, export=access.export)


@router.get("/export.xlsx")
async def export_report(
    session: SessionDep,
    membership: UnitMembershipDep,
    app_settings: SettingsDep,
    month: str | None = Query(default=None, max_length=7),
    department_id: uuid.UUID | None = Query(default=None),
) -> Response:
    """The month as the department's sheet ("Tổng hợp" + "Chi tiết"), for the
    people the caller sees on ``/team``."""
    access = await _access(session, membership)
    if not access.export or not access.view_team:
        raise PerformanceForbiddenError("Bạn không có quyền xuất báo cáo điểm.")
    service = PerformanceReportService(session, tz=app_settings.timezone)
    period = service.month(month)
    people, _ = await _team_people(session, service, access, department_id, period)
    team = await service.team(people, period)
    details = await service.detail_rows(people, period)
    codes = await service.codes(people, period, department_id=department_id)
    names = await service.names({row.user_id for row, _, _ in details})
    departments = await service.department_names(
        {row.department_id for row, _, _ in details if row.department_id is not None}
    )
    content = report_workbook(
        month=period.label,
        team=team,
        details=details,
        codes=codes,
        names=names,
        departments=departments,
        tz=app_settings.timezone,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="diem-hieu-suat-{period.label}.xlsx"',
            "Cache-Control": "no-store",
        },
    )


def _person_id(person: str, access: PerformanceAccess) -> uuid.UUID:
    if person == "me":
        if access.user_id is None:
            raise PerformancePersonNotFoundError()
        return access.user_id
    try:
        return uuid.UUID(person)
    except ValueError as error:
        raise PerformancePersonNotFoundError() from error


async def _team_people(
    session: AsyncSession,
    service: PerformanceReportService,
    access: PerformanceAccess,
    department_id: uuid.UUID | None,
    month: Month,
) -> tuple[list[uuid.UUID], list[DepartmentOptionResponse]]:
    """Who the team table lists, and the departments its filter offers: a
    company viewer any department (or everybody seated or scoring), a head
    only their direct reports, narrowed to one department they head."""
    if access.view_all:
        departments = (
            await session.scalars(
                select(Department)
                .where(Department.active.is_(True))
                .order_by(Department.sort_order, Department.name)
            )
        ).all()
        options = [DepartmentOptionResponse(id=row.id, name=row.name) for row in departments]
        if department_id is not None:
            return await service.department_people(department_id, month), options
        seated = list(await load_seats(session))
        return list(dict.fromkeys([*seated, *await service.people_with_points(month)])), options
    assert access.user_id is not None
    own = [
        seat
        for seat in (await load_seats(session, [access.user_id])).get(access.user_id, ())
        if seat.position is Position.HEAD
    ]
    options = [DepartmentOptionResponse(id=seat.department_id, name=seat.name) for seat in own]
    people = list(access.reports)
    if department_id is None:
        return people, options
    if department_id not in {seat.department_id for seat in own}:
        raise PerformanceForbiddenError("Bạn không phải Trưởng phòng của phòng này.")
    seats = await load_seats(session, people)
    return [
        user_id
        for user_id in people
        if any(seat.department_id == department_id for seat in seats.get(user_id, ()))
    ], options


def _sum(rows: Iterable[Figures]) -> Figures:
    totals = {field.name: 0 for field in fields(Figures)}
    for figures in rows:
        for name in totals:
            totals[name] += getattr(figures, name)
    return Figures(**totals)


__all__ = ["router"]
