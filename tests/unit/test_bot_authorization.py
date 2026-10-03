"""Bot-level authorisation: who gets past the middleware, and what /start says."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram.types import Chat, Message
from pydantic import PrivateAttr

from meobot.bot.middlewares import ActorMiddleware
from meobot.bot.texts import NOT_REGISTERED, WELCOME_OWNER, welcome_for
from meobot.core.config import Settings
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import FakeDatabase, FakeResult, FakeSession


class FakeTelegramUser:
    """Shape of ``aiogram.types.User`` that the middleware actually reads."""

    def __init__(self, user_id: int, username: str | None = None, full_name: str = "Ai đó") -> None:
        self.id = user_id
        self.username = username
        self.full_name = full_name


class RecordingMessage(Message):
    """A real ``aiogram`` Message whose ``answer`` records instead of calling Telegram.

    Subclassing the real type matters: :class:`ActorMiddleware` checks
    ``isinstance(event, Message)`` before replying, and a duck-typed stub would
    hide a wrong middleware registration.
    """

    _replies: list[str] = PrivateAttr(default_factory=list)

    async def answer(self, text: str, **kwargs: Any) -> Any:  # type: ignore[override]
        self._replies.append(text)
        return None

    @property
    def replies(self) -> list[str]:
        return self._replies


def make_message(text: str = "/start") -> RecordingMessage:
    """Build a minimal but valid Telegram message."""
    return RecordingMessage(
        message_id=1,
        date=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        chat=Chat(id=1, type="private"),
        text=text,
    )


async def run_middleware(
    settings: Settings,
    telegram_user: FakeTelegramUser,
    session: FakeSession,
    text: str = "/start",
) -> tuple[bool, RecordingMessage, dict[str, Any]]:
    """Drive the middleware and report whether the handler was reached."""
    called = False
    data: dict[str, Any] = {"event_from_user": telegram_user}
    message = make_message(text)

    async def handler(event: Any, handler_data: dict[str, Any]) -> None:
        nonlocal called
        called = True

    middleware = ActorMiddleware(FakeDatabase(session), settings)  # type: ignore[arg-type]
    await middleware(handler, message, data)  # type: ignore[arg-type]
    return called, message, data


async def test_owner_reaches_the_handler(settings: Settings) -> None:
    session = FakeSession(results=[FakeResult()])  # no users row yet
    called, message, data = await run_middleware(
        settings, FakeTelegramUser(OWNER_TELEGRAM_ID, "owner", "Chị Mèo"), session
    )

    assert called is True
    assert message.replies == []
    actor: Actor = data["actor"]
    assert actor.role is Role.OWNER


async def test_unregistered_user_is_stopped_with_one_message(settings: Settings) -> None:
    """Unknown accounts never reach a handler and learn nothing about the system."""
    session = FakeSession(results=[FakeResult()])
    called, message, data = await run_middleware(
        settings, FakeTelegramUser(424242, "nguoila"), session
    )

    assert called is False
    assert message.replies == [NOT_REGISTERED]
    assert "actor" not in data


async def test_deactivated_user_is_stopped(settings: Settings) -> None:
    user = User(
        telegram_user_id=888,
        telegram_username="cu",
        full_name="Đã nghỉ",
        role=Role.EMPLOYEE,
        active=False,
    )
    session = FakeSession(results=[FakeResult([user])])
    called, message, _ = await run_middleware(settings, FakeTelegramUser(888), session)

    assert called is False
    assert message.replies == [NOT_REGISTERED]


async def test_registered_employee_reaches_the_handler(settings: Settings) -> None:
    user = User(
        telegram_user_id=999,
        telegram_username="nv",
        full_name="Nhân viên",
        role=Role.EMPLOYEE,
        active=True,
    )
    session = FakeSession(results=[FakeResult([user])])
    called, _, data = await run_middleware(settings, FakeTelegramUser(999), session)

    assert called is True
    assert data["actor"].role is Role.EMPLOYEE


def test_owner_greeting_matches_the_specified_wording() -> None:
    assert welcome_for(Role.OWNER, "Chị Mèo") == WELCOME_OWNER
    # The display label - see meobot.domain.identity.labels. The internal enum
    # name is for the database and the audit trail, never for a greeting.
    assert "Chủ sở hữu" in WELCOME_OWNER
    assert "OWNER" not in WELCOME_OWNER


def test_non_owner_greeting_states_the_role() -> None:
    greeting = welcome_for(Role.TEAM_LEAD, "Trưởng nhóm B")
    assert "Vai trò của bạn: Trưởng nhóm" in greeting
    assert "TEAM_LEAD" not in greeting
    assert "Trưởng nhóm B" in greeting


# --- Milestone 2: invite-code onboarding ------------------------------------
async def test_join_reaches_the_handler_without_an_account(settings: Settings) -> None:
    """/join is how somebody becomes registered; refusing it would deadlock it."""
    session = FakeSession(results=[FakeResult()])
    called, message, data = await run_middleware(
        settings, FakeTelegramUser(424243, "moi"), session, text="/join ABCD234XYZ"
    )

    assert called is True
    assert message.replies == []
    # No actor is bound: the /join handler must not assume one exists.
    assert "actor" not in data


async def test_join_with_a_bot_suffix_is_still_public(settings: Settings) -> None:
    """Group chats append '@BotName' to every command."""
    session = FakeSession(results=[FakeResult()])
    called, _, _ = await run_middleware(
        settings, FakeTelegramUser(424244), session, text="/join@MeoBot ABCD234XYZ"
    )
    assert called is True


async def test_no_other_command_is_public(settings: Settings) -> None:
    """Only /join. Everything else still needs an account."""
    for text in ("/start", "/pending_scripts", "/create_invite", "/joinx CODE", "join ABCD"):
        session = FakeSession(results=[FakeResult()])
        called, message, _ = await run_middleware(
            settings, FakeTelegramUser(424245), session, text=text
        )
        assert called is False, text
        assert message.replies == [NOT_REGISTERED]
