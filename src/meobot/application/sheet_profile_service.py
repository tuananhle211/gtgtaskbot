"""Sheet profile management.

Owns the *configuration* used to read a spreadsheet tab: the mapping, the
write-back columns, the schema fingerprint, and the lifecycle state that stops
synchronisation when the sheet was restructured.

Reading the spreadsheet itself belongs to
:class:`~meobot.application.sheet_inspection_service.SheetInspectionService`;
this service never talks to Google.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import ConflictError, NotFoundError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.script import Script
from meobot.db.models.sheet_profile import SheetProfile
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.sheets.models import (
    FieldMapping,
    SchemaFingerprint,
    SheetProfileSpec,
    SheetProfileState,
    WriteBackMapping,
    spreadsheet_url,
)

logger = get_logger(__name__)


class SheetProfileService:
    """CRUD over ``sheet_profiles`` with domain validation.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- Queries ----------------------------------------------------------
    async def list_profiles(self, *, active_only: bool = True) -> Sequence[SheetProfile]:
        """List configured sheet profiles ordered by name."""
        statement = select(SheetProfile).order_by(SheetProfile.name)
        if active_only:
            statement = statement.where(SheetProfile.active.is_(True))
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def list_syncable(self) -> Sequence[SheetProfile]:
        """Profiles that may be read right now.

        A profile in ``schema_changed`` is deliberately excluded: importing
        through a stale mapping is worse than importing nothing.
        """
        result = await self._session.execute(
            select(SheetProfile)
            .where(
                SheetProfile.active.is_(True),
                SheetProfile.state == SheetProfileState.ACTIVE,
            )
            .order_by(SheetProfile.name)
        )
        return result.scalars().all()

    async def get(self, profile_id: uuid.UUID) -> SheetProfile:
        """Fetch one profile.

        Raises:
            NotFoundError: When the profile does not exist.
        """
        profile = await self._session.get(SheetProfile, profile_id)
        if profile is None:
            raise NotFoundError(f"Không tìm thấy sheet profile {profile_id}")
        return profile

    async def find_by_tab(self, spreadsheet_id: str, sheet_name: str) -> SheetProfile | None:
        """Look up a profile by its (spreadsheet, tab) pair."""
        result = await self._session.execute(
            select(SheetProfile).where(
                SheetProfile.spreadsheet_id == spreadsheet_id,
                SheetProfile.sheet_name == sheet_name,
            )
        )
        return result.scalar_one_or_none()

    async def script_counts(self) -> dict[uuid.UUID, int]:
        """``{profile_id: number of imported scripts}`` for the listing views."""
        result = await self._session.execute(
            select(Script.sheet_profile_id, func.count(Script.id)).group_by(Script.sheet_profile_id)
        )
        return {row[0]: int(row[1]) for row in result.all() if row[0] is not None}

    # --- Commands ---------------------------------------------------------
    async def create_profile(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        name: str,
        spreadsheet_id: str,
        sheet_name: str,
        field_mapping: dict[str, Any],
        header_row: int = 1,
        channel: str | None = None,
        script_type_id: uuid.UUID | None = None,
        status_mapping: dict[str, str] | None = None,
        headers: list[str] | None = None,
        write_back_mapping: dict[str, str] | None = None,
        spreadsheet_link: str | None = None,
        state: SheetProfileState = SheetProfileState.ACTIVE,
    ) -> SheetProfile:
        """Create a profile after validating its mapping against the domain rules.

        Args:
            headers: Header row snapshot. When given, its fingerprint is stored
                so later schema drift is detectable.
            write_back_mapping: Optional ``canonical field -> header`` map for
                the columns MeoBot may write back into.

        Raises:
            ValidationError: When the mapping or status mapping is invalid.
            ConflictError: When the (spreadsheet, tab) pair is already configured.
        """
        spec = self._build_spec(
            name=name,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            header_row=header_row,
            channel=channel,
            script_type_id=script_type_id,
            field_mapping=field_mapping,
            status_mapping=status_mapping or {},
            write_back_mapping=write_back_mapping or {},
            state=state,
        )

        missing = spec.field_mapping.missing_essential()
        if missing:
            raise ValidationError(
                f"Thiếu mapping bắt buộc: {missing}",
                details={"missing": missing},
            )

        fingerprint = SchemaFingerprint.from_headers(headers).value if headers else None

        profile = SheetProfile(
            name=spec.name,
            spreadsheet_id=spec.spreadsheet_id,
            sheet_name=spec.sheet_name,
            header_row=spec.header_row,
            channel=spec.channel,
            script_type_id=spec.script_type_id,
            field_mapping=spec.field_mapping.model_dump(mode="json"),
            status_mapping=dict(spec.status_mapping),
            schema_fingerprint=fingerprint,
            active=True,
            spreadsheet_url=spreadsheet_link or spreadsheet_url(spec.spreadsheet_id),
            state=spec.state,
            write_back_mapping=spec.write_back.model_dump(mode="json", exclude_none=True),
            last_headers={"headers": list(headers)} if headers else {},
            created_by=actor.user_id,
            created_by_telegram_id=actor.telegram_user_id,
        )
        self._session.add(profile)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Sheet {sheet_name!r} của spreadsheet này đã được cấu hình",
                details={"spreadsheet_id": spreadsheet_id, "sheet_name": sheet_name},
            ) from exc

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SHEET_PROFILE_CREATED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_profile",
            entity_id=str(profile.id),
            after_data={
                "name": name,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "channel": channel,
                "state": profile.state.value,
            },
        )
        return profile

    async def update_mapping(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile_id: uuid.UUID,
        field_mapping: dict[str, Any],
        headers: list[str] | None = None,
        status_mapping: dict[str, str] | None = None,
        write_back_mapping: dict[str, str] | None = None,
    ) -> SheetProfile:
        """Replace a profile's mapping and clear a ``schema_changed`` state.

        The previous mapping is written into the audit trail before it is
        overwritten, so a bad remap can always be reconstructed.

        Raises:
            NotFoundError: When the profile does not exist.
            ValidationError: When the new mapping is invalid.
        """
        profile = await self.get(profile_id)
        before: dict[str, object] = {
            "field_mapping": dict(profile.field_mapping),
            "state": profile.state.value,
            "schema_fingerprint": profile.schema_fingerprint,
        }

        spec = self._build_spec(
            name=profile.name,
            spreadsheet_id=profile.spreadsheet_id,
            sheet_name=profile.sheet_name,
            header_row=profile.header_row,
            channel=profile.channel,
            script_type_id=profile.script_type_id,
            field_mapping=field_mapping,
            status_mapping=(
                status_mapping
                if status_mapping is not None
                else {str(k): str(v) for k, v in dict(profile.status_mapping).items()}
            ),
            write_back_mapping=(
                write_back_mapping
                if write_back_mapping is not None
                else {
                    str(k): str(v)
                    for k, v in dict(profile.write_back_mapping).items()
                    if v is not None
                }
            ),
            state=SheetProfileState.ACTIVE,
        )
        missing = spec.field_mapping.missing_essential()
        if missing:
            raise ValidationError(
                f"Thiếu mapping bắt buộc: {missing}", details={"missing": missing}
            )

        profile.field_mapping = spec.field_mapping.model_dump(mode="json")
        profile.status_mapping = dict(spec.status_mapping)
        profile.write_back_mapping = spec.write_back.model_dump(mode="json", exclude_none=True)
        profile.state = SheetProfileState.ACTIVE
        if headers:
            profile.schema_fingerprint = SchemaFingerprint.from_headers(headers).value
            profile.last_headers = {"headers": list(headers)}
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SHEET_PROFILE_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_profile",
            entity_id=str(profile.id),
            before_data=before,
            after_data={
                "field_mapping": dict(profile.field_mapping),
                "state": profile.state.value,
                "schema_fingerprint": profile.schema_fingerprint,
            },
        )
        return profile

    async def mark_schema_changed(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile_id: uuid.UUID,
        headers: list[str],
        missing_headers: list[str],
    ) -> SheetProfile:
        """Stop importing and record why. The old mapping is preserved.

        Raises:
            NotFoundError: When the profile does not exist.
        """
        profile = await self.get(profile_id)
        before_state = profile.state
        profile.state = SheetProfileState.SCHEMA_CHANGED
        profile.last_headers = {"headers": list(headers)}
        profile.last_sync_status = "schema_changed"
        profile.last_sync_error = (
            f"Thiếu cột: {', '.join(missing_headers)}" if missing_headers else None
        )
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SHEET_PROFILE_SCHEMA_CHANGED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_profile",
            entity_id=str(profile.id),
            before_data={"state": before_state.value},
            after_data={
                "state": profile.state.value,
                "missing_headers": missing_headers,
                "headers": headers,
            },
        )
        logger.warning(
            "sheet_schema_changed",
            extra={"profile_id": str(profile.id), "missing": missing_headers},
        )
        return profile

    async def record_sync(
        self,
        *,
        profile_id: uuid.UUID,
        status: str,
        error: str | None = None,
    ) -> SheetProfile:
        """Store the outcome of a synchronisation run.

        Raises:
            NotFoundError: When the profile does not exist.
        """
        profile = await self.get(profile_id)
        profile.last_synced_at = utcnow()
        profile.last_sync_status = status[:30]
        profile.last_sync_error = error[:2000] if error else None
        await self._session.flush()
        return profile

    async def deactivate(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> SheetProfile:
        """Stop synchronising a profile without deleting its scripts.

        Raises:
            NotFoundError: When the profile does not exist.
        """
        profile = await self.get(profile_id)
        was_active = profile.active
        profile.active = False
        profile.state = SheetProfileState.INACTIVE
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SHEET_PROFILE_DEACTIVATED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_profile",
            entity_id=str(profile.id),
            before_data={"active": was_active},
            after_data={"active": False, "state": profile.state.value},
        )
        return profile

    # --- Conversion -------------------------------------------------------
    def to_spec(self, profile: SheetProfile) -> SheetProfileSpec:
        """Convert a stored row into the domain value object used by the normalizer."""
        return self._build_spec(
            profile_id=profile.id,
            name=profile.name,
            spreadsheet_id=profile.spreadsheet_id,
            sheet_name=profile.sheet_name,
            header_row=profile.header_row,
            channel=profile.channel,
            script_type_id=profile.script_type_id,
            field_mapping=dict(profile.field_mapping),
            status_mapping={str(k): str(v) for k, v in dict(profile.status_mapping).items()},
            write_back_mapping={
                str(k): str(v) for k, v in dict(profile.write_back_mapping).items() if v is not None
            },
            schema_fingerprint=profile.schema_fingerprint,
            active=profile.active,
            state=profile.state,
            spreadsheet_link=profile.spreadsheet_url,
        )

    @staticmethod
    def _build_spec(
        *,
        name: str,
        spreadsheet_id: str,
        sheet_name: str,
        header_row: int,
        channel: str | None,
        script_type_id: uuid.UUID | None,
        field_mapping: dict[str, Any],
        status_mapping: dict[str, str],
        write_back_mapping: dict[str, str] | None = None,
        profile_id: uuid.UUID | None = None,
        schema_fingerprint: str | None = None,
        active: bool = True,
        state: SheetProfileState = SheetProfileState.ACTIVE,
        spreadsheet_link: str | None = None,
    ) -> SheetProfileSpec:
        """Validate raw values into a :class:`SheetProfileSpec`.

        Raises:
            ValidationError: When Pydantic rejects the mapping payload.
        """
        try:
            return SheetProfileSpec(
                id=profile_id,
                name=name,
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                header_row=header_row,
                channel=channel,
                script_type_id=script_type_id,
                field_mapping=FieldMapping.model_validate(field_mapping),
                status_mapping=status_mapping,
                schema_fingerprint=schema_fingerprint,
                active=active,
                state=state,
                spreadsheet_url=spreadsheet_link,
                write_back=WriteBackMapping.model_validate(write_back_mapping or {}),
            )
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"Cấu hình sheet profile không hợp lệ: {exc}") from exc
