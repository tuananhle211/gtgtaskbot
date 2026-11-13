"""Tài chính > Công nợ (đợt 2; user 12/11 R10: "Có cảnh báo, và có thể cài đặt
được các mốc").

Every stored CRM order still owing (``crm_orders.the_rest`` > 0; the 5h run
re-reads the year's unpaid orders, so an old one paid off drops to 0), aged
from its creation day, put in the largest milestone it passed ("Cấu hình >
Công nợ", default 30 / 45 / 60 days).

After the 5h run, :class:`FinanceDebtNotifier` sends the day's summary to the
people set there (web inbox + Telegram), once a day; while the module is still
"đang phát triển" only an OWNER among them is told.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.finance.settings import FinanceSettingsService, fold
from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.org.developing import developing_modules
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.finance import CrmBranchRecord, CrmOrderRecord, CrmStaffRecord
from meobot.db.models.user import User
from meobot.domain.finance.crm import debt_bucket, whole
from meobot.domain.finance.permissions import MODULE_FINANCE
from meobot.domain.identity.models import Role
from meobot.domain.notifications.models import NotificationEvent

logger = get_logger(__name__)
TEMPLATE_KEY = "finance.debts"
TARGET_KIND = "finance_debts"


@dataclass(frozen=True, slots=True)
class DebtLine:
    order_id: int
    code: str | None
    created_on: date
    age: int
    bucket: int | None
    customer_code: str | None
    customer_name: str | None
    branch: str | None
    owner: str | None
    total: Decimal
    owed: Decimal


@dataclass(slots=True)
class DebtBucket:
    milestone: int | None
    orders: int = 0
    owed: Decimal = Decimal(0)


@dataclass(slots=True)
class DebtReport:
    today: date
    milestones: tuple[int, ...]
    lines: list[DebtLine]
    buckets: list[DebtBucket] = field(default_factory=list)

    @property
    def owed(self) -> Decimal:
        return sum((bucket.owed for bucket in self.buckets), Decimal(0))


class FinanceDebtService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def report(
        self, *, today: date, min_bucket: int | None = None, q: str = ""
    ) -> DebtReport:
        milestones, _ = await FinanceSettingsService(
            self._session, AuditService(self._session)
        ).debt_settings()
        orders = list(
            (
                await self._session.scalars(
                    select(CrmOrderRecord)
                    .where(CrmOrderRecord.the_rest > 0)
                    .order_by(CrmOrderRecord.created_on, CrmOrderRecord.id)
                )
            ).all()
        )
        branches = {
            row.id: row.name for row in await self._session.scalars(select(CrmBranchRecord))
        }
        owners = {
            row.id: row.name
            for row in await self._session.scalars(
                select(CrmStaffRecord).where(
                    CrmStaffRecord.id.in_({o.owner_id for o in orders if o.owner_id} or {-1})
                )
            )
        }
        buckets = {mark: DebtBucket(mark) for mark in (None, *milestones)}
        text = fold(q)
        lines: list[DebtLine] = []
        for order in orders:
            age = (today - order.created_on).days
            bucket = debt_bucket(age, milestones)
            slot = buckets[bucket]
            slot.orders += 1
            slot.owed += Decimal(order.the_rest)
            if min_bucket is not None and (bucket is None or bucket < min_bucket):
                continue
            if text and not any(
                text in fold(value or "")
                for value in (order.code, order.customer_code, order.customer_name)
            ):
                continue
            lines.append(
                DebtLine(
                    order_id=order.id,
                    code=order.code,
                    created_on=order.created_on,
                    age=age,
                    bucket=bucket,
                    customer_code=order.customer_code,
                    customer_name=order.customer_name,
                    branch=branches.get(order.branch_id) if order.branch_id is not None else None,
                    owner=owners.get(order.owner_id) if order.owner_id is not None else None,
                    total=Decimal(order.all_total),
                    owed=Decimal(order.the_rest),
                )
            )
        lines.sort(key=lambda line: (-line.age, line.code or ""))
        return DebtReport(
            today=today,
            milestones=milestones,
            lines=lines,
            buckets=list(buckets.values()),
        )


def _money(value: Decimal) -> str:
    return f"{int(whole(value)):,}".replace(",", ".") + " ₫"


def digest_text(report: DebtReport) -> str | None:
    """The day's summary, or ``None`` when nothing passed the first milestone."""
    late = [bucket for bucket in report.buckets if bucket.milestone is not None and bucket.orders]
    if not late:
        return None
    total = sum(bucket.orders for bucket in report.buckets)
    parts = [
        f"quá {bucket.milestone} ngày: {bucket.orders} đơn ({_money(bucket.owed)})"
        for bucket in sorted(late, key=lambda item: -(item.milestone or 0))
    ]
    return f"Còn nợ {total} đơn, tổng {_money(report.owed)}. " + "; ".join(parts) + "."


class FinanceDebtNotifier:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def send(self, today: date) -> int:
        """Tell the people set; returns how many were told (0 if nothing late)."""
        _, recipients = await FinanceSettingsService(
            self._session, AuditService(self._session)
        ).debt_settings()
        if not recipients:
            return 0
        report = await FinanceDebtService(self._session).report(today=today)
        body = digest_text(report)
        if body is None:
            return 0
        hidden = MODULE_FINANCE in await developing_modules(self._session)
        users = {
            user.id: user
            for user in await self._session.scalars(
                select(User).where(User.id.in_(recipients), User.deleted_at.is_(None))
            )
        }
        inbox = UserNotificationService(self._session)
        router = NotificationRouter(self._session, self._settings)
        resolver = RecipientResolver(self._session, self._settings)
        link = f"{self._settings.web_base_url.rstrip('/')}/finance/debts"
        title = f"Công nợ ngày {today:%d/%m/%Y}"
        told = 0
        for recipient in recipients:
            user = users.get(recipient)
            if user is None or (hidden and user.role is not Role.OWNER):
                continue
            key = f"finance_debts:{today.isoformat()}:{recipient}"
            await inbox.record(
                recipient_user_id=recipient,
                event=NotificationEvent.FINANCE_DEBTS_DIGEST,
                title=title,
                body=body,
                idempotency_key=key,
                target_kind=TARGET_KIND,
                target_id=None,
            )
            told += 1
            resolved = await resolver.private_destination(user_id=recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                logger.info(
                    "finance_debts_recipient_unreachable",
                    extra={"user_id": str(recipient), "reason": resolved.message},
                )
                continue
            await router.route(
                [
                    RouteRequest(
                        event_type=NotificationEvent.FINANCE_DEBTS_DIGEST,
                        template_key=TEMPLATE_KEY,
                        payload={
                            "heading": "CÔNG NỢ QUÁ HẠN",
                            "title": title,
                            "code": "Tài chính",
                            "body": body,
                            "link": link,
                        },
                        idempotency_key=key,
                        aggregate_type=TARGET_KIND,
                        aggregate_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
                        recipient_user_id=recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=None,
                        business_summary=title,
                    )
                ]
            )
        return told


__all__ = [
    "DebtBucket",
    "DebtLine",
    "DebtReport",
    "FinanceDebtNotifier",
    "FinanceDebtService",
    "digest_text",
]
