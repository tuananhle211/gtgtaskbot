"""HTTP surface for *Thành viên & Phân quyền*, phase 1.

Every route here is a translator, as in :mod:`meobot.api.routers.pr`. The
reads go to :class:`~meobot.application.pr_membership_service.PrMembershipService`;
every write goes to :class:`~meobot.application.user_service.UserService` -
**the same object, built the same way, that ``/add_user``, ``/suspend_user``,
``/enable_user``, ``/revoke_user`` and ``/change_user_role`` call** from the
Telegram handlers. There is no membership rule in this file: who may add whom,
which roles may be assigned, whether the owner can be touched, whether a person
may change themselves - all of it is decided once, in the service, and a
browser and a chat get the same answer.

Vocabulary, because it is easy to say the wrong thing on a button:

* ``deactivate`` is ``UserService.suspend`` - *Vô hiệu hóa thành viên*. The
  person stops being able to sign in or act; their history, their role and
  their grants stay on the row and come back on ``reactivate``.
* ``revoke`` is ``UserService.revoke`` - *Loại khỏi PR*. **Terminal in phase
  1.** ``UserService.restore`` exists as a service method but no Telegram
  command or tool exposes it, so the web exposes nothing either: a button the
  canonical surface does not have would be a second lifecycle. A revoked row
  stays readable - its history and its effective permissions - and that is all.
* Nothing is deleted. A member who owns content, contributed work or approved
  something is referenced from those rows, and a route that removed the row
  would leave every one of them pointing at nobody.

Approval grants are not managed here. The existing ``/api/pr/capabilities``
routes are the grant surface, and the grants tab still calls them; this router
only *reports* grants, inside :func:`effective_permissions`, so an
administrator can see where a permission comes from.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep, get_session
from meobot.api.schemas.pr_members import (
    AddMemberRequest,
    ChangeRoleRequest,
    EffectivePermissionsResponse,
    MemberListResponse,
    MemberResponse,
    ResponsibilitiesResponse,
    RoleOptionResponse,
    RoleResponse,
    RolesResponse,
    StatusChangeRequest,
)
from meobot.application.audit_service import AuditService
from meobot.application.pr_membership_service import MemberRow, PrMembershipService
from meobot.application.user_service import UserService
from meobot.core.errors import ValidationError
from meobot.core.time import utcnow
from meobot.db.models.user import User
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.membership import ASSIGNABLE_ROLES

router = APIRouter(prefix="/api/pr", tags=["pr-members"])

#: The sentence the roles tab and the grants tab print about scoped grants.
#: Server-owned so the two tabs, and Telegram if it ever prints it, agree.
GRANT_NOTE = (
    "Quyền duyệt cấp thêm chỉ áp dụng trong đúng phạm vi đã chọn "
    "và không thay đổi vai trò nền của thành viên."
)


def get_membership_service(services: PrServicesDep) -> PrMembershipService:
    return PrMembershipService(services.session, services.capabilities)


def get_user_service(session: Annotated[AsyncSession, Depends(get_session)]) -> UserService:
    """The canonical membership service, built exactly as the bot builds it."""
    return UserService(session, AuditService(session))


MembershipDep = Annotated[PrMembershipService, Depends(get_membership_service)]
UsersDep = Annotated[UserService, Depends(get_user_service)]


def _parse_role(value: str) -> Role:
    """A role code from the wire, or a 422 that names the field.

    Only the code is accepted - ``ADMIN``, not ``Quản trị viên`` - so a label
    change can never change what a request means.
    """
    try:
        return Role(value.strip().upper())
    except ValueError as error:
        raise ValidationError(
            f"Vai trò không hợp lệ: {value!r}.",
            details={"reason": "invalid_role", "field": "role", "value": value},
        ) from error


def _assignable() -> list[RoleOptionResponse]:
    return [
        RoleOptionResponse(role=role.value, label=role_label(role)) for role in ASSIGNABLE_ROLES
    ]


async def _member_response(
    membership: PrMembershipService, actor: Actor, user: User
) -> MemberResponse:
    row = await membership.member(actor=actor, user_id=user.id)
    return MemberResponse.from_row(row)


@router.get("/members", response_model=MemberListResponse)
async def list_members(actor: CurrentActorDep, membership: MembershipDep) -> MemberListResponse:
    """Everyone with an account, in every state, with what this actor may do."""
    result = await membership.list_members(actor=actor)
    return MemberListResponse.from_domain(result, _assignable())


@router.post("/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    body: AddMemberRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    users: UsersDep,
    membership: MembershipDep,
) -> MemberResponse:
    """Register a member by Telegram id. ``/add_user``, over HTTP."""
    user = await users.add_user(
        actor=actor,
        request_id=request_id,
        telegram_user_id=body.telegram_user_id,
        role=_parse_role(body.role),
        full_name=body.full_name.strip(),
        telegram_username=body.telegram_username,
    )
    return MemberResponse.from_row(MemberRow(user=user))


@router.get("/members/{user_id}", response_model=MemberResponse)
async def get_member(
    user_id: uuid.UUID, actor: CurrentActorDep, membership: MembershipDep
) -> MemberResponse:
    return MemberResponse.from_row(await membership.member(actor=actor, user_id=user_id))


@router.post("/members/{user_id}/role", response_model=MemberResponse)
async def change_member_role(
    user_id: uuid.UUID,
    body: ChangeRoleRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    users: UsersDep,
    membership: MembershipDep,
) -> MemberResponse:
    """Change the base role. ``/change_user_role``, over HTTP.

    Owner-only, owner-protected, never to ``OWNER``, never on oneself - all
    inside the service. A scoped grant is not touched: it is a different fact
    about the person, and this route does not know it exists.
    """
    user = await users.change_role(
        actor=actor, request_id=request_id, user_id=user_id, role=_parse_role(body.role)
    )
    return await _member_response(membership, actor, user)


@router.post("/members/{user_id}/deactivate", response_model=MemberResponse)
async def deactivate_member(
    user_id: uuid.UUID,
    body: StatusChangeRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    users: UsersDep,
    membership: MembershipDep,
) -> MemberResponse:
    """*Vô hiệu hóa thành viên.* ``/suspend_user``, over HTTP. Reversible."""
    user = await users.suspend(
        actor=actor, request_id=request_id, user_id=user_id, reason=body.reason
    )
    return await _member_response(membership, actor, user)


@router.post("/members/{user_id}/reactivate", response_model=MemberResponse)
async def reactivate_member(
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    users: UsersDep,
    membership: MembershipDep,
) -> MemberResponse:
    """*Kích hoạt lại.* ``/enable_user``, over HTTP. A revoked account is refused."""
    user = await users.enable(actor=actor, request_id=request_id, user_id=user_id)
    return await _member_response(membership, actor, user)


@router.post("/members/{user_id}/revoke", response_model=MemberResponse)
async def revoke_member(
    user_id: uuid.UUID,
    body: StatusChangeRequest,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    users: UsersDep,
    membership: MembershipDep,
) -> MemberResponse:
    """*Loại khỏi PR.* ``/revoke_user``, over HTTP. The row stays; history stays."""
    user = await users.revoke(
        actor=actor, request_id=request_id, user_id=user_id, reason=body.reason
    )
    return await _member_response(membership, actor, user)


@router.get("/members/{user_id}/effective-permissions", response_model=EffectivePermissionsResponse)
async def effective_permissions(
    user_id: uuid.UUID,
    actor: CurrentActorDep,
    membership: MembershipDep,
    on: Annotated[date | None, Query()] = None,
) -> EffectivePermissionsResponse:
    """Every PR capability for one person, with its provenance, as of ``on``."""
    as_of = on or utcnow().date()
    result = await membership.effective_permissions(actor=actor, user_id=user_id, on=as_of)
    return EffectivePermissionsResponse.from_domain(result, as_of)


@router.get("/members/{user_id}/responsibilities", response_model=ResponsibilitiesResponse)
async def responsibilities(
    user_id: uuid.UUID, actor: CurrentActorDep, membership: MembershipDep
) -> ResponsibilitiesResponse:
    """What the person still holds. Read before a deactivation; changes nothing."""
    result = await membership.responsibilities(actor=actor, user_id=user_id)
    return ResponsibilitiesResponse.from_domain(user_id, result)


@router.get("/roles", response_model=RolesResponse)
async def list_roles(actor: CurrentActorDep, membership: MembershipDep) -> RolesResponse:
    """The four base roles and what each one carries. Read-only in phase 1."""
    summaries = await membership.roles(actor=actor)
    return RolesResponse(
        roles=[RoleResponse.from_domain(summary) for summary in summaries], note=GRANT_NOTE
    )


__all__: list[str] = ["router"]
