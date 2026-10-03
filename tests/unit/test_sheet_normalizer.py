"""Two sheets with different layouts must normalise to the same shape."""

from __future__ import annotations

from datetime import date

import pytest

from meobot.core.errors import ValidationError
from meobot.domain.scripts.workflow import ScriptStatus
from meobot.domain.sheets.models import (
    FieldMapping,
    SchemaFingerprint,
    SheetProfileSpec,
)
from meobot.domain.sheets.normalizer import normalize_row, normalize_rows, parse_deadline

# --- Sheet A: Vietnamese headers, TikTok team ------------------------------
SHEET_A_HEADERS = [
    "Mã KB",
    "Tiêu đề",
    "Hook",
    "Nội dung kịch bản",
    "Ghi chú sản xuất",
    "Người viết",
    "Deadline",
    "Trạng thái",
]

PROFILE_A = SheetProfileSpec(
    name="TikTok - Kịch bản tháng 7",
    spreadsheet_id="sheet-a",
    sheet_name="Kịch bản",
    header_row=1,
    channel="tiktok_main",
    field_mapping=FieldMapping(
        script_id="Mã KB",
        title="Tiêu đề",
        hook="Hook",
        script_body="Nội dung kịch bản",
        production_notes="Ghi chú sản xuất",
        author="Người viết",
        deadline="Deadline",
        source_status="Trạng thái",
    ),
    status_mapping={
        "nháp": ScriptStatus.DRAFT.value,
        "chờ duyệt": ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL.value,
        "đã duyệt": ScriptStatus.APPROVED_FOR_PRODUCTION.value,
    },
)

ROW_A = {
    "Mã KB": "TT-0312",
    "Tiêu đề": "5 dấu hiệu thiếu ngủ",
    "Hook": "Bạn ngủ 8 tiếng vẫn mệt?",
    "Nội dung kịch bản": "Cảnh 1: bác sĩ ngồi trước camera...",
    "Ghi chú sản xuất": "Quay tại phòng khám",
    "Người viết": "Ngọc",
    "Deadline": "30/07/2026",
    "Trạng thái": "Chờ duyệt",
}

# --- Sheet B: English headers, different order, Facebook team --------------
SHEET_B_HEADERS = [
    "Script Code",
    "Writer",
    "Headline",
    "Opening Line",
    "Body",
    "Status",
    "Due Date",
    "Notes",
    "Page",
]

PROFILE_B = SheetProfileSpec(
    name="Facebook - Content plan",
    spreadsheet_id="sheet-b",
    sheet_name="Plan",
    header_row=2,
    channel="facebook_page_1",
    field_mapping=FieldMapping(
        script_id="Script Code",
        # 'Headline' is the real column; 'Title' is a legacy fallback.
        title=["Title", "Headline"],
        hook="Opening Line",
        script_body="Body",
        production_notes="Notes",
        author="Writer",
        deadline="Due Date",
        source_status="Status",
        extra={"page": ["Page"]},
    ),
    status_mapping={
        "draft": ScriptStatus.DRAFT.value,
        "pending approval": ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL.value,
        "approved": ScriptStatus.APPROVED_FOR_PRODUCTION.value,
    },
)

ROW_B = {
    "Script Code": "FB-0099",
    "Writer": "Minh",
    "Headline": "Chuyện của một y tá trực đêm",
    "Opening Line": "3 giờ sáng, chuông báo động...",
    "Body": "Scene 1: hành lang bệnh viện...",
    "Status": "Pending approval",
    "Due Date": "2026-08-05",
    "Notes": "Cần dựng nhạc nền",
    "Page": "Sức khoẻ mỗi ngày",
}


def test_sheet_a_normalises() -> None:
    script = normalize_row(PROFILE_A, ROW_A, row_number=5)

    assert script.script_id == "TT-0312"
    assert script.title == "5 dấu hiệu thiếu ngủ"
    assert script.author == "Ngọc"
    assert script.deadline == date(2026, 7, 30)
    assert script.status is ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL
    assert script.channel == "tiktok_main"
    assert script.source.row_number == 5
    assert script.warnings == ()


def test_sheet_b_normalises_to_the_same_shape() -> None:
    """Different headers, different order, different language - same output type."""
    script = normalize_row(PROFILE_B, ROW_B, row_number=3)

    assert script.script_id == "FB-0099"
    assert script.title == "Chuyện của một y tá trực đêm"
    assert script.hook == "3 giờ sáng, chuông báo động..."
    assert script.deadline == date(2026, 8, 5)
    assert script.status is ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL
    assert script.extra == {"page": "Sức khoẻ mỗi ngày"}


def test_both_sheets_agree_on_canonical_status() -> None:
    a = normalize_row(PROFILE_A, ROW_A)
    b = normalize_row(PROFILE_B, ROW_B)
    assert a.status == b.status
    assert type(a) is type(b)


def test_headers_are_matched_case_insensitively() -> None:
    row = {key.upper(): value for key, value in ROW_A.items()}
    script = normalize_row(PROFILE_A, row)
    assert script.script_id == "TT-0312"


def test_fallback_column_is_used_when_primary_missing() -> None:
    """'Title' takes precedence when present; otherwise 'Headline' is used."""
    row = dict(ROW_B)
    row["Title"] = "Tiêu đề ưu tiên"
    script = normalize_row(PROFILE_B, row)
    assert script.title == "Tiêu đề ưu tiên"


def test_unmapped_status_becomes_a_warning_not_an_error() -> None:
    row = dict(ROW_A) | {"Trạng thái": "Đang xem lại"}
    script = normalize_row(PROFILE_A, row)
    assert script.status is None
    assert script.source_status == "Đang xem lại"
    assert any("Unmapped source status" in warning for warning in script.warnings)


def test_unparsable_deadline_becomes_a_warning() -> None:
    row = dict(ROW_A) | {"Deadline": "cuối tháng"}
    script = normalize_row(PROFILE_A, row)
    assert script.deadline is None
    assert any("deadline" in warning for warning in script.warnings)


def test_missing_required_value_is_an_error() -> None:
    row = dict(ROW_A) | {"Mã KB": ""}
    with pytest.raises(ValidationError, match="script_id"):
        normalize_row(PROFILE_A, row)


def test_missing_required_mapping_is_an_error() -> None:
    profile = PROFILE_A.model_copy(
        update={"field_mapping": FieldMapping(title="Tiêu đề", script_body="Nội dung kịch bản")}
    )
    with pytest.raises(ValidationError, match="missing required mappings"):
        normalize_row(profile, ROW_A)


def test_normalize_rows_collects_errors_and_numbers_rows() -> None:
    rows = [ROW_A, dict(ROW_A) | {"Tiêu đề": ""}, ROW_A]
    normalized, errors = normalize_rows(PROFILE_A, rows)

    assert len(normalized) == 2
    assert len(errors) == 1
    # header_row is 1, so the first data row is row 2 and the bad row is row 3.
    assert errors[0][0] == 3


def test_status_mapping_rejects_unknown_target() -> None:
    with pytest.raises(ValueError, match="unknown ScriptStatus"):
        SheetProfileSpec(
            name="bad",
            spreadsheet_id="s",
            sheet_name="t",
            field_mapping=FieldMapping(script_id="a", title="b", script_body="c"),
            status_mapping={"xong": "totally_done"},
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-07-30", date(2026, 7, 30)),
        ("30/07/2026", date(2026, 7, 30)),
        ("30-07-2026", date(2026, 7, 30)),
        ("30.07.2026", date(2026, 7, 30)),
        ("", None),
        ("not a date", None),
    ],
)
def test_deadline_parsing(raw: str, expected: date | None) -> None:
    assert parse_deadline(raw) == expected


def test_schema_fingerprint_detects_layout_change() -> None:
    original = SchemaFingerprint.from_headers(SHEET_A_HEADERS)
    same_but_messy = SchemaFingerprint.from_headers([h.upper() + "  " for h in SHEET_A_HEADERS])
    reordered = SchemaFingerprint.from_headers(list(reversed(SHEET_A_HEADERS)))

    assert original.matches(same_but_messy)
    assert not original.matches(reordered)
    assert not original.matches(SchemaFingerprint.from_headers(SHEET_B_HEADERS))
