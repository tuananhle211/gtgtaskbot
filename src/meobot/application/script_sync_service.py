"""Idempotent import of sheet rows into scripts and script versions.

The rules this service exists to enforce:

* the same unchanged row never creates a second script or a second version;
* a content change always creates a new version and invalidates the review and
  the approval that belonged to the previous one;
* history (versions, reviews, approvals) is never rewritten or deleted;
* a materially changed sheet schema stops the import instead of silently
  importing garbage.

Content identity is a hash over title/hook/body/production notes only, so a
changed deadline or a re-typed status column does not force a re-review.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.sheet_inspection_service import SheetInspectionService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.sheet_profile import SheetProfile
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.scripts.models import compute_source_hash
from meobot.domain.scripts.workflow import ScriptStatus, can_transition_script
from meobot.domain.sheets.models import NormalizedScript, SheetProfileSpec
from meobot.domain.sheets.normalizer import normalize_rows
from meobot.integrations.google.sheets import SheetRange, SheetsClient

logger = get_logger(__name__)

#: Statuses whose script is no longer tracked against the sheet.
_FROZEN_STATUSES: frozenset[ScriptStatus] = frozenset(
    {ScriptStatus.ARCHIVED, ScriptStatus.IN_PRODUCTION}
)


@dataclass
class SyncReport:
    """What one synchronisation run did. Safe to render to Telegram."""

    profile_id: uuid.UUID
    profile_name: str
    created: int = 0
    new_versions: int = 0
    unchanged: int = 0
    duplicates: list[str] = field(default_factory=list)
    row_errors: list[tuple[int, str]] = field(default_factory=list)
    invalidated_approvals: list[uuid.UUID] = field(default_factory=list)
    needs_remap: bool = False
    missing_headers: list[str] = field(default_factory=list)
    failed: bool = False
    error: str | None = None

    @property
    def touched(self) -> int:
        return self.created + self.new_versions

    def as_dict(self) -> dict[str, object]:
        """Audit-friendly summary. No cell contents."""
        return {
            "profile_id": str(self.profile_id),
            "created": self.created,
            "new_versions": self.new_versions,
            "unchanged": self.unchanged,
            "duplicates": len(self.duplicates),
            "row_errors": len(self.row_errors),
            "invalidated_approvals": len(self.invalidated_approvals),
            "needs_remap": self.needs_remap,
            "failed": self.failed,
        }


class ScriptSyncService:
    """Reads a configured sheet and reconciles it with the ``scripts`` table.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
        sheets: Google Sheets client.
        profiles: Sheet profile service, for state and sync bookkeeping.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        sheets: SheetsClient,
        profiles: SheetProfileService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._sheets = sheets
        self._profiles = profiles

    async def sync_profile(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile: SheetProfile,
    ) -> SyncReport:
        """Import one profile's rows. Never raises for ordinary failures.

        A Google outage, a revoked share or a renamed tab is reported through
        :class:`SyncReport` and recorded on the profile, because a scheduled
        run must not crash the worker.
        """
        report = SyncReport(profile_id=profile.id, profile_name=profile.name)
        spec = self._profiles.to_spec(profile)

        try:
            changed, headers, missing = await SheetInspectionService(
                self._sheets
            ).detect_schema_change(
                spreadsheet_id=profile.spreadsheet_id,
                sheet_name=profile.sheet_name,
                header_row=profile.header_row,
                stored_fingerprint=profile.schema_fingerprint,
                field_mapping=dict(profile.field_mapping),
            )
            if changed:
                report.needs_remap = True
                report.missing_headers = missing
                await self._profiles.mark_schema_changed(
                    actor=actor,
                    request_id=request_id,
                    profile_id=profile.id,
                    headers=headers,
                    missing_headers=missing,
                )
                return report

            raw_rows = await self._sheets.read_rows(
                SheetRange(
                    spreadsheet_id=profile.spreadsheet_id,
                    sheet_name=profile.sheet_name,
                    header_row=profile.header_row,
                )
            )
        except MeoBotError as exc:
            report.failed = True
            report.error = exc.message
            await self._profiles.record_sync(
                profile_id=profile.id, status="failed", error=exc.message
            )
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.SHEET_SYNCED.value,
                result=AuditResult.FAILED,
                entity_type="sheet_profile",
                entity_id=str(profile.id),
                error_message=exc.message,
            )
            logger.warning(
                "sheet_sync_failed",
                extra={"profile_id": str(profile.id), "error_code": exc.code},
            )
            return report

        normalized, errors = normalize_rows(
            spec,
            list(raw_rows),
            first_row_number=profile.header_row + 1,
            skip_invalid=True,
            synthesize_missing=True,
        )
        report.row_errors = errors

        seen: set[str] = set()
        for script_row in normalized:
            if script_row.script_id in seen:
                report.duplicates.append(script_row.script_id)
                continue
            seen.add(script_row.script_id)
            await self._upsert(
                actor=actor,
                request_id=request_id,
                profile=profile,
                spec=spec,
                normalized=script_row,
                report=report,
            )

        await self._profiles.record_sync(profile_id=profile.id, status="ok")
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SHEET_SYNCED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_profile",
            entity_id=str(profile.id),
            after_data=report.as_dict(),
        )
        logger.info("sheet_sync_completed", extra=report.as_dict())
        return report

    async def _upsert(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile: SheetProfile,
        spec: SheetProfileSpec,
        normalized: NormalizedScript,
        report: SyncReport,
    ) -> None:
        """Create, version or leave alone one script."""
        source_hash = compute_source_hash(
            title=normalized.title,
            hook=normalized.hook,
            script_body=normalized.script_body,
            production_notes=normalized.production_notes,
        )

        script = await self._find(profile.id, normalized.script_id)
        if script is None:
            await self._create(
                actor=actor,
                request_id=request_id,
                profile=profile,
                normalized=normalized,
                source_hash=source_hash,
                report=report,
            )
            return

        if script.status in _FROZEN_STATUSES:
            report.unchanged += 1
            return

        current = script.current_version
        if current is not None and current.source_hash == source_hash:
            # Content identical: refresh only the metadata that may drift.
            script.source_row_number = normalized.source.row_number
            script.author = normalized.author or script.author
            script.deadline = normalized.deadline or script.deadline
            script.last_synced_at = utcnow()
            await self._session.flush()
            report.unchanged += 1
            return

        await self._add_version(
            actor=actor,
            request_id=request_id,
            script=script,
            normalized=normalized,
            source_hash=source_hash,
            report=report,
        )

    async def _find(self, profile_id: uuid.UUID, external_id: str) -> Script | None:
        result = await self._session.execute(
            select(Script).where(
                Script.sheet_profile_id == profile_id,
                Script.external_script_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def _create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile: SheetProfile,
        normalized: NormalizedScript,
        source_hash: str,
        report: SyncReport,
    ) -> None:
        """Insert a new script with version 1."""
        script = Script(
            sheet_profile_id=profile.id,
            external_script_id=normalized.script_id,
            source_row_number=normalized.source.row_number,
            script_type_id=profile.script_type_id,
            status=ScriptStatus.IMPORTED,
            author=normalized.author,
            deadline=normalized.deadline,
            channel=normalized.channel,
            last_synced_at=utcnow(),
        )
        try:
            # A savepoint, not the outer transaction: a lost race must not roll
            # back the rows this run already imported.
            async with self._session.begin_nested():
                self._session.add(script)
                await self._session.flush()
        except IntegrityError:
            # Two concurrent syncs of the same profile raced. The other one
            # won; treat this row as a duplicate rather than failing the batch.
            report.duplicates.append(normalized.script_id)
            logger.info("script_insert_raced", extra={"external_script_id": normalized.script_id})
            return

        version = self._new_version(script.id, 1, normalized, source_hash)
        self._session.add(version)
        await self._session.flush()
        script.current_version_id = version.id
        await self._session.flush()

        report.created += 1
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_IMPORTED.value,
            result=AuditResult.SUCCESS,
            entity_type="script",
            entity_id=str(script.id),
            after_data={
                "external_script_id": normalized.script_id,
                "sheet_profile_id": str(profile.id),
                "row_number": normalized.source.row_number,
                "version": 1,
            },
        )

    async def _add_version(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        script: Script,
        normalized: NormalizedScript,
        source_hash: str,
        report: SyncReport,
    ) -> None:
        """Append a new version and reset the script into the review queue."""
        next_number = await self._next_version_number(script.id)
        version = self._new_version(script.id, next_number, normalized, source_hash)
        self._session.add(version)
        await self._session.flush()

        status_before = script.status
        was_approved = status_before is ScriptStatus.APPROVED_FOR_PRODUCTION

        script.current_version_id = version.id
        script.source_row_number = normalized.source.row_number
        script.author = normalized.author or script.author
        script.deadline = normalized.deadline or script.deadline
        script.last_synced_at = utcnow()
        if can_transition_script(status_before, ScriptStatus.SUBMITTED_FOR_REVIEW):
            script.status = ScriptStatus.SUBMITTED_FOR_REVIEW
        await self._session.flush()

        report.new_versions += 1
        if was_approved:
            report.invalidated_approvals.append(script.id)
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.SCRIPT_APPROVAL_INVALIDATED.value,
                result=AuditResult.SUCCESS,
                entity_type="script",
                entity_id=str(script.id),
                before_data={"status": status_before.value},
                after_data={"status": script.status.value, "version": next_number},
            )

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_VERSION_CREATED.value,
            result=AuditResult.SUCCESS,
            entity_type="script",
            entity_id=str(script.id),
            before_data={"status": status_before.value},
            after_data={"status": script.status.value, "version": next_number},
        )

    async def _next_version_number(self, script_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(ScriptVersion.version_number)
            .where(ScriptVersion.script_id == script_id)
            .order_by(ScriptVersion.version_number.desc())
            .limit(1)
        )
        highest = result.scalar_one_or_none()
        return int(highest or 0) + 1

    @staticmethod
    def _new_version(
        script_id: uuid.UUID,
        version_number: int,
        normalized: NormalizedScript,
        source_hash: str,
    ) -> ScriptVersion:
        return ScriptVersion(
            script_id=script_id,
            version_number=version_number,
            title=normalized.title[:500],
            hook=normalized.hook,
            script_body=normalized.script_body,
            production_notes=normalized.production_notes,
            source_hash=source_hash,
            source_row_number=normalized.source.row_number,
            raw_source_data={
                "source_status": normalized.source_status,
                "extra": dict(normalized.extra),
                "warnings": list(normalized.warnings),
            },
        )


def summarize_reports(reports: Sequence[SyncReport]) -> str:
    """Render one or more sync reports as a Telegram message."""
    if not reports:
        return "Không có sheet profile nào đang hoạt động để đồng bộ."

    lines: list[str] = ["📥 Kết quả đồng bộ Google Sheet:"]
    for report in reports:
        if report.failed:
            lines.append(f"❌ {report.profile_name}: {report.error}")
            continue
        if report.needs_remap:
            lines.append(
                f"⚠️ {report.profile_name}: cấu trúc sheet đã thay đổi "
                f"(thiếu cột: {', '.join(report.missing_headers) or 'không rõ'}). "
                "Cần chạy lại /add_sheet để map lại."
            )
            continue
        parts = [
            f"mới {report.created}",
            f"bản mới {report.new_versions}",
            f"không đổi {report.unchanged}",
        ]
        if report.duplicates:
            parts.append(f"trùng ID {len(report.duplicates)}")
        if report.row_errors:
            parts.append(f"lỗi dòng {len(report.row_errors)}")
        lines.append(f"✅ {report.profile_name}: " + ", ".join(parts))
        if report.invalidated_approvals:
            lines.append(
                f"   ⚠️ {len(report.invalidated_approvals)} kịch bản đã duyệt bị sửa nội dung "
                "→ cần review và duyệt lại."
            )
    return "\n".join(lines)
