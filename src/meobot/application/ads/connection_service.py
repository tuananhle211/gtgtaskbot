"""The "Kết nối BM" screen (0078): the OWNER's alone, like "Kết nối AI".

A Business Manager is added with its System User token, encrypted at once
(:mod:`meobot.core.secrets`, AAD ``ads_connection:<id>``) and never read back
to a browser - the screen shows ``••••abcd``; a token left blank on an edit
keeps the stored one. "Test kết nối" asks Meta for the BM's name and its ad
accounts and records how it went. A BM with accounts on record is switched
off rather than deleted, so its history stays.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.core.errors import (
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsAccount, AdsConnection
from meobot.domain.ai.models import key_hint
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.integrations.meta_ads.client import MetaAdsClient, MetaAdsError


class AdsConnectionForbiddenError(AuthorizationError):
    code = "ads_connection_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu mới quản lý kết nối BM.",
            details={"reason": "ads_connection_forbidden"},
        )


class AdsConnectionNotFoundError(NotFoundError):
    code = "ads_connection_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy BM.", details={"reason": "ads_connection_not_found"})


class AdsConnectionInUseError(ConflictError):
    code = "ads_connection_in_use"

    def __init__(self) -> None:
        super().__init__(
            "BM này đã có tài khoản quảng cáo và số liệu: hãy Tắt thay vì xoá.",
            details={"reason": "ads_connection_in_use"},
        )


class AdsConnectionValidationError(ValidationError):
    code = "ads_connection_invalid"

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message, details={"reason": reason, **details})


@dataclass(frozen=True, slots=True)
class ConnectionTest:
    ok: bool
    message: str
    latency_ms: int
    account_count: int = 0


def token_aad(connection_id: uuid.UUID) -> str:
    """Binds an encrypted token to its row: pasted into another row it fails."""
    return f"ads_connection:{connection_id}"


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _snap(row: AdsConnection) -> dict[str, object]:
    """For the audit log: never the token, not even encrypted."""
    return {
        "name": row.name,
        "business_id": row.business_id,
        "site": row.site,
        "token_hint": row.token_hint,
        "active": row.active,
    }


class AdsConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        settings: Settings,
        *,
        client: MetaAdsClient,
        secret_box: Callable[[], SecretBox] | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._settings = settings
        self._client = client
        self._secret_box = secret_box or (lambda: build_secret_box(settings))

    @staticmethod
    def _require_owner(actor: Actor) -> None:
        if actor.role is not Role.OWNER:
            raise AdsConnectionForbiddenError()

    def secret_ready(self) -> bool:
        try:
            self._secret_box()
        except ConfigurationError:
            return False
        return True

    # --- reading ------------------------------------------------------------

    async def connections(self, *, actor: Actor) -> list[AdsConnection]:
        self._require_owner(actor)
        return list(
            await self._session.scalars(
                select(AdsConnection).order_by(AdsConnection.site, AdsConnection.name)
            )
        )

    async def connection(self, connection_id: uuid.UUID) -> AdsConnection:
        row = await self._session.get(AdsConnection, connection_id)
        if row is None:
            raise AdsConnectionNotFoundError()
        return row

    def token(self, row: AdsConnection) -> str:
        """The plain token, for a sync or a test. Never leaves the server."""
        try:
            return self._secret_box().decrypt(row.token_encrypted, aad=token_aad(row.id))
        except ConfigurationError as error:
            raise AdsConnectionValidationError(
                "ads_secret_not_configured",
                "Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên không đọc được token.",
            ) from error

    # --- writing ------------------------------------------------------------

    def _encrypt(self, connection_id: uuid.UUID, token: str) -> str:
        try:
            box = self._secret_box()
        except ConfigurationError as error:
            raise AdsConnectionValidationError(
                "ads_secret_not_configured",
                "Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được token.",
            ) from error
        return box.encrypt(token, aad=token_aad(connection_id))

    @staticmethod
    def _token(value: str | None) -> str:
        token = _clean(value)
        if not token:
            raise AdsConnectionValidationError("ads_token_missing", "Nhập token.", field="token")
        if len(token) > 1000 or any(char.isspace() for char in token):
            raise AdsConnectionValidationError(
                "ads_token_invalid", "Token không hợp lệ.", field="token"
            )
        return token

    @staticmethod
    def _name(value: str | None) -> str:
        name = _clean(value)
        if not name:
            raise AdsConnectionValidationError("ads_name_missing", "Đặt tên cho BM.", field="name")
        return name[:80]

    async def _business_id(self, value: str | None, *, exclude: uuid.UUID | None = None) -> str:
        business_id = _clean(value)
        if not business_id.isdigit() or len(business_id) > 40:
            raise AdsConnectionValidationError(
                "ads_business_id_invalid",
                "Business ID là một dãy số (xem trong Business Settings của BM).",
                field="business_id",
            )
        query = select(AdsConnection.id).where(AdsConnection.business_id == business_id)
        if exclude is not None:
            query = query.where(AdsConnection.id != exclude)
        taken = await self._session.scalar(query)
        if taken is not None:
            raise AdsConnectionValidationError(
                "ads_business_id_taken", "BM này đã được thêm.", field="business_id"
            )
        return business_id

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        name: str,
        business_id: str,
        site: str,
        token: str,
        note: str | None = None,
    ) -> AdsConnection:
        self._require_owner(actor)
        plain = self._token(token)
        row = AdsConnection(
            id=uuid.uuid4(),
            name=self._name(name),
            business_id=await self._business_id(business_id),
            site=_clean(site)[:60],
            note=_clean(note)[:300] or None,
            token_hint=key_hint(plain),
            created_by_user_id=actor.user_id,
        )
        row.token_encrypted = self._encrypt(row.id, plain)
        self._session.add(row)
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.ADS_CONNECTION_CREATED, row, None)
        return row

    async def update(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        connection_id: uuid.UUID,
        name: str | None = None,
        business_id: str | None = None,
        site: str | None = None,
        token: str | None = None,
        note: str | None = None,
        active: bool | None = None,
    ) -> AdsConnection:
        self._require_owner(actor)
        row = await self.connection(connection_id)
        before = _snap(row)
        if name is not None:
            row.name = self._name(name)
        if business_id is not None and _clean(business_id) != row.business_id:
            row.business_id = await self._business_id(business_id, exclude=row.id)
            row.last_test_ok = None
        if site is not None:
            row.site = _clean(site)[:60]
        if note is not None:
            row.note = _clean(note)[:300] or None
        if _clean(token):
            plain = self._token(token)
            row.token_encrypted = self._encrypt(row.id, plain)
            row.token_hint = key_hint(plain)
            # A new token has not been tested.
            row.last_tested_at = None
            row.last_test_ok = None
            row.last_test_message = None
        if active is not None:
            row.active = active
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.ADS_CONNECTION_UPDATED, row, before)
        return row

    async def delete(
        self, *, actor: Actor, request_id: uuid.UUID, connection_id: uuid.UUID
    ) -> None:
        self._require_owner(actor)
        row = await self.connection(connection_id)
        used = await self._session.scalar(
            select(func.count()).select_from(AdsAccount).where(AdsAccount.connection_id == row.id)
        )
        if used:
            raise AdsConnectionInUseError()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_CONNECTION_DELETED.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_connection",
            entity_id=str(row.id),
            before_data=_snap(row),
        )
        await self._session.delete(row)
        await self._session.flush()

    # --- the test -----------------------------------------------------------

    async def test(
        self, *, actor: Actor, request_id: uuid.UUID, connection_id: uuid.UUID
    ) -> ConnectionTest:
        self._require_owner(actor)
        row = await self.connection(connection_id)
        token = self.token(row)
        started = time.monotonic()
        try:
            meta_name = await self._client.business_name(token, row.business_id)
            accounts = await self._client.ad_accounts(token, row.business_id)
            outcome = ConnectionTest(
                ok=True,
                message=f"Kết nối được · BM {meta_name} · {len(accounts)} tài khoản quảng cáo.",
                latency_ms=int((time.monotonic() - started) * 1000),
                account_count=len(accounts),
            )
            row.meta_name = meta_name[:200]
            row.account_count = len(accounts)
        except MetaAdsError as error:
            outcome = ConnectionTest(
                ok=False,
                message=error.message[:300],
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        row.last_tested_at = utcnow()
        row.last_test_ok = outcome.ok
        row.last_test_message = outcome.message
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ADS_CONNECTION_TESTED.value,
            result=AuditResult.SUCCESS if outcome.ok else AuditResult.FAILED,
            entity_type="ads_connection",
            entity_id=str(row.id),
            after_data={"ok": outcome.ok, "latency_ms": outcome.latency_ms},
        )
        return outcome

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        row: AdsConnection,
        before: dict[str, object] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="ads_connection",
            entity_id=str(row.id),
            before_data=before,
            after_data=_snap(row),
        )


__all__ = [
    "AdsConnectionForbiddenError",
    "AdsConnectionInUseError",
    "AdsConnectionNotFoundError",
    "AdsConnectionService",
    "AdsConnectionValidationError",
    "ConnectionTest",
    "token_aad",
]
