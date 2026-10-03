"""Value objects for Drive folders and created spreadsheets."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from meobot.integrations.base import build_idempotency_key


class FolderValidationStatus(StrEnum):
    """Result of the last check of a registered folder against Google."""

    UNVALIDATED = "unvalidated"
    VALID = "valid"
    NOT_FOUND = "not_found"
    NO_ACCESS = "no_access"
    NOT_A_FOLDER = "not_a_folder"
    OUTSIDE_ROOT = "outside_root"
    CANNOT_CREATE = "cannot_create"


class CreationStatus(StrEnum):
    """Lifecycle of one spreadsheet-creation attempt."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CreationMethod(StrEnum):
    """How the file came to exist. Reported to the user, never guessed."""

    #: ``files.copy`` of a human-designed template: keeps formatting, formulas,
    #: dropdowns, charts, conditional formatting and protected ranges.
    TEMPLATE_COPY = "template_copy"
    #: Generated from scratch with standard header rows because no template id
    #: is configured. Functional, but plain.
    BLANK_STANDARD = "blank_standard"


class TemplateKind(StrEnum):
    """The two kinds of spreadsheet MeoBot produces."""

    WORK_MANAGEMENT = "work_management"
    SCRIPT_MANAGEMENT = "script_management"


#: ``/folders/<id>`` in a Drive URL, or a bare id. Drive ids are base64url-ish;
#: a Shared Drive root id is shorter than a folder id, so the floor is low.
_FOLDER_URL_PATTERN = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_DRIVE_QUERY_PATTERN = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")
_BARE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def extract_folder_id(url_or_id: str) -> str | None:
    """Pull a folder id out of a Drive URL, or accept a bare id.

    Returns ``None`` when the input is neither, so the caller asks the human
    again instead of sending a malformed request to Google.
    """
    candidate = url_or_id.strip()
    if not candidate:
        return None
    for pattern in (_FOLDER_URL_PATTERN, _DRIVE_QUERY_PATTERN):
        match = pattern.search(candidate)
        if match is not None:
            return match.group(1)
    if _BARE_ID_PATTERN.match(candidate):
        return candidate
    return None


def folder_url(folder_id: str) -> str:
    """Human-clickable link to a Drive folder."""
    return f"https://drive.google.com/drive/folders/{folder_id}"


def short_id(identifier: str | None, *, keep: int = 6) -> str:
    """Shorten an id for display.

    Drive ids are not secrets, but a full id in a Telegram message is noise and
    invites copy-paste into the wrong place.
    """
    if not identifier:
        return "—"
    if len(identifier) <= keep * 2:
        return identifier
    return f"{identifier[:keep]}…{identifier[-keep:]}"


class SpreadsheetRequest(BaseModel):
    """A validated request to create one spreadsheet.

    The idempotency key is derived from everything that makes the request
    *this* request. Two presses of the same confirmation button produce the
    same key and therefore the same file; a genuinely new request (different
    name, different folder) produces a different one.
    """

    model_config = ConfigDict(frozen=True)

    template_code: str = Field(min_length=1, max_length=100)
    template_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=300)
    folder_id: str = Field(min_length=1, max_length=200)
    kind: TemplateKind
    team: str | None = Field(default=None, max_length=100)
    channel: str | None = Field(default=None, max_length=100)
    campaign: str | None = Field(default=None, max_length=200)
    period: str | None = Field(default=None, max_length=100)
    actor_reference: str = Field(default="", max_length=100)
    confirmation_reference: str = Field(default="", max_length=100)
    register_profile: bool = Field(
        default=False,
        description="Script sheets register a Sheet Profile; work sheets never do.",
    )
    run_initial_sync: bool = False

    def idempotency_key(self) -> str:
        """Stable key for this exact request."""
        return build_idempotency_key(
            "spreadsheet.create",
            self.actor_reference,
            self.template_code,
            self.template_version,
            self.name.strip().lower(),
            self.folder_id,
            self.confirmation_reference,
        )

    def application_properties(self, *, environment: str) -> dict[str, str]:
        """Drive ``appProperties`` written on the created file.

        Safe by construction: template identity, environment and the
        idempotency reference used for reconciliation. No secrets, no personal
        data - Drive file properties are visible to anyone with file access.
        """
        return {
            "meobot_managed": "true",
            "meobot_template_code": self.template_code,
            "meobot_template_version": str(self.template_version),
            "meobot_environment": environment,
            "meobot_idempotency_reference": self.idempotency_key(),
        }
