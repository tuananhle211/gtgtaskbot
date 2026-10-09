"""Display labels, role aliases, and the line between the two.

The rule this file exists to hold in place: ``Role`` is authority, a label is
wording. Renaming what a person reads must not rename anything the database,
the permission matrix, the audit trail or the policy engine works with - and an
alias must never be a second, weaker way of asking for a role.

So the tests come in three groups:

1. every user-facing surface prints the label ("Chủ sở hữu", "Nhân viên");
2. every input surface accepts the aliases and folds them to the enum, and a
   folded alias is refused exactly where the raw enum would have been;
3. the stored vocabulary - enum members, column definitions, migrations, audit
   payloads - is byte-for-byte what it was.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.user_service import UserService
from meobot.bot.commands import BY_NAME, render_help
from meobot.bot.texts import WELCOME_OWNER, welcome_for
from meobot.core.errors import AuthorizationError, ConflictError
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.invite import InviteCode
from meobot.db.models.user import User
from meobot.domain.identity.labels import (
    GUEST_LABEL,
    ROLE_ALIASES,
    ROLE_LABELS,
    RoleInput,
    actor_label,
    fold,
    parse_role,
    parse_role_prefix,
    role_label,
    role_labels,
)
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission, permissions_for
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import DecisionCode, RiskLevel
from meobot.tools.invite_tools import CreateInviteArgs
from tests.fakes import RecordingSession, SqliteDatabase, make_update
from tests.unit.test_policy_engine import StubTool, plan_for

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: The Dispatcher is process-wide and its deduplication middleware remembers
#: every ``update_id`` it has seen, so this file works in a range of its own.
FIRST_UPDATE_ID = 910_000


# --- 1. What people read ----------------------------------------------------
def test_owner_displays_as_chu_so_huu() -> None:
    """OWNER is workspace ownership, not the department head's job title.

    "Trưởng phòng" names an approval gate and the person who holds it; it is
    never printed for the role, whoever happens to own the workspace.
    """
    assert role_label(Role.OWNER) == "Chủ sở hữu"
    assert "Trưởng phòng" not in ROLE_LABELS.values()


def test_employee_displays_as_nhan_vien() -> None:
    assert role_label(Role.EMPLOYEE) == "Nhân viên"


def test_every_role_has_the_specified_label() -> None:
    assert ROLE_LABELS == {
        Role.OWNER: "Chủ sở hữu",
        Role.ADMIN: "Quản trị viên",
        Role.TEAM_LEAD: "Trưởng nhóm",
        Role.EMPLOYEE: "Nhân viên",
    }
    # No role may go unlabelled, or a message would render an enum by accident.
    assert set(ROLE_LABELS) == set(Role)


def test_someone_without_membership_is_a_guest() -> None:
    """Temporary access, no ``users`` row: shown as Guest, whatever role they carry."""
    guest = Actor(user_id=None, telegram_user_id=42, full_name="Khách", role=Role.EMPLOYEE)

    assert guest.is_guest is True
    assert actor_label(guest) == GUEST_LABEL == "Guest"


def test_the_bootstrap_owner_is_not_a_guest(owner_actor: Actor) -> None:
    """No ``users`` row either, but they are configuration, not a visitor."""
    assert owner_actor.is_guest is False
    assert actor_label(owner_actor) == "Chủ sở hữu"


def test_a_registered_member_is_not_a_guest(employee_actor: Actor) -> None:
    assert employee_actor.is_guest is False
    assert actor_label(employee_actor) == "Nhân viên"


def test_the_welcome_message_uses_labels() -> None:
    assert "Chủ sở hữu" in WELCOME_OWNER
    assert "Trưởng phòng" not in WELCOME_OWNER
    assert "OWNER" not in WELCOME_OWNER

    greeting = welcome_for(Role.TEAM_LEAD, "Trưởng nhóm B")
    assert "Vai trò của bạn: Trưởng nhóm" in greeting
    assert "TEAM_LEAD" not in greeting

    assert f"Vai trò của bạn: {GUEST_LABEL}" in welcome_for(Role.EMPLOYEE, "Khách", guest=True)


def test_a_policy_refusal_names_the_label_not_the_enum(employee_actor: Actor) -> None:
    """The refusal a user actually sees, from the real engine."""
    engine = PolicyEngine(
        {"invite.create": StubTool("invite.create", required_permission=Permission.USER_MANAGE)}
    )
    decision = engine.evaluate(plan_for("invite.create", risk_level=RiskLevel.HIGH), employee_actor)

    assert decision.allowed is False
    assert decision.code is DecisionCode.MISSING_PERMISSION
    assert "Nhân viên" in decision.reason
    assert "EMPLOYEE" not in decision.reason
    # The permission key is a stable identifier and stays exactly as it is.
    assert Permission.USER_MANAGE.value in decision.reason


def test_a_min_role_refusal_names_the_label(employee_actor: Actor) -> None:
    engine = PolicyEngine({"sheet.add": StubTool("sheet.add", min_role=Role.ADMIN)})
    decision = engine.evaluate(plan_for("sheet.add"), employee_actor)

    assert decision.reason == "Cần vai trò Quản trị viên trở lên."


@pytest.mark.parametrize("role", list(Role))
def test_help_never_prints_an_internal_enum_name(role: Role) -> None:
    body = render_help(role)
    for name in (member.value for member in Role):
        assert name not in body, f"/help exposes the internal name {name}"


def test_the_people_commands_document_roles_by_label() -> None:
    for command in ("create_invite", "add_user"):
        spec = BY_NAME[command]
        text = f"{spec.usage} {spec.example}"
        assert "Nhân viên" in text
        assert "EMPLOYEE" not in text


def test_labels_are_listed_least_privileged_first() -> None:
    assert role_labels(set(Role)) == "Nhân viên, Trưởng nhóm, Quản trị viên, Chủ sở hữu"


# --- 2. What people type ----------------------------------------------------
@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("OWNER", Role.OWNER),
        ("TRUONG_PHONG", Role.OWNER),
        ("trưởng phòng", Role.OWNER),
        ("Trưởng Phòng", Role.OWNER),
        ("chủ hệ thống", Role.OWNER),
        ("ADMIN", Role.ADMIN),
        ("quản trị viên", Role.ADMIN),
        ("TEAM_LEAD", Role.TEAM_LEAD),
        ("TRUONG_NHOM", Role.TEAM_LEAD),
        ("trưởng nhóm", Role.TEAM_LEAD),
        ("team lead", Role.TEAM_LEAD),
        ("EMPLOYEE", Role.EMPLOYEE),
        ("MEMBER", Role.EMPLOYEE),
        ("member", Role.EMPLOYEE),
        ("nhân viên", Role.EMPLOYEE),
        # Shapes people type without meaning to.
        ("  employee  ", Role.EMPLOYEE),
        ("team-lead", Role.TEAM_LEAD),
        ("truong nhom", Role.TEAM_LEAD),
    ],
)
def test_every_specified_alias_resolves(written: str, expected: Role) -> None:
    assert parse_role(written) is expected


def test_every_declared_alias_is_reachable() -> None:
    """The table in the module and the parser cannot disagree."""
    for role, aliases in ROLE_ALIASES.items():
        for alias in aliases:
            assert parse_role(alias) is role, f"{alias!r} does not resolve to {role}"


def test_a_label_typed_back_resolves_to_its_role() -> None:
    for role, label in ROLE_LABELS.items():
        assert parse_role(label) is role


@pytest.mark.parametrize(
    "written",
    [
        "",
        "   ",
        "GUEST",
        "Guest",
        "SUPERUSER",
        "root",
        "sếp",
        # A whole sentence is not a role, however many role words are in it.
        "chị ấy là trưởng phòng marketing",
        "tôi là admin của công ty",
        "Trưởng phòng Nhân sự Nguyễn Văn A",
    ],
)
def test_a_role_is_never_inferred_from_prose_or_a_job_title(written: str) -> None:
    """Authority comes from a grant, never from what somebody calls themselves."""
    assert parse_role(written) is None


def test_the_guest_label_is_not_a_role() -> None:
    """ "Guest" is wording for a missing membership; it can never be granted."""
    assert parse_role(GUEST_LABEL) is None
    assert GUEST_LABEL not in {role.value for role in Role}


def test_a_multi_word_role_leaves_the_name_alone() -> None:
    """``/add_user 42 trưởng nhóm Nguyễn Văn A`` must not eat half the name."""
    role, rest = parse_role_prefix(["trưởng", "nhóm", "Nguyễn", "Văn", "A"])

    assert role is Role.TEAM_LEAD
    assert rest == ["Nguyễn", "Văn", "A"]


def test_an_unparsable_prefix_consumes_nothing() -> None:
    role, rest = parse_role_prefix(["5", "7"])

    assert role is None
    assert rest == ["5", "7"]


def test_folding_is_what_makes_the_alias_table_small() -> None:
    assert fold("TRUONG_PHONG") == fold("Trưởng Phòng") == fold(" trưởng-phòng ")


def test_a_tool_call_alias_is_normalised_before_anything_else_sees_it() -> None:
    """An LLM writing "MEMBER" produces the authoritative enum, not a string."""
    assert CreateInviteArgs(role="MEMBER").role is Role.EMPLOYEE
    assert CreateInviteArgs(role="trưởng nhóm").role is Role.TEAM_LEAD
    assert CreateInviteArgs().role is Role.EMPLOYEE


def test_a_tool_call_with_an_invented_role_is_still_rejected() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        CreateInviteArgs(role="SUPERUSER")


def test_role_input_accepts_the_enum_unchanged() -> None:
    from pydantic import BaseModel

    class Holder(BaseModel):
        role: RoleInput

    assert Holder(role=Role.ADMIN).role is Role.ADMIN


# --- Aliases do not bypass permission checks --------------------------------
@pytest.mark.parametrize("alias", ["MEMBER", "member", "nhân viên", "EMPLOYEE"])
def test_an_alias_grants_exactly_the_permissions_of_its_enum(alias: str) -> None:
    resolved = parse_role(alias)

    assert resolved is Role.EMPLOYEE
    assert permissions_for(resolved) == permissions_for(Role.EMPLOYEE)
    assert not has_permission(resolved, Permission.USER_MANAGE)


def test_an_alias_does_not_promote_the_actor(employee_actor: Actor) -> None:
    """Saying "trưởng phòng" is not becoming one."""
    spoken = parse_role("trưởng phòng")

    assert spoken is Role.OWNER
    # ...and the actor's own role is untouched by anything they typed.
    assert employee_actor.role is Role.EMPLOYEE
    assert not has_permission(employee_actor.role, Permission.USER_MANAGE)


async def test_an_alias_cannot_be_used_to_grant_a_role_the_actor_lacks(
    session: AsyncSession, admin_actor: Actor, request_id: uuid.UUID
) -> None:
    """``chủ hệ thống`` folds to OWNER and is refused exactly like OWNER is."""
    service = UserService(session, AuditService(session))

    for written in ("chủ hệ thống", "OWNER", "TRUONG_PHONG"):
        role = parse_role(written)
        assert role is Role.OWNER
        with pytest.raises(AuthorizationError):
            await service.add_user(
                actor=admin_actor,
                request_id=request_id,
                telegram_user_id=51_000,
                role=role,
            )

    assert (await session.execute(select(User))).scalars().all() == []


async def test_a_member_cannot_add_anybody(
    session: AsyncSession, employee_actor: Actor, request_id: uuid.UUID
) -> None:
    service = UserService(session, AuditService(session))

    with pytest.raises(AuthorizationError):
        await service.add_user(
            actor=employee_actor,
            request_id=request_id,
            telegram_user_id=52_000,
            role=Role.EMPLOYEE,
        )


async def test_the_same_telegram_account_cannot_be_added_twice(
    session: AsyncSession, owner_actor: Actor, request_id: uuid.UUID
) -> None:
    service = UserService(session, AuditService(session))
    await service.add_user(
        actor=owner_actor, request_id=request_id, telegram_user_id=53_000, role=Role.EMPLOYEE
    )

    with pytest.raises(ConflictError):
        await service.add_user(
            actor=owner_actor, request_id=request_id, telegram_user_id=53_000, role=Role.ADMIN
        )


# --- /add_user, end to end --------------------------------------------------
async def _users_in(database: SqliteDatabase) -> list[User]:
    async with database.session() as active:
        result = await active.execute(select(User))
        return list(result.scalars().all())


async def _audit_in(database: SqliteDatabase) -> list[AuditLog]:
    async with database.session() as active:
        result = await active.execute(select(AuditLog))
        return list(result.scalars().all())


async def test_add_user_member_creates_the_employee_role(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The rename, proven at both ends: "MEMBER" in, ``EMPLOYEE`` in the row."""
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot, make_update("/add_user 55500001 MEMBER Nguyễn Văn A", update_id=FIRST_UPDATE_ID + 1)
    )

    users = await _users_in(bot_database)
    assert len(users) == 1
    assert users[0].role is Role.EMPLOYEE
    assert users[0].telegram_user_id == 55500001
    assert users[0].full_name == "Nguyễn Văn A"
    # What the owner is told is the label, and the exact sentence specified.
    assert "Đã thêm Nguyễn Văn A với vai trò Nhân viên." in session.combined_text()
    assert "EMPLOYEE" not in session.combined_text()


async def test_add_user_employee_is_still_accepted(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """Backward compatibility: the old spelling does the same thing."""
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_update(
            "/add_user 55500002 EMPLOYEE Người Cũ", update_id=FIRST_UPDATE_ID + 2, message_id=2
        ),
    )

    users = await _users_in(bot_database)
    assert len(users) == 1
    assert users[0].role is Role.EMPLOYEE
    assert "vai trò Nhân viên" in session.combined_text()


async def test_add_user_accepts_a_vietnamese_multi_word_role(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_update(
            "/add_user 55500003 trưởng nhóm Trần Thị B", update_id=FIRST_UPDATE_ID + 3, message_id=3
        ),
    )

    users = await _users_in(bot_database)
    assert len(users) == 1
    assert users[0].role is Role.TEAM_LEAD
    assert users[0].full_name == "Trần Thị B"
    assert "vai trò Trưởng nhóm" in session.combined_text()


async def test_add_user_refuses_an_unknown_role_without_creating_anything(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_update(
            "/add_user 55500004 SUPERUSER Ai Đó", update_id=FIRST_UPDATE_ID + 4, message_id=4
        ),
    )

    assert await _users_in(bot_database) == []
    reply = session.combined_text()
    assert "Vai trò không hợp lệ" in reply
    # The refusal offers the labels, not the enum names.
    assert "Nhân viên" in reply
    assert "EMPLOYEE" not in reply


async def test_add_user_without_arguments_answers_with_its_usage(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session

    await dispatcher.feed_update(
        bot, make_update("/add_user", update_id=FIRST_UPDATE_ID + 5, message_id=5)
    )

    assert await _users_in(bot_database) == []
    assert "Cú pháp: /add_user" in session.combined_text()


async def test_add_user_writes_the_enum_to_the_audit_trail(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The label may ride along; the queryable value stays ``EMPLOYEE``."""
    bot, _ = bot_and_session

    await dispatcher.feed_update(
        bot,
        make_update(
            "/add_user 55500006 Member Người Mới", update_id=FIRST_UPDATE_ID + 6, message_id=6
        ),
    )

    entries = [row for row in await _audit_in(bot_database) if row.action == "user.registered"]
    assert len(entries) == 1
    assert entries[0].after_data is not None
    assert entries[0].after_data["role"] == "EMPLOYEE"
    assert entries[0].after_data["role_label"] == "Nhân viên"
    assert entries[0].after_data["via"] == "add_user"


async def test_a_member_is_refused_add_user_over_telegram(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The permission check is the same one whatever spelling of the role is used."""
    bot, session = bot_and_session
    async with bot_database.transaction() as active:
        active.add(
            User(
                telegram_user_id=55500007,
                telegram_username="nv",
                full_name="Nhân viên",
                role=Role.EMPLOYEE,
                active=True,
            )
        )

    await dispatcher.feed_update(
        bot,
        make_update(
            "/add_user 55500008 MEMBER Bạn Tôi",
            user_id=55500007,
            update_id=FIRST_UPDATE_ID + 7,
            message_id=7,
        ),
    )

    assert {user.telegram_user_id for user in await _users_in(bot_database)} == {55500007}
    # Refused before the handler runs: only the owner has the full bot.
    assert "chỉ dành cho chủ sở hữu" in session.combined_text()


# --- 3. The stored vocabulary is untouched ----------------------------------
def test_the_role_enum_still_says_what_it_always_said() -> None:
    assert [role.value for role in Role] == ["OWNER", "ADMIN", "TEAM_LEAD", "EMPLOYEE"]
    assert [role.name for role in Role] == ["OWNER", "ADMIN", "TEAM_LEAD", "EMPLOYEE"]


def test_the_rank_order_is_unchanged() -> None:
    assert [role.rank for role in (Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN, Role.OWNER)] == [
        10,
        20,
        30,
        40,
    ]


@pytest.mark.parametrize("model", [User, InviteCode])
def test_the_database_columns_still_store_the_enum(model: Any) -> None:
    """A label must never reach a column: these are the values on disk."""
    assert set(model.__table__.c.role.type.enums) == {
        "OWNER",
        "ADMIN",
        "TEAM_LEAD",
        "EMPLOYEE",
    }


@pytest.mark.parametrize(
    "migration",
    ["alembic/versions/0001_initial_schema.py", "alembic/versions/0003_script_workflow.py"],
)
def test_the_migrations_still_declare_the_same_values(migration: str) -> None:
    body = (REPOSITORY_ROOT / migration).read_text(encoding="utf-8")
    assert 'ROLE_VALUES = ("OWNER", "ADMIN", "TEAM_LEAD", "EMPLOYEE")' in body


def test_permission_keys_are_untouched_by_the_rename() -> None:
    """The matrix is keyed by role and by permission string; neither moved."""
    assert set(permissions_for(Role.EMPLOYEE)) <= set(permissions_for(Role.OWNER))
    assert Permission.USER_MANAGE.value == "user.manage"
    assert has_permission(Role.OWNER, Permission.USER_MANAGE)
    assert not has_permission(Role.TEAM_LEAD, Permission.USER_MANAGE)


def test_logs_and_audit_descriptions_keep_the_enum(employee_actor: Actor) -> None:
    """``describe`` feeds logs, which operators grep against the database."""
    assert employee_actor.describe() == "Nhân viên A[EMPLOYEE]"


async def test_the_invite_audit_trail_keeps_the_enum_and_adds_the_label(
    session: AsyncSession, owner_actor: Actor, request_id: uuid.UUID
) -> None:
    from meobot.application.invite_service import InviteService

    invite, _ = await InviteService(session, AuditService(session)).create(
        actor=owner_actor, request_id=request_id, role=Role.EMPLOYEE
    )

    assert invite.role is Role.EMPLOYEE
    entries = (await session.execute(select(AuditLog))).scalars().all()
    created = [row for row in entries if row.action == "invite.created"]
    assert len(created) == 1
    recorded = created[0].after_data
    assert recorded is not None
    assert recorded["role"] == "EMPLOYEE"
    assert recorded["role_label"] == "Nhân viên"
