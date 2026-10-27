"""The points reports: one person, a Trưởng phòng's team, the whole company.

Every figure comes from one place - ``order_node_points`` (what a person
earned on a step, written when the step is done, 0074) plus the steps still
open - so a person, their Trưởng phòng and the company view always agree:

* **points / steps done** - rows whose ``first_completed_at`` falls in the
  month (Vietnam time);
* **pending** ("đang làm, dự kiến") - steps the person is on and not done yet,
  scored from the codes they carry now (a hand-in waiting for its codes to be
  approved counts its proposal); shown apart, never added to the total;
* **returns** - sends back in the month on a step the person is on, by reason
  (Làm lỗi / Do thay đổi yêu cầu / Mã công việc chưa hợp lý);
* **late** - steps done in the month after their deadline (``deadline_met``).

Every point is shown after the person's coefficient for its month ("hệ số
điểm", 0076, :mod:`meobot.application.performance.coefficients`); pending
points take this month's.

Department figures use the department the points were recorded for
(``order_node_points.department_id``, 0075: the step's phase). Who may read
what is decided by the caller (:mod:`meobot.application.performance.access`).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.stats_service import Month, resolve_month
from meobot.application.org.seats import load_seats
from meobot.application.performance.coefficients import (
    first_day,
    load_coefficients,
    month_of,
    weigh,
)
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.department import Department
from meobot.db.models.order import (
    Order,
    OrderEvent,
    OrderNode,
    OrderNodeAssignee,
    OrderNodePoints,
)
from meobot.db.models.user import User
from meobot.domain.orders.design_codes import DesignItem, items_from_json, total_points
from meobot.domain.orders.models import (
    TERMINAL_STAGES,
    OrderCategory,
    OrderNodeStatus,
    OrderNodeType,
    OrderReturnReason,
    OrderStage,
)
from meobot.domain.orders.task_codes import TASK_PHASES, phase_label

#: Steps still being worked on.
OPEN_STATUSES = (OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA, OrderNodeStatus.CHO_DUYET)
#: Orders that will never finish their steps.
DEAD_STAGES = (OrderStage.CANCELLED,)


@dataclass(frozen=True, slots=True)
class Figures:
    """One person's month."""

    points: int = 0
    steps_done: int = 0
    pending_points: int = 0
    pending_count: int = 0
    returns_fault: int = 0
    returns_other: int = 0
    returns_codes: int = 0
    late_count: int = 0
    with_deadline: int = 0
    previous_points: int = 0

    @property
    def on_time_rate(self) -> float | None:
        if self.with_deadline == 0:
            return None
        return round((self.with_deadline - self.late_count) / self.with_deadline, 4)


@dataclass(frozen=True, slots=True)
class PersonLine:
    """One step on the person's list."""

    node_id: uuid.UUID
    order_id: uuid.UUID
    order_code: str
    order_title: str
    phase: str
    phase_label: str
    items: tuple[DesignItem, ...]
    #: The line's points after the person's coefficient for the month.
    points: int
    #: ``EARNED`` (counted), ``PENDING_REVIEW`` (codes wait for the Trưởng
    #: phòng), ``IN_PROGRESS``.
    status: str
    at: datetime | None = None
    #: Each item's points after the coefficient, in ``items`` order.
    item_points: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonReport:
    user_id: uuid.UUID
    name: str
    month: str
    figures: Figures
    by_phase: dict[str, int] = field(default_factory=dict)
    #: Points by week of the month (week 1 = days 1-7, …, week 5 = 29-31).
    by_week: tuple[int, ...] = (0, 0, 0, 0, 0)
    lines: tuple[PersonLine, ...] = ()
    departments: tuple[str, ...] = ()
    #: How many of each task code they did in the month.
    codes: tuple[CodeRow, ...] = ()


@dataclass(frozen=True, slots=True)
class TeamRow:
    user_id: uuid.UUID
    name: str
    departments: tuple[str, ...]
    figures: Figures


@dataclass(frozen=True, slots=True)
class DepartmentRow:
    department_id: uuid.UUID | None
    name: str
    people: int
    points: int
    steps_done: int
    late_count: int
    with_deadline: int

    @property
    def average(self) -> int:
        return round(self.points / self.people) if self.people else 0

    @property
    def on_time_rate(self) -> float | None:
        if self.with_deadline == 0:
            return None
        return round((self.with_deadline - self.late_count) / self.with_deadline, 4)


@dataclass(frozen=True, slots=True)
class CodeRow:
    """One task code (in one form) over a set of counted steps."""

    phase: str
    #: The catalogue code, or "Việc khác" for a free line.
    code: str
    name: str
    quantity: int
    #: After each person's coefficient.
    points: int
    phase_label: str = ""
    form: str = "NEW"
    #: ``None`` for "Làm mới" and free lines.
    form_label: str | None = None
    free: bool = False
    #: Distinct people and steps behind the row.
    people: int = 0
    steps: int = 0


@dataclass(frozen=True, slots=True)
class Overview:
    month: str
    points: int
    steps_done: int
    people: int
    departments: tuple[DepartmentRow, ...]
    top: tuple[TeamRow, ...]
    codes: tuple[CodeRow, ...]


@dataclass(frozen=True, slots=True)
class TrendPoint:
    month: str
    points: int
    by_department: dict[str, int]


@dataclass(frozen=True, slots=True)
class TimelineBar:
    """One step of one person on the Gantt chart ("Tiến độ")."""

    node_id: uuid.UUID
    order_code: str
    order_title: str
    phase: str
    phase_label: str
    #: When the person took it (accepted, else handed to them).
    start: datetime
    #: When it was first done; ``None`` while still being worked on.
    done_at: datetime | None
    deadline_at: datetime | None
    #: EARNED / PENDING_REVIEW / IN_PROGRESS (as on the person's lines), or
    #: UPCOMING: handed to them ahead, the order has not reached the step yet
    #: (``start`` = now, ``deadline_at`` = the step's, else the order's wish).
    status: str
    late: bool
    #: After the coefficient (pending: this month's), like every figure.
    points: int
    #: "D3", "T1 x2", "D4 · Animated (125%)", "Việc khác".
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineGroup:
    user_id: uuid.UUID
    name: str
    departments: tuple[str, ...]
    #: Points of the steps done in the month (after the coefficient).
    points: int
    pending_points: int
    bars: tuple[TimelineBar, ...]
    #: Steps handed to them ahead that the order has not reached yet.
    upcoming: int = 0


class PerformanceReportService:
    def __init__(self, session: AsyncSession, *, tz: ZoneInfo) -> None:
        self._session = session
        self._tz = tz

    def month(self, value: str | None) -> Month:
        return resolve_month(value, tz=self._tz)

    # --- one person -----------------------------------------------------------------

    async def person(self, user_id: uuid.UUID, month: Month) -> PersonReport:
        figures = (await self.figures([user_id], month)).get(user_id, Figures())
        earned = await self._earned_rows([user_id], month)
        book = await load_coefficients(self._session, [user_id])
        factor = book.of(user_id, first_day(month))
        now_factor = book.of(user_id, month_of(utcnow(), self._tz))
        by_phase: dict[str, int] = defaultdict(int)
        by_week = [0, 0, 0, 0, 0]
        lines: list[PersonLine] = []
        for points_row, order in earned:
            points = weigh(points_row.points, factor)
            items = items_from_json(points_row.items)
            by_phase[points_row.phase.value] += points
            local = ensure_utc(points_row.first_completed_at).astimezone(self._tz).date()
            by_week[min((local.day - 1) // 7, 4)] += points
            lines.append(
                PersonLine(
                    node_id=points_row.node_id,
                    order_id=order.id,
                    order_code=order.code,
                    order_title=order.title,
                    phase=points_row.phase.value,
                    phase_label=phase_label(points_row.phase),
                    items=items,
                    points=points,
                    status="EARNED",
                    at=points_row.first_completed_at,
                    item_points=tuple(weigh(item.points, factor) for item in items),
                )
            )
        for node, order, items in (await self._pending([user_id])).get(user_id, []):
            lines.append(
                PersonLine(
                    node_id=node.id,
                    order_id=order.id,
                    order_code=order.code,
                    order_title=order.title,
                    phase=node.node_type.value,
                    phase_label=phase_label(node.node_type),
                    items=items,
                    points=weigh(total_points(items), now_factor),
                    item_points=tuple(weigh(item.points, now_factor) for item in items),
                    status=(
                        "PENDING_REVIEW"
                        if node.proposed_design_items is not None
                        or node.status is OrderNodeStatus.CHO_DUYET
                        else "IN_PROGRESS"
                    ),
                    at=node.submitted_at or node.accepted_at or node.assigned_at,
                )
            )
        names = await self._names([user_id])
        seats = await load_seats(self._session, [user_id])
        return PersonReport(
            user_id=user_id,
            name=names.get(user_id, "—"),
            month=month.label,
            figures=figures,
            by_phase=dict(by_phase),
            by_week=tuple(by_week),
            lines=tuple(lines),
            departments=tuple(seat.name for seat in seats.get(user_id, ())),
            codes=await self.codes([user_id], month),
        )

    # --- many people -------------------------------------------------------------------

    async def team(self, user_ids: Iterable[uuid.UUID], month: Month) -> tuple[TeamRow, ...]:
        """One row per person, highest points first."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return ()
        figures = await self.figures(ids, month)
        names = await self._names(ids)
        seats = await load_seats(self._session, ids)
        rows = [
            TeamRow(
                user_id=user_id,
                name=names.get(user_id, "—"),
                departments=tuple(seat.name for seat in seats.get(user_id, ())),
                figures=figures.get(user_id, Figures()),
            )
            for user_id in ids
        ]
        return tuple(sorted(rows, key=lambda row: (-row.figures.points, row.name)))

    async def figures(
        self, user_ids: Sequence[uuid.UUID], month: Month
    ) -> dict[uuid.UUID, Figures]:
        """Every requested person's figures, in grouped queries."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        raw: dict[uuid.UUID, dict[str, int]] = {user_id: defaultdict(int) for user_id in ids}
        book = await load_coefficients(self._session, ids)
        start = first_day(month)
        done = await self._session.execute(
            select(OrderNodePoints.user_id, OrderNodePoints.points, OrderNode.deadline_met)
            .join(OrderNode, OrderNode.id == OrderNodePoints.node_id)
            .where(
                OrderNodePoints.user_id.in_(ids),
                OrderNodePoints.first_completed_at >= month.start,
                OrderNodePoints.first_completed_at < month.end,
            )
        )
        for user_id, points, deadline_met in done.all():
            raw[user_id]["points"] += book.weigh(user_id, points, start)
            raw[user_id]["steps_done"] += 1
            if deadline_met is not None:
                raw[user_id]["with_deadline"] += 1
                if deadline_met is False:
                    raw[user_id]["late_count"] += 1
        previous = _previous(month, self._tz)
        before = await self._session.execute(
            select(OrderNodePoints.user_id, OrderNodePoints.points).where(
                OrderNodePoints.user_id.in_(ids),
                OrderNodePoints.first_completed_at >= previous.start,
                OrderNodePoints.first_completed_at < previous.end,
            )
        )
        for user_id, points in before.all():
            raw[user_id]["previous_points"] += book.weigh(user_id, points, first_day(previous))
        today = month_of(utcnow(), self._tz)
        for user_id, steps in (await self._pending(ids)).items():
            raw[user_id]["pending_count"] = len(steps)
            raw[user_id]["pending_points"] = sum(
                book.weigh(user_id, total_points(items), today) for _, _, items in steps
            )
        for (user_id, reason), count in (await self._returns(ids, month)).items():
            key = {
                OrderReturnReason.WORKER_FAULT: "returns_fault",
                OrderReturnReason.CHANGED_REQUEST: "returns_other",
                OrderReturnReason.WRONG_CODES: "returns_codes",
            }[reason]
            raw[user_id][key] += count
        return {user_id: Figures(**values) for user_id, values in raw.items()}

    # --- the company ----------------------------------------------------------------

    async def overview(self, month: Month) -> Overview:
        rows = (
            await self._session.execute(
                select(OrderNodePoints, OrderNode.deadline_met)
                .join(OrderNode, OrderNode.id == OrderNodePoints.node_id)
                .where(
                    OrderNodePoints.first_completed_at >= month.start,
                    OrderNodePoints.first_completed_at < month.end,
                )
            )
        ).all()
        buckets: dict[uuid.UUID | None, _Bucket] = {}
        by_person: dict[uuid.UUID, int] = defaultdict(int)
        book = await load_coefficients(self._session, {row[0].user_id for row in rows})
        start = first_day(month)
        for points_row, deadline_met in rows:
            factor = book.of(points_row.user_id, start)
            points = weigh(points_row.points, factor)
            bucket = buckets.setdefault(points_row.department_id, _Bucket())
            bucket.people.add(points_row.user_id)
            bucket.points += points
            bucket.steps += 1
            if deadline_met is not None:
                bucket.with_deadline += 1
                if deadline_met is False:
                    bucket.late += 1
            by_person[points_row.user_id] += points
        names = await self._department_names([key for key in buckets if key is not None])
        departments = tuple(
            sorted(
                (
                    DepartmentRow(
                        department_id=department_id,
                        name=(
                            names.get(department_id, "Chưa có phòng")
                            if department_id is not None
                            else "Chưa có phòng"
                        ),
                        people=len(bucket.people),
                        points=bucket.points,
                        steps_done=bucket.steps,
                        late_count=bucket.late,
                        with_deadline=bucket.with_deadline,
                    )
                    for department_id, bucket in buckets.items()
                ),
                key=lambda row: (-row.points, row.name),
            )
        )
        top_ids = [
            user_id for user_id, _ in sorted(by_person.items(), key=lambda pair: -pair[1])[:10]
        ]
        top = await self.team(top_ids, month)
        code_rows = tuple(
            sorted(
                _tally_codes((row, book.of(row.user_id, start)) for row, _ in rows),
                key=lambda row: -row.points,
            )[:15]
        )
        return Overview(
            month=month.label,
            points=sum(row.points for row in departments),
            steps_done=sum(row.steps_done for row in departments),
            people=len(by_person),
            departments=departments,
            top=top,
            codes=code_rows,
        )

    async def codes(
        self,
        user_ids: Sequence[uuid.UUID] | None,
        month: Month,
        *,
        department_id: uuid.UUID | None = None,
    ) -> tuple[CodeRow, ...]:
        """How many of each task code (and form) these people did in the month
        - everybody when ``user_ids`` is None, only the points counted for
        ``department_id`` when given - by phase, then code, then form."""
        statement = select(OrderNodePoints).where(
            OrderNodePoints.first_completed_at >= month.start,
            OrderNodePoints.first_completed_at < month.end,
        )
        if user_ids is not None:
            if not user_ids:
                return ()
            statement = statement.where(OrderNodePoints.user_id.in_(list(user_ids)))
        if department_id is not None:
            statement = statement.where(OrderNodePoints.department_id == department_id)
        rows = (await self._session.scalars(statement)).all()
        book = await load_coefficients(self._session, {row.user_id for row in rows})
        start = first_day(month)
        phases = {phase.value: index for index, phase in enumerate(TASK_PHASES)}
        return tuple(
            sorted(
                _tally_codes((row, book.of(row.user_id, start)) for row in rows),
                key=lambda row: (
                    phases.get(row.phase, 99),
                    row.free,
                    row.code,
                    row.form != "NEW",
                    row.form_label or "",
                ),
            )
        )

    async def timeline(
        self, user_ids: Sequence[uuid.UUID], month: Month
    ) -> tuple[TimelineGroup, ...]:
        """Each person's steps for the Gantt chart: the ones first done in the
        month, the ones still open whose span (from when they took it to its
        deadline, or today) touches the month, and the ones handed to them
        ahead that the order has not reached yet (now to their deadline)."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return ()
        now = utcnow()
        book = await load_coefficients(self._session, ids)
        start_month = first_day(month)
        today = month_of(now, self._tz)
        earned = (
            await self._session.execute(
                select(OrderNodePoints, OrderNode, Order)
                .join(OrderNode, OrderNode.id == OrderNodePoints.node_id)
                .join(Order, Order.id == OrderNodePoints.order_id)
                .where(
                    OrderNodePoints.user_id.in_(ids),
                    OrderNodePoints.first_completed_at >= month.start,
                    OrderNodePoints.first_completed_at < month.end,
                )
            )
        ).all()
        pending = await self._pending(ids)
        upcoming = await self._upcoming(ids)
        node_ids = {node.id for _, node, _ in earned} | {
            node.id for steps in pending.values() for node, _, _ in steps
        }
        own_rows: dict[tuple[uuid.UUID, uuid.UUID], OrderNodeAssignee] = {}
        if node_ids:
            for row in (
                await self._session.scalars(
                    select(OrderNodeAssignee).where(
                        OrderNodeAssignee.node_id.in_(list(node_ids)),
                        OrderNodeAssignee.user_id.in_(ids),
                    )
                )
            ).all():
                own_rows.setdefault((row.node_id, row.user_id), row)

        def taken(user_id: uuid.UUID, node: OrderNode, order: Order) -> datetime:
            row = own_rows.get((node.id, user_id))
            moments = ((row.accepted_at, row.assigned_at) if row is not None else ()) + (
                node.accepted_at,
                node.assigned_at,
                node.activated_at,
                order.submitted_at,
            )
            return ensure_utc(next((at for at in moments if at is not None), order.created_at))

        bars: dict[uuid.UUID, list[TimelineBar]] = defaultdict(list)
        for points_row, node, order in earned:
            items = items_from_json(points_row.items)
            done_at = ensure_utc(points_row.first_completed_at)
            bars[points_row.user_id].append(
                TimelineBar(
                    node_id=node.id,
                    order_code=order.code,
                    order_title=order.title,
                    phase=points_row.phase.value,
                    phase_label=phase_label(points_row.phase),
                    start=min(taken(points_row.user_id, node, order), done_at),
                    done_at=done_at,
                    deadline_at=ensure_utc(node.deadline_at) if node.deadline_at else None,
                    status="EARNED",
                    late=node.deadline_met is False,
                    points=book.weigh(points_row.user_id, points_row.points, start_month),
                    codes=_code_labels(items),
                )
            )
        for user_id, steps in pending.items():
            for node, order, items in steps:
                start = taken(user_id, node, order)
                deadline = node.revision_deadline_at or node.deadline_at
                planned_end = max(now, ensure_utc(deadline)) if deadline else now
                if start >= month.end or planned_end < month.start:
                    continue
                bars[user_id].append(
                    TimelineBar(
                        node_id=node.id,
                        order_code=order.code,
                        order_title=order.title,
                        phase=node.node_type.value,
                        phase_label=phase_label(node.node_type),
                        start=start,
                        done_at=None,
                        deadline_at=ensure_utc(deadline) if deadline else None,
                        status=(
                            "PENDING_REVIEW"
                            if node.proposed_design_items is not None
                            or node.status is OrderNodeStatus.CHO_DUYET
                            else "IN_PROGRESS"
                        ),
                        late=deadline is not None and ensure_utc(deadline) < now,
                        points=book.weigh(user_id, total_points(items), today),
                        codes=_code_labels(items),
                    )
                )
        for user_id, steps in upcoming.items():
            for node, order, items in steps:
                deadline = node.deadline_at or order.desired_deadline_at
                planned_end = max(now, ensure_utc(deadline)) if deadline else now
                if now >= month.end or planned_end < month.start:
                    continue
                bars[user_id].append(
                    TimelineBar(
                        node_id=node.id,
                        order_code=order.code,
                        order_title=order.title,
                        phase=node.node_type.value,
                        phase_label=phase_label(node.node_type),
                        start=now,
                        done_at=None,
                        deadline_at=ensure_utc(deadline) if deadline else None,
                        status="UPCOMING",
                        late=deadline is not None and ensure_utc(deadline) < now,
                        points=book.weigh(user_id, total_points(items), today),
                        codes=_code_labels(items),
                    )
                )
        names = await self._names(ids)
        seats = await load_seats(self._session, ids)
        return tuple(
            TimelineGroup(
                user_id=user_id,
                name=names.get(user_id, "—"),
                departments=tuple(seat.name for seat in seats.get(user_id, ())),
                points=sum(bar.points for bar in bars[user_id] if bar.done_at is not None),
                pending_points=sum(
                    bar.points
                    for bar in bars[user_id]
                    if bar.done_at is None and bar.status != "UPCOMING"
                ),
                bars=tuple(sorted(bars[user_id], key=lambda bar: (bar.start, bar.order_code))),
                upcoming=sum(1 for bar in bars[user_id] if bar.status == "UPCOMING"),
            )
            for user_id in ids
        )

    async def trend(
        self,
        month: Month,
        *,
        months: int = 6,
        user_ids: Sequence[uuid.UUID] | None = None,
    ) -> tuple[TrendPoint, ...]:
        """The total points of the last ``months`` months up to ``month``, and
        by department - everybody, or only ``user_ids``."""
        windows = [month]
        for _ in range(months - 1):
            windows.insert(0, _previous(windows[0], self._tz))
        statement = select(
            OrderNodePoints.department_id,
            OrderNodePoints.first_completed_at,
            OrderNodePoints.points,
            OrderNodePoints.user_id,
        ).where(
            OrderNodePoints.first_completed_at >= windows[0].start,
            OrderNodePoints.first_completed_at < month.end,
        )
        if user_ids is not None:
            if not user_ids:
                return tuple(TrendPoint(window.label, 0, {}) for window in windows)
            statement = statement.where(OrderNodePoints.user_id.in_(list(user_ids)))
        rows = (await self._session.execute(statement)).all()
        book = await load_coefficients(self._session, {row[3] for row in rows})
        names = await self._department_names({row[0] for row in rows if row[0] is not None})
        points: list[TrendPoint] = []
        for window in windows:
            total = 0
            split: dict[str, int] = defaultdict(int)
            for department_id, at, value, user_id in rows:
                if window.start <= ensure_utc(at) < window.end:
                    weighed = book.weigh(user_id, value, first_day(window))
                    total += weighed
                    split[names.get(department_id, "Chưa có phòng")] += weighed
            points.append(TrendPoint(window.label, total, dict(split)))
        return tuple(points)

    async def detail_rows(
        self, user_ids: Sequence[uuid.UUID] | None, month: Month
    ) -> list[tuple[OrderNodePoints, Order, Decimal]]:
        """Every counted step of the month, with the person's coefficient for
        it (for the Excel export)."""
        rows = await self._earned_rows(user_ids, month)
        book = await load_coefficients(self._session, {row.user_id for row, _ in rows})
        start = first_day(month)
        return [(row, order, book.of(row.user_id, start)) for row, order in rows]

    async def names(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        return await self._names(user_ids)

    async def department_names(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        return await self._department_names(ids)

    async def people_with_points(self, month: Month) -> list[uuid.UUID]:
        rows = await self._session.scalars(
            select(OrderNodePoints.user_id)
            .where(
                OrderNodePoints.first_completed_at >= month.start,
                OrderNodePoints.first_completed_at < month.end,
            )
            .distinct()
        )
        return list(rows.all())

    async def department_people(self, department_id: uuid.UUID, month: Month) -> list[uuid.UUID]:
        """Who is seated in the department now, plus whoever earned points for
        it in the month (a person who has moved on still shows there)."""
        seats = await load_seats(self._session)
        seated = [
            user_id
            for user_id, held in seats.items()
            if any(seat.department_id == department_id for seat in held)
        ]
        earned = await self._session.scalars(
            select(OrderNodePoints.user_id)
            .where(
                OrderNodePoints.department_id == department_id,
                OrderNodePoints.first_completed_at >= month.start,
                OrderNodePoints.first_completed_at < month.end,
            )
            .distinct()
        )
        return list(dict.fromkeys([*seated, *earned.all()]))

    # --- helpers -------------------------------------------------------------------------

    async def _earned_rows(
        self, user_ids: Sequence[uuid.UUID] | None, month: Month
    ) -> list[tuple[OrderNodePoints, Order]]:
        statement = (
            select(OrderNodePoints, Order)
            .join(Order, Order.id == OrderNodePoints.order_id)
            .where(
                OrderNodePoints.first_completed_at >= month.start,
                OrderNodePoints.first_completed_at < month.end,
            )
            .order_by(OrderNodePoints.first_completed_at.desc())
        )
        if user_ids is not None:
            statement = statement.where(OrderNodePoints.user_id.in_(list(user_ids)))
        return [(row[0], row[1]) for row in (await self._session.execute(statement)).all()]

    async def _pending(
        self, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[OrderNode, Order, tuple[DesignItem, ...]]]]:
        """The steps each person is on and not done yet, with the codes they
        would score: their own Design list, else the step's (a proposal first)."""
        ids = list(user_ids)
        if not ids:
            return {}
        open_steps = (
            select(OrderNode, Order)
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                OrderNode.status.in_(OPEN_STATUSES),
                OrderNode.node_type.in_(TASK_PHASES),
                Order.stage.not_in(DEAD_STAGES),
            )
        )
        rows_by_node: dict[uuid.UUID, list[OrderNodeAssignee]] = defaultdict(list)
        for row in (
            await self._session.scalars(
                select(OrderNodeAssignee).where(
                    OrderNodeAssignee.user_id.in_(ids), OrderNodeAssignee.removed_at.is_(None)
                )
            )
        ).all():
            rows_by_node[row.node_id].append(row)
        involved: ColumnElement[bool] = OrderNode.assignee_user_id.in_(ids)
        if rows_by_node:
            involved = or_(involved, OrderNode.id.in_(list(rows_by_node)))
        found = (await self._session.execute(open_steps.where(involved))).all()
        pending: dict[uuid.UUID, list[tuple[OrderNode, Order, tuple[DesignItem, ...]]]] = (
            defaultdict(list)
        )
        for node, order in found:
            people: dict[uuid.UUID, tuple[DesignItem, ...]] = {}
            if node.node_type is OrderNodeType.THIET_KE and rows_by_node.get(node.id):
                for row in rows_by_node[node.id]:
                    people[row.user_id] = items_from_json(row.work_items)
            elif node.assignee_user_id in ids:
                assert node.assignee_user_id is not None
                if node.node_type is OrderNodeType.THIET_KE:
                    items = items_from_json(node.proposed_design_items) or items_from_json(
                        order.design_items
                    )
                else:
                    items = items_from_json(node.proposed_design_items) or items_from_json(
                        node.task_items
                    )
                if order.category is OrderCategory.BIRTHDAY and not items:
                    items = ()
                people[node.assignee_user_id] = items
            for user_id, items in people.items():
                pending[user_id].append((node, order, items))
        return pending

    async def _upcoming(
        self, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[OrderNode, Order, tuple[DesignItem, ...]]]]:
        """Steps handed to each person ahead (chosen on the order) that the
        order has not reached yet, with the codes it suggests for them."""
        ids = list(user_ids)
        if not ids:
            return {}
        found = (
            await self._session.execute(
                select(OrderNode, Order)
                .join(Order, Order.id == OrderNode.order_id)
                .where(
                    OrderNode.status == OrderNodeStatus.CHUA_TOI,
                    OrderNode.preassigned_user_id.in_(ids),
                    OrderNode.node_type.in_(TASK_PHASES),
                    Order.stage.not_in(TERMINAL_STAGES),
                )
            )
        ).all()
        upcoming: dict[uuid.UUID, list[tuple[OrderNode, Order, tuple[DesignItem, ...]]]] = (
            defaultdict(list)
        )
        for node, order in found:
            assert node.preassigned_user_id is not None
            items = items_from_json(
                order.design_items if node.node_type is OrderNodeType.THIET_KE else node.task_items
            )
            upcoming[node.preassigned_user_id].append((node, order, items))
        return upcoming

    async def _returns(
        self, user_ids: Sequence[uuid.UUID], month: Month
    ) -> dict[tuple[uuid.UUID, OrderReturnReason], int]:
        """Sends back in the month on a step each person is on, by reason."""
        ids = list(user_ids)
        in_month = (
            OrderEvent.return_reason.is_not(None),
            OrderEvent.created_at >= month.start,
            OrderEvent.created_at < month.end,
        )
        pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
        reasons: dict[uuid.UUID, OrderReturnReason] = {}
        for event_id, user_id, reason in (
            await self._session.execute(
                select(OrderEvent.id, OrderNode.assignee_user_id, OrderEvent.return_reason)
                .join(OrderNode, OrderNode.id == OrderEvent.node_id)
                .where(OrderNode.assignee_user_id.in_(ids), *in_month)
            )
        ).all():
            pairs.add((event_id, user_id))
            reasons[event_id] = reason
        for event_id, user_id, reason in (
            await self._session.execute(
                select(OrderEvent.id, OrderNodeAssignee.user_id, OrderEvent.return_reason)
                .join(OrderNodeAssignee, OrderNodeAssignee.node_id == OrderEvent.node_id)
                .where(OrderNodeAssignee.user_id.in_(ids), *in_month)
            )
        ).all():
            pairs.add((event_id, user_id))
            reasons[event_id] = reason
        counts: dict[tuple[uuid.UUID, OrderReturnReason], int] = defaultdict(int)
        for event_id, user_id in pairs:
            counts[(user_id, reasons[event_id])] += 1
        return counts

    async def _names(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        ids = list(set(user_ids))
        if not ids:
            return {}
        rows = await self._session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
        return {user_id: name or "—" for user_id, name in rows.all()}

    async def _department_names(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        wanted = list(set(ids))
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(Department.id, Department.name).where(Department.id.in_(wanted))
        )
        return {row[0]: row[1] for row in rows.all()}


@dataclass(slots=True)
class _Bucket:
    people: set[uuid.UUID] = field(default_factory=set)
    points: int = 0
    steps: int = 0
    late: int = 0
    with_deadline: int = 0


@dataclass(slots=True)
class _CodeBucket:
    phase: OrderNodeType
    code: str
    form: str
    form_label: str | None
    free: bool
    names: set[str] = field(default_factory=set)
    quantity: int = 0
    points: int = 0
    people: set[uuid.UUID] = field(default_factory=set)
    steps: set[uuid.UUID] = field(default_factory=set)


def _code_labels(items: Iterable[DesignItem]) -> tuple[str, ...]:
    labels = []
    for item in items:
        label = "Việc khác" if item.free else item.code
        if not item.free and item.form != "NEW":
            label = f"{label} · {item.form_label}"
        if item.quantity > 1:
            label = f"{label} x{item.quantity}"
        labels.append(label)
    return tuple(labels)


def _tally_codes(rows: Iterable[tuple[OrderNodePoints, Decimal]]) -> list[CodeRow]:
    """Counted steps, each with its person's coefficient -> one row per
    (phase, code, form). A free line ("Việc khác") is one row per phase."""
    buckets: dict[tuple[str, str, str], _CodeBucket] = {}
    for points_row, factor in rows:
        for item in items_from_json(points_row.items):
            form = "" if item.free else item.form
            key = (points_row.phase.value, "" if item.free else str(item.code_id), form)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = _CodeBucket(
                    phase=points_row.phase,
                    code="Việc khác" if item.free else item.code,
                    form=item.form,
                    form_label=None if item.free or item.form == "NEW" else item.form_label,
                    free=item.free,
                )
            name = (item.note or "") if item.free else (item.name or item.examples or "")
            if name:
                bucket.names.add(name)
            bucket.quantity += item.quantity
            bucket.points += weigh(item.points, factor)
            bucket.people.add(points_row.user_id)
            bucket.steps.add(points_row.node_id)
    return [
        CodeRow(
            phase=bucket.phase.value,
            phase_label=phase_label(bucket.phase),
            code=bucket.code,
            name=(
                next(iter(bucket.names))
                if len(bucket.names) == 1
                else (f"{len(bucket.names)} việc khác nhau" if bucket.names else "")
            ),
            quantity=bucket.quantity,
            points=bucket.points,
            form=bucket.form,
            form_label=bucket.form_label,
            free=bucket.free,
            people=len(bucket.people),
            steps=len(bucket.steps),
        )
        for bucket in buckets.values()
    ]


def _previous(month: Month, tz: ZoneInfo) -> Month:
    year, number = (month.year - 1, 12) if month.month == 1 else (month.year, month.month - 1)
    return resolve_month(date(year, number, 1).strftime("%Y-%m"), tz=tz)


__all__ = [
    "CodeRow",
    "DepartmentRow",
    "Figures",
    "Overview",
    "PerformanceReportService",
    "PersonLine",
    "PersonReport",
    "TeamRow",
    "TimelineBar",
    "TimelineGroup",
    "TrendPoint",
]
