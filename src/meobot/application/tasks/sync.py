"""Keep ``tasks`` in step with its two sources, from inside the flush.

A ``before_flush`` listener on :class:`sqlalchemy.orm.Session` - the class, so
every session in the process is covered: the web request, the Telegram bot,
the Celery worker and the test fixtures alike - looks at the
:class:`~meobot.db.models.pr.PrContentItem` and
:class:`~meobot.db.models.order.Order` rows about to be written and writes
their :class:`~meobot.db.models.task.Task` in the same flush. No PR or Ads
service knows the table exists, which is the point: the PR workflow is
frozen, and a projection that every write path had to remember would be
wrong the first time one forgot.

What is copied
--------------

Code, title, kind, owner, the priority flag, the source's own stage and the
phase it maps to (:data:`~meobot.domain.board.models.PR_STAGE_PHASE`,
:data:`~meobot.domain.board.models.ADS_STAGE_PHASE`). ``stage_since`` moves
when the stage does; ``finished_at`` is stamped when the phase becomes
``DONE`` or ``CANCELLED`` and cleared when an undo takes it back out.

Cheap by construction
---------------------

A dirty source whose copied columns did not change (a new version number,
a producer claimed) costs nothing: no query, no write. A changed one costs
one ``SELECT`` by a unique key and an ``UPDATE``.

Deletes
-------

PR deletes content with a bulk ``DELETE`` statement, which never reaches a
flush. ``do_orm_execute`` sees it instead and removes the task first, with
the same ``WHERE``; the foreign key's ``ON DELETE CASCADE`` is the backstop
for raw SQL. ``session.delete(item)`` is handled in the flush.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, event, inspect, select
from sqlalchemy.orm import InstanceState, ORMExecuteState, Session, UOWTransaction

from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import Order
from meobot.db.models.pr import PrContentItem
from meobot.db.models.task import SOURCE_ORDER, SOURCE_PR_CONTENT, TASKS, Task
from meobot.domain.board.models import ADS_STAGE_PHASE, PR_STAGE_PHASE, Phase
from meobot.domain.orders.models import OrderStage
from meobot.domain.pr.models import PrPriority, PrWorkflowStage
from meobot.domain.units.models import UnitCode, unit_seed_id

#: The PR priorities the board calls "Ưu tiên".
PRIORITY_LEVELS: frozenset[PrPriority] = frozenset(
    {PrPriority.HIGH, PrPriority.URGENT, PrPriority.CRITICAL}
)
#: Phases after which the work is over.
FINISHED_PHASES: frozenset[Phase] = frozenset({Phase.DONE, Phase.CANCELLED})

_PR_FIELDS = ("code", "title", "content_type", "owner_user_id", "priority", "workflow_stage")
_ORDER_FIELDS = (
    "code",
    "title",
    "video_type",
    "owner_user_id",
    "is_priority",
    "stage",
    "completed_at",
)


# --- the picture of one source ---------------------------------------------------


def _pr_values(item: PrContentItem) -> dict[str, Any]:
    stage = item.workflow_stage or PrWorkflowStage.IDEA
    priority = item.priority or PrPriority.NORMAL
    return {
        "unit_id": unit_seed_id(UnitCode.PR),
        "source_type": SOURCE_PR_CONTENT,
        "code": item.code,
        "title": item.title,
        "kind": item.content_type.value if item.content_type is not None else "",
        "owner_user_id": item.owner_user_id,
        "is_priority": priority in PRIORITY_LEVELS,
        "stage": stage.value,
        "phase": PR_STAGE_PHASE[stage].value,
    }


def _order_values(order: Order) -> dict[str, Any]:
    stage = order.stage or OrderStage.ORDER_PENDING
    return {
        "unit_id": order.unit_id,
        "source_type": SOURCE_ORDER,
        "code": order.code,
        "title": order.title,
        "kind": order.video_type.value,
        "owner_user_id": order.owner_user_id,
        "is_priority": bool(order.is_priority),
        "stage": stage.value,
        "phase": ADS_STAGE_PHASE[stage].value,
    }


def _finished_at(source: object, now: datetime) -> datetime:
    completed = getattr(source, "completed_at", None)
    return ensure_utc(completed) if isinstance(completed, datetime) else now


def _changed(source: object, fields: Iterable[str]) -> bool:
    state: InstanceState[Any] = inspect(source)  # type: ignore[assignment]
    return any(state.attrs[name].history.has_changes() for name in fields)


def _apply(task: Task, values: dict[str, Any], source: object, now: datetime) -> None:
    """Copy ``values`` onto ``task``, moving the two clocks only on a change."""
    stage_moved = task.stage != values["stage"]
    for key, value in values.items():
        if getattr(task, key) != value:
            setattr(task, key, value)
    if stage_moved:
        task.stage_since = now
    finished = Phase(values["phase"]) in FINISHED_PHASES
    if finished and task.finished_at is None:
        task.finished_at = _finished_at(source, now)
    elif not finished and task.finished_at is not None:
        task.finished_at = None
    task.updated_at = now


def _new_task(source: PrContentItem | Order, values: dict[str, Any], now: datetime) -> Task:
    if getattr(source, "id", None) is None:
        # The primary key's default runs at INSERT; the task needs it now.
        source.id = uuid.uuid4()
    if isinstance(source, Order):
        submitted = source.submitted_at
        since = ensure_utc(submitted) if submitted is not None else now
        task = Task(order_id=source.id, **values)
        task.order = source
    else:
        since = now
        task = Task(pr_content_id=source.id, **values)
        task.pr_content = source
    task.stage_since = since
    # An order's clock starts at its submission, as on the board.
    task.created_at = since
    task.updated_at = now
    task.finished_at = (
        _finished_at(source, now) if Phase(values["phase"]) in FINISHED_PHASES else None
    )
    return task


# --- the hooks ---------------------------------------------------------------------


#: Where a connection remembers whether ``tasks`` exists, and for how long.
_TABLE_KEY = "meobot.tasks_table"
_TABLE_RECHECK_SECONDS = 60.0
_SOURCES = (PrContentItem, Order)


def _table_ready(session: Session) -> bool:
    """Whether this database has ``tasks`` yet.

    Deploys do not run Alembic, so new code can meet a database still at
    ``0042`` - and so can a migration test seeding an old revision through the
    ORM. Until the table exists the hook stands aside; the ``0043`` backfill
    and the self-healing read in the detail service cover what it skipped.
    Remembered on the pooled connection and re-asked once a minute, so the
    cost is one catalog lookup per connection per minute.
    """
    connection = session.connection()
    cached = connection.info.get(_TABLE_KEY)
    now = time.monotonic()
    if cached is not None and now - cached[1] < _TABLE_RECHECK_SECONDS:
        return bool(cached[0])
    present = connection.dialect.has_table(connection, TASKS)
    connection.info[_TABLE_KEY] = (present, now)
    return bool(present)


def _touches_sources(session: Session) -> bool:
    return any(
        isinstance(obj, _SOURCES)
        for collection in (session.new, session.dirty, session.deleted)
        for obj in collection
    )


def _before_flush(session: Session, _context: UOWTransaction, _instances: object) -> None:
    if not _touches_sources(session) or not _table_ready(session):
        return
    now = utcnow()
    for obj in list(session.new):
        if isinstance(obj, PrContentItem):
            _add_new(session, obj, _pr_values(obj), now)
        elif isinstance(obj, Order):
            _add_new(session, obj, _order_values(obj), now)
    for obj in list(session.dirty):
        if isinstance(obj, PrContentItem):
            if _changed(obj, _PR_FIELDS):
                _sync_existing(session, obj, Task.pr_content_id, _pr_values(obj), now)
        elif isinstance(obj, Order) and _changed(obj, _ORDER_FIELDS):
            _sync_existing(session, obj, Task.order_id, _order_values(obj), now)
    for obj in list(session.deleted):
        if isinstance(obj, PrContentItem):
            session.execute(delete(Task).where(Task.pr_content_id == obj.id))
        elif isinstance(obj, Order):
            session.execute(delete(Task).where(Task.order_id == obj.id))


def _complete(values: dict[str, Any]) -> bool:
    """Whether the source carries everything a task row requires yet."""
    return all(values[key] is not None for key in ("code", "title", "owner_user_id", "unit_id"))


def _add_new(
    session: Session, source: PrContentItem | Order, values: dict[str, Any], now: datetime
) -> None:
    if _complete(values):
        session.add(_new_task(source, values, now))


def build_task(source: PrContentItem | Order, now: datetime) -> Task | None:
    """A new, unsaved task for ``source``, or ``None`` while it is incomplete."""
    values = _pr_values(source) if isinstance(source, PrContentItem) else _order_values(source)
    return _new_task(source, values, now) if _complete(values) else None


def _sync_existing(
    session: Session,
    source: PrContentItem | Order,
    key: Any,
    values: dict[str, Any],
    now: datetime,
) -> None:
    task = session.execute(select(Task).where(key == source.id)).scalar_one_or_none()
    if task is None:
        # A row written before the hook existed and missed by the backfill
        # (raw SQL), or one that was incomplete when first flushed. Give it its
        # task now rather than never.
        _add_new(session, source, values, now)
        return
    _apply(task, values, source, now)


def _on_orm_execute(state: ORMExecuteState) -> None:
    """Remove the tasks of rows a bulk ``DELETE`` is about to remove."""
    if not state.is_delete:
        return
    mapper = state.bind_mapper
    target = None if mapper is None else mapper.class_
    if target is PrContentItem:
        column, source_id = Task.pr_content_id, PrContentItem.id
    elif target is Order:
        column, source_id = Task.order_id, Order.id
    else:
        return
    if not _table_ready(state.session):
        return
    statement = state.statement
    ids = select(source_id)
    where = getattr(statement, "whereclause", None)
    if where is not None:
        ids = ids.where(where)
    state.session.execute(delete(Task).where(column.in_(ids)))


def install_task_sync() -> None:
    """Register both hooks on every session in the process. Idempotent."""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
    if not event.contains(Session, "do_orm_execute", _on_orm_execute):
        event.listen(Session, "do_orm_execute", _on_orm_execute)


__all__ = ["FINISHED_PHASES", "PRIORITY_LEVELS", "build_task", "install_task_sync"]
