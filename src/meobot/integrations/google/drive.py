"""Google Drive access: protocol, in-memory fake, and the real REST client.

Like the Sheets client this speaks the REST API over ``httpx`` rather than
``googleapiclient``, for the same reasons: that library is synchronous, large,
and would need a thread per call.

The capability surface is deliberately incomplete. MeoBot can:

* read metadata for a file or folder;
* list the subfolders of *one* folder it was given;
* create a blank spreadsheet, or copy a template;
* place the new file in a chosen folder and rename it.

It cannot delete, cannot trash, cannot move an existing file, and cannot search
the company Drive - those methods do not exist here, so no tool, no LLM plan
and no future handler can reach them. The one search that does exist is scoped
to MeoBot's own ``appProperties`` and is used purely to reconcile a creation
that succeeded at Google but failed to commit locally.

Shared Drives are supported throughout (``supportsAllDrives``,
``includeItemsFromAllDrives``), because a file a service account creates in My
Drive has no human owner and cannot be recovered by the team.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from meobot.core.errors import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationNotConfiguredError,
    IntegrationRateLimitError,
    NotFoundError,
    ValidationError,
)
from meobot.core.logging import get_logger
from meobot.integrations.base import IntegrationConfig, build_idempotency_key, call_with_retries
from meobot.integrations.google.credentials import GoogleCredentials

logger = get_logger(__name__)

PROVIDER = "google_drive"
API_BASE = "https://www.googleapis.com/drive/v3"

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"

#: Fields requested for a file. Kept explicit so no response ever carries more
#: than what is needed, and so ``capabilities`` is always present.
FILE_FIELDS = (
    "id,name,mimeType,webViewLink,parents,driveId,trashed,"
    "capabilities(canAddChildren,canEdit,canListChildren),appProperties"
)

#: Hard ceiling on a folder listing, so a folder with ten thousand children
#: cannot exhaust a worker.
MAX_LIST_RESULTS = 100


@dataclass(frozen=True, slots=True)
class DriveFile:
    """A file or folder on Drive, as MeoBot needs to see it."""

    file_id: str
    name: str
    mime_type: str
    web_link: str = ""
    parents: tuple[str, ...] = ()
    drive_id: str | None = None
    trashed: bool = False
    can_add_children: bool = False
    can_edit: bool = False
    app_properties: dict[str, str] = field(default_factory=dict)

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_spreadsheet(self) -> bool:
        return self.mime_type == SPREADSHEET_MIME

    @property
    def spreadsheet_id(self) -> str:
        """For a spreadsheet the Drive file id *is* the spreadsheet id."""
        return self.file_id


@runtime_checkable
class DriveClient(Protocol):
    """Create files on Google Drive. No deletion, no arbitrary search."""

    async def get_file(self, file_id: str) -> DriveFile:
        """Metadata for a file or folder."""
        ...

    async def list_child_folders(self, parent_id: str, *, limit: int = 50) -> list[DriveFile]:
        """Subfolders of one folder. Never a Drive-wide search."""
        ...

    async def create_spreadsheet(
        self,
        name: str,
        *,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> DriveFile:
        """Create an empty spreadsheet in ``folder_id``."""
        ...

    async def copy_file(
        self,
        source_file_id: str,
        *,
        name: str,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
    ) -> DriveFile:
        """Copy a template, preserving its formatting and formulas."""
        ...

    async def rename_file(self, file_id: str, name: str) -> DriveFile:
        """Rename an already created file."""
        ...

    async def find_by_idempotency_reference(
        self,
        reference: str,
        *,
        parent_id: str | None = None,
    ) -> DriveFile | None:
        """Find a file MeoBot created, by its own app property.

        Used only to reconcile a creation whose local commit failed. It is not
        a search facility: the query is bound to MeoBot's own property key.
        """
        ...

    async def share_with(self, file_id: str, email: str, *, role: str = "reader") -> None:
        """Grant ``email`` access to ``file_id``."""
        ...


@dataclass
class FakeDriveClient:
    """In-memory :class:`DriveClient` for tests and offline development.

    Honours ``app_properties`` and the idempotency reference, so the
    duplicate-prevention path can be tested without Google.
    """

    files: dict[str, DriveFile] = field(default_factory=dict)
    shares: list[tuple[str, str, str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    #: Raised by the next call, then cleared. Lets a test drive an error path.
    next_error: Exception | None = None
    _by_key: dict[str, str] = field(default_factory=dict)
    _counter: int = 0

    def add_folder(
        self,
        folder_id: str,
        name: str,
        *,
        parents: tuple[str, ...] = (),
        drive_id: str | None = None,
        can_add_children: bool = True,
    ) -> DriveFile:
        """Seed a folder the fake will resolve."""
        folder = DriveFile(
            file_id=folder_id,
            name=name,
            mime_type=FOLDER_MIME,
            web_link=f"https://drive.google.com/drive/folders/{folder_id}",
            parents=parents,
            drive_id=drive_id,
            can_add_children=can_add_children,
            can_edit=can_add_children,
        )
        self.files[folder_id] = folder
        return folder

    def add_template(self, file_id: str, name: str = "Template") -> DriveFile:
        """Seed a template spreadsheet available for copying."""
        template = DriveFile(
            file_id=file_id,
            name=name,
            mime_type=SPREADSHEET_MIME,
            web_link=f"https://docs.google.com/spreadsheets/d/{file_id}/edit",
            can_edit=True,
        )
        self.files[file_id] = template
        return template

    def _check(self) -> None:
        if self.next_error is not None:
            error, self.next_error = self.next_error, None
            raise error

    async def get_file(self, file_id: str) -> DriveFile:
        self.calls.append(f"get_file:{file_id}")
        self._check()
        found = self.files.get(file_id)
        if found is None:
            raise NotFoundError(
                "Không tìm thấy file hoặc thư mục trên Drive.",
                details={"file_id": file_id},
            )
        return found

    async def list_child_folders(self, parent_id: str, *, limit: int = 50) -> list[DriveFile]:
        self.calls.append(f"list_child_folders:{parent_id}")
        self._check()
        return [
            item for item in self.files.values() if item.is_folder and parent_id in item.parents
        ][:limit]

    async def create_spreadsheet(
        self,
        name: str,
        *,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> DriveFile:
        self.calls.append(f"create_spreadsheet:{name}")
        self._check()
        key = idempotency_key or build_idempotency_key("drive", name, folder_id)
        if key in self._by_key:
            return self.files[self._by_key[key]]
        created = self._new_spreadsheet(name, folder_id, app_properties)
        self._by_key[key] = created.file_id
        return created

    async def copy_file(
        self,
        source_file_id: str,
        *,
        name: str,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
    ) -> DriveFile:
        self.calls.append(f"copy_file:{source_file_id}")
        self._check()
        if source_file_id not in self.files:
            raise NotFoundError(
                "Không tìm thấy file mẫu trên Drive.",
                details={"file_id": source_file_id},
            )
        reference = (app_properties or {}).get("meobot_idempotency_reference")
        if reference and reference in self._by_key:
            return self.files[self._by_key[reference]]
        created = self._new_spreadsheet(name, folder_id, app_properties)
        if reference:
            self._by_key[reference] = created.file_id
        return created

    def _new_spreadsheet(
        self,
        name: str,
        folder_id: str | None,
        app_properties: dict[str, str] | None,
    ) -> DriveFile:
        self._counter += 1
        file_id = f"fake-file-{self._counter}"
        created = DriveFile(
            file_id=file_id,
            name=name,
            mime_type=SPREADSHEET_MIME,
            web_link=f"https://docs.google.com/spreadsheets/d/{file_id}/edit",
            parents=(folder_id,) if folder_id else (),
            drive_id=self.files[folder_id].drive_id if folder_id in self.files else None,
            can_edit=True,
            app_properties=dict(app_properties or {}),
        )
        self.files[file_id] = created
        return created

    async def rename_file(self, file_id: str, name: str) -> DriveFile:
        self.calls.append(f"rename_file:{file_id}")
        self._check()
        existing = await self.get_file(file_id)
        renamed = DriveFile(
            file_id=existing.file_id,
            name=name,
            mime_type=existing.mime_type,
            web_link=existing.web_link,
            parents=existing.parents,
            drive_id=existing.drive_id,
            can_add_children=existing.can_add_children,
            can_edit=existing.can_edit,
            app_properties=existing.app_properties,
        )
        self.files[file_id] = renamed
        return renamed

    async def find_by_idempotency_reference(
        self,
        reference: str,
        *,
        parent_id: str | None = None,
    ) -> DriveFile | None:
        self.calls.append(f"find_by_reference:{reference}")
        self._check()
        for item in self.files.values():
            if item.app_properties.get("meobot_idempotency_reference") != reference:
                continue
            if parent_id is not None and parent_id not in item.parents:
                continue
            return item
        return None

    async def share_with(self, file_id: str, email: str, *, role: str = "reader") -> None:
        self.calls.append(f"share_with:{file_id}")
        self._check()
        self.shares.append((file_id, email, role))


class NotConfiguredDriveClient:
    """Placeholder used when no Google credentials are present.

    Every call raises, so a missing credential fails loudly at the call site
    with an actionable message - and every other part of MeoBot still starts.
    Only Drive-related operations are affected.
    """

    provider = PROVIDER

    async def get_file(self, file_id: str) -> DriveFile:
        raise self._error("get_file")

    async def list_child_folders(self, parent_id: str, *, limit: int = 50) -> list[DriveFile]:
        raise self._error("list_child_folders")

    async def create_spreadsheet(
        self,
        name: str,
        *,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> DriveFile:
        raise self._error("create_spreadsheet")

    async def copy_file(
        self,
        source_file_id: str,
        *,
        name: str,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
    ) -> DriveFile:
        raise self._error("copy_file")

    async def rename_file(self, file_id: str, name: str) -> DriveFile:
        raise self._error("rename_file")

    async def find_by_idempotency_reference(
        self,
        reference: str,
        *,
        parent_id: str | None = None,
    ) -> DriveFile | None:
        raise self._error("find_by_idempotency_reference")

    async def share_with(self, file_id: str, email: str, *, role: str = "reader") -> None:
        raise self._error("share_with")

    def _error(self, operation: str) -> IntegrationNotConfiguredError:
        return IntegrationNotConfiguredError(
            "Google Drive chưa được cấu hình (GOOGLE_SERVICE_ACCOUNT_FILE trống). "
            "Các chức năng khác của MeoBot vẫn hoạt động bình thường.",
            provider=self.provider,
            details={"operation": operation},
        )


class GoogleDriveClient:
    """Drive v3 REST client backed by a service account.

    Args:
        credentials: Token source. Never logged, never returned.
        config: Timeout and retry budget.
        shared_drive_id: Default Shared Drive for creations, when configured.
        client: Injected ``httpx.AsyncClient`` (tests pass a MockTransport).
    """

    provider = PROVIDER

    def __init__(
        self,
        credentials: GoogleCredentials,
        *,
        config: IntegrationConfig | None = None,
        shared_drive_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credentials = credentials
        self._config = config or IntegrationConfig(provider=PROVIDER)
        self._shared_drive_id = shared_drive_id or None
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client when this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Reads ------------------------------------------------------------
    async def get_file(self, file_id: str) -> DriveFile:
        """Fetch metadata for a file or folder, on any drive."""
        payload = await self._request(
            "GET",
            f"/files/{file_id}",
            operation="get_file",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            file_id=file_id,
        )
        return self._to_file(payload)

    async def list_child_folders(self, parent_id: str, *, limit: int = 50) -> list[DriveFile]:
        """List the subfolders of one folder.

        Scoped to ``parent_id`` by construction - there is no code path here
        that lists a drive root or runs a full-text query.
        """
        payload = await self._request(
            "GET",
            "/files",
            operation="list_child_folders",
            params={
                "q": f"'{parent_id}' in parents and mimeType='{FOLDER_MIME}' and trashed=false",
                "fields": f"files({FILE_FIELDS})",
                "pageSize": str(min(limit, MAX_LIST_RESULTS)),
                "orderBy": "name",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                **self._corpora_params(),
            },
            file_id=parent_id,
        )
        return [self._to_file(item) for item in payload.get("files") or []]

    async def find_by_idempotency_reference(
        self,
        reference: str,
        *,
        parent_id: str | None = None,
    ) -> DriveFile | None:
        """Locate a file MeoBot created, by the property it wrote on it.

        This is the reconciliation path: Drive accepted the creation, the local
        commit did not happen, and a retry must find the existing file instead
        of creating a second one.
        """
        clauses = [
            f"appProperties has {{ key='meobot_idempotency_reference' and value='{reference}' }}",
            "trashed=false",
        ]
        if parent_id:
            clauses.append(f"'{parent_id}' in parents")
        payload = await self._request(
            "GET",
            "/files",
            operation="find_by_idempotency_reference",
            params={
                "q": " and ".join(clauses),
                "fields": f"files({FILE_FIELDS})",
                "pageSize": "2",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                **self._corpora_params(),
            },
            file_id=parent_id or "",
        )
        files = payload.get("files") or []
        if not files:
            return None
        if len(files) > 1:
            # Two files sharing one idempotency reference means a previous
            # duplicate slipped through. Report it rather than pick one.
            logger.error(
                "drive_duplicate_idempotency_reference",
                extra={"reference": reference[:32], "count": len(files)},
            )
        return self._to_file(files[0])

    # --- Writes -----------------------------------------------------------
    async def create_spreadsheet(
        self,
        name: str,
        *,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> DriveFile:
        """Create an empty spreadsheet.

        ``idempotency_key`` is accepted for interface compatibility with the
        fake; Drive itself has no idempotent create, which is exactly why the
        caller writes a ``created_spreadsheets`` row first and why
        ``app_properties`` carries the reference used to reconcile.
        """
        body: dict[str, Any] = {"name": name, "mimeType": SPREADSHEET_MIME}
        if folder_id:
            body["parents"] = [folder_id]
        if app_properties:
            body["appProperties"] = app_properties

        payload = await self._request(
            "POST",
            "/files",
            operation="create_spreadsheet",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            json_body=body,
            file_id=folder_id or "",
        )
        created = self._to_file(payload)
        logger.info(
            "drive_spreadsheet_created",
            extra={"file_id": created.file_id, "parent": folder_id, "method": "blank"},
        )
        return created

    async def copy_file(
        self,
        source_file_id: str,
        *,
        name: str,
        folder_id: str | None = None,
        app_properties: dict[str, str] | None = None,
    ) -> DriveFile:
        """Copy a template spreadsheet into ``folder_id``.

        Preferred over generating a file: a copy keeps the formatting,
        formulas, dropdowns, charts, conditional formatting and protected
        ranges a person designed.
        """
        body: dict[str, Any] = {"name": name}
        if folder_id:
            body["parents"] = [folder_id]
        if app_properties:
            body["appProperties"] = app_properties

        payload = await self._request(
            "POST",
            f"/files/{source_file_id}/copy",
            operation="copy_file",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            json_body=body,
            file_id=source_file_id,
        )
        created = self._to_file(payload)
        logger.info(
            "drive_spreadsheet_created",
            extra={"file_id": created.file_id, "parent": folder_id, "method": "copy"},
        )
        return created

    async def rename_file(self, file_id: str, name: str) -> DriveFile:
        """Rename a file. The only mutation of an existing file MeoBot performs."""
        payload = await self._request(
            "PATCH",
            f"/files/{file_id}",
            operation="rename_file",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            json_body={"name": name},
            file_id=file_id,
        )
        return self._to_file(payload)

    async def share_with(self, file_id: str, email: str, *, role: str = "reader") -> None:
        """Grant a person access to a created file."""
        if role not in {"reader", "commenter", "writer"}:
            raise ValidationError(f"Quyền chia sẻ không hợp lệ: {role!r}")
        await self._request(
            "POST",
            f"/files/{file_id}/permissions",
            operation="share_with",
            params={"supportsAllDrives": "true", "sendNotificationEmail": "false"},
            json_body={"type": "user", "role": role, "emailAddress": email},
            file_id=file_id,
        )

    # --- Plumbing ---------------------------------------------------------
    def _corpora_params(self) -> dict[str, str]:
        """Restrict listings to the configured Shared Drive when there is one."""
        if not self._shared_drive_id:
            return {}
        return {"corpora": "drive", "driveId": self._shared_drive_id}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        file_id: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One authenticated API call with a timeout, retries and typed errors."""

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
            return self._handle(response, operation=operation, file_id=file_id)

        return await call_with_retries(
            attempt,
            config=self._config,
            operation_name=f"drive.{operation}",
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
        file_id: str,
    ) -> dict[str, Any]:
        """Translate an HTTP response into data or a typed, sanitised error.

        Google's error body is never surfaced verbatim to a user: a 403 echoes
        the caller identity and a 400 can quote request content. The short
        ``message`` field is kept in ``details`` for the log, and the user gets
        an actionable Vietnamese sentence instead.
        """
        status = response.status_code
        if status in (httpx.codes.OK, httpx.codes.CREATED, httpx.codes.NO_CONTENT):
            if not response.content:
                return {}
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

        message = self._error_message(response)
        details: dict[str, Any] = {
            "operation": operation,
            "file_id": file_id[:60],
            "status": status,
            "google_message": message,
        }

        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            if "storageQuotaExceeded" in message or "quota" in message.lower():
                raise IntegrationRateLimitError(
                    "Google Drive báo hết dung lượng hoặc vượt hạn mức. "
                    "Hãy dùng Shared Drive thay cho My Drive của service account.",
                    provider=PROVIDER,
                    details=details,
                )
            raise IntegrationAuthError(
                "Google từ chối truy cập thư mục này. Hãy chia sẻ thư mục với "
                "email service account của MeoBot, quyền Content manager "
                "(hoặc Editor).",
                provider=PROVIDER,
                details=details,
            )
        if status == httpx.codes.NOT_FOUND:
            raise NotFoundError(
                "Không tìm thấy file hoặc thư mục trên Drive (có thể đã bị đổi "
                "chỗ, bị xoá, hoặc chưa được chia sẻ với MeoBot).",
                details=details,
            )
        if status == httpx.codes.CONFLICT:
            raise IntegrationError(
                "Google Drive báo trùng lặp khi tạo file. MeoBot đã dừng lại để "
                "tránh tạo file thừa.",
                provider=PROVIDER,
                details=details,
            )
        if status == httpx.codes.TOO_MANY_REQUESTS or status >= 500:
            raise IntegrationRateLimitError(
                f"Google Drive tạm thời không phục vụ được (HTTP {status}).",
                provider=PROVIDER,
                details=details,
            )
        if status == httpx.codes.BAD_REQUEST:
            raise ValidationError(
                "Google Drive từ chối yêu cầu (tham số không hợp lệ). "
                "Hãy kiểm tra lại thư mục đích và file mẫu.",
                details=details,
            )
        raise IntegrationError(
            f"Google Drive trả về lỗi HTTP {status}.",
            provider=PROVIDER,
            details=details,
        )

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """Google's short error message, bounded in length."""
        try:
            payload = response.json()
        except ValueError:
            return ""
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("message", ""))[:300]
        return ""

    @staticmethod
    def _to_file(payload: dict[str, Any]) -> DriveFile:
        """Map a Drive ``files`` resource onto :class:`DriveFile`."""
        capabilities = payload.get("capabilities") or {}
        raw_properties = payload.get("appProperties") or {}
        return DriveFile(
            file_id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            mime_type=str(payload.get("mimeType", "")),
            web_link=str(payload.get("webViewLink", "")),
            parents=tuple(str(parent) for parent in payload.get("parents") or ()),
            drive_id=payload.get("driveId"),
            trashed=bool(payload.get("trashed", False)),
            can_add_children=bool(capabilities.get("canAddChildren", False)),
            can_edit=bool(capabilities.get("canEdit", False)),
            app_properties={str(k): str(v) for k, v in raw_properties.items()},
        )
