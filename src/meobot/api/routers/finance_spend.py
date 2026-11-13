"""Tài chính đợt 3 (0107): hồ sơ chi, mã chi & tuyến duyệt, vai trò tài chính,
ngân sách, đối soát MISA, thu - chi. Same ``/api/finance`` prefix as
:mod:`meobot.api.routers.finance` (included right after it).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SettingsDep,
    UnitMembershipDep,
    get_drive_client,
    get_session,
)
from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess, load_finance_access
from meobot.application.finance.budgets import FinanceBudgetService
from meobot.application.finance.codes import (
    CASH_ROLES,
    EXECUTOR_ROLES,
    PAYMENT_ROLES,
    PURCHASE_ROLES,
    FinanceCodeService,
)
from meobot.application.finance.misa import FinanceMisaService
from meobot.application.finance.pnl import FinancePnlService, totals
from meobot.application.finance.requests import FinanceRequestService, RequestView
from meobot.application.finance.roles import FinanceRoleService
from meobot.application.org.seats import load_seats
from meobot.db.models.department import Department
from meobot.db.models.finance_spend import FinanceCode, FinanceRequest
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.finance.crm import whole
from meobot.domain.finance.errors import FinanceForbiddenError, FinanceInvalidError
from meobot.domain.finance.spend import (
    ASSIGNABLE,
    DOCUMENT_LABELS,
    PHASE_LABELS,
    ROLE_LABELS,
    STATUS_LABELS,
    DocumentKind,
    FinanceRole,
    Phase,
    RequestStatus,
    Route,
)
from meobot.domain.org.models import BRANCHES
from meobot.integrations.google.drive import DriveClient

router = APIRouter(prefix="/api/finance", tags=["finance"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
MAX_UPLOAD = 10 * 1024 * 1024


async def _access(session: SessionDep, membership: UnitMembershipDep) -> FinanceAccess:
    access = await load_finance_access(session, membership)
    if not access.sees:
        raise FinanceForbiddenError()
    return access


AccessDep = Annotated[FinanceAccess, Depends(_access)]


def _month(value: str | None) -> date:
    if not value:
        return vn_today().replace(day=1)
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as error:
        raise FinanceInvalidError("month_invalid", "Tháng dạng YYYY-MM.", field="month") from error


async def _body(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD:
            raise FinanceInvalidError("file_invalid", "Tệp lớn hơn 10 MB.")
    return bytes(data)


def _requests(
    session: AsyncSession, settings: Any, drive: DriveClient | None = None
) -> FinanceRequestService:
    return FinanceRequestService(session, settings, AuditService(session), drive=drive)


def _labels(roles: tuple[FinanceRole, ...] | list[FinanceRole]) -> list[dict[str, str]]:
    return [{"value": role.value, "label": ROLE_LABELS[role]} for role in roles]


# --- catalogues -------------------------------------------------------------------------------


@router.get("/spend/catalog")
async def catalog(session: SessionDep, access: AccessDep) -> dict[str, Any]:
    """Labels and choices for the forms."""
    return {
        "roles": _labels(list(FinanceRole)),
        "assignable": _labels(ASSIGNABLE),
        "purchase_roles": _labels(PURCHASE_ROLES),
        "payment_roles": _labels(PAYMENT_ROLES),
        "executor_roles": _labels(EXECUTOR_ROLES),
        "cash_roles": _labels(CASH_ROLES),
        "documents": [
            {"value": kind.value, "label": label} for kind, label in DOCUMENT_LABELS.items()
        ],
        "phases": [{"value": phase.value, "label": label} for phase, label in PHASE_LABELS.items()],
        "statuses": [
            {"value": value.value, "label": label} for value, label in STATUS_LABELS.items()
        ],
        "branches": [{"value": code, "label": label} for code, label in BRANCHES.items()],
        "entities": [
            {"value": str(row.id), "label": f"{row.code} - {row.name}"}
            for row in await session.scalars(
                select(LegalEntity).where(LegalEntity.active.is_(True)).order_by(LegalEntity.code)
            )
        ],
        "departments": [
            {"value": str(row.id), "label": f"{row.code} - {row.name}"}
            for row in await session.scalars(
                select(Department).where(Department.active.is_(True)).order_by(Department.code)
            )
        ],
        "access": {
            "view_requests": access.view_requests,
            "codes": access.codes,
            "budget": access.budget,
            "misa": access.misa,
            "report": access.report,
        },
    }


def _code(row: FinanceCode) -> dict[str, Any]:
    route = Route.from_value(row.route)
    return {
        "code": row.code,
        "name": row.name,
        "parent_code": row.parent_code,
        "kind": row.kind,
        "leaf": row.leaf,
        "active": row.active,
        "route": route.as_value(),
        "no_route": row.leaf and route.empty,
    }


@router.get("/codes")
async def codes(session: SessionDep, access: AccessDep) -> dict[str, Any]:
    rows = await FinanceCodeService(session, AuditService(session)).codes()
    return {"codes": [_code(row) for row in rows], "can_edit": access.codes}


class CodeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    active: bool | None = None
    route: dict[str, Any] | None = None


@router.patch("/codes/{code}")
async def update_code(
    code: str,
    body: CodeUpdate,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    row = await FinanceCodeService(session, AuditService(session)).update(
        actor=actor,
        access=access,
        request_id=request_id,
        code=code,
        name=body.name,
        active=body.active,
        route=body.route,
    )
    return _code(row)


@router.get("/codes/{code}/history")
async def code_history(code: str, session: SessionDep, access: AccessDep) -> list[dict[str, Any]]:
    service = FinanceCodeService(session, AuditService(session))
    rows = await service.history(code)
    names = await _names(session, {row.changed_by_user_id for row in rows})
    return [
        {
            "at": row.changed_at.isoformat(),
            "by": names.get(row.changed_by_user_id) if row.changed_by_user_id else None,
            "before": row.before,
            "after": row.after,
        }
        for row in rows
    ]


async def _names(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {item for item in ids if item}
    if not wanted:
        return {}
    return {
        user.id: user.full_name or ""
        for user in await session.scalars(select(User).where(User.id.in_(wanted)))
    }


# --- roles + settings ---------------------------------------------------------------------------


@router.get("/roles")
async def roles(session: SessionDep, settings: SettingsDep, access: AccessDep) -> dict[str, Any]:
    rows = await FinanceRoleService(session, settings, AuditService(session)).assignments()
    wanted: set[uuid.UUID | None] = {row.user_id for row in rows}
    wanted |= {row.standin_user_id for row in rows}
    names = await _names(session, wanted)
    entities = {row.id: row.code for row in await session.scalars(select(LegalEntity))}
    people = (
        await session.scalars(
            select(User)
            .where(User.deleted_at.is_(None), User.status == UserStatus.ACTIVE)
            .order_by(User.full_name)
        )
    ).all()
    return {
        "assignments": [
            {
                "id": row.id,
                "role": row.role,
                "role_label": ROLE_LABELS[FinanceRole(row.role)],
                "user_id": str(row.user_id),
                "user_name": names.get(row.user_id, ""),
                "legal_entity_id": str(row.legal_entity_id) if row.legal_entity_id else None,
                "legal_entity": entities.get(row.legal_entity_id) if row.legal_entity_id else None,
                "apm_branch": row.apm_branch,
                "standin_user_id": str(row.standin_user_id) if row.standin_user_id else None,
                "standin_name": names.get(row.standin_user_id) if row.standin_user_id else None,
                "note": row.note,
            }
            for row in rows
        ],
        "people": [
            {"id": str(user.id), "name": user.full_name or "", "member_code": user.member_code}
            for user in people
        ],
        "can_edit": access.codes,
    }


class RoleAdd(BaseModel):
    role: str = Field(max_length=30)
    user_id: uuid.UUID
    legal_entity_id: uuid.UUID | None = None
    apm_branch: str | None = Field(default=None, max_length=10)
    standin_user_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)


@router.post("/roles", status_code=status.HTTP_201_CREATED)
async def add_role(
    body: RoleAdd,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    row = await FinanceRoleService(session, settings, AuditService(session)).add(
        actor=actor,
        access=access,
        request_id=request_id,
        role=body.role,
        user_id=body.user_id,
        legal_entity_id=body.legal_entity_id,
        apm_branch=body.apm_branch,
        standin_user_id=body.standin_user_id,
        note=body.note,
    )
    return {"id": row.id}


@router.delete("/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(
    assignment_id: int,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await FinanceRoleService(session, settings, AuditService(session)).remove(
        actor=actor, access=access, request_id=request_id, assignment_id=assignment_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SpendSettings(BaseModel):
    ceo_threshold: str | None = Field(default=None, max_length=20)
    drive_folder_id: str | None = Field(default=None, max_length=200)


@router.get("/spend-settings")
async def spend_settings(session: SessionDep, access: AccessDep) -> dict[str, Any]:
    value = await FinanceCodeService(session, AuditService(session)).spend_settings()
    return {
        "ceo_threshold": int(value["ceo_threshold"]) if value["ceo_threshold"] else None,
        "drive_folder_id": value["drive_folder_id"],
        "can_edit": access.codes,
    }


@router.put("/spend-settings")
async def set_spend_settings(
    body: SpendSettings,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    value = await FinanceCodeService(session, AuditService(session)).set_spend_settings(
        actor=actor,
        access=access,
        request_id=request_id,
        ceo_threshold=body.ceo_threshold,
        drive_folder_id=body.drive_folder_id,
    )
    return {
        "ceo_threshold": int(value["ceo_threshold"]) if value["ceo_threshold"] else None,
        "drive_folder_id": value["drive_folder_id"],
        "can_edit": True,
    }


# --- hồ sơ chi -----------------------------------------------------------------------------------


def _row(request: FinanceRequest, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": str(request.id),
        "code": request.code,
        "title": request.title,
        "amount": int(whole(Decimal(request.amount))),
        "budget_code": request.budget_code,
        "status": request.status,
        "status_label": STATUS_LABELS[RequestStatus(request.status)],
        "phase": request.phase,
        "phase_label": PHASE_LABELS[Phase(request.phase)] if request.phase else None,
        "requester": names.get(request.requester_id, ""),
        "created_at": request.created_at.isoformat(),
        "paid_at": request.paid_at.isoformat() if request.paid_at else None,
        "docs_due_on": request.docs_due_on.isoformat() if request.docs_due_on else None,
    }


@router.get("/requests")
async def list_requests(
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    tab: str = Query(default="mine", pattern="^(mine|awaiting|all)$"),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    q: str = Query(default="", max_length=100),
) -> dict[str, Any]:
    service = _requests(session, settings)
    rows = await service.listing(actor=actor, access=access, tab=tab, status=status_filter, q=q)
    names = await _names(session, {row.requester_id for row in rows})
    return {
        "rows": [_row(row, names) for row in rows],
        "awaiting": await service.awaiting_count(actor),
    }


@router.get("/requests/form")
async def request_form(
    session: SessionDep, access: AccessDep, actor: CurrentActorDep
) -> dict[str, Any]:
    access.require("requests", "Bạn chưa lập được hồ sơ chi.")
    seats = (
        (await load_seats(session, [actor.user_id])).get(actor.user_id, ()) if actor.user_id else ()
    )
    user = await session.get(User, actor.user_id) if actor.user_id else None
    rows = await FinanceCodeService(session, AuditService(session)).codes(active_only=True)
    return {
        "codes": [_code(row) for row in rows],
        "my_departments": [str(seat.department_id) for seat in seats],
        "my_entity": str(user.legal_entity_id) if user and user.legal_entity_id else None,
        "my_branch": user.branch if user else None,
    }


class RequestCreate(BaseModel):
    legal_entity_id: uuid.UUID
    department_id: uuid.UUID | None = None
    apm_branch: str | None = Field(default=None, max_length=10)
    budget_code: str = Field(max_length=30)
    title: str = Field(max_length=300)
    description: str = Field(default="", max_length=5000)
    vendor: str | None = Field(default=None, max_length=300)
    payee: str | None = Field(default=None, max_length=500)
    amount: str = Field(max_length=20)
    budget_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    needed_on: date | None = None


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def create_request(
    body: RequestCreate,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    request = await _requests(session, settings).create(
        actor=actor,
        access=access,
        request_id=request_id,
        legal_entity_id=body.legal_entity_id,
        department_id=body.department_id,
        apm_branch=body.apm_branch,
        budget_code=body.budget_code,
        title=body.title,
        description=body.description,
        vendor=body.vendor,
        payee=body.payee,
        amount=body.amount,
        budget_month=_month(body.budget_month),
        needed_on=body.needed_on,
    )
    return {"id": str(request.id), "code": request.code}


def _detail(view: RequestView, access: FinanceAccess) -> dict[str, Any]:
    request = view.request
    names = view.names

    def name(person: uuid.UUID | None) -> str | None:
        return names.get(person) if person else None

    return {
        **_row(request, names),
        "description": request.description,
        "vendor": request.vendor,
        "payee": request.payee,
        "legal_entity_id": str(request.legal_entity_id),
        "legal_entity": view.entity_name,
        "department_id": str(request.department_id) if request.department_id else None,
        "department": view.department_name,
        "apm_branch": request.apm_branch,
        "branch": BRANCHES.get(request.apm_branch or "", None),
        "code_name": view.code_name,
        "budget_month": request.budget_month.strftime("%Y-%m"),
        "needed_on": request.needed_on.isoformat() if request.needed_on else None,
        "purchase_note": request.purchase_note,
        "realized_at": request.realized_at.isoformat() if request.realized_at else None,
        "version": request.version,
        "route": request.route,
        "steps": [
            {
                "id": step.id,
                "round": step.round,
                "phase": step.phase,
                "phase_label": PHASE_LABELS[Phase(step.phase)],
                "role": step.role,
                "role_label": ROLE_LABELS[FinanceRole(step.role)],
                "status": step.status,
                "approver": name(step.approver_id),
                "standin": name(step.standin_id),
                "decided_by": name(step.decided_by_user_id),
                "decided_at": step.decided_at.isoformat() if step.decided_at else None,
                "note": step.note,
                "remark": step.remark,
            }
            for step in view.steps
        ],
        "open_step_id": view.open_step.id if view.open_step else None,
        "events": [
            {
                "at": event.at.isoformat(),
                "by": name(event.actor_id),
                "action": event.action,
                "note": event.note,
            }
            for event in view.events
        ],
        "documents": [
            {
                "id": str(document.id),
                "kind": document.kind,
                "kind_label": DOCUMENT_LABELS[DocumentKind(document.kind)],
                "name": document.name,
                "size": document.size,
                "storage": document.storage,
                "by": name(document.uploaded_by_user_id),
                "at": document.created_at.isoformat(),
            }
            for document in view.documents
        ],
        "required": [
            {"value": kind.value, "label": DOCUMENT_LABELS[kind]}
            for kind in Route.from_value(request.route).documents
        ],
        "documents_note": Route.from_value(request.route).documents_note,
        "missing": [{"value": kind.value, "label": DOCUMENT_LABELS[kind]} for kind in view.missing],
        "budget": (
            {
                "budget": int(whole(view.budget.budget)),
                "committed": int(whole(view.budget.committed)),
                "realized": int(whole(view.budget.realized)),
                "remaining": int(whole(view.budget.remaining)),
            }
            if view.budget
            else None
        ),
        "can": view.can_act,
        "can_paper": access.view_requests
        or (
            view.open_step is not None
            and view.open_step.role.startswith("ACC_")
            and view.open_step.id is not None
        ),
    }


@router.get("/requests/{finance_request_id}")
async def request_detail(
    finance_request_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
) -> dict[str, Any]:
    view = await _requests(session, settings).detail(
        actor=actor, access=access, finance_request_id=finance_request_id
    )
    return _detail(view, access)


class RequestEdit(BaseModel):
    legal_entity_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    apm_branch: str | None = Field(default=None, max_length=10)
    budget_code: str | None = Field(default=None, max_length=30)
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    vendor: str | None = Field(default=None, max_length=300)
    payee: str | None = Field(default=None, max_length=500)
    amount: str | None = Field(default=None, max_length=20)
    budget_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    needed_on: date | None = None


@router.patch("/requests/{finance_request_id}")
async def edit_request(
    finance_request_id: uuid.UUID,
    body: RequestEdit,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    changes: dict[str, object] = {key: getattr(body, key) for key in body.model_fields_set}
    if changes.get("budget_month"):
        changes["budget_month"] = _month(str(changes["budget_month"]))
    request = await _requests(session, settings).edit(
        actor=actor,
        access=access,
        request_id=request_id,
        finance_request_id=finance_request_id,
        changes=changes,
    )
    return {"id": str(request.id), "version": request.version}


class RequestAction(BaseModel):
    action: str = Field(pattern="^(approve|reject|return|execute|pay|cancel|resubmit)$")
    note: str | None = Field(default=None, max_length=2000)
    step_id: int | None = None
    purchase_note: str | None = Field(default=None, max_length=5000)


@router.post("/requests/{finance_request_id}/actions")
async def act(
    finance_request_id: uuid.UUID,
    body: RequestAction,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    service = _requests(session, settings)
    await service.act(
        actor=actor,
        access=access,
        request_id=request_id,
        finance_request_id=finance_request_id,
        action=body.action,
        note=body.note,
        step_id=body.step_id,
        purchase_note=body.purchase_note,
    )
    view = await service.detail(actor=actor, access=access, finance_request_id=finance_request_id)
    return _detail(view, access)


@router.post("/requests/{finance_request_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    finance_request_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    kind: str = Query(max_length=30),
    name: str = Query(min_length=1, max_length=255),
    drive: DriveClient = Depends(get_drive_client),
) -> dict[str, Any]:
    document = await _requests(session, settings, drive).upload(
        actor=actor,
        access=access,
        request_id=request_id,
        finance_request_id=finance_request_id,
        kind=kind,
        name=name,
        data=await _body(request),
    )
    return {"id": str(document.id), "storage": document.storage}


class PaperBody(BaseModel):
    kind: str = Field(max_length=30)


@router.post("/requests/{finance_request_id}/papers", status_code=status.HTTP_201_CREATED)
async def paper(
    finance_request_id: uuid.UUID,
    body: PaperBody,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    document = await _requests(session, settings).paper(
        actor=actor,
        access=access,
        request_id=request_id,
        finance_request_id=finance_request_id,
        kind=body.kind,
    )
    return {"id": str(document.id)}


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await _requests(session, settings).remove_document(
        actor=actor, access=access, request_id=request_id, document_id=document_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}")
async def download_document(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    access: AccessDep,
    actor: CurrentActorDep,
    drive: DriveClient = Depends(get_drive_client),
) -> Response:
    document, data = await _requests(session, settings, drive).file(
        actor=actor, access=access, document_id=document_id
    )
    inline = (
        document.content_type.startswith("image/") or document.content_type == "application/pdf"
    )
    disposition = "inline" if inline else "attachment"
    safe = document.name.encode("ascii", "ignore").decode() or "chung-tu"
    return Response(
        content=data,
        media_type=document.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )


# --- ngân sách -----------------------------------------------------------------------------------


@router.get("/budgets")
async def budgets(
    session: SessionDep,
    access: AccessDep,
    month: str | None = Query(default=None),
    legal_entity_id: uuid.UUID | None = Query(default=None),
) -> dict[str, Any]:
    if not (access.budget or access.report or access.view_requests):
        raise FinanceForbiddenError("Bạn chưa có quyền xem ngân sách.")
    first = _month(month)
    lines = await FinanceBudgetService(session, AuditService(session)).month(
        first, legal_entity_id=legal_entity_id
    )
    entities = {row.id: row.code for row in await session.scalars(select(LegalEntity))}
    departments = {row.id: row for row in await session.scalars(select(Department))}
    codes = {row.code: row.name for row in await session.scalars(select(FinanceCode))}
    return {
        "month": first.strftime("%Y-%m"),
        "lines": [
            {
                "legal_entity_id": str(line.legal_entity_id),
                "legal_entity": entities.get(line.legal_entity_id, ""),
                "department_id": str(line.department_id),
                "department": departments[line.department_id].name
                if line.department_id in departments
                else "",
                "code": line.code,
                "code_name": codes.get(line.code, ""),
                "budget": int(whole(line.budget)),
                "committed": int(whole(line.committed)),
                "realized": int(whole(line.realized)),
                "remaining": int(whole(line.remaining)),
            }
            for line in lines
        ],
        "can_edit": access.budget,
    }


class BudgetCell(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    legal_entity_id: uuid.UUID
    department_id: uuid.UUID
    code: str = Field(max_length=30)
    amount: str | None = Field(default=None, max_length=20)


@router.put("/budgets/cell", status_code=status.HTTP_204_NO_CONTENT)
async def set_budget(
    body: BudgetCell,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> Response:
    await FinanceBudgetService(session, AuditService(session)).set_cell(
        actor=actor,
        access=access,
        request_id=request_id,
        month=_month(body.month),
        legal_entity_id=body.legal_entity_id,
        department_id=body.department_id,
        code=body.code,
        amount=body.amount,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/budgets/template.xlsx")
async def budget_template(
    session: SessionDep, access: AccessDep, month: str | None = Query(default=None)
) -> Response:
    access.require("budget", "Chỉ người có quyền ngân sách tải được file mẫu.")
    first = _month(month)
    data = await FinanceBudgetService(session, AuditService(session)).template(first)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="ngan-sach-{first:%Y-%m}.xlsx"'},
    )


@router.post("/budgets/import")
async def import_budgets(
    request: Request,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await FinanceBudgetService(session, AuditService(session)).import_file(
        actor=actor, access=access, request_id=request_id, data=await _body(request)
    )


# --- MISA + thu - chi ----------------------------------------------------------------------------


@router.get("/misa")
async def misa(
    session: SessionDep,
    access: AccessDep,
    legal_entity_id: uuid.UUID = Query(),
    month: str | None = Query(default=None),
) -> dict[str, Any]:
    if not (access.misa or access.report):
        raise FinanceForbiddenError("Bạn chưa có quyền đối soát MISA.")
    view = await FinanceMisaService(session, AuditService(session)).reconcile(
        legal_entity_id, _month(month)
    )
    return {
        "month": view.month.strftime("%Y-%m"),
        "imported": view.imported,
        "misa_revenue": int(whole(view.misa_revenue)),
        "apm_revenue": int(whole(view.apm_revenue)),
        "revenue_gap": int(whole(view.revenue_gap)),
        "misa_cost": int(whole(view.misa_cost)),
        "apm_cost": int(whole(view.apm_cost)),
        "cost_gap": int(whole(view.cost_gap)),
        "lines": [
            {
                "account": line.account,
                "name": line.name,
                "debit": int(whole(Decimal(line.debit))),
                "credit": int(whole(Decimal(line.credit))),
                "closing_debit": int(whole(Decimal(line.closing_debit))),
                "closing_credit": int(whole(Decimal(line.closing_credit))),
            }
            for line in view.lines
            if len(line.account) == 3
        ],
        "can_import": access.misa,
    }


@router.post("/misa/import")
async def import_misa(
    request: Request,
    session: SessionDep,
    access: AccessDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    legal_entity_id: uuid.UUID = Query(),
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
) -> dict[str, Any]:
    count = await FinanceMisaService(session, AuditService(session)).import_file(
        actor=actor,
        access=access,
        request_id=request_id,
        legal_entity_id=legal_entity_id,
        month=_month(month),
        data=await _body(request),
    )
    return {"lines": count}


@router.get("/pnl")
async def pnl(
    session: SessionDep, access: AccessDep, month: str | None = Query(default=None)
) -> dict[str, Any]:
    access.require("report", "Bạn chưa có quyền xem báo cáo thu - chi.")
    view = await FinancePnlService(session, AuditService(session)).month(
        _month(month), today=vn_today()
    )

    def line(item: Any) -> dict[str, Any]:
        return {
            "key": item.key,
            "label": item.label,
            "income": int(item.income),
            "realized": int(item.realized),
            "committed": int(item.committed),
            "budget": int(item.budget),
            "result": int(item.result),
        }

    return {
        "month": view.month.strftime("%Y-%m"),
        "entities": [line(item) for item in view.entities],
        "total": line(totals(view.entities)),
        "departments": [line(item) for item in view.departments],
    }


__all__ = ["router"]
