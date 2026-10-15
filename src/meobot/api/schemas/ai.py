"""Wire shapes for "Kết nối AI" (0064). No key ever leaves the server: a
connection carries ``api_key_hint`` (``••••abcd``) only."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meobot.db.models.ai_connection import AiConnection
from meobot.domain.ai.models import (
    MAX_CHAIN,
    PROVIDER_KINDS,
    PURPOSE_LABELS,
    AiProviderKind,
    AiPurpose,
)


class AiConnectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    kind_label: str
    #: As stored; null = the kind's own endpoint (``endpoint`` says which).
    base_url: str | None
    endpoint: str
    model: str
    api_key_hint: str
    active: bool
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None

    @classmethod
    def from_row(cls, row: AiConnection) -> AiConnectionResponse:
        info = PROVIDER_KINDS[row.kind]
        return cls(
            id=row.id,
            name=row.name,
            kind=row.kind.value,
            kind_label=info.label,
            base_url=row.base_url,
            endpoint=row.base_url or info.base_url or "",
            model=row.model,
            api_key_hint=row.api_key_hint,
            active=row.active,
            last_tested_at=row.last_tested_at,
            last_test_ok=row.last_test_ok,
            last_test_message=row.last_test_message,
        )


class AiKindResponse(BaseModel):
    kind: str
    label: str
    base_url: str | None
    model_hint: str
    key_hint: str


class AiPurposeResponse(BaseModel):
    purpose: str
    label: str
    #: The connections tried, in order; [] = this purpose uses the default chain.
    connection_ids: list[uuid.UUID]


class AiOverviewResponse(BaseModel):
    connections: list[AiConnectionResponse]
    routes: list[AiPurposeResponse]
    kinds: list[AiKindResponse]
    max_chain: int = MAX_CHAIN
    #: False: the server has no PR_SECRET_ENCRYPTION_KEY, keys cannot be stored.
    secret_ready: bool
    #: What answers when no connection does: the environment's ``LLM_*``.
    env_provider: str
    env_model: str | None

    @staticmethod
    def kind_list() -> list[AiKindResponse]:
        return [
            AiKindResponse(
                kind=kind.value,
                label=info.label,
                base_url=info.base_url,
                model_hint=info.model_hint,
                key_hint=info.key_hint,
            )
            for kind, info in PROVIDER_KINDS.items()
        ]

    @staticmethod
    def routes_of(stored: dict[AiPurpose, list[uuid.UUID]]) -> list[AiPurposeResponse]:
        return [
            AiPurposeResponse(
                purpose=purpose.value,
                label=PURPOSE_LABELS[purpose],
                connection_ids=stored.get(purpose, []),
            )
            for purpose in AiPurpose
        ]


class CreateAiConnectionRequest(BaseModel):
    name: str = Field(max_length=80)
    kind: AiProviderKind
    model: str = Field(max_length=200)
    api_key: str = Field(max_length=600)
    base_url: str | None = Field(default=None, max_length=400)


class UpdateAiConnectionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    #: Null clears it (the kind's own endpoint).
    base_url: str | None = Field(default=None, max_length=400)
    #: Blank / absent keeps the stored key.
    api_key: str | None = Field(default=None, max_length=600)
    active: bool | None = None


class AiRoutesRequest(BaseModel):
    #: Purpose -> connection ids in order. Purposes left out are kept.
    routes: dict[AiPurpose, list[uuid.UUID]]


class AiTestResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int
    connection: AiConnectionResponse


__all__ = [
    "AiConnectionResponse",
    "AiOverviewResponse",
    "AiRoutesRequest",
    "AiTestResponse",
    "CreateAiConnectionRequest",
    "UpdateAiConnectionRequest",
]
