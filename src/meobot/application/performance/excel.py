"""The month's points report as an Excel file: "Tổng hợp" (one line per
person) and "Chi tiết" (one line per counted step), laid out like the
department's own sheet so it can go on being used."""

from __future__ import annotations

import io
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from meobot.application.performance.coefficients import weigh
from meobot.application.performance.report_service import (
    CodeRow,
    EarnedLine,
    TeamRow,
    line_phase_label,
)
from meobot.core.time import ensure_utc

SUMMARY_COLUMNS = (
    ("Nhân sự", 26),
    ("Phòng ban", 26),
    ("Tổng điểm", 12),
    ("Định mức", 11),
    ("% định mức", 11),
    ("Task xong", 11),
    ("Đang làm (dự kiến)", 18),
    ("Bị trả - làm lỗi", 15),
    ("Bị trả - đổi yêu cầu", 18),
    ("Bị trả - mã chưa hợp lý", 20),
    ("Trễ hạn", 9),
    ("Điểm tháng trước", 16),
)
DETAIL_COLUMNS = (
    ("Ngày xong", 17),
    ("Nhân sự", 24),
    ("Phòng ban", 22),
    ("Mã order / bài", 14),
    ("Tên order / bài", 34),
    ("Pha", 10),
    ("Mã task", 10),
    ("Hình thức", 22),
    ("Số lượng", 9),
    ("Ghi chú", 28),
    ("Điểm", 9),
)


CODE_COLUMNS = (
    ("Bước", 12),
    ("Mã task", 12),
    ("Tên / ví dụ", 44),
    ("Hình thức", 18),
    ("Số lượng", 10),
    ("Số người", 10),
    ("Điểm", 10),
)


def report_workbook(
    *,
    month: str,
    team: Sequence[TeamRow],
    details: Sequence[tuple[EarnedLine, Decimal]],
    codes: Sequence[CodeRow] = (),
    quota: int | None = None,
    names: Mapping[uuid.UUID, str],
    departments: Mapping[uuid.UUID, str],
    tz: ZoneInfo,
) -> bytes:
    book = Workbook()
    summary = book.active
    assert summary is not None
    summary.title = "Tổng hợp"
    summary.append([f"BÁO CÁO ĐIỂM HIỆU SUẤT - THÁNG {month}"])
    summary["A1"].font = Font(bold=True, size=13)
    summary.append([label for label, _ in SUMMARY_COLUMNS])
    for row in team:
        figures = row.figures
        summary.append(
            [
                row.name,
                ", ".join(row.departments),
                figures.points,
                quota or "",
                round(figures.points * 100 / quota) / 100 if quota else "",
                figures.steps_done,
                figures.pending_points,
                figures.returns_fault,
                figures.returns_other,
                figures.returns_codes,
                figures.late_count,
                figures.previous_points,
            ]
        )
    total = sum(row.figures.points for row in team)
    team_quota = (quota or 0) * len(team)
    summary.append(
        [
            "Tổng",
            "",
            total,
            team_quota or "",
            round(total * 100 / team_quota) / 100 if team_quota else "",
        ]
    )
    _shape(summary, SUMMARY_COLUMNS, header_row=2)
    for cell in summary["E"][2:]:
        cell.number_format = "0%"

    detail = book.create_sheet("Chi tiết")
    detail.append([label for label, _ in DETAIL_COLUMNS])
    for line, factor in details:
        when = ensure_utc(line.at).astimezone(tz).strftime("%d/%m/%Y %H:%M")
        person = names.get(line.user_id, "—")
        department = departments.get(line.department_id, "") if line.department_id else ""
        for item in line.items:
            detail.append(
                [
                    when,
                    person,
                    department,
                    line.ref_code or "",
                    line.ref_title or "",
                    line_phase_label(line.source, line.phase),
                    "Việc khác" if item.free else item.code,
                    "" if item.free else item.form_label,
                    item.quantity,
                    item.note or "",
                    weigh(item.points, factor),
                ]
            )
    _shape(detail, DETAIL_COLUMNS, header_row=1)

    by_code = book.create_sheet("Theo mã task")
    by_code.append([label for label, _ in CODE_COLUMNS])
    for code_row in codes:
        by_code.append(
            [
                code_row.phase_label,
                code_row.code,
                code_row.name,
                code_row.form_label or ("" if code_row.free else "Làm mới"),
                code_row.quantity,
                code_row.people,
                code_row.points,
            ]
        )
    quantity = sum(code_row.quantity for code_row in codes)
    by_code.append(["Tổng", "", "", "", quantity, "", sum(code_row.points for code_row in codes)])
    _shape(by_code, CODE_COLUMNS, header_row=1)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _shape(sheet: Worksheet, columns: Sequence[tuple[str, int]], *, header_row: int) -> None:
    for index, (_label, width) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.cell(row=header_row, column=index).font = Font(bold=True)
    sheet.freeze_panes = f"A{header_row + 1}"


__all__ = ["report_workbook"]
