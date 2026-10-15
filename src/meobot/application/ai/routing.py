"""From the ``ai_connections`` / ``ai_routes`` rows to live providers.

:func:`build_ai_provider` is what the API, the bot and the worker hold
instead of the bare environment provider: a
:class:`~meobot.integrations.llm.routed.RoutedLLMProvider` whose loader reads
the rows here, decrypts the keys and builds one OpenAI-compatible client per
connection (kept while the row is unchanged, so what a client learned about
its endpoint - refused parameters, output modes - is not thrown away).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError
from meobot.core.logging import get_logger
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.db.models.ai_connection import AiConnection, AiRoute
from meobot.db.session import Database
from meobot.domain.ai.models import PROVIDER_KINDS, AiProviderKind, AiPurpose
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.llm.base import LLMProvider
from meobot.integrations.llm.factory import build_llm_provider
from meobot.integrations.llm.openai_compatible import OpenAICompatibleProvider
from meobot.integrations.llm.routed import RoutedLLMProvider, RouteTable

logger = get_logger(__name__)

#: A connection that fails should hand over quickly: one retry at most.
ROUTED_MAX_RETRIES = 1


def key_aad(connection_id: uuid.UUID) -> str:
    """Binds an encrypted key to its row: pasted into another row it fails."""
    return f"ai_connection:{connection_id}"


def endpoint_of(kind: AiProviderKind, base_url: str | None) -> str:
    return (base_url or PROVIDER_KINDS[kind].base_url or "").strip().rstrip("/")


def connection_provider(
    *,
    kind: AiProviderKind,
    base_url: str | None,
    model: str,
    api_key: str,
    timeout_seconds: float,
    max_retries: int = ROUTED_MAX_RETRIES,
) -> OpenAICompatibleProvider:
    """One AI service, through the OpenAI-compatible client."""
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=model,
        base_url=endpoint_of(kind, base_url),
        config=IntegrationConfig(
            provider=kind.value.lower(),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        ),
    )


class AiRoutingLoader:
    """Reads the routes in its own session; the result feeds the router."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings
        self._box: SecretBox | None = None
        self._clients: dict[uuid.UUID, tuple[datetime, OpenAICompatibleProvider]] = {}

    def _secret_box(self) -> SecretBox | None:
        if self._box is None:
            try:
                self._box = build_secret_box(self._settings)
            except ConfigurationError:
                # No key, or an unreadable one: the routes cannot be read.
                return None
        return self._box

    async def __call__(self) -> RouteTable:
        async with self._database.session() as session:
            return await load_route_table(session, self._provider_for)

    def _provider_for(self, row: AiConnection) -> LLMProvider | None:
        cached = self._clients.get(row.id)
        if cached is not None and cached[0] == row.updated_at:
            return cached[1]
        box = self._secret_box()
        if box is None:
            logger.warning("ai_connection_key_unreadable", extra={"reason": "no_secret_key"})
            return None
        try:
            api_key = box.decrypt(row.api_key_encrypted, aad=key_aad(row.id))
        except Exception as exc:
            logger.warning("ai_connection_key_unreadable", extra={"error": type(exc).__name__})
            return None
        provider = connection_provider(
            kind=row.kind,
            base_url=row.base_url,
            model=row.model,
            api_key=api_key,
            timeout_seconds=self._settings.llm_timeout_seconds,
        )
        self._clients[row.id] = (row.updated_at, provider)
        return provider


async def load_route_table(
    session: AsyncSession, provider_for: Callable[[AiConnection], LLMProvider | None]
) -> RouteTable:
    """The chains as stored: active connections only, unknown ids skipped."""
    connections = {
        row.id: row
        for row in (
            await session.scalars(select(AiConnection).where(AiConnection.active.is_(True)))
        ).all()
    }
    chains: dict[AiPurpose, tuple[LLMProvider, ...]] = {}
    for route in (await session.scalars(select(AiRoute))).all():
        providers: list[LLMProvider] = []
        for raw in route.connection_ids or []:
            try:
                row = connections.get(uuid.UUID(str(raw)))
            except ValueError:
                continue
            if row is None:
                continue
            provider = provider_for(row)
            if provider is not None:
                providers.append(provider)
        if providers:
            chains[route.purpose] = tuple(providers)
    return RouteTable(chains=chains)


def build_ai_provider(settings: Settings, database: Database) -> RoutedLLMProvider:
    """The provider every process holds: the routes on top of ``LLM_*``."""
    return RoutedLLMProvider(
        fallback=build_llm_provider(settings),
        loader=AiRoutingLoader(database, settings),
    )


__all__ = [
    "AiRoutingLoader",
    "build_ai_provider",
    "connection_provider",
    "endpoint_of",
    "key_aad",
    "load_route_table",
]
