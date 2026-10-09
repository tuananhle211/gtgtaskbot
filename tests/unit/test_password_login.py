"""Password login (0045) and reset by Telegram (0046).

Runs the real app with **real per-request transactions** (a file SQLite
database, committed on success and rolled back on an exception, exactly as
:func:`meobot.api.deps.get_session` does on PostgreSQL). That matters here: a
failed login must still persist its counter and its audit row, which only a
committed transaction proves.

The sections:

* the default password signs in, flags the session, and the gate holds every
  route but ``/api/auth/*``, ``/api/account/me`` and ``/api/account/password``;
* the new-password rules (only: not empty, not the default), and that changing
  signs every *other* session out;
* every failure reads the same 401; five in a row lock the account (429);
* admin resets: OWNER and ADMIN only, never an ADMIN on an OWNER; a temporary
  password goes to the member's Telegram;
* "Quên mật khẩu?": always the same 202, a temporary password by Telegram that
  must be changed, sessions revoked, one reset per five minutes, the password
  never logged, audited or kept after delivery;
* the Telegram link and the bot are untouched;
* what is on disk is a scrypt hash and nothing else.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from meobot.api.deps import get_session, password_change_allows
from meobot.api.main import create_app
from meobot.application.account.passwords import (
    TEMPORARY_PASSWORD_ALPHABET,
    burn_dummy_hash,
    check_new_password,
    generate_temporary_password,
    hash_password,
    matches_default,
    verify_password,
)
from meobot.application.delivery_service import TelegramDeliveryService
from meobot.application.outbox_service import OutboxService
from meobot.application.web_auth_service import SESSION_COOKIE, WebAuthService
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.base import Base
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionAuthMethod, WebSessionKind
from meobot.domain.access.models import UserStatus
from meobot.domain.account.errors import PasswordRejectedError
from meobot.domain.identity.models import Role
from meobot.domain.notifications.models import OutboxStatus
from meobot.domain.units.models import UnitCode, UnitMemberRole, unit_seed_id
from meobot.integrations.telegram.notifier import FakeNotifier

DEFAULT = "Apm@2026"
NEW = "Mèo con 2026"
FAILED_BODY = {
    "error": {
        "code": "login_failed",
        "message": "Sai ID Telegram hoặc mật khẩu.",
        "details": {"reason": "login_failed"},
    }
}
SRC = Path(__file__).resolve().parents[2] / "src" / "meobot"
RESET_BODY = {"message": "Nếu ID tồn tại, mật khẩu tạm đã được gửi qua Telegram."}


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    client: TestClient
    settings: Settings
    owner: uuid.UUID
    admin: uuid.UUID
    member: uuid.UUID
    other: uuid.UUID
    inactive: uuid.UUID
    suspended: uuid.UUID
    ads_only: uuid.UUID

    async def user(self, user_id: uuid.UUID) -> User:
        async with self.factory() as session:
            row = await session.get(User, user_id)
            assert row is not None
            return row

    async def set_user(self, user_id: uuid.UUID, **values: Any) -> None:
        async with self.factory() as session:
            await session.execute(update(User).where(User.id == user_id).values(**values))
            await session.commit()

    async def audit(self) -> list[AuditLog]:
        async with self.factory() as session:
            return list((await session.scalars(select(AuditLog).order_by(AuditLog.id))).all())

    async def outbox(self) -> list[OutboundMessage]:
        async with self.factory() as session:
            return list(
                (
                    await session.scalars(
                        select(OutboundMessage).order_by(OutboundMessage.created_at)
                    )
                ).all()
            )

    async def temporary_password(self, user_id: uuid.UUID) -> str:
        """The password in the newest message queued to ``user_id``."""
        rows = [row for row in await self.outbox() if row.recipient_user_id == user_id]
        assert rows, "no temporary password was queued"
        password = rows[-1].safe_payload_json["password"]
        assert isinstance(password, str) and password
        return password

    def forgot(self, username: str) -> Response:
        self.client.cookies.clear()
        return self.client.post("/api/auth/password-reset", json={"username": username})

    async def telegram_link_login(self, user_id: uuid.UUID) -> Response:
        async with self.factory() as session:
            issued = await WebAuthService(session, self.settings).issue_login_link(user_id=user_id)
            await session.commit()
        token = issued.url.split("t=")[1]
        self.client.cookies.clear()
        return self.client.get(f"/auth/login?t={token}", follow_redirects=False)

    def login(self, username: str, password: str, *, fresh: bool = True) -> Response:
        if fresh:
            self.client.cookies.clear()
        return self.client.post(
            "/api/auth/password-login", json={"username": username, "password": password}
        )

    def change(self, current: str, new: str) -> Response:
        return self.client.post(
            "/api/account/password", json={"current_password": current, "new_password": new}
        )

    def use(self, token: str) -> None:
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, token)


def _engine(path: Path) -> AsyncEngine:
    # NullPool: the TestClient runs the app on its own event loop, and an
    # aiosqlite connection belongs to the loop that opened it.
    return create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)


@pytest_asyncio.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    engine = _engine(tmp_path / "accounts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        units = {
            code: OrgUnit(id=unit_seed_id(code), code=code, name=f"Phòng {code.value}", settings={})
            for code in UnitCode
        }
        # Everybody active has started the bot, so a reset can reach them.
        reachable = {"private_chat_available": True}
        owner = User(full_name="Chủ", role=Role.OWNER, telegram_user_id=9001, **reachable)
        admin = User(full_name="Quản trị", role=Role.ADMIN, telegram_user_id=9002, **reachable)
        member = User(full_name="Hảo", role=Role.EMPLOYEE, telegram_user_id=9003, **reachable)
        other = User(full_name="Lan", role=Role.EMPLOYEE, telegram_user_id=9006, **reachable)
        inactive = User(full_name="Nghỉ", role=Role.EMPLOYEE, telegram_user_id=9004, active=False)
        suspended = User(
            full_name="Tạm khoá",
            role=Role.EMPLOYEE,
            telegram_user_id=9007,
            status=UserStatus.SUSPENDED,
        )
        ads_only = User(full_name="Dựng A", role=Role.EMPLOYEE, telegram_user_id=9005, **reachable)
        session.add_all([*units.values(), owner, admin, member, other, inactive, suspended])
        session.add(ads_only)
        await session.flush()
        session.add(
            OrgUnitMember(
                unit_id=units[UnitCode.ADS].id, user_id=ads_only.id, role=UnitMemberRole.DUNG
            )
        )
        # The PR people are tagged PR (untagged sees no stream).
        session.add_all(
            OrgUnitMember(
                unit_id=units[UnitCode.PR].id, user_id=person.id, role=UnitMemberRole.MEMBER
            )
            for person in (admin, member, other, inactive, suspended)
        )
        await session.commit()

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    app = create_app(settings)

    async def transactional_session() -> AsyncIterator[AsyncSession]:
        async with factory() as active:
            try:
                yield active
            except Exception:
                await active.rollback()
                raise
            else:
                await active.commit()

    app.dependency_overrides[get_session] = transactional_session
    with TestClient(app) as client:
        yield World(
            factory=factory,
            client=client,
            settings=settings,
            owner=owner.id,
            admin=admin.id,
            member=member.id,
            other=other.id,
            inactive=inactive.id,
            suspended=suspended.id,
            ads_only=ads_only.id,
        )
    app.dependency_overrides.clear()
    await engine.dispose()


def _code(response: Response) -> str | None:
    body: dict[str, Any] = response.json()
    return body.get("error", {}).get("code")


def _reason(response: Response) -> str | None:
    body: dict[str, Any] = response.json()
    return body.get("error", {}).get("details", {}).get("reason")


# --- the hash ------------------------------------------------------------------


def test_01_the_hash_is_scrypt_salted_and_verifies_in_constant_time() -> None:
    stored = hash_password(NEW)
    scheme, n, r, p, salt, key = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(base64.b64decode(salt)) == 16 and len(base64.b64decode(key)) == 64
    assert NEW not in stored and len(stored) <= 255
    assert verify_password(NEW, stored)
    assert not verify_password("Mèo con 2027", stored)
    # A fresh salt every time: the same password never hashes the same twice.
    assert hash_password(NEW) != stored
    # The same Vietnamese word typed composed or decomposed is one password.
    decomposed = "Mèo con 2026"
    assert verify_password(decomposed, stored)
    # A corrupted row verifies nothing (and still costs a hash).
    assert not verify_password(NEW, "scrypt$16384$8$1$!!$!!")
    assert not verify_password(NEW, "plain-text")
    burn_dummy_hash(NEW)
    assert matches_default(DEFAULT, DEFAULT) and not matches_default("apm@2026", DEFAULT)


def test_02_the_new_password_rules() -> None:
    """Anything goes - except nothing at all, and the shared default."""
    for fine in ("a", "1", "123", "abcdefgh", "Mật khẩu có dấu cách", "x" * 1024, NEW):
        check_new_password(fine, default=DEFAULT)
    for empty in ("", "   ", "\t\n "):
        with pytest.raises(PasswordRejectedError) as caught:
            check_new_password(empty, default=DEFAULT)
        assert caught.value.code == "password_empty"
        assert caught.value.details["reason"] == "password_empty"
    with pytest.raises(PasswordRejectedError) as caught:
        check_new_password("x" * 1025, default=DEFAULT)
    assert caught.value.code == "password_too_long"
    with pytest.raises(PasswordRejectedError) as caught:
        check_new_password(DEFAULT, default=DEFAULT)
    assert caught.value.code == "password_is_default"


def test_02b_temporary_passwords_are_random_and_unambiguous() -> None:
    seen = {generate_temporary_password() for _ in range(200)}
    assert len(seen) == 200
    for password in seen:
        assert len(password) == 10
        assert set(password) <= set(TEMPORARY_PASSWORD_ALPHABET)
        assert any(char.isdigit() for char in password)
        assert any(char.isalpha() for char in password)
    for ambiguous in "0O1lIo":
        assert ambiguous not in TEMPORARY_PASSWORD_ALPHABET


# --- default login and the gate -------------------------------------------------


async def test_03_the_default_password_signs_in_and_flags_the_session(world: World) -> None:
    response = world.login(" 9003 ", DEFAULT)
    assert response.status_code == 200, response.text
    assert response.json() == {"must_change_password": True}
    cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()

    session = world.client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["must_change_password"] is True
    assert session.json()["user_id"] == str(world.member)
    me = world.client.get("/api/account/me")
    assert me.status_code == 200, me.text
    assert me.json()["must_change_password"] is True
    assert me.json()["has_custom_password"] is False
    assert me.json()["telegram_user_id"] == 9003

    # Everything else is closed, with one reason, as a 403 (not a 401 that
    # would bounce the panel back to the login form).
    for path in (
        "/api/units/me",
        "/api/pr/dashboard",
        "/api/notifications",
        "/api/board/tasks",
        "/api/account/me/stats",
        "/api/account/members",
    ):
        blocked = world.client.get(path)
        assert blocked.status_code == 403, (path, blocked.text)
        assert _code(blocked) == "password_change_required", path
        assert _reason(blocked) == "password_change_required", path
    blocked = world.client.patch("/api/account/profile", json={"full_name": "Hảo Mới"})
    assert blocked.status_code == 403 and _code(blocked) == "password_change_required"

    # Sign-out stays open.
    assert world.client.post("/api/auth/logout").status_code == 204
    assert world.client.get("/api/auth/session").status_code == 401


def test_04_the_gate_allow_list_is_exact() -> None:
    assert password_change_allows("/api/auth/session")
    assert password_change_allows("/api/auth/logout")
    assert password_change_allows("/api/account/me")
    assert password_change_allows("/api/account/password")
    for closed in (
        "/api/account/me/stats",
        "/api/account/members",
        "/api/account/profile",
        "/api/account/password/x",
        "/api/accounts/me",
        "/api/authx",
        "/api/units/me",
    ):
        assert not password_change_allows(closed), closed


async def test_05_changing_the_password_lifts_the_gate(world: World) -> None:
    assert world.login("9003", DEFAULT).status_code == 200

    empty = world.change(DEFAULT, "   ")
    assert empty.status_code == 422 and _code(empty) == "password_empty"
    assert _reason(empty) == "password_empty"
    is_default = world.change(DEFAULT, DEFAULT)
    assert is_default.status_code == 422 and _code(is_default) == "password_is_default"
    wrong = world.change("Sai@2026x", NEW)
    assert wrong.status_code == 422 and _code(wrong) == "current_password_wrong"
    assert _reason(wrong) == "current_password_wrong"
    # A wrong current password counts towards the lockout - and the count was
    # committed even though the answer was an error.
    assert (await world.user(world.member)).failed_login_count == 1

    changed = world.change(DEFAULT, NEW)
    assert changed.status_code == 204, changed.text
    row = await world.user(world.member)
    assert row.password_hash is not None and row.password_hash.startswith("scrypt$16384$8$1$")
    assert row.password_changed_at is not None and row.failed_login_count == 0

    # Same browser, still signed in, gate lifted.
    session = world.client.get("/api/auth/session")
    assert session.status_code == 200 and session.json()["must_change_password"] is False
    assert world.client.get("/api/units/me").status_code == 200
    me = world.client.get("/api/account/me").json()
    assert me["must_change_password"] is False and me["has_custom_password"] is True
    assert me["password_changed_at"] is not None

    # The same password again is fine now: there are no rules beyond "not
    # empty, not the default".
    assert world.change(NEW, NEW).status_code == 204

    # The new password signs in without a flag; the default no longer does.
    fresh = world.login("9003", NEW)
    assert fresh.status_code == 200 and fresh.json() == {"must_change_password": False}
    assert world.login("9003", DEFAULT).status_code == 401

    actions = [row.action for row in await world.audit()]
    assert "auth.password.changed" in actions
    assert "auth.password.change_failed" in actions


async def test_06_a_change_signs_every_other_session_out(world: World) -> None:
    first = world.login("9003", DEFAULT).cookies[SESSION_COOKIE]
    second = world.login("9003", DEFAULT).cookies[SESSION_COOKIE]
    telegram = (await world.telegram_link_login(world.member)).cookies[SESSION_COOKIE]
    assert len({first, second, telegram}) == 3

    world.use(second)
    assert world.change(DEFAULT, NEW).status_code == 204
    assert world.client.get("/api/auth/session").status_code == 200
    for gone in (first, telegram):
        world.use(gone)
        assert world.client.get("/api/auth/session").status_code == 401


async def test_07_signing_in_again_replaces_the_browsers_session(world: World) -> None:
    old = world.login("9003", DEFAULT).cookies[SESSION_COOKIE]
    world.use(old)
    again = world.login("9003", DEFAULT, fresh=False)
    assert again.status_code == 200
    new = again.cookies[SESSION_COOKIE]
    assert new != old
    world.use(old)
    assert world.client.get("/api/auth/session").status_code == 401
    world.use(new)
    assert world.client.get("/api/auth/session").status_code == 200


# --- failures and the lockout ---------------------------------------------------


async def test_08_every_failure_reads_the_same(world: World) -> None:
    attempts = [
        ("9003", "Sai@2026"),  # wrong password
        ("123456", DEFAULT),  # unknown id
        ("9004", DEFAULT),  # deactivated
        ("9007", DEFAULT),  # suspended
        ("abc", DEFAULT),  # not an id at all
        ("-9003", DEFAULT),
        ("9" * 25, DEFAULT),
        ("9003", "x" * 5000),  # absurdly long
    ]
    for username, password in attempts:
        response = world.login(username, password)
        assert response.status_code == 401, (username, response.text)
        assert response.json() == FAILED_BODY, username
        assert SESSION_COOKIE not in response.cookies

    rows = [row for row in await world.audit() if row.action == "auth.password_login.failed"]
    assert len(rows) == len(attempts)
    # Only the real, active account was counted - inactive ones are strangers.
    assert (await world.user(world.member)).failed_login_count == 2
    assert (await world.user(world.inactive)).failed_login_count == 0


async def test_09_five_failures_lock_the_account_for_a_while(world: World) -> None:
    for _ in range(5):
        assert world.login("9003", "Sai@2026").status_code == 401
    row = await world.user(world.member)
    assert row.locked_until is not None

    # Locked: even the right password is refused, with its own code.
    locked = world.login("9003", DEFAULT)
    assert locked.status_code == 429
    assert _code(locked) == "login_locked" and _reason(locked) == "login_locked"
    assert SESSION_COOKIE not in locked.cookies
    # An unknown id is never "locked": it keeps reading as a plain failure.
    assert world.login("123456", "Sai@2026").status_code == 401

    # The Telegram link is not locked.
    link = await world.telegram_link_login(world.member)
    assert link.status_code == 303 and SESSION_COOKIE in link.cookies
    assert world.client.get("/api/units/me").status_code == 200
    # ... but changing the password while locked is refused too.
    locked_change = world.change(DEFAULT, NEW)
    assert locked_change.status_code == 429 and _code(locked_change) == "login_locked"

    # The lock expires; the right password works and the count starts over.
    await world.set_user(world.member, locked_until=utcnow() - timedelta(seconds=1))
    assert world.login("9003", DEFAULT).status_code == 200
    row = await world.user(world.member)
    assert row.failed_login_count == 0 and row.locked_until is None


async def test_10_a_success_resets_the_count(world: World) -> None:
    for _ in range(4):
        assert world.login("9003", "Sai@2026").status_code == 401
    assert world.login("9003", DEFAULT).status_code == 200
    for _ in range(4):
        assert world.login("9003", "Sai@2026").status_code == 401
    assert world.login("9003", DEFAULT).status_code == 200


# --- resets ----------------------------------------------------------------------


async def _with_custom_password(world: World, user_id: uuid.UUID, telegram_id: int) -> str:
    """Sign ``user_id`` in and give them their own password. Returns the cookie."""
    assert world.login(str(telegram_id), DEFAULT).status_code == 200
    assert world.change(DEFAULT, NEW).status_code == 204
    token = world.client.cookies[SESSION_COOKIE]
    assert (await world.user(user_id)).password_hash is not None
    return token


async def _sign_in(world: World, telegram_id: int) -> None:
    assert world.login(str(telegram_id), DEFAULT).status_code == 200
    assert world.change(DEFAULT, f"Riêng{telegram_id}x").status_code == 204


async def test_11_the_owner_resets_a_password(world: World) -> None:
    member_cookie = await _with_custom_password(world, world.member, 9003)
    old_hash = (await world.user(world.member)).password_hash
    await world.set_user(world.member, failed_login_count=3)
    await _sign_in(world, 9001)

    reset = world.client.post(f"/api/account/members/{world.member}/reset-password")
    assert reset.status_code == 204, reset.text
    row = await world.user(world.member)
    assert row.password_hash is not None and row.password_hash != old_hash
    assert row.password_temporary is True
    assert row.failed_login_count == 0 and row.locked_until is None
    # Signed out everywhere.
    world.use(member_cookie)
    assert world.client.get("/api/auth/session").status_code == 401

    # The temporary password went to the member's own chat, queued by the owner.
    [message] = await world.outbox()
    assert message.telegram_chat_id == 9003
    assert message.recipient_user_id == world.member
    assert message.created_by_user_id == world.owner
    assert message.template_key == "account.temporary_password"
    temporary = await world.temporary_password(world.member)

    # Not the shared default any more, and not the old password: the temporary
    # one, which must be changed.
    assert world.login("9003", NEW).status_code == 401
    assert world.login("9003", DEFAULT).status_code == 401
    assert world.login("9003", temporary).json() == {"must_change_password": True}
    me = world.client.get("/api/account/me").json()
    assert me["password_temporary"] is True and me["has_custom_password"] is False

    audited = [row for row in await world.audit() if row.action == "auth.password.reset"]
    assert len(audited) == 1 and audited[0].entity_id == str(world.member)
    assert temporary not in json.dumps(audited[0].after_data)

    # The roster says so too.
    assert world.login("9001", "Riêng9001x").status_code == 200
    roster = world.client.get("/api/account/members").json()["members"]
    [mine] = [entry for entry in roster if entry["user_id"] == str(world.member)]
    assert mine["password_temporary"] is True and mine["has_custom_password"] is False


async def test_12_who_may_reset_whom(world: World) -> None:
    # An ADMIN may not reset an OWNER.
    await _sign_in(world, 9002)
    refused = world.client.post(f"/api/account/members/{world.owner}/reset-password")
    assert refused.status_code == 403 and _code(refused) == "password_reset_forbidden"
    # An ADMIN sees every stream: an ORD-only person is theirs to reset too.
    ads = world.client.post(f"/api/account/members/{world.ads_only}/reset-password")
    assert ads.status_code == 204
    # Nor themselves.
    own = world.client.post(f"/api/account/members/{world.admin}/reset-password")
    assert own.status_code == 403 and _code(own) == "password_reset_forbidden"
    # A PR colleague is fine.
    assert (
        world.client.post(f"/api/account/members/{world.other}/reset-password").status_code == 204
    )
    missing = world.client.post(f"/api/account/members/{uuid.uuid4()}/reset-password")
    assert missing.status_code == 404
    # Somebody MeoBot cannot message privately: refused, and nothing changes.
    await world.set_user(world.other, private_chat_available=False)
    before = (await world.user(world.other)).password_hash
    unreachable = world.client.post(f"/api/account/members/{world.other}/reset-password")
    assert unreachable.status_code == 409
    assert _code(unreachable) == "password_reset_undeliverable"
    assert (await world.user(world.other)).password_hash == before

    # A member may not reset anybody.
    await _sign_in(world, 9003)
    member = world.client.post(f"/api/account/members/{world.other}/reset-password")
    assert member.status_code == 403 and _code(member) == "password_reset_forbidden"

    # The OWNER may reset an ADMIN and an Ads member.
    await _sign_in(world, 9001)
    for target in (world.admin, world.ads_only):
        assert world.client.post(f"/api/account/members/{target}/reset-password").status_code == 204


# --- the Telegram link and the bot ----------------------------------------------


async def test_13_the_telegram_link_still_signs_in_unflagged(world: World) -> None:
    response = await world.telegram_link_login(world.member)
    assert response.status_code == 303
    assert response.headers["location"] == "/pr"
    session = world.client.get("/api/auth/session")
    assert session.status_code == 200
    # Still on the default password - but this session proved identity through
    # Telegram, so it is not held to the change.
    assert session.json()["must_change_password"] is False
    assert world.client.get("/api/units/me").status_code == 200
    assert world.client.get("/api/account/me").json()["has_custom_password"] is False
    async with world.factory() as db:
        methods = (
            await db.scalars(
                select(WebSession.auth_method).where(
                    WebSession.user_id == world.member, WebSession.kind == WebSessionKind.SESSION
                )
            )
        ).all()
    assert methods == [WebSessionAuthMethod.TELEGRAM_LINK]


def test_14_the_gate_lives_only_in_the_web_layer() -> None:
    """The bot and the workers never see the must-change rule.

    It is enforced in one dependency of the HTTP layer; nothing under ``bot/``,
    ``workers/`` or ``tools/`` mentions it, so a Telegram command or a Celery
    task behaves exactly as before for somebody still on the default password.
    """
    for folder in ("bot", "workers", "tools", "integrations"):
        root = SRC / folder
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for word in ("must_change_password", "PasswordChangeRequired", "password_hash"):
                assert word not in source, (path, word)
    deps = (SRC / "api" / "deps.py").read_text(encoding="utf-8")
    assert deps.count("raise PasswordChangeRequiredError()") == 1


# --- what is on disk -------------------------------------------------------------


async def test_15_no_plain_text_ever_reaches_the_database(world: World) -> None:
    assert world.login("9003", DEFAULT).status_code == 200
    assert world.change(DEFAULT, NEW).status_code == 204
    assert world.login("9003", "Sai@2026x").status_code == 401

    async with world.factory() as db:
        dump: list[str] = []
        for table in ("users", "web_sessions", "audit_logs"):
            rows = (await db.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
            dump.extend(
                json.dumps([str(value) for value in row], ensure_ascii=False) for row in rows
            )
    everything = "\n".join(dump)
    for secret in (NEW, DEFAULT, "Sai@2026x"):
        assert secret not in everything, secret
    assert "Mèo" not in everything

    # The database itself refuses anything that is not a scrypt hash.
    async with world.factory() as db:
        with pytest.raises(IntegrityError):
            await db.execute(update(User).where(User.id == world.member).values(password_hash=NEW))
            await db.flush()
        await db.rollback()


async def test_16_profile_rename_is_trimmed_bounded_and_audited(world: World) -> None:
    await _sign_in(world, 9003)
    renamed = world.client.patch("/api/account/profile", json={"full_name": "  Hảo   Nguyễn  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["full_name"] == "Hảo Nguyễn"
    for bad in ("x", " ", "y" * 81):
        response = world.client.patch("/api/account/profile", json={"full_name": bad})
        assert response.status_code == 422 and _code(response) == "full_name_invalid", bad
    assert "user.profile.updated" in [row.action for row in await world.audit()]


# --- "Quên mật khẩu?" --------------------------------------------------------------


async def test_17_a_reset_request_reads_the_same_for_everybody(world: World) -> None:
    await world.set_user(world.other, private_chat_available=False)
    before = {user_id: (await world.user(user_id)).password_hash for user_id in (world.other,)}
    for username in ("123456", "9004", "9007", "abc", "-1", "9" * 25, "9006", " 9003 "):
        response = world.forgot(username)
        assert response.status_code == 202, (username, response.text)
        assert response.json() == RESET_BODY, username
        assert SESSION_COOKIE not in response.cookies

    # Only the real, active, reachable account got a password.
    [message] = await world.outbox()
    assert message.recipient_user_id == world.member and message.telegram_chat_id == 9003
    assert message.created_by_user_id is None
    assert (await world.user(world.member)).password_temporary is True
    # Unreachable (never started the bot): untouched, nothing sent.
    assert (await world.user(world.other)).password_hash == before[world.other]
    assert (await world.user(world.other)).password_temporary is False
    for untouched in (world.inactive, world.suspended):
        row = await world.user(untouched)
        assert row.password_hash is None and row.password_temporary is False

    requested = [
        row for row in await world.audit() if row.action == "auth.password.reset_requested"
    ]
    assert len(requested) == 8
    outcomes = [row.after_data["outcome"] for row in requested]
    assert outcomes.count("sent") == 1
    assert outcomes.count("undeliverable") == 1
    assert outcomes.count("unknown_or_inactive") == 6
    # No user enumeration through a body field or a missing route guard either:
    # the route needs no session at all.
    assert world.client.post("/api/auth/password-reset", json={}).status_code == 422


async def test_18_the_temporary_password_signs_in_and_must_be_changed(world: World) -> None:
    password_cookie = await _with_custom_password(world, world.member, 9003)
    telegram_cookie = (await world.telegram_link_login(world.member)).cookies[SESSION_COOKIE]
    await world.set_user(world.member, failed_login_count=4, locked_until=None)

    assert world.forgot("9003").json() == RESET_BODY
    temporary = await world.temporary_password(world.member)
    row = await world.user(world.member)
    assert row.password_temporary is True and row.failed_login_count == 0
    assert row.password_reset_at is not None

    # Every session of the account is gone, the Telegram one included.
    for gone in (password_cookie, telegram_cookie):
        world.use(gone)
        assert world.client.get("/api/auth/session").status_code == 401
    # The old password stops working; the temporary one signs in, flagged.
    assert world.login("9003", NEW).status_code == 401
    signed_in = world.login("9003", temporary)
    assert signed_in.status_code == 200 and signed_in.json() == {"must_change_password": True}
    assert world.client.get("/api/auth/session").json()["must_change_password"] is True
    blocked = world.client.get("/api/units/me")
    assert blocked.status_code == 403 and _code(blocked) == "password_change_required"

    # Any non-empty password that is not the default will do - even one letter.
    assert world.change(temporary, DEFAULT).status_code == 422
    assert world.change(temporary, "a").status_code == 204
    row = await world.user(world.member)
    assert row.password_temporary is False and row.password_changed_at is not None
    assert world.client.get("/api/units/me").status_code == 200
    assert world.login("9003", "a").json() == {"must_change_password": False}
    assert world.login("9003", temporary).status_code == 401


async def test_19_one_reset_per_five_minutes(world: World) -> None:
    assert world.forgot("9003").status_code == 202
    first = await world.temporary_password(world.member)
    # Inside the window: the same answer, and nothing happens.
    assert world.forgot("9003").json() == RESET_BODY
    assert len(await world.outbox()) == 1
    assert world.login("9003", first).status_code == 200
    outcomes = [
        row.after_data["outcome"]
        for row in await world.audit()
        if row.action == "auth.password.reset_requested"
    ]
    assert sorted(outcomes) == ["rate_limited", "sent"]

    # After the window a new password replaces the first, whose unsent
    # message is cancelled rather than delivered.
    await world.set_user(world.member, password_reset_at=utcnow() - timedelta(minutes=6))
    assert world.forgot("9003").status_code == 202
    rows = await world.outbox()
    assert len(rows) == 2
    assert rows[0].status is OutboxStatus.CANCELLED
    assert rows[0].safe_payload_json["password"] == ""
    assert rows[1].status is OutboxStatus.PENDING
    second = await world.temporary_password(world.member)
    assert second != first
    assert world.login("9003", first).status_code == 401
    assert world.login("9003", second).json() == {"must_change_password": True}


async def test_20_the_message_and_what_never_keeps_the_password(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Alembic's ``fileConfig`` (run by the migration tests in the same process)
    # disables every logger that existed before it; the ones on this path must
    # be live, or "nothing was logged" would prove nothing.
    for name in (
        "meobot.application.account.password_reset_service",
        "meobot.application.account.password_service",
        "meobot.application.outbox_service",
        "meobot.application.delivery_service",
        "meobot.application.notification_router",
        "meobot.application.web_auth_service",
    ):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    with caplog.at_level(logging.DEBUG):
        assert world.forgot("9003").status_code == 202
        temporary = await world.temporary_password(world.member)

        # The worker renders it as HTML, to the member's own chat.
        [row] = await world.outbox()
        notifier = FakeNotifier()
        result = await TelegramDeliveryService(notifier).deliver(row)
        assert result.delivered
        assert notifier.sent == [
            (
                9003,
                f"Mật khẩu tạm TasksBot của bạn: <code>{temporary}</code>. "
                "Đăng nhập tại https://pr.example.com/login rồi đổi mật khẩu.",
            )
        ]
        assert notifier.parse_modes == ["HTML"]

        # Settled: the stored payload no longer carries it.
        async with world.factory() as session:
            outbox = OutboxService(session, world.settings)
            stored = await outbox.by_id(row.id)
            assert stored is not None
            await outbox.mark_delivered(stored)
            await session.commit()

    [delivered] = await world.outbox()
    assert delivered.status is OutboxStatus.DELIVERED
    assert delivered.safe_payload_json == {
        "password": "",
        "login_url": "https://pr.example.com/login",
    }
    # A hand-made retry of the settled row cannot resend a credential.
    replay = FakeNotifier()
    assert (await TelegramDeliveryService(replay).deliver(delivered)).delivered
    assert "<code>" not in replay.sent[0][1] and "hết hiệu lực" in replay.sent[0][1]

    logged = [record.getMessage() for record in caplog.records] + [
        str(value) for record in caplog.records for value in record.__dict__.values()
    ]
    assert any("web_password_reset_requested" in line for line in logged)
    assert not any(temporary in line for line in logged)

    async with world.factory() as db:
        dump: list[str] = []
        for table in ("users", "web_sessions", "audit_logs", "outbound_messages"):
            rows = (await db.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
            dump.extend(
                json.dumps([str(value) for value in item], ensure_ascii=False) for item in rows
            )
    assert temporary not in "\n".join(dump)
