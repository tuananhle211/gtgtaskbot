"""``/chat_status`` and ``/chat_test``: useful, and safe to screenshot.

During the outage, ``/chat_status`` reported the model name and looked entirely
healthy while every message failed - it had no way to say whether the provider
had ever answered. These tests pin both halves of the fix:

* the two probes are reported **separately**, because a gateway without
  structured output breaks routing while conversation keeps working, and one
  verdict would hide that;
* nothing a probe reports could carry a credential.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from aiogram import Bot, Dispatcher

from meobot.application.chat_diagnostics_service import ChatDiagnosticsService
from meobot.core.config import Settings
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.llm.diagnostics import ErrorCategory, ProviderDiagnostics
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.integrations.llm.openai_compatible import OpenAICompatibleProvider
from tests.fakes import RecordingSession, SqliteDatabase, make_update

API_KEY = "sk-diagnostics-not-a-real-key-999"
MODEL = "configured-model"
FAST = IntegrationConfig(provider="test", timeout_seconds=2.0, max_retries=0)


def provider(handler: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=API_KEY,
        model=MODEL,
        base_url="https://gateway.example.com/v1?key=super-secret",
        config=FAST,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# --- The two probes --------------------------------------------------------
async def test_both_probes_pass_against_a_healthy_provider(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("response_format"):
            return ok(json.dumps({"mode": "chat", "confidence": 0.9}))
        return ok("OK")

    report = await ChatDiagnosticsService(provider(handler), settings).run()

    assert report.plain_text.passed
    assert report.structured_routing.passed
    assert report.usable_for_chat
    assert "bình thường" in report.recommendation()


async def test_structured_routing_can_fail_while_chat_keeps_working(
    settings: Settings,
) -> None:
    """The exact case the brief describes, and the reason for two probes.

    Plain text ✅, structured routing ❌ - and the recommendation says to keep
    chatting rather than to treat the assistant as down.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("response_format"):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "response_format is not supported",
                        "code": "unsupported_parameter",
                        "param": "response_format",
                    }
                },
            )
        # Structured output is impossible *and* the model will not emit JSON.
        return ok("Mình nghĩ đây là trò chuyện thôi.")

    report = await ChatDiagnosticsService(provider(handler), settings).run()

    assert report.plain_text.passed
    assert not report.structured_routing.passed
    assert report.usable_for_chat, "a routing failure must not disable conversation"
    assert "văn bản" in report.recommendation()


async def test_a_dead_provider_fails_both_probes_with_a_category(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})

    report = await ChatDiagnosticsService(provider(handler), settings).run()

    assert not report.plain_text.passed
    assert not report.structured_routing.passed
    assert report.plain_text.error_category == ErrorCategory.AUTH
    assert "LLM_API_KEY" in report.recommendation()


async def test_the_report_shows_the_negotiated_parameters(settings: Settings) -> None:
    """What ``/chat_test`` tells an operator about the gateway it found."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "max_tokens" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "use max_completion_tokens",
                        "code": "unsupported_parameter",
                        "param": "max_tokens",
                    }
                },
            )
        if body.get("response_format"):
            return ok(json.dumps({"mode": "chat"}))
        return ok("OK")

    report = await ChatDiagnosticsService(provider(handler), settings).run()

    assert report.capabilities.max_tokens_parameter == "max_completion_tokens"
    assert report.capabilities.downgrades


async def test_a_probe_reports_a_category_never_a_provider_body(settings: Settings) -> None:
    """A provider may echo the prompt in its error message. Probes do not."""
    echoed = "your prompt was: kịch bản bí mật"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": echoed, "code": "context_length_exceeded"}}
        )

    report = await ChatDiagnosticsService(provider(handler), settings).run()
    rendered = report.plain_text.render() + report.structured_routing.render()

    assert echoed not in rendered
    assert API_KEY not in rendered
    assert report.plain_text.error_category


async def test_the_offline_provider_passes_both_probes(settings: Settings) -> None:
    """The NAS default must not look broken."""
    report = await ChatDiagnosticsService(FakeLLMProvider(), settings).run()

    assert report.plain_text.passed
    assert report.structured_routing.passed
    assert report.provider == "fake"


def test_a_probe_line_is_readable_in_telegram() -> None:
    from meobot.application.chat_diagnostics_service import ProbeResult

    assert ProbeResult("Sinh văn bản", True, 1234).render() == "Sinh văn bản: ✅ 1.2s"
    assert "❌" in ProbeResult("Định tuyến", False, 10, "schema_not_supported").render()


# --- What /chat_status may show -------------------------------------------
def test_the_base_url_is_reported_as_a_host_and_nothing_else() -> None:
    """A gateway key in a query string is a real pattern, so only the host."""
    configured = Settings(llm_base_url="https://gateway.example.com/v1?key=super-secret")

    assert configured.llm_base_url_host == "gateway.example.com"
    assert "super-secret" not in (configured.llm_base_url_host or "")
    assert Settings(llm_base_url="").llm_base_url_host is None


def test_the_api_key_is_reported_only_as_configured_or_not() -> None:
    from pydantic import SecretStr

    assert Settings(llm_api_key=SecretStr("sk-abc")).llm_key_configured is True
    assert Settings().llm_key_configured is False


def test_diagnostics_record_success_and_failure_separately() -> None:
    diagnostics = ProviderDiagnostics()
    assert not diagnostics.has_succeeded

    diagnostics.record_success(task="generate_chat_reply", latency_ms=1200)
    diagnostics.record_error(task="route_message", category=ErrorCategory.PARSE_FAILED)

    assert diagnostics.has_succeeded
    assert diagnostics.last_latency_ms == 1200
    assert diagnostics.last_error_category == ErrorCategory.PARSE_FAILED
    assert diagnostics.success_count == 1
    assert diagnostics.error_count == 1


def test_every_error_category_has_an_operator_hint() -> None:
    """A category nobody can act on is not a diagnosis."""
    categories = [
        value
        for name, value in vars(ErrorCategory).items()
        if name.isupper() and isinstance(value, str)
    ]
    assert categories
    for category in categories:
        assert ErrorCategory.hint(category) != ErrorCategory.HINTS[ErrorCategory.UNKNOWN] or (
            category == ErrorCategory.UNKNOWN
        )


def test_a_diagnostics_snapshot_contains_no_secret() -> None:
    diagnostics = ProviderDiagnostics()
    diagnostics.record_success(task="t", latency_ms=1)
    diagnostics.record_downgrade("max_tokens->max_completion_tokens")

    rendered = json.dumps(diagnostics.as_dict())
    assert "sk-" not in rendered
    assert "max_completion_tokens" in rendered


# --- The commands ----------------------------------------------------------
async def test_chat_status_answers_without_revealing_configuration(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("/chat_status", update_id=901, message_id=21))

    text = session.combined_text()
    assert "CHAT_ENABLED" in text
    assert "API key" in text
    assert "Lần gọi thành công gần nhất" in text
    assert "sk-" not in text


async def test_chat_test_is_owner_only_and_reports_both_probes(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """The bootstrap owner may run it; the report names both probes."""
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("/chat_test", update_id=902, message_id=22))

    text = session.combined_text()
    assert "Sinh văn bản" in text
    assert "Định tuyến có cấu trúc" in text
    assert "Khuyến nghị" in text
    assert "sk-" not in text


async def test_chat_test_is_refused_for_a_non_owner(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    """It spends real tokens, so only the person paying for them may run it."""
    from meobot.db.models.user import User
    from meobot.domain.identity.models import Role

    employee_id = 424242
    async with bot_database.transaction() as db_session:
        db_session.add(
            User(
                telegram_user_id=employee_id,
                telegram_username="nhanvien",
                full_name="Nhân viên A",
                role=Role.EMPLOYEE,
                active=True,
            )
        )

    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot, make_update("/chat_test", update_id=903, message_id=23, user_id=employee_id)
    )

    text = session.combined_text()
    assert "⛔" in text
    assert "Sinh văn bản" not in text


# Distinct update ids per case: the deduplication middleware is process-wide,
# so reusing one id would make the second parametrisation a no-op.
@pytest.mark.parametrize(
    ("command", "update_id"), [("/whoami", 9041), ("/assistant_profile", 9042)]
)
async def test_the_identity_commands_answer(
    command: str,
    update_id: int,
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update(command, update_id=update_id, message_id=24))

    assert session.sent_texts(), f"{command} produced no answer"
    assert "sk-" not in session.combined_text()


async def test_whoami_shows_the_authoritative_role(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(bot, make_update("/whoami", update_id=905, message_id=25))

    text = session.combined_text()
    # The display label, never the internal enum name.
    assert "Chủ sở hữu" in text
    assert "OWNER" not in text
    assert "hệ thống định danh" in text


async def test_set_preferred_address_changes_how_meobot_speaks(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot, make_update("/set_preferred_address anh", update_id=906, message_id=26)
    )
    assert "anh" in session.combined_text()

    await dispatcher.feed_update(bot, make_update("/whoami", update_id=907, message_id=27))
    assert "anh" in session.combined_text()


async def test_set_preferred_address_without_an_argument_shows_the_usage(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
) -> None:
    bot, session = bot_and_session
    await dispatcher.feed_update(
        bot, make_update("/set_preferred_address", update_id=908, message_id=28)
    )
    assert "Cú pháp" in session.combined_text()
