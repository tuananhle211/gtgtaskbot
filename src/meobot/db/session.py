"""Async engine and session lifecycle.

The engine is expensive and must be shared per process; sessions are cheap and
must be per-unit-of-work. :class:`Database` owns both, and every entry point
(API lifespan, bot startup, Celery worker init) creates exactly one instance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from meobot.core.config import Settings, get_settings
from meobot.core.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Owns the async engine and hands out sessions.

    Args:
        settings: Application settings supplying the DSN and pool sizing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session without an implicit transaction boundary.

        Use this for read-only work. Writes should use :meth:`transaction`.
        """
        async with self._session_factory() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Yield a session wrapped in one explicit transaction.

        Commits on clean exit, rolls back on any exception, and always
        re-raises - failures are never swallowed here.
        """
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    async def ping(self) -> bool:
        """Return True when the database answers ``SELECT 1``."""
        async with self._engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            return bool(result.scalar_one() == 1)

    async def dispose(self) -> None:
        """Close pooled connections. Call on shutdown."""
        await self._engine.dispose()
        logger.info("database_engine_disposed")


@lru_cache(maxsize=1)
def get_database() -> Database:
    """Process-wide :class:`Database` singleton built from cached settings."""
    return Database(get_settings())
