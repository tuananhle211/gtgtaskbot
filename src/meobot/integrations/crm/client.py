"""The CRM's Open API (aicrmtech.vn, "API Internal Raw Data").

GET only, the key in the ``x-api-key`` header - never in the URL, a log line
or the browser. 60 requests a minute per IP: a 429 is waited out and asked
again. Pages hold up to 500 rows.

Only what the revenue needs is read and kept: the marketing users, the
branches (name -> region), and each
payment's id, day, amount, type, marketing id, branch, order id, new/old
customer, customer source and customer code (``KH…``, for reconciling) -
never a customer's name or phone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

PER_PAGE = 500
TIMEOUT_SECONDS = 60.0
#: Waits before asking again after a 429 / a 5xx.
RETRY_WAITS = (5.0, 20.0, 60.0)
#: A typo guard: a month is ~3 pages.
MAX_PAGES = 200


class CrmError(Exception):
    """A refusal from the CRM, in words a person can act on."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class CrmMarketingUser:
    id: int
    name: str
    active: bool
    is_leader: bool = False


@dataclass(frozen=True, slots=True)
class CrmPayment:
    id: int
    day: date
    price: Decimal
    payment_type: int
    marketing_id: int | None
    marketing_name: str | None = None
    branch_id: int | None = None
    order_id: int | None = None
    is_upsale: bool | None = None
    source_id: int | None = None
    customer_code: str | None = None


@dataclass(frozen=True, slots=True)
class CrmBranch:
    id: int
    name: str


class CrmClient(Protocol):
    async def marketing_users(self) -> list[CrmMarketingUser]: ...

    async def branches(self) -> list[CrmBranch]: ...

    async def payments(self, since: date, until: date) -> list[CrmPayment]: ...

    async def aclose(self) -> None: ...


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _id_of(value: Any) -> int | None:
    return _int(value.get("id")) if isinstance(value, dict) else None


def parse_payment(raw: dict[str, Any]) -> CrmPayment:
    try:
        price = Decimal(str(raw.get("price") or 0))
    except InvalidOperation as error:
        raise CrmError(f"Khoản thanh toán {raw.get('id')} có số tiền lạ.") from error
    marketing = raw.get("marketing") if isinstance(raw.get("marketing"), dict) else None
    customer: dict[str, Any] = raw["customer"] if isinstance(raw.get("customer"), dict) else {}
    upsale = customer.get("is_upsale")
    return CrmPayment(
        id=int(raw["id"]),
        day=date.fromisoformat(str(raw["payment_date"])[:10]),
        price=price,
        payment_type=_int(raw.get("payment_type")) or 0,
        marketing_id=_id_of(marketing),
        marketing_name=str(marketing.get("name") or "") or None if marketing else None,
        branch_id=_id_of(raw.get("branch")),
        order_id=_id_of(raw.get("order")),
        is_upsale=bool(upsale) if upsale is not None else None,
        source_id=_id_of(raw.get("customer_source")),
        customer_code=str(customer.get("account_code") or "").strip()[:40] or None,
    )


def explain(status: int, body: Any) -> CrmError:
    detail = ""
    if isinstance(body, dict) and body.get("messages"):
        detail = f" ({str(body['messages'])[:200]})"
    if status in (401, 403):
        return CrmError("CRM từ chối key (x-api-key sai hoặc đã bị thu hồi).", status=status)
    if status == 429:
        return CrmError("CRM báo gọi quá nhiều (60 lần/phút).", status=status)
    if status == 422:
        return CrmError(f"CRM không nhận tham số{detail}.", status=status)
    return CrmError(f"CRM trả lỗi {status}{detail}.", status=status)


class HttpCrmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        waits: tuple[float, ...] = RETRY_WAITS,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )
        self._waits = waits

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(len(self._waits) + 1):
            try:
                response = await self._http.get(path, params=params)
            except httpx.HTTPError as error:
                if attempt < len(self._waits):
                    await asyncio.sleep(self._waits[attempt])
                    continue
                raise CrmError(f"Không gọi được CRM ({type(error).__name__}).") from error
            retry = response.status_code == 429 or response.status_code >= 500
            if retry and attempt < len(self._waits):
                await asyncio.sleep(self._waits[attempt])
                continue
            try:
                body = response.json()
            except ValueError:
                body = None
            if response.status_code != 200 or not isinstance(body, dict):
                raise explain(response.status_code, body)
            return body
        raise CrmError("CRM không trả lời.")  # pragma: no cover - the loop returns or raises

    async def marketing_users(self) -> list[CrmMarketingUser]:
        body = await self._get(
            "/meta/users", {"department_key": "marketing", "include_inactive": 1}
        )
        return [
            CrmMarketingUser(
                id=int(row["id"]),
                name=str(row.get("full_name") or row["id"]),
                active=bool(row.get("active")),
                is_leader=bool(row.get("is_leader")),
            )
            for row in body.get("data") or []
            if isinstance(row, dict) and row.get("id") is not None
        ]

    async def branches(self) -> list[CrmBranch]:
        body = await self._get("/raw-data/filters/orders", {})
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        return [
            CrmBranch(int(row["value"]), str(row.get("label") or row["value"]))
            for row in (data or {}).get("branches") or []
            if isinstance(row, dict) and row.get("value") is not None
        ]

    async def payments(self, since: date, until: date) -> list[CrmPayment]:
        rows: list[CrmPayment] = []
        page = 1
        while page <= MAX_PAGES:
            body = await self._get(
                "/raw-data/order-payments",
                {
                    "start_date": since.isoformat(),
                    "end_date": until.isoformat(),
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            rows += [parse_payment(raw) for raw in body.get("data") or [] if isinstance(raw, dict)]
            meta = body.get("meta") or {}
            if page >= int(meta.get("last_page") or 1):
                return rows
            page += 1
        raise CrmError("CRM trả quá nhiều trang thanh toán.")


@dataclass
class FakeCrmClient:
    """In-memory CRM for the tests."""

    users: list[CrmMarketingUser] = field(default_factory=list)
    rows: list[CrmPayment] = field(default_factory=list)
    branch_list: list[CrmBranch] = field(default_factory=list)
    error: CrmError | None = None
    calls: list[tuple[str, date | None, date | None]] = field(default_factory=list)

    async def marketing_users(self) -> list[CrmMarketingUser]:
        self.calls.append(("users", None, None))
        if self.error:
            raise self.error
        return list(self.users)

    async def branches(self) -> list[CrmBranch]:
        self.calls.append(("branches", None, None))
        if self.error:
            raise self.error
        return list(self.branch_list)

    async def payments(self, since: date, until: date) -> list[CrmPayment]:
        self.calls.append(("payments", since, until))
        if self.error:
            raise self.error
        return [row for row in self.rows if since <= row.day <= until]

    async def aclose(self) -> None:
        return None


__all__ = [
    "CrmBranch",
    "CrmClient",
    "CrmError",
    "CrmMarketingUser",
    "CrmPayment",
    "FakeCrmClient",
    "HttpCrmClient",
    "parse_payment",
]
