"""Deterministic header -> canonical field mapping, and mapping validation.

The alias table below is the cheap path: it covers the layouts the team
actually uses (Vietnamese and English) without spending an LLM call. The LLM
proposal in :mod:`meobot.application.sheet_inspection_service` is only
consulted for headers this table cannot place, and its answer is validated
here before a human ever sees it.

Pure functions, no I/O.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from meobot.core.errors import ValidationError
from meobot.domain.scripts.models import MappingProposal
from meobot.domain.sheets.models import (
    CANONICAL_FIELDS,
    ESSENTIAL_FIELDS,
    WRITE_BACK_FIELDS,
    FieldMapping,
    normalize_header,
)

#: ``đ`` is a distinct Vietnamese letter, not a decorated ``d``, so Unicode
#: decomposition leaves it alone. Folding it by hand is what makes "Tiêu đề"
#: match the alias "tieu de".
_LETTER_FOLDING = str.maketrans({"đ": "d", "Đ": "d"})


def fold(text: str) -> str:
    """Accent-fold and normalise a header for alias comparison."""
    normalized = normalize_header(text).translate(_LETTER_FOLDING)
    decomposed = unicodedata.normalize("NFD", normalized)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


#: ``canonical field -> accent-folded aliases``, most specific first. Order
#: inside a field matters: the earliest matching alias wins when two headers
#: could feed the same field.
FIELD_ALIASES: Mapping[str, tuple[str, ...]] = {
    "script_id": (
        "ma kich ban",
        "ma kb",
        "script id",
        "script code",
        "id kich ban",
        "id",
        "ma",
        "stt",
        "code",
    ),
    "title": (
        "tieu de",
        "chu de",
        "ten kich ban",
        "title",
        "headline",
        "topic",
        "subject",
    ),
    "hook": (
        "hook",
        "mo dau",
        "cau mo dau",
        "opening line",
        "opening",
        "intro",
        "3 giay dau",
    ),
    "script_body": (
        "kich ban video",
        "noi dung kich ban",
        "noi dung",
        "kich ban",
        "script body",
        "script",
        "body",
        "content",
        "full script",
    ),
    "production_notes": (
        "luu y quay",
        "ghi chu san xuat",
        "ghi chu",
        "luu y",
        "production notes",
        "notes",
        "note",
    ),
    "author": (
        "content creator",
        "nguoi viet",
        "nguoi phu trach",
        "tac gia",
        "author",
        "writer",
        "owner",
        "pic",
    ),
    "deadline": (
        "ngay hoan thanh",
        "han chot",
        "deadline",
        "due date",
        "due",
        "ngay",
    ),
    "source_status": (
        "tien do",
        "trang thai",
        "status",
        "progress",
        "tinh trang",
    ),
}

#: Aliases for the optional write-back columns.
WRITE_BACK_ALIASES: Mapping[str, tuple[str, ...]] = {
    "meobot_status": ("meobot status", "trang thai meobot", "meobot"),
    "review_score": ("diem ai", "diem review", "review score", "ai score", "score"),
    "review_summary": ("nhan xet ai", "tom tat review", "review summary", "ai summary"),
    "reviewed_at": ("thoi diem review", "reviewed at", "ngay review"),
    "approved_by": ("nguoi duyet", "approved by"),
    "approved_at": ("thoi diem duyet", "approved at", "ngay duyet"),
    "revision_comment": ("ghi chu sua", "yeu cau sua", "revision comment"),
}


def propose_mapping(headers: Sequence[str]) -> MappingProposal:
    """Map sheet headers onto canonical fields using the alias table.

    Args:
        headers: The header row exactly as it appears in the sheet.

    Returns:
        A :class:`MappingProposal` holding ``canonical field -> header text``.
        ``confidence`` reflects how many canonical fields were placed; headers
        the table could not place are listed in ``unmapped_headers``.
    """
    folded = {header: fold(header) for header in headers if header and header.strip()}
    mapping: dict[str, str] = {}
    used: set[str] = set()

    # Exact alias match first, then a containment pass, so 'Nội dung' does not
    # steal the column that 'Nội dung kịch bản' should own.
    for matcher in (_exact_match, _contains_match):
        for field, aliases in FIELD_ALIASES.items():
            if field in mapping:
                continue
            header = matcher(folded, aliases, used)
            if header is not None:
                mapping[field] = header
                used.add(header)

    unmapped = [header for header in folded if header not in used]
    placed = sum(1 for field in CANONICAL_FIELDS if field in mapping)
    confidence = round(placed / len(CANONICAL_FIELDS), 2)
    return MappingProposal(
        mapping=mapping,
        confidence=confidence,
        unmapped_headers=unmapped,
        notes="Alias-based deterministic mapping.",
    )


def propose_write_back(headers: Sequence[str]) -> dict[str, str]:
    """Map existing sheet columns onto write-back targets, when they exist.

    Never invents a column: MeoBot writes only where the team already made
    room for it.
    """
    folded = {header: fold(header) for header in headers if header and header.strip()}
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for field in WRITE_BACK_FIELDS:
        aliases = WRITE_BACK_ALIASES.get(field, ())
        header = _exact_match(folded, aliases, used)
        if header is not None:
            mapping[field] = header
            used.add(header)
    return mapping


def _exact_match(
    folded: Mapping[str, str],
    aliases: Sequence[str],
    used: set[str],
) -> str | None:
    for alias in aliases:
        for header, key in folded.items():
            if header not in used and key == alias:
                return header
    return None


def _contains_match(
    folded: Mapping[str, str],
    aliases: Sequence[str],
    used: set[str],
) -> str | None:
    for alias in aliases:
        for header, key in folded.items():
            if header not in used and (alias in key or key in alias):
                return header
    return None


def build_field_mapping(mapping: Mapping[str, str | list[str]]) -> FieldMapping:
    """Turn a flat ``field -> header`` proposal into a :class:`FieldMapping`.

    Raises:
        ValidationError: When an unknown canonical field is present or the
            mapping does not cover ``script_body``.
    """
    known = set(CANONICAL_FIELDS)
    payload: dict[str, object] = {}
    extra: dict[str, object] = {}
    for field, header in mapping.items():
        if not header:
            continue
        if field in known:
            payload[field] = header
        else:
            extra[field] = header
    if extra:
        payload["extra"] = extra

    try:
        field_mapping = FieldMapping.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"Mapping không hợp lệ: {exc}") from exc

    missing = field_mapping.missing_essential()
    if missing:
        raise ValidationError(
            f"Thiếu cột bắt buộc: {missing}. Cần ít nhất cột nội dung kịch bản.",
            details={"missing": missing, "essential": list(ESSENTIAL_FIELDS)},
        )
    return field_mapping


def validate_against_headers(
    field_mapping: FieldMapping,
    headers: Sequence[str],
) -> list[str]:
    """Return the mapped headers that do not exist in ``headers``.

    An empty list means the mapping can be applied to this sheet as-is.
    """
    present = {normalize_header(header) for header in headers}
    return sorted(header for header in field_mapping.mapped_headers() if header not in present)
