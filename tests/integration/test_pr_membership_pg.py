"""Thành viên & Phân quyền on PostgreSQL: the web routes over a real server.

The unit suite proves the rules on SQLite. This file proves what SQLite cannot:
that ``UserService._require``'s ``FOR UPDATE`` lock runs on a real server, that
the roster, the grouped grant count and the responsibilities counts (with
their ``NOT IN`` over string-stored enums) execute on PostgreSQL, and that a
deactivation is refused by the session on the next request while the row, the
grants and the audit trail stay.

No migration is under test: phase 1 added none, and the scratch database is
upgraded to the existing head.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_membership_pg.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.grants import GrantScope
from meobot.domain.pr.policy import PrCapability
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SETTINGS_KWARGS: dict[str, Any] = {"_env_file": None, "web_cookie_secure": False}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_members_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    handle = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))
    try:
        yield handle
    finally:
        await handle.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def http(database: Database) -> AsyncIterator[tuple[object, dict[str, uuid.UUID], dict]]:  # type: ignore[type-arg]
    """A real app over the scratch database, with committed people and one grant."""
    from httpx import ASGITransport, AsyncClient

    from meobot.api.deps import get_current_web_actor
    from meobot.api.main import create_app

    settings = Settings(
        database_url=database.engine.url.render_as_string(hide_password=False),
        web_base_url="https://pr.example.com",
        **SETTINGS_KWARGS,
    )
    async with database.session_factory() as session:
        stamp = uuid.uuid4().hex[:6]
        people = {
            "owner": User(full_name=f"Chị Chủ {stamp}", role=Role.OWNER),
            "admin": User(full_name=f"Quản trị {stamp}", role=Role.ADMIN),
            "member": User(full_name=f"Hảo {stamp}", role=Role.EMPLOYEE),
        }
        session.add_all(people.values())
        await session.flush()
        services = build_pr_services(session, settings)
        owner = Actor(user_id=people["owner"].id, full_name="Chị Chủ", role=Role.OWNER)
        await services.capabilities.grant(
            actor=owner,
            request_id=uuid.uuid4(),
            user_id=people["member"].id,
            capability=PrCapability.PR_TEAM_LEAD_REVIEW,
            scope=GrantScope.everything(),
        )
        await session.commit()
        ids = {name: person.id for name, person in people.items()}
        names = {name: person.full_name for name, person in people.items()}
        roles = {name: person.role for name, person in people.items()}

    app = create_app(settings)
    app.state.database = database
    current = {"name": "owner"}

    def actor() -> Actor:
        name = current["name"]
        return Actor(user_id=ids[name], full_name=names[name], role=roles[name], active=True)

    app.dependency_overrides[get_current_web_actor] = actor
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        yield client, ids, current
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_01_the_membership_routes_over_http(http, database: Database) -> None:  # type: ignore[no-untyped-def]
    client, ids, current = http
    member = str(ids["member"])

    async def row(user_id: str) -> dict[str, object]:
        async with database.session() as session:
            result = await session.execute(
                text(
                    "SELECT role, status, active, suspended_by_user_id, status_reason, "
                    "(SELECT count(*) FROM pr_user_capabilities c "
                    " WHERE c.user_id = u.id AND c.revoked_at IS NULL) AS grants, "
                    "(SELECT count(*) FROM audit_logs a WHERE a.entity_id = CAST(u.id AS text)) "
                    " AS audit_rows FROM users u WHERE id = :id"
                ),
                {"id": user_id},
            )
            return dict(result.mappings().one())

    # The roster, with the grouped grant count and the actor's flags.
    listed = await client.get("/api/pr/members")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["may_add"] and body["may_change_status"] and body["may_change_role"]
    by_id = {entry["user_id"]: entry for entry in body["members"]}
    assert by_id[member]["active_grant_count"] == 1
    assert by_id[member]["status"] == "active"

    # Effective permissions with provenance, from the real grant row.
    view = await client.get(f"/api/pr/members/{member}/effective-permissions")
    assert view.status_code == 200, view.text
    rows = {entry["capability"]: entry for entry in view.json()["capabilities"]}
    assert rows["PR_TEAM_LEAD_REVIEW"]["source"] == "SCOPED_GRANT"
    assert rows["PR_CONTENT_EDIT"]["source"] == "ROLE"
    assert rows["PR_CHANNEL_MANAGE"]["source"] == "NONE"

    # Responsibilities run their NOT IN over string-stored enums on PostgreSQL.
    held = await client.get(f"/api/pr/members/{member}/responsibilities")
    assert held.status_code == 200, held.text
    assert held.json()["active_grants"] == 1 and held.json()["total"] == 1

    # Add through the web: the same service as /add_user, committed by the request.
    added = await client.post(
        "/api/pr/members",
        json={"telegram_user_id": 900_001, "full_name": "Người Mới", "role": "TEAM_LEAD"},
    )
    assert added.status_code == 201, added.text
    new_id = added.json()["user_id"]
    assert (await row(new_id))["role"] == "TEAM_LEAD"
    again = await client.post(
        "/api/pr/members", json={"telegram_user_id": 900_001, "role": "EMPLOYEE"}
    )
    assert (
        again.status_code == 409
        and again.json()["error"]["details"]["reason"] == "member_already_registered"
    )

    # Role change under the row lock; the grant is untouched.
    changed = await client.post(f"/api/pr/members/{member}/role", json={"role": "TEAM_LEAD"})
    assert changed.status_code == 200, changed.text
    after = await row(member)
    assert after["role"] == "TEAM_LEAD" and after["grants"] == 1

    # Deactivate: status, active, provenance and reason are stored; grants stay.
    off = await client.post(f"/api/pr/members/{member}/deactivate", json={"reason": "Nghỉ phép"})
    assert off.status_code == 200, off.text
    after = await row(member)
    assert after["status"] == "suspended" and after["active"] is False
    assert after["suspended_by_user_id"] == ids["owner"] and after["status_reason"] == "Nghỉ phép"
    assert after["grants"] == 1

    # A deactivated member is refused by the real session dependency on the
    # next request: no override, a live session cookie, and `active` is false.
    from meobot.application.web_auth_service import (
        LOGIN_TOKEN_PARAM,
        SESSION_COOKIE,
        WebAuthService,
    )

    # ``issue_login_link`` refuses an inactive user, so: reactivate, mint a
    # session, deactivate again, then present the cookie.
    back = await client.post(f"/api/pr/members/{member}/reactivate")
    assert back.status_code == 200, back.text
    async with database.session_factory() as session:
        auth = WebAuthService(
            session,
            Settings(
                database_url=database.engine.url.render_as_string(hide_password=False),
                web_base_url="https://pr.example.com",
                **SETTINGS_KWARGS,
            ),
        )
        link = await auth.issue_login_link(user_id=ids["member"])
        token = link.url.split(f"{LOGIN_TOKEN_PARAM}=")[1]
        issued = await auth.redeem_login_token(token=token)
        await session.commit()
    off = await client.post(f"/api/pr/members/{member}/deactivate", json={})
    assert off.status_code == 200, off.text

    from meobot.api.deps import get_current_web_actor

    app = client._transport.app
    override = app.dependency_overrides.pop(get_current_web_actor)
    try:
        refused = await client.get("/api/pr/members", cookies={SESSION_COOKIE: issued.token})
        assert refused.status_code == 401, refused.text
    finally:
        app.dependency_overrides[get_current_web_actor] = override

    # The owner is protected as a target, whoever asks.
    current["name"] = "admin"
    for path in ("deactivate", "revoke"):
        refused = await client.post(f"/api/pr/members/{ids['owner']}/{path}", json={})
        assert refused.status_code == 403
        assert refused.json()["error"]["details"]["reason"] == "member_manage_forbidden"
    current["name"] = "owner"
    refused = await client.post(f"/api/pr/members/{ids['owner']}/role", json={"role": "ADMIN"})
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["reason"] == "owner_protected"

    # Revoked is terminal: enable is refused with its structured code, the
    # Telegram id cannot be registered again, and exactly one row remains.
    gone = await client.post(f"/api/pr/members/{new_id}/revoke", json={"reason": "Đã nghỉ"})
    assert gone.status_code == 200 and gone.json()["status_label"] == "Đã loại khỏi PR"
    back = await client.post(f"/api/pr/members/{new_id}/reactivate")
    assert back.status_code == 422
    assert back.json()["error"]["details"]["reason"] == "member_revoked"
    twice = await client.post(
        "/api/pr/members", json={"telegram_user_id": 900_001, "role": "EMPLOYEE"}
    )
    assert twice.status_code == 409
    assert twice.json()["error"]["details"]["reason"] == "member_revoked"
    assert "đã bị loại khỏi PR" in twice.json()["error"]["message"]
    assert (await client.post(f"/api/pr/members/{new_id}/restore")).status_code == 404
    async with database.session() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM users WHERE telegram_user_id = 900001")
        )
    assert count == 1
    assert (await row(new_id))["status"] == "revoked"

    # Every change above wrote an audit row for the member.
    assert int(str((await row(member))["audit_rows"])) >= 4
