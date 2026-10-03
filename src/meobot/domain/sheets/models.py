"""Models for reading heterogeneous Google Sheets into one canonical shape.

Every team sheet has its own column names, language and status vocabulary. A
:class:`SheetProfileSpec` describes one sheet; :func:`~meobot.domain.sheets.
normalizer.normalize_row` turns a raw row into a :class:`NormalizedScript`,
which is the only shape the rest of the system knows about.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from meobot.domain.scripts.workflow import ScriptStatus

#: Canonical fields every sheet must be mapped onto.
CANONICAL_FIELDS: tuple[str, ...] = (
    "script_id",
    "title",
    "hook",
    "script_body",
    "production_notes",
    "author",
    "deadline",
    "source_status",
)

REQUIRED_FIELDS: tuple[str, ...] = ("script_id", "title", "script_body")

#: The only field that genuinely cannot be synthesised. A sheet without an id
#: column gets a stable row-based identity; a sheet without a title column gets
#: one derived from the body - but a sheet without a body column is not a
#: script sheet at all.
ESSENTIAL_FIELDS: tuple[str, ...] = ("script_body",)


class SheetProfileState(StrEnum):
    """Lifecycle of a sheet profile's *mapping*, not of its data.

    ``schema_changed`` is the important one: it stops synchronisation instead
    of silently importing rows through a mapping that no longer describes the
    sheet.
    """

    NEEDS_MAPPING = "needs_mapping"
    ACTIVE = "active"
    SCHEMA_CHANGED = "schema_changed"
    INACTIVE = "inactive"


#: Canonical write-back targets a profile may map onto sheet columns.
WRITE_BACK_FIELDS: tuple[str, ...] = (
    "meobot_status",
    "review_score",
    "review_summary",
    "reviewed_at",
    "approved_by",
    "approved_at",
    "revision_comment",
)


def _as_column_list(value: Any) -> Any:
    """Allow a mapping entry to be a single header or a list of candidates."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return value


#: One canonical field may be fed by several candidate headers; the first
#: header that exists and is non-empty wins.
ColumnSelector = Annotated[list[str], BeforeValidator(_as_column_list)]


def normalize_header(header: str) -> str:
    """Case/whitespace-insensitive header key used for lookup."""
    return " ".join(header.strip().lower().split())


#: ``/spreadsheets/d/<id>`` is the only shape Google publishes; the id itself is
#: base64url-ish and at least 20 characters.
_SPREADSHEET_URL_PATTERN = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_BARE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def extract_spreadsheet_id(url_or_id: str) -> str | None:
    """Pull the spreadsheet id out of a full URL, or accept a bare id.

    Returns ``None`` when the input is neither, so the caller can ask the human
    again instead of sending a malformed request to Google.
    """
    candidate = url_or_id.strip()
    if not candidate:
        return None
    match = _SPREADSHEET_URL_PATTERN.search(candidate)
    if match is not None:
        return match.group(1)
    if _BARE_ID_PATTERN.match(candidate):
        return candidate
    return None


def spreadsheet_url(spreadsheet_id: str, *, sheet_name: str | None = None) -> str:
    """Human-clickable link back to the source sheet."""
    base = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    return base if sheet_name is None else f"{base}#gid=0"


class SchemaFingerprint(BaseModel):
    """Stable hash of a sheet's header row, used to detect schema drift."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(min_length=8, max_length=64)
    headers: tuple[str, ...] = ()

    @classmethod
    def from_headers(cls, headers: list[str] | tuple[str, ...]) -> SchemaFingerprint:
        """Compute a fingerprint from the raw header row.

        Header order is preserved (a moved column *is* a schema change) but
        case and surrounding whitespace are ignored.
        """
        normalized = tuple(normalize_header(header) for header in headers)
        digest = hashlib.sha256("␟".join(normalized).encode("utf-8")).hexdigest()[:32]
        return cls(value=digest, headers=normalized)

    def matches(self, other: SchemaFingerprint) -> bool:
        return self.value == other.value


class SheetRowReference(BaseModel):
    """Where a normalised script came from - needed for write-back later."""

    model_config = ConfigDict(frozen=True)

    spreadsheet_id: str
    sheet_name: str
    row_number: int = Field(ge=1, description="1-based row number in the sheet")

    @property
    def a1(self) -> str:
        """A1-style reference to the row, e.g. ``'Kịch bản'!5:5``."""
        return f"'{self.sheet_name}'!{self.row_number}:{self.row_number}"


class FieldMapping(BaseModel):
    """Maps canonical field names onto candidate sheet headers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    script_id: ColumnSelector = Field(default_factory=list)
    title: ColumnSelector = Field(default_factory=list)
    hook: ColumnSelector = Field(default_factory=list)
    script_body: ColumnSelector = Field(default_factory=list)
    production_notes: ColumnSelector = Field(default_factory=list)
    author: ColumnSelector = Field(default_factory=list)
    deadline: ColumnSelector = Field(default_factory=list)
    source_status: ColumnSelector = Field(default_factory=list)
    extra: dict[str, ColumnSelector] = Field(
        default_factory=dict,
        description="Sheet-specific passthrough fields kept on NormalizedScript.extra.",
    )

    def candidates(self, field: str) -> list[str]:
        """Candidate headers configured for a canonical field."""
        if field in CANONICAL_FIELDS:
            value: list[str] = getattr(self, field)
            return value
        return self.extra.get(field, [])

    def missing_required(self) -> list[str]:
        """Required canonical fields that have no mapped column."""
        return [field for field in REQUIRED_FIELDS if not self.candidates(field)]

    def missing_essential(self) -> list[str]:
        """Fields that cannot be synthesised and must be mapped.

        Used by the milestone-2 import path, which falls back to a row-based
        identity and a body-derived title when those columns are absent.
        """
        return [field for field in ESSENTIAL_FIELDS if not self.candidates(field)]

    def mapped_headers(self) -> set[str]:
        """Every header this mapping references, normalised for comparison."""
        headers: set[str] = set()
        for field in CANONICAL_FIELDS:
            headers.update(normalize_header(header) for header in self.candidates(field))
        for candidates in self.extra.values():
            headers.update(normalize_header(header) for header in candidates)
        return headers


class WriteBackMapping(BaseModel):
    """Canonical write-back field -> the sheet column that receives it.

    Only the columns named here are ever written. Anything the team keeps in
    the sheet stays untouched, which is the whole point of an explicit mapping
    rather than 'append a few columns at the end'.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    meobot_status: str | None = None
    review_score: str | None = None
    review_summary: str | None = None
    reviewed_at: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    revision_comment: str | None = None

    @property
    def configured(self) -> bool:
        """True when at least one write-back column is mapped."""
        return any(getattr(self, field) for field in WRITE_BACK_FIELDS)

    def columns(self) -> dict[str, str]:
        """``{canonical field: header}`` for the configured columns only."""
        return {
            field: header
            for field in WRITE_BACK_FIELDS
            if (header := getattr(self, field)) is not None and header.strip()
        }


class SheetProfileSpec(BaseModel):
    """Domain view of a ``sheet_profiles`` row."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    spreadsheet_id: str = Field(min_length=1, max_length=200)
    sheet_name: str = Field(min_length=1, max_length=200)
    header_row: int = Field(default=1, ge=1, le=50)
    channel: str | None = Field(default=None, max_length=100)
    script_type_id: uuid.UUID | None = None
    field_mapping: FieldMapping
    status_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Raw sheet status text (lower-cased) -> ScriptStatus value.",
    )
    schema_fingerprint: str | None = None
    active: bool = True
    spreadsheet_url: str | None = None
    state: SheetProfileState = SheetProfileState.ACTIVE
    write_back: WriteBackMapping = Field(default_factory=WriteBackMapping)

    @property
    def importable(self) -> bool:
        """True when synchronisation may read this profile right now."""
        return self.active and self.state is SheetProfileState.ACTIVE

    @field_validator("status_mapping")
    @classmethod
    def _known_statuses(cls, mapping: dict[str, str]) -> dict[str, str]:
        valid = {status.value for status in ScriptStatus}
        unknown = sorted({value for value in mapping.values() if value not in valid})
        if unknown:
            raise ValueError(f"status_mapping targets unknown ScriptStatus values: {unknown}")
        return {normalize_header(key): value for key, value in mapping.items()}

    def resolve_status(self, raw_status: str | None) -> ScriptStatus | None:
        """Translate a sheet's own status wording into a canonical status."""
        if not raw_status:
            return None
        mapped = self.status_mapping.get(normalize_header(raw_status))
        if mapped is None:
            return None
        return ScriptStatus(mapped)


class NormalizedScript(BaseModel):
    """Canonical representation of one script row, regardless of sheet layout."""

    model_config = ConfigDict(frozen=True)

    script_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    script_body: str
    hook: str | None = None
    production_notes: str | None = None
    author: str | None = None
    deadline: date | None = None
    source_status: str | None = Field(default=None, description="Raw status text from the sheet")
    status: ScriptStatus | None = Field(
        default=None, description="Canonical status, if the profile could map it"
    )
    channel: str | None = None
    script_type_id: uuid.UUID | None = None
    source: SheetRowReference
    extra: dict[str, str] = Field(default_factory=dict)
    warnings: tuple[str, ...] = Field(
        default=(),
        description="Non-fatal normalisation problems (unmapped status, bad date, ...).",
    )
