"""Reading a Business Manager's ad accounts, daily insights and billing charges.

The calls are the ones the department's Apps Script proved over months
(``docs/report/Mã.gs``, ``testHoadon.gs``):

* accounts: ``/{business_id}/owned_ad_accounts`` + ``/client_ad_accounts``;
* insights: ``act_<id>/insights?level=account&time_increment=1`` - spend, the
  conversations started ("Mess") and ``omni_purchase`` ("SĐT");
* charges: ``act_<id>/activities`` events ``ad_account_billing_charge``, the
  amount in ``extra_data.new_value``, de-duplicated on ``transaction_id``.

Per-account calls go through the Graph batch endpoint (50 at a time, 10 for
the heavier ``/activities``). One account failing never fails the others: it
comes back in ``errors``. An account Meta answers "try again" for (the batch
timed out - code 960 -, call limits) is asked again in a smaller batch.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from meobot.domain.ads.models import ACTION_MESSAGES, ACTION_PHONES

GRAPH_VERSION = "v25.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
BATCH_SIZE = 50
#: ``/activities`` reads a month of history per account: 50 at once made Meta
#: abort part of the batch (code 960) on a 181-account BM.
ACTIVITY_BATCH_SIZE = 10
TIMEOUT_SECONDS = 60.0
#: Graph's "try again" answers: unknown / temporary (1, 2), call limits, and
#: 960 = a batch request aborted because the whole batch timed out.
TRANSIENT_CODES = frozenset({1, 2, 4, 17, 32, 613, 960, 80000, 80004})
NO_ANSWER = "Meta không trả lời tài khoản này."


class MetaAdsError(Exception):
    """A refusal from Meta, in words a person can act on."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def explain(error: dict[str, Any]) -> MetaAdsError:
    """Graph's ``error`` object -> a Vietnamese reason."""
    code = error.get("code")
    sub = error.get("error_subcode")
    raw = str(error.get("message") or "")
    if code == 190:
        message = "Token không hợp lệ hoặc đã hết hạn - tạo token System User mới."
    elif code in (10, 200, 294) or "permission" in raw.lower():
        message = "Token thiếu quyền ads_read / business_management cho BM này."
    elif code in (4, 17, 32, 613, 80000, 80004):
        message = "Meta đang giới hạn số lần gọi - hệ thống sẽ thử lại sau."
    elif code == 960:
        message = "Meta hết thời gian xử lý lô yêu cầu - hệ thống sẽ thử lại sau."
    elif code == 100 and sub in (33, None) and "Unsupported get request" in raw:
        message = "Không tìm thấy BM với Business ID này, hoặc token không thuộc BM đó."
    else:
        message = f"Meta báo lỗi: {raw[:200]}" if raw else "Meta báo lỗi không rõ."
    return MetaAdsError(message, code=code if isinstance(code, int) else None)


@dataclass(frozen=True, slots=True)
class AdAccountInfo:
    account_id: str
    name: str
    status: int | None = None
    currency: str | None = None
    #: The last four digits of the card paying for it, when Meta says.
    card_last4: str | None = None


def card_last4(details: Any) -> str | None:
    """Four digits out of ``funding_source_details.display_string`` ("Visa ••••
    4037", "xxxx 4037"...), as the Sheet's ``_card4`` read them."""
    if not isinstance(details, dict):
        return None
    text = str(details.get("display_string") or details.get("card_last4") or "").strip()
    if not text:
        return None
    masked = re.search(r"[•*xX·.\-\s]\s*(\d{4})\b", text)
    if masked:
        return masked.group(1)
    groups = re.findall(r"\b\d{4}\b", text)
    if groups:
        return str(groups[-1])
    digits = re.sub(r"\D", "", text)
    return digits[-4:] if len(digits) >= 4 else None


@dataclass(frozen=True, slots=True)
class DailyInsight:
    account_id: str
    day: date
    spend: Decimal
    messages: int
    phones: int


@dataclass(frozen=True, slots=True)
class BillingCharge:
    account_id: str
    at: datetime
    amount: Decimal
    transaction_id: str | None = None


@dataclass(slots=True)
class Pulled[T]:
    rows: list[T] = field(default_factory=list)
    #: ``account_id -> why`` for the accounts that could not be read.
    errors: dict[str, str] = field(default_factory=dict)


class MetaAdsClient(Protocol):
    async def business_name(self, token: str, business_id: str) -> str: ...

    async def ad_accounts(self, token: str, business_id: str) -> list[AdAccountInfo]: ...

    async def daily_insights(
        self, token: str, account_ids: Sequence[str], since: date, until: date
    ) -> Pulled[DailyInsight]: ...

    async def billing_charges(
        self, token: str, account_ids: Sequence[str], since: date, until: date
    ) -> Pulled[BillingCharge]: ...


#: One batch answer: the body, else why not and whether asking again may help.
_Answer = tuple[dict[str, Any] | None, str | None, bool]


def _action(values: Any, kind: str) -> int:
    for item in values or []:
        if isinstance(item, dict) and item.get("action_type") == kind:
            try:
                return int(Decimal(str(item.get("value") or 0)))
            except InvalidOperation:
                return 0
    return 0


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


def _time_range(since: date, until: date) -> str:
    return json.dumps({"since": since.isoformat(), "until": until.isoformat()})


class GraphMetaAdsClient:
    """The real thing, over ``httpx``."""

    def __init__(
        self, *, http: httpx.AsyncClient | None = None, retries: int = 2, pause: float = 2.0
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._retries = retries
        #: Seconds to wait before a retry, times the attempt number.
        self._pause = pause

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # httpx replaces a URL's whole query with ``params`` - even an empty
        # dict - which stripped the token and cursor off Meta's "next" links.
        response = await self._http.get(url, params=params or None)
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            raise explain(data["error"])
        if not isinstance(data, dict):
            raise MetaAdsError("Meta trả về dữ liệu không đọc được.")
        return data

    async def _pages(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = await self._get(url, params)
        for _ in range(200):
            rows.extend(item for item in data.get("data") or [] if isinstance(item, dict))
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                break
            data = await self._get(str(next_url))
        return rows

    async def _batch(self, token: str, relative_urls: Sequence[str]) -> list[_Answer]:
        """One answer per URL: ``(body, None, _)`` or ``(None, why, transient)``."""
        batch = [{"method": "GET", "relative_url": url} for url in relative_urls]
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = await self._http.post(
                    f"{GRAPH_URL}/",
                    data={
                        "access_token": token,
                        "batch": json.dumps(batch),
                        "include_headers": "false",
                    },
                )
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    error = explain(data["error"])
                    if error.code in TRANSIENT_CODES and attempt < self._retries:
                        await asyncio.sleep(self._pause * (attempt + 1))
                        continue
                    raise error
                if not isinstance(data, list):
                    raise MetaAdsError("Meta trả về dữ liệu không đọc được.")
                answers: list[_Answer] = []
                for item in data:
                    if not isinstance(item, dict) or not item.get("body"):
                        answers.append((None, NO_ANSWER, True))
                        continue
                    try:
                        body = json.loads(item["body"])
                    except (TypeError, ValueError):
                        answers.append((None, "Meta trả về dữ liệu không đọc được.", False))
                        continue
                    if item.get("code") != 200 or "error" in body:
                        error = explain(body.get("error") or {})
                        answers.append((None, error.message, error.code in TRANSIENT_CODES))
                    else:
                        answers.append((body, None, False))
                return answers
            except httpx.HTTPError as error:
                last = error
                await asyncio.sleep(self._pause * (attempt + 1))
        raise MetaAdsError(f"Không gọi được Meta: {last}") from last

    async def _each(
        self, token: str, account_ids: Sequence[str], url_of: Any, size: int
    ) -> dict[str, tuple[dict[str, Any] | None, str | None]]:
        """Every account's answer, batched ``size`` at a time; the accounts Meta
        answered "try again" for are asked again in half-size batches, up to
        ``retries`` more rounds."""
        answers: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
        pending = list(account_ids)
        for round_ in range(self._retries + 1):
            again: list[str] = []
            for start in range(0, len(pending), size):
                chunk = pending[start : start + size]
                results = await self._batch(token, [url_of(account) for account in chunk])
                for index, account in enumerate(chunk):
                    body, error, transient = (
                        results[index] if index < len(results) else (None, NO_ANSWER, True)
                    )
                    answers[account] = (body, error)
                    if transient:
                        again.append(account)
            if not again or round_ == self._retries:
                break
            pending = again
            size = max(1, size // 2)
            await asyncio.sleep(self._pause * (round_ + 1))
        return answers

    async def business_name(self, token: str, business_id: str) -> str:
        data = await self._get(
            f"{GRAPH_URL}/{business_id}", {"fields": "id,name", "access_token": token}
        )
        return str(data.get("name") or business_id)

    async def ad_accounts(self, token: str, business_id: str) -> list[AdAccountInfo]:
        found: dict[str, AdAccountInfo] = {}
        params = {
            "fields": "account_id,name,account_status,currency,funding_source_details",
            "limit": 200,
            "access_token": token,
        }
        for edge in ("owned_ad_accounts", "client_ad_accounts"):
            for row in await self._pages(f"{GRAPH_URL}/{business_id}/{edge}", params):
                account_id = str(row.get("account_id") or "").strip()
                if not account_id or account_id in found:
                    continue
                status = row.get("account_status")
                found[account_id] = AdAccountInfo(
                    account_id=account_id,
                    name=str(row.get("name") or f"act_{account_id}"),
                    status=status if isinstance(status, int) else None,
                    currency=str(row.get("currency") or "") or None,
                    card_last4=card_last4(row.get("funding_source_details")),
                )
        return list(found.values())

    async def daily_insights(
        self, token: str, account_ids: Sequence[str], since: date, until: date
    ) -> Pulled[DailyInsight]:
        pulled: Pulled[DailyInsight] = Pulled()
        fields = "spend,actions"
        answers = await self._each(
            token,
            account_ids,
            lambda account: (
                f"act_{account}/insights?level=account&time_increment=1&fields={fields}"
                f"&time_range={_time_range(since, until)}&limit=500"
            ),
            BATCH_SIZE,
        )
        for account, (body, error) in answers.items():
            if error is not None or body is None:
                pulled.errors[account] = error or "Không đọc được."
                continue
            for row in body.get("data") or []:
                try:
                    day = date.fromisoformat(str(row.get("date_start")))
                except ValueError:
                    continue
                pulled.rows.append(
                    DailyInsight(
                        account_id=account,
                        day=day,
                        spend=_money(row.get("spend")),
                        messages=_action(row.get("actions"), ACTION_MESSAGES),
                        phones=_action(row.get("actions"), ACTION_PHONES),
                    )
                )
        return pulled

    async def billing_charges(
        self, token: str, account_ids: Sequence[str], since: date, until: date
    ) -> Pulled[BillingCharge]:
        pulled: Pulled[BillingCharge] = Pulled()
        answers = await self._each(
            token,
            account_ids,
            lambda account: (
                f"act_{account}/activities?fields=event_time,event_type,extra_data"
                f"&since={since.isoformat()}&until={until.isoformat()}&limit=500"
            ),
            ACTIVITY_BATCH_SIZE,
        )
        for account, (body, error) in answers.items():
            if error is not None or body is None:
                pulled.errors[account] = error or "Không đọc được."
                continue
            events = list(body.get("data") or [])
            next_url = (body.get("paging") or {}).get("next")
            if next_url:
                try:
                    events.extend(await self._pages(str(next_url)))
                except MetaAdsError as failure:
                    # Half an account's charges would undercount it: none at all.
                    pulled.errors[account] = failure.message
                    continue
            seen: set[str] = set()
            for event in events:
                if event.get("event_type") != "ad_account_billing_charge":
                    continue
                extra = event.get("extra_data") or {}
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except ValueError:
                        extra = {}
                transaction = str(extra.get("transaction_id") or "") or None
                if transaction:
                    if transaction in seen:
                        continue
                    seen.add(transaction)
                try:
                    at = datetime.fromisoformat(str(event.get("event_time")))
                except ValueError:
                    continue
                pulled.rows.append(
                    BillingCharge(
                        account_id=account,
                        at=at if at.tzinfo else at.replace(tzinfo=UTC),
                        amount=_money(extra.get("new_value")),
                        transaction_id=transaction,
                    )
                )
        return pulled


@dataclass(slots=True)
class FakeMetaAdsClient:
    """In-memory Meta for the tests: set the data, read what was asked."""

    names: dict[str, str] = field(default_factory=dict)
    accounts: dict[str, list[AdAccountInfo]] = field(default_factory=dict)
    insights: list[DailyInsight] = field(default_factory=list)
    charges: list[BillingCharge] = field(default_factory=list)
    #: ``token -> error``: every call with that token fails like this.
    broken_tokens: dict[str, MetaAdsError] = field(default_factory=dict)
    #: Accounts whose per-account calls fail.
    broken_accounts: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _check(self, token: str) -> None:
        if token in self.broken_tokens:
            raise self.broken_tokens[token]

    async def business_name(self, token: str, business_id: str) -> str:
        self.calls.append(("business", business_id))
        self._check(token)
        if business_id not in self.names:
            raise MetaAdsError(
                "Không tìm thấy BM với Business ID này, hoặc token không thuộc BM đó."
            )
        return self.names[business_id]

    async def ad_accounts(self, token: str, business_id: str) -> list[AdAccountInfo]:
        self.calls.append(("accounts", business_id))
        self._check(token)
        return list(self.accounts.get(business_id, []))

    async def daily_insights(
        self, token: str, account_ids: Sequence[str], since: date, until: date
    ) -> Pulled[DailyInsight]:
        self.calls.append(("insights", f"{since}..{until}"))
        self._check(token)
        wanted = set(account_ids)
        pulled: Pulled[DailyInsight] = Pulled()
        for account in wanted & set(self.broken_accounts):
            pulled.errors[account] = self.broken_accounts[account]
        pulled.rows = [
            row
            for row in self.insights
            if row.account_id in wanted
            and row.account_id not in self.broken_accounts
            and since <= row.day <= until
        ]
        return pulled

    async def billing_charges(
        self, token: str, account_ids: Sequence[str], since: date, until: date
    ) -> Pulled[BillingCharge]:
        self.calls.append(("charges", f"{since}..{until}"))
        self._check(token)
        wanted = set(account_ids)
        pulled: Pulled[BillingCharge] = Pulled()
        for account in wanted & set(self.broken_accounts):
            pulled.errors[account] = self.broken_accounts[account]
        pulled.rows = [
            row
            for row in self.charges
            if row.account_id in wanted and row.account_id not in self.broken_accounts
        ]
        return pulled


__all__ = [
    "AdAccountInfo",
    "BillingCharge",
    "DailyInsight",
    "FakeMetaAdsClient",
    "GraphMetaAdsClient",
    "MetaAdsClient",
    "MetaAdsError",
    "Pulled",
    "explain",
]
