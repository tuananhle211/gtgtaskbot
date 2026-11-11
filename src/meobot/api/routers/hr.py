"""``/api/hr``: hồ sơ nhân sự (0096, ``docs/hr/HR_MODULE_PLAN.md`` §5).

Who sees what is :mod:`meobot.application.hr.access`: everybody their own
profile (``/me``), a Trưởng phòng their departments' people, HCNS everybody;
sensitive parts only for oneself and ``SENSITIVE`` holders. 403
``hr_forbidden``, 404 ``hr_not_found`` (also for a profile one may not see),
422 ``hr_*`` reasons.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel, Field
from sqlalchemy import select

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SessionDep,
    SettingsDep,
    UnitMembershipDep,
)
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.application.hr.access import HrAccess, HrForbiddenError, hr_access
from meobot.application.hr.files import HrFileService
from meobot.application.hr.profiles import HrProfileService, HrValidationError
from meobot.db.models.department import Department
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.hr.profiles import (
    CONTRACT_STATE_LABELS,
    DOCUMENT_KINDS,
    GENDERS,
    MARITAL,
    MAX_DOCUMENT_BYTES,
    REQUEST_FIELDS,
    SALARY_TYPES,
    WORK_FORMS,
)
from meobot.domain.org.models import BRANCHES, Position
from meobot.domain.resources.models import EMPLOYMENT_STATUS_LABELS, EmploymentStatus

router = APIRouter(prefix="/api/hr", tags=["hr"])


async def _ctx(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> tuple[HrProfileService, HrAccess]:
    return (
        HrProfileService(session, settings, AuditService(session)),
        await hr_access(session, membership),
    )


# --- request bodies ---------------------------------------------------------------------


class ProfileBody(BaseModel):
    """Sent keys only; ``null`` clears. Money in đồng."""

    full_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    branch: str | None = Field(default=None, max_length=20)
    legal_entity_id: uuid.UUID | None = None
    employment_status: EmploymentStatus | None = None
    grade: str | None = Field(default=None, max_length=40)
    work_form: str | None = Field(default=None, max_length=60)
    work_email: str | None = Field(default=None, max_length=200)
    joined_on: date | None = None
    official_on: date | None = None
    left_on: date | None = None
    leave_reason: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)
    gender: str | None = Field(default=None, max_length=10)
    birth_date: date | None = None
    birth_place: str | None = Field(default=None, max_length=200)
    nationality: str | None = Field(default=None, max_length=60)
    ethnicity: str | None = Field(default=None, max_length=60)
    religion: str | None = Field(default=None, max_length=60)
    marital_status: str | None = Field(default=None, max_length=30)
    id_number: str | None = Field(default=None, max_length=30)
    id_issued_on: date | None = None
    id_issued_by: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    personal_email: str | None = Field(default=None, max_length=200)
    drive_email: str | None = Field(default=None, max_length=200)
    permanent_address: str | None = Field(default=None, max_length=500)
    current_address: str | None = Field(default=None, max_length=500)
    bank_name: str | None = Field(default=None, max_length=100)
    bank_account: str | None = Field(default=None, max_length=40)
    bank_holder: str | None = Field(default=None, max_length=200)
    base_salary: int | None = Field(default=None, ge=0)
    allowance: int | None = Field(default=None, ge=0)
    insurance_salary: int | None = Field(default=None, ge=0)
    salary_type: str | None = Field(default=None, max_length=40)
    salary_note: str | None = Field(default=None, max_length=500)
    social_insurance_no: str | None = Field(default=None, max_length=30)
    social_insurance_from: date | None = None
    health_insurance_no: str | None = Field(default=None, max_length=30)
    tax_code: str | None = Field(default=None, max_length=20)
    dependants: int | None = Field(default=None, ge=0, le=30)
    # 0097
    team: str | None = Field(default=None, max_length=120)
    fingerprint_id: str | None = Field(default=None, max_length=30)
    fingerprint_on: date | None = None
    fingerprint_floor: str | None = Field(default=None, max_length=30)
    bank2_name: str | None = Field(default=None, max_length=100)
    bank_account_2: str | None = Field(default=None, max_length=40)
    bank2_holder: str | None = Field(default=None, max_length=200)
    insurance_title: str | None = Field(default=None, max_length=200)
    scan_note: str | None = Field(default=None, max_length=1000)
    paper_documents: list[str] | None = Field(default=None, max_length=20)


def _sent(body: BaseModel) -> dict[str, Any]:
    return {key: getattr(body, key) for key in body.model_fields_set}


class CreateBody(ProfileBody):
    member_code: str = Field(min_length=1, max_length=40)
    full_name: str = Field(min_length=1, max_length=200)
    department_id: uuid.UUID
    position: Position = Position.MEMBER


class BulkBody(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    fields: dict[str, Any]


class ContractBody(BaseModel):
    type_code: str | None = Field(default=None, max_length=20)
    number: str | None = Field(default=None, max_length=80)
    start_on: date | None = None
    end_on: date | None = None
    signed: bool | None = None
    signed_by: str | None = Field(default=None, max_length=200)
    ended_on: date | None = None
    salary: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=2000)


class RenewBody(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    type_code: str = Field(min_length=1, max_length=20)
    start_on: date | None = None
    signed: bool = False


class SettingsBody(BaseModel):
    contract_warn_days: int = Field(ge=1, le=365)
    required_documents: list[str] = Field(default_factory=list, max_length=20)


class ContractTypeBody(BaseModel):
    code: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    months: int | None = Field(default=None, ge=0, le=240)
    active: bool | None = None


class RequestBody(BaseModel):
    field: str = Field(min_length=1, max_length=40)
    value: str | None = Field(default=None, max_length=500)


class ScanLinkBody(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    url: str = Field(min_length=1, max_length=2000)


class DecideBody(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


# --- reads ---------------------------------------------------------------------------------


@router.get("/access")
async def access_of(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    types = await service.contract_types()
    # Every active department, whatever the reader may manage on "Phòng ban":
    # the list's filter and the new-employee form pick from the whole company.
    departments = (
        await session.scalars(
            select(Department)
            .where(Department.active.is_(True))
            .order_by(Department.block.nulls_last(), Department.name)
        )
    ).all()
    return {
        "lists": access.lists,
        "overview": access.view_all,
        "edit": access.edit,
        "sensitive": access.sensitive,
        "export": access.export,
        "choices": {
            "branches": [{"code": code, "label": label} for code, label in BRANCHES.items()],
            "statuses": [
                {"code": item.value, "label": label}
                for item, label in EMPLOYMENT_STATUS_LABELS.items()
            ],
            "genders": [{"code": code, "label": label} for code, label in GENDERS.items()],
            "marital": list(MARITAL),
            "work_forms": list(WORK_FORMS),
            "salary_types": list(SALARY_TYPES),
            "document_kinds": [
                {"code": code, "label": label} for code, label in DOCUMENT_KINDS.items()
            ],
            "request_fields": [
                {"code": code, "label": label} for code, label in REQUEST_FIELDS.items()
            ],
            "contract_types": [
                {"code": row.code, "name": row.name, "months": row.months} for row in types
            ],
            "contract_states": [
                {"code": state.value, "label": label}
                for state, label in CONTRACT_STATE_LABELS.items()
            ],
            "max_document_mb": MAX_DOCUMENT_BYTES // (1024 * 1024),
            "departments": [
                {
                    "id": str(row.id),
                    "code": row.code,
                    "name": row.name,
                    "block": row.block,
                    "parent_id": None if row.parent_id is None else str(row.parent_id),
                }
                for row in departments
            ],
        },
        "settings": (await service.settings()).as_dict(),
    }


@router.get("/overview")
async def overview(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    return await service.overview(access=access, today=vn_today())


@router.get("/profiles")
async def list_profiles(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    q: str | None = Query(default=None, max_length=100),
    department_id: str | None = Query(default=None, max_length=40),
    branch: str | None = Query(default=None, max_length=20),
    employment_status: str | None = Query(default=None, max_length=20),
    legal_entity_id: str | None = Query(default=None, max_length=40),
    gender: str | None = Query(default=None, max_length=10),
    contract: str | None = Query(default=None, pattern="^(expiring|expired|none)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    if not access.lists:
        raise HrForbiddenError("Danh sách hồ sơ dành cho phòng Hành chính nhân sự và trưởng phòng.")
    rows = service.filter_rows(
        await service.rows(access=access, today=vn_today()),
        q=q,
        department_id=department_id,
        branch=branch,
        status=employment_status,
        legal_entity_id=legal_entity_id,
        gender=gender,
        contract=contract,
    )
    start = (page - 1) * page_size
    return {
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "items": rows[start : start + page_size],
    }


@router.get("/profiles/{user_id}")
async def get_profile(
    user_id: str,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """``me`` = one's own."""
    service, access = await _ctx(session, settings, membership)
    target = _user_id(user_id, access)
    return await service.profile(
        actor=actor, access=access, request_id=request_id, user_id=target, today=vn_today()
    )


def _user_id(value: str, access: HrAccess) -> uuid.UUID:
    if value == "me":
        if access.user_id is None:
            raise HrForbiddenError()
        return access.user_id
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HrValidationError("hr_invalid", "Mã người không hợp lệ.") from None


@router.get("/org")
async def org_chart(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    service, _access = await _ctx(session, settings, membership)
    return {"departments": await service.org_chart()}


def _profiles_workbook(rows: list[dict[str, Any]], sensitive: bool) -> bytes:
    """The "Hồ sơ nhân sự" sheet. Plain data in, bytes out: it runs in a
    worker thread so a big export does not hold up everybody else."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Hồ sơ nhân sự"
    head = [
        "Mã NV",
        "Họ và tên",
        "Giới tính",
        "Phòng ban",
        "Chức danh",
        "Cấp bậc",
        "Chi nhánh",
        "Pháp nhân",
        "Trạng thái",
        "Ngày vào làm",
        "Ngày chính thức",
        "Thâm niên",
        "Email công việc",
        "Loại HĐ hiện tại",
        "Số HĐ",
        "HĐ hết hạn",
    ]
    if sensitive:
        head += ["Ngày sinh", "Điện thoại", "Email cá nhân"]
    sheet.append(head)
    for row in rows:
        contract = row["contract"] or {}
        line = [
            row["member_code"],
            row["full_name"],
            row["gender_label"],
            ", ".join(seat["name"] for seat in row["departments"]),
            row["job_title"],
            row["grade"],
            row["branch_label"],
            row["legal_entity_code"],
            row["employment_status_label"],
            row["joined_on"],
            row["official_on"],
            row["seniority"],
            row["work_email"],
            contract.get("type_name"),
            contract.get("number"),
            contract.get("end_on"),
        ]
        if sensitive:
            line += [row.get("birth_date"), row.get("phone"), row.get("personal_email")]
        sheet.append(line)
    bold = Font(bold=True)
    for cell in sheet[1]:
        cell.font = bold
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@router.get("/export.xlsx")
async def export(
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    include_left: bool = Query(default=False),
) -> Response:
    service, access = await _ctx(session, settings, membership)
    if not access.export:
        raise HrForbiddenError("Bạn không có quyền xuất Excel hồ sơ.")
    rows = await service.rows(access=access, today=vn_today())
    if not include_left:
        rows = [row for row in rows if row["active"]]
    content = await asyncio.to_thread(_profiles_workbook, rows, access.sensitive)
    await AuditService(session).record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.HR_EXPORTED.value,
        result=AuditResult.SUCCESS,
        entity_type="hr_profile",
        after_data={"rows": len(rows), "sensitive": access.sensitive},
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="ho-so-nhan-su-{vn_today():%Y%m%d}.xlsx"',
            "Cache-Control": "no-store",
        },
    )


# --- writes --------------------------------------------------------------------------------


@router.patch("/profiles/{user_id}")
async def update_profile(
    user_id: uuid.UUID,
    body: ProfileBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    await service.update(
        actor=actor, access=access, request_id=request_id, user_id=user_id, fields=_sent(body)
    )
    return await service.profile(
        actor=actor, access=access, request_id=request_id, user_id=user_id, today=vn_today()
    )


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: CreateBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """A new employee: account (no Telegram, temporary password shown once),
    seat and profile."""
    service, access = await _ctx(session, settings, membership)
    fields = {
        key: value
        for key, value in _sent(body).items()
        if key not in ("member_code", "full_name", "department_id", "position")
    }
    user, password = await service.create_employee(
        actor=actor,
        access=access,
        request_id=request_id,
        member_code=body.member_code,
        full_name=body.full_name,
        department_id=body.department_id,
        position=body.position,
        fields=fields,
    )
    return {"user_id": str(user.id), "member_code": user.member_code, "password": password}


@router.post("/profiles/bulk")
async def bulk_update(
    body: BulkBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    fields = dict(body.fields)
    if fields.get("legal_entity_id"):
        fields["legal_entity_id"] = uuid.UUID(str(fields["legal_entity_id"]))
    if "employment_status" in fields and fields["employment_status"] is not None:
        try:
            fields["employment_status"] = EmploymentStatus(fields["employment_status"]).value
        except ValueError:
            raise HrValidationError("hr_bulk_invalid", "Trạng thái không hợp lệ.") from None
    changed = await service.bulk_update(
        actor=actor, access=access, request_id=request_id, user_ids=body.user_ids, fields=fields
    )
    return {"changed": changed}


@router.post("/profiles/{user_id}/contracts", status_code=status.HTTP_201_CREATED)
async def add_contract(
    user_id: uuid.UUID,
    body: ContractBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    row = await service.save_contract(
        actor=actor,
        access=access,
        request_id=request_id,
        user_id=user_id,
        contract_id=None,
        fields=_sent(body),
    )
    return {"id": str(row.id)}


@router.patch("/profiles/{user_id}/contracts/{contract_id}")
async def edit_contract(
    user_id: uuid.UUID,
    contract_id: uuid.UUID,
    body: ContractBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    row = await service.save_contract(
        actor=actor,
        access=access,
        request_id=request_id,
        user_id=user_id,
        contract_id=contract_id,
        fields=_sent(body),
    )
    return {"id": str(row.id)}


@router.delete("/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    contract_id: uuid.UUID,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> Response:
    service, access = await _ctx(session, settings, membership)
    await service.delete_contract(
        actor=actor, access=access, request_id=request_id, contract_id=contract_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/contracts/renew")
async def renew_contracts(
    body: RenewBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    made = await service.renew_contracts(
        actor=actor,
        access=access,
        request_id=request_id,
        user_ids=body.user_ids,
        type_code=body.type_code,
        start_on=body.start_on,
        today=vn_today(),
        signed=body.signed,
    )
    return {"created": made}


@router.put("/settings")
async def save_settings(
    body: SettingsBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    saved = await service.save_settings(
        actor=actor,
        access=access,
        request_id=request_id,
        contract_warn_days=body.contract_warn_days,
        required_documents=body.required_documents,
    )
    return saved.as_dict()


@router.get("/contract-types")
async def contract_types(
    session: SessionDep, settings: SettingsDep, membership: UnitMembershipDep
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    rows = await service.contract_types(include_inactive=access.edit)
    return {
        "types": [
            {
                "id": str(r.id),
                "code": r.code,
                "name": r.name,
                "months": r.months,
                "active": r.active,
            }
            for r in rows
        ]
    }


@router.post("/contract-types", status_code=status.HTTP_201_CREATED)
async def create_contract_type(
    body: ContractTypeBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    row = await service.save_contract_type(
        actor=actor, access=access, request_id=request_id, type_id=None, fields=_sent(body)
    )
    return {"id": str(row.id), "code": row.code}


@router.patch("/contract-types/{type_id}")
async def update_contract_type(
    type_id: uuid.UUID,
    body: ContractTypeBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service, access = await _ctx(session, settings, membership)
    row = await service.save_contract_type(
        actor=actor, access=access, request_id=request_id, type_id=type_id, fields=_sent(body)
    )
    return {"id": str(row.id), "code": row.code}


# --- documents -----------------------------------------------------------------------------


@router.post("/profiles/{user_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    user_id: uuid.UUID,
    request: Request,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    kind: Annotated[str, Query(min_length=1, max_length=20)],
    name: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict[str, Any]:
    """The raw bytes are the body (≤ 10 MB)."""
    _service, access = await _ctx(session, settings, membership)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise HrValidationError(
                "hr_document_too_large", f"Tệp lớn quá {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB."
            )
    row = await HrFileService(session, AuditService(session)).upload(
        actor=actor,
        access=access,
        request_id=request_id,
        user_id=user_id,
        kind=kind,
        name=name,
        data=bytes(data),
    )
    return {"id": str(row.id), "name": row.name, "kind": row.kind}


@router.get("/documents/{document_id}")
async def download_document(
    document_id: uuid.UUID,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> Response:
    _service, access = await _ctx(session, settings, membership)
    row = await HrFileService(session, AuditService(session)).download(
        actor=actor, access=access, request_id=request_id, document_id=document_id
    )
    inline = row.content_type.startswith("image/") or row.content_type == "application/pdf"
    safe = "".join(char if char.isascii() and char not in '"\\' else "_" for char in row.name)
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> Response:
    _service, access = await _ctx(session, settings, membership)
    await HrFileService(session, AuditService(session)).delete(
        actor=actor, access=access, request_id=request_id, document_id=document_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- scan links (0099) ---------------------------------------------------------------------


def _link(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "label": row.label,
        "url": row.url,
        "created_at": row.created_at,
    }


@router.post("/profiles/{user_id}/scan-links", status_code=status.HTTP_201_CREATED)
async def add_scan_link(
    user_id: uuid.UUID,
    body: ScanLinkBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """A Drive link of the person's scanned papers. EDIT + sensitive view;
    422 ``hr_scan_link_invalid`` (not http(s), too long, > 30) /
    ``hr_scan_link_duplicate``."""
    _service, access = await _ctx(session, settings, membership)
    row = await HrFileService(session, AuditService(session)).add_scan_link(
        actor=actor,
        access=access,
        request_id=request_id,
        user_id=user_id,
        label=body.label,
        url=body.url,
    )
    return _link(row)


@router.patch("/scan-links/{link_id}")
async def update_scan_link(
    link_id: uuid.UUID,
    body: ScanLinkBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _service, access = await _ctx(session, settings, membership)
    row = await HrFileService(session, AuditService(session)).update_scan_link(
        actor=actor,
        access=access,
        request_id=request_id,
        link_id=link_id,
        label=body.label,
        url=body.url,
    )
    return _link(row)


@router.delete("/scan-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan_link(
    link_id: uuid.UUID,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> Response:
    _service, access = await _ctx(session, settings, membership)
    await HrFileService(session, AuditService(session)).delete_scan_link(
        actor=actor, access=access, request_id=request_id, link_id=link_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- change requests -----------------------------------------------------------------------


@router.get("/requests")
async def list_requests(
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_status: str | None = Query(default=None, alias="status", max_length=20),
    mine: bool = Query(default=False),
) -> dict[str, Any]:
    _service, access = await _ctx(session, settings, membership)
    files = HrFileService(session, AuditService(session))
    return {
        "items": await files.requests(access=access, status=request_status, mine=mine),
        "can_decide": access.edit,
    }


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def submit_request(
    body: RequestBody,
    actor: CurrentActorDep,
    session: SessionDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    row = await HrFileService(session, AuditService(session)).submit(
        actor=actor, request_id=request_id, field=body.field, value=body.value
    )
    return {"id": str(row.id), "status": row.status}


@router.post("/requests/{change_id}/decide")
async def decide_request(
    change_id: uuid.UUID,
    body: DecideBody,
    actor: CurrentActorDep,
    session: SessionDep,
    settings: SettingsDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    _service, access = await _ctx(session, settings, membership)
    row = await HrFileService(session, AuditService(session)).decide(
        actor=actor,
        access=access,
        request_id=request_id,
        change_id=change_id,
        approve=body.approve,
        note=body.note,
    )
    return {"id": str(row.id), "status": row.status}
