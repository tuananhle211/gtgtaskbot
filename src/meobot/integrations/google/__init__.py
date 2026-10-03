"""Google Workspace integration (Sheets + Drive).

Both clients are real as of 0.3.0 and both are backed by the same
service-account file, with different scopes: Sheets reads and writes cell
values, Drive creates files. Neither can delete anything - there is no such
method on either client.
"""

from meobot.integrations.google.credentials import (
    DRIVE_SCOPES,
    SHEETS_SCOPES,
    GoogleCredentials,
    ServiceAccountInfo,
)
from meobot.integrations.google.drive import (
    FOLDER_MIME,
    SPREADSHEET_MIME,
    DriveClient,
    DriveFile,
    FakeDriveClient,
    GoogleDriveClient,
    NotConfiguredDriveClient,
)
from meobot.integrations.google.factory import (
    build_drive_client,
    build_fake_drive_client,
    build_sheets_client,
    google_configured,
)
from meobot.integrations.google.sheets import (
    CellUpdate,
    FakeSheetsClient,
    GoogleSheetsClient,
    NotConfiguredSheetsClient,
    SheetRange,
    SheetsClient,
    SpreadsheetMetadata,
    WorksheetInfo,
    column_letter,
)

__all__ = [
    "DRIVE_SCOPES",
    "FOLDER_MIME",
    "SHEETS_SCOPES",
    "SPREADSHEET_MIME",
    "CellUpdate",
    "DriveClient",
    "DriveFile",
    "FakeDriveClient",
    "FakeSheetsClient",
    "GoogleCredentials",
    "GoogleDriveClient",
    "GoogleSheetsClient",
    "NotConfiguredDriveClient",
    "NotConfiguredSheetsClient",
    "ServiceAccountInfo",
    "SheetRange",
    "SheetsClient",
    "SpreadsheetMetadata",
    "WorksheetInfo",
    "build_drive_client",
    "build_fake_drive_client",
    "build_sheets_client",
    "column_letter",
    "google_configured",
]
