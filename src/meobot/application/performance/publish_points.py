"""+10 for whoever records a PR publication with a link (0085).

A ``pr_publications`` row that is PUBLISHED, has a link and names its
publisher is worth ``PUBLISH_POINTS`` to that person, in the month it went
out. Kept by a global ``after_flush`` hook - like the task sync - so the PR
code that writes publications is not touched: a new row writes its points, an
edit rewrites them (link removed, taken down, publisher changed), a deleted
row loses them. Core statements only, inside the flush's transaction.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, event, insert, select
from sqlalchemy.orm import Session, UOWTransaction

from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.work_points import WORK_POINTS, WorkPoint
from meobot.domain.orders.design_codes import items_to_json
from meobot.domain.org.models import DepartmentFunction
from meobot.domain.performance.work_points import (
    PHASE_PUBLISH,
    WorkPointSource,
    publish_item,
)
from meobot.domain.pr.reporting import PrPublicationStatus

_TABLE_KEY = "meobot_work_points_present"
_RECHECK_SECONDS = 60.0


def source_key(publication_id: uuid.UUID) -> str:
    return f"pr_publication:{publication_id}"


def earns(publication: PrPublication) -> bool:
    return (
        publication.status is PrPublicationStatus.PUBLISHED
        and bool((publication.url or "").strip())
        and publication.publisher_user_id is not None
        and publication.published_at is not None
    )


def _table_ready(session: Session) -> bool:
    connection = session.connection()
    cached = connection.info.get(_TABLE_KEY)
    now = time.monotonic()
    if cached is not None and now - cached[1] < _RECHECK_SECONDS:
        return bool(cached[0])
    present = connection.dialect.has_table(connection, WORK_POINTS)
    connection.info[_TABLE_KEY] = (present, now)
    return bool(present)


def _department(session: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    found = session.execute(
        select(DepartmentMember.department_id)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(
            DepartmentMember.user_id == user_id,
            DepartmentMember.left_at.is_(None),
            Department.active.is_(True),
        )
        .order_by(
            case((Department.function == DepartmentFunction.PR, 0), else_=1),
            Department.sort_order,
            Department.code,
        )
        .limit(1)
    ).scalar_one_or_none()
    return found


def _write(session: Session, publication: PrPublication) -> None:
    key = source_key(publication.id)
    session.execute(delete(WorkPoint).where(WorkPoint.source_key == key))
    if not earns(publication):
        return
    assert publication.publisher_user_id is not None
    content = session.execute(
        select(PrContentItem.code, PrContentItem.title).where(
            PrContentItem.id == publication.content_id
        )
    ).first()
    item = publish_item()
    published_at: datetime = ensure_utc(publication.published_at)
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source": WorkPointSource.PR_PUBLISH.value,
        "source_key": key,
        "user_id": publication.publisher_user_id,
        "department_id": _department(session, publication.publisher_user_id),
        "phase": PHASE_PUBLISH,
        "items": items_to_json([item]),
        "points": item.points,
        "earned_at": published_at,
        "ref_code": content[0] if content else None,
        "ref_title": content[1] if content else None,
        "ref_link": f"/tasks/{content[0]}" if content else None,
        "created_at": utcnow(),
    }
    session.execute(insert(WorkPoint).values(**values))


def _after_flush(session: Session, _context: UOWTransaction) -> None:
    touched = [
        obj
        for obj in (*session.new, *session.dirty, *session.deleted)
        if isinstance(obj, PrPublication)
    ]
    if not touched or not _table_ready(session):
        return
    for obj in touched:
        if obj in session.deleted:
            session.execute(delete(WorkPoint).where(WorkPoint.source_key == source_key(obj.id)))
        else:
            _write(session, obj)


def install_publish_points() -> None:
    """Register the hook on every session in the process. Idempotent."""
    if not event.contains(Session, "after_flush", _after_flush):
        event.listen(Session, "after_flush", _after_flush)


__all__ = ["earns", "install_publish_points", "source_key"]
