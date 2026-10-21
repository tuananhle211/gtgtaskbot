"""Excel in and out: the export of a listing, and the one-off import of the
inventory file (``Apexmed_KhaiBaoTaiNguyen_v3.xlsx``).

Export
------
The rows of the current filter, as the viewer sees them, one sheet. The id
and the phone are **text cells** (``@``): a 15-digit id stays itself and a
leading zero stays (rule 2).

Import
------
Two steps, both from the same parse: a **preview** that writes nothing and
reports every row's errors, and a **commit** that writes everything at once
only when no row has an error (section 6 of the requirement):

* each sheet is a department (its name or code, matched without diacritics;
  "Người sử dụng" and "Tổng hợp" are skipped); headers on row 5, data from
  row 6; columns are matched by their header text;
* the pick-list values are matched by name or code, the holder by member
* code ("Mã NV") alone
  against the active accounts (an unknown or ambiguous name is an error);
* the month columns (``T10/2026`` …) become the status history - one line
  per filled cell, dated the first of that month - and the last filled one
  is the current status;
* an old code in the file is kept; a duplicate id within a type (in the
  file or in the database) is an error.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import select

from meobot.application.org.seats import load_seats
from meobot.application.resources.access import ResourceAccess, ResourceValidationError
from meobot.application.resources.service import (
    FIELD_LABELS,
    LIMITS,
    OptionCatalog,
    ResourceInput,
    ResourceRow,
    ResourceService,
)
from meobot.core.time import utcnow
from meobot.db.models.department import Department
from meobot.db.models.resource import Resource, ResourceOption
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import Seat
from meobot.domain.resources.models import (
    DEFAULT_HANDOVER_CODE,
    DEFAULT_STATUS_CODE,
    EMPLOYMENT_STATUS_LABELS,
    OptionKind,
    is_resource_code,
)
from meobot.domain.units.member_code import normalise_member_code

#: The largest file the import takes (the base64 body is a third larger).
MAX_IMPORT_BYTES = 8 * 1024 * 1024
HEADER_ROW = 5
FIRST_DATA_ROW = 6
SKIPPED_SHEETS = ("nguoi su dung", "tong hop")


# --- text ------------------------------------------------------------------------------


def fold(text: Any) -> str:
    """Lower-case ASCII without diacritics, one space between words:
    ``"Người được bàn giao"`` → ``"nguoi duoc ban giao"``."""
    raw = str(text or "").replace("Đ", "D").replace("đ", "d")
    ascii_text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return " ".join(ascii_text.lower().split())


def cell_text(value: Any) -> str:
    """A cell as the text somebody typed: ``104000000000000.0`` → ``"104000000000000"``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def parse_day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()  # noqa: DTZ007 - a calendar day
        except ValueError:
            continue
    return None


# --- export ----------------------------------------------------------------------------

EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("code", "Mã tài nguyên"),
    ("type", "Loại"),
    ("external_id", "ID tài nguyên"),
    ("name", "Tên tài nguyên"),
    ("link", "Link"),
    ("department", "Ban quản lý"),
    ("holder", "Người được bàn giao"),
    ("employee", "Trạng thái nhân viên"),
    ("role", "Quyền đang có"),
    ("email", "Email đăng ký"),
    ("phone", "SĐT đăng ký"),
    ("two_factor", "Bảo mật 2 lớp"),
    ("two_factor_holder", "Người giữ mã 2 lớp"),
    ("origin", "Nguồn gốc"),
    ("started_on", "Ngày bắt đầu dùng"),
    ("purpose", "Mục đích sử dụng"),
    ("handover", "Bàn giao về Tài nguyên"),
    ("status", "Trạng thái"),
    ("reviewed_at", "Ngày rà soát gần nhất"),
    ("note", "Ghi chú"),
    ("alerts", "Cảnh báo"),
)
#: Written as text whatever they look like.
TEXT_COLUMNS = frozenset({"external_id", "phone", "code"})


def export_rows(rows: Sequence[ResourceRow], *, tz: Any) -> bytes:
    """One sheet, the listing's columns, ids and phones as text."""
    book = Workbook()
    sheet = book.create_sheet("Tài nguyên", 0)
    for extra in book.worksheets[1:]:
        book.remove(extra)
    sheet.append([label for _key, label in EXPORT_COLUMNS])
    for row in rows:
        values = _export_values(row, tz)
        sheet.append([values[key] for key, _label in EXPORT_COLUMNS])
    for index, (key, _label) in enumerate(EXPORT_COLUMNS, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = 18 if key not in ("name", "purpose", "note") else 32
        if key in TEXT_COLUMNS:
            for cell in sheet[letter][1:]:
                cell.number_format = "@"
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


#: The import template (2026-10-20): ONE sheet, only what a line needs.
#: The department is a column ("Ban quản lý"), not a tab; the holder is the
#: member code alone ("Mã NV"). Everything else the system fills or the
#: screen completes later.
TEMPLATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("holder_code", "Mã NV"),
    ("type", "Loại tài nguyên"),
    ("external_id", "ID tài nguyên"),
    ("name", "Tên tài nguyên"),
    ("status", "Trạng thái"),
    ("link", "Link"),
    ("note", "Ghi chú"),
)
#: The note on the template's example lines: a line still carrying it is
#: skipped by the import (so the template goes through as it came).
SAMPLE_NOTE = "Dòng ví dụ - xoá hoặc ghi đè trước khi nhập"
TEMPLATE_SHEET = "Mẫu nhập"


def _sample_rows() -> list[list[Any]]:
    return [
        [
            "M001A",
            "Via",
            "61577285782640",
            "Via Nguyễn Văn A",
            "Hoạt động",
            "https://facebook.com/profile.php?id=61577285782640",
            SAMPLE_NOTE,
        ],
        ["M002B", "Gmail", "", "Gmail Biên tập 01", "Hoạt động", "", SAMPLE_NOTE],
    ]


def template_workbook(catalog: OptionCatalog) -> bytes:
    """The import template: a single "Tài nguyên" sheet - header on row 1,
    two example lines (skipped while their note is untouched), id and member
    code columns as text, drop-downs for ban / loại / trạng thái."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = TEMPLATE_SHEET
    for column, (_key, label) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column, value=label)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DAE3F1")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for sample in _sample_rows():
        sheet.append(sample)
    for row in (2, 3):
        for column in range(1, len(TEMPLATE_COLUMNS) + 1):
            sheet.cell(row=row, column=column).font = Font(italic=True, color="55627F")
    lists = {
        "type": [o.name for o in catalog.of_kind(OptionKind.TYPE)],
        "status": [o.name for o in catalog.of_kind(OptionKind.STATUS)],
    }
    for index, (key, _label) in enumerate(TEMPLATE_COLUMNS, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = 38 if key in ("name", "link", "note") else 20
        if key in ("external_id", "holder_code"):
            for row in range(2, 502):
                sheet.cell(row=row, column=index).number_format = "@"
        names = lists.get(key)
        # An inline list is limited to 255 characters and cannot hold commas.
        if names and all("," not in n for n in names) and len(",".join(names)) <= 255:
            validation = DataValidation(
                type="list", formula1='"' + ",".join(names) + '"', allow_blank=True
            )
            validation.add(f"{letter}2:{letter}501")
            sheet.add_data_validation(validation)
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _export_values(row: ResourceRow, tz: Any) -> dict[str, Any]:
    resource = row.resource
    reviewed = (
        resource.reviewed_at.astimezone(tz).strftime("%d/%m/%Y") if resource.reviewed_at else ""
    )
    return {
        "code": resource.code,
        "type": row.type.name,
        "external_id": resource.external_id,
        "name": resource.name,
        "link": resource.link or "",
        "department": row.department.name,
        "holder": row.holder.full_name if row.holder else "",
        "employee": row.holder.employment_status_label if row.holder else "",
        "role": row.role.name if row.role else "",
        "email": resource.email or "",
        "phone": resource.phone or "",
        "two_factor": row.two_factor.name if row.two_factor else "",
        "two_factor_holder": row.two_factor_holder.full_name if row.two_factor_holder else "",
        "origin": row.origin.name if row.origin else "",
        "started_on": resource.started_on.strftime("%d/%m/%Y") if resource.started_on else "",
        "purpose": resource.purpose or "",
        "handover": row.handover.name,
        "status": row.status.name,
        "reviewed_at": reviewed,
        "note": resource.note or "",
        "alerts": "; ".join(alert.label for alert in row.alerts),
    }


# --- import: parsing -------------------------------------------------------------------

#: Header text (folded) → field. Checked in order: the first match wins.
HEADER_RULES: tuple[tuple[str, str], ...] = (
    ("stt", "_skip"),
    ("trang thai nguoi dung", "_skip"),
    ("ma tai nguyen", "code"),
    ("ban quan ly", "department"),
    ("ma nv", "holder_code"),
    ("ma nhan vien", "holder_code"),
    ("loai", "type"),
    ("id tai nguyen", "external_id"),
    ("ten tai nguyen", "name"),
    ("link", "link"),
    ("nguoi duoc ban giao", "holder"),
    ("quyen", "role"),
    ("email", "email"),
    ("sdt", "phone"),
    ("so dien thoai", "phone"),
    ("ai dang giu ma 2 lop", "two_factor_holder"),
    ("nguoi giu ma 2 lop", "two_factor_holder"),
    ("giu ma 2 lop", "two_factor_holder"),
    ("bao mat 2 lop", "two_factor"),
    ("2 lop", "two_factor"),
    ("nguon goc", "origin"),
    ("ngay bat dau", "started_on"),
    ("muc dich", "purpose"),
    ("ghi chu", "note"),
    ("ban giao ve", "handover"),
    ("ban giao", "handover"),
    ("trang thai hien tai", "status"),
    ("trang thai", "status"),
    ("id", "external_id"),
    ("ten", "name"),
    # A column naming the department: an aggregate sheet ("Dữ liệu gộp"),
    # not a department's tab - the tab's name is its department.
    ("ban", "_aggregate"),
)
_MONTH = re.compile(r"^(?:t|thang)?\s*(\d{1,2})\s*[/\-.]\s*(\d{4})$")
OPTION_FIELDS: Mapping[str, OptionKind] = {
    "type": OptionKind.TYPE,
    "role": OptionKind.ROLE,
    "two_factor": OptionKind.TWO_FACTOR,
    "origin": OptionKind.ORIGIN,
    "handover": OptionKind.HANDOVER,
    "status": OptionKind.STATUS,
}
#: Pick-lists the import may add entries to (a status needs a group: never).
CREATABLE_KINDS: frozenset[OptionKind] = frozenset(
    {
        OptionKind.TYPE,
        OptionKind.ROLE,
        OptionKind.ORIGIN,
        OptionKind.TWO_FACTOR,
        OptionKind.HANDOVER,
    }
)
PEOPLE_FIELDS = ("holder", "two_factor_holder")
#: Nothing a resource is made of is required any more (2026-10-16): a
#: blank id takes the code, a blank type "Khác", a blank name is made from
#: the type and the id - all flagged, all to be completed on the screen.
REQUIRED: tuple[str, ...] = ()
#: The type a row without one gets.
FALLBACK_TYPE_CODE = "OTHER"
#: The rows above which a department tab's header is looked for.
HEADER_SCAN_ROWS = 8


def header_field(text: Any, rules: tuple[tuple[str, str], ...] = HEADER_RULES) -> str | None:
    """The field a column header names: a field key, ``"month:YYYY-MM"``,
    ``"_skip"`` or ``None`` for a header nothing is known about."""
    folded = fold(text)
    if not folded:
        return None
    month = _MONTH.match(folded.replace(" ", ""))
    if month:
        number, year = int(month.group(1)), int(month.group(2))
        if 1 <= number <= 12:
            return f"month:{year:04d}-{number:02d}"
    for fragment, key in rules:
        if folded.startswith(fragment) or (len(fragment) > 4 and fragment in folded):
            return key
    return None


def _columns(
    header_cells: Sequence[Any], rules: tuple[tuple[str, str], ...]
) -> tuple[dict[int, str], list[str]]:
    columns: dict[int, str] = {}
    unknown: list[str] = []
    for index, header in enumerate(header_cells):
        key = header_field(header, rules)
        if key is None:
            if cell_text(header):
                unknown.append(cell_text(header))
            continue
        if key not in ("_skip",) and key not in columns.values():
            columns[index] = key
    return columns, unknown


def _find_header(
    sheet: Any, rules: tuple[tuple[str, str], ...], must: tuple[str, ...]
) -> tuple[int, dict[int, str], list[str]] | None:
    """The first row (within the top rows) whose headers name every field in
    ``must``: a department tab, a people tab. ``None`` = not such a sheet."""
    for row_no, cells in enumerate(
        sheet.iter_rows(min_row=1, max_row=HEADER_SCAN_ROWS, values_only=True), start=1
    ):
        columns, unknown = _columns(cells, rules)
        if all(field in columns.values() for field in must):
            return row_no, columns, unknown
    return None


@dataclass(slots=True)
class ImportRow:
    sheet: str
    row_no: int
    department: Department | None = None
    code: str | None = None
    #: What the cells said, by field, for the preview table.
    raw: dict[str, str] = field(default_factory=dict)
    #: Resolved values, as the service takes them.
    values: dict[str, Any] = field(default_factory=dict)
    #: ``(YYYY-MM, status option)`` for every filled month cell, in order.
    months: list[tuple[str, ResourceOption]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Pick-list entries this row needs that do not exist: ``(kind, text)``.
    missing_options: list[tuple[OptionKind, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class SheetSummary:
    name: str
    department: Department | None
    rows: int
    errors: int
    skipped: bool = False
    unknown_headers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ImportPreview:
    sheets: list[SheetSummary]
    rows: list[ImportRow]
    #: Template rows left out: pre-numbered lines with a code and nothing else.
    skipped_rows: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for row in self.rows if row.errors)

    @property
    def ok_count(self) -> int:
        return sum(1 for row in self.rows if row.ok)

    @property
    def missing_options(self) -> list[tuple[OptionKind, str]]:
        found: dict[tuple[OptionKind, str], tuple[OptionKind, str]] = {}
        for row in self.rows:
            for kind, text in row.missing_options:
                found.setdefault((kind, fold(text)), (kind, text))
        return list(found.values())


@dataclass(frozen=True, slots=True)
class ImportContext:
    departments: Sequence[Department]
    catalog: OptionCatalog
    people: Sequence[User]
    #: ``(type option id, folded external id)`` already in the database.
    taken_ids: frozenset[tuple[uuid.UUID, str]]
    taken_codes: frozenset[str]
    #: Every open seat by person, for "the holder's ban".
    seats: Mapping[uuid.UUID, tuple[Seat, ...]] = field(default_factory=dict)
    #: Whether missing pick-list entries will be created on commit: the
    #: preview then warns instead of refusing.
    create_options: bool = False


def decode_upload(data: str) -> bytes:
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ResourceValidationError(
            "File không đọc được (base64 không hợp lệ).",
            details={"reason": "import_file_invalid", "field": "data"},
        ) from error
    if len(raw) > MAX_IMPORT_BYTES:
        raise ResourceValidationError(
            "File lớn hơn 8 MB.", details={"reason": "import_file_too_large", "field": "data"}
        )
    return raw


def parse_workbook(raw: bytes, context: ImportContext) -> ImportPreview:
    try:
        book = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as error:  # openpyxl raises several kinds
        raise ResourceValidationError(
            "File không phải Excel (.xlsx) hợp lệ.",
            details={"reason": "import_file_invalid", "field": "data"},
        ) from error
    sheets: list[SheetSummary] = []
    rows: list[ImportRow] = []
    seen_ids: dict[tuple[uuid.UUID, str], ImportRow] = {}
    seen_codes: dict[str, ImportRow] = {}
    skipped_rows = 0
    for sheet in book.worksheets:
        # The "Người sử dụng" tab (and any other non-inventory sheet) has no
        # Loại / ID / Tên header: it is listed as skipped. People are never
        # read from the file - the one link to an account is "Mã NV".
        found = _find_header(sheet, HEADER_RULES, ("type", "external_id", "name"))
        if found is None:
            sheets.append(
                SheetSummary(name=sheet.title, department=None, rows=0, errors=0, skipped=True)
            )
            continue
        header_row, columns, unknown = found
        per_row = "department" in columns.values()
        if "_aggregate" in columns.values() and not per_row:
            # A roll-up of the department tabs: every row is already in its tab.
            sheets.append(
                SheetSummary(name=sheet.title, department=None, rows=0, errors=0, skipped=True)
            )
            continue
        # The template's own sheet names no department: the ban follows the holder.
        department = (
            None
            if fold(sheet.title) == fold(TEMPLATE_SHEET)
            else _match_department(sheet.title, context.departments)
        )
        department_column = next(
            (index for index, key in columns.items() if key == "department"), None
        )
        parsed: list[ImportRow] = []
        for row_no, cells in enumerate(
            sheet.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1
        ):
            if not any(cell_text(cell) for cell in cells):
                continue
            row = ImportRow(sheet=sheet.title, row_no=row_no, department=department)
            if department_column is not None:
                # An explicit "Ban quản lý" column (older files): it wins.
                text = cell_text(
                    cells[department_column] if department_column < len(cells) else None
                )
                if text:
                    row.department = _match_department(text, context.departments)
                    if row.department is None:
                        row.errors.append(f"Ban quản lý '{text}' không khớp phòng ban nào.")
            _read_row(row, cells, columns, context)
            if _is_template_row(row):
                skipped_rows += 1
                continue
            _check_duplicates(row, context, seen_ids, seen_codes)
            parsed.append(row)
        sheets.append(
            SheetSummary(
                name=sheet.title,
                department=None if department_column is not None else department,
                rows=len(parsed),
                errors=sum(1 for row in parsed if row.errors),
                unknown_headers=unknown,
            )
        )
        rows.extend(parsed)
    book.close()
    return ImportPreview(sheets=sheets, rows=rows, skipped_rows=skipped_rows)


def _is_template_row(row: ImportRow) -> bool:
    """A pre-numbered line of the template: a code, at most a type and the
    name of whoever the line is reserved for, and nothing a resource is
    made of. Also one of the template's own example lines (its note is
    :data:`SAMPLE_NOTE`), left in the file as it came."""
    if row.raw.get("note", "").strip() == SAMPLE_NOTE:
        return True
    filled = {
        key
        for key, value in row.raw.items()
        if value and key not in ("code", "type", "holder", "holder_code", "department")
    }
    return not filled and not row.months


def _match_department(title: str, departments: Sequence[Department]) -> Department | None:
    wanted = fold(title)
    for department in departments:
        if wanted in (fold(department.name), fold(department.code)):
            return department
    partial = [
        department
        for department in departments
        if fold(department.name)
        and (fold(department.name) in wanted or wanted in fold(department.name))
    ]
    return partial[0] if len(partial) == 1 else None


def _person_by_code(context: ImportContext, code: str) -> User | None:
    """The account whose member code ("Mã nhân viên") is ``code``: the one
    link between the file and the people (2026-10-19; names are not used)."""
    wanted = normalise_member_code(code)
    if wanted is None:
        return None
    for user in context.people:
        if user.member_code == wanted:
            return user
    return None


def _person_by_name(context: ImportContext, name: str) -> User | str | None:
    """For the one column that carries no code ("Ai đang giữ mã 2 lớp"): the
    user, ``None`` when there is none, or an error sentence when several
    share the name."""
    matches = [user for user in context.people if fold(user.full_name) == fold(name)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    return f"'{name}' trùng tên {len(matches)} nhân viên."


def _match_holder(row: ImportRow, context: ImportContext) -> None:
    """ "Người được bàn giao" is matched by "Mã NV" alone: the name column is
    informative only. No code = nobody (a warning, when the line has a ban
    of its own); a code no account carries = an ERROR (2026-10-20) - the
    file is not written until the OWNER / ADMIN gives that member code on
    the members screen. No account is ever created from the file."""
    code = normalise_member_code(row.raw.get("holder_code") or None)
    name = " ".join(row.raw.get("holder", "").split())
    if code is None:
        if name:
            row.warnings.append(
                f"Không có Mã NV: bỏ qua người giữ '{name}' (tài nguyên chưa gắn với ai)."
            )
        return
    match = _person_by_code(context, code)
    if match is not None:
        row.values["holder_user_id"] = match.id
        return
    # An unknown code is an error, whatever else the line says: nothing is
    # written until the account carries that member code.
    row.errors.append(
        f"Mã NV '{code}' chưa có trong hệ thống." + (f" (file ghi '{name}')" if name else "")
    )


#: The order a person's several departments are tried in (2026-10-20): ADS,
#: then Dựng, Thiết kế, Biên tập; any other department after them, the one
#: listed first winning.
_DEPARTMENT_PRIORITY: tuple[str, ...] = ("ADS", "DUNG", "THIET_KE", "BIEN_TAP")


def department_of_seats(seats: Sequence[Seat]) -> uuid.UUID | None:
    """The department a resource follows when its holder sits in several."""
    if not seats:
        return None

    def rank(seat: Seat) -> int:
        if seat.code.upper() == "ADS":
            return 0
        try:
            return 1 + _DEPARTMENT_PRIORITY.index(seat.function.value)
        except ValueError:
            return len(_DEPARTMENT_PRIORITY) + 1

    # ``min`` keeps the first of equals: the seats come in the departments' order.
    return min(seats, key=rank).department_id


def _resolve_department(row: ImportRow, context: ImportContext) -> None:
    """The ban of a line without one of its own: the holder's (2026-10-20).
    "Mã NV" is then required - no code, an unknown code or a person with no
    seat leaves the line without a ban, which is an error."""
    if row.department is not None:
        return
    # What _match_holder said about a missing holder is now an error, not a warning.
    row.warnings = [w for w in row.warnings if "Mã NV" not in w]
    holder_id = row.values.get("holder_user_id")
    if holder_id is None:
        # An unknown code was already refused by ``_match_holder``.
        if normalise_member_code(row.raw.get("holder_code") or None) is None:
            row.errors.append("Thiếu Mã NV: cần mã nhân viên để biết tài nguyên thuộc ban nào.")
        return
    wanted = department_of_seats(context.seats.get(holder_id, ()))
    row.department = next((d for d in context.departments if d.id == wanted), None)
    if row.department is None:
        row.errors.append("Nhân viên này chưa thuộc ban nào: gán ban cho họ trước.")
    else:
        row.warnings.append(f"Ban lấy theo người giữ: {row.department.name}.")


def _read_row(
    row: ImportRow, cells: Sequence[Any], columns: Mapping[int, str], context: ImportContext
) -> None:
    catalog = context.catalog
    texts: dict[str, Any] = {}
    months: list[tuple[str, str]] = []
    for index, key in columns.items():
        value = cells[index] if index < len(cells) else None
        if key.startswith("month:"):
            text = cell_text(value)
            if text:
                months.append((key[6:], text))
            continue
        texts[key] = value if key == "started_on" else cell_text(value)
    row.raw = {key: cell_text(value) for key, value in texts.items()}
    if _is_template_row(row):
        return
    # The id is the platform's, and a tool account (Adobe, Magnific...) has
    # none of its own: a blank one is no error, the resource code stands in
    # (one email often serves several such accounts, so it is no id either).
    if not row.raw.get("external_id"):
        row.warnings.append("Không có ID: hệ thống lấy mã tài nguyên làm ID.")
    for key in REQUIRED:
        if not row.raw.get(key):
            row.errors.append(f"Thiếu {_label(key).lower()}.")
    code = row.raw.get("code", "").upper()
    if code:
        if is_resource_code(code):
            row.code = code
        else:
            row.errors.append(f"Mã tài nguyên {code!r} không đúng dạng ADS-001.")
    for key, kind in OPTION_FIELDS.items():
        text = row.raw.get(key, "")
        if key == "status":
            continue
        if not text:
            if key == "handover":
                option = catalog.by_code(OptionKind.HANDOVER, DEFAULT_HANDOVER_CODE)
                row.values["handover_id"] = option.id if option else None
                row.warnings.append("Chưa ghi bàn giao: lấy 'Chưa bàn giao'.")
            elif key == "type":
                fallback = catalog.by_code(OptionKind.TYPE, FALLBACK_TYPE_CODE)
                if fallback is None:
                    row.errors.append("Thiếu loại tài nguyên.")
                else:
                    row.values["type_id"] = fallback.id
                    row.warnings.append(
                        f"Thiếu loại: tạm để '{fallback.name}', bổ sung sau trên màn hình."
                    )
            continue
        option = _match_option(text, catalog.of_kind(kind, include_inactive=True))
        if option is not None:
            row.values[_id_field(key)] = option.id
        elif kind in CREATABLE_KINDS and context.create_options:
            row.missing_options.append((kind, text))
            row.warnings.append(f"{_label(key)}: '{text}' sẽ được thêm vào danh sách lựa chọn.")
        else:
            row.missing_options.append((kind, text))
            row.errors.append(f"{_label(key)}: '{text}' không có trong danh sách lựa chọn.")
    _match_holder(row, context)
    _resolve_department(row, context)
    two_factor_holder = " ".join(row.raw.get("two_factor_holder", "").split())
    if two_factor_holder:
        match = _person_by_name(context, two_factor_holder)
        if isinstance(match, User):
            row.values["two_factor_holder_user_id"] = match.id
        elif isinstance(match, str):
            row.errors.append(f"{_label('two_factor_holder')}: {match}")
        else:
            row.warnings.append(
                f"{_label('two_factor_holder')}: '{two_factor_holder}' không khớp tên nhân viên "
                "nào, bỏ trống."
            )
    for key in ("external_id", "name", "link", "email", "phone", "purpose", "note"):
        text = row.raw.get(key, "")
        if text:
            row.values[key] = text
    if not row.values.get("name"):
        type_option = catalog.get(row.values.get("type_id"))
        stem = type_option.name if type_option is not None else "Tài nguyên"
        made = f"{stem} {row.values['external_id']}" if row.values.get("external_id") else stem
        row.values["name"] = made[: LIMITS["name"]]
        row.warnings.append(f"Thiếu tên: tạm đặt '{row.values['name']}', bổ sung sau.")
    started = texts.get("started_on")
    if started not in (None, ""):
        day = parse_day(started)
        if day is None:
            row.errors.append(f"Ngày bắt đầu dùng '{cell_text(started)}' không đọc được.")
        else:
            row.values["started_on"] = day
    # The month columns, in calendar order; the last filled one is the status.
    statuses = catalog.of_kind(OptionKind.STATUS, include_inactive=True)
    for month, text in sorted(months):
        option = _match_option(text, statuses)
        if option is None:
            row.errors.append(
                f"Tháng {month[5:]}/{month[:4]}: trạng thái '{text}' không có trong danh sách."
            )
        else:
            row.months.append((month, option))
    current = row.raw.get("status", "")
    if row.months:
        row.values["status_id"] = row.months[-1][1].id
    elif current:
        option = _match_option(current, statuses)
        if option is None:
            row.errors.append(f"Trạng thái '{current}' không có trong danh sách lựa chọn.")
        else:
            row.values["status_id"] = option.id
    else:
        default = catalog.by_code(OptionKind.STATUS, DEFAULT_STATUS_CODE)
        if default is None:
            row.errors.append("Chưa có trạng thái (không ô tháng nào có giá trị).")
        else:
            row.values["status_id"] = default.id
            row.warnings.append(f"Chưa có trạng thái tháng nào: lấy '{default.name}'.")
    if row.department is not None:
        row.values["department_id"] = row.department.id


def _check_duplicates(
    row: ImportRow,
    context: ImportContext,
    seen_ids: dict[tuple[uuid.UUID, str], ImportRow],
    seen_codes: dict[str, ImportRow],
) -> None:
    type_id = row.values.get("type_id")
    external_id = row.values.get("external_id")
    if type_id is not None and external_id:
        key = (type_id, fold(external_id))
        if key in context.taken_ids:
            row.errors.append(f"ID '{external_id}' đã có trong hệ thống ở cùng loại.")
        elif key in seen_ids:
            earlier = seen_ids[key]
            row.errors.append(
                f"ID '{external_id}' trùng với tab {earlier.sheet} dòng {earlier.row_no}."
            )
        else:
            seen_ids[key] = row
    if row.code:
        if row.code in context.taken_codes:
            row.errors.append(f"Mã {row.code} đã có trong hệ thống.")
        elif row.code in seen_codes:
            # The file numbered two lines alike: the later one gets a fresh code.
            earlier = seen_codes[row.code]
            row.warnings.append(
                f"Mã {row.code} trùng với tab {earlier.sheet} dòng {earlier.row_no}: "
                "dòng này sẽ được cấp mã mới."
            )
            row.code = None
        else:
            seen_codes[row.code] = row


def _loose(text: str) -> str:
    """Folded, with the spacing around ``/`` and in parentheses levelled, so
    "Chủ sở hữu / Admin" is "Chủ sở hữu/Admin" and "Die / Mất" is "Die/Mất"."""
    return re.sub(r"\s*([/()])\s*", r"\1", fold(text))


def _match_option(text: str, options: Iterable[ResourceOption]) -> ResourceOption | None:
    """The entry a cell means: by name or code, spacing forgiven; else the
    one entry whose name the cell contains ("Chủ tài khoản (Via/AI)" → Chủ
    tài khoản, "Được chia sẻ dùng chung" → Dùng chung)."""
    rows = list(options)
    wanted = _loose(text)
    for option in rows:
        if wanted in (_loose(option.name), fold(option.code)):
            return option
    partial = [
        option for option in rows if len(_loose(option.name)) >= 4 and _loose(option.name) in wanted
    ]
    if len(partial) == 1:
        return partial[0]
    return None


def _id_field(key: str) -> str:
    return f"{key}_user_id" if key in PEOPLE_FIELDS else f"{key}_id"


def _label(key: str) -> str:
    return FIELD_LABELS.get(
        _id_field(key) if key in OPTION_FIELDS or key in PEOPLE_FIELDS else key, key
    )


# --- import: the service side ---------------------------------------------------------


class ResourceImportService:
    def __init__(self, service: ResourceService) -> None:
        self._service = service
        self._session = service.session

    async def context(self, *, create_options: bool = False) -> ImportContext:
        departments = (
            await self._session.scalars(
                select(Department)
                .where(Department.active.is_(True))
                .order_by(Department.sort_order)
            )
        ).all()
        people = (
            await self._session.scalars(
                select(User).where(User.deleted_at.is_(None), User.active.is_(True))
            )
        ).all()
        taken = (
            await self._session.execute(
                select(Resource.type_id, Resource.external_id, Resource.code)
            )
        ).all()
        return ImportContext(
            departments=departments,
            catalog=await self._service.catalog(),
            people=people,
            seats=await load_seats(self._session, [user.id for user in people]),
            taken_ids=frozenset(
                (type_id, fold(external_id)) for type_id, external_id, _code in taken
            ),
            taken_codes=frozenset(code for _type_id, _external_id, code in taken),
            create_options=create_options,
        )

    async def preview(
        self,
        access: ResourceAccess,
        data: str,
        *,
        create_options: bool = False,
    ) -> ImportPreview:
        access.require("import_excel")
        await self._service.ensure_seed_options()
        return parse_workbook(
            decode_upload(data), await self.context(create_options=create_options)
        )

    async def commit(
        self,
        access: ResourceAccess,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        data: str,
        create_options: bool = False,
    ) -> ImportPreview:
        """Write every row - only when none has an error (422 ``import_has_errors``).
        With ``create_options`` the missing pick-list entries are created
        first (and the file read again)."""
        access.require("import_excel")
        raw = decode_upload(data)
        await self._service.ensure_seed_options()
        preview = parse_workbook(raw, await self.context(create_options=create_options))
        if preview.error_count or not preview.rows:
            raise ResourceValidationError(
                "File còn dòng lỗi (hoặc không có dòng nào). Sửa trong file rồi tải lại."
                if preview.rows
                else "File không có dòng dữ liệu nào.",
                details={
                    "reason": "import_has_errors" if preview.rows else "import_empty",
                    "errors": preview.error_count,
                },
            )
        created_options = 0
        if create_options:
            for kind, text in preview.missing_options:
                await self._service.create_option(
                    access=access, actor=actor, request_id=request_id, kind=kind, name=text
                )
                created_options += 1
        if created_options:
            preview = parse_workbook(raw, await self.context())
            if preview.error_count:
                raise ResourceValidationError(
                    "File còn dòng lỗi sau khi tạo lựa chọn. Tải lại để xem.",
                    details={"reason": "import_has_errors", "errors": preview.error_count},
                )
        now = utcnow()
        for row in preview.rows:
            data_in = ResourceInput(**row.values)
            last_month = row.months[-1][0] if row.months else None
            reviewed_at = min(_month_start(last_month), now) if last_month else now
            resource = await self._service.create(
                # Importing a file adds its lines: IMPORT carries CREATE.
                access=replace(access, create=True),
                actor=actor,
                request_id=request_id,
                data=data_in,
                code=row.code,
                reviewed_at=reviewed_at,
                audit=False,
                status_line=not row.months,
            )
            previous: uuid.UUID | None = None
            for month, option in row.months:
                # Dated the first of its month, as the requirement says - even
                # a month still to come: the file is the statement of record.
                await self._service.add_status_line(
                    resource,
                    from_status_id=previous,
                    to_status_id=option.id,
                    at=_month_start(month),
                    actor=actor,
                    note=f"Nhập từ Excel (T{int(month[5:])}/{month[:4]})",
                )
                previous = option.id
        await self._session.flush()
        await self._service.record(
            actor,
            request_id,
            AuditAction.RESOURCE_IMPORTED,
            "resource_import",
            None,
            None,
            {
                "rows": len(preview.rows),
                "sheets": [sheet.name for sheet in preview.sheets if not sheet.skipped],
                "created_options": created_options,
            },
        )
        return preview


def _month_start(month: str) -> datetime:
    year, number = (int(part) for part in month.split("-", 1))
    return datetime(year, number, 1, tzinfo=UTC)


def employment_label(user: User) -> str:
    return EMPLOYMENT_STATUS_LABELS[user.employment_status]


__all__ = [
    "EXPORT_COLUMNS",
    "SAMPLE_NOTE",
    "TEMPLATE_COLUMNS",
    "ImportContext",
    "ImportPreview",
    "ImportRow",
    "ResourceImportService",
    "SheetSummary",
    "cell_text",
    "decode_upload",
    "export_rows",
    "fold",
    "header_field",
    "parse_workbook",
    "template_workbook",
]
