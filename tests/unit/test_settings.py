"""Settings loading and normalisation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from meobot.core.config import Settings


def test_settings_load_from_environment(settings: Settings) -> None:
    """The pinned test environment is what the app sees."""
    assert settings.app_env == "test"
    assert settings.app_name == "MeoBot"
    assert settings.app_timezone == "Asia/Ho_Chi_Minh"
    assert settings.llm_provider == "fake"


def test_empty_optional_values_become_none(settings: Settings) -> None:
    """``LLM_API_KEY=`` in .env must not blow up SecretStr parsing."""
    assert settings.llm_api_key is None
    assert settings.telegram_bot_token is None
    assert settings.telegram_enabled is False


def test_owner_id_parsed_as_int(settings: Settings) -> None:
    assert settings.meobot_owner_telegram_id == 777000111


def test_database_url_is_forced_to_asyncpg() -> None:
    """A plain postgres:// DSN is upgraded to the async driver."""
    parsed = Settings(
        _env_file=None,
        database_url="postgresql://user:pw@postgres:5432/meobot",
    )
    assert parsed.database_url.startswith("postgresql+asyncpg://")


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        Settings(_env_file=None, app_timezone="Mars/Olympus_Mons")


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        Settings(_env_file=None, log_level="CHATTY")


def test_safe_summary_hides_secrets() -> None:
    """The summary used by /api/v1/system/info must not leak credentials."""
    parsed = Settings(
        _env_file=None,
        telegram_bot_token="123456789:AAHfake_token_value_for_test_only_x",
        llm_api_key="sk-not-a-real-key",
    )
    summary = parsed.safe_summary()
    serialised = str(summary)
    assert "AAHfake_token_value" not in serialised
    assert "sk-not-a-real-key" not in serialised
    assert summary["telegram_configured"] is True


def test_settings_are_frozen(settings: Settings) -> None:
    """Configuration is immutable at runtime - no global mutable state."""
    with pytest.raises(PydanticValidationError):
        settings.app_name = "Other"  # type: ignore[misc]
