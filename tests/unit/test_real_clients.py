"""The real Google Sheets and LLM clients, driven by a mocked transport.

No test in this file performs a network call: every request is answered by an
``httpx.MockTransport``. What is being checked is the part that is easy to get
wrong - error translation, strict parsing, exactly-scoped writes, and the fact
that no credential ever reaches a log record.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from meobot.core.errors import (
    IntegrationAuthError,
    IntegrationNotConfiguredError,
    LLMError,
    NotFoundError,
)
from meobot.core.logging import REDACTED, JsonFormatter, RedactingFilter
from meobot.domain.identity.models import Role
from meobot.domain.policy.models import RiskLevel
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.google.credentials import GoogleCredentials
from meobot.integrations.google.sheets import (
    CellUpdate,
    GoogleSheetsClient,
    NotConfiguredSheetsClient,
    SheetRange,
    column_letter,
)
from meobot.integrations.llm.base import PlanningRequest, StructuredRequest, ToolSummary
from meobot.integrations.llm.openai_compatible import OpenAICompatibleProvider

TOKEN = "ya29.super-secret-google-access-token"
API_KEY = "sk-test-not-a-real-key-000111222"

FAST = IntegrationConfig(provider="test", timeout_seconds=2.0, max_retries=0)


class StubCredentials(GoogleCredentials):
    """Credentials that mint a fixed token without touching the filesystem."""

    def __init__(self) -> None:
        super().__init__("/nonexistent/key.json")

    async def token(self) -> str:
        return TOKEN


def sheets_client(handler: Any) -> GoogleSheetsClient:
    transport = httpx.MockTransport(handler)
    return GoogleSheetsClient(
        StubCredentials(),
        config=FAST,
        client=httpx.AsyncClient(transport=transport),
    )


# --- Google Sheets ----------------------------------------------------------
def test_column_letters_follow_spreadsheet_numbering() -> None:
    assert column_letter(0) == "A"
    assert column_letter(25) == "Z"
    assert column_letter(26) == "AA"
    assert column_letter(27) == "AB"


async def test_metadata_lists_worksheets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(
            200,
            json={
                "properties": {"title": "Kịch bản tháng 8"},
                "sheets": [
                    {"properties": {"title": "Tuần 1", "sheetId": 0}},
                    {"properties": {"title": "Tuần 2", "sheetId": 7}},
                ],
            },
        )

    metadata = await sheets_client(handler).get_metadata("sheet-1")
    assert metadata.title == "Kịch bản tháng 8"
    assert metadata.worksheet_names == ["Tuần 1", "Tuần 2"]


async def test_rows_are_keyed_by_header_and_padded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/values/" not in str(request.url):
            return httpx.Response(200, json={})
        if "1:1" in str(request.url):
            return httpx.Response(200, json={"values": [["ID", "Tiêu đề", "Nội dung"]]})
        # The second row is short: Sheets truncates trailing empty cells.
        return httpx.Response(200, json={"values": [["A1", "Tiêu đề 1", "Body 1"], ["A2"]]})

    rows = await sheets_client(handler).read_rows(SheetRange("sheet-1", "Tuần 1"))
    assert rows[0] == {"ID": "A1", "Tiêu đề": "Tiêu đề 1", "Nội dung": "Body 1"}
    assert rows[1] == {"ID": "A2", "Tiêu đề": "", "Nội dung": ""}


async def test_batch_update_writes_only_the_named_cells() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"totalUpdatedCells": 2})

    written = await sheets_client(handler).batch_update(
        "sheet-1",
        [
            CellUpdate(sheet_name="Tuần 1", row_number=5, column_index=2, value="duyệt"),
            CellUpdate(sheet_name="Tuần 1", row_number=5, column_index=3, value="88"),
        ],
    )

    assert written == 2
    ranges = [entry["range"] for entry in captured["data"]]
    # One range per cell: no row-wide write that could clobber a neighbour.
    assert ranges == ["'Tuần 1'!C5", "'Tuần 1'!D5"]
    assert all(
        len(entry["values"]) == 1 and len(entry["values"][0]) == 1 for entry in captured["data"]
    )


async def test_permission_error_says_what_to_do() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"message": "The caller does not have permission"}}
        )

    with pytest.raises(IntegrationAuthError, match="service account"):
        await sheets_client(handler).get_metadata("sheet-1")


async def test_missing_sheet_becomes_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Requested entity was not found"}})

    with pytest.raises(NotFoundError):
        await sheets_client(handler).get_metadata("sheet-gone")


async def test_renamed_tab_becomes_not_found() -> None:
    """Sheets answers 400 'Unable to parse range' when a tab was renamed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Unable to parse range: 'Old'!1:1"}})

    with pytest.raises(NotFoundError, match="đổi tên"):
        await sheets_client(handler).read_headers(SheetRange("sheet-1", "Old"))


async def test_not_configured_client_fails_loudly_on_every_call() -> None:
    client = NotConfiguredSheetsClient()
    with pytest.raises(IntegrationNotConfiguredError):
        await client.get_metadata("sheet-1")
    with pytest.raises(IntegrationNotConfiguredError):
        await client.read_rows(SheetRange("sheet-1", "Tab"))
    with pytest.raises(IntegrationNotConfiguredError):
        await client.batch_update("sheet-1", [])


async def test_missing_credential_file_is_reported_without_leaking() -> None:
    credentials = GoogleCredentials("/nonexistent/service-account.json")
    with pytest.raises(IntegrationNotConfiguredError, match="not found"):
        await credentials.token()


def test_unconfigured_credentials_report_it_without_touching_disk() -> None:
    assert GoogleCredentials(None).configured is False
    assert GoogleCredentials("  ").configured is False


# --- LLM provider -----------------------------------------------------------
def llm_provider(handler: Any) -> OpenAICompatibleProvider:
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleProvider(
        api_key=API_KEY,
        model="test-model-v1",
        config=FAST,
        client=httpx.AsyncClient(transport=transport),
    )


def chat_response(payload: dict[str, Any], **extra: Any) -> httpx.Response:
    body = {
        "model": "test-model-v1",
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        **extra,
    }
    return httpx.Response(200, json=body)


async def test_structured_response_is_parsed_with_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        sent = json.loads(request.content)
        assert sent["model"] == "test-model-v1"
        assert sent["response_format"]["type"] == "json_schema"
        return chat_response(
            {"overall_score": 82, "verdict": "approve"},
            usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        )

    response = await llm_provider(handler).complete_structured(
        StructuredRequest(
            task="script_review",
            system_prompt="system",
            user_prompt="user",
            json_schema={"type": "object"},
        )
    )

    assert response.payload["overall_score"] == 82
    assert response.model == "test-model-v1"
    assert response.usage["total_tokens"] == 160


async def test_non_json_answer_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "sorry, here is prose"}}]}
        )

    with pytest.raises(LLMError, match="JSON"):
        await llm_provider(handler).complete_structured(
            StructuredRequest(task="t", system_prompt="s", user_prompt="u")
        )


async def test_refusal_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"refusal": "no"}}]})

    with pytest.raises(LLMError, match="từ chối"):
        await llm_provider(handler).complete_structured(
            StructuredRequest(task="t", system_prompt="s", user_prompt="u")
        )


async def test_bad_api_key_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(LLMError):
        await llm_provider(handler).complete_structured(
            StructuredRequest(task="t", system_prompt="s", user_prompt="u")
        )


async def test_plan_refuses_a_tool_the_model_invented() -> None:
    """A hallucinated tool never reaches the policy engine as a real plan."""

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            {
                "intent": "delete_everything",
                "tool_name": "data.delete_all",
                "arguments": {},
                "risk_level": "low",
                "confidence": 0.99,
                "reasoning": None,
            }
        )

    plan = await llm_provider(handler).plan(
        PlanningRequest(
            message="xoá hết dữ liệu",
            actor_role=Role.OWNER,
            available_tools=[
                ToolSummary(name="system.health", description="", risk_level=RiskLevel.LOW)
            ],
        )
    )
    assert plan.is_unknown
    assert plan.tool_name is None


async def test_plan_degrades_to_unknown_when_the_provider_fails() -> None:
    """A Telegram message must get an answer even when the provider is down."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    plan = await llm_provider(handler).plan(
        PlanningRequest(message="kiểm tra hệ thống", actor_role=Role.OWNER)
    )
    assert plan.is_unknown


async def test_valid_plan_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            {
                "intent": "check_health",
                "tool_name": "system.health",
                "arguments": {},
                "risk_level": "low",
                "confidence": 0.9,
                "reasoning": "user asked about the system",
            }
        )

    plan = await llm_provider(handler).plan(
        PlanningRequest(
            message="hệ thống ổn không",
            actor_role=Role.OWNER,
            available_tools=[
                ToolSummary(name="system.health", description="", risk_level=RiskLevel.LOW)
            ],
        )
    )
    assert plan.tool_name == "system.health"


def test_missing_key_or_model_is_refused_at_construction() -> None:
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        OpenAICompatibleProvider(api_key="", model="m")
    with pytest.raises(LLMError, match="LLM_MODEL"):
        OpenAICompatibleProvider(api_key=API_KEY, model="")


# --- Credential leakage -----------------------------------------------------
def test_secrets_never_reach_a_log_record() -> None:
    """The redacting filter is what stands between a token and `docker logs`.

    The record is built and filtered directly, then rendered through the very
    formatter the containers use - so this asserts on the bytes that would be
    written to stdout, not on an intermediate object.
    """
    record = logging.LogRecord(
        name="meobot.integrations.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=f"calling google with Bearer {TOKEN}",
        args=None,
        exc_info=None,
    )
    record.api_key = API_KEY
    record.access_token = TOKEN
    record.authorization = f"Bearer {TOKEN}"
    record.private_key = "-----BEGIN PRIVATE KEY-----abc"
    record.credentials = {"client_email": "bot@project.iam.gserviceaccount.com"}

    assert RedactingFilter().filter(record) is True
    rendered = JsonFormatter("test").format(record)

    assert API_KEY not in rendered
    assert TOKEN not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered
    assert REDACTED in rendered
