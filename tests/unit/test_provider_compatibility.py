"""Regression tests for the outage: an OpenAI-compatible endpoint that isn't.

Every test here drives the real provider through an ``httpx.MockTransport``.
No test performs a network call and no test needs an API key that works.

The failure being reproduced: the deployed model rejected ``max_tokens`` (it
wants ``max_completion_tokens``) and rejected any ``temperature`` other than the
default. Both are HTTP 400. Because every task went through one
structured-output call, both made *every* natural message fail while every slash
command kept working.

What is asserted, in order of importance:

1. those two 400s are recovered from, not surfaced;
2. plain-text chat keeps working when structured output does not;
3. the remaining HTTP statuses are classified into categories an operator can
   act on;
4. nothing secret reaches a log record.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest

from meobot.core.errors import LLMError
from meobot.core.logging import REDACTED, JsonFormatter, RedactingFilter
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.llm.base import (
    ChatRequest,
    RouteRequest,
    StructuredRequest,
)
from meobot.integrations.llm.capabilities import (
    MAX_COMPLETION_TOKENS,
    MAX_TOKENS,
    OutputMode,
    ProviderCapabilities,
)
from meobot.integrations.llm.diagnostics import ErrorCategory
from meobot.integrations.llm.openai_compatible import OpenAICompatibleProvider

API_KEY = "sk-test-not-a-real-key-000111222"
MODEL = "configured-model-v9"

FAST = IntegrationConfig(provider="test", timeout_seconds=2.0, max_retries=0)


def provider(handler: Any, **kwargs: Any) -> OpenAICompatibleProvider:
    """A provider whose transport is a function, not a network."""
    return OpenAICompatibleProvider(
        api_key=API_KEY,
        model=MODEL,
        config=FAST,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def chat_response(content: str, *, usage: dict[str, int] | None = None) -> httpx.Response:
    body: dict[str, Any] = {
        "model": MODEL,
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }
    if usage:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def error_response(
    status: int, *, code: str | None = None, param: str | None = None, message: str = ""
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": code,
                "param": param,
            }
        },
    )


# --- The two parameters that caused the outage -----------------------------
async def test_max_tokens_rejection_is_recovered_by_renaming_the_parameter() -> None:
    """The exact 400 the deployed model returned, and the exact recovery.

    ``Unsupported parameter: 'max_tokens' is not supported with this model. Use
    'max_completion_tokens' instead.`` Before this fix that 400 became an
    ``LLMError`` and the user was told to write "Hello" more concisely.
    """
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if MAX_TOKENS in body:
            return error_response(
                400,
                code="unsupported_parameter",
                param=MAX_TOKENS,
                message="Unsupported parameter: 'max_tokens' is not supported with this "
                "model. Use 'max_completion_tokens' instead.",
            )
        return chat_response("Chào bạn, mình là TasksBot.")

    client = provider(handler)
    reply = await client.generate_chat_reply(ChatRequest(message="Hello"))

    assert reply.text == "Chào bạn, mình là TasksBot."
    assert MAX_TOKENS in sent[0], "the first attempt should use the common spelling"
    assert MAX_COMPLETION_TOKENS in sent[1], "the retry should use the name the model asked for"
    assert client.capabilities.max_tokens_parameter == MAX_COMPLETION_TOKENS


async def test_the_renamed_parameter_sticks_for_later_calls() -> None:
    """One wasted round-trip after start-up, not one per message."""
    attempts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        attempts.append(body)
        if MAX_TOKENS in body:
            return error_response(400, code="unsupported_parameter", param=MAX_TOKENS)
        return chat_response("ok")

    client = provider(handler)
    await client.generate_chat_reply(ChatRequest(message="một"))
    await client.generate_chat_reply(ChatRequest(message="hai"))

    rejected = [body for body in attempts if MAX_TOKENS in body]
    assert len(rejected) == 1, "the downgrade was not remembered"


async def test_unsupported_temperature_is_dropped_not_surfaced() -> None:
    """The second 400 the deployed model returned, recovered the same way."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if "temperature" in body:
            return error_response(
                400,
                code="unsupported_value",
                param="temperature",
                message="Unsupported value: 'temperature' does not support 0.7 with this "
                "model. Only the default (1) value is supported.",
            )
        return chat_response("Chào bạn.")

    client = provider(handler)
    reply = await client.generate_chat_reply(ChatRequest(message="Hello"))

    assert reply.text == "Chào bạn."
    assert "temperature" in sent[0]
    assert "temperature" not in sent[-1]
    assert client.capabilities.supports_temperature is False


async def test_both_rejections_at_once_still_produce_an_answer() -> None:
    """The real deployment refused both parameters, on every single call."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if MAX_TOKENS in body:
            return error_response(400, code="unsupported_parameter", param=MAX_TOKENS)
        if "temperature" in body:
            return error_response(400, code="unsupported_value", param="temperature")
        return chat_response("Chào bạn, mình là TasksBot.")

    reply = await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))
    assert "TasksBot" in reply.text


async def test_a_400_we_cannot_fix_is_still_reported() -> None:
    """Recovery is for parameters we know how to drop, not for every 400."""

    def handler(request: httpx.Request) -> httpx.Response:
        return error_response(400, code="context_length_exceeded", param="messages")

    with pytest.raises(LLMError, match="400"):
        await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))


# --- Structured output that is not supported -------------------------------
async def test_plain_text_chat_works_when_json_schema_is_unsupported() -> None:
    """The property the whole redesign exists for.

    A gateway with no ``response_format`` support breaks routing. It must not
    break conversation, because conversation never asks for one.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "response_format" in body:
            return error_response(
                400,
                code="unsupported_parameter",
                param="response_format",
                message="response_format is not supported",
            )
        return chat_response("Chào bạn, hôm nay bạn muốn bắt đầu từ đâu?")

    reply = await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))
    assert "Chào bạn" in reply.text


async def test_routing_degrades_from_schema_to_json_mode_to_plain_text() -> None:
    """Three modes, tried strongest first, and the answer still parses."""
    modes: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_format = body.get("response_format")
        modes.append(response_format["type"] if response_format else None)
        if response_format is not None:
            return error_response(
                400,
                code="unsupported_parameter",
                param="response_format",
                message="response_format is not supported by this model",
            )
        # Plain text carrying strict JSON, parsed locally.
        return chat_response('```json\n{"mode": "chat", "confidence": 0.8}\n```')

    client = provider(handler)
    route = await client.route_message(RouteRequest(message="Hôm nay thế nào?"))

    assert route.mode.value == "chat"
    assert modes == ["json_schema", "json_object", None]
    assert client.capabilities.supports_json_schema is False
    assert client.capabilities.supports_json_mode is False
    # And plain text is untouched, which is what keeps chat alive.
    assert client.capabilities.supports_plain_text is True


async def test_json_embedded_in_prose_is_still_parsed() -> None:
    """A model that will not stop explaining itself is not a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response('Đây là kết quả:\n{"mode": "clarify"}\nHy vọng giúp được bạn.')

    client = provider(
        handler,
        capabilities=ProviderCapabilities(supports_json_schema=False, supports_json_mode=False),
    )
    route = await client.route_message(RouteRequest(message="duyệt đi"))
    assert route.mode.value == "clarify"


async def test_unparsable_routing_raises_rather_than_guessing() -> None:
    """A route that cannot be read must never become a tool call.

    The caller's fallback decides what to do; inventing a mode here would put a
    parse failure on the execution path.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response("Mình nghĩ bạn muốn đồng bộ Sheet.")

    client = provider(handler)
    with pytest.raises(LLMError):
        await client.route_message(RouteRequest(message="làm gì đó đi"))


async def test_a_routed_tool_name_outside_the_catalogue_is_dropped() -> None:
    """The model may not introduce a tool by naming one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(json.dumps({"mode": "tool", "possible_tool_name": "drive.delete_all"}))

    route = await provider(handler).route_message(
        RouteRequest(message="xoá hết", tool_names=["system.health"])
    )
    assert route.possible_tool_name is None


# --- HTTP status classification -------------------------------------------
async def test_401_is_reported_as_a_configuration_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})

    client = provider(handler)
    with pytest.raises(LLMError, match="API key"):
        await client.generate_chat_reply(ChatRequest(message="Hello"))
    assert client.diagnostics.last_error_category == ErrorCategory.AUTH


async def test_404_points_at_the_model_or_the_base_url() -> None:
    """The two things actually worth checking when a 404 comes back."""

    def handler(request: httpx.Request) -> httpx.Response:
        return error_response(404, code="model_not_found", message="The model does not exist")

    client = provider(handler)
    with pytest.raises(LLMError, match="LLM_MODEL"):
        await client.generate_chat_reply(ChatRequest(message="Hello"))
    assert client.diagnostics.last_error_category == ErrorCategory.MODEL_OR_ENDPOINT_NOT_FOUND


async def test_timeout_retries_once_then_reports_a_timeout() -> None:
    """A slow provider is retried once, then reported as slow - not as broken."""
    attempts = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        await asyncio.sleep(1.0)  # longer than the 0.2s budget below
        return chat_response("too late")

    client = OpenAICompatibleProvider(
        api_key=API_KEY,
        model=MODEL,
        config=IntegrationConfig(provider="test", timeout_seconds=0.2, max_retries=1),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LLMError, match="kịp thời"):
        await client.generate_chat_reply(ChatRequest(message="Hello"))

    assert attempts["count"] == 2, "one retry, not zero and not five"
    assert client.diagnostics.last_error_category == ErrorCategory.TIMEOUT


async def test_a_refusal_is_not_mistaken_for_an_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "refusal": "no"}}]}
        )

    with pytest.raises(LLMError):
        await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))


async def test_empty_content_is_a_failure_not_an_empty_reply() -> None:
    """An empty answer must reach the caller's fallback, not the user."""

    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response("   ")

    client = provider(handler)
    with pytest.raises(LLMError):
        await client.generate_chat_reply(ChatRequest(message="Hello"))
    assert client.diagnostics.last_error_category == ErrorCategory.EMPTY_RESPONSE


async def test_an_answer_truncated_to_nothing_buys_a_bigger_budget() -> None:
    """A model that thinks before it answers can spend the whole budget.

    Observed live: ``finish_reason="length"`` with empty content. That is a
    budget problem, not an outage - treating it as an empty answer would send a
    perfectly healthy provider down the fallback path.
    """
    budgets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        budget = body.get(MAX_TOKENS) or body.get(MAX_COMPLETION_TOKENS)
        budgets.append(budget)
        if budget < 500:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            )
        return chat_response("Bạn muốn duyệt kịch bản nào?")

    reply = await provider(handler).generate_chat_reply(
        ChatRequest(message="duyệt đi", max_output_tokens=200)
    )

    assert reply.text == "Bạn muốn duyệt kịch bản nào?"
    assert budgets[0] < budgets[-1], "the budget was not raised"


async def test_a_persistently_empty_answer_still_gives_up() -> None:
    """The escalation is bounded: a broken model cannot spend forever."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        )

    with pytest.raises(LLMError):
        await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))
    assert calls["count"] <= 4, "the budget escalation is unbounded"


async def test_an_empty_answer_that_was_not_truncated_is_not_retried() -> None:
    """Only ``finish_reason="length"`` means "give me more room"."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        )

    with pytest.raises(LLMError):
        await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))
    assert calls["count"] == 1


# --- Prompt shape ----------------------------------------------------------
async def test_chat_generation_sends_no_tools_and_no_schema() -> None:
    """The structural reason a chat turn cannot execute anything."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return chat_response("Chào bạn.")

    await provider(handler).generate_chat_reply(
        ChatRequest(message="Hello", prompt_context="[ASSISTANT IDENTITY]\nTên: TasksBot")
    )

    assert "tools" not in captured
    assert "tool_choice" not in captured
    assert "response_format" not in captured
    assert "functions" not in captured


async def test_the_minimal_retry_keeps_identity_and_drops_the_rest() -> None:
    """A smaller prompt, not a different assistant."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return chat_response("Chào bạn.")

    context = (
        "[ASSISTANT IDENTITY]\nTên: TasksBot\n\n"
        "[CURRENT USER]\nXưng hô: gọi người dùng là 'anh'\n\n"
        "[AVAILABLE CAPABILITIES]\nrất nhiều dòng dài\n\n"
        "[RECENT MESSAGES]\nNgười dùng: ...\n"
    )
    await provider(handler).generate_chat_reply(
        ChatRequest(message="Hello", prompt_context=context, minimal=True)
    )

    system_text = "\n".join(
        part["content"] for part in captured["messages"] if part["role"] == "system"
    )
    assert "[ASSISTANT IDENTITY]" in system_text
    assert "[CURRENT USER]" in system_text
    assert "[AVAILABLE CAPABILITIES]" not in system_text


async def test_usage_is_reported_when_the_provider_sends_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response(
            json.dumps({"summary": "ok"}),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    response = await provider(handler).complete_structured(
        StructuredRequest(task="t", system_prompt="s", user_prompt="u")
    )
    assert response.usage["total_tokens"] == 15


# --- Diagnostics -----------------------------------------------------------
async def test_a_successful_call_records_latency_for_chat_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response("ok")

    client = provider(handler)
    await client.generate_chat_reply(ChatRequest(message="Hello"))

    assert client.diagnostics.has_succeeded
    assert client.diagnostics.last_latency_ms is not None
    assert client.diagnostics.last_error_category is None


# --- Nothing secret is logged ---------------------------------------------
async def test_no_secret_and_no_response_body_reaches_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The API key, the Authorization header and the answer stay out of logs.

    Asserted on the *formatted* record, because that is what a log shipper
    stores - and the redacting filter only helps if the value was going to be
    written in the first place.
    """
    private_answer = "Kịch bản bí mật của khách hàng ABC"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        return chat_response(private_answer)

    formatter = JsonFormatter("test")
    with caplog.at_level(logging.DEBUG):
        caplog.handler.addFilter(RedactingFilter())
        await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))

    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert API_KEY not in rendered
    assert "Bearer" not in rendered
    assert private_answer not in rendered
    # And the diagnostics that *are* wanted did get written.
    assert "generate_chat_reply" in rendered


async def test_an_error_body_is_classified_but_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A provider may echo the prompt in ``error.message``. We read it, we do
    not write it."""
    echoed = "your prompt was: Kịch bản bí mật"

    def handler(request: httpx.Request) -> httpx.Response:
        return error_response(400, code="context_length_exceeded", message=echoed)

    formatter = JsonFormatter("test")
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError):
        caplog.handler.addFilter(RedactingFilter())
        await provider(handler).generate_chat_reply(ChatRequest(message="Hello"))

    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert echoed not in rendered
    assert "context_length_exceeded" in rendered, "the code is what an operator needs"


def test_redacting_filter_still_masks_a_key_that_slips_through() -> None:
    """Belt and braces: the filter is the last line, not the only one.

    Nothing in the provider logs the key. If something ever starts to, passing
    it as an ``extra`` under a sensitive name is the likeliest shape - and that
    is the shape the filter catches.
    """
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling provider",
        args=None,
        exc_info=None,
    )
    record.llm_api_key = API_KEY
    record.authorization = f"Bearer {API_KEY}"

    assert RedactingFilter().filter(record) is True
    rendered = JsonFormatter("test").format(record)
    assert API_KEY not in rendered
    assert REDACTED in rendered


# --- Capability negotiation, in isolation ---------------------------------
def test_capabilities_fall_back_through_the_documented_order() -> None:
    caps = ProviderCapabilities()
    assert caps.best_mode(OutputMode.JSON_SCHEMA) is OutputMode.JSON_SCHEMA
    assert caps.weaken(OutputMode.JSON_SCHEMA) is OutputMode.JSON_OBJECT
    assert caps.weaken(OutputMode.JSON_OBJECT) is OutputMode.TEXT
    assert caps.best_mode(OutputMode.JSON_SCHEMA) is OutputMode.TEXT
    assert caps.supports_plain_text is True


def test_capabilities_ignore_a_400_that_is_not_about_a_parameter() -> None:
    """Only a knob we know how to drop counts as an adjustment."""
    caps = ProviderCapabilities()
    assert caps.adjust_for(code="content_filter", param=None, message="blocked") is None
    assert caps.downgrades == []


def test_capabilities_recognise_the_message_when_param_is_missing() -> None:
    """Some gateways describe the problem without filling in ``param``."""
    caps = ProviderCapabilities()
    label = caps.adjust_for(
        code="invalid_request_error",
        param=None,
        message="Unsupported parameter: 'max_tokens' is not supported. Use "
        "'max_completion_tokens' instead.",
    )
    assert label is not None
    assert caps.max_tokens_parameter == MAX_COMPLETION_TOKENS
