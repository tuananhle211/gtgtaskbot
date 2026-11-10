"""Reading the time clock's workbook (0100, HCNS's "DỮ LIỆU CHẤM CÔNG").

HCNS exports the machines' punches and lays them out one tab per site ("VP -
T10", "CS HN - T10", "CS HCM - T10"): a title, then a header row
``Mã nhân viên | Mã chấm công | Tên nhân viên | Phòng ban | Ngày | Thứ | Vào |
Ra …`` (or ``Vào 1 | Ra 1 | Vào 2 …``) and one row per person per day. A tab
is read when its header (within the first 15 rows) holds "Mã nhân viên",
"Ngày" and at least one "Vào" / "Ra" column; every other tab (the code list,
the raw machine dumps, notes) is listed as skipped. Times come as text
("08:13", "08:13:00"), as times, or as a fraction of a day; the day as a date
or "dd/mm/yyyy". The first "Vào" and the last "Ra" of the row are the day's
check-in / check-out.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from openpyxl import load_workbook

from meobot.domain.identity.labels import fold

HEADER_SCAN_ROWS = 15
MAX_ROWS = 60_000

_VAO = re.compile(r"^(vao|gio vao|check in)( \d+)?$")
_RA = re.compile(r"^(ra|gio ra|check out)( \d+)?$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_DAY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


@dataclass(frozen=True, slots=True)
class PunchRow:
    code: str
    machine_code: str | None
    day: date
    check_in: time | None
    check_out: time | None
    sheet: str
    line: int


@dataclass(slots=True)
class SheetReport:
    name: str
    read: bool
    reason: str | None = None
    rows: int = 0
    bad_rows: int = 0


@dataclass(slots=True)
class ParsedFile:
    sheets: list[SheetReport] = field(default_factory=list)
    rows: list[PunchRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def clock_of(value: Any) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, (int, float)) and 0 <= float(value) < 1:
        minutes = round(float(value) * 24 * 60)
        return time(minutes // 60 % 24, minutes % 60)
    text = str(value).strip()
    match = _CLOCK.match(text)
    if match is None:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return time(hours, minutes)


def day_of(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(value))
    match = _DAY.match(str(value or "").strip())
    if match is None:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def _header(row: Iterable[Any]) -> dict[str, Any] | None:
    cells = [fold(str(cell)) if cell is not None else "" for cell in row]
    if "ma nhan vien" not in cells or "ngay" not in cells:
        return None
    ins = [index for index, cell in enumerate(cells) if _VAO.match(cell)]
    outs = [index for index, cell in enumerate(cells) if _RA.match(cell)]
    if not ins and not outs:
        return None
    return {
        "code": cells.index("ma nhan vien"),
        "machine": cells.index("ma cham cong") if "ma cham cong" in cells else None,
        "day": cells.index("ngay"),
        "ins": ins,
        "outs": outs,
    }


def read_workbook(data: bytes) -> ParsedFile:
    parsed = ParsedFile()
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        parsed.problems.append("Không đọc được file. Gửi file Excel (.xlsx).")
        return parsed
    try:
        for sheet in book.worksheets:
            report = SheetReport(name=sheet.title, read=False)
            parsed.sheets.append(report)
            columns: dict[str, Any] | None = None
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if columns is None:
                    if number > HEADER_SCAN_ROWS:
                        break
                    columns = _header(row)
                    continue
                cells = list(row)
                if not any(cell not in (None, "") for cell in cells):
                    continue

                def cell(index: int | None, cells: list[Any] = cells) -> Any:
                    return cells[index] if index is not None and index < len(cells) else None

                code = str(cell(columns["code"]) or "").strip()
                day = day_of(cell(columns["day"]))
                if not code or day is None:
                    report.bad_rows += 1
                    continue
                ins = [clock_of(cell(index)) for index in columns["ins"]]
                outs = [clock_of(cell(index)) for index in columns["outs"]]
                ins_found = [item for item in ins if item is not None]
                outs_found = [item for item in outs if item is not None]
                machine = cell(columns["machine"])
                parsed.rows.append(
                    PunchRow(
                        code=code,
                        machine_code=None if machine in (None, "") else str(machine).strip()[:40],
                        day=day,
                        check_in=min(ins_found) if ins_found else None,
                        check_out=max(outs_found) if outs_found else None,
                        sheet=sheet.title[:120],
                        line=number,
                    )
                )
                report.rows += 1
                if len(parsed.rows) > MAX_ROWS:
                    parsed.problems.append(f"File quá {MAX_ROWS} dòng; tách nhỏ rồi nhập.")
                    return parsed
            if columns is None:
                report.reason = "Không có cột Mã nhân viên / Ngày / Vào / Ra"
            else:
                report.read = True
    finally:
        book.close()
    if not any(report.read for report in parsed.sheets):
        parsed.problems.append(
            "Không tab nào có cột Mã nhân viên, Ngày, Vào, Ra (dòng tiêu đề trong 15 dòng đầu)."
        )
    return parsed


__all__ = ["ParsedFile", "PunchRow", "SheetReport", "clock_of", "day_of", "read_workbook"]
