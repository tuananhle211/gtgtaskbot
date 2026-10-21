"""Wire shapes of the resources module (``/api/resources``, 0065)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from meobot.application.resources.access import ResourceAccess
from meobot.application.resources.excel import ImportPreview, ImportRow, SheetSummary
from meobot.application.resources.reports import Summary, Warnings
from meobot.application.resources.service import (
    ChangeLine,
    HandoverLine,
    Person,
    PersonLookup,
    ResourceDetail,
    ResourcePage,
    ResourceRow,
    StatusLine,
)
from meobot.db.models.department import Department
from meobot.db.models.resource import ResourceOption
from meobot.domain.org.models import FUNCTION_LABELS
from meobot.domain.resources.models import (
    ALERT_LABELS,
    EMPLOYMENT_STATUS_LABELS,
    HANDOVER_REASON_LABELS,
    OPTION_KIND_LABELS,
    STATUS_GROUP_LABELS,
    AlertKind,
    EmploymentStatus,
    HandoverReason,
    OptionKind,
    StatusGroup,
)

# --- access ---------------------------------------------------------------------------


class ResourceAccessResponse(BaseModel):
    """What this person may do in the module (also in ``/api/units/me`` ``resources``)."""

    view: bool = False
    view_all: bool = False
    #: "Thêm tài nguyên" (2026-10-21: the Phòng Tài nguyên).
    create: bool = False
    #: Edit EVERY resource and change any holder; one's own (and a head's
    #: department's people's) are editable without it - see ``can_edit``.
    edit: bool = False
    import_excel: bool = False
    settings: bool = False
    report: bool = False
    hr: bool = False

    @classmethod
    def from_access(cls, access: ResourceAccess) -> ResourceAccessResponse:
        return cls(
            view=access.any_view,
            view_all=access.view_all,
            create=access.create,
            edit=access.edit,
            import_excel=access.import_excel,
            settings=access.settings,
            report=access.report,
            hr=access.hr,
        )


# --- pick-lists -----------------------------------------------------------------------


class OptionResponse(BaseModel):
    id: uuid.UUID
    kind: str
    code: str
    name: str
    status_group: str | None = None
    status_group_label: str | None = None
    sort_order: int
    active: bool

    @classmethod
    def from_row(cls, row: ResourceOption) -> OptionResponse:
        return cls(
            id=row.id,
            kind=row.kind.value,
            code=row.code,
            name=row.name,
            status_group=None if row.status_group is None else row.status_group.value,
            status_group_label=None
            if row.status_group is None
            else STATUS_GROUP_LABELS[row.status_group],
            sort_order=row.sort_order,
            active=row.active,
        )


class OptionKindResponse(BaseModel):
    kind: str
    label: str
    options: list[OptionResponse]


class DepartmentRefResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    function: str = "NONE"
    function_label: str = ""

    @classmethod
    def from_row(cls, row: Department) -> DepartmentRefResponse:
        return cls(
            id=row.id,
            code=row.code,
            name=row.name,
            function=row.function.value,
            function_label=FUNCTION_LABELS[row.function],
        )


class OptionsResponse(BaseModel):
    """Every pick-list, the departments and the fixed vocabularies the forms need."""

    kinds: list[OptionKindResponse]
    departments: list[DepartmentRefResponse]
    handover_reasons: list[dict[str, str]]
    employment_statuses: list[dict[str, str]]
    status_groups: list[dict[str, str]]
    alert_kinds: list[dict[str, str]]
    review_days: int
    can_manage: bool = False

    @classmethod
    def build(
        cls,
        *,
        kinds: dict[OptionKind, list[ResourceOption]],
        departments: list[Department],
        review_days: int,
        can_manage: bool,
    ) -> OptionsResponse:
        return cls(
            kinds=[
                OptionKindResponse(
                    kind=kind.value,
                    label=OPTION_KIND_LABELS[kind],
                    options=[OptionResponse.from_row(row) for row in kinds.get(kind, [])],
                )
                for kind in OptionKind
            ],
            departments=[DepartmentRefResponse.from_row(row) for row in departments],
            handover_reasons=[
                {"value": reason.value, "label": HANDOVER_REASON_LABELS[reason]}
                for reason in HandoverReason
            ],
            employment_statuses=[
                {"value": status.value, "label": EMPLOYMENT_STATUS_LABELS[status]}
                for status in EmploymentStatus
            ],
            status_groups=[
                {"value": group.value, "label": STATUS_GROUP_LABELS[group]} for group in StatusGroup
            ],
            alert_kinds=[{"value": kind.value, "label": ALERT_LABELS[kind]} for kind in AlertKind],
            review_days=review_days,
            can_manage=can_manage,
        )


class CreateOptionRequest(BaseModel):
    kind: OptionKind
    name: str = Field(min_length=1, max_length=200)
    status_group: StatusGroup | None = None


class UpdateOptionRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    status_group: StatusGroup | None = None


class ReorderOptionsRequest(BaseModel):
    kind: OptionKind
    ids: list[uuid.UUID] = Field(max_length=500)


class SettingsResponse(BaseModel):
    review_days: int


class UpdateSettingsRequest(BaseModel):
    review_days: int = Field(ge=1, le=3650)


# --- people ---------------------------------------------------------------------------


class PersonResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    active: bool
    employment_status: str
    employment_status_label: str
    departments: list[DepartmentRefResponse] = []

    @classmethod
    def from_domain(cls, person: Person) -> PersonResponse:
        return cls(
            user_id=person.user_id,
            full_name=person.full_name,
            active=person.active,
            employment_status=person.employment_status.value,
            employment_status_label=person.employment_status_label,
            departments=[DepartmentRefResponse.from_row(row) for row in person.departments],
        )


class PeopleResponse(BaseModel):
    people: list[PersonResponse]


# --- resources ------------------------------------------------------------------------


class AlertResponse(BaseModel):
    kind: str
    label: str
    level: str


class ResourceRowResponse(BaseModel):
    id: uuid.UUID
    code: str
    type: OptionResponse
    external_id: str
    name: str
    link: str | None = None
    department: DepartmentRefResponse
    holder: PersonResponse | None = None
    role: OptionResponse | None = None
    email: str | None = None
    phone: str | None = None
    two_factor: OptionResponse | None = None
    two_factor_holder: PersonResponse | None = None
    origin: OptionResponse | None = None
    started_on: date | None = None
    purpose: str | None = None
    handover: OptionResponse
    status: OptionResponse
    reviewed_at: datetime | None = None
    note: str | None = None
    alerts: list[AlertResponse] = []
    #: Red row: a holder who left / was deactivated / changed department, or a loss.
    flagged: bool = False
    #: The note or the purpose reads like a password (rule 1).
    secret_warning: bool = False
    created_at: datetime
    updated_at: datetime
    detail_path: str

    @classmethod
    def from_row(cls, row: ResourceRow) -> ResourceRowResponse:
        resource = row.resource
        return cls(
            id=resource.id,
            code=resource.code,
            type=OptionResponse.from_row(row.type),
            external_id=resource.external_id,
            name=resource.name,
            link=resource.link,
            department=DepartmentRefResponse.from_row(row.department),
            holder=None if row.holder is None else PersonResponse.from_domain(row.holder),
            role=None if row.role is None else OptionResponse.from_row(row.role),
            email=resource.email,
            phone=resource.phone,
            two_factor=None if row.two_factor is None else OptionResponse.from_row(row.two_factor),
            two_factor_holder=None
            if row.two_factor_holder is None
            else PersonResponse.from_domain(row.two_factor_holder),
            origin=None if row.origin is None else OptionResponse.from_row(row.origin),
            started_on=resource.started_on,
            purpose=resource.purpose,
            handover=OptionResponse.from_row(row.handover),
            status=OptionResponse.from_row(row.status),
            reviewed_at=resource.reviewed_at,
            note=resource.note,
            alerts=[
                AlertResponse(kind=alert.kind.value, label=alert.label, level=alert.level)
                for alert in row.alerts
            ],
            flagged=row.flagged,
            secret_warning=row.secret_warning,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
            detail_path=f"/resources/{resource.id}",
        )


class ResourceListResponse(BaseModel):
    items: list[ResourceRowResponse]
    total: int
    limit: int
    offset: int
    review_days: int

    @classmethod
    def from_page(cls, page: ResourcePage, *, limit: int, offset: int) -> ResourceListResponse:
        return cls(
            items=[ResourceRowResponse.from_row(row) for row in page.rows],
            total=page.total,
            limit=limit,
            offset=offset,
            review_days=page.review_days,
        )


class HandoverResponse(BaseModel):
    id: uuid.UUID
    at: datetime
    from_person: PersonResponse | None = None
    to_person: PersonResponse | None = None
    reason: str
    reason_label: str
    note: str | None = None
    actor: PersonResponse | None = None

    @classmethod
    def from_line(cls, line: HandoverLine) -> HandoverResponse:
        return cls(
            id=line.row.id,
            at=line.row.created_at,
            from_person=None
            if line.from_person is None
            else PersonResponse.from_domain(line.from_person),
            to_person=None
            if line.to_person is None
            else PersonResponse.from_domain(line.to_person),
            reason=line.row.reason.value,
            reason_label=HANDOVER_REASON_LABELS[line.row.reason],
            note=line.row.note,
            actor=None if line.actor is None else PersonResponse.from_domain(line.actor),
        )


class StatusLineResponse(BaseModel):
    id: uuid.UUID
    at: datetime
    from_status: OptionResponse | None = None
    to_status: OptionResponse
    review_only: bool
    note: str | None = None
    actor: PersonResponse | None = None

    @classmethod
    def from_line(cls, line: StatusLine) -> StatusLineResponse:
        return cls(
            id=line.row.id,
            at=line.row.created_at,
            from_status=None
            if line.from_status is None
            else OptionResponse.from_row(line.from_status),
            to_status=OptionResponse.from_row(line.to_status),
            review_only=line.row.review_only,
            note=line.row.note,
            actor=None if line.actor is None else PersonResponse.from_domain(line.actor),
        )


class ChangeResponse(BaseModel):
    id: uuid.UUID
    at: datetime
    field: str
    field_label: str
    old_value: str | None = None
    new_value: str | None = None
    actor: PersonResponse | None = None

    @classmethod
    def from_line(cls, line: ChangeLine) -> ChangeResponse:
        return cls(
            id=line.row.id,
            at=line.row.created_at,
            field=line.row.field,
            field_label=line.field_label,
            old_value=line.row.old_value,
            new_value=line.row.new_value,
            actor=None if line.actor is None else PersonResponse.from_domain(line.actor),
        )


class ResourceDetailResponse(BaseModel):
    resource: ResourceRowResponse
    handovers: list[HandoverResponse]
    statuses: list[StatusLineResponse]
    changes: list[ChangeResponse]
    can_edit: bool
    #: "Đổi người được bàn giao" (whoever edits every resource).
    can_handover: bool = False

    @classmethod
    def from_detail(cls, detail: ResourceDetail) -> ResourceDetailResponse:
        return cls(
            resource=ResourceRowResponse.from_row(detail.row),
            handovers=[HandoverResponse.from_line(line) for line in detail.handovers],
            statuses=[StatusLineResponse.from_line(line) for line in detail.statuses],
            changes=[ChangeResponse.from_line(line) for line in detail.changes],
            can_edit=detail.can_edit,
            can_handover=detail.can_handover,
        )


class ResourceBody(BaseModel):
    """The form's fields. ``PATCH`` sends the ones that changed."""

    department_id: uuid.UUID | None = None
    type_id: uuid.UUID | None = None
    external_id: str | None = Field(default=None, max_length=400)
    name: str | None = Field(default=None, max_length=600)
    link: str | None = Field(default=None, max_length=4000)
    holder_user_id: uuid.UUID | None = None
    role_id: uuid.UUID | None = None
    email: str | None = Field(default=None, max_length=400)
    phone: str | None = Field(default=None, max_length=80)
    two_factor_id: uuid.UUID | None = None
    two_factor_holder_user_id: uuid.UUID | None = None
    origin_id: uuid.UUID | None = None
    started_on: date | None = None
    purpose: str | None = Field(default=None, max_length=8000)
    handover_id: uuid.UUID | None = None
    status_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=8000)


class CreateResourceRequest(ResourceBody):
    department_id: uuid.UUID
    type_id: uuid.UUID
    #: Optional: blank = the resource code stands in.
    name: str = Field(max_length=600)


class ChangeHolderRequest(BaseModel):
    holder_user_id: uuid.UUID | None = None
    reason: HandoverReason
    note: str | None = Field(default=None, max_length=4000)
    handover_id: uuid.UUID | None = None


class UpdateStatusRequest(BaseModel):
    status_id: uuid.UUID
    note: str | None = Field(default=None, max_length=4000)


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


# --- look-up and reports ----------------------------------------------------------------


class TypeCountResponse(BaseModel):
    type: OptionResponse
    count: int


class PersonLookupResponse(BaseModel):
    person: PersonResponse
    total: int
    by_type: list[TypeCountResponse]
    holding: list[ResourceRowResponse]
    held_before: list[ResourceRowResponse]

    @classmethod
    def from_domain(cls, lookup: PersonLookup) -> PersonLookupResponse:
        return cls(
            person=PersonResponse.from_domain(lookup.person),
            total=len(lookup.holding),
            by_type=[
                TypeCountResponse(type=OptionResponse.from_row(option), count=count)
                for option, count in lookup.by_type
            ],
            holding=[ResourceRowResponse.from_row(row) for row in lookup.holding],
            held_before=[ResourceRowResponse.from_row(row) for row in lookup.held_before],
        )


class DepartmentLineResponse(BaseModel):
    department: DepartmentRefResponse
    by_type: dict[str, int]
    total: int
    handed_over: int
    handover_rate: float


class StatusCountResponse(BaseModel):
    status: OptionResponse
    count: int


class SummaryResponse(BaseModel):
    month: str
    types: list[OptionResponse]
    departments: list[DepartmentLineResponse]
    total: int
    handed_over: int
    handover_rate: float
    by_status: list[StatusCountResponse]
    by_group: list[dict[str, Any]]

    @classmethod
    def from_domain(cls, summary: Summary) -> SummaryResponse:
        return cls(
            month=summary.month,
            types=[OptionResponse.from_row(option) for option in summary.types],
            departments=[
                DepartmentLineResponse(
                    department=DepartmentRefResponse.from_row(line.department),
                    by_type={str(type_id): count for type_id, count in line.by_type.items()},
                    total=line.total,
                    handed_over=line.handed_over,
                    handover_rate=line.handover_rate,
                )
                for line in summary.departments
            ],
            total=summary.total,
            handed_over=summary.handed_over,
            handover_rate=round(summary.handed_over / summary.total, 4) if summary.total else 0.0,
            by_status=[
                StatusCountResponse(status=OptionResponse.from_row(item.status), count=item.count)
                for item in summary.by_status
            ],
            by_group=[
                {"group": group.value, "label": STATUS_GROUP_LABELS[group], "count": count}
                for group, count in summary.by_group.items()
            ],
        )


class WarningsResponse(BaseModel):
    items: list[ResourceRowResponse]
    counts: list[dict[str, Any]]
    review_days: int

    @classmethod
    def from_domain(cls, warnings: Warnings) -> WarningsResponse:
        return cls(
            items=[ResourceRowResponse.from_row(row) for row in warnings.rows],
            counts=[
                {"kind": kind.value, "label": ALERT_LABELS[kind], "count": count}
                for kind, count in warnings.counts.items()
            ],
            review_days=warnings.review_days,
        )


# --- import ---------------------------------------------------------------------------


#: 8 MB of file is a third more in base64.
MAX_IMPORT_CHARS = 12 * 1024 * 1024


class ImportFileRequest(BaseModel):
    filename: str = Field(default="", max_length=300)
    #: The ``.xlsx`` file, base64.
    data: str = Field(min_length=1, max_length=MAX_IMPORT_CHARS)
    #: Add the pick-list entries the file uses and the lists lack (type,
    #: role, origin...; never a status) instead of refusing the row.
    create_options: bool = False


class ImportRowResponse(BaseModel):
    sheet: str
    row_no: int
    department: DepartmentRefResponse | None = None
    code: str | None = None
    raw: dict[str, str]
    months: list[dict[str, str]]
    errors: list[str]
    warnings: list[str]
    ok: bool

    @classmethod
    def from_row(cls, row: ImportRow) -> ImportRowResponse:
        return cls(
            sheet=row.sheet,
            row_no=row.row_no,
            department=None
            if row.department is None
            else DepartmentRefResponse.from_row(row.department),
            code=row.code,
            raw=row.raw,
            months=[{"month": month, "status": option.name} for month, option in row.months],
            errors=row.errors,
            warnings=row.warnings,
            ok=row.ok,
        )


class ImportSheetResponse(BaseModel):
    name: str
    department: DepartmentRefResponse | None = None
    rows: int
    errors: int
    skipped: bool = False
    unknown_headers: list[str] = []

    @classmethod
    def from_sheet(cls, sheet: SheetSummary) -> ImportSheetResponse:
        return cls(
            name=sheet.name,
            department=None
            if sheet.department is None
            else DepartmentRefResponse.from_row(sheet.department),
            rows=sheet.rows,
            errors=sheet.errors,
            skipped=sheet.skipped,
            unknown_headers=sheet.unknown_headers,
        )


class ImportPreviewResponse(BaseModel):
    sheets: list[ImportSheetResponse]
    rows: list[ImportRowResponse]
    ok_count: int
    error_count: int
    #: Pre-numbered template lines left out (a code and nothing else).
    skipped_rows: int = 0
    #: Pick-list entries the file uses and the lists lack: ``{kind, label, name}``.
    missing_options: list[dict[str, str]] = []
    written: bool = False

    @classmethod
    def from_preview(
        cls, preview: ImportPreview, *, written: bool = False
    ) -> ImportPreviewResponse:
        return cls(
            sheets=[ImportSheetResponse.from_sheet(sheet) for sheet in preview.sheets],
            rows=[ImportRowResponse.from_row(row) for row in preview.rows],
            ok_count=preview.ok_count,
            error_count=preview.error_count,
            skipped_rows=preview.skipped_rows,
            missing_options=[
                {"kind": kind.value, "label": OPTION_KIND_LABELS[kind], "name": text}
                for kind, text in preview.missing_options
            ],
            written=written,
        )


__all__ = [
    "ChangeHolderRequest",
    "CreateOptionRequest",
    "CreateResourceRequest",
    "ImportFileRequest",
    "ImportPreviewResponse",
    "OptionsResponse",
    "PeopleResponse",
    "PersonLookupResponse",
    "PersonResponse",
    "ReorderOptionsRequest",
    "ResourceAccessResponse",
    "ResourceBody",
    "ResourceDetailResponse",
    "ResourceListResponse",
    "ResourceRowResponse",
    "ReviewRequest",
    "SettingsResponse",
    "SummaryResponse",
    "UpdateOptionRequest",
    "UpdateSettingsRequest",
    "UpdateStatusRequest",
    "WarningsResponse",
]
