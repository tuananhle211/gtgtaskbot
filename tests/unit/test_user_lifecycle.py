"""Account lifecycle: add, suspend, enable, revoke, change role.

The invariant every test here circles is that **nothing is deleted**. "Xoá
người này khỏi hệ thống" is a revocation: the row stays, and every script,
approval and audit entry that names the person keeps pointing at a real user.
A system that answers "who approved this?" with a dangling id has lost the
thing the audit trail was for.

The other invariant is that the owner is out of reach. A deployment whose
administrator can be suspended by an administrator has no administrator.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from sqlalchemy import select

from meobot.application.audit_service import AuditService
from meobot.application.invite_service import InviteService
from meobot.application.quota_service import QuotaService
from meobot.application.user_service import UserService, status_label
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConflictError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.conversation import ConversationThread
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import (
    RecordingSession,
    SqliteDatabase,
    make_group_update,
    member_reply_message,
)

BASE = 500_000
MEMBER = 920_001
GROUP = -3001


def uid(offset: int) -> int:
    return BASE + offset


@pytest.fixture
def owner() -> Actor:
    """The bootstrap owner, as the gate resolves them."""
    return Actor(
        telegram_user_id=OWNER_TELEGRAM_ID,
        full_name="Owner",
        role=Role.OWNER,
        is_bootstrap_owner=True,
    )


async def make_member(
    session: object, *, telegram_user_id: int = MEMBER, role: Role = Role.EMPLOYEE
) -> User:
    user = User(
        telegram_user_id=telegram_user_id,
        telegram_username="tv",
        full_name="Nguyễn Văn A",
        role=role,
        active=True,
        status=UserStatus.ACTIVE,
    )
    session.add(user)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    return user


# --- Permissions ------------------------------------------------------------
def test_only_the_owner_may_change_an_account_status() -> None:
    """Adding a user stays with ADMIN; changing their status does not."""
    for permission in (
        Permission.USER_STATUS_MANAGE,
        Permission.USER_ROLE_MANAGE,
        Permission.USER_QUOTA_MANAGE,
        Permission.GUEST_ACCESS_MANAGE,
        Permission.GROUP_MEMBER_POLICY_MANAGE,
    ):
        assert has_permission(Role.OWNER, permission)
        assert not has_permission(Role.ADMIN, permission)
        assert not has_permission(Role.TEAM_LEAD, permission)
        assert not has_permission(Role.EMPLOYEE, permission)


def test_the_existing_admin_invitation_permission_is_untouched() -> None:
    """This release must not quietly narrow what an admin could already do."""
    assert has_permission(Role.ADMIN, Permission.USER_MANAGE)
    assert has_permission(Role.ADMIN, Permission.USER_READ)
    assert not has_permission(Role.TEAM_LEAD, Permission.USER_MANAGE)


# --- Suspend / enable / revoke ---------------------------------------------
async def test_suspend_blocks_the_account_without_deleting_it(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    users = UserService(session, AuditService(session))
    user = await make_member(session)

    await users.suspend(actor=owner, request_id=request_id, user_id=user.id, reason="nghỉ dài")

    assert user.status is UserStatus.SUSPENDED
    assert user.active is False
    assert user.may_use_meobot is False
    assert user.status_reason == "nghỉ dài"
    # The row is still there, with its id intact.
    still_there = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert still_there.full_name == "Nguyễn Văn A"


async def test_enable_restores_access(session, owner: Actor, request_id: uuid.UUID) -> None:
    users = UserService(session, AuditService(session))
    user = await make_member(session)
    await users.suspend(actor=owner, request_id=request_id, user_id=user.id)

    await users.enable(actor=owner, request_id=request_id, user_id=user.id)

    assert user.status is UserStatus.ACTIVE
    assert user.active is True
    assert user.status_reason is None


async def test_revoke_is_not_a_delete(session, owner: Actor, request_id: uuid.UUID) -> None:
    users = UserService(session, AuditService(session))
    user = await make_member(session)

    await users.revoke(actor=owner, request_id=request_id, user_id=user.id, reason="đã nghỉ việc")

    assert user.status is UserStatus.REVOKED
    rows = (await session.execute(select(User))).scalars().all()
    assert len(rows) == 1, "revocation must never remove the row"


async def test_a_revoked_account_cannot_be_re_enabled(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    """Revocation is meant to be final; undoing it is a separate, named act."""
    users = UserService(session, AuditService(session))
    user = await make_member(session)
    await users.revoke(actor=owner, request_id=request_id, user_id=user.id)

    with pytest.raises(ValidationError):
        await users.enable(actor=owner, request_id=request_id, user_id=user.id)

    await users.restore(actor=owner, request_id=request_id, user_id=user.id)
    assert user.status is UserStatus.ACTIVE


async def test_a_revoked_account_cannot_be_recreated_through_an_old_invite(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    """Redeeming a code again must not resurrect access."""
    users = UserService(session, AuditService(session))
    invites = InviteService(session, AuditService(session))
    user = await make_member(session)
    await users.revoke(actor=owner, request_id=request_id, user_id=user.id)
    _, code = await invites.create(actor=owner, request_id=request_id, role=Role.EMPLOYEE)

    with pytest.raises(ValidationError):
        await invites.redeem(
            request_id=request_id,
            code=code,
            telegram_user_id=MEMBER,
            full_name="Nguyễn Văn A",
        )

    # ...and adding them directly is refused too, rather than silently reviving.
    with pytest.raises(ConflictError):
        await users.add_user(
            actor=owner,
            request_id=request_id,
            telegram_user_id=MEMBER,
            role=Role.EMPLOYEE,
        )
    assert user.status is UserStatus.REVOKED


# --- The owner is protected -------------------------------------------------
@pytest.mark.parametrize("operation", ["suspend", "revoke"])
async def test_the_owner_cannot_be_suspended_or_revoked(
    session, owner: Actor, request_id: uuid.UUID, operation: str
) -> None:
    users = UserService(session, AuditService(session))
    other_owner = await make_member(session, telegram_user_id=999_111, role=Role.OWNER)

    with pytest.raises(AuthorizationError):
        await getattr(users, operation)(actor=owner, request_id=request_id, user_id=other_owner.id)
    assert other_owner.status is UserStatus.ACTIVE


async def test_the_owner_cannot_be_demoted(session, owner: Actor, request_id: uuid.UUID) -> None:
    users = UserService(session, AuditService(session))
    other_owner = await make_member(session, telegram_user_id=999_112, role=Role.OWNER)

    with pytest.raises(AuthorizationError):
        await users.change_role(
            actor=owner, request_id=request_id, user_id=other_owner.id, role=Role.EMPLOYEE
        )
    assert other_owner.role is Role.OWNER


async def test_nobody_can_be_promoted_to_owner(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    """OWNER is configuration, not something a command can hand out."""
    users = UserService(session, AuditService(session))
    user = await make_member(session)

    with pytest.raises(AuthorizationError):
        await users.change_role(
            actor=owner, request_id=request_id, user_id=user.id, role=Role.OWNER
        )
    assert user.role is Role.EMPLOYEE


async def test_an_admin_cannot_suspend_anybody(session, request_id: uuid.UUID) -> None:
    admin = Actor(user_id=uuid.uuid4(), telegram_user_id=5, full_name="Quản trị", role=Role.ADMIN)
    users = UserService(session, AuditService(session))
    user = await make_member(session)

    with pytest.raises(AuthorizationError):
        await users.suspend(actor=admin, request_id=request_id, user_id=user.id)
    assert user.status is UserStatus.ACTIVE


# --- Role changes -----------------------------------------------------------
async def test_a_role_change_is_audited_with_enum_and_label(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    users = UserService(session, AuditService(session))
    user = await make_member(session)

    await users.change_role(
        actor=owner, request_id=request_id, user_id=user.id, role=Role.TEAM_LEAD
    )

    assert user.role is Role.TEAM_LEAD
    entries = (await session.execute(select(AuditLog))).scalars().all()
    changes = [row for row in entries if row.action == "user.role_changed"]
    assert len(changes) == 1
    assert changes[0].before_data == {"role": "EMPLOYEE", "role_label": "Nhân viên"}
    assert changes[0].after_data == {"role": "TEAM_LEAD", "role_label": "Trưởng nhóm"}


async def test_status_changes_are_audited_with_the_internal_enum(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    users = UserService(session, AuditService(session))
    user = await make_member(session)

    await users.suspend(actor=owner, request_id=request_id, user_id=user.id, reason="lý do riêng")

    entries = (await session.execute(select(AuditLog))).scalars().all()
    suspensions = [row for row in entries if row.action == "user.suspended"]
    assert len(suspensions) == 1
    assert suspensions[0].before_data == {"status": "active"}
    assert suspensions[0].after_data is not None
    assert suspensions[0].after_data["status"] == "suspended"
    assert suspensions[0].after_data["role"] == "EMPLOYEE"
    assert suspensions[0].after_data["role_label"] == "Nhân viên"
    # The reason is kept for the audit trail, and only there.
    assert suspensions[0].after_data["reason"] == "lý do riêng"


def test_status_labels_are_vietnamese() -> None:
    assert status_label(UserStatus.ACTIVE) == "Đang hoạt động"
    assert status_label(UserStatus.SUSPENDED) == "Tạm khoá"
    assert status_label(UserStatus.REVOKED) == "Đã loại khỏi PR"
    for status in UserStatus:
        assert status.value not in status_label(status)


# --- Direct add -------------------------------------------------------------
async def test_direct_add_records_who_added_them(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    users = UserService(session, AuditService(session))

    user = await users.add_user(
        actor=owner,
        request_id=request_id,
        telegram_user_id=MEMBER,
        role=Role.EMPLOYEE,
        full_name="Nguyễn Văn A",
    )

    assert user.status is UserStatus.ACTIVE
    assert user.added_by_user_id == owner.user_id
    assert user.last_status_changed_at is not None


# --- Telegram command surface ----------------------------------------------
async def test_suspend_over_telegram_needs_a_reply(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """No reply, no target: MeoBot asks rather than guessing from a name."""
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/suspend_user Nguyễn Văn A",
            update_id=uid(1),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            mention=False,
        ),
    )

    assert "reply trực tiếp" in session.combined_text()


async def test_suspend_over_telegram_refuses_a_bot_target(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    from tests.fakes import bot_reply_message

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/suspend_user",
            update_id=uid(2),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            mention=False,
            reply_to=bot_reply_message(chat_id=GROUP),
        ),
    )

    assert "một bot" in session.combined_text()


async def test_suspend_over_telegram_refuses_the_owner_as_target(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/suspend_user",
            update_id=uid(3),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            mention=False,
            reply_to=member_reply_message(
                chat_id=GROUP, user_id=OWNER_TELEGRAM_ID, full_name="Owner", message_id=9
            ),
        ),
    )

    assert role_label(Role.OWNER) in session.combined_text()
    assert "Không thể thay đổi quyền" in session.combined_text()


async def test_suspend_over_telegram_archives_the_open_thread(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """A status change should not leave a half-finished conversation waiting."""
    bot, session = bot_and_session
    async with bot_database.transaction() as active:
        await make_member(active)
        active.add(
            ConversationThread(
                bot_id=1, chat_id=GROUP, telegram_user_id=MEMBER, active=True, recent_references=[]
            )
        )

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/suspend_user nghỉ phép",
            update_id=uid(4),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            mention=False,
            reply_to=member_reply_message(
                chat_id=GROUP, user_id=MEMBER, full_name="Nguyễn Văn A", message_id=8
            ),
        ),
    )

    assert "Đã tạm khoá" in session.combined_text()
    async with bot_database.session() as active:
        threads = (await active.execute(select(ConversationThread))).scalars().all()
        users = (await active.execute(select(User))).scalars().all()
    assert all(thread.active is False for thread in threads)
    assert users[0].status is UserStatus.SUSPENDED


async def test_a_member_cannot_suspend_anybody_over_telegram(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    async with bot_database.transaction() as active:
        await make_member(active)
        await make_member(active, telegram_user_id=920_002)

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/suspend_user",
            update_id=uid(5),
            user_id=MEMBER,
            chat_id=GROUP,
            mention=False,
            reply_to=member_reply_message(
                chat_id=GROUP, user_id=920_002, full_name="Người Khác", message_id=7
            ),
        ),
    )

    assert "Chỉ Chủ sở hữu" in session.combined_text()
    async with bot_database.session() as active:
        users = (await active.execute(select(User))).scalars().all()
    assert all(user.status is UserStatus.ACTIVE for user in users)


async def test_quota_commands_report_and_override(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, session = bot_and_session
    async with bot_database.transaction() as active:
        user = await make_member(active)
        user_id = user.id

    await dispatcher.feed_update(
        bot,
        make_group_update(
            "/add_user_quota 5",
            update_id=uid(6),
            user_id=OWNER_TELEGRAM_ID,
            chat_id=GROUP,
            mention=False,
            reply_to=member_reply_message(
                chat_id=GROUP, user_id=MEMBER, full_name="Nguyễn Văn A", message_id=6
            ),
        ),
    )

    assert "Đã thêm 5 lượt" in session.combined_text()
    async with bot_database.session() as active:
        verdict = await QuotaService(active, settings).inspect_member(
            user_id=user_id, role=Role.EMPLOYEE
        )
    assert verdict.limit == 25


async def test_a_standing_limit_survives_midnight(
    bot_database: SqliteDatabase, settings: Settings, owner: Actor
) -> None:
    """A persistent override is copied onto each new day; a bonus is not."""
    async with bot_database.transaction() as session:
        user = await make_member(session)
        user_id = user.id
        quota = QuotaService(session, settings)
        await quota.set_daily_limit(
            user_id=user_id, limit=50, actor_user_id=None, actor_telegram_id=OWNER_TELEGRAM_ID
        )
        await quota.add_bonus(user_id=user_id, amount=10)
        today = await quota.inspect_member(user_id=user_id, role=Role.EMPLOYEE)
        tomorrow = await quota.inspect_member(
            user_id=user_id, role=Role.EMPLOYEE, now=utcnow() + timedelta(days=1)
        )

    assert today.limit == 60, "the standing limit and today's bonus compose"
    assert tomorrow.limit == 50, "the bonus was for today only"
