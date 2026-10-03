"""Provider health as observed from inside MeoBot.

``/chat_status`` used to report configuration - the model name, whether the
provider was fake - and nothing about whether the provider had ever actually
answered. During the outage that motivated this module, ``/chat_status`` looked
entirely healthy while every message failed.

What is recorded here is deliberately narrow, because it is shown to a user in
Telegram:

* **categories, not bodies.** ``http_400`` is recorded; the provider's response
  body, which can echo the prompt, never is.
* **timings and counts**, which say whether the provider is slow or broken.
* **no credential-derived value at all.** Not the key, not a fingerprint of it,
  not the Authorization header, not a URL with a query string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from meobot.core.time import utcnow


class ErrorCategory:
    """Log-safe classification of a provider failure.

    These strings appear in ``/chat_status`` and in logs, so they are stable
    identifiers rather than sentences.
    """

    TIMEOUT = "timeout"
    AUTH = "auth_error"
    RATE_LIMIT = "rate_limited"
    BAD_REQUEST = "bad_request"
    MODEL_OR_ENDPOINT_NOT_FOUND = "model_or_endpoint_not_found"
    SERVER_ERROR = "provider_server_error"
    NETWORK = "network_error"
    EMPTY_RESPONSE = "empty_response"
    REFUSAL = "refusal"
    PARSE_FAILED = "parse_failed"
    SCHEMA_NOT_SUPPORTED = "schema_not_supported"
    UNKNOWN = "unknown_error"

    #: Human-readable hint per category, shown by ``/chat_status`` and
    #: ``/chat_test``. Written for the operator, not for an end user.
    HINTS: ClassVar[dict[str, str]] = {
        TIMEOUT: "Mô hình trả lời chậm hơn LLM_TIMEOUT_SECONDS.",
        AUTH: "LLM_API_KEY sai hoặc hết hạn.",
        RATE_LIMIT: "Provider đang giới hạn tốc độ hoặc quá tải.",
        BAD_REQUEST: "Provider từ chối tham số của yêu cầu (kiểm tra LLM_MODEL).",
        MODEL_OR_ENDPOINT_NOT_FOUND: "Sai LLM_MODEL hoặc sai LLM_BASE_URL.",
        SERVER_ERROR: "Lỗi phía provider, thường tự hết.",
        NETWORK: "Không kết nối được tới provider từ container này.",
        EMPTY_RESPONSE: "Provider trả lời rỗng.",
        REFUSAL: "Mô hình từ chối trả lời nội dung này.",
        PARSE_FAILED: "Mô hình trả về dữ liệu không đúng định dạng.",
        SCHEMA_NOT_SUPPORTED: "Mô hình không hỗ trợ structured output; đã chuyển sang chế độ khác.",
        UNKNOWN: "Lỗi chưa phân loại.",
    }

    @classmethod
    def hint(cls, category: str | None) -> str:
        return cls.HINTS.get(category or "", cls.HINTS[cls.UNKNOWN])


@dataclass(slots=True)
class ProviderDiagnostics:
    """Rolling, in-memory view of the last few provider calls.

    Not persisted: this answers "is the model answering *right now*", and a
    value that survived a restart would answer a different question. The audit
    log remains the durable record.
    """

    last_success_at: datetime | None = None
    last_success_task: str | None = None
    last_latency_ms: int | None = None
    last_error_at: datetime | None = None
    last_error_category: str | None = None
    last_error_task: str | None = None
    success_count: int = 0
    error_count: int = 0
    #: Adjustments the capability negotiation made, newest last.
    downgrades: list[str] = field(default_factory=list)

    def record_success(self, *, task: str, latency_ms: int) -> None:
        self.last_success_at = utcnow()
        self.last_success_task = task
        self.last_latency_ms = latency_ms
        self.success_count += 1

    def record_error(self, *, task: str, category: str) -> None:
        self.last_error_at = utcnow()
        self.last_error_task = task
        self.last_error_category = category
        self.error_count += 1

    def record_downgrade(self, label: str) -> None:
        if label not in self.downgrades:
            self.downgrades.append(label)

    @property
    def has_succeeded(self) -> bool:
        return self.last_success_at is not None

    def as_dict(self) -> dict[str, object]:
        """Log-safe snapshot. Contains no secret and no response content."""
        return {
            "last_success_at": (self.last_success_at.isoformat() if self.last_success_at else None),
            "last_latency_ms": self.last_latency_ms,
            "last_error_at": (self.last_error_at.isoformat() if self.last_error_at else None),
            "last_error_category": self.last_error_category,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "downgrades": list(self.downgrades),
        }
