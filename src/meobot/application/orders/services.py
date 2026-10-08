"""One wiring for the order engine, shared by the API and anything else that
calls it, so two clients can never disagree about the rules."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.orders.code_service import OrderCodeService
from meobot.application.orders.command_service import OrderCommandService
from meobot.application.orders.notifications import OrderNotificationService
from meobot.application.orders.query_service import OrderQueryService
from meobot.application.orders.work_recorder import OrderWorkRecorder
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings


@dataclass(frozen=True, slots=True)
class OrderServices:
    session: AsyncSession
    settings: Settings
    directory: UnitDirectoryService
    codes: OrderCodeService
    notifications: OrderNotificationService
    kpi: OrderWorkRecorder
    commands: OrderCommandService
    queries: OrderQueryService
    pr: PrServices


def build_order_services(
    session: AsyncSession, settings: Settings, *, pr: PrServices | None = None
) -> OrderServices:
    pr_services = pr or build_pr_services(session, settings)
    audit = AuditService(session)
    directory = UnitDirectoryService(session)
    codes = OrderCodeService(session, settings)
    notifications = OrderNotificationService(session, settings)
    kpi = OrderWorkRecorder(session, pr_services.work_results)
    commands = OrderCommandService(
        session,
        settings,
        audit=audit,
        directory=directory,
        codes=codes,
        notifications=notifications,
        kpi=kpi,
    )
    queries = OrderQueryService(session, settings, directory)
    return OrderServices(
        session=session,
        settings=settings,
        directory=directory,
        codes=codes,
        notifications=notifications,
        kpi=kpi,
        commands=commands,
        queries=queries,
        pr=pr_services,
    )


__all__ = ["OrderServices", "build_order_services"]
