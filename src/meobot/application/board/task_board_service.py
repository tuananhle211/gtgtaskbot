"""Which source answers, and the OWNER's view across both.

A caller names a unit; the service checks they may see it, builds that unit's
source and asks it. ``ALL`` is the OWNER's convenience: both sources are
asked with the same filters and the pages are merged in the board's sort
order. Its total is exact; its paging is a merge of two independently paged
lists, which is good enough for a person skimming both departments and is
documented as such - the per-unit tabs are the precise view.
"""

from __future__ import annotations

import heapq
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.board.sources import AdsBoardSource, BoardSource, PrBoardSource
from meobot.application.board.unified import unify_row
from meobot.application.orders.scope import OrderScope
from meobot.application.pr_services import PrServices
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings
from meobot.domain.board.models import BoardPage, BoardQuery, Phase, TaskRow
from meobot.domain.identity.models import Actor
from meobot.domain.units.errors import UnitNotFoundError
from meobot.domain.units.models import UnitCode, UnitMembership, UnitSettings

#: The OWNER's "both units" selector on the wire.
ALL_UNITS = "ALL"


class TaskBoardService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        directory: UnitDirectoryService,
        pr: PrServices,
    ) -> None:
        self._session = session
        self._settings = settings
        self._directory = directory
        self._pr = pr

    async def membership(self, actor: Actor) -> UnitMembership:
        return await self._directory.membership_for(actor)

    async def resolve_units(self, actor: Actor, unit: str | None) -> tuple[UnitCode, ...]:
        """Which units a request for ``unit`` may read. Outside → 404."""
        membership = await self.membership(actor)
        visible = membership.visible_units()
        if unit is None or not unit.strip():
            if not visible:
                raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})
            return (visible[0],)
        if unit.strip().upper() == ALL_UNITS:
            if not membership.is_owner:
                raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})
            return tuple(visible)
        try:
            code = UnitCode(unit.strip().upper())
        except ValueError as error:
            raise UnitNotFoundError(
                "Không tìm thấy.", details={"reason": "unit_not_visible"}
            ) from error
        if not membership.has(code):
            raise UnitNotFoundError("Không tìm thấy.", details={"reason": "unit_not_visible"})
        return (code,)

    @staticmethod
    def _narrow(codes: tuple[UnitCode, ...], query: BoardQuery) -> tuple[UnitCode, ...]:
        """A video-kind filter is an Ads filter: across both units it leaves
        PR out rather than listing every PR item as if it matched."""
        if query.video_kind_id is not None and len(codes) > 1:
            return tuple(code for code in codes if code is UnitCode.ADS)
        return codes

    async def source_for(self, actor: Actor, code: UnitCode) -> BoardSource:
        if code is UnitCode.PR:
            return PrBoardSource(self._session, self._settings, self._pr.queries)
        membership = await self.membership(actor)
        unit = await self._directory.unit(UnitCode.ADS)
        unit_settings = UnitSettings.model_validate(unit.settings or {})
        scope = OrderScope.for_actor(membership, unit_settings, unit_id=unit.id)
        return AdsBoardSource(
            self._session, self._settings, scope=scope, unit_settings=unit_settings
        )

    async def page(self, actor: Actor, *, unit: str | None, query: BoardQuery) -> BoardPage:
        found = await self._page(actor, unit=unit, query=query)
        if (unit or "").strip().upper() != ALL_UNITS:
            return found
        # "Tất cả": one set of step columns for both units.
        return replace(found, rows=tuple(unify_row(row) for row in found.rows))

    async def _page(self, actor: Actor, *, unit: str | None, query: BoardQuery) -> BoardPage:
        codes = self._narrow(await self.resolve_units(actor, unit), query)
        if len(codes) == 1:
            source = await self.source_for(actor, codes[0])
            return await source.rows(actor, query)
        # Each unit is asked for the same prefix (rows 0 .. offset+limit) and the
        # prefixes are merged head by head. The merge only ever compares the
        # current head of each list, so the first n merged rows depend only on
        # the first n rows of each unit: page 2 continues page 1 exactly, with
        # nothing repeated or skipped, even though PR keeps its own ordering.
        prefix = replace(query, offset=0, limit=query.offset + query.limit)
        pages = [await (await self.source_for(actor, code)).rows(actor, prefix) for code in codes]
        merged = heapq.merge(
            *(page.rows for page in pages),
            key=lambda row: (not row.is_priority, -row.created_at.timestamp()),
        )
        rows = list(merged)[query.offset : query.offset + query.limit]
        return BoardPage(
            rows=tuple(rows),
            total=sum(page.total for page in pages),
            limit=query.limit,
            offset=query.offset,
        )

    async def count_by_phase(
        self, actor: Actor, *, unit: str | None, query: BoardQuery
    ) -> dict[Phase, int]:
        codes = self._narrow(await self.resolve_units(actor, unit), query)
        counts: dict[Phase, int] = dict.fromkeys(Phase, 0)
        for code in codes:
            source = await self.source_for(actor, code)
            for phase, count in (await source.count_by_phase(actor, query)).items():
                counts[phase] += count
        return counts


def row_unit(row: TaskRow) -> UnitCode:
    return row.unit


__all__ = ["ALL_UNITS", "TaskBoardService", "row_unit"]
