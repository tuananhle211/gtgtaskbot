"""Wire shapes for *Thành viên & Phân quyền*, phase 1.

Every label a screen prints - a role's name, a status's name, a capability's
name and domain - comes from the server on a ``*_label`` field, so the browser
holds no second vocabulary and a Telegram command and a web card call the same
state by the same word.

Three concepts, three shapes, kept apart on purpose:

* :class:`MemberResponse` is **membership** - a ``users`` row, its status and
  its base role;
* :class:`RoleResponse` is a **base role** - what the matrix gives everyone
  who holds it;
* :class:`EffectiveCapabilityResponse` is what one person **can actually do**,
  with its provenance: ``ROLE`` from the base role, ``SCOPED_GRANT`` from an
  approval grant in a scope, ``NONE`` otherwise. A grant never appears as a
  role and a role never appears as a grant.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meobot.api.schemas.pr import CapabilityGrantResponse
from meobot.application.pr_membership_service import (
    EffectiveCapability,
    EffectivePermissions,
    MemberCounts,
    MemberList,
    MemberRow,
    OpenResponsibilities,
    RoleSummary,
)
from meobot.application.user_service import status_label
from meobot.domain.identity.labels import role_label
from meobot.domain.pr.membership import (
    CAPABILITY_DOMAIN_LABELS,
    capability_domain,
    capability_label,
)
from meobot.domain.pr.policy import PrCapability


class MemberResponse(BaseModel):
    """One member. A ``users`` row, reduced to what the admin screen needs.

    ``telegram_user_id`` is present because it is the identity every Telegram
    command names a person by, and an administrator matching a card to a chat
    needs it; the username is a display convenience that Telegram lets people
    change. Neither ``status_reason`` nor the private-chat fields travel: the
    first is written for the owner and the audit trail, the second is about
    reachability, and this is a page many people can open.
    """

    user_id: uuid.UUID
    full_name: str
    telegram_user_id: int | None
    telegram_username: str | None
    telegram_linked: bool
    role: str
    role_label: str
    status: str
    status_label: str
    is_active: bool
    active_grant_count: int
    last_status_changed_at: datetime | None
    created_at: datetime | None

    @classmethod
    def from_row(cls, row: MemberRow) -> MemberResponse:
        user = row.user
        return cls(
            user_id=user.id,
            full_name=user.full_name,
            telegram_user_id=user.telegram_user_id,
            telegram_username=user.telegram_username,
            telegram_linked=user.telegram_user_id is not None,
            role=user.role.value,
            role_label=role_label(user.role),
            status=user.status.value,
            status_label=status_label(user.status),
            is_active=row.is_active,
            active_grant_count=row.active_grant_count,
            last_status_changed_at=user.last_status_changed_at,
            created_at=user.created_at,
        )


class MemberCountsResponse(BaseModel):
    total: int
    active: int
    suspended: int
    revoked: int
    pending: int

    @classmethod
    def from_domain(cls, counts: MemberCounts) -> MemberCountsResponse:
        return cls(
            total=counts.total,
            active=counts.active,
            suspended=counts.suspended,
            revoked=counts.revoked,
            pending=counts.pending,
        )


class MemberListResponse(BaseModel):
    """The whole roster plus what this actor may do to it.

    The three ``may_*`` flags are the same permission checks the write routes
    make, sent ahead so the screen draws only the controls that will work.
    They are hints for rendering; the server checks again on every write.
    """

    members: list[MemberResponse]
    counts: MemberCountsResponse
    may_add: bool
    may_change_status: bool
    may_change_role: bool
    assignable_roles: list[RoleOptionResponse]

    @classmethod
    def from_domain(
        cls, result: MemberList, assignable: list[RoleOptionResponse]
    ) -> MemberListResponse:
        return cls(
            members=[MemberResponse.from_row(row) for row in result.members],
            counts=MemberCountsResponse.from_domain(result.counts),
            may_add=result.may_add,
            may_change_status=result.may_change_status,
            may_change_role=result.may_change_role,
            assignable_roles=assignable,
        )


class RoleOptionResponse(BaseModel):
    role: str
    label: str


class AddMemberRequest(BaseModel):
    """Register somebody by Telegram id - the same command as ``/add_user``.

    A Telegram id is required because it is the only identity the workspace
    has: every account is reached, authenticated and notified through it. A
    name is optional and cosmetic; the person's Telegram profile fills it in
    the first time they message the bot if it is left blank.
    """

    telegram_user_id: int
    role: str
    full_name: str = Field(default="", max_length=200)
    telegram_username: str | None = Field(default=None, max_length=100)


class ChangeRoleRequest(BaseModel):
    role: str


class StatusChangeRequest(BaseModel):
    """An optional note for the audit trail; never shown to the member."""

    reason: str | None = Field(default=None, max_length=1000)


class CapabilityDescriptorResponse(BaseModel):
    """One capability's name and domain - the vocabulary of the roles tab."""

    capability: str
    label: str
    domain: str
    domain_label: str

    @classmethod
    def from_capability(cls, capability: PrCapability) -> CapabilityDescriptorResponse:
        domain = capability_domain(capability)
        return cls(
            capability=capability.value,
            label=capability_label(capability),
            domain=domain.value,
            domain_label=CAPABILITY_DOMAIN_LABELS[domain],
        )


class RoleResponse(BaseModel):
    """A base role: its name, who holds it, and what the matrix gives it.

    ``assignable`` is ``false`` for the owner. The owner is configuration
    (``MEOBOT_OWNER_TELEGRAM_ID``), not a role anybody is promoted into, so no
    picker on the web offers it - the same rule ``/change_user_role`` applies.
    """

    role: str
    label: str
    active_member_count: int
    assignable: bool
    capabilities: list[CapabilityDescriptorResponse]

    @classmethod
    def from_domain(cls, summary: RoleSummary) -> RoleResponse:
        return cls(
            role=summary.role.value,
            label=summary.label,
            active_member_count=summary.active_member_count,
            assignable=summary.assignable,
            capabilities=[
                CapabilityDescriptorResponse.from_capability(capability)
                for capability in summary.capabilities
            ],
        )


class RolesResponse(BaseModel):
    roles: list[RoleResponse]
    #: A note the tab prints verbatim, so the sentence about what a grant does
    #: and does not do is the server's, not the client's.
    note: str


class EffectiveCapabilityResponse(BaseModel):
    """One capability for one person, with where it comes from.

    ``source`` is ``ROLE``, ``SCOPED_GRANT`` or ``NONE``. For a grant, ``grants``
    lists every active grant carrying it - each with its own scope, so the
    screen can say *approves video content on two channels* rather than
    *approves*.
    """

    capability: str
    label: str
    domain: str
    domain_label: str
    allowed: bool
    source: str
    grants: list[CapabilityGrantResponse]

    @classmethod
    def from_domain(cls, row: EffectiveCapability) -> EffectiveCapabilityResponse:
        domain = capability_domain(row.capability)
        return cls(
            capability=row.capability.value,
            label=capability_label(row.capability),
            domain=domain.value,
            domain_label=CAPABILITY_DOMAIN_LABELS[domain],
            allowed=row.allowed,
            source=row.source,
            grants=[CapabilityGrantResponse.from_grant(grant) for grant in row.grants],
        )


class EffectivePermissionsResponse(BaseModel):
    """What one person may do today, and why.

    ``is_active`` is stated once at the top rather than folded into every row:
    a suspended member's role and grants are still facts - they are what comes
    back when the account is reactivated - but none of them is usable, and the
    screen says so in one banner instead of twenty greyed rows.
    """

    user_id: uuid.UUID
    full_name: str
    role: str
    role_label: str
    status: str
    status_label: str
    is_active: bool
    as_of: date
    capabilities: list[EffectiveCapabilityResponse]

    @classmethod
    def from_domain(cls, result: EffectivePermissions, as_of: date) -> EffectivePermissionsResponse:
        user = result.user
        return cls(
            user_id=user.id,
            full_name=user.full_name,
            role=user.role.value,
            role_label=role_label(user.role),
            status=user.status.value,
            status_label=status_label(user.status),
            is_active=result.is_active,
            as_of=as_of,
            capabilities=[
                EffectiveCapabilityResponse.from_domain(row) for row in result.capabilities
            ],
        )


class ResponsibilitiesResponse(BaseModel):
    """What a member still holds - shown before a deactivation, never changed by it."""

    user_id: uuid.UUID
    content_owned: int
    open_work: int
    open_tasks: int
    kpi_drafts: int
    kpi_awaiting_review: int
    active_grants: int
    total: int

    @classmethod
    def from_domain(
        cls, user_id: uuid.UUID, result: OpenResponsibilities
    ) -> ResponsibilitiesResponse:
        return cls(
            user_id=user_id,
            content_owned=result.content_owned,
            open_work=result.open_work,
            open_tasks=result.open_tasks,
            kpi_drafts=result.kpi_drafts,
            kpi_awaiting_review=result.kpi_awaiting_review,
            active_grants=result.active_grants,
            total=result.total,
        )


__all__: list[str] = [
    "AddMemberRequest",
    "CapabilityDescriptorResponse",
    "ChangeRoleRequest",
    "EffectiveCapabilityResponse",
    "EffectivePermissionsResponse",
    "MemberCountsResponse",
    "MemberListResponse",
    "MemberResponse",
    "ResponsibilitiesResponse",
    "RoleOptionResponse",
    "RoleResponse",
    "RolesResponse",
    "StatusChangeRequest",
]
