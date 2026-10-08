"""Profile pictures (0047): ``PUT/DELETE /api/account/avatar`` and
``GET /api/account/avatar/{user_id}``.

Runs the real app with real per-request transactions on a file SQLite
database, like ``test_password_login.py``.

* an upload is checked strictly - declared type, strict base64, 300 KB decoded
  (an over-long string refused before it is decoded), magic bytes - and bumps
  the version, which changes the URL;
* the image comes back as raw bytes with cache, ``nosniff`` and ``inline``
  headers, for anybody signed in and nobody else;
* ``avatar_url`` appears on the session, the account card and member rows;
* a session that must change its password may read pictures, not change its own;
* the audit trail never holds the image.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from meobot.api.deps import get_session, password_change_allows
from meobot.api.main import create_app
from meobot.application.account.avatar_service import (
    AVATAR_MAX_BASE64_CHARS,
    avatar_url,
    matches_signature,
)
from meobot.application.web_auth_service import WebAuthService
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.user import User
from meobot.db.models.user_avatar import AVATAR_MAX_BYTES, UserAvatar
from meobot.domain.identity.models import Role

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + bytes(range(64))
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(64))
WEBP = b"RIFF" + (76).to_bytes(4, "little") + b"WEBPVP8 " + bytes(range(64))
GIF = b"GIF89a" + bytes(range(64))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    client: TestClient
    settings: Settings
    owner: uuid.UUID
    member: uuid.UUID
    other: uuid.UUID

    async def sign_in(self, user_id: uuid.UUID) -> None:
        """A Telegram-link session (never gated) for ``user_id``."""
        async with self.factory() as session:
            issued = await WebAuthService(session, self.settings).issue_login_link(user_id=user_id)
            await session.commit()
        token = issued.url.split("t=")[1]
        self.client.cookies.clear()
        response = self.client.get(f"/auth/login?t={token}", follow_redirects=False)
        assert response.status_code == 303, response.text

    def upload(self, content_type: str, data: str) -> Response:
        return self.client.put(
            "/api/account/avatar", json={"content_type": content_type, "data": data}
        )

    async def audit(self, action: str) -> list[AuditLog]:
        async with self.factory() as session:
            rows = await session.scalars(
                select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
            )
            return list(rows.all())

    async def avatar(self, user_id: uuid.UUID) -> UserAvatar | None:
        async with self.factory() as session:
            return await session.get(UserAvatar, user_id)


@pytest_asyncio.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    database = tmp_path / "avatars.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}", poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        owner = User(full_name="Chủ", role=Role.OWNER, telegram_user_id=8001)
        member = User(full_name="Hảo", role=Role.EMPLOYEE, telegram_user_id=8002)
        other = User(full_name="Lan", role=Role.EMPLOYEE, telegram_user_id=8003)
        session.add_all([owner, member, other])
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
            member=member.id,
            other=other.id,
        )
    app.dependency_overrides.clear()
    await engine.dispose()


def _error(response: Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    error: dict[str, Any] = body["error"]
    return error


# --- the checks, without HTTP ---------------------------------------------------------


def test_01_signatures_and_limits() -> None:
    assert matches_signature("image/png", PNG)
    assert matches_signature("image/jpeg", JPEG)
    assert matches_signature("image/webp", WEBP)
    assert not matches_signature("image/png", JPEG)
    assert not matches_signature("image/jpeg", WEBP)
    assert not matches_signature("image/webp", b"RIFF\x00\x00\x00\x00WAVEfmt ")
    assert not matches_signature("image/webp", b"RIFF")
    assert not matches_signature("image/gif", GIF)
    assert AVATAR_MAX_BYTES == 300 * 1024
    # The longest base64 of 300 KB, and not one character more.
    assert AVATAR_MAX_BASE64_CHARS == len(b64(b"\x00" * AVATAR_MAX_BYTES)) == 409_600
    uid = uuid.uuid4()
    assert avatar_url(uid, 3) == f"/api/account/avatar/{uid}?v=3"


# --- upload, version, URL -------------------------------------------------------------


async def test_02_upload_bumps_the_version_and_the_url(world: World) -> None:
    await world.sign_in(world.member)
    first = world.upload("image/webp", b64(WEBP))
    assert first.status_code == 200, first.text
    assert first.json() == {"avatar_url": f"/api/account/avatar/{world.member}?v=1"}

    image = world.client.get(first.json()["avatar_url"])
    assert image.status_code == 200
    assert image.content == WEBP
    assert image.headers["content-type"] == "image/webp"

    second = world.upload("image/jpeg", b64(JPEG))
    assert second.status_code == 200, second.text
    assert second.json() == {"avatar_url": f"/api/account/avatar/{world.member}?v=2"}
    assert second.json()["avatar_url"] != first.json()["avatar_url"]
    image = world.client.get(second.json()["avatar_url"])
    assert image.content == JPEG
    assert image.headers["content-type"] == "image/jpeg"

    stored = await world.avatar(world.member)
    assert stored is not None
    assert (stored.content_type, stored.size_bytes, stored.version) == (
        "image/jpeg",
        len(JPEG),
        2,
    )
    # Surrounding spaces / case in the declared type are forgiven.
    third = world.upload(" IMAGE/PNG ", b64(PNG))
    assert third.status_code == 200, third.text
    assert third.json()["avatar_url"].endswith("?v=3")


async def test_03_the_audit_never_holds_the_image(world: World) -> None:
    await world.sign_in(world.member)
    assert world.upload("image/png", b64(PNG)).status_code == 200
    assert world.upload("image/webp", b64(WEBP)).status_code == 200
    assert world.client.delete("/api/account/avatar").status_code == 204

    updated = await world.audit("user.avatar.updated")
    removed = await world.audit("user.avatar.removed")
    assert len(updated) == 2 and len(removed) == 1
    assert updated[0].after_data == {
        "content_type": "image/png",
        "size_bytes": len(PNG),
        "version": 1,
    }
    assert updated[1].before_data == {
        "content_type": "image/png",
        "size_bytes": len(PNG),
        "version": 1,
    }
    assert updated[1].after_data == {
        "content_type": "image/webp",
        "size_bytes": len(WEBP),
        "version": 2,
    }
    assert removed[0].before_data == {
        "content_type": "image/webp",
        "size_bytes": len(WEBP),
        "version": 2,
    }
    for row in [*updated, *removed]:
        assert row.entity_id == str(world.member)
        dumped = json.dumps([row.before_data, row.after_data])
        for image in (PNG, WEBP):
            assert b64(image) not in dumped
            assert b64(image)[:16] not in dumped


# --- refusals ---------------------------------------------------------------------------


async def test_04_size_limits(world: World) -> None:
    await world.sign_in(world.member)
    exactly = PNG + b"\x00" * (AVATAR_MAX_BYTES - len(PNG))
    ok = world.upload("image/png", b64(exactly))
    assert ok.status_code == 200, ok.text[:200]

    over = exactly + b"\x00"
    refused = world.upload("image/png", b64(over))
    assert refused.status_code == 422
    assert _error(refused)["code"] == "avatar_too_large"
    assert _error(refused)["details"] == {"reason": "avatar_too_large", "field": "data"}
    # The body does not echo the upload back.
    assert len(refused.content) < 1000

    # A string too long to be 300 KB is refused before decoding - even garbage.
    early = world.upload("image/png", "!" * (AVATAR_MAX_BASE64_CHARS + 1))
    assert early.status_code == 422
    assert _error(early)["code"] == "avatar_too_large"

    stored = await world.avatar(world.member)
    assert stored is not None and stored.version == 1 and stored.size_bytes == AVATAR_MAX_BYTES


async def test_05_type_and_magic_bytes(world: World) -> None:
    await world.sign_in(world.member)
    cases = [
        ("image/gif", b64(GIF), "content_type"),
        ("image/svg+xml", b64(b"<svg xmlns='http://www.w3.org/2000/svg'/>"), "content_type"),
        ("text/html", b64(b"<script>alert(1)</script>"), "content_type"),
        ("", b64(PNG), "content_type"),
        ("image/png", b64(JPEG), "data"),
        ("image/jpeg", b64(WEBP), "data"),
        ("image/webp", b64(PNG), "data"),
        ("image/webp", b64(b"RIFF\x10\x00\x00\x00WAVEfmt "), "data"),
        ("image/png", b64(b"<svg onload=alert(1)>"), "data"),
        ("image/png", "", "data"),
    ]
    for content_type, data, field in cases:
        response = world.upload(content_type, data)
        assert response.status_code == 422, (content_type, response.text)
        assert _error(response)["code"] == "avatar_invalid_image", content_type
        assert _error(response)["details"] == {"reason": "avatar_invalid_image", "field": field}
    assert await world.avatar(world.member) is None
    assert await world.audit("user.avatar.updated") == []


async def test_06_garbage_base64(world: World) -> None:
    await world.sign_in(world.member)
    encoded = b64(PNG)
    for data in (
        "not base64 at all!!",
        f"data:image/png;base64,{encoded}",
        encoded[:40] + "\n" + encoded[40:],
        encoded[:40] + " " + encoded[40:],
        encoded + "A",  # data after the padding
        encoded[:-1],  # padding missing
        "=" + encoded[1:],  # leading padding
        "ảnh đại diện",
        "abc",
    ):
        response = world.upload("image/png", data)
        assert response.status_code == 422, (data[:30], response.text)
        assert _error(response)["code"] == "avatar_invalid_image"
    # Missing fields are a plain request-validation 422.
    assert world.client.put("/api/account/avatar", json={"data": encoded}).status_code == 422
    assert await world.avatar(world.member) is None


# --- remove, read ----------------------------------------------------------------------------


async def test_07_delete_goes_back_to_initials(world: World) -> None:
    await world.sign_in(world.member)
    url = world.upload("image/png", b64(PNG)).json()["avatar_url"]
    deleted = world.client.delete("/api/account/avatar")
    assert deleted.status_code == 204 and deleted.content == b""
    assert await world.avatar(world.member) is None

    gone = world.client.get(url)
    assert gone.status_code == 404
    assert _error(gone)["code"] == "avatar_not_found"
    assert world.client.get("/api/account/me").json()["avatar_url"] is None

    # Idempotent, and only a real removal is audited.
    assert world.client.delete("/api/account/avatar").status_code == 204
    assert len(await world.audit("user.avatar.removed")) == 1

    # Uploading again starts a fresh row.
    again = world.upload("image/png", b64(PNG))
    assert again.json()["avatar_url"].endswith("?v=1")


async def test_08_get_bytes_and_headers(world: World) -> None:
    await world.sign_in(world.member)
    url = world.upload("image/webp", b64(WEBP)).json()["avatar_url"]
    image = world.client.get(url)
    assert image.status_code == 200
    assert image.content == WEBP
    assert image.headers["content-type"] == "image/webp"
    assert image.headers["content-length"] == str(len(WEBP))
    assert image.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["content-disposition"] == "inline"
    assert image.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert image.headers["cross-origin-resource-policy"] == "same-origin"
    assert "x-request-id" in image.headers
    # Not wrapped in any JSON envelope.
    assert not image.content.startswith(b"{")

    # Without ``v`` it is still the picture; with a stale ``v`` it is the
    # current picture but must not be cached as immutable under that URL.
    plain = world.client.get(f"/api/account/avatar/{world.member}")
    assert plain.content == WEBP
    stale = world.client.get(f"/api/account/avatar/{world.member}?v=99")
    assert stale.status_code == 200 and stale.content == WEBP
    assert stale.headers["cache-control"] == "private, no-cache"


async def test_09_get_404s(world: World) -> None:
    await world.sign_in(world.member)
    for target in (world.other, uuid.uuid4()):
        response = world.client.get(f"/api/account/avatar/{target}?v=1")
        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "avatar_not_found",
                "message": "Chưa có ảnh đại diện.",
                "details": {"reason": "avatar_not_found"},
            }
        }
    assert world.client.get("/api/account/avatar/not-a-uuid").status_code == 422


async def test_10_any_signed_in_user_can_read_any_avatar(world: World) -> None:
    await world.sign_in(world.owner)
    url = world.upload("image/png", b64(PNG)).json()["avatar_url"]
    for reader in (world.member, world.other):
        await world.sign_in(reader)
        response = world.client.get(url)
        assert response.status_code == 200
        assert response.content == PNG
    # Writes only ever touch one's own row.
    assert world.upload("image/jpeg", b64(JPEG)).status_code == 200
    owner_row = await world.avatar(world.owner)
    assert owner_row is not None and owner_row.content_type == "image/png"


async def test_11_unauthenticated_requests_are_refused(world: World) -> None:
    await world.sign_in(world.member)
    url = world.upload("image/png", b64(PNG)).json()["avatar_url"]
    world.client.cookies.clear()
    response = world.client.get(url)
    assert response.status_code == 401
    assert PNG not in response.content
    assert world.upload("image/png", b64(PNG)).status_code == 401
    assert world.client.delete("/api/account/avatar").status_code == 401


# --- avatar_url elsewhere -----------------------------------------------------------------------


async def test_12_avatar_url_on_session_me_and_members(world: World) -> None:
    await world.sign_in(world.owner)
    assert world.client.get("/api/auth/session").json()["avatar_url"] is None
    assert world.client.get("/api/account/me").json()["avatar_url"] is None

    await world.sign_in(world.member)
    member_url = world.upload("image/png", b64(PNG)).json()["avatar_url"]
    member_url = world.upload("image/png", b64(PNG)).json()["avatar_url"]
    assert member_url == f"/api/account/avatar/{world.member}?v=2"
    assert world.client.get("/api/auth/session").json()["avatar_url"] == member_url
    assert world.client.get("/api/account/me").json()["avatar_url"] == member_url
    renamed = world.client.patch("/api/account/profile", json={"full_name": "Hảo Mới"})
    assert renamed.status_code == 200 and renamed.json()["avatar_url"] == member_url

    await world.sign_in(world.owner)
    owner_url = world.upload("image/webp", b64(WEBP)).json()["avatar_url"]
    assert world.client.get("/api/auth/session").json()["avatar_url"] == owner_url
    roster = world.client.get("/api/account/members")
    assert roster.status_code == 200, roster.text
    by_id = {row["user_id"]: row["avatar_url"] for row in roster.json()["members"]}
    assert by_id == {
        str(world.owner): owner_url,
        str(world.member): member_url,
        str(world.other): None,
    }


# --- the forced password change ----------------------------------------------------------------


def test_13_the_gate_lets_only_the_avatar_read_through() -> None:
    uid = uuid.uuid4()
    assert password_change_allows(f"/api/account/avatar/{uid}")
    assert not password_change_allows("/api/account/avatar")
    assert not password_change_allows("/api/account/avatar/")
    assert not password_change_allows(f"/api/account/avatar/{uid}/x")
    assert not password_change_allows("/api/account/avatars")
    assert not password_change_allows("/api/account/members")


async def test_14_a_must_change_session_can_read_but_not_change(world: World) -> None:
    await world.sign_in(world.other)
    other_url = world.upload("image/png", b64(PNG)).json()["avatar_url"]

    world.client.cookies.clear()
    login = world.client.post(
        "/api/auth/password-login",
        json={
            "username": "8002",
            "password": world.settings.web_default_password.get_secret_value(),
        },
    )
    assert login.status_code == 200, login.text
    assert login.json() == {"must_change_password": True}

    assert world.client.get(other_url).status_code == 200
    assert world.client.get(other_url).content == PNG
    me = world.client.get("/api/account/me")
    assert me.status_code == 200 and me.json()["avatar_url"] is None

    refused = world.upload("image/png", b64(PNG))
    assert refused.status_code == 403
    assert _error(refused)["code"] == "password_change_required"
    deleted = world.client.delete("/api/account/avatar")
    assert deleted.status_code == 403
    assert _error(deleted)["code"] == "password_change_required"
    assert await world.avatar(world.member) is None
