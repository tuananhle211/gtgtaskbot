"""Wire shapes for units: who the caller is in which unit, and the admin page.

Every label a screen prints comes from the server on a ``*_label`` field, as
for the PR membership screens, so the browser holds no second vocabulary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from meobot.application.units.directory import UnitMemberRow
from meobot.db.models.org_unit import UnitVideoKind
from meobot.domain.identity.labels import role_label
from meobot.domain.orders.permissions import ROLE_LABELS, SCOPE_LABELS, catalog, normalise_matrix
from meobot.domain.units.labels import unit_label, unit_role_label
from meobot.domain.units.models import UnitMemberRole, UnitMembership, UnitSettings


class PermissionCatalogEntry(BaseModel):
    key: str
    label: str
    own_meaning: str | None
    scopes: list[str]


class PermissionRoleEntry(BaseModel):
    key: str
    label: str


class UnitSettingsResponse(BaseModel):
    urgent_days: int
    media_nas_url: str | None
    design_nas_url: str | None
    btd_link_attacher: str
    telegram_enabled: bool
    review_bien_tap: bool
    review_thiet_ke: bool
    review_dung: bool
    review_video_by_script_lead: bool
    #: The Ads permission matrix, complete (defaults filled in):
    #: role -> permission -> NONE / OWN / ALL.
    permissions: dict[str, dict[str, str]]
    permission_catalog: list[PermissionCatalogEntry]
    permission_roles: list[PermissionRoleEntry]
    scope_labels: dict[str, str]

    @classmethod
    def from_domain(cls, settings: UnitSettings) -> UnitSettingsResponse:
        return cls(
            urgent_days=settings.urgent_days,
            media_nas_url=settings.media_nas_url,
            design_nas_url=settings.design_nas_url,
            btd_link_attacher=settings.btd_link_attacher.value,
            telegram_enabled=settings.telegram_enabled,
            review_bien_tap=settings.review_bien_tap,
            review_thiet_ke=settings.review_thiet_ke,
            review_dung=settings.review_dung,
            review_video_by_script_lead=settings.review_video_by_script_lead,
            permissions=normalise_matrix(settings.permissions),
            permission_catalog=[PermissionCatalogEntry(**entry) for entry in catalog()],
            permission_roles=[
                PermissionRoleEntry(key=role.value, label=label)
                for role, label in ROLE_LABELS.items()
            ],
            scope_labels={scope.value: label for scope, label in SCOPE_LABELS.items()},
        )


class UnitEntryResponse(BaseModel):
    """One unit the caller may see, and what they are in it."""

    code: str
    label: str
    role: str
    role_label: str
    is_lead: bool
    member_code: str | None
    personal_nas_url: str | None
    settings: UnitSettingsResponse


class UnitMeResponse(BaseModel):
    """What the shell needs to draw the unit switch and the nav."""

    units: list[UnitEntryResponse]
    default_unit: str | None
    #: The OWNER's "all units" view.
    can_view_all: bool
    #: Units this caller may administer (OWNER: every unit; ADMIN: the ones
    #: they are tagged in).
    can_admin: list[str]

    @classmethod
    def from_domain(
        cls,
        membership: UnitMembership,
        entries: list[UnitEntryResponse],
        *,
        can_admin: list[str],
    ) -> UnitMeResponse:
        return cls(
            units=entries,
            default_unit=entries[0].code if entries else None,
            can_view_all=membership.is_owner,
            can_admin=can_admin,
        )


class UnitMemberResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    base_role: str
    base_role_label: str
    role: str
    role_label: str
    is_lead: bool
    member_code: str | None
    personal_nas_url: str | None
    joined_at: datetime
    left_at: datetime | None
    active: bool

    @classmethod
    def from_row(cls, row: UnitMemberRow) -> UnitMemberResponse:
        membership, user = row.membership, row.user
        return cls(
            user_id=user.id,
            full_name=user.full_name,
            base_role=user.role.value,
            base_role_label=role_label(user.role),
            role=membership.role.value,
            role_label=unit_role_label(membership.role, membership.is_lead),
            is_lead=membership.is_lead,
            member_code=membership.member_code,
            personal_nas_url=membership.personal_nas_url,
            joined_at=membership.joined_at,
            left_at=membership.left_at,
            active=membership.left_at is None and user.active,
        )


class UnitMemberListResponse(BaseModel):
    unit: str
    unit_label: str
    members: list[UnitMemberResponse]
    #: What the admin page may offer in its role picker for this unit.
    assignable_roles: list[RoleOptionResponse]


class RoleOptionResponse(BaseModel):
    """One position the picker offers: a role, and for a function role whether
    it is the function's head (``Trưởng phòng Biên kịch`` = BIEN_TAP + lead)."""

    role: str
    label: str
    is_lead: bool = False


class DirectoryUserResponse(BaseModel):
    """One account with its unit tags, for the admin page's "add member" picker."""

    user_id: uuid.UUID
    full_name: str
    base_role: str
    base_role_label: str
    active: bool
    units: list[str]


class TagMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: str
    is_lead: bool = False
    member_code: str | None = Field(default=None, max_length=20)
    personal_nas_url: str | None = Field(default=None, max_length=2000)


class UpdateMemberRequest(BaseModel):
    role: str | None = None
    is_lead: bool | None = None
    member_code: str | None = Field(default=None, max_length=20)
    personal_nas_url: str | None = Field(default=None, max_length=2000)


class UpdateUnitSettingsRequest(BaseModel):
    urgent_days: int | None = Field(default=None, ge=1, le=365)
    media_nas_url: str | None = Field(default=None, max_length=2000)
    design_nas_url: str | None = Field(default=None, max_length=2000)
    btd_link_attacher: str | None = None
    telegram_enabled: bool | None = None
    review_bien_tap: bool | None = None
    review_thiet_ke: bool | None = None
    review_dung: bool | None = None
    review_video_by_script_lead: bool | None = None
    permissions: dict[str, dict[str, str]] | None = None


class VideoKindResponse(BaseModel):
    """One entry of the unit's "Loại video" catalogue."""

    id: uuid.UUID
    name: str
    points: float
    active: bool
    sort_order: int

    @classmethod
    def from_row(cls, row: UnitVideoKind) -> VideoKindResponse:
        return cls(
            id=row.id,
            name=row.name,
            points=float(row.points),
            active=row.active,
            sort_order=row.sort_order,
        )


class VideoKindListResponse(BaseModel):
    kinds: list[VideoKindResponse]


class CreateVideoKindRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    points: Decimal = Decimal("1")
    active: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class UpdateVideoKindRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    points: Decimal | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class UnitHealthWarning(BaseModel):
    code: str
    message: str


class UnitHealthResponse(BaseModel):
    unit: str
    warnings: list[UnitHealthWarning]


def unit_entry(
    code: str,
    role: UnitMemberRole,
    *,
    is_lead: bool,
    member_code: str | None,
    personal_nas_url: str | None,
    settings: UnitSettings,
) -> UnitEntryResponse:
    from meobot.domain.units.models import UnitCode

    return UnitEntryResponse(
        code=code,
        label=unit_label(UnitCode(code)),
        role=role.value,
        role_label=unit_role_label(role, is_lead),
        is_lead=is_lead,
        member_code=member_code,
        personal_nas_url=personal_nas_url,
        settings=UnitSettingsResponse.from_domain(settings),
    )


__all__ = [
    "CreateVideoKindRequest",
    "DirectoryUserResponse",
    "RoleOptionResponse",
    "TagMemberRequest",
    "UnitEntryResponse",
    "UnitHealthResponse",
    "UnitHealthWarning",
    "UnitMeResponse",
    "UnitMemberListResponse",
    "UnitMemberResponse",
    "UnitSettingsResponse",
    "UpdateMemberRequest",
    "UpdateUnitSettingsRequest",
    "UpdateVideoKindRequest",
    "VideoKindListResponse",
    "VideoKindResponse",
    "unit_entry",
]
