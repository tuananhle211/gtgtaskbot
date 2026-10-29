"""The points quota's settings ("định mức", 2026-10-29), kept in
``system_settings`` under ``performance_quota``; the arithmetic is in
:mod:`meobot.domain.performance.quota`. Read by every report, set by the
OWNER (the same right as the coefficients)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.performance.coefficients import may_set_coefficients
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.performance.errors import (
    PerformanceQuotaForbiddenError,
    PerformanceQuotaInvalidError,
)
from meobot.domain.performance.quota import MonthQuota, QuotaSettings, month_quota
from meobot.domain.units.models import UnitMembership

SETTINGS_KEY = "performance_quota"
#: name -> (label, lowest, highest)
LIMITS: dict[str, tuple[str, int, int]] = {
    "points_per_hour": ("Điểm cho 1 giờ công", 1, 600),
    "hours_per_day": ("Số giờ của 1 ngày công", 1, 24),
    "quota_pct": ("Tỷ lệ định mức", 1, 100),
}


class QuotaService:
    def __init__(self, session: AsyncSession, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit

    async def settings(self) -> QuotaSettings:
        row = await self._row()
        stored = row.value if row is not None and isinstance(row.value, dict) else {}
        defaults = QuotaSettings()
        values: dict[str, int] = {}
        for name, (_label, lowest, highest) in LIMITS.items():
            value = stored.get(name)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and lowest <= value <= highest
            ):
                values[name] = value
            else:
                values[name] = getattr(defaults, name)
        return QuotaSettings(**values)

    async def month(self, year: int, month: int, *, today: date) -> MonthQuota:
        return month_quota(year, month, await self.settings(), today=today)

    async def set(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        request_id: uuid.UUID,
        points_per_hour: int,
        hours_per_day: int,
        quota_pct: int,
    ) -> QuotaSettings:
        if not may_set_coefficients(membership):
            raise PerformanceQuotaForbiddenError()
        wanted = {
            "points_per_hour": points_per_hour,
            "hours_per_day": hours_per_day,
            "quota_pct": quota_pct,
        }
        for name, value in wanted.items():
            label, lowest, highest = LIMITS[name]
            if not lowest <= value <= highest:
                raise PerformanceQuotaInvalidError(
                    f"{label} phải từ {lowest} đến {highest}.", field=name
                )
        before = await self.settings()
        row = await self._row()
        if row is None:
            row = SystemSetting(
                key=SETTINGS_KEY,
                value=wanted,
                description="Điểm hiệu suất: điểm/giờ công, giờ/ngày công, tỷ lệ định mức.",
                updated_by=actor.user_id,
            )
            self._session.add(row)
        else:
            row.value = {**(row.value or {}), **wanted}
            row.version = int(row.version) + 1
            row.updated_by = actor.user_id
        await self._session.flush()
        if self._audit is not None:
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.PERFORMANCE_QUOTA_UPDATED.value,
                result=AuditResult.SUCCESS,
                entity_type="system_setting",
                entity_id=SETTINGS_KEY,
                before_data={
                    "points_per_hour": before.points_per_hour,
                    "hours_per_day": before.hours_per_day,
                    "quota_pct": before.quota_pct,
                },
                after_data=dict(wanted),
            )
        return QuotaSettings(**wanted)

    async def _row(self) -> SystemSetting | None:
        row: SystemSetting | None = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        return row


__all__ = ["SETTINGS_KEY", "QuotaService"]
