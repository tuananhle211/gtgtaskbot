"""Inspecting a spreadsheet and proposing a column mapping.

Order of preference, cheapest first:

1. the deterministic alias table (:mod:`meobot.domain.sheets.mapping`);
2. an LLM proposal, consulted only when the alias table left an *essential*
   field unmapped and a real provider is configured.

Whatever comes back is validated against the actual headers before a human
sees it, so a hallucinated column name never reaches the confirmation screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from meobot.core.errors import LLMError, ValidationError
from meobot.core.logging import get_logger
from meobot.domain.scripts.models import MappingProposal
from meobot.domain.sheets.mapping import (
    build_field_mapping,
    propose_mapping,
    propose_write_back,
    validate_against_headers,
)
from meobot.domain.sheets.models import (
    CANONICAL_FIELDS,
    SchemaFingerprint,
    normalize_header,
)
from meobot.integrations.google.sheets import SheetRange, SheetsClient, SpreadsheetMetadata
from meobot.integrations.llm.base import LLMProvider, SheetMappingRequest

logger = get_logger(__name__)

#: How many data rows are shown to the Owner (and to the LLM) while mapping.
SAMPLE_ROW_LIMIT = 5

MAPPING_SYSTEM_PROMPT = """\
Bạn giúp ánh xạ (mapping) các cột của một Google Sheet kịch bản video ngắn sang \
các trường chuẩn của TasksBot.

Các trường chuẩn: script_id, title, hook, script_body, production_notes, author, \
deadline, source_status.

Quy tắc:
- Chỉ được dùng đúng tên cột (header) có trong danh sách được cung cấp.
- Nếu không tìm được cột phù hợp cho một trường, hãy bỏ trường đó ra khỏi mapping.
- script_body là cột chứa nội dung kịch bản đầy đủ, KHÔNG phải cột ghi chú.
- Trả lời bằng JSON đúng schema.
"""

MAPPING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mapping", "confidence", "notes"],
    "properties": {
        "mapping": {"type": "object", "additionalProperties": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string", "maxLength": 2000},
    },
}


@dataclass(frozen=True, slots=True)
class SheetInspection:
    """Everything the Owner needs in order to confirm a mapping."""

    spreadsheet_id: str
    spreadsheet_title: str
    sheet_name: str
    header_row: int
    headers: list[str]
    sample_rows: list[dict[str, str]] = field(default_factory=list)
    proposal: MappingProposal = field(default_factory=MappingProposal)
    write_back: dict[str, str] = field(default_factory=dict)
    source: str = "aliases"

    @property
    def fingerprint(self) -> str:
        return SchemaFingerprint.from_headers(self.headers).value

    @property
    def missing_fields(self) -> list[str]:
        """Canonical fields the proposal could not place."""
        return [
            field_name for field_name in CANONICAL_FIELDS if field_name not in self.proposal.mapping
        ]


class SheetInspectionService:
    """Reads a spreadsheet's shape and proposes how to interpret it.

    Args:
        sheets: Google Sheets client (real, fake or the not-configured stub).
        llm: Provider used only when the alias table is not enough.
    """

    def __init__(self, sheets: SheetsClient, llm: LLMProvider | None = None) -> None:
        self._sheets = sheets
        self._llm = llm

    async def list_worksheets(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        """Fetch the spreadsheet's title and tabs.

        Raises:
            IntegrationNotConfiguredError: When Google is not configured.
            IntegrationAuthError: When the sheet is not shared with the bot.
            NotFoundError: When the spreadsheet does not exist.
        """
        return await self._sheets.get_metadata(spreadsheet_id)

    async def inspect(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        header_row: int = 1,
        spreadsheet_title: str = "",
        use_llm: bool = True,
    ) -> SheetInspection:
        """Read headers and a few rows, then propose a mapping.

        Raises:
            ValidationError: When the tab has no usable header row.
        """
        target = SheetRange(
            spreadsheet_id=spreadsheet_id, sheet_name=sheet_name, header_row=header_row
        )
        headers = [header for header in await self._sheets.read_headers(target) if header.strip()]
        if not headers:
            raise ValidationError(
                f"Tab {sheet_name!r} không có dòng tiêu đề ở dòng {header_row}.",
                details={"sheet_name": sheet_name, "header_row": header_row},
            )

        sample_rows = await self._sheets.read_rows(target, limit=SAMPLE_ROW_LIMIT)
        proposal = propose_mapping(headers)
        source = "aliases"

        if use_llm and self._needs_llm(proposal):
            llm_proposal = await self._ask_llm(headers, sample_rows)
            if llm_proposal is not None:
                proposal = self._merge(proposal, llm_proposal, headers)
                source = "llm"

        return SheetInspection(
            spreadsheet_id=spreadsheet_id,
            spreadsheet_title=spreadsheet_title,
            sheet_name=sheet_name,
            header_row=header_row,
            headers=headers,
            sample_rows=sample_rows,
            proposal=proposal,
            write_back=propose_write_back(headers),
            source=source,
        )

    async def detect_schema_change(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        header_row: int,
        stored_fingerprint: str | None,
        field_mapping: dict[str, Any],
    ) -> tuple[bool, list[str], list[str]]:
        """Compare the live header row against what the profile expects.

        Returns:
            ``(materially_changed, headers, missing_headers)``. A reordered or
            renamed column that the mapping does not use changes the
            fingerprint but is *not* material: only mapped columns that
            disappeared stop the import.
        """
        target = SheetRange(
            spreadsheet_id=spreadsheet_id, sheet_name=sheet_name, header_row=header_row
        )
        headers = [header for header in await self._sheets.read_headers(target) if header.strip()]
        mapping = build_field_mapping(
            {
                key: value
                for key, value in field_mapping.items()
                if key != "extra" and isinstance(value, str | list)
            }
        )
        missing = validate_against_headers(mapping, headers)
        fingerprint = SchemaFingerprint.from_headers(headers).value
        drifted = stored_fingerprint is not None and stored_fingerprint != fingerprint
        if drifted and not missing:
            logger.info(
                "sheet_schema_drift_tolerated",
                extra={"spreadsheet_id": spreadsheet_id, "sheet_name": sheet_name},
            )
        return bool(missing), headers, missing

    # --- LLM assistance ---------------------------------------------------
    @staticmethod
    def _needs_llm(proposal: MappingProposal) -> bool:
        """True when the deterministic pass left something important open."""
        return "script_body" not in proposal.mapping or "title" not in proposal.mapping

    async def _ask_llm(
        self,
        headers: list[str],
        sample_rows: list[dict[str, str]],
    ) -> MappingProposal | None:
        """Ask the provider for a mapping. Returns None on any failure.

        Uses the typed task method rather than the generic structured call: the
        prompt shaping and the validation are the provider's business, and this
        service only needs the proposal or nothing.
        """
        if self._llm is None:
            return None
        try:
            return await self._llm.propose_sheet_mapping(
                SheetMappingRequest(
                    system_prompt=MAPPING_SYSTEM_PROMPT,
                    headers=headers,
                    sample_rows=sample_rows,
                    json_schema=MAPPING_SCHEMA,
                )
            )
        except (LLMError, ValueError) as exc:
            logger.warning("sheet_mapping_llm_failed", extra={"error": type(exc).__name__})
            return None

    @staticmethod
    def _merge(
        deterministic: MappingProposal,
        llm: MappingProposal,
        headers: list[str],
    ) -> MappingProposal:
        """Fill the gaps in the deterministic mapping with validated LLM guesses.

        The deterministic answer always wins where it has one; an LLM value
        naming a header that does not exist is dropped.
        """
        known = {normalize_header(header): header for header in headers}
        merged = dict(deterministic.mapping)
        for field_name, header in llm.mapping.items():
            if field_name in merged or field_name not in CANONICAL_FIELDS:
                continue
            actual = known.get(normalize_header(str(header)))
            if actual is not None and actual not in merged.values():
                merged[field_name] = actual

        used = set(merged.values())
        return MappingProposal(
            mapping=merged,
            confidence=max(deterministic.confidence, llm.confidence),
            unmapped_headers=[header for header in headers if header not in used],
            notes="Alias mapping completed by the LLM proposal.",
        )
