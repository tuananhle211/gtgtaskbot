"""Owner bootstrap and rejection of unregistered Telegram accounts."""

from __future__ import annotations

import uuid

from meobot.application.identity_service import IdentityService
from meobot.core.config import Settings
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import FakeResult, FakeSession


async def test_owner_telegram_id_is_recognised_before_any_user_row(
    settings: Settings,
) -> None:
    """The bootstrap owner works on a completely empty database."""
    session = FakeSession(results=[FakeResult()])
    service = IdentityService(session, settings)

    actor = await service.resolve_actor(OWNER_TELEGRAM_ID, full_name="Chị Mèo")

    assert actor is not None
    assert actor.role is Role.OWNER
    assert actor.is_bootstrap_owner is True
    assert actor.user_id is None


async def test_unregistered_user_gets_no_actor(settings: Settings) -> None:
    """An unknown Telegram account has no capabilities at all."""
    session = FakeSession(results=[FakeResult()])
    service = IdentityService(session, settings)

    actor = await service.resolve_actor(999_888_777, full_name="Người lạ")

    assert actor is None


async def test_database_user_wins_over_bootstrap_rule(settings: Settings) -> None:
    """Once the owner exists in `users`, that row is the source of truth."""
    user = User(
        telegram_user_id=OWNER_TELEGRAM_ID,
        telegram_username="owner",
        full_name="Chị Mèo",
        role=Role.ADMIN,
        active=True,
    )
    user.id = uuid.uuid4()
    session = FakeSession(results=[FakeResult([user])])
    service = IdentityService(session, settings)

    actor = await service.resolve_actor(OWNER_TELEGRAM_ID)

    assert actor is not None
    assert actor.user_id == user.id
    assert actor.role is Role.ADMIN
    assert actor.is_bootstrap_owner is False


async def test_registered_employee_is_resolved(settings: Settings) -> None:
    user = User(
        telegram_user_id=555,
        telegram_username="nv",
        full_name="Nhân viên",
        role=Role.EMPLOYEE,
        active=True,
    )
    user.id = uuid.uuid4()
    session = FakeSession(results=[FakeResult([user])])
    service = IdentityService(session, settings)

    actor = await service.resolve_actor(555)

    assert actor is not None
    assert actor.role is Role.EMPLOYEE
    assert actor.active is True


async def test_inactive_user_is_resolved_but_marked_inactive(settings: Settings) -> None:
    """Deactivation is enforced downstream, so the reason stays auditable."""
    user = User(
        telegram_user_id=556,
        telegram_username="cu",
        full_name="Đã nghỉ việc",
        role=Role.EMPLOYEE,
        active=False,
    )
    user.id = uuid.uuid4()
    session = FakeSession(results=[FakeResult([user])])
    service = IdentityService(session, settings)

    actor = await service.resolve_actor(556)

    assert actor is not None
    assert actor.active is False
