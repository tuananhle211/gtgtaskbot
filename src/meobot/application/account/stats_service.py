"""Per-person monthly statistics for the account screen. Read-only.

One method, :meth:`AccountStatsService.stats_for`, answers for any number of
people at once: every figure is one grouped aggregate query over the whole set
of ids (``GROUP BY user``), never a query per person. The member list of
forty people costs the same eight queries as the account screen of one.

The month is a **Vietnamese calendar month** (``Settings.timezone``): the
columns are UTC instants, and October begins at 2026-09-30T17:00Z. Comparing
against midnight UTC would move the first seven hours of every month into the
previous one - the same trap :class:`~meobot.application.pr_work_period_service.PrWorkPeriodService`
documents.

What each figure counts
-----------------------

Ads (``orders`` / ``order_nodes`` / ``order_events``):

* ``points`` / ``nodes_done`` - production nodes (Biên kịch, Design, Dựng) the
  person is assignee of, ``HOAN_THANH``, approved in the month; points are the
  order's ``video_kind_points`` snapshot (an order without a kind counts 0);
* ``nodes_in_progress`` - nodes assigned to them now in ``DANG_LAM`` /
  ``DANG_SUA`` / ``CHO_DUYET``, any month, on an order not cancelled;
* ``revisions`` - returns (``NODE_RETURNED`` / ``VIDEO_RETURNED`` /
  ``FINAL_RETURNED``) in the month on nodes they are assignee of;
* ``orders_created`` / ``orders_completed`` - orders they ordered, submitted /
  completed in the month;
* ``first_pass_rate`` ("Không bị trả") - of ``nodes_done``, the share
  approved with no return (``revision_count = 0``); ``None`` when there are none;
* ``on_time_rate`` ("Đúng hạn", 0053) - of ``nodes_done`` that had a
  deadline, the share finished by it (``deadline_met``); ``late_count`` the
  others ("Số lần trễ hạn");
* ``tokens_used`` / ``tokens_budget`` / ``effort_rate`` (0053) - tokens taken
  off their days in the month (``order_token_ledger``) against their daily
  budget on the month's working days; ORD function members only;
* ``performance_score`` (0053) - 0..100: the unit's ``perf_weights`` over
  output (``nodes_done`` against ``output_target``, else the ban's top
  performer of the month, capped at 1), ``on_time_rate`` and
  ``first_pass_rate``; a part with no data is left out and the weights
  renormalised. ``None`` with nothing done.

PR: content items they own created in the month, production hand-ins they
submitted, approval decisions they made. Both units: KPI ledger rows
(``pr_work_results``) ``COUNTED`` for them in the month.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.orders.effort_service import daily_budget, working_share
from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderEvent, OrderNode, OrderTokenLedger
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.account.errors import AccountValidationError
from meobot.domain.orders.models import (
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
)
from meobot.domain.pr.work import PrWorkCountStatus
from meobot.domain.units.models import FUNCTION_ROLES, UnitCode, UnitSettings

#: The nodes that earn points. ``GAN_LINK`` is a hand-over, not production.
PRODUCTION_NODES: tuple[OrderNodeType, ...] = (
    OrderNodeType.BIEN_TAP,
    OrderNodeType.THIET_KE,
    OrderNodeType.DUNG,
)
IN_PROGRESS: tuple[OrderNodeStatus, ...] = (
    OrderNodeStatus.DANG_LAM,
    OrderNodeStatus.DANG_SUA,
    OrderNodeStatus.CHO_DUYET,
)
RETURN_EVENTS: tuple[OrderEventKind, ...] = (
    OrderEventKind.NODE_RETURNED,
    OrderEventKind.VIDEO_RETURNED,
    OrderEventKind.FINAL_RETURNED,
)

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True, slots=True)
class Month:
    """One calendar month and its UTC bounds, ``[start, end)``."""

    year: int
    month: int
    start: datetime
    end: datetime

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def resolve_month(value: str | None, *, tz: ZoneInfo, now: datetime | None = None) -> Month:
    """``"YYYY-MM"`` (or the current month in ``tz``) as UTC bounds.

    Raises:
        AccountValidationError: ``invalid_month``.
    """
    if value is None or not value.strip():
        local = (now or utcnow()).astimezone(tz)
        year, month = local.year, local.month
    else:
        match = _MONTH.match(value.strip())
        if match is None:
            raise AccountValidationError(
                "invalid_month", "Tháng không hợp lệ (YYYY-MM).", details={"field": "month"}
            )
        year, month = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 2000 <= year <= 2100):
            raise AccountValidationError(
                "invalid_month", "Tháng không hợp lệ (YYYY-MM).", details={"field": "month"}
            )
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    start = datetime.combine(date(year, month, 1), time.min, tzinfo=tz).astimezone(ZoneInfo("UTC"))
    end = datetime.combine(date(next_year, next_month, 1), time.min, tzinfo=tz).astimezone(
        ZoneInfo("UTC")
    )
    return Month(year=year, month=month, start=start, end=end)


@dataclass(frozen=True, slots=True)
class MemberStats:
    month: str
    points: float = 0.0
    nodes_done: int = 0
    nodes_in_progress: int = 0
    revisions: int = 0
    orders_created: int = 0
    orders_completed: int = 0
    pr_contents_owned: int = 0
    pr_productions_done: int = 0
    pr_approvals: int = 0
    work_items_counted: int = 0
    on_time_rate: float | None = None
    first_pass_rate: float | None = None
    late_count: int = 0
    tokens_used: float = 0.0
    tokens_budget: float = 0.0
    effort_rate: float | None = None
    performance_score: float | None = None
    output_target: int | None = None


class AccountStatsService:
    """Monthly figures for one or many people, in grouped queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def stats_for(
        self, user_ids: Iterable[uuid.UUID], month: Month
    ) -> dict[uuid.UUID, MemberStats]:
        """Every requested person's figures, zeros included. No per-row loop."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        raw: dict[uuid.UUID, dict[str, Any]] = {user_id: {} for user_id in ids}

        def merge(rows: Iterable[Any], *fields: str) -> None:
            for row in rows:
                target = raw.get(row[0])
                if target is None:
                    continue
                for index, name in enumerate(fields, start=1):
                    target[name] = row[index]

        # Ads: production nodes done in the month, their points and first passes.
        done = await self._session.execute(
            select(
                OrderNode.assignee_user_id,
                func.count(OrderNode.id),
                func.coalesce(func.sum(Order.video_kind_points), 0),
                func.sum(case((OrderNode.revision_count == 0, 1), else_=0)),
                func.sum(case((OrderNode.deadline_met.is_not(None), 1), else_=0)),
                func.sum(case((OrderNode.deadline_met.is_(True), 1), else_=0)),
            )
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                OrderNode.assignee_user_id.in_(ids),
                OrderNode.node_type.in_(PRODUCTION_NODES),
                OrderNode.status == OrderNodeStatus.HOAN_THANH,
                OrderNode.approved_at >= month.start,
                OrderNode.approved_at < month.end,
            )
            .group_by(OrderNode.assignee_user_id)
        )
        merge(done.all(), "nodes_done", "points", "first_pass", "with_deadline", "met")

        in_progress = await self._session.execute(
            select(OrderNode.assignee_user_id, func.count(OrderNode.id))
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                OrderNode.assignee_user_id.in_(ids),
                OrderNode.status.in_(IN_PROGRESS),
                Order.stage != OrderStage.CANCELLED,
            )
            .group_by(OrderNode.assignee_user_id)
        )
        merge(in_progress.all(), "nodes_in_progress")

        revisions = await self._session.execute(
            select(OrderNode.assignee_user_id, func.count(OrderEvent.id))
            .join(OrderNode, OrderNode.id == OrderEvent.node_id)
            .where(
                OrderNode.assignee_user_id.in_(ids),
                OrderEvent.kind.in_(RETURN_EVENTS),
                OrderEvent.created_at >= month.start,
                OrderEvent.created_at < month.end,
            )
            .group_by(OrderNode.assignee_user_id)
        )
        merge(revisions.all(), "revisions")

        # Ads orders they ordered: submitted and completed in the month, in one pass.
        submitted_in = and_(Order.submitted_at >= month.start, Order.submitted_at < month.end)
        completed_in = and_(
            Order.stage == OrderStage.COMPLETED,
            Order.completed_at >= month.start,
            Order.completed_at < month.end,
        )
        orders = await self._session.execute(
            select(
                Order.owner_user_id,
                func.sum(case((submitted_in, 1), else_=0)),
                func.sum(case((completed_in, 1), else_=0)),
            )
            .where(Order.owner_user_id.in_(ids))
            .group_by(Order.owner_user_id)
        )
        merge(orders.all(), "orders_created", "orders_completed")

        # PR.
        contents = await self._session.execute(
            select(PrContentItem.owner_user_id, func.count(PrContentItem.id))
            .where(
                PrContentItem.owner_user_id.in_(ids),
                PrContentItem.created_at >= month.start,
                PrContentItem.created_at < month.end,
            )
            .group_by(PrContentItem.owner_user_id)
        )
        merge(contents.all(), "pr_contents_owned")

        productions = await self._session.execute(
            select(
                PrProductionSubmission.submitted_by_user_id, func.count(PrProductionSubmission.id)
            )
            .where(
                PrProductionSubmission.submitted_by_user_id.in_(ids),
                PrProductionSubmission.created_at >= month.start,
                PrProductionSubmission.created_at < month.end,
            )
            .group_by(PrProductionSubmission.submitted_by_user_id)
        )
        merge(productions.all(), "pr_productions_done")

        approvals = await self._session.execute(
            select(PrApprovalEvent.reviewer_user_id, func.count(PrApprovalEvent.id))
            .where(
                PrApprovalEvent.reviewer_user_id.in_(ids),
                PrApprovalEvent.decided_at >= month.start,
                PrApprovalEvent.decided_at < month.end,
            )
            .group_by(PrApprovalEvent.reviewer_user_id)
        )
        merge(approvals.all(), "pr_approvals")

        # Both units: the KPI ledger.
        counted = await self._session.execute(
            select(PrWorkResult.user_id, func.count(PrWorkResult.id))
            .where(
                PrWorkResult.user_id.in_(ids),
                PrWorkResult.status == PrWorkCountStatus.COUNTED,
                PrWorkResult.counted_at >= month.start,
                PrWorkResult.counted_at < month.end,
            )
            .group_by(PrWorkResult.user_id)
        )
        merge(counted.all(), "work_items_counted")

        effort = await self._effort(ids, month)
        return {
            user_id: self._build(raw[user_id], month, effort.get(user_id), effort.get(None))
            for user_id in ids
        }

    async def _effort(
        self, ids: list[uuid.UUID], month: Month
    ) -> dict[uuid.UUID | None, dict[str, Any]]:
        """ORD effort and the score's knobs for the month (0053). Key ``None``
        holds the unit-wide values: weights and the output benchmark."""
        unit = await self._session.scalar(select(OrgUnit).where(OrgUnit.code == UnitCode.ADS))
        if unit is None:
            return {}
        settings = UnitSettings.model_validate(unit.settings or {})
        first = date(month.year, month.month, 1)
        last = date(month.year + (month.month == 12), month.month % 12 + 1, 1) - timedelta(days=1)
        work_days = working_share(first, last, settings)
        used = await self._session.execute(
            select(OrderTokenLedger.user_id, func.sum(OrderTokenLedger.tokens))
            .where(
                OrderTokenLedger.unit_id == unit.id,
                OrderTokenLedger.user_id.in_(ids),
                OrderTokenLedger.work_date >= first,
                OrderTokenLedger.work_date <= last,
            )
            .group_by(OrderTokenLedger.user_id)
        )
        members = await self._session.scalars(
            select(OrgUnitMember).where(
                OrgUnitMember.unit_id == unit.id,
                OrgUnitMember.user_id.in_(ids),
                OrgUnitMember.left_at.is_(None),
                OrgUnitMember.role.in_(sorted(FUNCTION_ROLES, key=lambda role: role.value)),
            )
        )
        per: dict[uuid.UUID | None, dict[str, Any]] = {}
        for member in members:
            per[member.user_id] = {"budget": daily_budget(member, settings) * work_days}
        for user_id, total in used.all():
            per.setdefault(user_id, {"budget": 0.0})["used"] = float(total or 0)
        target = settings.output_target
        if target is None:
            counts = (
                select(func.count(OrderNode.id).label("done"))
                .join(Order, Order.id == OrderNode.order_id)
                .where(
                    Order.unit_id == unit.id,
                    OrderNode.node_type.in_(PRODUCTION_NODES),
                    OrderNode.status == OrderNodeStatus.HOAN_THANH,
                    OrderNode.approved_at >= month.start,
                    OrderNode.approved_at < month.end,
                    OrderNode.assignee_user_id.is_not(None),
                )
                .group_by(OrderNode.assignee_user_id)
                .subquery()
            )
            target = int(await self._session.scalar(select(func.max(counts.c.done))) or 0) or None
        per[None] = {"weights": settings.perf_weights, "target": target}
        return per

    async def stats_for_one(self, user_id: uuid.UUID, month: Month) -> MemberStats:
        return (await self.stats_for([user_id], month))[user_id]

    @staticmethod
    def _build(
        values: dict[str, Any],
        month: Month,
        effort: dict[str, Any] | None = None,
        unit: dict[str, Any] | None = None,
    ) -> MemberStats:
        nodes_done = int(values.get("nodes_done") or 0)
        first_pass = int(values.get("first_pass") or 0)
        with_deadline = int(values.get("with_deadline") or 0)
        met = int(values.get("met") or 0)
        points = values.get("points") or 0
        first_pass_rate = round(first_pass / nodes_done, 4) if nodes_done else None
        on_time_rate = round(met / with_deadline, 4) if with_deadline else None
        used = float((effort or {}).get("used") or 0.0)
        budget = float((effort or {}).get("budget") or 0.0)
        target = None if unit is None else unit.get("target")
        score = None
        if unit is not None and nodes_done:
            weights = unit["weights"]
            parts = [(weights.output, min(nodes_done / target, 1.0) if target else 1.0)]
            if on_time_rate is not None:
                parts.append((weights.on_time, on_time_rate))
            if first_pass_rate is not None:
                parts.append((weights.quality, first_pass_rate))
            total = sum(weight for weight, _ in parts)
            if total > 0:
                score = round(100 * sum(weight * value for weight, value in parts) / total, 1)
        return MemberStats(
            month=month.label,
            points=round(float(Decimal(str(points))), 2),
            nodes_done=nodes_done,
            nodes_in_progress=int(values.get("nodes_in_progress") or 0),
            revisions=int(values.get("revisions") or 0),
            orders_created=int(values.get("orders_created") or 0),
            orders_completed=int(values.get("orders_completed") or 0),
            pr_contents_owned=int(values.get("pr_contents_owned") or 0),
            pr_productions_done=int(values.get("pr_productions_done") or 0),
            pr_approvals=int(values.get("pr_approvals") or 0),
            work_items_counted=int(values.get("work_items_counted") or 0),
            on_time_rate=on_time_rate,
            first_pass_rate=first_pass_rate,
            late_count=with_deadline - met,
            tokens_used=round(used, 2),
            tokens_budget=round(budget, 2),
            effort_rate=round(used / budget, 4) if budget else None,
            performance_score=score,
            output_target=target,
        )


__all__ = [
    "IN_PROGRESS",
    "PRODUCTION_NODES",
    "RETURN_EVENTS",
    "AccountStatsService",
    "MemberStats",
    "Month",
    "resolve_month",
]
