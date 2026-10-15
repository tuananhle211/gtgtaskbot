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
* the pick-list values are matched by name or code, people by full name
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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from meobot.application.resources.access import ResourceAccess, ResourceValidationError
from meobot.application.resources.service import (
    FIELD_LABELS,
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
from meobot.domain.resources.models import (
    DEFAULT_HANDOVER_CODE,
    EMPLOYMENT_STATUS_LABELS,
    OptionKind,
    is_resource_code,
)

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
PEOPLE_FIELDS = ("holder", "two_factor_holder")
REQUIRED = ("type", "external_id", "name")


def header_field(text: Any) -> str | None:
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
    for fragment, key in HEADER_RULES:
        if folded.startswith(fragment) or (len(fragment) > 4 and fragment in folded):
            return key
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

    @property
    def error_count(self) -> int:
        return sum(1 for row in self.rows if row.errors)

    @property
    def ok_count(self) -> int:
        return sum(1 for row in self.rows if row.ok)


@dataclass(frozen=True, slots=True)
class ImportContext:
    departments: Sequence[Department]
    catalog: OptionCatalog
    people: Sequence[User]
    #: ``(type option id, folded external id)`` already in the database.
    taken_ids: frozenset[tuple[uuid.UUID, str]]
    taken_codes: frozenset[str]


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
    for sheet in book.worksheets:
        if fold(sheet.title) in SKIPPED_SHEETS:
            sheets.append(
                SheetSummary(name=sheet.title, department=None, rows=0, errors=0, skipped=True)
            )
            continue
        department = _match_department(sheet.title, context.departments)
        header_cells = next(
            sheet.iter_rows(min_row=HEADER_ROW, max_row=HEADER_ROW, values_only=True), ()
        )
        columns: dict[int, str] = {}
        unknown: list[str] = []
        for index, header in enumerate(header_cells):
            key = header_field(header)
            if key is None:
                if cell_text(header):
                    unknown.append(cell_text(header))
                continue
            if key != "_skip" and key not in columns.values():
                columns[index] = key
        parsed: list[ImportRow] = []
        for row_no, cells in enumerate(
            sheet.iter_rows(min_row=FIRST_DATA_ROW, values_only=True), start=FIRST_DATA_ROW
        ):
            if not any(cell_text(cell) for cell in cells):
                continue
            row = ImportRow(sheet=sheet.title, row_no=row_no, department=department)
            if department is None:
                row.errors.append("Tên tab không khớp phòng ban nào.")
            if not columns:
                row.errors.append(f"Không tìm thấy dòng tiêu đề ở dòng {HEADER_ROW}.")
            _read_row(row, cells, columns, context)
            _check_duplicates(row, context, seen_ids, seen_codes)
            parsed.append(row)
        sheets.append(
            SheetSummary(
                name=sheet.title,
                department=department,
                rows=len(parsed),
                errors=sum(1 for row in parsed if row.errors),
                unknown_headers=unknown,
            )
        )
        rows.extend(parsed)
    book.close()
    return ImportPreview(sheets=sheets, rows=rows)


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
            continue
        option = _match_option(text, catalog.of_kind(kind, include_inactive=True))
        if option is None:
            row.errors.append(f"{_label(key)}: '{text}' không có trong danh sách lựa chọn.")
        else:
            row.values[_id_field(key)] = option.id
    for key in PEOPLE_FIELDS:
        text = row.raw.get(key, "")
        if not text:
            continue
        matches = [user for user in context.people if fold(user.full_name) == fold(text)]
        if len(matches) == 1:
            row.values[_id_field(key)] = matches[0].id
        elif not matches:
            row.errors.append(f"{_label(key)}: '{text}' không khớp danh sách nhân viên.")
        else:
            row.errors.append(f"{_label(key)}: '{text}' trùng tên {len(matches)} nhân viên.")
    for key in ("external_id", "name", "link", "email", "phone", "purpose", "note"):
        text = row.raw.get(key, "")
        if text:
            row.values[key] = text
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
        row.errors.append("Chưa có trạng thái (không ô tháng nào có giá trị).")
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
            earlier = seen_codes[row.code]
            row.errors.append(f"Mã {row.code} trùng với tab {earlier.sheet} dòng {earlier.row_no}.")
        else:
            seen_codes[row.code] = row


def _match_option(text: str, options: Iterable[ResourceOption]) -> ResourceOption | None:
    wanted = fold(text)
    for option in options:
        if wanted in (fold(option.name), fold(option.code)):
            return option
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

    async def context(self) -> ImportContext:
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
            taken_ids=frozenset(
                (type_id, fold(external_id)) for type_id, external_id, _code in taken
            ),
            taken_codes=frozenset(code for _type_id, _external_id, code in taken),
        )

    async def preview(self, access: ResourceAccess, data: str) -> ImportPreview:
        access.require("import_excel")
        return parse_workbook(decode_upload(data), await self.context())

    async def commit(
        self, access: ResourceAccess, *, actor: Actor, request_id: uuid.UUID, data: str
    ) -> ImportPreview:
        """Write every row - only when none has an error (422 ``import_has_errors``)."""
        access.require("import_excel")
        preview = await self.preview(access, data)
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
        now = utcnow()
        for row in preview.rows:
            data_in = ResourceInput(**row.values)
            last_month = row.months[-1][0] if row.months else None
            reviewed_at = min(_month_start(last_month), now) if last_month else now
            resource = await self._service.create(
                access=access,
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
]
