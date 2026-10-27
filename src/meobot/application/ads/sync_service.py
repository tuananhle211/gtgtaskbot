"""Pulling Meta into the database (0078): ad accounts, daily spend, billing charges.

Run by the scheduled tasks (:mod:`meobot.tasks.ads`) - spend at 08:00 for
yesterday and again at 23:00 (Meta still corrects the day before), charges at
08:00 over the last five days - and by "Đồng bộ ngay" / "Kéo lại khoảng ngày".

Each sync **replaces** the rows of the days it read, for the accounts it read
- an account Meta could not answer for keeps what it had. The report it
returns says ``SUCCESS`` / ``PARTIAL`` / ``FAILED`` in the job monitor's own
words (:func:`meobot.application.cron.recorder.outcome_of`), so a half-failed
sync is retried and, failing again, reported to the OWNER.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.ads.connection_service import token_aad
from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.core.time import utcnow
from meobot.db.models.ads import AdsAccount, AdsChargeDaily, AdsConnection, AdsSpendDaily
from meobot.domain.ads.models import brand_of
from meobot.integrations.meta_ads.client import MetaAdsClient, MetaAdsError

_VN = ZoneInfo("Asia/Ho_Chi_Minh")
#: ``INSERT ... ON CONFLICT DO NOTHING`` per dialect (production, unit tests).
_INSERTS: dict[str, Any] = {"postgresql": pg_insert, "sqlite": sqlite_insert}


@dataclass(slots=True)
class SyncReport:
    kind: str
    since: date
    until: date
    connections: int = 0
    accounts: int = 0
    rows: int = 0
    #: ``"<BM name>" -> why`` for a BM that could not be read at all.
    failed_connections: dict[str, str] = field(default_factory=dict)
    #: ``account id -> why`` for single accounts.
    failed_accounts: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.connections and len(self.failed_connections) == self.connections:
            return "FAILED"
        if self.failed_connections or self.failed_accounts:
            return "PARTIAL"
        return "SUCCESS"

    @property
    def message(self) -> str:
        label = "chi phí" if self.kind == "spend" else "hoá đơn"
        head = (
            f"Đồng bộ {label} {self.since:%d/%m} - {self.until:%d/%m}: "
            f"{self.connections} BM, {self.accounts} TK, {self.rows} dòng"
        )
        problems = [f"BM {name}: {why}" for name, why in self.failed_connections.items()]
        if self.failed_accounts:
            sample = next(iter(self.failed_accounts.values()))
            problems.append(f"{len(self.failed_accounts)} TK lỗi (ví dụ: {sample})")
        return head + (". " + "; ".join(problems) if problems else ".")

    def as_task_result(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message[:480],
            "connections": self.connections,
            "accounts": self.accounts,
            "rows": self.rows,
        }


def vn_today() -> date:
    return utcnow().astimezone(_VN).date()


class AdsSyncService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        client: MetaAdsClient,
        secret_box: Callable[[], SecretBox] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._client = client
        self._secret_box = secret_box or (lambda: build_secret_box(settings))

    async def _connections(self, connection_id: uuid.UUID | None) -> list[AdsConnection]:
        query = select(AdsConnection).where(AdsConnection.active.is_(True))
        if connection_id is not None:
            query = query.where(AdsConnection.id == connection_id)
        return list(await self._session.scalars(query.order_by(AdsConnection.name)))

    def _token(self, row: AdsConnection) -> str:
        try:
            return self._secret_box().decrypt(row.token_encrypted, aad=token_aad(row.id))
        except ConfigurationError as error:
            raise MetaAdsError("Server chưa có khoá mã hoá nên không đọc được token.") from error
        except Exception as error:  # a damaged or foreign envelope: this BM only
            raise MetaAdsError("Không giải mã được token của BM này - nhập lại token.") from error

    async def discover(self, row: AdsConnection, token: str) -> list[AdsAccount]:
        """Every ad account of the BM, upserted; the brand follows the name
        unless somebody set it by hand."""
        now = utcnow()
        seen = await self._client.ad_accounts(token, row.business_id)
        ids = [item.account_id for item in seen]
        existing = await self._accounts(ids)
        new = [item for item in seen if item.account_id not in existing]
        insert = _INSERTS.get(self._session.get_bind().dialect.name)
        if new and insert is not None:
            # "Đồng bộ ngay" for spend and charges run side by side: on a new BM
            # both meet the same new accounts, and the second insert must not
            # fail on the first one's rows (it did, 27/10).
            await self._session.execute(
                insert(AdsAccount)
                .values(
                    [
                        {
                            "account_id": item.account_id,
                            "connection_id": row.id,
                            "name": item.name[:300],
                            "brand": brand_of(item.name),
                        }
                        for item in new
                    ]
                )
                .on_conflict_do_nothing(index_elements=["account_id"])
            )
            existing = await self._accounts(ids)
        accounts: list[AdsAccount] = []
        for item in seen:
            account = existing.get(item.account_id)
            if account is None:
                account = AdsAccount(
                    account_id=item.account_id,
                    connection_id=row.id,
                    name=item.name[:300],
                    brand=brand_of(item.name),
                )
                self._session.add(account)
            account.connection_id = row.id
            account.name = item.name[:300]
            account.status = item.status
            account.currency = item.currency
            if item.card_last4:
                account.card_last4 = item.card_last4
            account.last_seen_at = now
            if not account.brand_custom:
                account.brand = brand_of(item.name)
            accounts.append(account)
        row.account_count = len(accounts)
        await self._session.flush()
        return accounts

    async def _accounts(self, ids: list[str]) -> dict[str, AdsAccount]:
        rows = await self._session.scalars(
            select(AdsAccount)
            .where(AdsAccount.account_id.in_(ids))
            .execution_options(populate_existing=True)
        )
        return {account.account_id: account for account in rows}

    async def sync_spend(
        self, since: date, until: date, *, connection_id: uuid.UUID | None = None
    ) -> SyncReport:
        report = SyncReport(kind="spend", since=since, until=until)
        for row in await self._connections(connection_id):
            report.connections += 1
            try:
                token = self._token(row)
                accounts = await self.discover(row, token)
                ids = [account.account_id for account in accounts]
                pulled = await self._client.daily_insights(token, ids, since, until)
            except MetaAdsError as error:
                report.failed_connections[row.name] = error.message
                self._mark(row, "spend", ok=False, message=error.message)
                continue
            read = [account for account in ids if account not in pulled.errors]
            totals: dict[tuple[date, str], list[Any]] = defaultdict(lambda: [Decimal(0), 0, 0])
            for item in pulled.rows:
                bucket = totals[(item.day, item.account_id)]
                bucket[0] += item.spend
                bucket[1] += item.messages
                bucket[2] += item.phones
            if read:
                await self._session.execute(
                    delete(AdsSpendDaily).where(
                        AdsSpendDaily.day >= since,
                        AdsSpendDaily.day <= until,
                        AdsSpendDaily.account_id.in_(read),
                    )
                )
            now = utcnow()
            for (day, account), (spend, messages, phones) in totals.items():
                if not (spend or messages or phones):
                    continue
                self._session.add(
                    AdsSpendDaily(
                        day=day,
                        account_id=account,
                        spend=spend,
                        messages=messages,
                        phones=phones,
                        synced_at=now,
                    )
                )
                report.rows += 1
            report.accounts += len(read)
            report.failed_accounts.update(pulled.errors)
            self._mark(
                row,
                "spend",
                ok=not pulled.errors,
                message=f"{len(read)}/{len(ids)} TK"
                + (f" · {len(pulled.errors)} TK lỗi" if pulled.errors else ""),
            )
        await self._session.flush()
        return report

    async def sync_charges(
        self, since: date, until: date, *, connection_id: uuid.UUID | None = None
    ) -> SyncReport:
        report = SyncReport(kind="charges", since=since, until=until)
        for row in await self._connections(connection_id):
            report.connections += 1
            try:
                token = self._token(row)
                accounts = await self.discover(row, token)
                ids = [account.account_id for account in accounts]
                # Meta's ``until`` is exclusive; one day either side, then cut
                # by the Vietnam day.
                pulled = await self._client.billing_charges(
                    token, ids, since - timedelta(days=1), until + timedelta(days=1)
                )
            except MetaAdsError as error:
                report.failed_connections[row.name] = error.message
                self._mark(row, "charges", ok=False, message=error.message)
                continue
            read = [account for account in ids if account not in pulled.errors]
            totals: dict[tuple[date, str], list[Any]] = defaultdict(lambda: [Decimal(0), 0])
            for charge in pulled.rows:
                day = charge.at.astimezone(_VN).date()
                if not since <= day <= until:
                    continue
                bucket = totals[(day, charge.account_id)]
                bucket[0] += charge.amount
                bucket[1] += 1
            if read:
                await self._session.execute(
                    delete(AdsChargeDaily).where(
                        AdsChargeDaily.day >= since,
                        AdsChargeDaily.day <= until,
                        AdsChargeDaily.account_id.in_(read),
                    )
                )
            now = utcnow()
            for (day, account), (amount, count) in totals.items():
                self._session.add(
                    AdsChargeDaily(
                        day=day, account_id=account, amount=amount, charges=count, synced_at=now
                    )
                )
                report.rows += 1
            report.accounts += len(read)
            report.failed_accounts.update(pulled.errors)
            self._mark(
                row,
                "charges",
                ok=not pulled.errors,
                message=f"{len(read)}/{len(ids)} TK"
                + (f" · {len(pulled.errors)} TK lỗi" if pulled.errors else ""),
            )
        await self._session.flush()
        return report

    @staticmethod
    def _mark(row: AdsConnection, kind: str, *, ok: bool, message: str) -> None:
        now = utcnow()
        if kind == "spend":
            row.last_spend_sync_at = now
            row.last_spend_sync_ok = ok
            row.last_spend_sync_message = message[:300]
        else:
            row.last_charge_sync_at = now
            row.last_charge_sync_ok = ok
            row.last_charge_sync_message = message[:300]


def spend_window(today: date) -> tuple[date, date]:
    """08:00 and 23:00 both read yesterday (chốt 27/10)."""
    yesterday = today - timedelta(days=1)
    return yesterday, yesterday


def charge_window(today: date) -> tuple[date, date]:
    """The last five days; on the 1st-3rd, the whole previous month as well
    (charges land late)."""
    since = today - timedelta(days=4)
    if today.day <= 3:
        last_month_end = today.replace(day=1) - timedelta(days=1)
        since = min(since, last_month_end.replace(day=1))
    return since, today


__all__ = ["AdsSyncService", "SyncReport", "charge_window", "spend_window", "vn_today"]
