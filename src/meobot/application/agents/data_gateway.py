"""The door the Hermes agents read APM figures through (2026-11-09).

brain-mcp (on the agents' VPS) calls ``/api/agents/gateway`` with the shared
token and the agent's name (``X-Agent``); the agent reads only the areas ticked
for it (:mod:`meobot.domain.agents.data`), as the reports the Telegram
assistant gives the bosses - one source for every figure. Each read is audited
(agent, report, arguments; never the figures).

The ticks live in ``system_settings`` under :data:`SETTINGS_KEY` as
``{slug: [areas]}``; an agent not in it keeps :data:`DEFAULT_DATA_AREAS`.
"""

from __future__ import annotations

import hmac
import ipaddress
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.agents.access import AgentAccess
from meobot.application.assistant.reports import AssistantReportService, Report
from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.db.models.agent_chat import AgentProfile
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.agents.data import (
    DATA_AREA_INFO,
    DataArea,
    clean_areas,
    default_areas,
)
from meobot.domain.agents.errors import AgentNotFoundError
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.tools.base import NoArguments
from meobot.tools.report_tools import (
    KpiArguments,
    MonthArguments,
    OrderArguments,
    PerformanceArguments,
    RevenueArguments,
)

SETTINGS_KEY = "agent_data_areas"


class GatewayClosedError(Exception):
    """No token configured: the door does not exist (404)."""


class GatewayRefusedError(Exception):
    """Wrong token, an IP not on the list, or an unknown / inactive agent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GatewayAreaError(Exception):
    """The agent may not read this area, or asked it something it cannot answer."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def agent_actor(row: AgentProfile) -> Actor:
    """Who a report is run as: the company view (like the Telegram assistant),
    named after the agent so the audit log says who read."""
    return Actor(user_id=None, role=Role.OWNER, full_name=f"Agent {row.name} ({row.slug})")


def allowed_ip(settings: Settings, client_ip: str | None) -> bool:
    listed = [
        item.strip() for item in settings.agent_gateway_allowed_ips.split(",") if item.strip()
    ]
    if not listed:
        return True
    if not client_ip:
        return False
    try:
        address = ipaddress.ip_address(client_ip.strip())
    except ValueError:
        return False
    for item in listed:
        try:
            if address in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def token_ok(settings: Settings, presented: str | None) -> bool:
    if settings.agent_gateway_token is None:
        raise GatewayClosedError()
    expected = settings.agent_gateway_token.get_secret_value()
    if not expected:
        raise GatewayClosedError()
    if not presented:
        return False
    return hmac.compare_digest(presented.encode(), f"Bearer {expected}".encode())


Runner = Callable[[AssistantReportService, Actor, Any], Awaitable[Report]]


@dataclass(frozen=True, slots=True)
class _AreaSpec:
    arguments: type[BaseModel]
    run: Runner


_SPECS: dict[DataArea, _AreaSpec] = {
    DataArea.COMPANY: _AreaSpec(
        MonthArguments, lambda service, actor, args: service.company(actor, args.month)
    ),
    DataArea.ADS_REVENUE: _AreaSpec(
        RevenueArguments,
        lambda service, actor, args: service.ads_revenue(
            args.month, args.day, args.day_to, args.person
        ),
    ),
    DataArea.ADS_KPI_BUDGET: _AreaSpec(
        KpiArguments, lambda service, actor, args: service.ads_kpi_budget(args.month, args.person)
    ),
    DataArea.PERFORMANCE: _AreaSpec(
        PerformanceArguments,
        lambda service, actor, args: service.performance(args.month, args.department, args.person),
    ),
    DataArea.ORDERS: _AreaSpec(
        OrderArguments, lambda service, actor, args: service.orders(actor, args.month, args.state)
    ),
    DataArea.PR: _AreaSpec(
        MonthArguments, lambda service, actor, args: service.pr(actor, args.month)
    ),
    DataArea.RESOURCES: _AreaSpec(NoArguments, lambda service, actor, args: service.resources()),
}


def report_text(report: Report, base_url: str) -> str:
    """The report as Markdown for an agent (no "em - sếp" voice: the agent speaks for itself)."""
    parts = [f"**{report.title}**", *report.lines]
    parts.extend(f"_{note}_" for note in report.notes)
    if report.link:
        base = base_url.strip().rstrip("/")
        parts.append(
            f"Xem trên APM: {base}{report.link}" if base else f"Xem trên APM: {report.link}"
        )
    return "\n".join(parts)


class AgentDataService:
    def __init__(
        self, session: AsyncSession, settings: Settings, audit: AuditService | None = None
    ):
        self._session = session
        self._settings = settings
        self._audit = audit

    # --- who reads what ------------------------------------------------------------

    async def book(self) -> dict[str, tuple[DataArea, ...]]:
        """Every agent's areas: the stored ticks, else the defaults."""
        stored = await self._stored()
        slugs = (await self._session.scalars(select(AgentProfile.slug))).all()
        return {
            slug: clean_areas(stored[slug]) if slug in stored else default_areas(slug)
            for slug in slugs
        }

    async def areas_of(self, slug: str) -> tuple[DataArea, ...]:
        stored = await self._stored()
        return clean_areas(stored[slug]) if slug in stored else default_areas(slug)

    async def set_areas(
        self,
        access: AgentAccess,
        actor: Actor,
        slug: str,
        areas: list[str],
        *,
        request_id: uuid.UUID,
    ) -> tuple[DataArea, ...]:
        access.require_manage()
        if await self._agent(slug) is None:
            raise AgentNotFoundError()
        wanted = clean_areas(areas)
        before = await self.areas_of(slug)
        row = await self._row()
        values = {slug: [area.value for area in wanted]}
        if row is None:
            self._session.add(
                SystemSetting(
                    key=SETTINGS_KEY,
                    value=values,
                    description="Đội agent: dữ liệu APM mỗi agent được đọc.",
                    updated_by=actor.user_id,
                )
            )
        else:
            row.value = {**(row.value or {}), **values}
            row.version = int(row.version) + 1
            row.updated_by = actor.user_id
        await self._session.flush()
        if self._audit is not None:
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.AGENT_DATA_AREAS_UPDATED.value,
                result=AuditResult.SUCCESS,
                entity_type="agent",
                entity_id=slug,
                before_data={"areas": [area.value for area in before]},
                after_data={"areas": [area.value for area in wanted]},
            )
        return wanted

    # --- the door -------------------------------------------------------------------

    async def caller(self, slug: str | None) -> AgentProfile:
        """The active agent named in ``X-Agent``."""
        row = await self._agent((slug or "").strip().lower())
        if row is None or not row.active:
            raise GatewayRefusedError("agent_unknown", "Agent không có hoặc đã tắt.")
        return row

    async def report(
        self,
        agent: AgentProfile,
        area_value: str,
        arguments: dict[str, Any],
        *,
        request_id: uuid.UUID,
    ) -> dict[str, Any]:
        try:
            area = DataArea(area_value.upper())
        except ValueError:
            raise GatewayAreaError(
                "data_area_unknown",
                "Không có loại báo cáo này.",
                {"areas": [item.value for item in DataArea]},
            ) from None
        allowed = await self.areas_of(agent.slug)
        if area not in allowed:
            raise GatewayAreaError(
                "data_area_forbidden",
                f"Agent {agent.name} không được đọc {DATA_AREA_INFO[area][0]}.",
                {"allowed": [item.value for item in allowed]},
            )
        spec = _SPECS[area]
        cleaned = {key: value for key, value in arguments.items() if value not in (None, "")}
        try:
            args = spec.arguments.model_validate(cleaned)
        except ValidationError as exc:
            problems = [
                f"{'.'.join(str(p) for p in error['loc']) or 'tham số'}: {error['msg']}"
                for error in exc.errors()
            ]
            raise GatewayAreaError(
                "data_arguments_invalid", "Tham số không hợp lệ.", {"problems": problems}
            ) from None
        actor = agent_actor(agent)
        service = AssistantReportService(self._session, self._settings)
        report = await spec.run(service, actor, args)
        if self._audit is not None:
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.AGENT_DATA_READ.value,
                result=AuditResult.SUCCESS,
                entity_type="agent",
                entity_id=agent.slug,
                after_data={"area": area.value, "arguments": args.model_dump(exclude_none=True)},
            )
        base = self._settings.web_base_url.strip().rstrip("/")
        return {
            "area": area.value,
            "title": report.title,
            "text": report_text(report, base),
            "lines": list(report.lines),
            "notes": list(report.notes),
            "data": report.data,
            "link": f"{base}{report.link}" if report.link and base else report.link,
        }

    # --- storage ----------------------------------------------------------------------

    async def _agent(self, slug: str) -> AgentProfile | None:
        if not slug:
            return None
        row: AgentProfile | None = await self._session.scalar(
            select(AgentProfile).where(AgentProfile.slug == slug)
        )
        return row

    async def _row(self) -> SystemSetting | None:
        row: SystemSetting | None = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        return row

    async def _stored(self) -> dict[str, Any]:
        row = await self._row()
        return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


__all__ = [
    "SETTINGS_KEY",
    "AgentDataService",
    "GatewayAreaError",
    "GatewayClosedError",
    "GatewayRefusedError",
    "agent_actor",
    "allowed_ip",
    "report_text",
    "token_ok",
]
