"""Profile pictures (0047): upload, remove, serve.

No image library on the server. The browser crops and resizes to 256x256 and
exports WebP (JPEG where WebP encoding is unsupported); the server **checks**
what arrives and stores it as it is:

* the declared type is one of ``image/webp``, ``image/jpeg``, ``image/png``;
* ``data`` is strict base64 (no ``data:`` prefix, no whitespace). A string
  longer than 300 KB can encode is refused before it is decoded;
* the decoded bytes are 1 byte .. 300 KB (``avatar_too_large`` above that);
* the bytes start with the declared type's signature (RIFF....WEBP,
  FF D8 FF, 89 50 4E 47 0D 0A 1A 0A) - otherwise ``avatar_invalid_image``.

Anybody signed in may read anybody's picture: they appear next to names all
over the panel. Only the person themself changes or removes theirs. The audit
trail records type, size and version, never the bytes.

The URL carries the version (``/api/account/avatar/<user_id>?v=<version>``),
so the image can be cached as immutable: a new upload is a new URL.
"""

from __future__ import annotations

import binascii
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.db.models.user_avatar import AVATAR_CONTENT_TYPES, AVATAR_MAX_BYTES, UserAvatar
from meobot.domain.account.errors import (
    AccountNotFoundError,
    AvatarNotFoundError,
    AvatarRejectedError,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor

#: The longest base64 string that can decode to :data:`AVATAR_MAX_BYTES`.
#: Anything longer is refused without decoding it.
AVATAR_MAX_BASE64_CHARS = 4 * -(-AVATAR_MAX_BYTES // 3)

AVATAR_PATH = "/api/account/avatar"

TOO_LARGE_MESSAGE = "Ảnh đại diện quá lớn (tối đa 300 KB)."
INVALID_IMAGE_MESSAGE = "Ảnh không hợp lệ. Hãy chọn ảnh PNG, JPEG hoặc WebP khác."


def avatar_url(user_id: uuid.UUID, version: int) -> str:
    """Where the picture is served. The version busts every cache."""
    return f"{AVATAR_PATH}/{user_id}?v={version}"


def matches_signature(content_type: str, data: bytes) -> bool:
    """Whether ``data`` starts the way an image of ``content_type`` must."""
    if content_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if content_type == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if content_type == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    return False


def decode_avatar(content_type: str, data: str) -> tuple[str, bytes]:
    """Check an upload and return ``(content_type, image bytes)``.

    Raises:
        AvatarRejectedError: ``avatar_too_large`` or ``avatar_invalid_image``.
    """
    declared = content_type.strip().lower()
    if declared not in AVATAR_CONTENT_TYPES:
        raise AvatarRejectedError(
            "avatar_invalid_image", INVALID_IMAGE_MESSAGE, field="content_type"
        )
    if len(data) > AVATAR_MAX_BASE64_CHARS:
        raise AvatarRejectedError("avatar_too_large", TOO_LARGE_MESSAGE, field="data")
    try:
        # Strict: only the alphabet, correct padding, nothing after it.
        raw = binascii.a2b_base64(data.encode("ascii"), strict_mode=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise AvatarRejectedError(
            "avatar_invalid_image", INVALID_IMAGE_MESSAGE, field="data"
        ) from error
    if len(raw) > AVATAR_MAX_BYTES:
        raise AvatarRejectedError("avatar_too_large", TOO_LARGE_MESSAGE, field="data")
    if not raw or not matches_signature(declared, raw):
        raise AvatarRejectedError("avatar_invalid_image", INVALID_IMAGE_MESSAGE, field="data")
    return declared, raw


async def avatar_versions(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """``user_id -> version`` for those who have a picture. One query, no bytes."""
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(UserAvatar.user_id, UserAvatar.version).where(UserAvatar.user_id.in_(ids))
    )
    return dict(rows.tuples().all())


async def avatar_urls(session: AsyncSession, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """``user_id -> avatar URL`` for those who have a picture."""
    versions = await avatar_versions(session, user_ids)
    return {user_id: avatar_url(user_id, version) for user_id, version in versions.items()}


class AvatarService:
    """Upload, remove and read profile pictures."""

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    async def url_for(self, user_id: uuid.UUID | None) -> str | None:
        if user_id is None:
            return None
        return (await avatar_urls(self._session, [user_id])).get(user_id)

    async def upload(
        self, *, actor: Actor, request_id: uuid.UUID, content_type: str, data: str
    ) -> str:
        """Replace my picture. Returns its new URL (the version bumped)."""
        user_id = self._own_id(actor)
        declared, raw = decode_avatar(content_type, data)
        row = await self._session.scalar(
            select(UserAvatar).where(UserAvatar.user_id == user_id).with_for_update()
        )
        before: dict[str, object] | None = None
        if row is None:
            row = UserAvatar(
                user_id=user_id,
                content_type=declared,
                data=raw,
                size_bytes=len(raw),
                version=1,
            )
            self._session.add(row)
        else:
            before = {
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
                "version": row.version,
            }
            row.content_type = declared
            row.data = raw
            row.size_bytes = len(raw)
            row.version = row.version + 1
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_AVATAR_UPDATED,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user_id),
            before_data=before,
            after_data={
                "content_type": declared,
                "size_bytes": len(raw),
                "version": row.version,
            },
        )
        return avatar_url(user_id, row.version)

    async def remove(self, *, actor: Actor, request_id: uuid.UUID) -> bool:
        """Back to initials. Returns whether there was a picture to remove."""
        user_id = self._own_id(actor)
        row = await self._session.get(UserAvatar, user_id)
        if row is None:
            return False
        before = {
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "version": row.version,
        }
        await self._session.delete(row)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.USER_AVATAR_REMOVED,
            result=AuditResult.SUCCESS,
            entity_type="user",
            entity_id=str(user_id),
            before_data=before,
        )
        return True

    async def get(self, user_id: uuid.UUID) -> UserAvatar:
        """Anybody's picture, for any signed-in reader.

        Raises:
            AvatarNotFoundError: nobody, or no picture.
        """
        row = await self._session.get(UserAvatar, user_id)
        if row is None:
            raise AvatarNotFoundError()
        return row

    @staticmethod
    def _own_id(actor: Actor) -> uuid.UUID:
        if actor.user_id is None:
            raise AccountNotFoundError()
        return actor.user_id


__all__ = [
    "AVATAR_MAX_BASE64_CHARS",
    "AVATAR_PATH",
    "AvatarService",
    "avatar_url",
    "avatar_urls",
    "avatar_versions",
    "decode_avatar",
    "matches_signature",
]
