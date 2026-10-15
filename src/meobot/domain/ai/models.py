"""AI connections ("Kết nối AI", 0064): the AI services the app may call.

The OWNER registers connections - ChatGPT (OpenAI), Gemini, DeepSeek, or any
OpenAI-compatible gateway such as a self-hosted 9router - each with its API
key and model, and decides which one serves which **purpose**, in order: the
first that answers wins, the others are its fallbacks. Every one of them is
reached through the same OpenAI-compatible client
(:mod:`meobot.integrations.llm.openai_compatible`); a kind only fills in the
endpoint and says what to call it.

With nothing configured here the app keeps using the ``LLM_*`` environment
provider, which is also the last fallback after the configured ones (unless
it is the offline ``fake`` one).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class AiProviderKind(StrEnum):
    OPENAI = "OPENAI"
    GEMINI = "GEMINI"
    DEEPSEEK = "DEEPSEEK"
    #: Any endpoint speaking ``POST /chat/completions``: 9router, OpenRouter,
    #: a self-hosted vLLM... Its base URL is required.
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


@dataclass(frozen=True, slots=True)
class ProviderKindInfo:
    label: str
    #: Filled in when the connection gives none; None = the URL is required.
    base_url: str | None
    model_hint: str
    key_hint: str


PROVIDER_KINDS: MappingProxyType[AiProviderKind, ProviderKindInfo] = MappingProxyType(
    {
        AiProviderKind.OPENAI: ProviderKindInfo(
            label="ChatGPT (OpenAI)",
            base_url="https://api.openai.com/v1",
            model_hint="gpt-4o-mini",
            key_hint=(
                "API key tạo ở platform.openai.com (nạp credit riêng, không phải gói ChatGPT Plus)."
            ),
        ),
        AiProviderKind.GEMINI: ProviderKindInfo(
            label="Gemini (Google)",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model_hint="gemini-2.5-flash",
            key_hint="API key tạo ở aistudio.google.com.",
        ),
        AiProviderKind.DEEPSEEK: ProviderKindInfo(
            label="DeepSeek",
            base_url="https://api.deepseek.com",
            model_hint="deepseek-chat",
            key_hint="API key tạo ở platform.deepseek.com.",
        ),
        AiProviderKind.OPENAI_COMPATIBLE: ProviderKindInfo(
            label="9router / chuẩn OpenAI khác",
            base_url=None,
            model_hint="cx/gpt-5.5 hoặc tên combo",
            key_hint="API key tạo trong dashboard của 9router (bật REQUIRE_API_KEY).",
        ),
    }
)


class AiPurpose(StrEnum):
    """What a call is for. ``DEFAULT`` serves every purpose that has no
    route of its own."""

    DEFAULT = "DEFAULT"
    #: "AI duyệt kịch bản" of an order (task ``order_script_review``).
    ORDER_SCRIPT = "ORDER_SCRIPT"
    #: The PR module's AI review of a content draft and of a script.
    PR_REVIEW = "PR_REVIEW"
    #: The Telegram assistant: routing, chat, planning, summaries.
    ASSISTANT = "ASSISTANT"


PURPOSE_LABELS: MappingProxyType[AiPurpose, str] = MappingProxyType(
    {
        AiPurpose.DEFAULT: "Mặc định (mọi việc chưa chọn riêng)",
        AiPurpose.ORDER_SCRIPT: "AI duyệt kịch bản order",
        AiPurpose.PR_REVIEW: "AI duyệt nội dung PR",
        AiPurpose.ASSISTANT: "Trợ lý Telegram",
    }
)

#: How many connections one purpose may chain (the first + its fallbacks).
MAX_CHAIN = 3

#: Structured tasks with a purpose of their own; any other task is DEFAULT.
STRUCTURED_TASK_PURPOSES: MappingProxyType[str, AiPurpose] = MappingProxyType(
    {
        "order_script_review": AiPurpose.ORDER_SCRIPT,
        "script_review": AiPurpose.PR_REVIEW,
        "pr_full_review": AiPurpose.PR_REVIEW,
    }
)


def purpose_of_task(task: str) -> AiPurpose:
    return STRUCTURED_TASK_PURPOSES.get(task, AiPurpose.DEFAULT)


def key_hint(api_key: str) -> str:
    """What the screen shows of a key: its last four characters."""
    tail = api_key.strip()[-4:]
    return f"••••{tail}" if tail else ""


__all__ = [
    "MAX_CHAIN",
    "PROVIDER_KINDS",
    "PURPOSE_LABELS",
    "STRUCTURED_TASK_PURPOSES",
    "AiProviderKind",
    "AiPurpose",
    "ProviderKindInfo",
    "key_hint",
    "purpose_of_task",
]
