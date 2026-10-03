"""Resolving "kịch bản này", "ý tưởng số 3", "Sheet đó".

Vietnamese conversation is full of pointers, and a pointer is only useful if
resolving it is *safe*. The rule this module encodes is the same one the policy
engine enforces one layer down, stated for references:

**Chat-only references may be resolved. Operational references may not be
guessed.**

* "ý tưởng số 3", "cái thứ hai", "hook vừa rồi" point at MeoBot's own recent
  output. Nothing happens when they are resolved wrongly except a slightly
  off answer, and the text is right there in the recent messages, so the model
  resolves them from context.
* "Sheet đó", "kịch bản này", "bản vừa rồi" may point at a real Sheet or a real
  script. Resolving those wrongly writes into the wrong spreadsheet or approves
  the wrong script. So they are only resolved from a *recorded* reference with
  sufficient confidence, and otherwise MeoBot asks.

:class:`RecentReference` is that record: what MeoBot last operated on, kept as a
short bounded list on the thread. It is a pointer list, not memory - it holds
identifiers and labels, never content.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.core.time import utcnow

#: How many pointers a thread keeps. Small on purpose: the *last* thing that
#: was discussed is what "cái đó" means; the tenth-last is not.
MAX_RECENT_REFERENCES = 8

#: Below this, an operational reference is treated as unresolved and MeoBot
#: asks instead of acting.
MIN_OPERATIONAL_CONFIDENCE = 0.7

#: Entity kinds that can be the target of a real operation. A reference to one
#: of these is never guessed.
OPERATIONAL_ENTITY_TYPES: frozenset[str] = frozenset(
    {"script", "script_version", "sheet_profile", "spreadsheet", "drive_folder", "invite"}
)

#: Phrases that point at something without naming it.
_DEMONSTRATIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(cái|kịch bản|sheet|thư mục|bản|file|link)\s+(này|đó|kia|ấy|vừa rồi)\b", re.I),
    re.compile(r"\b(vừa rồi|vừa nãy|lúc nãy|ban nãy)\b", re.I),
    re.compile(r"\b(cái|ý tưởng|hướng|phương án|mục)\s+(thứ\s+)?(\d+|nhất|hai|ba|bốn|năm)\b", re.I),
)

#: "ý tưởng số 3", "hướng 2", "cái thứ hai" - an index into MeoBot's own list.
_ORDINAL_WORDS: dict[str, int] = {
    "nhất": 1,
    "đầu": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "tư": 4,
    "năm": 5,
    "sáu": 6,
}
_ENUMERATED_PATTERN = re.compile(
    r"\b(?:ý tưởng|hướng|phương án|cái|mục|option)\s*(?:số\s*|thứ\s*)?(\d+|"
    + "|".join(_ORDINAL_WORDS)
    + r")\b",
    re.IGNORECASE,
)


class RecentReference(BaseModel):
    """One thing the conversation has been pointing at."""

    model_config = ConfigDict(frozen=True)

    entity_type: str = Field(max_length=50)
    entity_id: str = Field(max_length=200)
    display_name: str = Field(default="", max_length=300)
    version: int | None = None
    source_message_id: int | None = None
    resolved_at: datetime = Field(default_factory=utcnow)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def is_operational(self) -> bool:
        """True when acting on this reference would change real data."""
        return self.entity_type in OPERATIONAL_ENTITY_TYPES

    @property
    def usable_without_asking(self) -> bool:
        """True when MeoBot may act on this reference without confirming it."""
        if not self.is_operational:
            return True
        return self.confidence >= MIN_OPERATIONAL_CONFIDENCE

    def label(self) -> str:
        """One line for the ``[ACTIVE WORK CONTEXT]`` prompt section."""
        name = self.display_name or self.entity_id
        suffix = f" (v{self.version})" if self.version else ""
        return f"{self.entity_type}: {name}{suffix}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "display_name": self.display_name,
            "version": self.version,
            "source_message_id": self.source_message_id,
            "resolved_at": self.resolved_at.isoformat(),
            "confidence": self.confidence,
        }


def load_references(raw: Any) -> list[RecentReference]:
    """Rebuild the pointer list from its stored JSON, dropping anything odd.

    A malformed stored entry is skipped rather than raised on: a corrupted
    pointer must not make a conversation unanswerable.
    """
    if not isinstance(raw, list):
        return []
    references: list[RecentReference] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            references.append(RecentReference.model_validate(item))
        except ValueError:
            continue
    return references[-MAX_RECENT_REFERENCES:]


def push_reference(
    existing: list[RecentReference], reference: RecentReference
) -> list[RecentReference]:
    """Append ``reference``, de-duplicating by identity and keeping it bounded."""
    kept = [
        item
        for item in existing
        if not (item.entity_type == reference.entity_type and item.entity_id == reference.entity_id)
    ]
    kept.append(reference)
    return kept[-MAX_RECENT_REFERENCES:]


def mentions_a_reference(message: str) -> bool:
    """True when the message points at something without naming it."""
    return any(pattern.search(message) for pattern in _DEMONSTRATIVE_PATTERNS)


def enumerated_index(message: str) -> int | None:
    """The 1-based index in "ý tưởng số 3" / "cái thứ hai", if there is one.

    Used only to tell the model *which* item of its own last list is meant. It
    never selects an operational entity.
    """
    match = _ENUMERATED_PATTERN.search(message)
    if match is None:
        return None
    token = match.group(1).lower()
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 20 else None
    return _ORDINAL_WORDS.get(token)


def render_references(references: list[RecentReference]) -> str:
    """The ``[ACTIVE WORK CONTEXT]`` body, newest first."""
    if not references:
        return ""
    lines = ["Những thứ đang được nhắc tới (mới nhất trước):"]
    for reference in reversed(references):
        marker = "" if reference.usable_without_asking else " [chưa chắc chắn — phải hỏi lại]"
        lines.append(f"- {reference.label()}{marker}")
    return "\n".join(lines)
