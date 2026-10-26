"""Payloads of the points reports (``docs/perf/PERFORMANCE_REPORTS_PLAN.md``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from meobot.api.schemas.board import DesignCodeItemResponse
from meobot.application.board.sources import code_view
from meobot.application.performance.access import PerformanceAccess
from meobot.application.performance.coefficients import CoefficientChange, CoefficientRow
from meobot.application.performance.report_service import (
    CodeRow,
    DepartmentRow,
    Figures,
    Overview,
    PersonLine,
    PersonReport,
    TeamRow,
    TrendPoint,
)


class PerformanceAccessResponse(BaseModel):
    """The nav's flags: "Phòng của tôi" (``view_team``), "Toàn công ty"
    (``view_all``), the Excel button (``export``), "Hệ số điểm"
    (``manage_coefficients``: the OWNER)."""

    view_team: bool = False
    view_all: bool = False
    export: bool = False
    manage_coefficients: bool = False

    @classmethod
    def from_access(
        cls, access: PerformanceAccess, *, manage_coefficients: bool = False
    ) -> PerformanceAccessResponse:
        return cls(
            view_team=access.view_team,
            view_all=access.view_all,
            export=access.export,
            manage_coefficients=manage_coefficients,
        )


class FiguresResponse(BaseModel):
    points: int
    steps_done: int
    pending_points: int
    pending_count: int
    returns_fault: int
    returns_other: int
    returns_codes: int
    late_count: int
    with_deadline: int
    on_time_rate: float | None
    previous_points: int

    @classmethod
    def from_domain(cls, figures: Figures) -> FiguresResponse:
        return cls(
            points=figures.points,
            steps_done=figures.steps_done,
            pending_points=figures.pending_points,
            pending_count=figures.pending_count,
            returns_fault=figures.returns_fault,
            returns_other=figures.returns_other,
            returns_codes=figures.returns_codes,
            late_count=figures.late_count,
            with_deadline=figures.with_deadline,
            on_time_rate=figures.on_time_rate,
            previous_points=figures.previous_points,
        )


class PersonLineResponse(BaseModel):
    node_id: uuid.UUID
    order_id: uuid.UUID
    order_code: str
    order_title: str
    phase: str
    phase_label: str
    items: list[DesignCodeItemResponse]
    points: int
    #: EARNED (counted this month) / PENDING_REVIEW / IN_PROGRESS (dự kiến).
    status: str
    at: datetime | None

    @classmethod
    def from_domain(cls, line: PersonLine) -> PersonLineResponse:
        return cls(
            node_id=line.node_id,
            order_id=line.order_id,
            order_code=line.order_code,
            order_title=line.order_title,
            phase=line.phase,
            phase_label=line.phase_label,
            items=[
                DesignCodeItemResponse.from_domain(code_view(item)).model_copy(
                    update={"points": points}
                )
                for item, points in zip(line.items, line.item_points, strict=True)
            ]
            if line.item_points
            else [DesignCodeItemResponse.from_domain(code_view(item)) for item in line.items],
            points=line.points,
            status=line.status,
            at=line.at,
        )


class PersonReportResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    month: str
    departments: list[str]
    figures: FiguresResponse
    by_phase: dict[str, int]
    by_week: list[int]
    lines: list[PersonLineResponse]

    @classmethod
    def from_domain(cls, report: PersonReport) -> PersonReportResponse:
        return cls(
            user_id=report.user_id,
            name=report.name,
            month=report.month,
            departments=list(report.departments),
            figures=FiguresResponse.from_domain(report.figures),
            by_phase=report.by_phase,
            by_week=list(report.by_week),
            lines=[PersonLineResponse.from_domain(line) for line in report.lines],
        )


class TeamRowResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    departments: list[str]
    figures: FiguresResponse

    @classmethod
    def from_domain(cls, row: TeamRow) -> TeamRowResponse:
        return cls(
            user_id=row.user_id,
            name=row.name,
            departments=list(row.departments),
            figures=FiguresResponse.from_domain(row.figures),
        )


class TrendPointResponse(BaseModel):
    month: str
    points: int
    by_department: dict[str, int]

    @classmethod
    def from_domain(cls, point: TrendPoint) -> TrendPointResponse:
        return cls(month=point.month, points=point.points, by_department=point.by_department)


class DepartmentOptionResponse(BaseModel):
    id: uuid.UUID
    name: str


class TeamResponse(BaseModel):
    month: str
    #: The department the table is narrowed to, if any.
    department_id: uuid.UUID | None
    departments: list[DepartmentOptionResponse]
    rows: list[TeamRowResponse]
    totals: FiguresResponse
    trend: list[TrendPointResponse]
    #: Whether the caller sees the company (the department filter lists all).
    view_all: bool
    export: bool


class DepartmentRowResponse(BaseModel):
    department_id: uuid.UUID | None
    name: str
    people: int
    points: int
    steps_done: int
    average: int
    late_count: int
    on_time_rate: float | None

    @classmethod
    def from_domain(cls, row: DepartmentRow) -> DepartmentRowResponse:
        return cls(
            department_id=row.department_id,
            name=row.name,
            people=row.people,
            points=row.points,
            steps_done=row.steps_done,
            average=row.average,
            late_count=row.late_count,
            on_time_rate=row.on_time_rate,
        )


class CodeRowResponse(BaseModel):
    phase: str
    code: str
    name: str
    quantity: int
    points: int

    @classmethod
    def from_domain(cls, row: CodeRow) -> CodeRowResponse:
        return cls(
            phase=row.phase, code=row.code, name=row.name, quantity=row.quantity, points=row.points
        )


class OverviewResponse(BaseModel):
    month: str
    points: int
    steps_done: int
    people: int
    departments: list[DepartmentRowResponse]
    top: list[TeamRowResponse]
    codes: list[CodeRowResponse]
    trend: list[TrendPointResponse]
    export: bool

    @classmethod
    def from_domain(
        cls, overview: Overview, *, trend: tuple[TrendPoint, ...], export: bool
    ) -> OverviewResponse:
        return cls(
            month=overview.month,
            points=overview.points,
            steps_done=overview.steps_done,
            people=overview.people,
            departments=[DepartmentRowResponse.from_domain(row) for row in overview.departments],
            top=[TeamRowResponse.from_domain(row) for row in overview.top],
            codes=[CodeRowResponse.from_domain(row) for row in overview.codes],
            trend=[TrendPointResponse.from_domain(point) for point in trend],
            export=export,
        )


def _month(value: date | None) -> str | None:
    return value.strftime("%Y-%m") if value is not None else None


class CoefficientRowResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    member_code: str | None
    departments: list[str]
    #: As a string ("1.20"): a Decimal, never a float.
    coefficient: str
    #: ``YYYY-MM`` the coefficient in force started; ``None`` = the default 1.
    effective_month: str | None
    note: str | None
    next_coefficient: str | None
    next_month: str | None

    @classmethod
    def from_domain(cls, row: CoefficientRow) -> CoefficientRowResponse:
        return cls(
            user_id=row.user_id,
            name=row.name,
            member_code=row.member_code,
            departments=list(row.departments),
            coefficient=str(row.coefficient),
            effective_month=_month(row.effective_month),
            note=row.note,
            next_coefficient=str(row.next_coefficient)
            if row.next_coefficient is not None
            else None,
            next_month=_month(row.next_month),
        )


class CoefficientsResponse(BaseModel):
    month: str
    rows: list[CoefficientRowResponse]


class CoefficientChangeResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    coefficient: str
    effective_month: str
    note: str | None
    created_by: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, change: CoefficientChange) -> CoefficientChangeResponse:
        return cls(
            id=change.id,
            user_id=change.user_id,
            coefficient=str(change.coefficient),
            effective_month=change.effective_month.strftime("%Y-%m"),
            note=change.note,
            created_by=change.created_by,
            created_at=change.created_at,
        )


class SetCoefficientRequest(BaseModel):
    #: 0.01-10, two decimals at most; a comma is read as the decimal point.
    coefficient: Decimal | str
    #: ``YYYY-MM``: from this month on.
    month: str = Field(min_length=7, max_length=7)
    note: str | None = Field(default=None, max_length=500)
