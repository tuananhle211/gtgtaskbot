"""Wire shapes of the personal permission layer (``/api/permissions``, 0066)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meobot.api.schemas.pr import CapabilityGrantResponse
from meobot.application.org.access import EffectiveGrant
from meobot.application.org.permissions import EffectiveRow, ModuleOverview, PersonOverview
from meobot.db.models.user_permission import UserPermission
from meobot.domain.identity.labels import role_label
from meobot.domain.org.models import POSITION_LABELS, Position
from meobot.domain.org.modules import MODULES
from meobot.domain.resources.models import EMPLOYMENT_STATUS_LABELS

SCOPE_LABELS = {
    "NONE": "Không",
    "OWN": "Của mình / phòng mình",
    "ALL": "Toàn bộ",
    "SCOPED": "Theo phạm vi đã cấp",
}


class SourceResponse(BaseModel):
    """Where one held permission comes from."""

    source: str
    scope: str
    department_id: uuid.UUID | None = None
    department_code: str | None = None
    department_name: str | None = None
    position: str | None = None
    position_label: str | None = None
    grant_id: uuid.UUID | None = None
    granted_by_user_id: uuid.UUID | None = None
    effective_to: date | None = None
    note: str | None = None

    @classmethod
    def from_grant(cls, grant: EffectiveGrant) -> SourceResponse:
        seat = grant.seat
        return cls(
            source=grant.source,
            scope=grant.scope,
            department_id=None if seat is None else seat.department_id,
            department_code=None if seat is None else seat.code,
            department_name=None if seat is None else seat.name,
            position=None if seat is None else seat.position.value,
            position_label=None if seat is None else POSITION_LABELS[seat.position],
            grant_id=grant.grant_id,
            granted_by_user_id=grant.granted_by_user_id,
            effective_to=grant.effective_to,
            note=grant.note,
        )


class EffectiveRowResponse(BaseModel):
    permission: str
    label: str
    scope: str
    scope_label: str
    sources: list[SourceResponse]
    gate_grants: list[CapabilityGrantResponse] = []
    personal_only: bool = False

    @classmethod
    def from_row(cls, row: EffectiveRow) -> EffectiveRowResponse:
        return cls(
            permission=row.permission,
            label=row.label,
            scope=row.scope,
            scope_label=SCOPE_LABELS.get(row.scope, row.scope),
            sources=[SourceResponse.from_grant(grant) for grant in row.sources],
            gate_grants=[CapabilityGrantResponse.from_grant(grant) for grant in row.gate_grants],
            personal_only=row.personal_only,
        )


class ModuleOverviewResponse(BaseModel):
    module: str
    label: str
    rows: list[EffectiveRowResponse]
    can_manage: bool
    grantable: dict[str, list[str]]

    @classmethod
    def from_domain(cls, module: ModuleOverview) -> ModuleOverviewResponse:
        return cls(
            module=module.module,
            label=module.label,
            rows=[EffectiveRowResponse.from_row(row) for row in module.rows],
            can_manage=module.can_manage,
            grantable=module.grantable,
        )


class PersonalGrantResponse(BaseModel):
    id: uuid.UUID
    module: str
    module_label: str
    permission: str
    permission_label: str
    scope: str
    scope_label: str
    note: str | None = None
    granted_by_user_id: uuid.UUID | None = None
    effective_to: date | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row: UserPermission) -> PersonalGrantResponse:
        spec = MODULES.get(row.module)
        return cls(
            id=row.id,
            module=row.module,
            module_label=spec.label if spec is not None else row.module,
            permission=row.permission,
            permission_label=spec.label_of(row.permission) if spec is not None else row.permission,
            scope=row.scope,
            scope_label=SCOPE_LABELS.get(row.scope, row.scope),
            note=row.note,
            granted_by_user_id=row.granted_by_user_id,
            effective_to=row.effective_to,
            created_at=row.created_at,
        )


class SeatResponse(BaseModel):
    department_id: uuid.UUID
    code: str
    name: str
    position: str
    position_label: str


class PersonOverviewResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    role: str
    role_label: str
    employment_status: str
    employment_status_label: str
    #: OWNER / ADMIN: holds everything, everywhere.
    sees_all: bool
    seats: list[SeatResponse]
    modules: list[ModuleOverviewResponse]
    personal: list[PersonalGrantResponse]
    scope_labels: dict[str, str] = SCOPE_LABELS

    @classmethod
    def from_domain(cls, overview: PersonOverview) -> PersonOverviewResponse:
        user = overview.user
        return cls(
            user_id=user.id,
            full_name=user.full_name,
            role=user.role.value,
            role_label=role_label(user.role),
            employment_status=user.employment_status.value,
            employment_status_label=EMPLOYMENT_STATUS_LABELS[user.employment_status],
            sees_all=overview.access.sees_all,
            seats=[
                SeatResponse(
                    department_id=department.id,
                    code=department.code,
                    name=department.name,
                    position=position,
                    position_label=POSITION_LABELS[Position(position)],
                )
                for department, position in overview.departments
            ],
            modules=[ModuleOverviewResponse.from_domain(module) for module in overview.modules],
            personal=[PersonalGrantResponse.from_row(row) for row in overview.personal],
        )


class ChecklistRequest(BaseModel):
    """The checklist of one module: ``permission -> scope``, ``null`` = not ticked."""

    module: str = Field(max_length=20)
    permissions: dict[str, str | None] = Field(max_length=100)


class GrantRequest(BaseModel):
    module: str = Field(max_length=20)
    permission: str = Field(max_length=40)
    scope: str = Field(pattern=r"^(OWN|ALL)$")
    note: str | None = Field(default=None, max_length=1000)
    effective_to: date | None = None


__all__ = [
    "ChecklistRequest",
    "GrantRequest",
    "PersonOverviewResponse",
    "PersonalGrantResponse",
]
