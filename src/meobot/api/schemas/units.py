"""Wire shapes for units: who the caller is in which unit, and the admin page.

Every label a screen prints comes from the server on a ``*_label`` field, as
for the PR membership screens, so the browser holds no second vocabulary.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from meobot.api.schemas.resources import ResourceAccessResponse
from meobot.application.orders.effort_service import BanStats, EffortGrid
from meobot.application.units.directory import UnitMemberRow
from meobot.db.models.org_unit import UnitDesignCode, UnitDuration, UnitPlatform, UnitVideoKind
from meobot.domain.identity.labels import role_label
from meobot.domain.orders.permissions import ROLE_LABELS, SCOPE_LABELS, catalog, normalise_matrix
from meobot.domain.org.models import POSITION_LABELS, Seat
from meobot.domain.units.labels import (
    FUNCTION_TAGS,
    function_tag,
    unit_label,
    unit_role_label,
    unit_short_label,
)
from meobot.domain.units.models import (
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitSettings,
    stream_rank,
)


class PermissionCatalogEntry(BaseModel):
    key: str
    label: str
    own_meaning: str | None
    scopes: list[str]


class PermissionRoleEntry(BaseModel):
    key: str
    label: str


class PerfWeightsBody(BaseModel):
    output: float = Field(default=0.5, ge=0, le=100)
    on_time: float = Field(default=0.3, ge=0, le=100)
    quality: float = Field(default=0.2, ge=0, le=100)


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
    #: Effort (token) mode; only the OWNER switches it.
    effort_enabled: bool = True
    #: 0058: design codes ("mã video") are asked for on hand-ins to / by Design.
    design_codes_enabled: bool = True
    #: The AI reads the order's script when it is placed; the rules it uses.
    script_ai_review: bool = True
    script_review_rules: str = ""
    #: Hashtags + standard footer added to the AI's corrected script.
    script_footer: str = ""
    #: 0053: token budget and the performance score's knobs.
    default_daily_tokens: float = 80
    work_weekdays: list[int] = [0, 1, 2, 3, 4]
    #: Half days (half the budget): Saturday morning by default.
    half_weekdays: list[int] = [5]
    perf_weights: PerfWeightsBody = Field(default_factory=lambda: PerfWeightsBody())
    output_target: int | None = None

    @classmethod
    def from_domain(
        cls, settings: UnitSettings, matrix: dict[str, dict[str, str]] | None = None
    ) -> UnitSettingsResponse:
        """``matrix``: the order flow's permission matrix as read from the
        departments' grants (0061); without it, the stored one."""
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
            permissions=matrix if matrix is not None else normalise_matrix(settings.permissions),
            effort_enabled=settings.effort_enabled,
            design_codes_enabled=settings.design_codes_enabled,
            script_ai_review=settings.script_ai_review,
            script_review_rules=settings.script_review_rules,
            script_footer=settings.script_footer,
            default_daily_tokens=settings.default_daily_tokens,
            work_weekdays=list(settings.work_weekdays),
            half_weekdays=list(settings.half_weekdays),
            perf_weights=PerfWeightsBody(**settings.perf_weights.model_dump()),
            output_target=settings.output_target,
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
    #: The chip tag: "PR" / "ORD".
    short_label: str
    role: str
    role_label: str
    is_lead: bool
    #: ORD function roles only: "BT" / "TK" / "D"; null otherwise.
    function_tag: str | None = None
    member_code: str | None
    personal_nas_url: str | None
    settings: UnitSettingsResponse


class LibraryLinkResponse(BaseModel):
    """One entry of the Design team's library: a named link with a short icon
    (an emoji or a letter or two; null = the UI draws the name's initials)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    url: str
    icon: str | None = None


class LibraryResponse(BaseModel):
    links: list[LibraryLinkResponse]
    can_manage: bool = False


class LibraryLinkBody(BaseModel):
    #: Kept for an entry that was already there; absent for a new one.
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(max_length=200)
    url: str = Field(max_length=2100)
    icon: str | None = Field(default=None, max_length=40)


class UpdateLibraryRequest(BaseModel):
    """The whole library, in menu order."""

    links: list[LibraryLinkBody] = Field(max_length=100)


class DepartmentSeatResponse(BaseModel):
    """One department the caller sits in (0061)."""

    department_id: uuid.UUID
    code: str
    name: str
    function: str
    position: str
    position_label: str
    manager_user_id: uuid.UUID | None = None

    @classmethod
    def from_domain(cls, seat: Seat) -> DepartmentSeatResponse:
        return cls(
            department_id=seat.department_id,
            code=seat.code,
            name=seat.name,
            function=seat.function.value,
            position=seat.position.value,
            position_label=POSITION_LABELS[seat.position],
            manager_user_id=seat.manager_user_id,
        )


class DesignAccessResponse(BaseModel):
    """What the Design team's screens are for this caller (0058)."""

    #: In the Design team (and not OWNER/ADMIN): the task table shows the
    #: Design team's own columns.
    view: bool = False
    #: Keeps the catalogue of design codes: the Design Leader, OWNER, ADMIN.
    manage_codes: bool = False
    #: May open the birthday board and place birthday orders: the Design
    #: team, OWNER / ADMIN, and a member granted it on their ORD tag (0059).
    birthday: bool = False
    #: The Design team's library ("Thư viện") for the sidebar: empty for
    #: anybody outside the Design team. ``manage_library``: may edit it.
    library: list[LibraryLinkResponse] = []
    manage_library: bool = False


class UnitMeResponse(BaseModel):
    """What the shell needs to draw the unit switch and the nav."""

    units: list[UnitEntryResponse]
    default_unit: str | None
    #: The "all units" view: the OWNER and the ADMIN.
    can_view_all: bool
    #: Units this caller may administer (the OWNER and the ADMIN: every unit).
    can_admin: list[str]
    #: Streams this caller may place people in - role, Leader link, tag and
    #: untag: the OWNER alone since departments (0061).
    can_tag: list[str] = []
    #: Streams where this caller may edit the rest of a member's row (member
    #: code, NAS link, daily tokens, birthday-board grant): OWNER/ADMIN every
    #: stream, a team lead the streams they are tagged in.
    can_edit_members: list[str] = []
    #: 0061: the departments this caller sits in.
    departments: list[DepartmentSeatResponse] = []
    #: No open tag at all. ``units`` is then empty unless OWNER/ADMIN.
    is_untagged: bool = False
    #: ORD's effort (token) mode, for every screen that shows tokens. False
    #: only when the OWNER switched it off.
    effort_enabled: bool = True
    design: DesignAccessResponse = DesignAccessResponse()
    #: 0065: what this caller may do in the resources module (the nav's
    #: "Tài nguyên" branch follows it).
    resources: ResourceAccessResponse = ResourceAccessResponse()

    @classmethod
    def from_domain(
        cls,
        membership: UnitMembership,
        entries: list[UnitEntryResponse],
        *,
        can_admin: list[str],
        can_tag: list[str] | None = None,
        can_edit_members: list[str] | None = None,
        design: DesignAccessResponse | None = None,
        seats: list[DepartmentSeatResponse] | None = None,
        resources: ResourceAccessResponse | None = None,
    ) -> UnitMeResponse:
        entries = sorted(entries, key=lambda entry: stream_rank(entry.code))
        return cls(
            units=entries,
            default_unit=entries[0].code if entries else None,
            can_view_all=membership.sees_all,
            can_admin=sorted(can_admin, key=stream_rank),
            can_tag=sorted(can_tag or [], key=stream_rank),
            can_edit_members=sorted(can_edit_members or [], key=stream_rank),
            departments=seats or [],
            is_untagged=membership.is_untagged,
            effort_enabled=next(
                (
                    entry.settings.effort_enabled
                    for entry in entries
                    if entry.code == UnitCode.ADS.value and entry.settings is not None
                ),
                True,
            ),
            design=design or DesignAccessResponse(),
            resources=resources or ResourceAccessResponse(),
        )


class UnitMemberResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    base_role: str
    base_role_label: str
    role: str
    role_label: str
    is_lead: bool
    #: ORD function roles only: "BT" / "TK" / "D"; null otherwise.
    function_tag: str | None = None
    member_code: str | None
    personal_nas_url: str | None
    joined_at: datetime
    left_at: datetime | None
    active: bool
    #: Additive: the account itself is active (``users.active``), whatever the tag.
    account_active: bool = True
    #: Additive: the account's picture, null without one.
    avatar_url: str | None = None
    #: Additive (0050): the one Leader / head this member reports to.
    manager_user_id: uuid.UUID | None = None
    #: Additive (0053): their own daily token budget; null = the unit default.
    daily_tokens: float | None = None

    @classmethod
    def from_row(cls, row: UnitMemberRow, avatar_url: str | None = None) -> UnitMemberResponse:
        membership, user = row.membership, row.user
        return cls(
            avatar_url=avatar_url,
            user_id=user.id,
            full_name=user.full_name,
            base_role=user.role.value,
            base_role_label=role_label(user.role),
            role=membership.role.value,
            role_label=unit_role_label(membership.role, membership.is_lead),
            is_lead=membership.is_lead,
            function_tag=FUNCTION_TAGS.get(membership.role),
            member_code=membership.member_code,
            personal_nas_url=membership.personal_nas_url,
            joined_at=membership.joined_at,
            left_at=membership.left_at,
            active=membership.left_at is None and user.active,
            account_active=bool(user.active),
            manager_user_id=membership.manager_user_id,
            daily_tokens=(
                None if membership.daily_tokens is None else float(membership.daily_tokens)
            ),
        )


class UnitMemberListResponse(BaseModel):
    unit: str
    unit_label: str
    #: The chip tag: "PR" / "ORD".
    unit_short_label: str
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


class UntaggedUserResponse(BaseModel):
    """An active account with no open tag, waiting for a stream."""

    user_id: uuid.UUID
    full_name: str
    telegram_username: str | None
    role: str
    role_label: str
    created_at: datetime
    avatar_url: str | None = None


class UntaggedUsersResponse(BaseModel):
    users: list[UntaggedUserResponse]


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
    #: ORD: the member's own Leader (an orderer: their head); null clears it.
    manager_user_id: uuid.UUID | None = None
    #: ORD (0053): their own daily token budget; null = the unit default.
    daily_tokens: Decimal | None = Field(default=None, ge=0, le=Decimal("999.99"))


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
    #: OWNER only (403 ``effort_setting_forbidden``).
    effort_enabled: bool | None = None
    design_codes_enabled: bool | None = None
    script_ai_review: bool | None = None
    script_review_rules: str | None = Field(default=None, max_length=8000)
    script_footer: str | None = Field(default=None, max_length=2000)
    default_daily_tokens: float | None = Field(default=None, ge=0, le=999)
    work_weekdays: list[int] | None = None
    half_weekdays: list[int] | None = None
    perf_weights: PerfWeightsBody | None = None
    #: null = the ban's top performer of the month.
    output_target: int | None = Field(default=None, ge=1, le=10000)


class DesignCodeResponse(BaseModel):
    """One row of the Design team's catalogue of design codes ("mã video")."""

    id: uuid.UUID
    code: str
    name: str
    active: bool
    sort_order: int

    @classmethod
    def from_row(cls, row: UnitDesignCode) -> DesignCodeResponse:
        return cls(
            id=row.id, code=row.code, name=row.name, active=row.active, sort_order=row.sort_order
        )


class DesignCodeListResponse(BaseModel):
    codes: list[DesignCodeResponse]


class CreateDesignCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=300)
    active: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class UpdateDesignCodeRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)


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


class PlatformResponse(BaseModel):
    id: uuid.UUID
    name: str
    active: bool
    sort_order: int

    @classmethod
    def from_row(cls, row: UnitPlatform) -> PlatformResponse:
        return cls(id=row.id, name=row.name, active=row.active, sort_order=row.sort_order)


class PlatformListResponse(BaseModel):
    platforms: list[PlatformResponse]


class CreatePlatformRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class UpdatePlatformRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class DurationResponse(BaseModel):
    id: uuid.UUID
    name: str
    points: float
    active: bool
    sort_order: int

    @classmethod
    def from_row(cls, row: UnitDuration) -> DurationResponse:
        return cls(
            id=row.id,
            name=row.name,
            points=float(row.points),
            active=row.active,
            sort_order=row.sort_order,
        )


class DurationListResponse(BaseModel):
    durations: list[DurationResponse]


class CreateDurationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    points: Decimal = Decimal("1")
    active: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=100000)


class UpdateDurationRequest(BaseModel):
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
    matrix: dict[str, dict[str, str]] | None = None,
) -> UnitEntryResponse:
    from meobot.domain.units.models import UnitCode

    return UnitEntryResponse(
        code=code,
        label=unit_label(UnitCode(code)),
        short_label=unit_short_label(UnitCode(code)),
        role=role.value,
        role_label=unit_role_label(role, is_lead),
        is_lead=is_lead,
        function_tag=function_tag(UnitCode(code), role),
        member_code=member_code,
        personal_nas_url=personal_nas_url,
        settings=UnitSettingsResponse.from_domain(settings, matrix),
    )


class EffortDayResponse(BaseModel):
    date: date_type
    budget: float
    used: float
    left: float


class EffortPersonResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    role_label: str
    function_tag: str | None
    is_lead: bool
    daily_tokens: float
    open_tokens: float
    open_tasks: int
    days: list[EffortDayResponse]


class EffortGridResponse(BaseModel):
    """``GET /api/units/ADS/effort``: people by days (0053)."""

    date_from: date_type
    date_to: date_type
    today: date_type
    days: list[date_type]
    people: list[EffortPersonResponse]

    @classmethod
    def from_domain(cls, grid: EffortGrid) -> EffortGridResponse:
        return cls(
            date_from=grid.date_from,
            date_to=grid.date_to,
            today=grid.today,
            days=list(grid.days),
            people=[
                EffortPersonResponse(
                    user_id=person.user_id,
                    full_name=person.full_name,
                    role_label=person.role_label,
                    function_tag=person.function_tag,
                    is_lead=person.is_lead,
                    daily_tokens=person.daily_tokens,
                    open_tokens=person.open_tokens,
                    open_tasks=person.open_tasks,
                    days=[
                        EffortDayResponse(
                            date=day.day, budget=day.budget, used=day.used, left=day.left
                        )
                        for day in person.days
                    ],
                )
                for person in grid.people
            ],
        )


class BanMemberResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    is_lead: bool
    budget: float
    used: float
    left: float
    today_left: float
    open_tokens: float
    open_tasks: int
    done: int


class BanStatResponse(BaseModel):
    role: str
    label: str
    budget: float
    used: float
    left: float
    open_tokens: float
    open_tasks: int
    done: int
    in_progress: int
    overdue: int
    late: int
    members: list[BanMemberResponse]


class BanStatsResponse(BaseModel):
    """``GET /api/units/ADS/ban-stats``: the dashboard's "Theo ban" (tokens
    and work per ban over the range)."""

    date_from: date_type
    date_to: date_type
    today: date_type
    my_ban: str | None
    bans: list[BanStatResponse]
    effort_enabled: bool = True

    @classmethod
    def from_domain(cls, stats: BanStats) -> BanStatsResponse:
        return cls(
            date_from=stats.date_from,
            date_to=stats.date_to,
            today=stats.today,
            my_ban=None if stats.my_ban is None else stats.my_ban.value,
            effort_enabled=stats.effort_enabled,
            bans=[
                BanStatResponse(
                    role=ban.role.value,
                    label=ban.label,
                    budget=ban.budget,
                    used=ban.used,
                    left=ban.left,
                    open_tokens=ban.open_tokens,
                    open_tasks=ban.open_tasks,
                    done=ban.done,
                    in_progress=ban.in_progress,
                    overdue=ban.overdue,
                    late=ban.late,
                    members=[
                        BanMemberResponse(
                            user_id=member.user_id,
                            full_name=member.full_name,
                            is_lead=member.is_lead,
                            budget=member.budget,
                            used=member.used,
                            left=member.left,
                            today_left=member.today_left,
                            open_tokens=member.open_tokens,
                            open_tasks=member.open_tasks,
                            done=member.done,
                        )
                        for member in ban.members
                    ],
                )
                for ban in stats.bans
            ],
        )


__all__ = [
    "BanStatsResponse",
    "CreateDurationRequest",
    "CreatePlatformRequest",
    "CreateVideoKindRequest",
    "DirectoryUserResponse",
    "DurationListResponse",
    "DurationResponse",
    "EffortGridResponse",
    "PlatformListResponse",
    "PlatformResponse",
    "RoleOptionResponse",
    "TagMemberRequest",
    "UnitEntryResponse",
    "UnitHealthResponse",
    "UnitHealthWarning",
    "UnitMeResponse",
    "UnitMemberListResponse",
    "UnitMemberResponse",
    "UnitSettingsResponse",
    "UntaggedUserResponse",
    "UntaggedUsersResponse",
    "UpdateDurationRequest",
    "UpdateMemberRequest",
    "UpdatePlatformRequest",
    "UpdateUnitSettingsRequest",
    "UpdateVideoKindRequest",
    "VideoKindListResponse",
    "VideoKindResponse",
    "unit_entry",
]
