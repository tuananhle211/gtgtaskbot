"""The real Google Drive client, against a mocked HTTP transport.

No test here reaches Google. What they check is the part that is easy to get
wrong and impossible to notice until production: the exact requests MeoBot
constructs, the Shared Drive parameters that decide whether a file is
recoverable by the team, and the error translation that decides whether a
403 reaches the Owner as an actionable sentence or as Google's internal
message with a caller identity in it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from meobot.core.errors import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationRateLimitError,
    NotFoundError,
    ValidationError,
)
from meobot.integrations.base import IntegrationConfig
from meobot.integrations.google.credentials import DRIVE_SCOPES, SHEETS_SCOPES
from meobot.integrations.google.drive import (
    FOLDER_MIME,
    SPREADSHEET_MIME,
    DriveClient,
    FakeDriveClient,
    GoogleDriveClient,
    NotConfiguredDriveClient,
)


class StubCredentials:
    """Hands out a token without touching a key file."""

    def __init__(self) -> None:
        self.calls = 0

    async def token(self) -> str:
        self.calls += 1
        return "test-access-token"


def _client(
    handler: Any,
    *,
    shared_drive_id: str | None = None,
) -> tuple[GoogleDriveClient, list[httpx.Request]]:
    """A Drive client whose transport records every request."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    return (
        GoogleDriveClient(
            StubCredentials(),  # type: ignore[arg-type]
            config=IntegrationConfig(provider="google_drive", max_retries=0),
            shared_drive_id=shared_drive_id,
            client=httpx.AsyncClient(transport=transport),
        ),
        seen,
    )


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _error(status: int, message: str = "denied") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "code": status}})


FOLDER_PAYLOAD: dict[str, Any] = {
    "id": "folder-1",
    "name": "Kịch bản TikTok",
    "mimeType": FOLDER_MIME,
    "parents": ["root-1"],
    "driveId": "shared-1",
    "trashed": False,
    "capabilities": {"canAddChildren": True, "canEdit": True},
}

FILE_PAYLOAD: dict[str, Any] = {
    "id": "file-1",
    "name": "Kịch bản tháng 8",
    "mimeType": SPREADSHEET_MIME,
    "webViewLink": "https://docs.google.com/spreadsheets/d/file-1/edit",
    "parents": ["folder-1"],
    "driveId": "shared-1",
    "capabilities": {"canEdit": True},
    "appProperties": {"meobot_managed": "true", "meobot_idempotency_reference": "abc123"},
}


# --- Scopes -----------------------------------------------------------------
def test_drive_needs_a_wider_scope_than_sheets() -> None:
    """Documented deliberately: Drive creates files, Sheets does not.

    ``drive.file`` would not do - MeoBot copies a template it did not create,
    into a folder a human shared with it.
    """
    assert "https://www.googleapis.com/auth/drive" in DRIVE_SCOPES
    assert "https://www.googleapis.com/auth/drive" not in SHEETS_SCOPES
    assert "https://www.googleapis.com/auth/drive.readonly" in SHEETS_SCOPES


# --- Request construction ---------------------------------------------------
async def test_get_file_asks_for_shared_drive_support() -> None:
    """Without ``supportsAllDrives`` a Shared Drive file is simply not found."""
    client, seen = _client(lambda request: _ok(FOLDER_PAYLOAD))
    folder = await client.get_file("folder-1")

    assert folder.is_folder
    assert folder.can_add_children
    assert folder.drive_id == "shared-1"
    assert seen[0].url.params["supportsAllDrives"] == "true"
    assert "capabilities" in seen[0].url.params["fields"]


async def test_listing_child_folders_is_scoped_to_one_parent() -> None:
    """It is a folder listing, never a Drive-wide search."""
    client, seen = _client(lambda request: _ok({"files": [FOLDER_PAYLOAD]}))
    await client.list_child_folders("parent-9")

    query = seen[0].url.params["q"]
    assert "'parent-9' in parents" in query
    assert f"mimeType='{FOLDER_MIME}'" in query
    assert "trashed=false" in query
    assert seen[0].url.params["includeItemsFromAllDrives"] == "true"
    # Bounded, so a huge folder cannot exhaust a worker.
    assert int(seen[0].url.params["pageSize"]) <= 100


async def test_listing_is_confined_to_the_configured_shared_drive() -> None:
    """With a Shared Drive configured, listings do not escape it."""
    client, seen = _client(lambda request: _ok({"files": []}), shared_drive_id="shared-1")
    await client.list_child_folders("parent-9")

    assert seen[0].url.params["corpora"] == "drive"
    assert seen[0].url.params["driveId"] == "shared-1"


async def test_create_spreadsheet_places_the_file_and_stamps_it() -> None:
    """Parent, mime type and the app properties used for reconciliation."""
    client, seen = _client(lambda request: _ok(FILE_PAYLOAD))
    created = await client.create_spreadsheet(
        "Kịch bản tháng 8",
        folder_id="folder-1",
        app_properties={"meobot_managed": "true", "meobot_idempotency_reference": "abc123"},
    )

    body = json.loads(seen[0].content)
    assert body["mimeType"] == SPREADSHEET_MIME
    assert body["parents"] == ["folder-1"]
    assert body["appProperties"]["meobot_idempotency_reference"] == "abc123"
    assert seen[0].url.params["supportsAllDrives"] == "true"
    assert created.spreadsheet_id == "file-1"
    assert created.web_link.endswith("/edit")


async def test_copy_file_targets_the_template_and_the_destination() -> None:
    """A template copy keeps formatting; the request must name both ends."""
    client, seen = _client(lambda request: _ok(FILE_PAYLOAD))
    await client.copy_file(
        "template-7",
        name="Kịch bản tháng 8",
        folder_id="folder-1",
        app_properties={"meobot_template_code": "SCRIPT_MANAGEMENT"},
    )

    assert seen[0].url.path.endswith("/files/template-7/copy")
    body = json.loads(seen[0].content)
    assert body["name"] == "Kịch bản tháng 8"
    assert body["parents"] == ["folder-1"]
    assert body["appProperties"]["meobot_template_code"] == "SCRIPT_MANAGEMENT"


async def test_rename_uses_patch_and_changes_only_the_name() -> None:
    client, seen = _client(lambda request: _ok(FILE_PAYLOAD))
    await client.rename_file("file-1", "Tên mới")

    assert seen[0].method == "PATCH"
    assert json.loads(seen[0].content) == {"name": "Tên mới"}


async def test_reconciliation_searches_only_meobots_own_property() -> None:
    """The one search that exists is bound to MeoBot's own marker."""
    client, seen = _client(lambda request: _ok({"files": [FILE_PAYLOAD]}))
    found = await client.find_by_idempotency_reference("abc123", parent_id="folder-1")

    assert found is not None and found.file_id == "file-1"
    query = seen[0].url.params["q"]
    assert "meobot_idempotency_reference" in query
    assert "'folder-1' in parents" in query
    assert "trashed=false" in query


async def test_reconciliation_returns_none_when_nothing_was_created() -> None:
    client, _ = _client(lambda request: _ok({"files": []}))
    assert await client.find_by_idempotency_reference("nope") is None


# --- Error translation ------------------------------------------------------
async def test_permission_error_becomes_an_actionable_sentence() -> None:
    """Google's 403 body names the caller; the user gets the fix instead."""
    client, _ = _client(
        lambda request: _error(403, "meobot@x.iam.gserviceaccount.com does not have permission")
    )
    with pytest.raises(IntegrationAuthError) as caught:
        await client.get_file("folder-1")

    assert "service account" in caught.value.message
    assert "iam.gserviceaccount.com" not in caught.value.message


async def test_quota_error_points_at_the_shared_drive_fix() -> None:
    """A service account's My Drive has no quota - the fix is a Shared Drive."""
    client, _ = _client(lambda request: _error(403, "storageQuotaExceeded"))
    with pytest.raises(IntegrationRateLimitError) as caught:
        await client.create_spreadsheet("x", folder_id="folder-1")

    assert "Shared Drive" in caught.value.message


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ValidationError),
        (404, NotFoundError),
        (409, IntegrationError),
        (429, IntegrationRateLimitError),
        (500, IntegrationRateLimitError),
    ],
)
async def test_google_status_codes_map_to_typed_errors(
    status: int, expected: type[Exception]
) -> None:
    client, _ = _client(lambda request: _error(status))
    with pytest.raises(expected):
        await client.get_file("folder-1")


async def test_error_messages_never_leak_the_google_body_to_the_user() -> None:
    """The vendor text is kept for the log, not for the message."""
    secret_ish = "internal detail about project 12345 and caller identity"
    client, _ = _client(lambda request: _error(400, secret_ish))
    with pytest.raises(ValidationError) as caught:
        await client.get_file("folder-1")

    assert secret_ish not in caught.value.message
    assert caught.value.details["google_message"] == secret_ish


async def test_credentials_never_appear_in_a_raised_error() -> None:
    """No token, header or key material in anything a user or log line sees."""
    client, seen = _client(lambda request: _error(403))
    with pytest.raises(IntegrationAuthError) as caught:
        await client.get_file("folder-1")

    rendered = f"{caught.value.message} {caught.value.details}"
    assert "test-access-token" not in rendered
    assert "Bearer" not in rendered
    # ...but the request itself was authenticated.
    assert seen[0].headers["Authorization"] == "Bearer test-access-token"


# --- Capability surface -----------------------------------------------------
@pytest.mark.parametrize("forbidden", ["delete", "trash", "delete_file", "move", "empty_trash"])
def test_the_drive_client_has_no_destructive_method(forbidden: str) -> None:
    """The out-of-scope list is enforced by absence, not by a policy check.

    A tool cannot call what does not exist, and neither can a future handler
    or an LLM plan.
    """
    for implementation in (GoogleDriveClient, FakeDriveClient, NotConfiguredDriveClient):
        assert not hasattr(implementation, forbidden), (
            f"{implementation.__name__} grew a {forbidden!r} method"
        )


def test_the_drive_protocol_declares_no_destructive_operation() -> None:
    exposed = {name for name in dir(DriveClient) if not name.startswith("_")}
    assert not exposed & {"delete", "trash", "move", "delete_file"}


async def test_share_refuses_a_role_that_is_not_offered() -> None:
    """Sharing exists for created files; granting ownership does not."""
    client, _ = _client(lambda request: _ok({}))
    with pytest.raises(ValidationError):
        await client.share_with("file-1", "a@example.com", role="owner")


# --- The not-configured placeholder ----------------------------------------
@pytest.mark.parametrize(
    "operation",
    ["get_file", "list_child_folders", "create_spreadsheet", "copy_file", "rename_file"],
)
async def test_missing_credentials_fail_loudly_and_only_for_drive(operation: str) -> None:
    """Drive configuration errors stay Drive's problem.

    The message has to tell the operator what to set *and* that the rest of
    MeoBot is unaffected - otherwise a missing template id reads like an
    outage.
    """
    client = NotConfiguredDriveClient()
    arguments: dict[str, tuple[Any, ...]] = {
        "get_file": ("folder-1",),
        "list_child_folders": ("folder-1",),
        "create_spreadsheet": ("name",),
        "copy_file": ("template-1",),
        "rename_file": ("file-1", "name"),
    }
    call = getattr(client, operation)
    with pytest.raises(Exception) as caught:
        if operation == "copy_file":
            await call("template-1", name="x")
        else:
            await call(*arguments[operation])

    message = str(caught.value)
    assert "GOOGLE_SERVICE_ACCOUNT_FILE" in message
    assert "vẫn hoạt động bình thường" in message


# --- The fake ---------------------------------------------------------------
async def test_fake_client_deduplicates_on_the_idempotency_reference() -> None:
    """The fake honours the rule the real flow depends on."""
    fake = FakeDriveClient()
    fake.add_folder("folder-1", "Đích")
    properties = {"meobot_idempotency_reference": "key-1"}

    first = await fake.create_spreadsheet(
        "Sheet", folder_id="folder-1", app_properties=properties, idempotency_key="key-1"
    )
    second = await fake.create_spreadsheet(
        "Sheet", folder_id="folder-1", app_properties=properties, idempotency_key="key-1"
    )
    assert first.file_id == second.file_id
    assert len([f for f in fake.files.values() if f.is_spreadsheet]) == 1


async def test_fake_client_finds_a_file_by_its_reference() -> None:
    fake = FakeDriveClient()
    fake.add_folder("folder-1", "Đích")
    created = await fake.create_spreadsheet(
        "Sheet",
        folder_id="folder-1",
        app_properties={"meobot_idempotency_reference": "key-2"},
    )
    found = await fake.find_by_idempotency_reference("key-2", parent_id="folder-1")
    assert found is not None and found.file_id == created.file_id
    assert await fake.find_by_idempotency_reference("key-2", parent_id="other") is None
