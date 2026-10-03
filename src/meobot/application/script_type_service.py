"""Script Type Registry service.

Invariants enforced here:

* A rubric must be valid (weights sum to 100, unique codes, no negatives).
* Versions are append-only: an existing version is never mutated, so past AI
  reviews stay reproducible.
* ``script_types.current_version`` always points at the newest version.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import ConflictError, NotFoundError
from meobot.core.logging import get_logger
from meobot.db.models.script_type import ScriptType, ScriptTypeVersion
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.script_types.rubric import ReviewRubric, validate_rubric_payload

logger = get_logger(__name__)


class ScriptTypeService:
    """Read and evolve the registry of content genres.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- Queries ----------------------------------------------------------
    async def list_script_types(self, *, active_only: bool = True) -> Sequence[ScriptType]:
        """List script types, newest configuration first by code."""
        statement = select(ScriptType).order_by(ScriptType.code)
        if active_only:
            statement = statement.where(ScriptType.active.is_(True))
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def get_by_code(self, code: str) -> ScriptType:
        """Fetch a script type by its code.

        Raises:
            NotFoundError: When no script type has that code.
        """
        result = await self._session.execute(select(ScriptType).where(ScriptType.code == code))
        script_type = result.scalar_one_or_none()
        if script_type is None:
            raise NotFoundError(f"Không tìm thấy thể loại kịch bản {code!r}")
        return script_type

    async def get_active_version(self, script_type_id: uuid.UUID) -> ScriptTypeVersion:
        """Return the version referenced by ``current_version``.

        Raises:
            NotFoundError: When the script type or its current version is missing.
        """
        script_type = await self._session.get(ScriptType, script_type_id)
        if script_type is None:
            raise NotFoundError(f"Không tìm thấy thể loại kịch bản {script_type_id}")
        result = await self._session.execute(
            select(ScriptTypeVersion).where(
                ScriptTypeVersion.script_type_id == script_type_id,
                ScriptTypeVersion.version == script_type.current_version,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise NotFoundError(
                f"Thể loại {script_type.code!r} không có version {script_type.current_version}"
            )
        return version

    async def get_rubric(self, script_type_id: uuid.UUID) -> ReviewRubric:
        """Return the validated rubric of the active version."""
        version = await self.get_active_version(script_type_id)
        return validate_rubric_payload(version.review_rubric)

    # --- Commands ---------------------------------------------------------
    async def create_script_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: str,
        name: str,
        review_rubric: dict[str, Any],
        description: str | None = None,
        configuration: dict[str, Any] | None = None,
        prompt_template: str | None = None,
    ) -> ScriptType:
        """Create a script type together with its version 1.

        Raises:
            ValidationError: When the rubric is invalid.
            ConflictError: When ``code`` is already taken.
        """
        rubric = validate_rubric_payload(review_rubric)

        existing = await self._session.execute(select(ScriptType).where(ScriptType.code == code))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"Thể loại kịch bản {code!r} đã tồn tại")

        script_type = ScriptType(
            code=code,
            name=name,
            description=description,
            active=True,
            current_version=1,
        )
        self._session.add(script_type)
        await self._session.flush()

        version = ScriptTypeVersion(
            script_type_id=script_type.id,
            version=1,
            configuration=configuration or {},
            review_rubric=rubric.model_dump(mode="json"),
            prompt_template=prompt_template,
        )
        self._session.add(version)
        await self._flush_unique(code=code)

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_TYPE_CREATED.value,
            result=AuditResult.SUCCESS,
            entity_type="script_type",
            entity_id=str(script_type.id),
            after_data={"code": code, "name": name, "version": 1},
        )
        return script_type

    async def add_version(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        script_type_id: uuid.UUID,
        review_rubric: dict[str, Any],
        configuration: dict[str, Any] | None = None,
        prompt_template: str | None = None,
        expected_version: int | None = None,
    ) -> ScriptTypeVersion:
        """Append a new immutable version and make it current.

        Args:
            expected_version: Optimistic-concurrency guard. When supplied and
                it does not match ``current_version``, the write is rejected
                instead of silently overwriting a concurrent change.

        Raises:
            ValidationError: When the rubric is invalid.
            NotFoundError: When the script type does not exist.
            ConflictError: On a stale ``expected_version`` or a duplicate version.
        """
        rubric = validate_rubric_payload(review_rubric)

        script_type = await self._session.get(ScriptType, script_type_id)
        if script_type is None:
            raise NotFoundError(f"Không tìm thấy thể loại kịch bản {script_type_id}")

        if expected_version is not None and expected_version != script_type.current_version:
            raise ConflictError(
                f"Version hiện tại là {script_type.current_version}, không phải {expected_version}",
                details={"current_version": script_type.current_version},
            )

        previous_version = script_type.current_version
        next_version = previous_version + 1

        version = ScriptTypeVersion(
            script_type_id=script_type.id,
            version=next_version,
            configuration=configuration or {},
            review_rubric=rubric.model_dump(mode="json"),
            prompt_template=prompt_template,
        )
        self._session.add(version)
        script_type.current_version = next_version
        await self._flush_unique(code=script_type.code, version=next_version)

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_TYPE_VERSION_ADDED.value,
            result=AuditResult.SUCCESS,
            entity_type="script_type",
            entity_id=str(script_type.id),
            before_data={"current_version": previous_version},
            after_data={"current_version": next_version},
        )
        return version

    async def deactivate(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        script_type_id: uuid.UUID,
    ) -> ScriptType:
        """Deactivate a script type without deleting its history.

        Raises:
            NotFoundError: When the script type does not exist.
        """
        script_type = await self._session.get(ScriptType, script_type_id)
        if script_type is None:
            raise NotFoundError(f"Không tìm thấy thể loại kịch bản {script_type_id}")

        was_active = script_type.active
        script_type.active = False
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_TYPE_DEACTIVATED.value,
            result=AuditResult.SUCCESS,
            entity_type="script_type",
            entity_id=str(script_type.id),
            before_data={"active": was_active},
            after_data={"active": False},
        )
        return script_type

    async def _flush_unique(self, *, code: str, version: int | None = None) -> None:
        """Flush and translate a unique-violation into a domain conflict."""
        try:
            await self._session.flush()
        except IntegrityError as exc:
            detail = f" version {version}" if version is not None else ""
            raise ConflictError(
                f"Thể loại {code!r}{detail} đã tồn tại",
                details={"code": code, "version": version},
            ) from exc
