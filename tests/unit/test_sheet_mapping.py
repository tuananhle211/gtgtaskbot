"""Alias-based mapping, mapping validation and schema-change detection."""

from __future__ import annotations

import pytest

from meobot.core.errors import ValidationError
from meobot.domain.sheets.mapping import (
    build_field_mapping,
    propose_mapping,
    propose_write_back,
    validate_against_headers,
)
from meobot.domain.sheets.models import (
    SchemaFingerprint,
    WriteBackMapping,
    extract_spreadsheet_id,
)

# The two layouts the team actually uses.
SHEET_A = ["ID", "Tiêu đề", "Hook", "Nội dung", "Ghi chú", "Người viết", "Deadline", "Trạng thái"]
SHEET_B = [
    "Mã kịch bản",
    "Chủ đề",
    "Mở đầu",
    "Kịch bản video",
    "Lưu ý quay",
    "Content Creator",
    "Ngày hoàn thành",
    "Tiến độ",
]


def test_format_a_maps_every_canonical_field() -> None:
    proposal = propose_mapping(SHEET_A)
    assert proposal.mapping["script_id"] == "ID"
    assert proposal.mapping["title"] == "Tiêu đề"
    assert proposal.mapping["hook"] == "Hook"
    assert proposal.mapping["script_body"] == "Nội dung"
    assert proposal.mapping["production_notes"] == "Ghi chú"
    assert proposal.mapping["author"] == "Người viết"
    assert proposal.mapping["deadline"] == "Deadline"
    assert proposal.mapping["source_status"] == "Trạng thái"
    assert proposal.unmapped_headers == []


def test_format_b_maps_to_the_same_canonical_fields() -> None:
    """Different wording, same normalised shape - that is the whole point."""
    proposal = propose_mapping(SHEET_B)
    assert proposal.mapping["script_id"] == "Mã kịch bản"
    assert proposal.mapping["title"] == "Chủ đề"
    assert proposal.mapping["hook"] == "Mở đầu"
    assert proposal.mapping["script_body"] == "Kịch bản video"
    assert proposal.mapping["production_notes"] == "Lưu ý quay"
    assert proposal.mapping["author"] == "Content Creator"
    assert proposal.mapping["deadline"] == "Ngày hoàn thành"
    assert proposal.mapping["source_status"] == "Tiến độ"


def test_both_formats_agree_on_the_mapped_field_set() -> None:
    assert set(propose_mapping(SHEET_A).mapping) == set(propose_mapping(SHEET_B).mapping)


def test_body_column_wins_over_the_notes_column() -> None:
    """'Ghi chú' must never be mistaken for the script body."""
    proposal = propose_mapping(SHEET_A)
    assert proposal.mapping["script_body"] != proposal.mapping["production_notes"]


def test_unknown_headers_are_reported_not_guessed() -> None:
    proposal = propose_mapping([*SHEET_A, "Link Drive", "Ghi chú nội bộ XYZ"])
    assert "Link Drive" in proposal.unmapped_headers


def test_confidence_drops_when_columns_are_missing() -> None:
    rich = propose_mapping(SHEET_A)
    poor = propose_mapping(["Nội dung"])
    assert poor.confidence < rich.confidence


# --- Validation -------------------------------------------------------------
def test_build_field_mapping_requires_the_body_column() -> None:
    with pytest.raises(ValidationError, match="script_body"):
        build_field_mapping({"title": "Tiêu đề"})


def test_build_field_mapping_accepts_a_body_only_sheet() -> None:
    """An id-less, title-less sheet is still importable: both are synthesised."""
    mapping = build_field_mapping({"script_body": "Nội dung"})
    assert mapping.missing_essential() == []
    assert mapping.candidates("script_body") == ["Nội dung"]


def test_build_field_mapping_ignores_unknown_fields() -> None:
    mapping = build_field_mapping({"script_body": "Nội dung", "nonsense": "Cột lạ"})
    assert mapping.candidates("nonsense") == ["Cột lạ"]  # kept as passthrough 'extra'
    assert mapping.missing_essential() == []


def test_validate_against_headers_finds_a_renamed_column() -> None:
    mapping = build_field_mapping(propose_mapping(SHEET_A).mapping)
    renamed = [header if header != "Nội dung" else "Nội dung kịch bản" for header in SHEET_A]
    missing = validate_against_headers(mapping, renamed)
    assert missing == ["nội dung"]


def test_validate_against_headers_tolerates_a_reordered_sheet() -> None:
    mapping = build_field_mapping(propose_mapping(SHEET_A).mapping)
    assert validate_against_headers(mapping, list(reversed(SHEET_A))) == []


# --- Fingerprints -----------------------------------------------------------
def test_fingerprint_changes_when_a_column_is_renamed() -> None:
    before = SchemaFingerprint.from_headers(SHEET_A)
    after = SchemaFingerprint.from_headers([*SHEET_A[:-1], "Tiến độ"])
    assert not before.matches(after)


def test_fingerprint_ignores_case_and_padding() -> None:
    noisy = [f"  {header.upper()} " for header in SHEET_A]
    assert SchemaFingerprint.from_headers(SHEET_A).matches(SchemaFingerprint.from_headers(noisy))


def test_fingerprint_notices_reordering() -> None:
    """A moved column *is* a schema change, even when nothing was renamed."""
    reordered = [SHEET_A[1], SHEET_A[0], *SHEET_A[2:]]
    assert not SchemaFingerprint.from_headers(SHEET_A).matches(
        SchemaFingerprint.from_headers(reordered)
    )


# --- Write-back -------------------------------------------------------------
def test_write_back_columns_are_only_proposed_when_they_exist() -> None:
    assert propose_write_back(SHEET_A) == {}

    with_columns = [*SHEET_A, "TasksBot Status", "Điểm AI", "Nhận xét AI"]
    proposed = propose_write_back(with_columns)
    assert proposed["meobot_status"] == "TasksBot Status"
    assert proposed["review_score"] == "Điểm AI"
    assert proposed["review_summary"] == "Nhận xét AI"


def test_write_back_mapping_reports_whether_it_is_configured() -> None:
    assert WriteBackMapping().configured is False
    assert WriteBackMapping(meobot_status="TasksBot Status").configured is True
    assert WriteBackMapping(meobot_status="TasksBot Status").columns() == {
        "meobot_status": "TasksBot Status"
    }


# --- URL parsing ------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=0",
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    ],
)
def test_spreadsheet_id_is_extracted_from_every_shape_humans_paste(value: str) -> None:
    assert extract_spreadsheet_id(value) == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


@pytest.mark.parametrize("value", ["", "   ", "not a link", "https://example.com/x"])
def test_bad_spreadsheet_input_returns_none(value: str) -> None:
    assert extract_spreadsheet_id(value) is None
