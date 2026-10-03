"""Google client selection from configuration.

Missing credentials are not an error at build time: every container still
starts, and only the code paths that actually talk to Google fail - loudly,
with a message telling the operator what to configure.
"""

from __future__ import annotations

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.google.credentials import DRIVE_SCOPES, GoogleCredentials
from meobot.integrations.google.drive import (
    PROVIDER as DRIVE_PROVIDER,
)
from meobot.integrations.google.drive import (
    DriveClient,
    FakeDriveClient,
    GoogleDriveClient,
    NotConfiguredDriveClient,
)
from meobot.integrations.google.sheets import (
    PROVIDER,
    GoogleSheetsClient,
    NotConfiguredSheetsClient,
    SheetsClient,
)

logger = get_logger(__name__)


def google_configured(settings: Settings) -> bool:
    """True when a service-account path is configured."""
    path = settings.google_service_account_file
    return bool(path and path.strip())


def build_sheets_client(settings: Settings) -> SheetsClient:
    """Return the real Sheets client, or the loud placeholder."""
    if not google_configured(settings):
        logger.info("google_sheets_not_configured")
        return NotConfiguredSheetsClient()
    credentials = GoogleCredentials(settings.google_service_account_file)
    config = IntegrationConfig(
        provider=PROVIDER,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    return GoogleSheetsClient(credentials, config=config)


def build_drive_client(settings: Settings) -> DriveClient:
    """Return the real Drive client, or the loud placeholder.

    Drive shares the Sheets service-account file but asks for a wider scope
    (see :data:`~meobot.integrations.google.credentials.DRIVE_SCOPES`), so it
    gets its own credential instance rather than reusing the Sheets one.

    A missing credential is not a startup failure: the placeholder is returned
    and only Drive operations report a configuration error.
    """
    if not google_configured(settings):
        logger.info("google_drive_not_configured")
        return NotConfiguredDriveClient()
    credentials = GoogleCredentials(settings.google_service_account_file, scopes=DRIVE_SCOPES)
    config = IntegrationConfig(
        provider=DRIVE_PROVIDER,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    return GoogleDriveClient(
        credentials,
        config=config,
        shared_drive_id=settings.google_shared_drive_id,
    )


def build_fake_drive_client() -> DriveClient:
    """In-memory Drive client. Used by tests, never by a running container."""
    return FakeDriveClient()
