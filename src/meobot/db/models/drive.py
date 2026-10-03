"""Drive folders, sheet templates and the spreadsheets MeoBot created.

The three tables enforce the safety property that makes Drive writes
acceptable at all: **MeoBot cannot create a file anywhere it likes.** It can
only create files inside a folder an OWNER or ADMIN explicitly registered in
``drive_folders``, from a template registered in ``sheet_templates``, and every
file it created is recorded in ``created_spreadsheets`` with a unique
idempotency key so a retried Celery task or a double-tapped inline button
cannot produce a second file.

There is deliberately no deletion path here, and no tool that would use one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class DriveFolder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A folder MeoBot is allowed to create files in.

    ``validation_status`` records the last check against Google: the folder
    exists, the service account can read it, and the service account can add
    children. A folder that has never validated is not offered as a
    destination.
    """

    __tablename__ = "drive_folders"
    __table_args__ = (UniqueConstraint("drive_folder_id", name="uq_drive_folders_drive_folder_id"),)

    drive_folder_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    shared_drive_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    path_label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(300), nullable=True)
    team_scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unvalidated"
    )
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SheetTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A spreadsheet MeoBot knows how to produce.

    ``source_file_id`` is a human-designed Google Sheet that gets copied with
    ``files.copy`` - which preserves formatting, formulas, dropdowns, charts,
    conditional formatting and protected ranges that no generated file would
    have. When it is absent MeoBot falls back to generating a blank spreadsheet
    with the standard header rows, and says which of the two it did.
    """

    __tablename__ = "sheet_templates"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_sheet_templates_code_version"),)

    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("drive_folders.id", ondelete="SET NULL"), nullable=True
    )
    default_worksheet_name: Mapped[str] = mapped_column(String(200), nullable=False)
    expected_tabs: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    default_field_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    default_write_back_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CreatedSpreadsheet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One spreadsheet MeoBot created, and the record that stops a second one.

    ``idempotency_key`` is unique and is written *before* Google is called. A
    repeated inline-button press, a Celery retry, or a task that crashed after
    Drive succeeded but before the commit all land on the same row: the
    reconciler finds the file by its ``meobot_idempotency_reference`` app
    property rather than creating another one.
    """

    __tablename__ = "created_spreadsheets"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_created_spreadsheets_idempotency_key"),
        UniqueConstraint("drive_file_id", name="uq_created_spreadsheets_drive_file_id"),
        Index("ix_created_spreadsheets_status_created", "creation_status", "created_at"),
    )

    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sheet_templates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Nullable until Google answers - the row exists before the file does.
    drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    spreadsheet_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    parent_folder_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shared_drive_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="work_management")
    template_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creation_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sheet_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sheet_profiles.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    creation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    creation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
