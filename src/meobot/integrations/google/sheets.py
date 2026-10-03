"""Google Sheets access: protocol, in-memory fake, and the real REST client.

The real client speaks the Sheets v4 REST API over ``httpx`` with a bearer
token from :mod:`meobot.integrations.google.credentials`. It deliberately does
not use ``googleapiclient``: that library is synchronous, drags in a large
dependency tree, and would need a thread per call anyway.

Guarantees every implementation must keep:

* a timeout on every request, and bounded retries for transient failures only;
* vendor errors translated into :mod:`meobot.core.errors` types;
* cell contents never logged, credentials never logged;
* writes touch exactly the ranges they are given - never a whole row.
"""

from __future__ import annotations

import string
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from meobot.core.errors import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationNotConfiguredError,
    IntegrationRateLimitError,
    NotFoundError,
)
from meobot.core.logging import get_logger
from meobot.integrations.base import IntegrationConfig, call_with_retries
from meobot.integrations.google.credentials import GoogleCredentials

logger = get_logger(__name__)

PROVIDER = "google_sheets"
API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

#: A raw row keyed by its header text, exactly as the sheet spells it.
RawRow = dict[str, str]

#: Hard ceiling on rows read in one synchronisation, so a runaway sheet cannot
#: exhaust worker memory.
MAX_ROWS = 5000


@dataclass(frozen=True, slots=True)
class SheetRange:
    """Identifies a tab within a spreadsheet."""

    spreadsheet_id: str
    sheet_name: str
    header_row: int = 1


@dataclass(frozen=True, slots=True)
class WorksheetInfo:
    """One tab of a spreadsheet."""

    title: str
    sheet_id: int = 0
    row_count: int = 0
    column_count: int = 0


@dataclass(frozen=True, slots=True)
class SpreadsheetMetadata:
    """Spreadsheet identity and its tabs."""

    spreadsheet_id: str
    title: str
    worksheets: tuple[WorksheetInfo, ...] = ()

    @property
    def worksheet_names(self) -> list[str]:
        return [sheet.title for sheet in self.worksheets]


@dataclass(frozen=True, slots=True)
class CellUpdate:
    """One cell to write, in A1 notation relative to a tab."""

    sheet_name: str
    row_number: int
    column_index: int
    value: str

    @property
    def a1(self) -> str:
        """``'Tab'!C7`` - quoted so tab names with spaces work."""
        return f"'{self.sheet_name}'!{column_letter(self.column_index)}{self.row_number}"


def column_letter(index: int) -> str:
    """0-based column index -> spreadsheet column letters (0 -> A, 26 -> AA)."""
    if index < 0:
        raise ValueError("Column index must be non-negative")
    letters = ""
    current = index
    while True:
        letters = string.ascii_uppercase[current % 26] + letters
        current = current // 26 - 1
        if current < 0:
            return letters


@runtime_checkable
class SheetsClient(Protocol):
    """Read and (narrowly) write access to Google Sheets."""

    async def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        """Spreadsheet title and the list of tabs."""
        ...

    async def list_sheet_names(self, spreadsheet_id: str) -> list[str]:
        """Return the tab names of a spreadsheet."""
        ...

    async def read_headers(self, target: SheetRange) -> list[str]:
        """Return the header row as it appears in the sheet."""
        ...

    async def read_rows(self, target: SheetRange, *, limit: int | None = None) -> list[RawRow]:
        """Return data rows below the header, keyed by header text."""
        ...

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        """Return the raw cell matrix of an arbitrary A1 range."""
        ...

    async def batch_update(self, spreadsheet_id: str, updates: Sequence[CellUpdate]) -> int:
        """Write the given cells and return how many were updated."""
        ...

    async def ensure_worksheets(self, spreadsheet_id: str, titles: Sequence[str]) -> list[str]:
        """Create any missing tabs, renaming the default one first.

        Used to initialise a spreadsheet MeoBot just created. Returns the tab
        names that were added.
        """
        ...

    async def write_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: Sequence[Sequence[str]],
        *,
        start_cell: str = "A1",
    ) -> int:
        """Write a block of rows starting at ``start_cell``."""
        ...


def rows_to_dicts(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[RawRow]:
    """Zip a cell matrix onto header keys, padding short rows with ''."""
    return [
        {
            header: (str(row[index]) if index < len(row) and row[index] is not None else "")
            for index, header in enumerate(headers)
        }
        for row in rows
    ]


@dataclass
class FakeSheetsClient:
    """In-memory :class:`SheetsClient` for tests and local development.

    Args:
        sheets: ``{(spreadsheet_id, sheet_name): (headers, rows)}``.
    """

    sheets: dict[tuple[str, str], tuple[list[str], list[list[str]]]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    writes: list[CellUpdate] = field(default_factory=list)
    titles: dict[str, str] = field(default_factory=dict)

    def load(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        headers: list[str],
        rows: list[list[str]],
    ) -> None:
        """Seed one tab of fake data."""
        self.sheets[(spreadsheet_id, sheet_name)] = (headers, rows)

    def _get(self, target: SheetRange) -> tuple[list[str], list[list[str]]]:
        key = (target.spreadsheet_id, target.sheet_name)
        if key not in self.sheets:
            raise NotFoundError(
                f"Fake sheet not loaded: {target.sheet_name!r}",
                details={"spreadsheet_id": target.spreadsheet_id},
            )
        return self.sheets[key]

    async def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        self.calls.append("get_metadata")
        names = [name for (sid, name) in self.sheets if sid == spreadsheet_id]
        if not names:
            raise NotFoundError(
                f"Fake spreadsheet not loaded: {spreadsheet_id!r}",
                details={"spreadsheet_id": spreadsheet_id},
            )
        return SpreadsheetMetadata(
            spreadsheet_id=spreadsheet_id,
            title=self.titles.get(spreadsheet_id, f"Fake spreadsheet {spreadsheet_id}"),
            worksheets=tuple(
                WorksheetInfo(
                    title=name,
                    sheet_id=index,
                    row_count=len(self.sheets[(spreadsheet_id, name)][1]) + 1,
                    column_count=len(self.sheets[(spreadsheet_id, name)][0]),
                )
                for index, name in enumerate(names)
            ),
        )

    async def read_headers(self, target: SheetRange) -> list[str]:
        self.calls.append(f"read_headers:{target.sheet_name}")
        headers, _ = self._get(target)
        return list(headers)

    async def read_rows(self, target: SheetRange, *, limit: int | None = None) -> list[RawRow]:
        self.calls.append(f"read_rows:{target.sheet_name}")
        headers, rows = self._get(target)
        selected = rows if limit is None else rows[:limit]
        return rows_to_dicts(headers, selected)

    async def list_sheet_names(self, spreadsheet_id: str) -> list[str]:
        self.calls.append("list_sheet_names")
        return [name for (sid, name) in self.sheets if sid == spreadsheet_id]

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        self.calls.append("read_range")
        sheet_name = a1_range.split("!", 1)[0].strip("'")
        headers, rows = self._get(SheetRange(spreadsheet_id, sheet_name))
        return [list(headers), *[list(row) for row in rows]]

    async def batch_update(self, spreadsheet_id: str, updates: Sequence[CellUpdate]) -> int:
        self.calls.append("batch_update")
        self.writes.extend(updates)
        return len(updates)

    async def ensure_worksheets(self, spreadsheet_id: str, titles: Sequence[str]) -> list[str]:
        self.calls.append("ensure_worksheets")
        added: list[str] = []
        for title in titles:
            if (spreadsheet_id, title) not in self.sheets:
                self.sheets[(spreadsheet_id, title)] = ([], [])
                added.append(title)
        return added

    async def write_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: Sequence[Sequence[str]],
        *,
        start_cell: str = "A1",
    ) -> int:
        self.calls.append(f"write_rows:{sheet_name}")
        matrix = [list(row) for row in rows]
        if not matrix:
            return 0
        headers, existing = self.sheets.get((spreadsheet_id, sheet_name), ([], []))
        if start_cell == "A1":
            headers, existing = list(matrix[0]), [list(row) for row in matrix[1:]]
        else:
            existing = [*existing, *[list(row) for row in matrix]]
        self.sheets[(spreadsheet_id, sheet_name)] = (headers, existing)
        return len(matrix)


class NotConfiguredSheetsClient:
    """Placeholder used when no Google credentials are present.

    Every call raises :class:`IntegrationNotConfiguredError`, so a missing
    credential fails loudly at the call site instead of silently returning
    empty data - and every other part of MeoBot still starts.
    """

    provider = PROVIDER

    async def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        raise self._error("get_metadata")

    async def read_headers(self, target: SheetRange) -> list[str]:
        raise self._error("read_headers")

    async def read_rows(self, target: SheetRange, *, limit: int | None = None) -> list[RawRow]:
        raise self._error("read_rows")

    async def list_sheet_names(self, spreadsheet_id: str) -> list[str]:
        raise self._error("list_sheet_names")

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        raise self._error("read_range")

    async def batch_update(self, spreadsheet_id: str, updates: Sequence[CellUpdate]) -> int:
        raise self._error("batch_update")

    async def ensure_worksheets(self, spreadsheet_id: str, titles: Sequence[str]) -> list[str]:
        raise self._error("ensure_worksheets")

    async def write_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: Sequence[Sequence[str]],
        *,
        start_cell: str = "A1",
    ) -> int:
        raise self._error("write_rows")

    def _error(self, operation: str) -> IntegrationNotConfiguredError:
        return IntegrationNotConfiguredError(
            "Google Sheets chưa được cấu hình (GOOGLE_SERVICE_ACCOUNT_FILE trống).",
            provider=self.provider,
            details={"operation": operation},
        )


class GoogleSheetsClient:
    """Sheets v4 REST client backed by a service account.

    Args:
        credentials: Token source. Never logged, never returned.
        config: Timeout and retry budget.
        client: Injected ``httpx.AsyncClient`` (tests pass a MockTransport).
            When omitted, one is created lazily and owned by this instance.
    """

    provider = PROVIDER

    def __init__(
        self,
        credentials: GoogleCredentials,
        *,
        config: IntegrationConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credentials = credentials
        self._config = config or IntegrationConfig(provider=PROVIDER)
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client when this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Reads ------------------------------------------------------------
    async def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        """Fetch the spreadsheet title and its tabs."""
        payload = await self._request(
            "GET",
            f"/{spreadsheet_id}",
            operation="get_metadata",
            params={"fields": "properties.title,sheets.properties"},
            spreadsheet_id=spreadsheet_id,
        )
        properties = payload.get("properties") or {}
        worksheets: list[WorksheetInfo] = []
        for sheet in payload.get("sheets") or []:
            sheet_properties = sheet.get("properties") or {}
            grid = sheet_properties.get("gridProperties") or {}
            worksheets.append(
                WorksheetInfo(
                    title=str(sheet_properties.get("title", "")),
                    sheet_id=int(sheet_properties.get("sheetId", 0)),
                    row_count=int(grid.get("rowCount", 0)),
                    column_count=int(grid.get("columnCount", 0)),
                )
            )
        return SpreadsheetMetadata(
            spreadsheet_id=spreadsheet_id,
            title=str(properties.get("title", "")),
            worksheets=tuple(worksheets),
        )

    async def list_sheet_names(self, spreadsheet_id: str) -> list[str]:
        """Return the tab names of a spreadsheet."""
        metadata = await self.get_metadata(spreadsheet_id)
        return metadata.worksheet_names

    async def read_headers(self, target: SheetRange) -> list[str]:
        """Read the configured header row.

        Raises:
            NotFoundError: When the tab does not exist (typically a renamed tab).
        """
        a1 = f"'{target.sheet_name}'!{target.header_row}:{target.header_row}"
        matrix = await self.read_range(target.spreadsheet_id, a1)
        if not matrix:
            return []
        return [str(cell).strip() for cell in matrix[0]]

    async def read_rows(self, target: SheetRange, *, limit: int | None = None) -> list[RawRow]:
        """Read every data row under the header, keyed by header text."""
        headers = await self.read_headers(target)
        if not headers:
            raise NotFoundError(
                f"Sheet {target.sheet_name!r} has no header row at row {target.header_row}",
                details={
                    "spreadsheet_id": target.spreadsheet_id,
                    "sheet_name": target.sheet_name,
                    "header_row": target.header_row,
                },
            )
        first_data_row = target.header_row + 1
        last_row = first_data_row + (limit or MAX_ROWS) - 1
        last_column = column_letter(len(headers) - 1)
        a1 = f"'{target.sheet_name}'!A{first_data_row}:{last_column}{last_row}"
        matrix = await self.read_range(target.spreadsheet_id, a1)
        return rows_to_dicts(headers, matrix)

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        """Read an arbitrary A1 range as a matrix of strings."""
        payload = await self._request(
            "GET",
            f"/{spreadsheet_id}/values/{a1_range}",
            operation="read_range",
            params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
            spreadsheet_id=spreadsheet_id,
        )
        values = payload.get("values") or []
        return [[("" if cell is None else str(cell)) for cell in row] for row in values]

    # --- Writes -----------------------------------------------------------
    async def batch_update(self, spreadsheet_id: str, updates: Sequence[CellUpdate]) -> int:
        """Write exactly the given cells.

        Uses ``values:batchUpdate`` with one entry per cell, so no unrelated
        column is ever included in the written range.
        """
        if not updates:
            return 0
        body: dict[str, Any] = {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": update.a1, "values": [[update.value]]} for update in updates],
        }
        payload = await self._request(
            "POST",
            f"/{spreadsheet_id}/values:batchUpdate",
            operation="batch_update",
            json_body=body,
            spreadsheet_id=spreadsheet_id,
        )
        updated = payload.get("totalUpdatedCells")
        logger.info(
            "sheets_batch_update",
            extra={"spreadsheet_id": spreadsheet_id, "cells": len(updates)},
        )
        return int(updated) if isinstance(updated, int) else len(updates)

    async def ensure_worksheets(self, spreadsheet_id: str, titles: Sequence[str]) -> list[str]:
        """Make sure ``titles`` exist as tabs, in order.

        A freshly created spreadsheet has exactly one tab called "Sheet1" (or
        its locale equivalent). That tab is renamed to the first requested
        title rather than left behind, so a generated file has no stray empty
        worksheet.
        """
        if not titles:
            return []
        metadata = await self.get_metadata(spreadsheet_id)
        existing = {sheet.title for sheet in metadata.worksheets}
        requests: list[dict[str, Any]] = []
        added: list[str] = []

        wanted = list(titles)
        # Rename the single default tab into the first wanted title.
        if len(metadata.worksheets) == 1 and wanted[0] not in existing:
            default_sheet = metadata.worksheets[0]
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": default_sheet.sheet_id, "title": wanted[0]},
                        "fields": "title",
                    }
                }
            )
            added.append(wanted[0])
            existing = {wanted[0]}
            wanted = wanted[1:]

        for title in wanted:
            if title in existing:
                continue
            requests.append({"addSheet": {"properties": {"title": title}}})
            added.append(title)

        if requests:
            await self._request(
                "POST",
                f"/{spreadsheet_id}:batchUpdate",
                operation="ensure_worksheets",
                json_body={"requests": requests},
                spreadsheet_id=spreadsheet_id,
            )
        return added

    async def write_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: Sequence[Sequence[str]],
        *,
        start_cell: str = "A1",
    ) -> int:
        """Write a rectangular block of values starting at ``start_cell``.

        Used to lay down header rows and dropdown vocabularies on a spreadsheet
        MeoBot just created. It writes exactly the range it computes; it never
        clears anything.
        """
        if not rows:
            return 0
        width = max(len(row) for row in rows)
        last_column = column_letter(width - 1)
        first_row = int("".join(ch for ch in start_cell if ch.isdigit()) or "1")
        a1 = f"'{sheet_name}'!{start_cell}:{last_column}{first_row + len(rows) - 1}"
        await self._request(
            "PUT",
            f"/{spreadsheet_id}/values/{a1}",
            operation="write_rows",
            params={"valueInputOption": "USER_ENTERED"},
            json_body={"values": [list(row) for row in rows]},
            spreadsheet_id=spreadsheet_id,
        )
        logger.info(
            "sheets_rows_written",
            extra={"spreadsheet_id": spreadsheet_id, "sheet_name": sheet_name, "rows": len(rows)},
        )
        return len(rows)

    # --- Plumbing ---------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        spreadsheet_id: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One authenticated API call with retries and error translation."""

        async def attempt() -> dict[str, Any]:
            token = await self._credentials.token()
            client = self._ensure_client()
            response = await client.request(
                method,
                f"{API_BASE}{path}",
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
            return self._handle(response, operation=operation, spreadsheet_id=spreadsheet_id)

        return await call_with_retries(
            attempt,
            config=self._config,
            operation_name=f"sheets.{operation}",
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client

    def _handle(
        self,
        response: httpx.Response,
        *,
        operation: str,
        spreadsheet_id: str,
    ) -> dict[str, Any]:
        """Translate an HTTP response into data or a typed error.

        The response body is never logged: for a permission error Google echoes
        the caller identity, and for a values response it contains cell data.
        """
        status = response.status_code
        if status == httpx.codes.OK:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

        message = self._error_message(response)
        details: dict[str, Any] = {
            "operation": operation,
            "spreadsheet_id": spreadsheet_id,
            "status": status,
        }

        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise IntegrationAuthError(
                (
                    "Google từ chối truy cập. Hãy chia sẻ Google Sheet với email "
                    "service account (quyền Editor)."
                ),
                provider=PROVIDER,
                details=details | {"google_message": message},
            )
        if status == httpx.codes.NOT_FOUND:
            raise NotFoundError(
                "Không tìm thấy spreadsheet hoặc tab (có thể đã bị đổi tên/xoá).",
                details=details,
            )
        if status == httpx.codes.BAD_REQUEST and "Unable to parse range" in message:
            raise NotFoundError(
                "Không đọc được vùng dữ liệu: tab có thể đã bị đổi tên.",
                details=details,
            )
        if status == httpx.codes.TOO_MANY_REQUESTS or status >= 500:
            raise IntegrationRateLimitError(
                f"Google Sheets tạm thời không phục vụ được (HTTP {status}).",
                provider=PROVIDER,
                details=details,
            )
        raise IntegrationError(
            f"Google Sheets trả về lỗi HTTP {status}.",
            provider=PROVIDER,
            details=details | {"google_message": message},
        )

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """Extract Google's short error message, bounded in length."""
        try:
            payload = response.json()
        except ValueError:
            return ""
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("message", ""))[:300]
        return ""
