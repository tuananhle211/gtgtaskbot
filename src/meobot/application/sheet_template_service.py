"""The registry of spreadsheets MeoBot knows how to produce.

Built-in templates are defined in :mod:`meobot.domain.drive.templates` and
upserted into ``sheet_templates`` by :meth:`SheetTemplateService.ensure_builtin`
rather than seeded by a migration. That keeps one source of truth: the column
lists, the tab names and the Sheet-Profile mapping are the same object the
creation service reads, so they cannot drift apart between releases.

What the database row adds on top of the code definition is the part an
operator owns: ``source_file_id`` (which human-designed Sheet to copy) and
``active``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.core.errors import NotFoundError, ValidationError
from meobot.core.logging import get_logger
from meobot.db.models.drive import SheetTemplate
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.drive.models import TemplateKind
from meobot.domain.drive.templates import (
    BUILTIN_TEMPLATES,
    TemplateSpec,
    template_for_kind,
)
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)


class SheetTemplateService:
    """List, seed and administer sheet templates.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
        settings: Supplies the configured template file ids.
    """

    def __init__(self, session: AsyncSession, audit: AuditService, settings: Settings) -> None:
        self._session = session
        self._audit = audit
        self._settings = settings

    # --- Queries ----------------------------------------------------------
    async def list_templates(self, *, active_only: bool = True) -> Sequence[SheetTemplate]:
        """Registered templates ordered by code."""
        statement = select(SheetTemplate).order_by(SheetTemplate.code, SheetTemplate.version)
        if active_only:
            statement = statement.where(SheetTemplate.active.is_(True))
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def get(self, template_id: uuid.UUID) -> SheetTemplate:
        """Fetch one template.

        Raises:
            NotFoundError: When the template does not exist.
        """
        template = await self._session.get(SheetTemplate, template_id)
        if template is None:
            raise NotFoundError(f"Không tìm thấy mẫu Sheet: {template_id}")
        return template

    async def find(self, code: str, version: int) -> SheetTemplate | None:
        """Look up a template by its code and version."""
        result = await self._session.execute(
            select(SheetTemplate).where(
                SheetTemplate.code == code, SheetTemplate.version == version
            )
        )
        return result.scalar_one_or_none()

    async def for_kind(self, kind: TemplateKind) -> SheetTemplate:
        """The active template MeoBot uses for ``kind``, seeding it if absent.

        Raises:
            ValidationError: When the template exists but is deactivated.
        """
        spec = template_for_kind(kind)
        template = await self.ensure_builtin(spec)
        if not template.active:
            raise ValidationError(
                f"Mẫu {template.code!r} đang bị tắt. Một OWNER/ADMIN cần bật lại "
                "trước khi tạo Sheet từ mẫu này."
            )
        return template

    # --- Seeding ----------------------------------------------------------
    async def ensure_builtin(self, spec: TemplateSpec) -> SheetTemplate:
        """Upsert one built-in template, refreshing the parts code owns.

        The structural fields (tabs, mappings, worksheet name, description) are
        overwritten from the code definition every time - they are not
        operator-editable, and letting a stale row win would silently create
        Sheets with the wrong mapping. ``active`` and ``source_file_id`` are
        left alone except that a newly configured template id is picked up.
        """
        existing = await self.find(spec.code, spec.version)
        configured_source = self._configured_source_id(spec.kind)

        if existing is None:
            template = SheetTemplate(
                code=spec.code,
                name=spec.name,
                description=spec.description,
                kind=spec.kind.value,
                version=spec.version,
                source_file_id=configured_source,
                active=True,
                default_worksheet_name=spec.default_worksheet_name,
                expected_tabs={"tabs": spec.expected_tabs},
                default_field_mapping=dict(spec.default_field_mapping),
                default_write_back_mapping=dict(spec.default_write_back_mapping),
            )
            self._session.add(template)
            await self._session.flush()
            logger.info("sheet_template_seeded", extra={"code": spec.code, "version": spec.version})
            return template

        existing.name = spec.name
        existing.description = spec.description
        existing.kind = spec.kind.value
        existing.default_worksheet_name = spec.default_worksheet_name
        existing.expected_tabs = {"tabs": spec.expected_tabs}
        existing.default_field_mapping = dict(spec.default_field_mapping)
        existing.default_write_back_mapping = dict(spec.default_write_back_mapping)
        # Configuration is authoritative for the source file: pointing
        # GOOGLE_*_TEMPLATE_ID at a new Sheet must take effect on restart.
        if configured_source and existing.source_file_id != configured_source:
            existing.source_file_id = configured_source
        await self._session.flush()
        return existing

    async def ensure_all_builtin(self) -> list[SheetTemplate]:
        """Seed every built-in template. Safe to call on every startup."""
        return [await self.ensure_builtin(spec) for spec in BUILTIN_TEMPLATES]

    def _configured_source_id(self, kind: TemplateKind) -> str | None:
        """The template file id configured for ``kind``, if any."""
        if kind is TemplateKind.WORK_MANAGEMENT:
            value = self._settings.google_work_sheet_template_id
        else:
            value = self._settings.google_script_sheet_template_id
        return value.strip() if value and value.strip() else None

    # --- Commands ---------------------------------------------------------
    async def set_active(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        template_id: uuid.UUID,
        active: bool,
    ) -> SheetTemplate:
        """Activate or deactivate a template. High risk: it changes what
        every future creation produces."""
        template = await self.get(template_id)
        template.active = active
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.TOOL_EXECUTED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_template",
            entity_id=str(template.id),
            after_data={"active": active, "code": template.code},
        )
        return template

    async def set_source_file(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        template_id: uuid.UUID,
        source_file_id: str | None,
    ) -> SheetTemplate:
        """Point a template at a different human-designed source Sheet."""
        template = await self.get(template_id)
        before = template.source_file_id
        template.source_file_id = (source_file_id or "").strip() or None
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.TOOL_EXECUTED.value,
            result=AuditResult.SUCCESS,
            entity_type="sheet_template",
            entity_id=str(template.id),
            before_data={"source_file_id": before},
            after_data={"source_file_id": template.source_file_id},
        )
        return template
