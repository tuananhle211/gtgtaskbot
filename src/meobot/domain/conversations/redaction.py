"""Secret redaction applied before any conversation content is stored.

Conversation history is the one place in MeoBot where arbitrary user-typed text
becomes durable. People paste credentials into chat windows - a service-account
JSON, a bot token, a DSN with the password still in it - and a database row
outlives the mistake.

Everything written to ``conversation_messages`` passes through
:func:`redact_secrets` first. It is deliberately aggressive: a false positive
costs a few characters of a stored message, a false negative costs a
credential.

What is never stored at all, regardless of redaction, is enforced by the
callers: provider credentials, complete service-account JSON, raw authorization
headers, and any private chain-of-thought (which MeoBot never requests - see
:mod:`meobot.domain.conversations.decision`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: What replaces a match. Short and obvious in a transcript.
PLACEHOLDER = "[đã ẩn]"

#: Longest single message we keep. Anything past this is a paste, not a
#: conversation, and storing it buys nothing.
MAX_STORED_CHARS = 4000

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # PEM private keys, including the escaped \n form found in JSON key files.
    (
        "private_key",
        re.compile(
            r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    ("private_key_escaped", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----(?:\\n|[^\"'])*")),
    # Telegram bot tokens: <digits>:<35 base64url chars>.
    ("telegram_token", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")),
    # OpenAI-compatible keys and the common vendor prefixes.
    ("api_key", re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b")),
    ("vendor_key", re.compile(r"\b(?:ghp|gho|ghs|xoxb|xoxp|AIza)[A-Za-z0-9_-]{16,}\b")),
    # Google OAuth access / refresh tokens.
    ("google_token", re.compile(r"\bya29\.[A-Za-z0-9_-]{10,}\b")),
    ("refresh_token", re.compile(r"\b1//[A-Za-z0-9_-]{20,}\b")),
    # JWTs (a service-account assertion, an id_token, a session cookie).
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # DSNs that carry a password.
    (
        "dsn_password",
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@", re.IGNORECASE),
    ),
    # Authorization headers, however they are spelled.
    (
        "authorization_header",
        re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+|basic\s+)?\S+"),
    ),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    # ``API_KEY=...`` / ``"client_secret": "..."`` style assignments.
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:token|secret|password|api[_-]?key|passwd|credential)"
            r"[A-Z0-9_]*)\s*[:=]\s*[\"']?([^\s\"',}]{6,})[\"']?"
        ),
    ),
)

#: A pasted service-account file is recognisable before any single pattern
#: fires. When these appear together the whole message is dropped.
_SERVICE_ACCOUNT_MARKERS: tuple[str, ...] = (
    '"type": "service_account"',
    '"type":"service_account"',
    "private_key_id",
)


def looks_like_credential_file(text: str) -> bool:
    """True when the message looks like a complete credential document.

    Such a message is never stored, redacted or otherwise: the surrounding
    fields (project id, client email, key id) are themselves sensitive and a
    partially redacted key file is still a key file.
    """
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in _SERVICE_ACCOUNT_MARKERS)


def redact_secrets(text: str) -> str:
    """Replace anything that looks like a credential in ``text``.

    Returns:
        The text with every match replaced by :data:`PLACEHOLDER`. Vietnamese
        content, links and ordinary punctuation are untouched.
    """
    if not text:
        return text

    redacted = text
    for name, pattern in _PATTERNS:
        if name == "dsn_password":
            redacted = pattern.sub(rf"\1:{PLACEHOLDER}@", redacted)
        elif name == "secret_assignment":
            redacted = pattern.sub(rf"\1={PLACEHOLDER}", redacted)
        else:
            redacted = pattern.sub(PLACEHOLDER, redacted)
    return redacted


def prepare_for_storage(text: str) -> str | None:
    """Redact and bound one message before it is persisted.

    Returns:
        The storable text, or ``None`` when the message must not be stored at
        all (a pasted credential file, or nothing but whitespace).
    """
    if not text or not text.strip():
        return None
    if looks_like_credential_file(text):
        return None
    cleaned = redact_secrets(text).strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_STORED_CHARS:
        cleaned = cleaned[:MAX_STORED_CHARS] + " …"
    return cleaned


def redact_all(texts: Iterable[str]) -> list[str]:
    """Redact every item, dropping the ones that must not be stored."""
    return [cleaned for text in texts if (cleaned := prepare_for_storage(text)) is not None]


def estimate_tokens(text: str) -> int:
    """Rough token count used to bound the prompt.

    Vietnamese runs about three characters per token on the tokenizers MeoBot
    talks to. This is a budget guard, not accounting - it only has to be in the
    right order of magnitude.
    """
    return max(1, len(text) // 3)
