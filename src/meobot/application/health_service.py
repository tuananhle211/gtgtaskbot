"""Dependency health checks shared by ``/health/ready``, ``/health`` (bot) and tools."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict, Field

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database

logger = get_logger(__name__)

#: Health probes must be fast - a slow probe is a failed probe.
PROBE_TIMEOUT_SECONDS = 3.0
WORKER_PING_TIMEOUT_SECONDS = 2.0


class ComponentHealth(BaseModel):
    """Health of a single dependency."""

    model_config = ConfigDict(frozen=True)

    name: str
    healthy: bool
    detail: str | None = None
    latency_ms: float | None = None


class HealthReport(BaseModel):
    """Aggregate health of everything MeoBot needs to operate."""

    model_config = ConfigDict(frozen=True)

    healthy: bool
    checked_at: datetime
    components: list[ComponentHealth] = Field(default_factory=list)

    def component(self, name: str) -> ComponentHealth | None:
        for item in self.components:
            if item.name == name:
                return item
        return None

    def as_lines(self) -> list[str]:
        """Telegram-friendly rendering."""
        return [
            f"{'✅' if item.healthy else '❌'} {item.name}"
            + (f" — {item.detail}" if item.detail else "")
            for item in self.components
        ]


class HealthService:
    """Probes PostgreSQL, Redis and (optionally) Celery workers.

    Args:
        database: Engine wrapper used for the SQL probe.
        settings: Supplies the Redis URL.
        worker_ping: Optional coroutine returning the list of responding
            workers. Injected so tests never touch a broker.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        worker_ping: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._worker_ping = worker_ping

    async def check(self, *, include_workers: bool = True) -> HealthReport:
        """Run all probes concurrently and aggregate the result.

        Worker health is reported but does not make the system unhealthy: the
        API stays ready even while the worker container restarts.
        """
        probes: list[Awaitable[ComponentHealth]] = [self._check_postgres(), self._check_redis()]
        if include_workers and self._worker_ping is not None:
            probes.append(self._check_workers())

        components = list(await asyncio.gather(*probes))
        required = [item for item in components if item.name in {"postgres", "redis"}]
        return HealthReport(
            healthy=all(item.healthy for item in required),
            checked_at=utcnow(),
            components=components,
        )

    async def _check_postgres(self) -> ComponentHealth:
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                await self._database.ping()
        except Exception as exc:
            logger.warning("healthcheck_postgres_failed", extra={"error": type(exc).__name__})
            return ComponentHealth(name="postgres", healthy=False, detail=type(exc).__name__)
        return ComponentHealth(
            name="postgres",
            healthy=True,
            latency_ms=self._elapsed_ms(started),
        )

    async def _check_redis(self) -> ComponentHealth:
        started = asyncio.get_running_loop().time()
        # The pool is built explicitly instead of via the untyped ``from_url``
        # helper, and torn down after every probe: a health check must not leak
        # connections.
        pool = aioredis.ConnectionPool.from_url(self._settings.redis_url)
        client = aioredis.Redis(connection_pool=pool)
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                await client.ping()
        except Exception as exc:
            logger.warning("healthcheck_redis_failed", extra={"error": type(exc).__name__})
            return ComponentHealth(name="redis", healthy=False, detail=type(exc).__name__)
        finally:
            await client.aclose(close_connection_pool=True)
        return ComponentHealth(name="redis", healthy=True, latency_ms=self._elapsed_ms(started))

    async def _check_workers(self) -> ComponentHealth:
        assert self._worker_ping is not None
        try:
            async with asyncio.timeout(WORKER_PING_TIMEOUT_SECONDS):
                workers = await self._worker_ping()
        except Exception as exc:
            logger.warning("healthcheck_worker_failed", extra={"error": type(exc).__name__})
            return ComponentHealth(name="celery_worker", healthy=False, detail=type(exc).__name__)
        return ComponentHealth(
            name="celery_worker",
            healthy=bool(workers),
            detail=f"{len(workers)} worker(s)" if workers else "no worker responded",
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((asyncio.get_running_loop().time() - started) * 1000, 2)
