"""Tài chính: the 8:00 reminders (F8: "nhắc sau 24 giờ làm việc, kèm bản tóm tắt
gửi mỗi sáng"; F5: papers missing after payment are due within 7 days).

Every morning each person something waits on gets one summary (how many hồ sơ
chi, how many waiting longer than a day); each requester of a paid request
still missing papers gets one line per request (due date, or overdue). While
the module is "đang phát triển" only an OWNER is told.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.requests import FinanceNotifier, FinanceRequestService
from meobot.application.org.developing import developing_modules
from meobot.core.config import Settings
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.finance_spend import FinanceRequest, FinanceRequestStep
from meobot.db.models.user import User
from meobot.domain.finance.permissions import MODULE_FINANCE
from meobot.domain.finance.spend import DOCUMENT_LABELS, RequestStatus, StepStatus
from meobot.domain.identity.models import Role
from meobot.domain.notifications.models import NotificationEvent

#: "Quá 24 giờ" (calendar hours; Sunday is not counted).
LATE_HOURS = 24


async def send_reminders(session: AsyncSession, settings: Settings, today: date) -> dict[str, int]:
    hidden = MODULE_FINANCE in await developing_modules(session)
    owners = {
        user.id for user in await session.scalars(select(User).where(User.role == Role.OWNER))
    }

    def allowed(person: uuid.UUID) -> bool:
        return not hidden or person in owners

    notifier = FinanceNotifier(session, settings)
    now = utcnow()
    rows = (
        await session.execute(
            select(FinanceRequestStep, FinanceRequest)
            .join(FinanceRequest, FinanceRequest.id == FinanceRequestStep.request_id)
            .where(
                FinanceRequestStep.status == StepStatus.PENDING.value,
                FinanceRequest.status == RequestStatus.PENDING.value,
                or_(
                    FinanceRequestStep.approver_id.is_not(None),
                    FinanceRequestStep.standin_id.is_not(None),
                ),
            )
        )
    ).all()
    waiting: dict[uuid.UUID, list[tuple[FinanceRequestStep, FinanceRequest]]] = defaultdict(list)
    for step, request in rows:
        for person in {step.approver_id, step.standin_id} - {None}:
            waiting[person].append((step, request))
    told = 0
    for person, items in waiting.items():
        if not allowed(person):
            continue
        late = [
            step
            for step, _request in items
            if step.opened_at is not None and _hours(step.opened_at, now) >= LATE_HOURS
        ]
        body = f"Có {len(items)} hồ sơ chi chờ bạn" + (
            f", {len(late)} hồ sơ đã chờ quá {LATE_HOURS} giờ." if late else "."
        )
        await notifier.send(
            [person],
            event=NotificationEvent.FINANCE_REQUEST_REMINDER,
            heading="Hồ sơ chi chờ bạn",
            body=body,
            request=None,
            key=f"morning:{today.isoformat()}:{person}",
            path="/finance/requests?tab=awaiting",
        )
        for step in late:
            step.reminded_at = now
        told += 1

    papers = 0
    service = FinanceRequestService(session, settings, AuditService(session))
    paid = (
        await session.scalars(
            select(FinanceRequest).where(
                FinanceRequest.status == RequestStatus.PAID.value,
                FinanceRequest.docs_due_on.is_not(None),
                FinanceRequest.docs_due_on >= today - timedelta(days=30),
            )
        )
    ).all()
    for request in paid:
        missing = await service.missing(request)
        if not missing or not allowed(request.requester_id):
            continue
        due = request.docs_due_on
        assert due is not None
        state = (
            f"đã quá hạn bổ sung ({due:%d/%m/%Y})" if due < today else f"hạn bổ sung {due:%d/%m/%Y}"
        )
        await notifier.send(
            [request.requester_id],
            event=NotificationEvent.FINANCE_REQUEST_REMINDER,
            heading="Hồ sơ chi còn thiếu chứng từ",
            body=f"{request.code}: thiếu "
            + ", ".join(DOCUMENT_LABELS[kind] for kind in missing)
            + f" - {state}.",
            request=request,
            key=f"papers:{today.isoformat()}",
        )
        papers += 1
    await session.flush()
    return {"approvers": told, "papers": papers}


def _hours(start: datetime, end: datetime) -> float:
    return (ensure_utc(end) - ensure_utc(start)).total_seconds() / 3600


__all__ = ["LATE_HOURS", "send_reminders"]
