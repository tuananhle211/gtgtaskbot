"""The CRM's Open API (aicrmtech.vn, "API Internal Raw Data").

GET only, the key in the ``x-api-key`` header - never in the URL, a log line
or the browser. 60 requests a minute per IP: a 429 is waited out and asked
again. Pages hold up to 500 rows.

Read for Phòng Ads and Tài chính in one run (``meobot.application.crm.sync``):
the marketing users, the branches, the payments (thực thu, with the people on
the customer's file and the order's owner), and for Tài chính the orders
(doanh số, VAT, còn nợ, the lines), the revenue shared to doctors / TVV / KTV /
nurses and the công tua. A customer's name is kept (Tài chính's reports,
user 12/11/2026); a phone number never.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
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
    #: The people on the customer's file when read (not the seller), and the
    #: order's owner ("người lên đơn") - Tài chính's "theo người".
    telesales_id: int | None = None
    telesales_name: str | None = None
    cskh_id: int | None = None
    cskh_name: str | None = None
    carepage_id: int | None = None
    carepage_name: str | None = None
    owner_id: int | None = None
    owner_name: str | None = None
    order_code: str | None = None
    customer_name: str | None = None

    def people(self) -> list[tuple[int, str, str]]:
        """(CRM id, name, role) of everybody the payment names."""
        found = (
            (self.marketing_id, self.marketing_name, "marketing"),
            (self.telesales_id, self.telesales_name, "telesales"),
            (self.cskh_id, self.cskh_name, "cskh"),
            (self.carepage_id, self.carepage_name, "carepage"),
            (self.owner_id, self.owner_name, "owner"),
        )
        return [(key, name or f"CRM {key}", role) for key, name, role in found if key is not None]


@dataclass(frozen=True, slots=True)
class CrmBranch:
    id: int
    name: str
    location_id: int | None = None


@dataclass(frozen=True, slots=True)
class CrmOrderItem:
    service_id: int | None
    service_name: str | None
    quantity: Decimal
    total_price: Decimal


@dataclass(frozen=True, slots=True)
class CrmOrder:
    """One order, by the day it was created: doanh số (``all_total``), the
    part before VAT, VAT, what is still owed (``the_rest``)."""

    id: int
    code: str | None
    created_at: datetime
    branch_id: int | None
    customer_id: int | None
    customer_code: str | None
    customer_name: str | None
    source_id: int | None
    all_total: Decimal
    amount_before_vat: Decimal
    vat_amount: Decimal
    vat_percent: Decimal | None
    gross_revenue: Decimal
    the_rest: Decimal
    discount: Decimal
    payment_type: int | None
    order_type: int | None
    role_type: int | None
    owner_id: int | None
    owner_name: str | None
    items: tuple[CrmOrderItem, ...] = ()


@dataclass(frozen=True, slots=True)
class CrmDepartment:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class CrmSharedRevenue:
    """A share of one payment credited to one person of a CRM department
    (doctors, TVV, KTV, nurses) - no id on the CRM, read by payment day."""

    payment_date: date
    department_id: int
    order_code: str | None
    customer_code: str | None
    customer_name: str | None
    services: str | None
    payment_amount: Decimal
    payment_type: int | None
    employee_id: int | None
    employee_name: str | None
    share_type: int | None
    shared_revenue: Decimal
    branch_name: str | None


@dataclass(frozen=True, slots=True)
class CrmTour:
    """One công tua: one person's part (primary or support) in one treatment."""

    performed_at: datetime
    order_code: str | None
    customer_code: str | None
    customer_name: str | None
    service_name: str | None
    treatment_type: int | None
    employee_id: int | None
    employee_name: str | None
    work_type: str | None
    procedure_name: str | None
    branch_name: str | None
    note: str | None


class CrmClient(Protocol):
    async def marketing_users(self) -> list[CrmMarketingUser]: ...

    async def branches(self) -> list[CrmBranch]: ...

    async def payments(self, since: date, until: date) -> list[CrmPayment]: ...

    async def orders(self, since: date, until: date, *, unpaid: bool = False) -> list[CrmOrder]: ...

    async def share_departments(self) -> list[CrmDepartment]: ...

    async def shared_revenues(
        self, since: date, until: date, department_id: int
    ) -> list[CrmSharedRevenue]: ...

    async def tours(self, since: date, until: date) -> list[CrmTour]: ...

    async def aclose(self) -> None: ...


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _id_of(value: Any) -> int | None:
    return _int(value.get("id")) if isinstance(value, dict) else None


def _name_of(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return _text(value.get("name"), 200)


def _text(value: Any, size: int) -> str | None:
    text = str(value or "").strip()
    return text[:size] or None


def _money(value: Any, what: str) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except InvalidOperation as error:
        raise CrmError(f"{what} có số tiền lạ.") from error


def _moment(value: Any) -> datetime:
    """``Y-m-d H:i:s`` in Vietnam time, kept naive (the CRM's own clock)."""
    text = str(value or "").strip()
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError as error:
        raise CrmError(f"CRM trả thời điểm lạ ({text[:30]}).") from error


def _value_of(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) else value


def parse_payment(raw: dict[str, Any]) -> CrmPayment:
    price = _money(raw.get("price"), f"Khoản thanh toán {raw.get('id')}")
    marketing = raw.get("marketing") if isinstance(raw.get("marketing"), dict) else None
    customer: dict[str, Any] = raw["customer"] if isinstance(raw.get("customer"), dict) else {}
    order: dict[str, Any] = raw["order"] if isinstance(raw.get("order"), dict) else {}
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
        telesales_id=_id_of(raw.get("telesales")),
        telesales_name=_name_of(raw.get("telesales")),
        cskh_id=_id_of(raw.get("cskh")),
        cskh_name=_name_of(raw.get("cskh")),
        carepage_id=_id_of(raw.get("carepage")),
        carepage_name=_name_of(raw.get("carepage")),
        owner_id=_id_of(order.get("owner")),
        owner_name=_name_of(order.get("owner")),
        order_code=_text(order.get("code"), 40),
        customer_name=_text(customer.get("full_name"), 200),
    )


def parse_order(raw: dict[str, Any]) -> CrmOrder:
    what = f"Đơn {raw.get('code') or raw.get('id')}"
    customer: dict[str, Any] = raw["customer"] if isinstance(raw.get("customer"), dict) else {}
    items = tuple(
        CrmOrderItem(
            service_id=_int(item.get("service_id")),
            service_name=_text(item.get("service_name"), 300),
            quantity=_money(item.get("quantity"), what),
            total_price=_money(item.get("total_price"), what),
        )
        for item in raw.get("items") or []
        if isinstance(item, dict)
    )
    percent = raw.get("vat_percent")
    return CrmOrder(
        id=int(raw["id"]),
        code=_text(raw.get("code"), 40),
        created_at=_moment(raw.get("created_at")),
        branch_id=_id_of(raw.get("branch")),
        customer_id=_int(customer.get("id")),
        customer_code=_text(customer.get("account_code"), 40),
        customer_name=_text(customer.get("full_name"), 200),
        source_id=_id_of(customer.get("source")),
        all_total=_money(raw.get("all_total"), what),
        amount_before_vat=_money(raw.get("amount_before_vat"), what),
        vat_amount=_money(raw.get("vat_amount"), what),
        vat_percent=_money(percent, what) if percent not in (None, "") else None,
        gross_revenue=_money(raw.get("gross_revenue"), what),
        the_rest=_money(raw.get("the_rest"), what),
        discount=_money(raw.get("discount"), what),
        payment_type=_int(raw.get("payment_type")),
        order_type=_int(raw.get("order_type")),
        role_type=_int(raw.get("role_type")),
        owner_id=_id_of(raw.get("owner")),
        owner_name=_name_of(raw.get("owner")),
        items=items,
    )


def parse_shared_revenue(raw: dict[str, Any], department_id: int) -> CrmSharedRevenue:
    what = f"Doanh thu chia đơn {raw.get('order_code')}"
    return CrmSharedRevenue(
        payment_date=date.fromisoformat(str(raw["payment_date"])[:10]),
        department_id=department_id,
        order_code=_text(raw.get("order_code"), 40),
        customer_code=_text(raw.get("customer_account_code"), 40),
        customer_name=_text(raw.get("customer_name"), 200),
        services=_text(raw.get("services"), 1000),
        payment_amount=_money(raw.get("payment_amount"), what),
        payment_type=_int(_value_of(raw.get("payment_type"))),
        employee_id=_id_of(raw.get("employee")),
        employee_name=_name_of(raw.get("employee")),
        share_type=_int(_value_of(raw.get("share_type"))),
        shared_revenue=_money(raw.get("shared_revenue"), what),
        branch_name=_text(raw.get("branch_name"), 200),
    )


def parse_tour(raw: dict[str, Any]) -> CrmTour:
    return CrmTour(
        performed_at=_moment(raw.get("performed_at")),
        order_code=_text(raw.get("order_code"), 40),
        customer_code=_text(raw.get("customer_account_code"), 40),
        customer_name=_text(raw.get("customer_name"), 200),
        service_name=_text(raw.get("service_name"), 300),
        treatment_type=_int(_value_of(raw.get("treatment_type"))),
        employee_id=_id_of(raw.get("employee")),
        employee_name=_name_of(raw.get("employee")),
        work_type=_text(_value_of(raw.get("work_type")), 20),
        procedure_name=_text(raw.get("procedure_name"), 300),
        branch_name=_text(raw.get("branch_name"), 200),
        note=_text(raw.get("note"), 500),
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
            CrmBranch(
                int(row["value"]),
                str(row.get("label") or row["value"]),
                _int(row.get("location_id")),
            )
            for row in (data or {}).get("branches") or []
            if isinstance(row, dict) and row.get("value") is not None
        ]

    async def _pages(self, path: str, params: dict[str, Any], what: str) -> list[dict[str, Any]]:
        """Every row of a listing, page by page, oldest id first."""
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= MAX_PAGES:
            body = await self._get(path, {**params, "per_page": PER_PAGE, "page": page})
            rows += [raw for raw in body.get("data") or [] if isinstance(raw, dict)]
            meta = body.get("meta") or {}
            if page >= int(meta.get("last_page") or 1):
                return rows
            page += 1
        raise CrmError(f"CRM trả quá nhiều trang {what}.")

    @staticmethod
    def _span(since: date, until: date) -> dict[str, Any]:
        return {"start_date": since.isoformat(), "end_date": until.isoformat()}

    async def payments(self, since: date, until: date) -> list[CrmPayment]:
        rows = await self._pages(
            "/raw-data/order-payments",
            {**self._span(since, until), "sort": "id", "direction": "asc"},
            "thanh toán",
        )
        return [parse_payment(raw) for raw in rows]

    async def orders(self, since: date, until: date, *, unpaid: bool = False) -> list[CrmOrder]:
        params: dict[str, Any] = {**self._span(since, until), "sort": "id", "direction": "asc"}
        if unpaid:
            params["payment_status"] = "unpaid"
        rows = await self._pages("/raw-data/orders", params, "đơn hàng")
        return [parse_order(raw) for raw in rows]

    async def share_departments(self) -> list[CrmDepartment]:
        body = await self._get("/raw-data/filters/shared-revenues", {"limit": 1})
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        return [
            CrmDepartment(int(row["value"]), str(row.get("label") or row["value"]))
            for row in (data or {}).get("departments") or []
            if isinstance(row, dict) and row.get("value") is not None
        ]

    async def shared_revenues(
        self, since: date, until: date, department_id: int
    ) -> list[CrmSharedRevenue]:
        rows = await self._pages(
            "/raw-data/shared-revenues",
            {**self._span(since, until), "department_id": department_id},
            "doanh thu chia",
        )
        return [parse_shared_revenue(raw, department_id) for raw in rows]

    async def tours(self, since: date, until: date) -> list[CrmTour]:
        rows = await self._pages("/raw-data/tour-history", self._span(since, until), "công tua")
        return [parse_tour(raw) for raw in rows]


@dataclass
class FakeCrmClient:
    """In-memory CRM for the tests."""

    users: list[CrmMarketingUser] = field(default_factory=list)
    rows: list[CrmPayment] = field(default_factory=list)
    branch_list: list[CrmBranch] = field(default_factory=list)
    order_list: list[CrmOrder] = field(default_factory=list)
    departments: list[CrmDepartment] = field(default_factory=list)
    shares: list[CrmSharedRevenue] = field(default_factory=list)
    tour_list: list[CrmTour] = field(default_factory=list)
    error: CrmError | None = None
    #: A refusal for one dataset only ("orders", "shares", "tours").
    errors: dict[str, CrmError] = field(default_factory=dict)
    calls: list[tuple[str, date | None, date | None]] = field(default_factory=list)

    def _fail(self, name: str) -> None:
        if self.error:
            raise self.error
        if name in self.errors:
            raise self.errors[name]

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

    async def orders(self, since: date, until: date, *, unpaid: bool = False) -> list[CrmOrder]:
        self.calls.append(("unpaid" if unpaid else "orders", since, until))
        self._fail("orders")
        return [
            row
            for row in self.order_list
            if since <= row.created_at.date() <= until and (not unpaid or row.the_rest > 0)
        ]

    async def share_departments(self) -> list[CrmDepartment]:
        self.calls.append(("departments", None, None))
        self._fail("shares")
        return list(self.departments)

    async def shared_revenues(
        self, since: date, until: date, department_id: int
    ) -> list[CrmSharedRevenue]:
        self.calls.append((f"shares:{department_id}", since, until))
        self._fail("shares")
        return [
            row
            for row in self.shares
            if row.department_id == department_id and since <= row.payment_date <= until
        ]

    async def tours(self, since: date, until: date) -> list[CrmTour]:
        self.calls.append(("tours", since, until))
        self._fail("tours")
        return [row for row in self.tour_list if since <= row.performed_at.date() <= until]

    async def aclose(self) -> None:
        return None


__all__ = [
    "CrmBranch",
    "CrmClient",
    "CrmDepartment",
    "CrmError",
    "CrmMarketingUser",
    "CrmOrder",
    "CrmOrderItem",
    "CrmPayment",
    "CrmSharedRevenue",
    "CrmTour",
    "FakeCrmClient",
    "HttpCrmClient",
    "parse_order",
    "parse_payment",
    "parse_shared_revenue",
    "parse_tour",
]
