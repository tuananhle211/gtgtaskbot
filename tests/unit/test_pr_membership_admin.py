"""*Thành viên & Phân quyền*, phase 1: the web routes are Telegram's rules.

Six sections, in the order the request listed them:

1. **Characterization.** What the Telegram flow already does through
   ``UserService`` - adding, duplicates, single role, ``active``, grants
   independent of the role - pinned before anything is layered on top.
2. **The list and the roles tab.** Every state, counts, the actor's flags.
3. **Adding from the web.** Same service, same refusals, same audit row.
4. **Changing the base role.** Owner-only, no self-change, no ``OWNER``,
   grants untouched.
5. **Deactivating and reactivating.** History intact, grants ineffective while
   suspended and back when reactivated, the session refused, revoke and
   revoke as a terminal act: no restore route, no restore button.
6. **Owner safety and effective permissions.** The owner cannot be removed or
   demoted, nobody is promoted into ``OWNER``, and the provenance view says
   ``ROLE`` for a role and ``SCOPED_GRANT`` for a grant - never the other.

The last section walks the source to assert the web router holds no
membership rule of its own.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from meobot.application.audit_service import AuditService
from meobot.application.pr_content_service import CreateContentCommand
from meobot.application.pr_membership_service import PrMembershipService
from meobot.application.pr_services import build_pr_services
from meobot.application.pr_task_service import CreateTaskCommand
from meobot.application.user_service import UserService
from meobot.application.web_auth_service import LOGIN_TOKEN_PARAM, SESSION_COOKIE, WebAuthService
from meobot.core.errors import AuthorizationError, ConflictError
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.pr.grants import GrantScope
from meobot.domain.pr.membership import ASSIGNABLE_ROLES, role_capabilities
from meobot.domain.pr.models import PrContentType, PrTaskAssignmentRole
from meobot.domain.pr.policy import GRANT_BACKED, PrCapability
from tests.unit.pr_world import TODAY, World, selected

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meobot"

MEMBERS = "/api/pr/members"


def users_service(world: World) -> UserService:
    return UserService(world.session, AuditService(world.session))


def membership(world: World) -> PrMembershipService:
    return PrMembershipService(world.session, world.capabilities)


async def audit_rows(world: World, action: AuditAction) -> list[AuditLog]:
    rows = await world.session.execute(select(AuditLog).where(AuditLog.action == action.value))
    return list(rows.scalars().all())


def error_reason(response) -> str:  # type: ignore[no-untyped-def]
    body = response.json()
    return str(body["error"]["details"]["reason"])


# =============================================================================
# 1. Characterization: what the Telegram path already does
# =============================================================================


async def test_1a_telegram_add_creates_a_users_row_with_one_base_role(world: World) -> None:
    """``/add_user`` is ``UserService.add_user``: one row, one role, active."""
    users = users_service(world)
    created = await users.add_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        telegram_user_id=700_001,
        role=Role.TEAM_LEAD,
        full_name="Trần Mới",
    )
    assert created.status is UserStatus.ACTIVE
    assert created.active is True
    assert created.role is Role.TEAM_LEAD
    assert created.telegram_user_id == 700_001
    assert created.added_by_user_id == world.owner.id
    rows = await audit_rows(world, AuditAction.USER_REGISTERED)
    assert [row.entity_id for row in rows] == [str(created.id)]


async def test_1b_adding_the_same_telegram_account_twice_is_a_conflict(world: World) -> None:
    users = users_service(world)
    await users.add_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        telegram_user_id=700_002,
        role=Role.EMPLOYEE,
    )
    with pytest.raises(ConflictError) as error:
        await users.add_user(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            telegram_user_id=700_002,
            role=Role.EMPLOYEE,
        )
    assert error.value.details["reason"] == "member_already_registered"


async def test_1c_a_member_holds_exactly_one_base_role_and_grants_are_separate(
    world: World,
) -> None:
    """A grant adds an approval right in a scope; the role column is untouched."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    await world.session.refresh(world.member)
    assert world.member.role is Role.EMPLOYEE
    held = await world.capabilities.capabilities_for_actor(world.actor(world.member))
    assert PrCapability.PR_TEAM_LEAD_REVIEW in held
    assert PrCapability.PR_CHANNEL_MANAGE not in held


async def test_1d_active_follows_status_and_the_web_session_reads_active(world: World) -> None:
    users = users_service(world)
    await users.suspend(
        actor=world.actor(world.owner), request_id=world.request_id, user_id=world.member.id
    )
    await world.session.refresh(world.member)
    assert (world.member.active, world.member.status) == (False, UserStatus.SUSPENDED)
    await users.enable(
        actor=world.actor(world.owner), request_id=world.request_id, user_id=world.member.id
    )
    await world.session.refresh(world.member)
    assert (world.member.active, world.member.status) == (True, UserStatus.ACTIVE)


async def test_1e_role_capabilities_mirror_the_permission_matrix() -> None:
    """The roles tab is the matrix, not a second table."""
    for role in Role:
        held = role_capabilities(role)
        for capability in PrCapability:
            if capability in GRANT_BACKED:
                assert capability not in held
        assert (PrCapability.PR_CHANNEL_MANAGE in held) == has_permission(
            role, Permission.SETTINGS_WRITE
        )
    assert role_capabilities(Role.OWNER) == role_capabilities(Role.ADMIN)
    assert role_capabilities(Role.EMPLOYEE) < role_capabilities(Role.TEAM_LEAD)


# =============================================================================
# 2. The list and the roles tab
# =============================================================================


async def test_2a_the_list_shows_every_state_with_counts_and_labels(world: World) -> None:
    users = users_service(world)
    extra = await users.add_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        telegram_user_id=700_003,
        role=Role.EMPLOYEE,
        full_name="Đã Nghỉ",
    )
    await users.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, user_id=extra.id
    )
    await users.suspend(
        actor=world.actor(world.owner), request_id=world.request_id, user_id=world.member.id
    )
    await world.grant(world.lead, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())

    world.act_as(world.owner)
    response = world.client.get(MEMBERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"] == {"total": 4, "active": 2, "suspended": 1, "revoked": 1, "pending": 0}
    assert body["may_add"] and body["may_change_status"] and body["may_change_role"]
    assert [option["role"] for option in body["assignable_roles"]] == [
        role.value for role in ASSIGNABLE_ROLES
    ]
    by_id = {row["user_id"]: row for row in body["members"]}
    revoked = by_id[str(extra.id)]
    assert revoked["status"] == "revoked" and revoked["status_label"] == "Đã loại khỏi PR"
    assert revoked["is_active"] is False and revoked["telegram_linked"] is True
    suspended = by_id[str(world.member.id)]
    assert suspended["status"] == "suspended" and suspended["status_label"] == "Tạm khoá"
    lead = by_id[str(world.lead.id)]
    assert lead["role_label"] == role_label(Role.TEAM_LEAD) == "Trưởng nhóm"
    assert lead["active_grant_count"] == 1
    assert by_id[str(world.owner.id)]["role_label"] == "Chủ sở hữu"
    assert by_id[str(world.member.id)]["role_label"] == "Nhân viên"
    # "Trưởng phòng" is the approval gate and the person - never the role.
    assert "Trưởng phòng" not in {row["role_label"] for row in body["members"]}
    assert "status_reason" not in revoked and "private_chat_available" not in revoked


async def test_2b_an_admin_reads_the_list_but_may_not_change_status_or_role(
    world: World,
) -> None:
    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    world.act_as(admin)
    body = world.client.get(MEMBERS).json()
    assert body["may_add"] is True
    assert body["may_change_status"] is False
    assert body["may_change_role"] is False


async def test_2c_a_team_lead_and_a_member_cannot_read_the_roster(world: World) -> None:
    for user in (world.lead, world.member):
        world.act_as(user)
        response = world.client.get(MEMBERS)
        assert response.status_code == 403
        assert error_reason(response) == "member_read_forbidden"
        assert world.client.get("/api/pr/roles").status_code == 403


async def test_2d_the_roles_tab_is_the_matrix_grouped_by_domain(world: World) -> None:
    world.act_as(world.owner)
    response = world.client.get("/api/pr/roles")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "không thay đổi vai trò nền" in body["note"]
    roles = {row["role"]: row for row in body["roles"]}
    assert list(roles) == ["OWNER", "ADMIN", "TEAM_LEAD", "EMPLOYEE"]
    assert roles["OWNER"]["assignable"] is False
    assert roles["OWNER"]["active_member_count"] == 1
    assert roles["EMPLOYEE"]["active_member_count"] == 1
    assert roles["ADMIN"]["active_member_count"] == 0
    for row in body["roles"]:
        codes = {item["capability"] for item in row["capabilities"]}
        assert codes == {cap.value for cap in role_capabilities(Role(row["role"]))}
        assert not codes & {cap.value for cap in GRANT_BACKED}
        assert all(item["domain_label"] and item["label"] for item in row["capabilities"])


# =============================================================================
# 3. Adding from the web
# =============================================================================


async def test_3a_the_owner_adds_a_member_by_telegram_id(world: World) -> None:
    world.act_as(world.owner)
    response = world.client.post(
        MEMBERS,
        json={"telegram_user_id": 700_010, "full_name": "  Người Mới ", "role": "team_lead"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "TEAM_LEAD" and body["role_label"] == "Trưởng nhóm"
    assert body["status"] == "active" and body["full_name"] == "Người Mới"
    assert body["telegram_user_id"] == 700_010 and body["telegram_linked"] is True
    row = await world.session.get(User, uuid.UUID(body["user_id"]))
    assert row is not None and row.added_by_user_id == world.owner.id
    rows = await audit_rows(world, AuditAction.USER_REGISTERED)
    assert len(rows) == 1 and rows[0].actor_user_id == world.owner.id


async def test_3b_web_duplicates_and_bad_input_get_the_same_refusals(world: World) -> None:
    world.act_as(world.owner)
    assert (
        world.client.post(
            MEMBERS, json={"telegram_user_id": 700_011, "role": "EMPLOYEE"}
        ).status_code
        == 201
    )
    duplicate = world.client.post(MEMBERS, json={"telegram_user_id": 700_011, "role": "EMPLOYEE"})
    assert duplicate.status_code == 409 and error_reason(duplicate) == "member_already_registered"
    bad_id = world.client.post(MEMBERS, json={"telegram_user_id": 0, "role": "EMPLOYEE"})
    assert bad_id.status_code == 422 and error_reason(bad_id) == "invalid_telegram_id"
    bad_role = world.client.post(MEMBERS, json={"telegram_user_id": 700_012, "role": "boss"})
    assert bad_role.status_code == 422 and error_reason(bad_role) == "invalid_role"
    as_owner = world.client.post(MEMBERS, json={"telegram_user_id": 700_013, "role": "OWNER"})
    assert as_owner.status_code == 403 and error_reason(as_owner) == "invalid_role"


async def test_3c_an_admin_may_add_below_their_rank_and_a_member_may_not_add(
    world: World,
) -> None:
    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    world.act_as(admin)
    ok = world.client.post(MEMBERS, json={"telegram_user_id": 700_020, "role": "TEAM_LEAD"})
    assert ok.status_code == 201, ok.text
    peer = world.client.post(MEMBERS, json={"telegram_user_id": 700_021, "role": "ADMIN"})
    assert peer.status_code == 403 and error_reason(peer) == "invalid_role"
    world.act_as(world.member)
    refused = world.client.post(MEMBERS, json={"telegram_user_id": 700_022, "role": "EMPLOYEE"})
    assert refused.status_code == 403 and error_reason(refused) == "member_add_forbidden"


async def test_3d_a_revoked_account_is_not_resurrected_by_adding_it_again(world: World) -> None:
    users = users_service(world)
    gone = await users.add_user(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        telegram_user_id=700_030,
        role=Role.EMPLOYEE,
    )
    await users.revoke(actor=world.actor(world.owner), request_id=world.request_id, user_id=gone.id)
    world.act_as(world.owner)
    again = world.client.post(MEMBERS, json={"telegram_user_id": 700_030, "role": "EMPLOYEE"})
    assert again.status_code == 409 and error_reason(again) == "member_revoked"
    assert "đã bị loại khỏi PR" in again.json()["error"]["message"]
    # No duplicate identity was created, and no route undoes the revocation.
    rows = (
        (await world.session.execute(select(User).where(User.telegram_user_id == 700_030)))
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].status is UserStatus.REVOKED
    assert world.client.post(f"{MEMBERS}/{gone.id}/restore").status_code == 404
    # The revoked row stays visible with its history, read-only.
    listed = world.client.get(MEMBERS).json()
    row = next(entry for entry in listed["members"] if entry["user_id"] == str(gone.id))
    assert row["status_label"] == "Đã loại khỏi PR" and row["is_active"] is False
    assert world.client.get(f"{MEMBERS}/{gone.id}/effective-permissions").status_code == 200


# =============================================================================
# 4. Changing the base role
# =============================================================================


async def test_4a_the_owner_changes_a_role_and_grants_stay_where_they_were(world: World) -> None:
    grant_id = await world.grant(
        world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything()
    )
    world.act_as(world.owner)
    response = world.client.post(f"{MEMBERS}/{world.member.id}/role", json={"role": "TEAM_LEAD"})
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "TEAM_LEAD"
    assert response.json()["active_grant_count"] == 1
    grant = await world.session.get(PrUserCapability, grant_id)
    assert grant is not None and grant.revoked_at is None
    rows = await audit_rows(world, AuditAction.USER_ROLE_CHANGED)
    assert len(rows) == 1
    assert rows[0].before_data is not None and rows[0].after_data is not None
    assert rows[0].before_data["role"] == "EMPLOYEE"
    assert rows[0].after_data["role"] == "TEAM_LEAD"


async def test_4b_the_same_role_again_is_a_conflict_not_a_silent_no_op(world: World) -> None:
    world.act_as(world.owner)
    response = world.client.post(f"{MEMBERS}/{world.member.id}/role", json={"role": "EMPLOYEE"})
    assert response.status_code == 409 and error_reason(response) == "role_unchanged"
    assert await audit_rows(world, AuditAction.USER_ROLE_CHANGED) == []


async def test_4c_only_the_owner_changes_roles(world: World) -> None:
    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    for user in (admin, world.lead, world.member):
        world.act_as(user)
        response = world.client.post(
            f"{MEMBERS}/{world.member.id}/role", json={"role": "TEAM_LEAD"}
        )
        assert response.status_code == 403, user.full_name
        # ``_guard_target`` runs first and it is the status-manage rule that
        # refuses; the role-specific code is for an actor who passed it.
        assert error_reason(response) == "member_manage_forbidden"
    await world.session.refresh(world.member)
    assert world.member.role is Role.EMPLOYEE


async def test_4d_nobody_is_promoted_into_owner_and_nobody_changes_themselves(
    world: World,
) -> None:
    world.act_as(world.owner)
    to_owner = world.client.post(f"{MEMBERS}/{world.member.id}/role", json={"role": "OWNER"})
    # ``can_invite_role`` refuses before anything else: OWNER is never grantable.
    assert to_owner.status_code == 403 and error_reason(to_owner) == "invalid_role"
    on_self = world.client.post(f"{MEMBERS}/{world.owner.id}/role", json={"role": "ADMIN"})
    # The owner is protected as a *target* before the self rule is reached.
    assert on_self.status_code == 403 and error_reason(on_self) == "owner_protected"
    await world.session.refresh(world.owner)
    assert world.owner.role is Role.OWNER


async def test_4e_a_scoped_grant_never_changes_a_base_role(world: World) -> None:
    """Granting through the existing route leaves ``users.role`` alone."""
    world.act_as(world.owner)
    response = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.member.id),
            "capability": "PR_HEAD_REVIEW",
            "scope": {"content_type_scope": "ALL", "channel_scope": "ALL"},
        },
    )
    assert response.status_code in (200, 201), response.text
    await world.session.refresh(world.member)
    assert world.member.role is Role.EMPLOYEE


# =============================================================================
# 5. Deactivating, reactivating, revoking, restoring
# =============================================================================


async def deactivate(world: World, user: User, reason: str | None = None) -> dict:  # type: ignore[type-arg]
    world.act_as(world.owner)
    response = world.client.post(f"{MEMBERS}/{user.id}/deactivate", json={"reason": reason})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_5a_deactivation_keeps_history_and_the_row(world: World) -> None:
    content = await world.content(channels=(world.tiktok,))
    content.owner_user_id = world.member.id
    await world.session.flush()
    grant_id = await world.grant(
        world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything()
    )

    body = await deactivate(world, world.member, "Nghỉ việc tạm thời")
    assert body["status"] == "suspended" and body["status_label"] == "Tạm khoá"
    assert body["is_active"] is False
    assert body["active_grant_count"] == 1, "a grant is kept, not revoked, on suspension"

    await world.session.refresh(world.member)
    assert world.member.status is UserStatus.SUSPENDED
    assert world.member.active is False
    assert world.member.status_reason == "Nghỉ việc tạm thời"
    assert world.member.suspended_by_user_id == world.owner.id
    still = await world.session.get(User, world.member.id)
    assert still is not None
    await world.session.refresh(content)
    assert content.owner_user_id == world.member.id, "ownership is history, not reassigned"
    grant = await world.session.get(PrUserCapability, grant_id)
    assert grant is not None and grant.revoked_at is None
    rows = await audit_rows(world, AuditAction.USER_SUSPENDED)
    assert len(rows) == 1 and rows[0].actor_user_id == world.owner.id


async def test_5b_a_deactivated_member_is_refused_by_the_session_on_the_next_request(
    world: World,
) -> None:
    """The cookie stays; the next request finds ``active`` false and gets a 401."""
    auth = WebAuthService(world.session, world.settings)
    link = await auth.issue_login_link(user_id=world.member.id)
    token = link.url.split(f"{LOGIN_TOKEN_PARAM}=")[1]
    issued = await auth.redeem_login_token(token=token)
    assert (await auth.resolve_session(token=issued.token)) is not None

    await deactivate(world, world.member)
    assert (await auth.resolve_session(token=issued.token)) is None

    world.client.app.dependency_overrides.pop(  # type: ignore[attr-defined]
        __import__("meobot.api.deps", fromlist=["get_current_web_actor"]).get_current_web_actor,
        None,
    )
    world.client.cookies.set(SESSION_COOKIE, issued.token)
    assert world.client.get(MEMBERS).status_code == 401
    world.client.cookies.clear()


async def test_5c_a_deactivated_members_grants_do_not_authorise_anything(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())
    await deactivate(world, world.member)
    await world.session.refresh(world.member)
    subject = Actor(
        user_id=world.member.id,
        full_name=world.member.full_name,
        role=world.member.role,
        active=world.member.active,
    )
    assert subject.active is False
    # The grant row is untouched and the capability service still enumerates it
    # - suspension is enforced at the two doors, not by rewriting grants: the
    # web session refuses the actor (5b) and the Telegram middleware refuses
    # an inactive actor before any handler runs.
    held = await world.capabilities.capabilities_for_actor(subject)
    assert PrCapability.PR_TEAM_LEAD_REVIEW in held
    middleware = (SRC / "bot" / "middlewares.py").read_text(encoding="utf-8")
    assert "if actor is None or not actor.active:" in middleware
    engine = (SRC / "domain" / "policy" / "engine.py").read_text(encoding="utf-8")
    assert "if not actor.active:" in engine
    view = await membership(world).effective_permissions(
        actor=world.actor(world.owner), user_id=world.member.id, on=TODAY
    )
    assert view.is_active is False
    by_code = {row.capability: row for row in view.capabilities}
    # The grant is still *reported*, with its provenance: it is what returns
    # on reactivation. The banner says none of it is usable.
    assert by_code[PrCapability.PR_TEAM_LEAD_REVIEW].source == "SCOPED_GRANT"


async def test_5d_reactivation_restores_role_and_grants_unchanged(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())
    await deactivate(world, world.member)
    world.act_as(world.owner)
    response = world.client.post(f"{MEMBERS}/{world.member.id}/reactivate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active" and body["is_active"] is True
    assert body["role"] == "EMPLOYEE" and body["active_grant_count"] == 1
    await world.session.refresh(world.member)
    held = await world.capabilities.capabilities_for_actor(world.actor(world.member))
    assert PrCapability.PR_TEAM_LEAD_REVIEW in held
    assert len(await audit_rows(world, AuditAction.USER_ENABLED)) == 1


async def test_5e_only_the_owner_deactivates_and_a_revoked_member_cannot_be_reactivated(
    world: World,
) -> None:
    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    for user in (admin, world.lead, world.member):
        world.act_as(user)
        response = world.client.post(f"{MEMBERS}/{world.lead.id}/deactivate", json={})
        assert response.status_code == 403, user.full_name
        assert error_reason(response) == "member_manage_forbidden"

    world.act_as(world.owner)
    gone = world.client.post(f"{MEMBERS}/{world.member.id}/revoke", json={"reason": "Đã nghỉ"})
    assert gone.status_code == 200 and gone.json()["status"] == "revoked"
    back = world.client.post(f"{MEMBERS}/{world.member.id}/reactivate")
    # ``UserService.enable`` has always answered this with a ValidationError;
    # the web keeps the status and the code rather than inventing a new one.
    assert back.status_code == 422 and error_reason(back) == "member_revoked"
    assert "không thể kích hoạt lại" in back.json()["error"]["message"]
    await world.session.refresh(world.member)
    assert world.member.status is UserStatus.REVOKED
    assert len(await audit_rows(world, AuditAction.USER_REVOKED)) == 1
    assert len(await audit_rows(world, AuditAction.USER_ENABLED)) == 0


async def test_5f_responsibilities_count_what_is_open_and_change_nothing(world: World) -> None:
    services = build_pr_services(world.session, world.settings)
    owner = world.actor(world.owner)
    mine = await services.content.create_content(
        actor=owner,
        request_id=world.request_id,
        command=CreateContentCommand(
            title="Của tôi",
            brand_id=world.brand.id,
            owner_user_id=world.member.id,
            content_type=PrContentType.SHORT_VIDEO_SCRIPT,
            script_text="x",
        ),
    )
    task = await services.tasks.create_task(
        actor=owner,
        request_id=world.request_id,
        command=CreateTaskCommand(task_type="EDIT", title="Cắt video", content_id=mine.content.id),
    )
    await services.tasks.assign_user(
        actor=owner,
        request_id=world.request_id,
        task_id=task.id,
        user_id=world.member.id,
        assignment_role=PrTaskAssignmentRole.CONTRIBUTOR,
    )
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything())

    world.act_as(world.owner)
    response = world.client.get(f"{MEMBERS}/{world.member.id}/responsibilities")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_owned"] == 1
    assert body["open_tasks"] == 1
    assert body["open_work"] == 0
    assert body["kpi_drafts"] == 0
    assert body["active_grants"] == 1
    assert body["total"] == 3

    world.act_as(world.member)
    assert world.client.get(f"{MEMBERS}/{world.member.id}/responsibilities").status_code == 403


# =============================================================================
# 6. Owner safety and effective permissions
# =============================================================================


async def test_6a_the_owner_cannot_be_deactivated_revoked_or_demoted_by_anyone(
    world: World,
) -> None:
    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    for user, expected in (
        (admin, "member_manage_forbidden"),
        (world.owner, "owner_protected"),
    ):
        world.act_as(user)
        for path in ("deactivate", "revoke"):
            response = world.client.post(f"{MEMBERS}/{world.owner.id}/{path}", json={})
            assert response.status_code == 403, (user.full_name, path)
            assert error_reason(response) == expected
    await world.session.refresh(world.owner)
    assert world.owner.status is UserStatus.ACTIVE and world.owner.role is Role.OWNER
    users = users_service(world)
    with pytest.raises(AuthorizationError) as error:
        await users.change_role(
            actor=Actor(user_id=None, full_name="hệ thống", role=Role.OWNER),
            request_id=world.request_id,
            user_id=world.owner.id,
            role=Role.ADMIN,
        )
    assert error.value.details["reason"] == "owner_protected"


async def test_6b_a_member_cannot_elevate_themselves(world: World) -> None:
    world.act_as(world.member)
    response = world.client.post(f"{MEMBERS}/{world.member.id}/role", json={"role": "ADMIN"})
    assert response.status_code == 403 and error_reason(response) == "member_manage_forbidden"
    world.act_as(world.lead)
    response = world.client.post(f"{MEMBERS}/{world.lead.id}/role", json={"role": "ADMIN"})
    assert response.status_code == 403


async def test_6c_effective_permissions_say_where_each_permission_comes_from(
    world: World,
) -> None:
    scope = selected(
        content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
        channels=frozenset({world.tiktok.id}),
    )
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, scope)
    world.act_as(world.owner)
    response = world.client.get(f"{MEMBERS}/{world.member.id}/effective-permissions")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is True and body["role"] == "EMPLOYEE"
    rows = {row["capability"]: row for row in body["capabilities"]}
    assert set(rows) == {cap.value for cap in PrCapability}
    assert rows["PR_CONTENT_EDIT"] == {
        **rows["PR_CONTENT_EDIT"],
        "allowed": True,
        "source": "ROLE",
        "grants": [],
    }
    assert rows["PR_CHANNEL_MANAGE"]["allowed"] is False
    assert rows["PR_CHANNEL_MANAGE"]["source"] == "NONE"
    lead_review = rows["PR_TEAM_LEAD_REVIEW"]
    assert lead_review["allowed"] is True and lead_review["source"] == "SCOPED_GRANT"
    assert len(lead_review["grants"]) == 1
    assert lead_review["grants"][0]["scope"]["channel_ids"] == [str(world.tiktok.id)]
    assert lead_review["grants"][0]["scope"]["content_types"] == ["SHORT_VIDEO_SCRIPT"]
    assert "note" not in lead_review["grants"][0]
    head_review = rows["PR_HEAD_REVIEW"]
    assert head_review["allowed"] is False and head_review["source"] == "NONE"
    assert all(row["domain_label"] and row["label"] for row in body["capabilities"])


async def test_6d_a_role_never_shows_as_a_grant_and_an_expired_grant_is_absent(
    world: World,
) -> None:
    await world.grant(
        world.lead,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope.everything(),
        effective_to=TODAY - timedelta(days=1),
    )
    world.act_as(world.owner)
    body = world.client.get(
        f"{MEMBERS}/{world.lead.id}/effective-permissions", params={"on": TODAY.isoformat()}
    ).json()
    rows = {row["capability"]: row for row in body["capabilities"]}
    assert rows["PR_TEAM_LEAD_REVIEW"]["source"] == "NONE"
    for code, row in rows.items():
        if PrCapability(code) not in GRANT_BACKED:
            assert row["source"] in ("ROLE", "NONE") and row["grants"] == []


async def test_6e_a_member_may_read_their_own_permissions_but_not_a_colleagues(
    world: World,
) -> None:
    world.act_as(world.member)
    own = world.client.get(f"{MEMBERS}/{world.member.id}/effective-permissions")
    assert own.status_code == 200
    other = world.client.get(f"{MEMBERS}/{world.lead.id}/effective-permissions")
    assert other.status_code == 403 and error_reason(other) == "member_read_forbidden"
    unknown = world.client.get(f"{MEMBERS}/{uuid.uuid4()}/effective-permissions")
    world.act_as(world.owner)
    unknown = world.client.get(f"{MEMBERS}/{uuid.uuid4()}/effective-permissions")
    assert unknown.status_code == 404 and error_reason(unknown) == "member_not_found"


# =============================================================================
# 7. One implementation
# =============================================================================


def test_7a_the_web_router_delegates_every_write_to_user_service() -> None:
    source = (SRC / "api" / "routers" / "pr_members.py").read_text(encoding="utf-8")
    for method in ("add_user", "change_role", "suspend", "enable", "revoke"):
        assert f"users.{method}(" in source, method
    # Phase 1: revocation is terminal on the web, as it is on Telegram.
    assert "users.restore(" not in source and "/restore" not in source
    forbidden = (
        "user.role =",
        "user.status =",
        "user.active =",
        "session.delete(",
        "has_permission(",
    )
    for text in forbidden:
        assert text not in source, text
    assert "UserService(session, AuditService(session))" in source


def test_7b_telegram_and_web_build_the_same_service() -> None:
    for handler in ("invites.py", "people.py", "access.py"):
        text = (SRC / "bot" / "handlers" / handler).read_text(encoding="utf-8")
        assert re.search(r"UserService\(session, (audit|AuditService\(session\))\)", text), handler


def test_7c_the_membership_service_writes_nothing() -> None:
    source = (SRC / "application" / "pr_membership_service.py").read_text(encoding="utf-8")
    for text in (
        "session.add(",
        "session.delete(",
        "session.execute(update(",
        "session.execute(delete(",
    ):
        assert text not in source, text
