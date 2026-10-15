"""Wire shapes for the departments screen (0061)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from meobot.application.org.departments import SeatRow
from meobot.db.models.department import Department
from meobot.domain.org.models import (
    FUNCTION_LABELS,
    MODULE_ORDER,
    POSITION_LABELS,
    DepartmentFunction,
    Position,
)
from meobot.domain.org.modules import MODULES, ModuleSpec


class DepartmentResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    function: str
    function_label: str
    active: bool
    sort_order: int
    member_count: int = 0

    @classmethod
    def from_row(cls, row: Department, member_count: int = 0) -> DepartmentResponse:
        return cls(
            id=row.id,
            code=row.code,
            name=row.name,
            function=row.function.value,
            function_label=FUNCTION_LABELS[row.function],
            active=row.active,
            sort_order=row.sort_order,
            member_count=member_count,
        )


class DepartmentListResponse(BaseModel):
    departments: list[DepartmentResponse]


class CreateDepartmentRequest(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    function: DepartmentFunction = DepartmentFunction.NONE
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class UpdateDepartmentRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    function: DepartmentFunction | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class SeatResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    active: bool
    position: str
    position_label: str
    manager_user_id: uuid.UUID | None = None

    @classmethod
    def from_row(cls, row: SeatRow) -> SeatResponse:
        return cls(
            user_id=row.user.id,
            full_name=row.user.full_name,
            active=bool(row.user.active),
            position=row.seat.position.value,
            position_label=POSITION_LABELS[row.seat.position],
            manager_user_id=row.seat.manager_user_id,
        )


class SeatListResponse(BaseModel):
    members: list[SeatResponse]


class SeatRequest(BaseModel):
    """Add somebody, or change their seat. ``manager_user_id``: a head of this
    department; ``null`` clears it (send it explicitly)."""

    user_id: uuid.UUID
    position: Position | None = None
    manager_user_id: uuid.UUID | None = None


class GrantsResponse(BaseModel):
    """``position -> permission -> scope`` for one module."""

    module: str = MODULE_ORDER
    cells: dict[str, dict[str, str]]


class GrantsRequest(BaseModel):
    #: The module the cells belong to (0065): ``ORDER`` unless said.
    module: str = Field(default=MODULE_ORDER, max_length=20)
    cells: dict[str, dict[str, str]]


class PermissionEntry(BaseModel):
    key: str
    label: str
    scopes: list[str]
    own_meaning: str | None = None


class ModuleEntry(BaseModel):
    """One module a department grants permissions in."""

    module: str
    module_label: str
    permissions: list[PermissionEntry]

    @classmethod
    def build(cls, spec: ModuleSpec) -> ModuleEntry:
        return cls(
            module=spec.module,
            module_label=spec.label,
            permissions=[
                PermissionEntry(
                    key=permission.value,
                    label=spec.labels[permission],
                    scopes=[scope.value for scope in spec.scopes(permission)],
                    own_meaning=spec.own_meaning.get(permission),
                )
                for permission in spec.permissions
            ],
        )


class CatalogResponse(BaseModel):
    """What a department may grant, and the words for it. ``module`` /
    ``module_label`` / ``permissions`` are the order flow's (the first
    module, as before 0065); ``modules`` lists every module."""

    module: str = MODULE_ORDER
    module_label: str = "Luồng order"
    permissions: list[PermissionEntry]
    modules: list[ModuleEntry] = []
    positions: list[dict[str, str]]
    functions: list[dict[str, str]]
    scope_labels: dict[str, str]

    @classmethod
    def build(cls) -> CatalogResponse:
        modules = [ModuleEntry.build(spec) for spec in MODULES.values()]
        order = next(entry for entry in modules if entry.module == MODULE_ORDER)
        return cls(
            module=order.module,
            module_label=order.module_label,
            permissions=order.permissions,
            modules=modules,
            positions=[
                {"value": position.value, "label": POSITION_LABELS[position]}
                for position in Position
            ],
            functions=[
                {"value": function.value, "label": FUNCTION_LABELS[function]}
                for function in DepartmentFunction
            ],
            scope_labels={"NONE": "Không", "OWN": "Của mình / nhóm mình", "ALL": "Toàn bộ"},
        )


__all__ = [
    "CatalogResponse",
    "CreateDepartmentRequest",
    "DepartmentListResponse",
    "DepartmentResponse",
    "GrantsRequest",
    "GrantsResponse",
    "ModuleEntry",
    "SeatListResponse",
    "SeatRequest",
    "SeatResponse",
    "UpdateDepartmentRequest",
]
