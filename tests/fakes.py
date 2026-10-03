"""Test doubles.

These deliberately implement only what the code under test calls. A fake that
grows a method nobody uses is a fake that hides a design problem.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from meobot.application.health_service import ComponentHealth, HealthReport
from meobot.core.time import utcnow
from meobot.db.base import Base


class FakeScalars:
    """Stand-in for :meth:`sqlalchemy.engine.Result.scalars`."""

    def __init__(self, items: Sequence[Any]) -> None:
        self._items = list(items)

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any | None:
        return self._items[0] if self._items else None


class FakeResult:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, items: Sequence[Any] = ()) -> None:
        self._items = list(items)

    def scalar_one_or_none(self) -> Any | None:
        if not self._items:
            return None
        if len(self._items) > 1:
            raise AssertionError("scalar_one_or_none() called on multiple rows")
        return self._items[0]

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._items)


class FakeSession:
    """Minimal ``AsyncSession`` substitute.

    Args:
        results: Queue of results returned by successive ``execute`` calls.
        rows: Objects returned by ``get`` keyed by primary key.
    """

    def __init__(
        self,
        results: Sequence[FakeResult] | None = None,
        rows: dict[Any, Any] | None = None,
    ) -> None:
        self.results = list(results or [])
        self.rows = dict(rows or {})
        self.added: list[Any] = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_error: Exception | None = None

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_count += 1
        if self.flush_error is not None:
            error, self.flush_error = self.flush_error, None
            raise error

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def get(self, model: type[Any], primary_key: Any) -> Any | None:
        return self.rows.get(primary_key)

    async def execute(self, statement: Any) -> FakeResult:
        if not self.results:
            return FakeResult()
        return self.results.pop(0)

    def added_of(self, model: type[Any]) -> list[Any]:
        """Objects of ``model`` handed to :meth:`add`."""
        return [item for item in self.added if isinstance(item, model)]


class FakeDatabase:
    """``Database`` substitute handing out one shared :class:`FakeSession`."""

    def __init__(self, session: FakeSession | None = None) -> None:
        self.fake_session = session or FakeSession()
        self.transactions = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[FakeSession]:
        yield self.fake_session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[FakeSession]:
        self.transactions += 1
        try:
            yield self.fake_session
        except Exception:
            await self.fake_session.rollback()
            raise
        else:
            await self.fake_session.commit()

    async def ping(self) -> bool:
        return True

    async def dispose(self) -> None:
        return None


class StubHealthService:
    """Health service that answers from a canned report."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.calls = 0

    async def check(self, *, include_workers: bool = True) -> HealthReport:
        self.calls += 1
        return HealthReport(
            healthy=self.healthy,
            checked_at=utcnow(),
            components=[
                ComponentHealth(name="postgres", healthy=self.healthy, latency_ms=1.0),
                ComponentHealth(name="redis", healthy=self.healthy, latency_ms=0.5),
            ],
        )


def new_uuid() -> uuid.UUID:
    """Readable helper for tests that just need a distinct id."""
    return uuid.uuid4()


# --- Real-schema database over SQLite --------------------------------------
class SqliteDatabase:
    """A ``Database`` substitute backed by a real (in-memory) SQLite engine.

    PostgreSQL remains the only supported runtime and Alembic remains the only
    way its schema changes. This exists so the *behavioural* rules that need
    real SQL - FSM state surviving between updates, conversation memory,
    idempotent creation - can be exercised without a database container. The
    schema is built from the same ORM metadata the migrations were written
    from; nothing here touches a deployed database.
    """

    def __init__(self) -> None:
        # A shared in-memory database: every connection from this engine sees
        # the same tables, which a plain ``:memory:`` URL would not give us.
        self._engine = create_async_engine(
            "sqlite+aiosqlite:///file:meobot_test?mode=memory&cache=shared&uri=true"
        )
        self._factory = async_sessionmaker(
            bind=self._engine, expire_on_commit=False, autoflush=False
        )
        self._ready = False

    async def create_schema(self) -> None:
        """Build every table once."""
        if self._ready:
            return
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self._ready = True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        await self.create_schema()
        async with self._factory() as active:
            yield active

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        await self.create_schema()
        async with self._factory() as active:
            try:
                yield active
            except Exception:
                await active.rollback()
                raise
            else:
                await active.commit()

    async def ping(self) -> bool:
        return True

    async def dispose(self) -> None:
        await self._engine.dispose()


class SwitchableDatabase:
    """Delegates to whichever database the current test installed.

    aiogram routers are module-level singletons, so exactly one Dispatcher can
    exist per process - and it must be built once, before any test runs. Its
    FSM storage and its handlers therefore hold a database reference that
    cannot be rebound later. This proxy is that reference; each test points it
    at a fresh :class:`SqliteDatabase`.
    """

    def __init__(self) -> None:
        self.target: SqliteDatabase | None = None

    def use(self, target: SqliteDatabase) -> None:
        self.target = target

    def _require(self) -> SqliteDatabase:
        if self.target is None:
            raise AssertionError("No database installed - use the `bot_database` fixture")
        return self.target

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._require().session() as active:
            yield active

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._require().transaction() as active:
            yield active

    async def ping(self) -> bool:
        return True

    async def dispose(self) -> None:
        return None


# --- Telegram transport ----------------------------------------------------
class RecordingSession(BaseSession):
    """An aiogram session that records outgoing calls instead of sending them.

    Lets a test feed a real :class:`~aiogram.types.Update` through the real
    Dispatcher - middlewares, filters, router order and all - and then assert
    on what the bot tried to send. That is the only way to prove a command is
    not swallowed by another handler.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        #: Raised by the next matching call, then cleared. Used to simulate
        #: Telegram refusing a message.
        self.fail_on: dict[str, Exception] = {}

    async def close(self) -> None:
        return None

    # ``timeout`` is part of aiogram's abstract signature, not a choice this
    # fake gets to make - hence the ASYNC109 exemptions here and below.
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        name = type(method).__name__
        self.requests.append(method)
        error = self.fail_on.pop(name, None)
        if error is not None:
            raise error
        if name in {"SendMessage", "EditMessageText"}:
            return make_message(
                text=str(getattr(method, "text", "")),
                chat_id=int(getattr(method, "chat_id", 1) or 1),
            )
        return True

    async def stream_content(  # type: ignore[override]
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncIterator[bytes]:
        yield b""

    # --- Assertions helpers ----------------------------------------------
    def sent_texts(self) -> list[str]:
        """Text of every ``sendMessage``/``editMessageText`` attempted."""
        return [
            str(request.text)
            for request in self.requests
            if type(request).__name__ in {"SendMessage", "EditMessageText"}
        ]

    def sent_of(self, method_name: str) -> list[TelegramMethod[Any]]:
        """Every recorded call of one method."""
        return [request for request in self.requests if type(request).__name__ == method_name]

    def combined_text(self) -> str:
        """All sent text joined - convenient for "does the reply mention X"."""
        return "\n".join(self.sent_texts())


def make_message(
    *,
    text: str,
    chat_id: int = 555,
    user_id: int = 777000111,
    message_id: int = 1,
    username: str | None = "owner",
    date: datetime | None = None,
) -> Message:
    """Build a Telegram ``Message`` as the API would deliver it."""
    return Message(
        message_id=message_id,
        date=date or utcnow(),
        chat=Chat(id=chat_id, type="private"),
        from_user=TelegramUser(
            id=user_id,
            is_bot=False,
            first_name="Owner",
            username=username,
        ),
        text=text,
    )


def make_update(
    text: str,
    *,
    chat_id: int = 555,
    user_id: int = 777000111,
    update_id: int = 1,
    message_id: int = 1,
) -> Update:
    """Wrap a text message in an ``Update``, ready for ``feed_update``."""
    return Update(
        update_id=update_id,
        message=make_message(text=text, chat_id=chat_id, user_id=user_id, message_id=message_id),
    )


#: The username the test bot answers to. Set on the Bot fixture as aiogram's
#: cached ``getMe`` result, so ``@MeoBotTest`` in a group message is recognised
#: as addressing MeoBot without any network call.
BOT_USERNAME = "MeoBotTest"
BOT_ID = 123456


def bot_user() -> TelegramUser:
    """The ``getMe`` result the fake bot reports."""
    return TelegramUser(id=BOT_ID, is_bot=True, first_name="MeoBot", username=BOT_USERNAME)


def make_group_message(
    *,
    text: str,
    chat_id: int = -1001,
    chat_title: str = "Nhóm Nội Dung",
    user_id: int = 900001,
    username: str | None = "nguoila",
    full_name: str = "Người Lạ",
    message_id: int = 1,
    mention: bool = True,
    reply_to: Message | None = None,
    is_bot: bool = False,
) -> Message:
    """A group message, optionally addressing MeoBot or replying to somebody.

    ``mention=True`` prefixes the bot's ``@username``, which is how a group
    message is addressed to it - and therefore what makes the access gate treat
    it as a question rather than as overheard conversation.
    """
    body = f"@{BOT_USERNAME} {text}" if mention else text
    return Message(
        message_id=message_id,
        date=utcnow(),
        chat=Chat(id=chat_id, type="supergroup", title=chat_title),
        from_user=TelegramUser(
            id=user_id,
            is_bot=is_bot,
            first_name=full_name.split()[0] if full_name else "Ai",
            last_name=" ".join(full_name.split()[1:]) or None,
            username=username,
        ),
        text=body,
        reply_to_message=reply_to,
    )


def make_group_update(
    text: str,
    *,
    update_id: int,
    chat_id: int = -1001,
    chat_title: str = "Nhóm Nội Dung",
    user_id: int = 900001,
    username: str | None = "nguoila",
    full_name: str = "Người Lạ",
    message_id: int = 1,
    mention: bool = True,
    reply_to: Message | None = None,
    is_bot: bool = False,
) -> Update:
    """A group message wrapped in an ``Update``, ready for ``feed_update``."""
    return Update(
        update_id=update_id,
        message=make_group_message(
            text=text,
            chat_id=chat_id,
            chat_title=chat_title,
            user_id=user_id,
            username=username,
            full_name=full_name,
            message_id=message_id,
            mention=mention,
            reply_to=reply_to,
            is_bot=is_bot,
        ),
    )


def bot_reply_message(*, chat_id: int = -1001, message_id: int = 500) -> Message:
    """A message from MeoBot, so a test can build a reply *to the bot*."""
    return Message(
        message_id=message_id,
        date=utcnow(),
        chat=Chat(id=chat_id, type="supergroup", title="Nhóm Nội Dung"),
        from_user=bot_user(),
        text="MeoBot đã trả lời trước đó.",
    )


def member_reply_message(
    *, chat_id: int, user_id: int, full_name: str, username: str | None = None, message_id: int
) -> Message:
    """A message from an ordinary person, to be replied to by an owner command."""
    return Message(
        message_id=message_id,
        date=utcnow(),
        chat=Chat(id=chat_id, type="supergroup", title="Nhóm Nội Dung"),
        from_user=TelegramUser(
            id=user_id,
            is_bot=False,
            first_name=full_name.split()[0] if full_name else "Ai",
            last_name=" ".join(full_name.split()[1:]) or None,
            username=username,
        ),
        text="tin nhắn của thành viên",
    )


def drawn_button(session: RecordingSession, label_starts: str) -> str:
    """The callback data of a button MeoBot actually rendered.

    Some buttons carry an id only the handler knows - a registration draft, an
    announcement - so a test that forges its own payload is exercising a
    different button than the one a person can press. This reads the real
    keyboard back off the recorded call, newest first.
    """
    for request in reversed(session.requests):
        markup = getattr(request, "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", None) or ():
            for button in row:
                if str(button.text).startswith(label_starts):
                    return str(button.callback_data)
    raise AssertionError(f"no button labelled {label_starts!r} was rendered")
