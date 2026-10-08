"""Writing MeoBot's decisions back into the source Google Sheet.

Only columns the profile explicitly maps are written, one cell at a time, in a
single batch request. A profile with no write-back mapping is not an error -
the workflow works entirely inside Telegram, and the sheet stays read-only.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import format_local, utcnow
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.script_review import ScriptReview
from meobot.db.models.sheet_profile import SheetProfile
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.scripts.workflow import ScriptStatus
from meobot.domain.sheets.models import WriteBackMapping, normalize_header
from meobot.integrations.google.sheets import CellUpdate, SheetRange, SheetsClient

logger = get_logger(__name__)

#: How each workflow status reads in the team's sheet.
STATUS_LABELS: dict[ScriptStatus, str] = {
    ScriptStatus.IMPORTED: "TasksBot: đã nhận",
    ScriptStatus.SUBMITTED_FOR_REVIEW: "TasksBot: chờ review",
    ScriptStatus.REVIEWING: "TasksBot: đang review",
    ScriptStatus.AI_REVIEWED: "TasksBot: đã review",
    ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL: "TasksBot: chờ duyệt",
    ScriptStatus.REVISION_REQUIRED: "TasksBot: cần sửa",
    ScriptStatus.APPROVED_FOR_PRODUCTION: "TasksBot: duyệt sản xuất",
    ScriptStatus.IN_PRODUCTION: "TasksBot: đang sản xuất",
    ScriptStatus.ARCHIVED: "TasksBot: lưu trữ",
    ScriptStatus.DRAFT: "TasksBot: nháp",
}

#: Review summaries are trimmed before they reach a spreadsheet cell.
MAX_CELL_TEXT = 500


class SheetWriteBackService:
    """Pushes status, score and approval facts back into the sheet.

    Args:
        session: Unit of work, used only for the audit entry.
        audit: Audit writer sharing the same session.
        sheets: Google Sheets client.
        timezone_name: Display timezone for human-readable timestamps.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        sheets: SheetsClient,
        *,
        timezone_name: str = "Asia/Ho_Chi_Minh",
    ) -> None:
        self._session = session
        self._audit = audit
        self._sheets = sheets
        self._timezone_name = timezone_name

    async def push(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile: SheetProfile,
        script: Script,
        version: ScriptVersion,
        review: ScriptReview | None = None,
        approved_by: str | None = None,
        revision_comment: str | None = None,
    ) -> int:
        """Write the configured cells for one script row.

        Returns the number of cells written; ``0`` when nothing is configured
        or the row number is unknown. Never raises for a Google failure: the
        decision is already committed, and a failed write-back is a warning,
        not a rollback.
        """
        mapping = WriteBackMapping.model_validate(
            {k: v for k, v in dict(profile.write_back_mapping).items() if v}
        )
        columns = mapping.columns()
        row_number = version.source_row_number or script.source_row_number
        if not columns or not row_number:
            return 0

        try:
            headers = await self._sheets.read_headers(
                SheetRange(
                    spreadsheet_id=profile.spreadsheet_id,
                    sheet_name=profile.sheet_name,
                    header_row=profile.header_row,
                )
            )
        except MeoBotError as exc:
            logger.warning(
                "sheet_write_back_header_failed",
                extra={"profile_id": str(profile.id), "error_code": exc.code},
            )
            return 0

        index_of = {normalize_header(header): index for index, header in enumerate(headers)}
        values = self._values(
            script=script,
            review=review,
            approved_by=approved_by,
            revision_comment=revision_comment,
            now=utcnow(),
        )

        updates: list[CellUpdate] = []
        for field_name, header in columns.items():
            column_index = index_of.get(normalize_header(header))
            value = values.get(field_name)
            if column_index is None or value is None:
                continue
            updates.append(
                CellUpdate(
                    sheet_name=profile.sheet_name,
                    row_number=row_number,
                    column_index=column_index,
                    value=value[:MAX_CELL_TEXT],
                )
            )

        if not updates:
            return 0

        try:
            written = await self._sheets.batch_update(profile.spreadsheet_id, updates)
        except MeoBotError as exc:
            logger.warning(
                "sheet_write_back_failed",
                extra={"profile_id": str(profile.id), "error_code": exc.code},
            )
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.SHEET_WRITE_BACK.value,
                result=AuditResult.FAILED,
                entity_type="script",
                entity_id=str(script.id),
                error_message=exc.message,
            )
            return 0

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SHEET_WRITE_BACK.value,
            result=AuditResult.SUCCESS,
            entity_type="script",
            entity_id=str(script.id),
            after_data={
                "sheet_profile_id": str(profile.id),
                "row_number": row_number,
                "cells": len(updates),
                "fields": sorted(columns),
            },
        )
        return written

    def _values(
        self,
        *,
        script: Script,
        review: ScriptReview | None,
        approved_by: str | None,
        revision_comment: str | None,
        now: datetime,
    ) -> dict[str, str | None]:
        """Build the cell values for the configured write-back fields."""
        from zoneinfo import ZoneInfo

        stamp = format_local(now, ZoneInfo(self._timezone_name), "%Y-%m-%d %H:%M")
        approved = script.status is ScriptStatus.APPROVED_FOR_PRODUCTION
        return {
            "meobot_status": STATUS_LABELS.get(script.status, script.status.value),
            "review_score": str(review.overall_score) if review is not None else None,
            "review_summary": review.summary if review is not None else None,
            "reviewed_at": stamp if review is not None else None,
            "approved_by": approved_by if approved else None,
            "approved_at": stamp if approved else None,
            "revision_comment": revision_comment,
        }
