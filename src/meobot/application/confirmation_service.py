"""Two-step confirmation for high-risk actions.

Flow: the policy engine says ``requires_confirmation`` -> the plan is stored
with a short-lived token -> the human replies ``/confirm <token>`` -> the plan
is re-validated by the policy engine (with ``confirmed=True``) and executed.

The stored plan is the source of truth. The confirming message cannot change
any argument, so the human confirms exactly what was shown to them.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.core.errors import ConfirmationExpiredError, NotFoundError
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utc_in, utcnow
from meobot.db.models.confirmation_request import ConfirmationRequestRow
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.policy.models import ActionPlan, ConfirmationState

logger = get_logger(__name__)

#: Token length in bytes before hex-encoding (8 bytes -> 16 chars, easy to retype).
TOKEN_BYTES = 8


class ConfirmationService:
    """Creates, redeems and expires confirmation requests.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Supplies the TTL.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit

    async def create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        plan: ActionPlan,
    ) -> ConfirmationRequestRow:
        """Store ``plan`` and return the pending request with its token.

        When the plan carries an ``idempotency_key`` that already has a pending
        request, that request is returned instead of a second one - re-asking
        must not create two ways to execute the same action.
        """
        if plan.idempotency_key:
            existing = await self._find_pending_by_key(plan.idempotency_key)
            if existing is not None:
                return existing

        row = ConfirmationRequestRow(
            user_id=actor.user_id,
            telegram_user_id=actor.telegram_user_id,
            action_plan=plan.model_dump(mode="json"),
            confirmation_token=secrets.token_hex(TOKEN_BYTES),
            idempotency_key=plan.idempotency_key,
            status=ConfirmationState.PENDING,
            expires_at=utc_in(self._settings.confirmation_ttl_seconds),
        )
        self._session.add(row)
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.CONFIRMATION_REQUESTED.value,
            result=AuditResult.PENDING_CONFIRMATION,
            entity_type="confirmation_request",
            entity_id=str(row.id),
            after_data={"tool_name": plan.tool_name, "intent": plan.intent},
        )
        logger.info(
            "confirmation_created",
            extra={"tool_name": plan.tool_name, "confirmation_id": str(row.id)},
        )
        return row

    async def redeem(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        token: str,
        now: datetime | None = None,
    ) -> tuple[ConfirmationRequestRow, ActionPlan]:
        """Consume a token and return the stored plan.

        Raises:
            NotFoundError: Unknown token, or a token belonging to someone else.
            ConfirmationExpiredError: Token expired or already used.
        """
        moment = now or utcnow()
        row = await self._get_by_token(token)

        if not self._belongs_to(row, actor):
            # Deliberately the same error as 'unknown token': do not confirm to
            # a stranger that somebody else's token exists.
            raise NotFoundError("Mã xác nhận không hợp lệ.")

        if row.status is not ConfirmationState.PENDING:
            raise ConfirmationExpiredError(
                "Mã xác nhận đã được sử dụng hoặc đã bị huỷ.",
                details={"status": row.status.value},
            )

        if moment >= self._aware(row.expires_at):
            row.status = ConfirmationState.EXPIRED
            await self._session.flush()
            raise ConfirmationExpiredError("Mã xác nhận đã hết hạn. Hãy thực hiện lại yêu cầu.")

        row.status = ConfirmationState.CONFIRMED
        row.confirmed_at = moment
        await self._session.flush()

        plan = ActionPlan.model_validate(row.action_plan)
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.CONFIRMATION_CONFIRMED.value,
            result=AuditResult.SUCCESS,
            entity_type="confirmation_request",
            entity_id=str(row.id),
            after_data={"tool_name": plan.tool_name},
        )
        return row, plan

    async def reject(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        token: str,
    ) -> ConfirmationRequestRow:
        """Mark a pending request as rejected.

        Raises:
            NotFoundError: Unknown token or not the requester's token.
        """
        row = await self._get_by_token(token)
        if not self._belongs_to(row, actor):
            raise NotFoundError("Mã xác nhận không hợp lệ.")
        row.status = ConfirmationState.REJECTED
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.CONFIRMATION_REJECTED.value,
            result=AuditResult.SUCCESS,
            entity_type="confirmation_request",
            entity_id=str(row.id),
        )
        return row

    async def expire_stale(self, *, now: datetime | None = None) -> int:
        """Mark every overdue pending request as expired. Returns the count."""
        moment = now or utcnow()
        result = await self._session.execute(
            select(ConfirmationRequestRow).where(
                ConfirmationRequestRow.status == ConfirmationState.PENDING,
                ConfirmationRequestRow.expires_at <= moment,
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = ConfirmationState.EXPIRED
        if rows:
            await self._session.flush()
        return len(rows)

    async def _get_by_token(self, token: str) -> ConfirmationRequestRow:
        result = await self._session.execute(
            select(ConfirmationRequestRow).where(ConfirmationRequestRow.confirmation_token == token)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("Mã xác nhận không hợp lệ.")
        return row

    async def _find_pending_by_key(self, idempotency_key: str) -> ConfirmationRequestRow | None:
        result = await self._session.execute(
            select(ConfirmationRequestRow).where(
                ConfirmationRequestRow.idempotency_key == idempotency_key,
                ConfirmationRequestRow.status == ConfirmationState.PENDING,
            )
        )
        return result.scalars().first()

    @staticmethod
    def _belongs_to(row: ConfirmationRequestRow, actor: Actor) -> bool:
        """A token may only be redeemed by the actor who requested it."""
        if row.user_id is not None and actor.user_id is not None:
            return row.user_id == actor.user_id
        if row.telegram_user_id is not None and actor.telegram_user_id is not None:
            return row.telegram_user_id == actor.telegram_user_id
        return False

    @staticmethod
    def _aware(value: datetime) -> datetime:
        """Guard against drivers that hand back naive datetimes."""
        return ensure_utc(value)
