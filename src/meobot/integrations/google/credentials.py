"""Service-account credential loading and access-token minting.

Nothing in this module ever logs a key, a token, or the contents of the
credential file. The only thing that leaves it is a bearer token handed
directly to an HTTP client, plus the service-account *email*, which is not a
secret and which the operator needs in order to share a Sheet with the bot.

``google-auth`` performs the RS256 signing and the token exchange; both are
blocking, so they run in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meobot.core.errors import IntegrationAuthError, IntegrationNotConfiguredError
from meobot.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER = "google"

#: Read/write on Sheets. The Drive scope is read-only here and only used for
#: metadata: the Sheets client never creates a file.
SHEETS_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
)

#: Scopes for the Drive client, which does create files.
#:
#: ``drive.file`` is not sufficient: MeoBot has to *copy a template it did not
#: create* into *a folder shared with it by a human*, and ``drive.file`` only
#: grants access to files the application itself created. The broader scope is
#: contained by the service account's own access - it can only touch what the
#: team explicitly shared with it - and by this codebase having no delete,
#: trash or move operation at all.
DRIVE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
)

#: Refresh a little before expiry so a long batch does not fail mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS = 120


@dataclass(frozen=True, slots=True)
class ServiceAccountInfo:
    """The non-secret facts about a loaded credential."""

    client_email: str
    project_id: str | None


class GoogleCredentials:
    """Lazily loads a service-account file and mints access tokens.

    Args:
        credentials_path: Path *inside the container* to the JSON key file.
        scopes: OAuth scopes to request.

    Raises:
        IntegrationNotConfiguredError: When no path is configured, on the first
            call that actually needs Google. Construction never raises, so the
            rest of MeoBot starts fine without credentials.
    """

    def __init__(
        self,
        credentials_path: str | None,
        *,
        scopes: tuple[str, ...] = SHEETS_SCOPES,
    ) -> None:
        self._path = credentials_path
        self._scopes = list(scopes)
        self._credentials: Any | None = None
        self._info: ServiceAccountInfo | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """True when a credential path is set. Does not touch the filesystem."""
        return bool(self._path and self._path.strip())

    async def info(self) -> ServiceAccountInfo:
        """Return the service-account identity, loading the file if needed."""
        await self._ensure_loaded()
        assert self._info is not None
        return self._info

    async def token(self) -> str:
        """Return a valid access token, refreshing when close to expiry."""
        await self._ensure_loaded()
        credentials = self._credentials
        assert credentials is not None

        if not credentials.valid or self._expiring_soon(credentials):
            async with self._lock:
                if not credentials.valid or self._expiring_soon(credentials):
                    await asyncio.to_thread(self._refresh, credentials)

        token = credentials.token
        if not isinstance(token, str) or not token:
            raise IntegrationAuthError("Google refused to issue an access token", provider=PROVIDER)
        return token

    async def _ensure_loaded(self) -> None:
        if self._credentials is not None:
            return
        async with self._lock:
            # Re-checked under the lock: two concurrent first calls must load
            # the file once, not twice.
            if self._credentials is None:
                self._credentials, self._info = await asyncio.to_thread(self._load)

    def _load(self) -> tuple[Any, ServiceAccountInfo]:
        """Read and parse the key file. Blocking; called in a thread."""
        if not self.configured:
            raise IntegrationNotConfiguredError(
                "Google is not configured (GOOGLE_SERVICE_ACCOUNT_FILE is unset)",
                provider=PROVIDER,
            )
        assert self._path is not None
        path = Path(self._path)
        if not path.is_file():
            # The path is operator-supplied configuration, not a secret.
            raise IntegrationNotConfiguredError(
                f"Google service-account file not found at {path}",
                provider=PROVIDER,
                details={"path": str(path)},
            )

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Deliberately does not include the exception's text: a JSON error
            # can quote the surrounding bytes, which here are a private key.
            raise IntegrationAuthError(
                "Google service-account file is unreadable or not valid JSON",
                provider=PROVIDER,
                details={"path": str(path), "error": type(exc).__name__},
            ) from None

        try:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                payload, scopes=self._scopes
            )
        except Exception as exc:
            raise IntegrationAuthError(
                "Google service-account file is not a usable service-account key",
                provider=PROVIDER,
                details={"error": type(exc).__name__},
            ) from None

        info = ServiceAccountInfo(
            client_email=str(payload.get("client_email", "")),
            project_id=payload.get("project_id"),
        )
        logger.info(
            "google_credentials_loaded",
            extra={"client_email": info.client_email, "project_id": info.project_id},
        )
        return credentials, info

    @staticmethod
    def _refresh(credentials: Any) -> None:
        """Exchange the signed JWT for an access token. Blocking."""
        try:
            from google.auth.transport.requests import (
                Request,
            )

            credentials.refresh(Request())
        except Exception as exc:
            raise IntegrationAuthError(
                "Google rejected the service-account credentials",
                provider=PROVIDER,
                details={"error": type(exc).__name__},
            ) from None

    @staticmethod
    def _expiring_soon(credentials: Any) -> bool:
        expiry: Any = getattr(credentials, "expiry", None)
        if expiry is None:
            return False
        from datetime import datetime, timedelta

        # google-auth stores a naive UTC expiry, so this comparison uses the
        # same naive clock deliberately.
        soon = datetime.utcnow() + timedelta(  # noqa: DTZ003 - matches google-auth
            seconds=TOKEN_REFRESH_MARGIN_SECONDS
        )
        return bool(soon >= expiry)
