"""Row normalisation: raw sheet dict -> :class:`NormalizedScript`.

Pure functions, no I/O. The Google Sheets client (real or fake) is responsible
for turning an API response into ``dict[header, cell_value]`` rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from meobot.core.errors import ValidationError
from meobot.domain.sheets.models import (
    CANONICAL_FIELDS,
    NormalizedScript,
    SheetProfileSpec,
    SheetRowReference,
    normalize_header,
)

#: Date formats seen in the team's sheets, most specific first.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d/%m/%y",
)


def _index_row(raw_row: Mapping[str, Any]) -> dict[str, str]:
    """Normalise header keys and stringify cell values."""
    indexed: dict[str, str] = {}
    for header, value in raw_row.items():
        key = normalize_header(str(header))
        if not key:
            continue
        indexed[key] = "" if value is None else str(value).strip()
    return indexed


def _pick(indexed: dict[str, str], candidates: list[str]) -> str | None:
    """First candidate header that exists with a non-empty value."""
    for candidate in candidates:
        value = indexed.get(normalize_header(candidate))
        if value:
            return value
    return None


def parse_deadline(value: str) -> date | None:
    """Parse a deadline cell using the formats used by the team's sheets."""
    text = value.strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007 - date-only cell
        except ValueError:
            continue
    return None


#: Length of the title synthesised from a body when the sheet has no title column.
SYNTHETIC_TITLE_LENGTH = 80


def synthetic_script_id(profile: SheetProfileSpec, row_number: int) -> str:
    """Stable identity for a row whose sheet has no id column.

    Tied to the tab and the row number, so re-reading the same sheet finds the
    same script instead of importing a duplicate.
    """
    return f"row:{profile.sheet_name}:{row_number}"


def normalize_row(
    profile: SheetProfileSpec,
    raw_row: Mapping[str, Any],
    *,
    row_number: int = 1,
    synthesize_missing: bool = False,
) -> NormalizedScript:
    """Normalise one raw sheet row into the canonical script shape.

    Args:
        profile: The sheet profile describing headers and status vocabulary.
        raw_row: ``{header: cell_value}`` for a single row.
        row_number: 1-based row number, kept for later write-back.
        synthesize_missing: When True, a missing ``script_id`` becomes a stable
            row-based identity and a missing ``title`` is derived from the body.
            Only ``script_body`` stays mandatory. The import path uses this;
            strict callers keep the default.

    Returns:
        A :class:`NormalizedScript`. Recoverable problems (unmapped status,
        unparsable deadline) are reported in ``warnings`` rather than raised.

    Raises:
        ValidationError: when a required field is unmapped or empty - such a
            row cannot be used.
    """
    missing_mapping = (
        profile.field_mapping.missing_essential()
        if synthesize_missing
        else profile.field_mapping.missing_required()
    )
    if missing_mapping:
        raise ValidationError(
            f"Sheet profile {profile.name!r} is missing required mappings: {missing_mapping}",
            details={"profile": profile.name, "missing": missing_mapping},
        )

    indexed = _index_row(raw_row)
    warnings: list[str] = []
    values: dict[str, str | None] = {
        field: _pick(indexed, profile.field_mapping.candidates(field)) for field in CANONICAL_FIELDS
    }

    if synthesize_missing:
        if not values.get("script_body"):
            raise ValidationError(
                f"Row {row_number} of {profile.name!r} has no script body",
                details={
                    "profile": profile.name,
                    "row_number": row_number,
                    "missing": ["script_body"],
                },
            )
        if not values.get("script_id"):
            values["script_id"] = synthetic_script_id(profile, row_number)
            warnings.append("Row has no external id; using a row-based identity.")
        if not values.get("title"):
            body = values["script_body"] or ""
            first_line = next((line for line in body.splitlines() if line.strip()), body)
            values["title"] = first_line.strip()[:SYNTHETIC_TITLE_LENGTH] or f"Row {row_number}"
            warnings.append("Row has no title; derived one from the script body.")

    missing_values = [
        field for field in ("script_id", "title", "script_body") if not values.get(field)
    ]
    if missing_values:
        raise ValidationError(
            f"Row {row_number} of {profile.name!r} is missing required values: {missing_values}",
            details={
                "profile": profile.name,
                "row_number": row_number,
                "missing": missing_values,
            },
        )

    deadline: date | None = None
    raw_deadline = values.get("deadline")
    if raw_deadline:
        deadline = parse_deadline(raw_deadline)
        if deadline is None:
            warnings.append(f"Unrecognised deadline format: {raw_deadline!r}")

    raw_status = values.get("source_status")
    status = profile.resolve_status(raw_status)
    if raw_status and status is None:
        warnings.append(f"Unmapped source status: {raw_status!r}")

    extra: dict[str, str] = {}
    for field, candidates in profile.field_mapping.extra.items():
        picked = _pick(indexed, candidates)
        if picked:
            extra[field] = picked

    # These three are guaranteed non-empty by the check above.
    script_id = values["script_id"]
    title = values["title"]
    script_body = values["script_body"]
    assert script_id is not None and title is not None and script_body is not None

    return NormalizedScript(
        script_id=script_id,
        title=title,
        script_body=script_body,
        hook=values.get("hook"),
        production_notes=values.get("production_notes"),
        author=values.get("author"),
        deadline=deadline,
        source_status=raw_status,
        status=status,
        channel=profile.channel,
        script_type_id=profile.script_type_id,
        source=SheetRowReference(
            spreadsheet_id=profile.spreadsheet_id,
            sheet_name=profile.sheet_name,
            row_number=row_number,
        ),
        extra=extra,
        warnings=tuple(warnings),
    )


def normalize_rows(
    profile: SheetProfileSpec,
    raw_rows: list[Mapping[str, Any]],
    *,
    first_row_number: int | None = None,
    skip_invalid: bool = True,
    synthesize_missing: bool = False,
) -> tuple[list[NormalizedScript], list[tuple[int, str]]]:
    """Normalise many rows.

    Args:
        profile: Sheet profile to apply.
        raw_rows: Rows in sheet order.
        first_row_number: Row number of ``raw_rows[0]``. Defaults to the row
            just after the profile's header row.
        skip_invalid: When True, unusable rows are collected as errors instead
            of aborting the whole batch.
        synthesize_missing: Forwarded to :func:`normalize_row`.

    Returns:
        ``(normalized, errors)`` where ``errors`` is ``[(row_number, message)]``.
    """
    start = first_row_number if first_row_number is not None else profile.header_row + 1
    normalized: list[NormalizedScript] = []
    errors: list[tuple[int, str]] = []
    for offset, raw_row in enumerate(raw_rows):
        row_number = start + offset
        if not any(str(value).strip() for value in raw_row.values()):
            continue  # Blank spacer row - not an error, just nothing to import.
        try:
            normalized.append(
                normalize_row(
                    profile,
                    raw_row,
                    row_number=row_number,
                    synthesize_missing=synthesize_missing,
                )
            )
        except ValidationError as exc:
            if not skip_invalid:
                raise
            errors.append((row_number, exc.message))
    return normalized, errors
