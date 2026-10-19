"""*Thành viên & Phân quyền*, phase 1: the read models behind the web admin.

What this module is **not**: a second implementation of adding, suspending,
reactivating or re-roling a member. Those are
:class:`~meobot.application.user_service.UserService`, the one service the
Telegram commands (``/add_user``, ``/suspend_user``, ``/enable_user``,
``/revoke_user``, ``/change_user_role``), the access-request buttons and now
the web routes all call. The web routes construct it exactly as the bot does
and pass their actor through; nothing about who may do what is decided here.

What it is: the three questions a screen asks that no existing service
answered in one place.

* **Who is in the PR workspace, and in what state?** There is no membership
  table and no team - a ``users`` row *is* the membership, its ``status`` is
  whether the membership is live, and its ``role`` is the base role. The
  list is that table. Whether a row is linked to a chat identity is stated
  by the response layer, not here: a PR service never reads a chat handle,
  and "is a member" and "has pressed Start" are different things anyway.
* **What may this person do, and why?** Base capabilities come from the role
  through the permission matrix; approval rights come from scoped grants.
  :meth:`effective_permissions` returns each with its provenance rather than
  a flat list, so an administrator can see *that* a permission exists and
  *where it comes from* - and that a grant did not change a role.
* **What is this person still holding?** Before an account is suspended, the
  content they own, the work they are on, the tasks assigned to them, the
  KPI proposal awaiting review and the grants they hold. Counted, shown, and
  never reassigned or deleted by this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import SOURCE_DEPARTMENT, SOURCE_PERSONAL, Access
from meobot.application.org.departments import function_department
from meobot.application.org.seats import load_grants
from meobot.application.pr_capability_service import PrCapabilityGrant, PrCapabilityService
from meobot.core.errors import AuthorizationError, NotFoundError
from meobot.core.time import utcnow
from meobot.db.models.pr import PrContentItem, PrTask, PrTaskAssignment
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem
from meobot.db.models.pr_work_quota import PrWorkPlan
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.invites import can_invite_role
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.models import MODULE_PR, POSITION_LABELS, DepartmentFunction, Position
from meobot.domain.org.modules import CommonPermission
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.pr.membership import (
    ASSIGNABLE_ROLES,
    MEMBER_READ_PERMISSION,
    PrCapabilityDomain,
    capability_domain,
    capability_label,
    role_capabilities,
)
from meobot.domain.pr.policy import GRANT_BACKED, PrCapability
from meobot.domain.pr.work import TERMINAL_WORK_STATUSES
from meobot.domain.pr.work_quota import PrWorkPlanStatus
from meobot.domain.pr.workflow import TERMINAL_STAGES, TERMINAL_TASK_STATUSES

#: The department's own roles: what the PR head may hand out or act upon.
MODULE_ROLES = (Role.EMPLOYEE, Role.TEAM_LEAD)


@dataclass(frozen=True, slots=True)
class MemberRow:
    """One person in the list. The row, plus nothing that costs a query per row."""

    user: User
    #: Active approval grants held right now. One grouped query for the list.
    active_grant_count: int = 0
    #: 0067: the viewer may grant / revoke this person's PR rights.
    can_manage_permissions: bool = False
    #: 2026-10-19: the viewer may change this person's role / status / PR
    #: membership - by system role, or as the PR head over a PR member.
    can_manage_account: bool = False

    @property
    def is_active(self) -> bool:
        return self.user.status is UserStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class MemberCounts:
    total: int = 0
    active: int = 0
    suspended: int = 0
    revoked: int = 0
    pending: int = 0


@dataclass(frozen=True, slots=True)
class MemberList:
    members: tuple[MemberRow, ...]
    counts: MemberCounts
    #: What **this** actor may do on the screen - the same checks the writes
    #: make, rendered as flags so the controls drawn are the ones that work.
    may_add: bool = False
    may_change_status: bool = False
    may_change_role: bool = False
    #: 0067: may bring an existing account into PR Thương Hiệu (the same
    #: right as adding one: ``user.manage`` or ``PR_MEMBERS_MANAGE``).
    may_tag_pr: bool = False


@dataclass(frozen=True, slots=True)
class RoleSummary:
    role: Role
    label: str
    active_member_count: int
    capabilities: tuple[PrCapability, ...]
    #: ``OWNER`` is configuration, not something the screen may assign.
    assignable: bool


@dataclass(frozen=True, slots=True)
class PositionSummary:
    """0066: a position in the PR department and what it grants."""

    position: Position
    label: str
    department_id: uuid.UUID
    capabilities: tuple[PrCapability, ...]
    #: Holds ``MANAGE_PERMISSIONS``: shapes the members' permissions.
    manages_members: bool


@dataclass(frozen=True, slots=True)
class EffectiveCapability:
    """One capability, with its provenance."""

    capability: PrCapability
    allowed: bool
    #: ``ROLE`` (OWNER / ADMIN) | ``DEPARTMENT`` | ``PERSONAL`` | ``SCOPED_GRANT`` | ``NONE``.
    source: str
    #: For a grant: the grants that carry it, each with its scope.
    grants: tuple[PrCapabilityGrant, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectivePermissions:
    user: User
    #: Whether any of the below is usable at all. False for a suspended or
    #: revoked account, whose grants and role are then facts about history.
    is_active: bool
    capabilities: tuple[EffectiveCapability, ...]


@dataclass(frozen=True, slots=True)
class OpenResponsibilities:
    """What a member still holds - the summary a deactivation dialog shows."""

    content_owned: int = 0
    open_work: int = 0
    open_tasks: int = 0
    kpi_drafts: int = 0
    kpi_awaiting_review: int = 0
    active_grants: int = 0

    @property
    def total(self) -> int:
        return (
            self.content_owned
            + self.open_work
            + self.open_tasks
            + self.kpi_drafts
            + self.active_grants
        )


@dataclass(frozen=True, slots=True)
class _Provenance:
    """Grouped reads shared by two views, fetched once."""

    grants: dict[uuid.UUID, list[PrCapabilityGrant]] = field(default_factory=dict)


class PrMembershipService:
    """Reads for the membership admin. ``user.read`` throughout, self excepted.

    Args:
        session: Unit of work.
        capabilities: The grant reader, so "what may this person approve" is the
            same enumeration authorization uses.
    """

    def __init__(self, session: AsyncSession, capabilities: PrCapabilityService) -> None:
        self._session = session
        self._capabilities = capabilities

    @property
    def session(self) -> AsyncSession:
        return self._session

    # --- who may read the roster (0067) --------------------------------------------
    async def may_read(self, actor: Actor) -> bool:
        """``user.read`` by role (OWNER / ADMIN), or ``PR_MEMBERS_VIEW`` through
        the unified layers (the head of PR Thương Hiệu by default)."""
        if has_permission(actor.role, MEMBER_READ_PERMISSION):
            return True
        return await self._capabilities.held(actor, PrCapability.PR_MEMBERS_VIEW)

    async def may_add(self, actor: Actor) -> bool:
        """``user.manage`` by role, or ``PR_MEMBERS_MANAGE`` through the layers."""
        if has_permission(actor.role, Permission.USER_MANAGE):
            return True
        return await self._capabilities.held(actor, PrCapability.PR_MEMBERS_MANAGE)

    @staticmethod
    def assignable_roles(actor: Actor) -> tuple[Role, ...]:
        """The roles this actor may hand out: everything assignable for whoever
        holds ``user.manage``; anybody else (the PR head with
        ``PR_MEMBERS_MANAGE``) the department's own two - Nhân viên and
        Trưởng phòng (2026-10-19) - never a system role."""
        roles = tuple(role for role in ASSIGNABLE_ROLES if can_invite_role(actor.role, role))
        if has_permission(actor.role, Permission.USER_MANAGE):
            return roles
        return tuple(role for role in MODULE_ROLES if role in ASSIGNABLE_ROLES)

    async def may_manage_accounts(self, actor: Actor) -> bool:
        """The screen's flags: the role's ``user.status.manage`` / ``user.role.manage``,
        or - for somebody below the system roles - ``PR_MEMBERS_MANAGE`` through
        the layers (the PR head). An ADMIN keeps the system half's rules."""
        if has_permission(actor.role, Permission.USER_STATUS_MANAGE):
            return True
        if actor.role in (Role.OWNER, Role.ADMIN):
            return False
        return await self._capabilities.held(actor, PrCapability.PR_MEMBERS_MANAGE)

    async def manages_account(self, actor: Actor, target: User) -> bool:
        """Whether ``PR_MEMBERS_MANAGE`` lets this actor act on this account
        (2026-10-19): the actor is below the system roles, the target a PR
        member in one of the department's roles, and not themselves. The
        system roles' own rights are checked by ``UserService`` itself."""
        if actor.role in (Role.OWNER, Role.ADMIN):
            return False
        if target.role not in MODULE_ROLES or target.id == actor.user_id:
            return False
        if not await self._capabilities.held(actor, PrCapability.PR_MEMBERS_MANAGE):
            return False
        return await self._in_pr(target.id)

    async def module_authorises(self, actor: Actor, user_id: uuid.UUID) -> bool:
        """For the write routes: whether to call ``UserService`` with
        ``authorized_by_module``. Somebody whose system role already allows
        the write needs no module authorisation; an unknown account is left to
        the service's own 404."""
        if has_permission(actor.role, Permission.USER_STATUS_MANAGE):
            return False
        target = await self._session.get(User, user_id)
        return target is not None and await self.manages_account(actor, target)

    async def _in_pr(self, user_id: uuid.UUID) -> bool:
        from meobot.db.models.org_unit import OrgUnit, OrgUnitMember

        return bool(
            await self._session.scalar(
                select(OrgUnitMember.id)
                .join(OrgUnit, OrgUnit.id == OrgUnitMember.unit_id)
                .where(
                    OrgUnit.code == "PR",
                    OrgUnitMember.user_id == user_id,
                    OrgUnitMember.left_at.is_(None),
                )
                .limit(1)
            )
        )

    async def _require_read(self, actor: Actor) -> None:
        if not await self.may_read(actor):
            raise AuthorizationError(
                "Bạn không có quyền xem danh sách thành viên.",
                details={"reason": "member_read_forbidden", "actor_role": actor.role.value},
            )

    async def _require_read_or_self(self, actor: Actor, user_id: uuid.UUID) -> None:
        if actor.user_id is not None and actor.user_id == user_id:
            return
        await self._require_read(actor)

    async def list_members(self, *, actor: Actor, on: date | None = None) -> MemberList:
        """Every registered person, by name, with status and grant count."""
        await self._require_read(actor)
        # A deleted account (0054) is listed nowhere.
        rows = (
            (
                await self._session.execute(
                    select(User).where(User.deleted_at.is_(None)).order_by(User.full_name)
                )
            )
            .scalars()
            .all()
        )
        today = on or utcnow().date()
        grant_counts = await self._active_grant_counts(on=today)
        by_role = has_permission(actor.role, Permission.USER_STATUS_MANAGE)
        listed: list[MemberRow] = []
        for row in rows:
            listed.append(
                MemberRow(
                    user=row,
                    active_grant_count=grant_counts.get(row.id, 0),
                    can_manage_permissions=await self._capabilities.manages(actor, row),
                    can_manage_account=(
                        (by_role and actor.role.outranks(row.role))
                        or await self.manages_account(actor, row)
                    ),
                )
            )
        members = tuple(listed)
        by_status = dict.fromkeys(UserStatus, 0)
        for row in rows:
            by_status[row.status] += 1
        return MemberList(
            members=members,
            counts=MemberCounts(
                total=len(rows),
                active=by_status[UserStatus.ACTIVE],
                suspended=by_status[UserStatus.SUSPENDED],
                revoked=by_status[UserStatus.REVOKED],
                pending=by_status[UserStatus.PENDING],
            ),
            may_add=await self.may_add(actor),
            may_tag_pr=await self.may_add(actor),
            may_change_status=await self.may_manage_accounts(actor),
            may_change_role=await self.may_manage_accounts(actor),
        )

    async def member(self, *, actor: Actor, user_id: uuid.UUID) -> MemberRow:
        await self._require_read_or_self(actor, user_id)
        user = await self._require_user(user_id)
        counts = await self._active_grant_counts(on=utcnow().date(), user_id=user.id)
        return MemberRow(
            user=user,
            active_grant_count=counts.get(user.id, 0),
            can_manage_account=(
                (
                    has_permission(actor.role, Permission.USER_STATUS_MANAGE)
                    and actor.role.outranks(user.role)
                )
                or await self.manages_account(actor, user)
            ),
            can_manage_permissions=await self._capabilities.manages(actor, user),
        )

    async def positions(self, *, actor: Actor) -> tuple[PositionSummary, ...]:
        """0066: what the PR department grants its heads and its members -
        the defaults a Trưởng nhóm / Nhân viên used to get from their role."""
        await self._require_read(actor)
        team = await function_department(self._session, DepartmentFunction.PR)
        if team is None:
            return ()
        table = await load_grants(self._session, MODULE_PR, [team.id])
        found: list[PositionSummary] = []
        for position in Position:
            cells = dict(table.get((team.id, position.value), ()))
            held = [
                capability for capability in PrCapability if cells.get(capability.value) == "ALL"
            ]
            found.append(
                PositionSummary(
                    position=position,
                    label=f"{POSITION_LABELS[position]} {team.name}",
                    department_id=team.id,
                    capabilities=tuple(sorted(held, key=_capability_order)),
                    manages_members=cells.get(CommonPermission.MANAGE_PERMISSIONS.value)
                    in ("OWN", "ALL"),
                )
            )
        return tuple(found)

    async def roles(self, *, actor: Actor) -> tuple[RoleSummary, ...]:
        """The four base roles, what each grants, and how many hold each.
        Since 0066 only the OWNER and an ADMIN hold PR capabilities by role;
        a Trưởng nhóm or a Nhân viên holds what their PR seat grants
        (:meth:`positions`) and what they were given personally."""
        await self._require_read(actor)
        rows = (
            await self._session.execute(
                select(User.role, func.count())
                .where(User.status == UserStatus.ACTIVE)
                .group_by(User.role)
            )
        ).all()
        counts = {role: int(count) for role, count in rows}
        return tuple(
            RoleSummary(
                role=role,
                label=role_label(role),
                active_member_count=counts.get(role, 0),
                capabilities=tuple(
                    sorted(role_capabilities(role), key=_capability_order)
                    if role in (Role.OWNER, Role.ADMIN)
                    else ()
                ),
                assignable=role in ASSIGNABLE_ROLES,
            )
            for role in (Role.OWNER, Role.ADMIN, Role.TEAM_LEAD, Role.EMPLOYEE)
        )

    async def effective_permissions(
        self, *, actor: Actor, user_id: uuid.UUID, on: date | None = None
    ) -> EffectivePermissions:
        """Every PR capability for one person, allowed or not, with its source.

        Role-backed capabilities are read off the permission matrix for the
        person's **current** role; grant-backed ones off the **active** grants
        on ``on`` - the same query the approval queue uses, so a grant that
        expired yesterday or was revoked an hour ago is already absent.
        Nothing is computed twice: a person with an approval grant and no role
        entitlement is shown the grant, and a person with neither is shown
        ``NONE``.
        """
        await self._require_read_or_self(actor, user_id)
        user = await self._require_user(user_id)
        today = on or utcnow().date()
        subject = Actor(user_id=user.id, full_name=user.full_name, role=user.role)
        grants = await self._capabilities.approval_grants_for(subject, on=today)
        by_capability: dict[PrCapability, list[PrCapabilityGrant]] = {}
        for grant in grants:
            by_capability.setdefault(grant.capability, []).append(grant)
        # 0066: the unified layers - system role, department seat, personal grant.
        access = await self._capabilities.access_of(subject)
        rows: list[EffectiveCapability] = []
        for capability in sorted(PrCapability, key=_capability_order):
            if capability in GRANT_BACKED:
                found = tuple(by_capability.get(capability, ()))
                if access.sees_all:
                    rows.append(
                        EffectiveCapability(
                            capability=capability, allowed=True, source="ROLE", grants=found
                        )
                    )
                    continue
                rows.append(
                    EffectiveCapability(
                        capability=capability,
                        allowed=bool(found),
                        source="SCOPED_GRANT" if found else "NONE",
                        grants=found,
                    )
                )
            else:
                rows.append(
                    EffectiveCapability(
                        capability=capability,
                        allowed=access.sees_all or access.allows(MODULE_PR, capability.value),
                        source=_source_of(access, capability),
                    )
                )
        return EffectivePermissions(
            user=user, is_active=user.status is UserStatus.ACTIVE, capabilities=tuple(rows)
        )

    async def responsibilities(self, *, actor: Actor, user_id: uuid.UUID) -> OpenResponsibilities:
        """What a person still holds, counted - shown before a deactivation.

        Content they own that is not at a terminal stage; work they contribute
        to whose item is still open; tasks assigned to them and unfinished; KPI
        drafts of theirs, and how many of those are awaiting review; the
        approval grants active today. Read-only: nothing here reassigns,
        revokes or closes anything, and the dialog says so.
        """
        await self._require_read(actor)
        await self._require_user(user_id)
        content_owned = await self._session.scalar(
            select(func.count())
            .select_from(PrContentItem)
            .where(
                PrContentItem.owner_user_id == user_id,
                PrContentItem.workflow_stage.not_in(sorted(TERMINAL_STAGES, key=str)),
            )
        )
        open_work = await self._session.scalar(
            select(func.count(func.distinct(PrWorkContribution.work_item_id)))
            .select_from(PrWorkContribution)
            .join(PrWorkItem, PrWorkItem.id == PrWorkContribution.work_item_id)
            .where(
                PrWorkContribution.user_id == user_id,
                PrWorkItem.status.not_in(sorted(TERMINAL_WORK_STATUSES, key=str)),
            )
        )
        open_tasks = await self._session.scalar(
            select(func.count(func.distinct(PrTaskAssignment.task_id)))
            .select_from(PrTaskAssignment)
            .join(PrTask, PrTask.id == PrTaskAssignment.task_id)
            .where(
                PrTaskAssignment.user_id == user_id,
                PrTaskAssignment.completed_at.is_(None),
                PrTask.status.not_in(sorted(TERMINAL_TASK_STATUSES, key=str)),
            )
        )
        drafts = (
            await self._session.execute(
                select(PrWorkPlan.submitted_at).where(
                    PrWorkPlan.user_id == user_id, PrWorkPlan.status == PrWorkPlanStatus.DRAFT
                )
            )
        ).all()
        grants = await self._active_grant_counts(on=utcnow().date(), user_id=user_id)
        return OpenResponsibilities(
            content_owned=int(content_owned or 0),
            open_work=int(open_work or 0),
            open_tasks=int(open_tasks or 0),
            kpi_drafts=len(drafts),
            kpi_awaiting_review=sum(1 for (submitted_at,) in drafts if submitted_at is not None),
            active_grants=grants.get(user_id, 0),
        )

    async def _active_grant_counts(
        self, *, on: date, user_id: uuid.UUID | None = None
    ) -> dict[uuid.UUID, int]:
        """Active grants per person, in one grouped statement."""
        statement = (
            select(PrUserCapability.user_id, func.count())
            .where(
                PrUserCapability.revoked_at.is_(None),
                (PrUserCapability.effective_from.is_(None))
                | (PrUserCapability.effective_from <= on),
                (PrUserCapability.effective_to.is_(None)) | (PrUserCapability.effective_to >= on),
            )
            .group_by(PrUserCapability.user_id)
        )
        if user_id is not None:
            statement = statement.where(PrUserCapability.user_id == user_id)
        rows = (await self._session.execute(statement)).all()
        return {holder: int(count) for holder, count in rows}

    async def _require_user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFoundError(
                f"Không tìm thấy người dùng {user_id}",
                details={"reason": "member_not_found", "user_id": str(user_id)},
            )
        return user


def _capability_order(capability: PrCapability) -> tuple[int, str]:
    domains = list(PrCapabilityDomain)
    return (domains.index(capability_domain(capability)), capability_label(capability))


def status_since(user: User) -> datetime | None:
    return user.last_status_changed_at


__all__: list[str] = [
    "EffectiveCapability",
    "EffectivePermissions",
    "MemberCounts",
    "MemberList",
    "MemberRow",
    "OpenResponsibilities",
    "PrMembershipService",
    "RoleSummary",
]


def _source_of(access: Access, capability: PrCapability) -> str:
    """Where an unscoped capability comes from, for the screen."""
    if access.sees_all:
        return "ROLE"
    sources = {
        grant.source
        for grant in access.of(MODULE_PR)
        if grant.permission == capability.value and grant.scope == "ALL"
    }
    if SOURCE_DEPARTMENT in sources:
        return "DEPARTMENT"
    if SOURCE_PERSONAL in sources:
        return "PERSONAL"
    return "NONE"
