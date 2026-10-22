"""The summary report and the warning list ("Báo cáo tổng hợp").

* department x type, with how many are handed over ("Đã bàn giao") and the
  rate;
* how many resources stood in each status at the end of a month, read from
  the status history (the last line dated inside or before the month), so a
  month that has passed keeps its figures;
* the warning list: rows at a leaver or somebody who changed department,
  rows past the review threshold, rows in a status that needs attention or is
  a loss - each row once, with every alert it carries.

Everything is counted inside what the viewer may see; the report itself
needs the ``REPORT`` permission (or seeing everything).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from meobot.application.resources.access import ResourceAccess, ResourceForbiddenError
from meobot.application.resources.service import ResourceRow, ResourceService
from meobot.db.models.department import Department
from meobot.db.models.resource import Resource, ResourceOption, ResourceStatusHistory
from meobot.domain.resources.models import (
    HANDED_OVER_CODE,
    AlertKind,
    OptionKind,
    StatusGroup,
)


@dataclass(frozen=True, slots=True)
class DepartmentLine:
    department: Department
    by_type: dict[uuid.UUID, int]
    total: int
    handed_over: int

    @property
    def handover_rate(self) -> float:
        return round(self.handed_over / self.total, 4) if self.total else 0.0


@dataclass(frozen=True, slots=True)
class StatusCount:
    status: ResourceOption
    count: int


@dataclass(frozen=True, slots=True)
class Summary:
    month: str
    types: list[ResourceOption]
    departments: list[DepartmentLine]
    total: int
    handed_over: int
    by_status: list[StatusCount]
    by_group: dict[StatusGroup, int]


@dataclass(frozen=True, slots=True)
class Warnings:
    rows: list[ResourceRow]
    counts: dict[AlertKind, int]
    review_days: int


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """``YYYY-MM`` → the first instant of the month and of the next, UTC."""
    try:
        year, number = (int(part) for part in month.split("-", 1))
        start = date(year, number, 1)
    except (ValueError, TypeError) as error:
        raise ValueError(month) from error
    end = date(year + (number // 12), number % 12 + 1, 1)
    return (
        datetime(start.year, start.month, 1, tzinfo=UTC),
        datetime(end.year, end.month, 1, tzinfo=UTC),
    )


class ResourceReportService:
    def __init__(self, service: ResourceService) -> None:
        self._service = service
        self._session = service.session

    async def _scoped(self, access: ResourceAccess) -> list[Resource]:
        if not (access.report or access.view_all):
            raise ResourceForbiddenError("Bạn chưa được xem báo cáo tài nguyên.")
        scope = self._service._scope(access)
        statement = select(Resource).where(Resource.deleted_at.is_(None)).order_by(Resource.code)
        if scope is not None:
            statement = statement.where(scope)
        return list((await self._session.scalars(statement)).all())

    async def summary(self, access: ResourceAccess, month: str) -> Summary:
        start, end = month_bounds(month)
        resources = await self._scoped(access)
        catalog = await self._service.catalog()
        rows = await self._service.rows_of(resources)
        types = catalog.of_kind(OptionKind.TYPE, include_inactive=True)
        handed = catalog.by_code(OptionKind.HANDOVER, HANDED_OVER_CODE)
        lines: dict[uuid.UUID, DepartmentLine] = {}
        for row in rows:
            line = lines.get(row.department.id)
            by_type = dict(line.by_type) if line else {}
            by_type[row.type.id] = by_type.get(row.type.id, 0) + 1
            lines[row.department.id] = DepartmentLine(
                department=row.department,
                by_type=by_type,
                total=(line.total if line else 0) + 1,
                handed_over=(line.handed_over if line else 0)
                + (1 if handed is not None and row.handover.id == handed.id else 0),
            )
        ordered = sorted(
            lines.values(), key=lambda line: (line.department.sort_order, line.department.code)
        )
        used = {type_id for line in ordered for type_id in line.by_type}
        shown_types = [option for option in types if option.active or option.id in used]

        # The status each resource stood in at the end of the month.
        ids = [resource.id for resource in resources]
        standing: dict[uuid.UUID, uuid.UUID] = {}
        if ids:
            history = (
                await self._session.execute(
                    select(
                        ResourceStatusHistory.resource_id,
                        ResourceStatusHistory.to_status_id,
                        ResourceStatusHistory.created_at,
                        ResourceStatusHistory.id,
                    )
                    .where(
                        ResourceStatusHistory.resource_id.in_(ids),
                        ResourceStatusHistory.created_at < end,
                        ResourceStatusHistory.review_only.is_(False),
                    )
                    .order_by(ResourceStatusHistory.created_at, ResourceStatusHistory.id)
                )
            ).all()
            for resource_id, status_id, _at, _id in history:
                standing[resource_id] = status_id  # the last one wins
        counts: dict[uuid.UUID, int] = {}
        for status_id in standing.values():
            counts[status_id] = counts.get(status_id, 0) + 1
        by_status = [
            StatusCount(status=option, count=counts.get(option.id, 0))
            for option in catalog.of_kind(OptionKind.STATUS, include_inactive=True)
            if option.active or option.id in counts
        ]
        by_group: dict[StatusGroup, int] = dict.fromkeys(StatusGroup, 0)
        for item in by_status:
            if item.status.status_group is not None:
                by_group[item.status.status_group] += item.count
        return Summary(
            month=f"{start.year:04d}-{start.month:02d}",
            types=shown_types,
            departments=ordered,
            total=sum(line.total for line in ordered),
            handed_over=sum(line.handed_over for line in ordered),
            by_status=by_status,
            by_group=by_group,
        )

    async def warnings(self, access: ResourceAccess) -> Warnings:
        resources = await self._scoped(access)
        rows = [row for row in await self._service.rows_of(resources) if row.alerts]
        counts: dict[AlertKind, int] = dict.fromkeys(AlertKind, 0)
        for row in rows:
            for alert in row.alerts:
                counts[alert.kind] += 1
        rows.sort(key=lambda row: (not row.flagged, row.resource.code))
        return Warnings(rows=rows, counts=counts, review_days=await self._service.review_days())


def previous_month(today: date) -> str:
    first = today.replace(day=1) - timedelta(days=1)
    return f"{first.year:04d}-{first.month:02d}"


__all__ = [
    "DepartmentLine",
    "ResourceReportService",
    "StatusCount",
    "Summary",
    "Warnings",
    "month_bounds",
    "previous_month",
]
