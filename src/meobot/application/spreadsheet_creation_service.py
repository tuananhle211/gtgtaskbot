"""Creating spreadsheets on Google Drive, exactly once.

The hard part is not calling Drive. It is that Drive has no idempotent create:
``files.create`` called twice makes two files, and a Celery retry, a
double-tapped inline button, or a task that dies between "Google said yes" and
"the database committed" would each leave a stray spreadsheet in a team's
folder.

The sequence that prevents it:

1. Write a ``created_spreadsheets`` row with the idempotency key **before**
   calling Google. The unique constraint on that key is the lock.
2. If the key already exists: a succeeded row is returned as-is (no second
   file), a pending row is reconciled rather than re-created.
3. Reconcile by asking Drive for a file carrying our own
   ``meobot_idempotency_reference`` app property. If Google made the file and
   we lost the answer, we find it instead of making another.
4. Only then create - copying the template when one is configured, otherwise
   generating a standard blank spreadsheet - and record which of the two it
   was.

A script sheet additionally registers a Sheet Profile with the mapping from
:mod:`meobot.domain.drive.templates`. A work sheet never does: it has no
``script_body`` column, and importing it would produce nonsense.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.drive_folder_service import DriveFolderService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.application.sheet_template_service import SheetTemplateService
from meobot.core.config import Settings
from meobot.core.errors import IntegrationError, MeoBotError, NotFoundError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.drive import CreatedSpreadsheet, DriveFolder, SheetTemplate
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.drive.models import (
    CreationMethod,
    CreationStatus,
    SpreadsheetRequest,
    TemplateKind,
)
from meobot.domain.drive.templates import TemplateSpec, template_for_kind
from meobot.domain.identity.models import Actor
from meobot.domain.sheets.models import spreadsheet_url
from meobot.integrations.google.drive import DriveClient, DriveFile
from meobot.integrations.google.sheets import SheetsClient

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CreationOutcome:
    """What a creation attempt produced."""

    record: CreatedSpreadsheet
    created_now: bool
    method: CreationMethod | None
    profile_id: uuid.UUID | None = None

    @property
    def url(self) -> str | None:
        return self.record.spreadsheet_url

    @property
    def succeeded(self) -> bool:
        return self.record.creation_status == CreationStatus.SUCCEEDED.value


class SpreadsheetCreationService:
    """Creates spreadsheets from registered templates into allowed folders.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
        drive: Drive client (file creation, copying, reconciliation).
        sheets: Sheets client (header rows for the blank fallback).
        settings: Environment label and template configuration.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        drive: DriveClient,
        sheets: SheetsClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._audit = audit
        self._drive = drive
        self._sheets = sheets
        self._settings = settings
        self._templates = SheetTemplateService(session, audit, settings)
        self._folders = DriveFolderService(session, audit, drive, settings)

    # --- Queries ----------------------------------------------------------
    async def list_created(self, *, limit: int = 20) -> list[CreatedSpreadsheet]:
        """Spreadsheets MeoBot created, newest first."""
        result = await self._session.execute(
            select(CreatedSpreadsheet)
            .order_by(desc(CreatedSpreadsheet.created_at))
            .limit(min(limit, 100))
        )
        return list(result.scalars().all())

    async def get(self, record_id: uuid.UUID) -> CreatedSpreadsheet:
        """Fetch one creation record.

        Raises:
            NotFoundError: When the record does not exist.
        """
        record = await self._session.get(CreatedSpreadsheet, record_id)
        if record is None:
            raise NotFoundError(f"Không tìm thấy bản ghi tạo Sheet: {record_id}")
        return record

    async def find_by_key(self, idempotency_key: str) -> CreatedSpreadsheet | None:
        """Look up a creation attempt by its idempotency key."""
        result = await self._session.execute(
            select(CreatedSpreadsheet).where(CreatedSpreadsheet.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    # --- Preview ----------------------------------------------------------
    async def preview(self, request: SpreadsheetRequest) -> dict[str, object]:
        """Everything a human should see before confirming.

        Deliberately resolves the *actual* destination and the *actual*
        creation method, so the confirmation shows what will happen rather than
        what was asked for.
        """
        template = await self._templates.for_kind(request.kind)
        spec = template_for_kind(request.kind)
        folder = await self._folders.find_by_drive_id(request.folder_id)
        method = (
            CreationMethod.TEMPLATE_COPY
            if template.source_file_id
            else CreationMethod.BLANK_STANDARD
        )
        return {
            "template_code": template.code,
            "template_version": template.version,
            "template_name": template.name,
            "creation_method": method.value,
            "name": request.name,
            "folder_name": folder.name if folder is not None else request.folder_id,
            "folder_id": request.folder_id,
            "shared_drive": bool(folder.shared_drive_id) if folder is not None else False,
            "tabs": spec.expected_tabs,
            "worksheet": spec.default_worksheet_name,
            "columns": list(spec.primary_headers),
            "team": request.team,
            "channel": request.channel,
            "campaign": request.campaign,
            "period": request.period,
            "will_register_profile": request.register_profile,
            "will_sync": request.run_initial_sync,
            "idempotency_key": request.idempotency_key(),
        }

    # --- Creation ---------------------------------------------------------
    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        request: SpreadsheetRequest,
    ) -> CreationOutcome:
        """Create one spreadsheet, at most once per idempotency key."""
        template = await self._templates.for_kind(request.kind)
        spec = template_for_kind(request.kind)
        folder = await self._folders.find_by_drive_id(request.folder_id)
        if folder is None:
            raise NotFoundError(
                "Thư mục đích chưa được đăng ký với TasksBot. Dùng /add_drive_folder trước."
            )
        self._folders.assert_usable_by(folder, actor)

        key = request.idempotency_key()
        existing = await self.find_by_key(key)
        if existing is not None:
            resolved = await self._resume(existing, request, template, spec, folder, actor)
            if resolved is not None:
                return resolved

        record = existing or await self._reserve(request, template, folder, actor, key)

        try:
            drive_file, method = await self._create_on_drive(request, template, spec, key)
        except MeoBotError as exc:
            record.creation_status = CreationStatus.FAILED.value
            record.creation_error = exc.message[:1000]
            await self._session.flush()
            await self._audit_creation(actor, request_id, record, AuditResult.FAILED, exc.message)
            raise

        return await self._finalise(
            actor=actor,
            request_id=request_id,
            record=record,
            request=request,
            template=template,
            spec=spec,
            folder=folder,
            drive_file=drive_file,
            method=method,
            created_now=True,
        )

    async def _resume(
        self,
        record: CreatedSpreadsheet,
        request: SpreadsheetRequest,
        template: SheetTemplate,
        spec: TemplateSpec,
        folder: DriveFolder,
        actor: Actor,
    ) -> CreationOutcome | None:
        """Handle a repeat of a request we have already seen.

        Returns the settled outcome, or ``None`` when the caller should proceed
        to create (the previous attempt failed before Google made anything).
        """
        if record.creation_status == CreationStatus.SUCCEEDED.value:
            logger.info(
                "spreadsheet_creation_deduplicated",
                extra={"record_id": str(record.id), "drive_file_id": record.drive_file_id},
            )
            return CreationOutcome(
                record=record,
                created_now=False,
                method=(CreationMethod(record.creation_method) if record.creation_method else None),
                profile_id=record.sheet_profile_id,
            )

        # Pending or failed: ask Drive whether the file exists after all.
        existing_file = await self._reconcile_on_drive(record, request)
        if existing_file is None:
            record.creation_status = CreationStatus.PENDING.value
            record.creation_error = None
            await self._session.flush()
            return None

        logger.info(
            "spreadsheet_creation_reconciled",
            extra={"record_id": str(record.id), "drive_file_id": existing_file.file_id},
        )
        return await self._finalise(
            actor=actor,
            request_id=uuid.uuid4(),
            record=record,
            request=request,
            template=template,
            spec=spec,
            folder=folder,
            drive_file=existing_file,
            method=(
                CreationMethod(record.creation_method)
                if record.creation_method
                else CreationMethod.BLANK_STANDARD
            ),
            created_now=False,
        )

    async def _reconcile_on_drive(
        self,
        record: CreatedSpreadsheet,
        request: SpreadsheetRequest,
    ) -> DriveFile | None:
        """Look for a file this attempt may already have created."""
        if record.drive_file_id:
            try:
                return await self._drive.get_file(record.drive_file_id)
            except (NotFoundError, IntegrationError):
                logger.info(
                    "spreadsheet_reconcile_file_missing",
                    extra={"record_id": str(record.id)},
                )
        try:
            return await self._drive.find_by_idempotency_reference(
                record.idempotency_key, parent_id=request.folder_id
            )
        except (NotFoundError, IntegrationError):
            logger.warning(
                "spreadsheet_reconcile_lookup_failed", extra={"record_id": str(record.id)}
            )
            return None

    async def _reserve(
        self,
        request: SpreadsheetRequest,
        template: SheetTemplate,
        folder: DriveFolder,
        actor: Actor,
        key: str,
    ) -> CreatedSpreadsheet:
        """Claim the idempotency key before Google is called.

        Raises:
            ConflictError: When a concurrent request won the race. That is the
                correct outcome - one of the two callers created the file and
                the other must not.
        """
        record = CreatedSpreadsheet(
            template_id=template.id,
            name=request.name[:300],
            parent_folder_id=request.folder_id,
            shared_drive_id=folder.shared_drive_id,
            kind=request.kind.value,
            template_code=template.code,
            template_version=template.version,
            metadata_json={
                "team": request.team,
                "channel": request.channel,
                "campaign": request.campaign,
                "period": request.period,
            },
            created_by_user_id=actor.user_id,
            created_by_telegram_id=actor.telegram_user_id,
            idempotency_key=key,
            creation_status=CreationStatus.PENDING.value,
        )
        self._session.add(record)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            duplicate = await self.find_by_key(key)
            if duplicate is not None:
                return duplicate
            raise
        return record

    async def _create_on_drive(
        self,
        request: SpreadsheetRequest,
        template: SheetTemplate,
        spec: TemplateSpec,
        key: str,
    ) -> tuple[DriveFile, CreationMethod]:
        """Copy the template, or generate a standard blank spreadsheet."""
        properties = request.application_properties(environment=self._settings.app_env)

        if template.source_file_id:
            drive_file = await self._drive.copy_file(
                template.source_file_id,
                name=request.name,
                folder_id=request.folder_id,
                app_properties=properties,
            )
            return drive_file, CreationMethod.TEMPLATE_COPY

        drive_file = await self._drive.create_spreadsheet(
            request.name,
            folder_id=request.folder_id,
            app_properties=properties,
            idempotency_key=key,
        )
        await self._initialise_blank(drive_file.spreadsheet_id, spec)
        return drive_file, CreationMethod.BLANK_STANDARD

    async def _initialise_blank(self, spreadsheet_id: str, spec: TemplateSpec) -> None:
        """Lay down the standard tabs, headers and dropdown vocabularies.

        A failure here is logged but not fatal: the file exists and is already
        recorded, so raising would strand a real spreadsheet behind a failed
        transaction. The user is told the sheet was created and can be
        re-initialised.
        """
        try:
            await self._sheets.ensure_worksheets(spreadsheet_id, spec.expected_tabs)
            for tab in spec.tabs:
                rows = [list(tab.headers), *[list(row) for row in tab.seed_rows]]
                if not tab.headers:
                    continue
                await self._sheets.write_rows(spreadsheet_id, tab.title, rows)
        except MeoBotError as exc:
            logger.warning(
                "spreadsheet_initialisation_failed",
                extra={"spreadsheet_id": spreadsheet_id, "error_code": exc.code},
            )

    async def _finalise(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        record: CreatedSpreadsheet,
        request: SpreadsheetRequest,
        template: SheetTemplate,
        spec: TemplateSpec,
        folder: DriveFolder,
        drive_file: DriveFile,
        method: CreationMethod,
        created_now: bool,
    ) -> CreationOutcome:
        """Record the created file and register a Sheet Profile when due."""
        record.drive_file_id = drive_file.file_id
        record.spreadsheet_id = drive_file.spreadsheet_id
        record.spreadsheet_url = drive_file.web_link or spreadsheet_url(drive_file.spreadsheet_id)
        record.shared_drive_id = drive_file.drive_id or folder.shared_drive_id
        record.creation_method = method.value
        record.creation_status = CreationStatus.SUCCEEDED.value
        record.creation_error = None
        await self._session.flush()

        profile_id: uuid.UUID | None = record.sheet_profile_id
        if request.register_profile and profile_id is None:
            profile_id = await self._register_profile(
                actor=actor,
                request_id=request_id,
                request=request,
                spec=spec,
                drive_file=drive_file,
            )
            record.sheet_profile_id = profile_id
            await self._session.flush()

        await self._audit_creation(actor, request_id, record, AuditResult.SUCCESS, None)
        logger.info(
            "spreadsheet_created",
            extra={
                "record_id": str(record.id),
                "template_code": template.code,
                "method": method.value,
                "created_now": created_now,
            },
        )
        return CreationOutcome(
            record=record,
            created_now=created_now,
            method=method,
            profile_id=profile_id,
        )

    async def _register_profile(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        request: SpreadsheetRequest,
        spec: TemplateSpec,
        drive_file: DriveFile,
    ) -> uuid.UUID | None:
        """Register a Sheet Profile for a script sheet we just created.

        The mapping comes from the template definition, not from the LLM: the
        structure is one MeoBot designed, so asking a model to guess it would
        be strictly worse. (A sheet whose headers later stop matching is caught
        by the existing schema-fingerprint check, which stops sync and asks.)

        Work-management sheets never reach this method - they have no
        ``script_body`` column and are not script sources.
        """
        if request.kind is not TemplateKind.SCRIPT_MANAGEMENT:  # pragma: no cover - guarded above
            return None

        profiles = SheetProfileService(self._session, self._audit)
        field_mapping = {field: [header] for field, header in spec.default_field_mapping.items()}
        try:
            profile = await profiles.create_profile(
                actor=actor,
                request_id=request_id,
                name=request.name[:200],
                spreadsheet_id=drive_file.spreadsheet_id,
                sheet_name=spec.default_worksheet_name,
                field_mapping=field_mapping,
                channel=request.channel,
                write_back_mapping=dict(spec.default_write_back_mapping),
                headers=list(spec.primary_headers),
                spreadsheet_link=drive_file.web_link or spreadsheet_url(drive_file.spreadsheet_id),
            )
        except MeoBotError as exc:
            # The spreadsheet exists and is recorded; a profile failure is
            # reported, not rolled back into "creation failed".
            logger.warning(
                "sheet_profile_registration_failed",
                extra={"spreadsheet_id": drive_file.spreadsheet_id, "error_code": exc.code},
            )
            return None
        return profile.id

    async def _audit_creation(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        record: CreatedSpreadsheet,
        result: AuditResult,
        error: str | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.TOOL_EXECUTED.value,
            result=result,
            entity_type="created_spreadsheet",
            entity_id=str(record.id),
            after_data={
                "name": record.name,
                "kind": record.kind,
                "template_code": record.template_code,
                "template_version": record.template_version,
                "drive_file_id": record.drive_file_id,
                "parent_folder_id": record.parent_folder_id,
                "creation_method": record.creation_method,
                "creation_status": record.creation_status,
            },
            error_message=error,
        )

    # --- Reconciliation ---------------------------------------------------
    async def reconcile_pending(self, *, actor: Actor, request_id: uuid.UUID) -> dict[str, int]:
        """Settle creation records that never reached a terminal state.

        Runs from Celery. For each pending row it asks Drive whether the file
        exists (by our own app property) and settles the row either way. It
        never creates a file - reconciliation only ever *finds*.
        """
        result = await self._session.execute(
            select(CreatedSpreadsheet).where(
                CreatedSpreadsheet.creation_status == CreationStatus.PENDING.value
            )
        )
        pending = list(result.scalars().all())
        recovered = 0
        failed = 0

        for record in pending:
            try:
                found = await self._drive.find_by_idempotency_reference(
                    record.idempotency_key, parent_id=record.parent_folder_id
                )
            except MeoBotError:
                logger.warning("spreadsheet_reconcile_failed", extra={"record_id": str(record.id)})
                continue

            if found is None:
                record.creation_status = CreationStatus.FAILED.value
                record.creation_error = "Không tìm thấy file tương ứng trên Drive."
                failed += 1
            else:
                record.drive_file_id = found.file_id
                record.spreadsheet_id = found.spreadsheet_id
                record.spreadsheet_url = found.web_link or spreadsheet_url(found.spreadsheet_id)
                record.creation_status = CreationStatus.SUCCEEDED.value
                record.creation_error = None
                recovered += 1
            await self._session.flush()
            await self._audit_creation(
                actor,
                request_id,
                record,
                AuditResult.SUCCESS if found is not None else AuditResult.FAILED,
                record.creation_error,
            )

        if pending:
            logger.info(
                "spreadsheet_reconcile_completed",
                extra={"pending": len(pending), "recovered": recovered, "failed": failed},
            )
        return {"pending": len(pending), "recovered": recovered, "failed": failed}

    @staticmethod
    def touched_at(record: CreatedSpreadsheet) -> str:
        """ISO timestamp used by API responses."""
        stamp = record.created_at or utcnow()
        return stamp.isoformat()
