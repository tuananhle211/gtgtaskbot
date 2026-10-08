"""Shared fixtures.

The environment is pinned here, before any MeoBot module is imported, so a
developer's local ``.env`` can never change what the tests assert. Environment
variables outrank ``.env`` in pydantic-settings, which is what makes this work.
"""

from __future__ import annotations

import os
import uuid

TEST_ENV: dict[str, str] = {
    "APP_ENV": "test",
    "APP_NAME": "TasksBot",
    "APP_TIMEZONE": "Asia/Ho_Chi_Minh",
    "LOG_LEVEL": "WARNING",
    "LOG_FORMAT": "console",
    "DATABASE_URL": "postgresql+asyncpg://meobot:test@localhost:5432/meobot_test",
    "REDIS_URL": "redis://localhost:6379/15",
    "CELERY_BROKER_URL": "redis://localhost:6379/14",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/13",
    "CELERY_TASK_ALWAYS_EAGER": "true",
    "TELEGRAM_BOT_TOKEN": "",
    "MEOBOT_OWNER_TELEGRAM_ID": "777000111",
    "LLM_PROVIDER": "fake",
    "LLM_API_KEY": "",
    # Pinned empty on purpose. A developer's real ``.env`` supplies LLM_MODEL
    # and GOOGLE_SERVICE_ACCOUNT_FILE, and pydantic-settings reads that file
    # for any key the environment does not set - which silently changed what
    # the "no model configured" and "Google not configured" tests observed.
    "LLM_MODEL": "",
    "LLM_BASE_URL": "",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "",
    "GOOGLE_SHARED_DRIVE_ID": "",
    "GOOGLE_DRIVE_ROOT_FOLDER_ID": "",
    "GOOGLE_WORK_SHEET_TEMPLATE_ID": "",
    "GOOGLE_SCRIPT_SHEET_TEMPLATE_ID": "",
    "CONFIRMATION_TTL_SECONDS": "300",
    "CHAT_ENABLED": "true",
    "CHAT_HISTORY_MAX_MESSAGES": "20",
    "CHAT_SUMMARY_TRIGGER_MESSAGES": "30",
    "CHAT_RESPONSE_MAX_TOKENS": "1200",
    "CHAT_TYPING_INDICATOR": "false",
    "CHAT_GROUP_REQUIRES_MENTION": "true",
    # Pinned empty for the same reason LLM_MODEL is: a real ``.env`` supplies
    # the organisation and the owner's title, and pydantic-settings reads that
    # file for any key the environment does not set - which would silently
    # change what the "unconfigured deployment" tests observe.
    "MEOBOT_ORGANIZATION_NAME": "",
    "MEOBOT_DEPARTMENT_NAME": "",
    "MEOBOT_DEPARTMENT_SIZE": "",
    "MEOBOT_OWNER_TITLE": "",
    "MEOBOT_OWNER_PREFERRED_ADDRESS": "",
    # Pinned to the code defaults for the same reason again: a local ``.env``
    # that serves the panel over plain HTTP sets WEB_COOKIE_SECURE=false, and
    # pydantic-settings would read it for any key the environment leaves unset,
    # which turned the "strict cookie defaults" tests red on that machine.
    "WEB_BASE_URL": "",
    "WEB_COOKIE_SECURE": "true",
    "WEB_EXTRA_ALLOWED_ORIGINS": "",
    "WEB_LOGIN_TOKEN_TTL_SECONDS": "600",
    "WEB_SESSION_TTL_SECONDS": "43200",
    # The password-login default (0045), pinned for the same reason.
    "MEOBOT_WEB_DEFAULT_PASSWORD": "Apm@2026",
}
os.environ.update(TEST_ENV)

from collections.abc import AsyncIterator  # noqa: E402
from functools import lru_cache  # noqa: E402

import pytest  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import meobot.db.models  # noqa: E402,F401 - registers every table on Base.metadata
from meobot.core.config import Settings, get_settings  # noqa: E402
from meobot.db.base import Base  # noqa: E402
from meobot.domain.identity.models import Actor, Role  # noqa: E402
from tests.fakes import (  # noqa: E402
    RecordingSession,
    SqliteDatabase,
    SwitchableDatabase,
    bot_user,
)
from tests.unit.work_clock import frozen_work_clock  # noqa: E402

#: Telegram id declared as the bootstrap owner in the test environment.
OWNER_TELEGRAM_ID = int(TEST_ENV["MEOBOT_OWNER_TELEGRAM_ID"])


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A throwaway in-memory database for tests that need real SQL.

    PostgreSQL remains the only supported runtime, and Alembic remains the only
    way its schema changes - see ``tests/integration`` for the tests that run
    against a migrated PostgreSQL. This fixture exists so the *behavioural*
    rules (sync idempotency, version binding, approval binding) can be checked
    offline, without a database container. The schema is built from the same
    ORM metadata the migrations were written from; nothing here touches a
    deployed database.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as active:
        try:
            yield active
        finally:
            await active.rollback()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Ensure every test observes settings built from ``TEST_ENV``."""
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings built from the pinned test environment."""
    return get_settings()


@pytest.fixture
def request_id() -> uuid.UUID:
    """A stable correlation id for a single test."""
    return uuid.uuid4()


@pytest.fixture
def owner_actor() -> Actor:
    """The bootstrap owner, as the bot would resolve them."""
    return Actor(
        user_id=None,
        telegram_user_id=OWNER_TELEGRAM_ID,
        telegram_username="owner",
        full_name="Owner",
        role=Role.OWNER,
        active=True,
        is_bootstrap_owner=True,
    )


@pytest.fixture
def employee_actor() -> Actor:
    """A regular registered employee."""
    return Actor(
        user_id=uuid.uuid4(),
        telegram_user_id=123456789,
        telegram_username="nhanvien",
        full_name="Nhân viên A",
        role=Role.EMPLOYEE,
        active=True,
    )


@pytest.fixture
def team_lead_actor() -> Actor:
    """A team lead - may approve scripts and videos."""
    return Actor(
        user_id=uuid.uuid4(),
        telegram_user_id=222333444,
        telegram_username="truongnhom",
        full_name="Trưởng nhóm B",
        role=Role.TEAM_LEAD,
        active=True,
    )


@pytest.fixture
def admin_actor() -> Actor:
    """An admin - may register Drive folders and manage templates."""
    return Actor(
        user_id=uuid.uuid4(),
        telegram_user_id=333444555,
        telegram_username="quantri",
        full_name="Quản trị C",
        role=Role.ADMIN,
        active=True,
    )


# --- The one Dispatcher ----------------------------------------------------
# aiogram routers are module-level singletons, so a router can be attached to
# exactly one Dispatcher per process - which is also true in production, where
# ``build_dispatcher`` is called once at startup. Every test that needs the
# real dispatcher shares this instance and points its database at a fresh
# SQLite schema.
_BOT_DATABASE = SwitchableDatabase()


@lru_cache(maxsize=1)
def _shared_dispatcher() -> Dispatcher:
    from meobot.bot.main import build_dispatcher

    return build_dispatcher(get_settings(), _BOT_DATABASE)  # type: ignore[arg-type]


@pytest.fixture
def dispatcher() -> Dispatcher:
    """The process-wide Dispatcher, wired exactly as the bot container wires it."""
    return _shared_dispatcher()


@pytest.fixture
async def bot_database() -> AsyncIterator[SqliteDatabase]:
    """A fresh schema behind the shared Dispatcher, for one test."""
    database = SqliteDatabase()
    await database.create_schema()
    _BOT_DATABASE.use(database)
    try:
        yield database
    finally:
        await database.dispose()
        _BOT_DATABASE.target = None


@pytest.fixture
async def bot_and_session() -> AsyncIterator[tuple[Bot, RecordingSession]]:
    """A Bot whose transport records calls instead of reaching Telegram.

    ``_me`` is pre-seeded with aiogram's ``getMe`` shape so the group-addressing
    rules can be exercised offline: without it, ``@MeoBotTest ...`` in a group
    would not be recognised as addressing the bot and every group test would
    silently take the "not addressed" path.
    """
    session = RecordingSession()
    bot = Bot(token="123456:TEST-TOKEN-NOT-REAL-AAAAAAAAAAAAAAAAAAAA", session=session)
    bot._me = bot_user()
    try:
        yield bot, session
    finally:
        await bot.session.close()


__all__ = ["frozen_work_clock"]
