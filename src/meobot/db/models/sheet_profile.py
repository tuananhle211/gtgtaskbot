"""``sheet_profiles`` - how to read one specific Google Sheet."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin
from meobot.domain.sheets.models import SheetProfileState


class SheetProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Adapter configuration for a single spreadsheet tab.

    ``schema_fingerprint`` stores the hash of the header row last seen. When it
    changes, the sheet was restructured and the mapping must be re-confirmed
    (milestone 2: MeoBot proposes a new mapping, a human approves it).
    """

    __tablename__ = "sheet_profiles"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "sheet_name", name="uq_sheet_profiles_spreadsheet_tab"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spreadsheet_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    sheet_name: Mapped[str] = mapped_column(String(200), nullable=False)
    header_row: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    channel: Mapped[str | None] = mapped_column(String(100), nullable=True)
    script_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_types.id", ondelete="SET NULL"), nullable=True, index=True
    )
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    status_mapping: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    schema_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Milestone 2 -----------------------------------------------------
    spreadsheet_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[SheetProfileState] = mapped_column(
        SAEnum(
            SheetProfileState,
            name="sheet_profile_state",
            native_enum=False,
            length=30,
            validate_strings=True,
        ),
        nullable=False,
        default=SheetProfileState.NEEDS_MAPPING,
    )
    write_back_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    last_headers: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
