"""Audit trail writing.

The service never opens or commits a transaction: it writes into the caller's
session so the audit row and the change it describes share one atomic unit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger, redact_mapping
from meobot.db.models.audit_log import AuditLog
from meobot.domain.audit.models import AuditEntry, AuditResult
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

#: Payload values longer than this are truncated before being stored.
MAX_FIELD_LENGTH = 4000


class AuditService:
    """Appends entries to ``audit_logs``.

    Args:
        session: The unit of work the audited change is happening in.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, entry: AuditEntry) -> AuditLog:
        """Persist one audit entry (redacted, truncated) and return the row."""
        row = AuditLog(
            request_id=entry.request_id,
            actor_user_id=entry.actor_user_id,
            actor_telegram_id=entry.actor_telegram_id,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            before_data=self._prepare(entry.before_data),
            after_data=self._prepare(entry.after_data),
            result=entry.result,
            error_message=entry.error_message,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "audit_recorded",
            extra={
                "action": entry.action,
                "result": entry.result.value,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
            },
        )
        return row

    async def record_action(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        action: str,
        result: AuditResult,
        entity_type: str | None = None,
        entity_id: str | None = None,
        before_data: dict[str, object] | None = None,
        after_data: dict[str, object] | None = None,
        error_message: str | None = None,
    ) -> AuditLog:
        """Convenience wrapper that builds the entry from an :class:`Actor`."""
        return await self.record(
            AuditEntry(
                request_id=request_id,
                actor_user_id=actor.user_id,
                actor_telegram_id=actor.telegram_user_id,
                action=action,
                result=result,
                entity_type=entity_type,
                entity_id=entity_id,
                before_data=dict(before_data) if before_data is not None else None,
                after_data=dict(after_data) if after_data is not None else None,
                error_message=error_message,
            )
        )

    @staticmethod
    def _prepare(payload: dict[str, object] | None) -> dict[str, object] | None:
        """Redact secrets and truncate oversized values before persisting."""
        if payload is None:
            return None
        redacted = redact_mapping(dict(payload))
        return {
            key: (value[:MAX_FIELD_LENGTH] if isinstance(value, str) else value)
            for key, value in redacted.items()
        }
