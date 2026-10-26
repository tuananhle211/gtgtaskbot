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
from meobot.application.performance.report_service import TeamRow
from meobot.core.time import ensure_utc
from meobot.db.models.order import Order, OrderNodePoints
from meobot.domain.orders.design_codes import items_from_json
from meobot.domain.orders.task_codes import phase_label

SUMMARY_COLUMNS = (
    ("Nhân sự", 26),
    ("Phòng ban", 26),
    ("Tổng điểm", 12),
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
    ("Mã order", 11),
    ("Tên order", 34),
    ("Pha", 10),
    ("Mã task", 10),
    ("Hình thức", 22),
    ("Số lượng", 9),
    ("Ghi chú", 28),
    ("Điểm", 9),
)


def report_workbook(
    *,
    month: str,
    team: Sequence[TeamRow],
    details: Sequence[tuple[OrderNodePoints, Order, Decimal]],
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
                figures.steps_done,
                figures.pending_points,
                figures.returns_fault,
                figures.returns_other,
                figures.returns_codes,
                figures.late_count,
                figures.previous_points,
            ]
        )
    summary.append(["Tổng", "", sum(row.figures.points for row in team)])
    _shape(summary, SUMMARY_COLUMNS, header_row=2)

    detail = book.create_sheet("Chi tiết")
    detail.append([label for label, _ in DETAIL_COLUMNS])
    for points_row, order, factor in details:
        when = ensure_utc(points_row.first_completed_at).astimezone(tz).strftime("%d/%m/%Y %H:%M")
        person = names.get(points_row.user_id, "—")
        department = (
            departments.get(points_row.department_id, "") if points_row.department_id else ""
        )
        for item in items_from_json(points_row.items) or ():
            detail.append(
                [
                    when,
                    person,
                    department,
                    order.code,
                    order.title,
                    phase_label(points_row.phase),
                    "Việc khác" if item.free else item.code,
                    "" if item.free else item.form_label,
                    item.quantity,
                    item.note or "",
                    weigh(item.points, factor),
                ]
            )
    _shape(detail, DETAIL_COLUMNS, header_row=1)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _shape(sheet: Worksheet, columns: Sequence[tuple[str, int]], *, header_row: int) -> None:
    for index, (_label, width) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.cell(row=header_row, column=index).font = Font(bold=True)
    sheet.freeze_panes = f"A{header_row + 1}"


__all__ = ["report_workbook"]
