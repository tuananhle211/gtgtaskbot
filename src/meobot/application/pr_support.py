"""Plumbing the PR application services share: row locks and domain events.

Two things every PR command needs, written once so they cannot drift apart
between eight services.

Row locking
-----------

:func:`lock_row` is ``SELECT ... FOR UPDATE`` on PostgreSQL and a plain ``get``
everywhere else, following the dialect check
:meth:`~meobot.application.reminder_service.ReminderService.due_batch` already
uses. The reason it is guarded rather than unconditional: the offline test
fixture is SQLite, which has no ``FOR UPDATE`` clause and serialises writers
anyway, so the same code is correct on both - and SQLAlchemy would otherwise
emit SQL SQLite silently ignores, which is worse than not emitting it, because
it reads as protection that is not there.

**No ``skip_locked``, unlike the outbox.** A worker claiming outbound messages
wants to step over a row somebody else is handling. A workflow command wants
the opposite: if another transaction is moving this content, this one must wait
and then re-read the stage it is validating against. Skipping would let two
concurrent commands each validate against a stage that was true when they
started and false when they wrote.

Domain events
-------------

:func:`record_pr_event` appends to the existing audit trail through
:class:`~meobot.application.audit_service.AuditService`, on the caller's
session, so the event and the change it describes commit together or not at
all.

**Why not the transactional outbox.** MeoBot's outbox
(``outbound_messages``) is a *notification* outbox: every row requires a
``telegram_chat_id``, a ``template_key`` and a ``template_version``, and
:class:`~meobot.domain.notifications.models.NotificationEvent` is a closed set
of things that get delivered to somebody. Step 1C sends nothing to anybody, so
there is no destination to name and no template to render. Enqueuing rows there
with invented destinations would either produce messages nobody asked for or
sit forever undeliverable. The audit trail is the mechanism this repository
already has for "this happened, durably, in the same transaction", and
``AuditAction`` already spells events as ``entity.verb`` codes - which is
exactly the shape the PR event names take. When PR notifications are specified,
they enqueue from the same transaction alongside these rows; nothing here has
to change for that to work.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.db.base import Base
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor


def supports_row_locks(session: AsyncSession) -> bool:
    """True when the bound dialect implements ``SELECT ... FOR UPDATE``."""
    bind = session.bind
    return bind is not None and bind.dialect.name == "postgresql"


async def lock_row[ModelT: Base](
    session: AsyncSession, model: type[ModelT], entity_id: uuid.UUID
) -> ModelT | None:
    """Load one row, holding it against concurrent writers where possible.

    Returns ``None`` when the row does not exist - the caller decides which
    not-found error that is, because "no such content" and "no such task" are
    different sentences to whoever asked.
    """
    if not supports_row_locks(session):
        return await session.get(model, entity_id)
    statement = select(model).where(model.id == entity_id).with_for_update()  # type: ignore[attr-defined]
    result = await session.execute(statement)
    return result.scalars().one_or_none()


async def record_pr_event(
    audit: AuditService,
    *,
    request_id: uuid.UUID,
    actor: Actor,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append one PR domain event to the audit trail.

    Always :attr:`~meobot.domain.audit.models.AuditResult.SUCCESS`: a command
    that failed raised before reaching here and its transaction is rolled back,
    so there is no half-event to record. Failures are logged by the caller and
    surfaced as typed errors, not written as audit rows describing changes that
    did not happen.

    ``entity_id`` is ``None`` only for an event that genuinely names no single
    row - M2.5's taxonomy bootstrap writes one event for the set it created,
    and pointing it at an arbitrary member would make the trail claim something
    it does not mean. The column is nullable and the pairing with
    ``entity_type`` still narrows a search to the right table.
    """
    await audit.record_action(
        request_id=request_id,
        actor=actor,
        action=action.value,
        result=AuditResult.SUCCESS,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before_data=before,
        after_data=after,
    )


__all__: list[str] = ["lock_row", "record_pr_event", "supports_row_locks"]
