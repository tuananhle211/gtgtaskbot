"""Bridging Celery's synchronous world into MeoBot's async one.

A Celery task body is a plain function; everything below the application layer
is async. :func:`run_async` owns that boundary, and it builds a *fresh*
:class:`Database` per task: an asyncpg pool is bound to the event loop that
created it, so a cached engine cannot be shared across ``asyncio.run`` calls.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from meobot.core.config import Settings, get_settings
from meobot.core.context import get_request_id
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.integrations.google.drive import DriveClient
from meobot.integrations.google.factory import build_drive_client, build_sheets_client
from meobot.integrations.google.sheets import SheetsClient
from meobot.integrations.llm.base import LLMProvider
from meobot.integrations.llm.factory import build_llm_provider
from meobot.integrations.telegram.notifier import Notifier, build_notifier

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(slots=True)
class TaskContext:
    """Collaborators a task body receives. Built once per task run."""

    settings: Settings
    database: Database
    sheets: SheetsClient
    drive: DriveClient
    llm: LLMProvider
    notifier: Notifier

    @property
    def owner_chat_id(self) -> int | None:
        """Where unsolicited notifications go."""
        return self.settings.meobot_owner_telegram_id

    def system_actor(self) -> Actor:
        """The actor a scheduled run acts as.

        Background work is attributed to the configured owner rather than to
        nobody, so an audit row always names a responsible account. It is not a
        privilege escalation path: the tasks that use it are the ones the owner
        scheduled, and no task approves a script.
        """
        return Actor(
            user_id=None,
            telegram_user_id=self.settings.meobot_owner_telegram_id,
            telegram_username=None,
            full_name="meobot-worker",
            role=Role.OWNER,
            active=True,
            is_bootstrap_owner=True,
        )


def run_async[T](operation: Callable[[TaskContext], Awaitable[T]]) -> T:
    """Run ``operation`` with a freshly built :class:`TaskContext`.

    The engine, the HTTP clients and the event loop are all torn down before
    returning, which keeps a long-lived worker from accumulating connections.
    """
    settings = get_settings()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # Celery's eager mode runs a task inline, and inline can mean "inside
        # an async handler". ``asyncio.run`` cannot nest, and letting it fail
        # after the coroutine exists leaves an un-awaited coroutine warning
        # pointing at the wrong place. Fail here instead, before building one.
        raise RuntimeError(
            "run_async cannot be called from a running event loop; "
            "call the task's async body directly instead"
        )

    async def main() -> T:
        database = Database(settings)
        sheets = build_sheets_client(settings)
        drive = build_drive_client(settings)
        notifier = build_notifier(
            settings.telegram_bot_token.get_secret_value()
            if settings.telegram_bot_token is not None
            else None
        )
        context = TaskContext(
            settings=settings,
            database=database,
            sheets=sheets,
            drive=drive,
            llm=build_llm_provider(settings),
            notifier=notifier,
        )
        try:
            return await operation(context)
        finally:
            await _close(sheets)
            await _close(drive)
            await _close(notifier)
            await _close(context.llm)
            await database.dispose()

    return asyncio.run(main())


def current_request_id() -> uuid.UUID:
    """Reuse the correlation id bound by ``task_prerun``, or mint one."""
    current = get_request_id()
    try:
        return uuid.UUID(current) if current else uuid.uuid4()
    except ValueError:  # pragma: no cover - a non-UUID header
        return uuid.uuid4()


async def _close(candidate: object) -> None:
    """Close a client that owns an HTTP session, if it has one."""
    closer = getattr(candidate, "aclose", None)
    if callable(closer):
        try:
            await closer()
        except Exception:  # pragma: no cover - shutdown must not mask the result
            logger.warning("task_client_close_failed", extra={"client": type(candidate).__name__})
