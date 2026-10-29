"""Ngân sách chạy ads theo tuần (0082): the Trưởng phòng ADS sets each
marketer's ad budget at the start of the month in four slots - days 1-7,
8-14, 15-21 and the 22nd to the month's end - and the dashboards set it
against what was spent in that week.

A week's status (:func:`budget_status`):

* ``OVER`` - spent more than the budget (as soon as it happens, even mid-week);
* ``REACHED`` - a finished week that spent at least :data:`REACHED_RATE` of it;
* ``UNDER`` - a finished week below that ("Chưa đến ngưỡng");
* ``RUNNING`` - the current week, still within budget;
* ``UPCOMING`` - not started yet;
* ``NO_BUDGET`` - nothing set for the week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

#: Weeks 1-3 are 7 days; week 4 takes the rest of the month.
WEEKS = 4
WEEK_DAYS = 7
#: A finished week that spent 90% of its budget has reached it.
REACHED_RATE = 0.9


@dataclass(frozen=True, slots=True)
class BudgetWeek:
    #: 1-4.
    index: int
    start: date
    #: Inclusive.
    end: date

    @property
    def label(self) -> str:
        return f"Tuần {self.index} · {self.start:%d/%m} - {self.end:%d/%m}"


def budget_weeks(month: date) -> tuple[BudgetWeek, ...]:
    """The month's four budget weeks."""
    first = month.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    weeks = []
    for index in range(1, WEEKS + 1):
        start = first + timedelta(days=(index - 1) * WEEK_DAYS)
        end = last if index == WEEKS else start + timedelta(days=WEEK_DAYS - 1)
        weeks.append(BudgetWeek(index, start, end))
    return tuple(weeks)


class BudgetStatus(StrEnum):
    OVER = "OVER"
    REACHED = "REACHED"
    UNDER = "UNDER"
    RUNNING = "RUNNING"
    UPCOMING = "UPCOMING"
    NO_BUDGET = "NO_BUDGET"


BUDGET_STATUS_LABELS: dict[BudgetStatus, str] = {
    BudgetStatus.OVER: "Vượt ngưỡng",
    BudgetStatus.REACHED: "Đạt ngưỡng",
    BudgetStatus.UNDER: "Chưa đến ngưỡng",
    BudgetStatus.RUNNING: "Đang chạy",
    BudgetStatus.UPCOMING: "Chưa tới tuần",
    BudgetStatus.NO_BUDGET: "Chưa đặt ngân sách",
}


def budget_status(
    spend: float, budget: float | None, week: BudgetWeek, today: date
) -> BudgetStatus:
    if budget is None:
        return BudgetStatus.NO_BUDGET
    if today < week.start:
        return BudgetStatus.UPCOMING
    if spend > budget:
        return BudgetStatus.OVER
    if today <= week.end:
        return BudgetStatus.RUNNING
    if budget == 0 or spend >= budget * REACHED_RATE:
        return BudgetStatus.REACHED
    return BudgetStatus.UNDER


__all__ = [
    "BUDGET_STATUS_LABELS",
    "REACHED_RATE",
    "WEEKS",
    "BudgetStatus",
    "BudgetWeek",
    "budget_status",
    "budget_weeks",
]
