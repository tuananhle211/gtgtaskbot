"""The "Kết nối AI" screen (0064): the OWNER's alone.

Connections are created with their API key, which is encrypted at once
(:mod:`meobot.core.secrets`) and never read back to a browser - the screen
shows ``••••abcd``. A key left blank on an edit keeps the stored one.
"Kiểm tra kết nối" sends one tiny structured request through the same client
the app uses and records how it went. Routes say, per purpose, which
connections answer and in what order.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ai.routing import connection_provider, key_aad
from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.core.errors import (
    AuthorizationError,
    ConfigurationError,
    LLMError,
    NotFoundError,
    ValidationError,
)
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.core.time import utcnow
from meobot.db.models.ai_connection import AiConnection, AiRoute
from meobot.domain.ai.models import (
    MAX_CHAIN,
    PROVIDER_KINDS,
    AiProviderKind,
    AiPurpose,
    key_hint,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.integrations.llm.base import LLMProvider, StructuredRequest
from meobot.integrations.llm.diagnostics import ErrorCategory

#: Cut-off for the connection test; a working endpoint answers well within it.
TEST_TIMEOUT_SECONDS = 45.0

_TEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}

_FAILURE_WORDS: dict[str, str] = {
    ErrorCategory.AUTH: "API key bị từ chối (sai key hoặc key đã bị thu hồi).",
    ErrorCategory.RATE_LIMIT: (
        "Bị giới hạn: hết hạn mức / hết credit, hoặc nhà cung cấp đang quá tải (HTTP 429/5xx)."
    ),
    ErrorCategory.MODEL_OR_ENDPOINT_NOT_FOUND: "Không tìm thấy model hoặc địa chỉ API.",
}


class AiForbiddenError(AuthorizationError):
    code = "ai_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu mới quản lý kết nối AI.", details={"reason": "ai_forbidden"}
        )


class AiNotFoundError(NotFoundError):
    code = "ai_connection_not_found"

    def __init__(self) -> None:
        super().__init__(
            "Không tìm thấy kết nối AI.", details={"reason": "ai_connection_not_found"}
        )


class AiValidationError(ValidationError):
    code = "ai_validation_error"

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message, details={"reason": reason, **details})


@dataclass(frozen=True, slots=True)
class TestOutcome:
    ok: bool
    message: str
    latency_ms: int


ProviderFactory = Callable[..., LLMProvider]


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _base_url(kind: AiProviderKind, value: str | None) -> str | None:
    url = _clean(value).rstrip("/")
    if not url:
        if PROVIDER_KINDS[kind].base_url is None:
            raise AiValidationError(
                "ai_base_url_required",
                "Loại kết nối này cần địa chỉ API (ví dụ http://9router:20128/v1).",
                field="base_url",
            )
        return None
    if not url.startswith(("https://", "http://")) or len(url) > 300 or " " in url:
        raise AiValidationError(
            "ai_base_url_invalid",
            "Địa chỉ API phải bắt đầu bằng http:// hoặc https://.",
            field="base_url",
        )
    return url


def _snap(row: AiConnection) -> dict[str, object]:
    """For the audit log: never the key, not even encrypted."""
    return {
        "name": row.name,
        "kind": row.kind.value,
        "base_url": row.base_url,
        "model": row.model,
        "api_key_hint": row.api_key_hint,
        "active": row.active,
    }


class AiConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        settings: Settings,
        *,
        secret_box: Callable[[], SecretBox] | None = None,
        provider_factory: ProviderFactory = connection_provider,
    ) -> None:
        self._session = session
        self._audit = audit
        self._settings = settings
        self._secret_box = secret_box or (lambda: build_secret_box(settings))
        self._provider_factory = provider_factory

    # --- reading ----------------------------------------------------------------

    @staticmethod
    def _require_owner(actor: Actor) -> None:
        if actor.role is not Role.OWNER:
            raise AiForbiddenError()

    def secret_ready(self) -> bool:
        try:
            self._secret_box()
        except ConfigurationError:
            return False
        return True

    async def connections(self, *, actor: Actor) -> list[AiConnection]:
        self._require_owner(actor)
        return list(
            (
                await self._session.scalars(
                    select(AiConnection).order_by(AiConnection.created_at, AiConnection.name)
                )
            ).all()
        )

    async def routes(self, *, actor: Actor) -> dict[AiPurpose, list[uuid.UUID]]:
        self._require_owner(actor)
        return await self._routes()

    async def _routes(self) -> dict[AiPurpose, list[uuid.UUID]]:
        stored: dict[AiPurpose, list[uuid.UUID]] = {purpose: [] for purpose in AiPurpose}
        for row in (await self._session.scalars(select(AiRoute))).all():
            ids: list[uuid.UUID] = []
            for raw in row.connection_ids or []:
                try:
                    ids.append(uuid.UUID(str(raw)))
                except ValueError:
                    continue
            stored[row.purpose] = ids
        return stored

    async def connection(self, connection_id: uuid.UUID) -> AiConnection:
        row = await self._session.get(AiConnection, connection_id)
        if row is None:
            raise AiNotFoundError()
        return row

    # --- writing ----------------------------------------------------------------

    def _encrypt(self, connection_id: uuid.UUID, api_key: str) -> str:
        try:
            box = self._secret_box()
        except ConfigurationError as error:
            raise AiValidationError(
                "ai_secret_not_configured",
                "Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được API key.",
            ) from error
        return box.encrypt(api_key, aad=key_aad(connection_id))

    @staticmethod
    def _api_key(value: str | None) -> str:
        key = _clean(value)
        if not key:
            raise AiValidationError("ai_key_missing", "Nhập API key.", field="api_key")
        if len(key) > 500 or any(character.isspace() for character in key):
            raise AiValidationError("ai_key_invalid", "API key không hợp lệ.", field="api_key")
        return key

    @staticmethod
    def _name(value: str | None) -> str:
        name = _clean(value)
        if not name:
            raise AiValidationError("ai_name_missing", "Đặt tên cho kết nối.", field="name")
        return name[:60]

    @staticmethod
    def _model(value: str | None) -> str:
        model = _clean(value)
        if not model:
            raise AiValidationError("ai_model_missing", "Nhập tên model.", field="model")
        if len(model) > 120:
            raise AiValidationError("ai_model_invalid", "Tên model quá dài.", field="model")
        return model

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        name: str,
        kind: AiProviderKind,
        model: str,
        api_key: str,
        base_url: str | None = None,
    ) -> AiConnection:
        self._require_owner(actor)
        key = self._api_key(api_key)
        row = AiConnection(
            id=uuid.uuid4(),
            name=self._name(name),
            kind=kind,
            base_url=_base_url(kind, base_url),
            model=self._model(model),
            api_key_hint=key_hint(key),
            created_by_user_id=actor.user_id,
        )
        row.api_key_encrypted = self._encrypt(row.id, key)
        self._session.add(row)
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.AI_CONNECTION_CREATED, row, None)
        return row

    async def update(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        connection_id: uuid.UUID,
        name: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        clear_base_url: bool = False,
        api_key: str | None = None,
        active: bool | None = None,
    ) -> AiConnection:
        self._require_owner(actor)
        row = await self.connection(connection_id)
        before = _snap(row)
        if name is not None:
            row.name = self._name(name)
        if model is not None:
            row.model = self._model(model)
        if clear_base_url or base_url is not None:
            row.base_url = _base_url(row.kind, None if clear_base_url else base_url)
        if _clean(api_key):
            key = self._api_key(api_key)
            row.api_key_encrypted = self._encrypt(row.id, key)
            row.api_key_hint = key_hint(key)
            # A new key has not been tested.
            row.last_tested_at = None
            row.last_test_ok = None
            row.last_test_message = None
        if active is not None:
            row.active = active
        await self._session.flush()
        await self._record(actor, request_id, AuditAction.AI_CONNECTION_UPDATED, row, before)
        return row

    async def delete(
        self, *, actor: Actor, request_id: uuid.UUID, connection_id: uuid.UUID
    ) -> None:
        """Gone for good, key included, and out of every route."""
        self._require_owner(actor)
        row = await self.connection(connection_id)
        before = _snap(row)
        for route in (await self._session.scalars(select(AiRoute))).all():
            kept = [raw for raw in route.connection_ids or [] if str(raw) != str(row.id)]
            if kept != list(route.connection_ids or []):
                route.connection_ids = kept
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.AI_CONNECTION_DELETED.value,
            result=AuditResult.SUCCESS,
            entity_type="ai_connection",
            entity_id=str(row.id),
            before_data=before,
        )
        await self._session.delete(row)
        await self._session.flush()

    async def set_routes(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        routes: Mapping[AiPurpose, Sequence[uuid.UUID]],
    ) -> dict[AiPurpose, list[uuid.UUID]]:
        """Replace the chains of the purposes given (others are kept). An
        empty chain = that purpose uses the default one."""
        self._require_owner(actor)
        known = set((await self._session.scalars(select(AiConnection.id))).all())
        before: dict[str, object] = {
            purpose.value: [str(i) for i in ids] for purpose, ids in (await self._routes()).items()
        }
        for purpose, ids in routes.items():
            chain = list(dict.fromkeys(ids))
            if len(chain) != len(list(ids)):
                raise AiValidationError(
                    "ai_route_duplicate",
                    "Một kết nối chỉ xuất hiện một lần trong cùng một việc.",
                    purpose=purpose.value,
                )
            if len(chain) > MAX_CHAIN:
                raise AiValidationError(
                    "ai_route_too_long",
                    f"Mỗi việc dùng tối đa {MAX_CHAIN} kết nối (1 chính + dự phòng).",
                    purpose=purpose.value,
                )
            unknown = [str(item) for item in chain if item not in known]
            if unknown:
                raise AiValidationError(
                    "ai_route_unknown_connection",
                    "Có kết nối không tồn tại.",
                    purpose=purpose.value,
                )
            row = await self._session.get(AiRoute, purpose)
            if row is None:
                self._session.add(
                    AiRoute(purpose=purpose, connection_ids=[str(item) for item in chain])
                )
            else:
                row.connection_ids = [str(item) for item in chain]
        await self._session.flush()
        after = await self._routes()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.AI_ROUTES_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="ai_routes",
            entity_id="ai_routes",
            before_data=before,
            after_data={purpose.value: [str(i) for i in ids] for purpose, ids in after.items()},
        )
        return after

    # --- the test ---------------------------------------------------------------

    async def test(
        self, *, actor: Actor, request_id: uuid.UUID, connection_id: uuid.UUID
    ) -> TestOutcome:
        self._require_owner(actor)
        row = await self.connection(connection_id)
        try:
            api_key = self._secret_box().decrypt(row.api_key_encrypted, aad=key_aad(row.id))
        except ConfigurationError as error:
            raise AiValidationError(
                "ai_secret_not_configured",
                "Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên không đọc được API key.",
            ) from error
        provider = self._provider_factory(
            kind=row.kind,
            base_url=row.base_url,
            model=row.model,
            api_key=api_key,
            timeout_seconds=TEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
        started = time.monotonic()
        try:
            await provider.complete_structured(
                StructuredRequest(
                    task="ai_connection_test",
                    system_prompt="Bạn là bài kiểm tra kết nối. Chỉ trả về JSON.",
                    user_prompt='Trả về đúng {"ok": true}.',
                    json_schema=_TEST_SCHEMA,
                    schema_name="connection_test",
                    max_output_tokens=256,
                )
            )
            outcome = TestOutcome(
                ok=True,
                message="Kết nối được, AI đã trả lời.",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except LLMError as exc:
            category = str((exc.details or {}).get("category") or "")
            outcome = TestOutcome(
                ok=False,
                message=_FAILURE_WORDS.get(category, exc.message)[:300],
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            closer = getattr(provider, "aclose", None)
            if callable(closer):
                await closer()
        row.last_tested_at = utcnow()
        row.last_test_ok = outcome.ok
        row.last_test_message = outcome.message
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.AI_CONNECTION_TESTED.value,
            result=AuditResult.SUCCESS if outcome.ok else AuditResult.FAILED,
            entity_type="ai_connection",
            entity_id=str(row.id),
            after_data={"ok": outcome.ok, "latency_ms": outcome.latency_ms},
        )
        return outcome

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        row: AiConnection,
        before: dict[str, object] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="ai_connection",
            entity_id=str(row.id),
            before_data=before,
            after_data=_snap(row),
        )


__all__ = [
    "AiConnectionService",
    "AiForbiddenError",
    "AiNotFoundError",
    "AiValidationError",
    "TestOutcome",
]
