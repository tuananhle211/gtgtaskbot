"""Pancake Public API + POS Cake Open API (0102, ported from ``hop-chat-apexmed``).

Pancake (``https://pages.fm/api``, docs https://developer.pancake.biz):

* ``GET  v1/pages?access_token=<user token>`` - the account's pages,
  ``categorized.activated`` / ``inactivated``;
* ``POST v1/pages/{page}/generate_page_access_token`` - a page token (makes the
  old one invalid);
* ``GET  public_api/v2/pages/{page}/conversations`` - the 60 newest;
* ``GET/POST public_api/v1/pages/{page}/conversations/{conv}/messages`` - the
  thread / ``{"action": "reply_inbox", "message"}``;
* ``public_api/v1/pages/{page}/tags``, ``/users``, ``conversations/{conv}/tags``,
  ``conversations/{conv}/assign``, ``ads?ad_ids=<one id>``.

Pancake refuses with **HTTP 200 + ``success: false``**; 5 calls / s / page.

POS Cake (``https://pos.pages.fm/api/v1``, ``?api_key=``): ``GET shops``,
``shops/{shop}/orders?search=`` (name / phone), ``GET/PUT shops/{shop}/orders/{id}``,
``POST shops/{shop}/orders``.

Tokens and keys travel in the query string as both ask; the log filter
(``core/logging.RedactingFilter``) blanks ``*token`` / ``api_key`` there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from meobot.domain.cskh.models import PANCAKE_API_BASE, POS_API_BASE, CskhUpstreamError

TIMEOUT_SECONDS = 30.0
#: Customer pictures are read (QR codes) only from Pancake's / Facebook's hosts.
IMAGE_HOSTS: tuple[str, ...] = (
    "pancake.vn",
    "pages.fm",
    "fbcdn.net",
    "fbsbx.com",
    "facebook.com",
    "cdninstagram.com",
)


def image_url_allowed(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError):
        return False
    host = (parsed.host or "").lower()
    return parsed.scheme == "https" and any(
        host == h or host.endswith("." + h) for h in IMAGE_HOSTS
    )


@dataclass(frozen=True, slots=True)
class PancakePageInfo:
    id: str
    name: str
    username: str = ""
    platform: str = ""
    #: ``activated`` / ``inactivated``.
    status: str = "activated"


def _check(response: httpx.Response, what: str) -> Any:
    if response.status_code in (401, 403):
        raise CskhUpstreamError(
            f"{what}_token_invalid", f"{_name(what)}: token / key sai hoặc đã hết hạn."
        )
    if response.status_code == 429:
        raise CskhUpstreamError(f"{what}_rate_limited", f"{_name(what)} đang giới hạn số lần gọi.")
    if response.status_code >= 400:
        body = response.text[:200].replace("\n", " ")
        raise CskhUpstreamError(
            f"{what}_failed", f"{_name(what)} trả lỗi HTTP {response.status_code}: {body}"
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise CskhUpstreamError(f"{what}_failed", f"{_name(what)} trả về dữ liệu lạ.") from error
    if isinstance(payload, dict) and payload.get("success") is False:
        message = str(
            payload.get("message") or payload.get("message_code") or payload.get("error_code") or ""
        )[:200]
        lowered = message.lower()
        code = (
            f"{what}_token_invalid"
            if "token" in lowered or "api_key" in lowered
            else f"{what}_failed"
        )
        raise CskhUpstreamError(code, f"{_name(what)}: {message or 'lỗi không rõ'}")
    return payload


def _name(what: str) -> str:
    return "Pancake POS" if what == "pos" else "Pancake"


class PancakeClient(Protocol):
    async def list_pages(self, user_token: str) -> list[PancakePageInfo]: ...

    async def generate_page_token(self, user_token: str, page_id: str) -> str: ...

    async def conversations(self, page_id: str, token: str) -> list[dict[str, Any]]: ...

    async def messages(
        self, page_id: str, conversation_id: str, token: str
    ) -> list[dict[str, Any]]: ...

    async def send(self, page_id: str, conversation_id: str, token: str, text: str) -> str: ...

    async def tags(self, page_id: str, token: str) -> list[dict[str, Any]]: ...

    async def users(self, page_id: str, token: str) -> list[dict[str, Any]]: ...

    async def set_tag(
        self, page_id: str, conversation_id: str, token: str, tag_id: str, action: str
    ) -> list[str]: ...

    async def assign(
        self, page_id: str, conversation_id: str, token: str, user_ids: list[str]
    ) -> list[str]: ...

    async def ads(self, page_id: str, token: str, ad_id: str) -> list[dict[str, Any]]: ...

    async def download(self, url: str, limit: int) -> bytes: ...


class PosClient(Protocol):
    async def shops(self, api_key: str) -> list[dict[str, Any]]: ...

    async def orders(self, api_key: str, shop_id: str, search: str) -> list[dict[str, Any]]: ...

    async def order(self, api_key: str, shop_id: str, order_id: str) -> dict[str, Any]: ...

    async def update_order(
        self, api_key: str, shop_id: str, order_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def create_order(
        self, api_key: str, shop_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...


class HttpPancakeClient:
    def __init__(
        self, base_url: str = PANCAKE_API_BASE, *, timeout: float = TIMEOUT_SECONDS
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def _call(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{self._base}/{path}", params=params, json=body
                )
        except httpx.HTTPError as error:
            raise CskhUpstreamError("pancake_unreachable", "Không kết nối được Pancake.") from error
        return _check(response, "pancake")

    async def list_pages(self, user_token: str) -> list[PancakePageInfo]:
        payload = await self._call("GET", "v1/pages", {"access_token": user_token})
        categorized = payload.get("categorized") if isinstance(payload, dict) else None
        out: list[PancakePageInfo] = []
        for status in ("activated", "inactivated"):
            for page in (categorized or {}).get(status) or []:
                if isinstance(page, dict) and page.get("id"):
                    out.append(
                        PancakePageInfo(
                            id=str(page["id"]),
                            name=str(page.get("name") or page["id"]),
                            username=str(page.get("username") or ""),
                            platform=str(page.get("platform") or ""),
                            status=status,
                        )
                    )
        return out

    async def generate_page_token(self, user_token: str, page_id: str) -> str:
        payload = await self._call(
            "POST",
            f"v1/pages/{page_id}/generate_page_access_token",
            {"access_token": user_token, "page_id": page_id},
        )
        token = str((payload or {}).get("page_access_token") or "")
        if not token:
            raise CskhUpstreamError(
                "pancake_no_token",
                "Pancake không trả về token (tài khoản cần quyền quản trị page).",
            )
        return token

    async def conversations(self, page_id: str, token: str) -> list[dict[str, Any]]:
        payload = await self._call(
            "GET", f"public_api/v2/pages/{page_id}/conversations", {"page_access_token": token}
        )
        items = (payload or {}).get("conversations")
        return [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []

    async def messages(
        self, page_id: str, conversation_id: str, token: str
    ) -> list[dict[str, Any]]:
        payload = await self._call(
            "GET",
            f"public_api/v1/pages/{page_id}/conversations/{conversation_id}/messages",
            {"page_access_token": token},
        )
        items = (payload or {}).get("messages")
        return [m for m in items if isinstance(m, dict)] if isinstance(items, list) else []

    async def send(self, page_id: str, conversation_id: str, token: str, text: str) -> str:
        payload = await self._call(
            "POST",
            f"public_api/v1/pages/{page_id}/conversations/{conversation_id}/messages",
            {"page_access_token": token},
            body={"action": "reply_inbox", "message": text},
        )
        return str((payload or {}).get("id") or "")[:200]

    async def tags(self, page_id: str, token: str) -> list[dict[str, Any]]:
        payload = await self._call(
            "GET", f"public_api/v1/pages/{page_id}/tags", {"page_access_token": token}
        )
        items = (payload or {}).get("tags")
        return [t for t in items if isinstance(t, dict)] if isinstance(items, list) else []

    async def users(self, page_id: str, token: str) -> list[dict[str, Any]]:
        payload = await self._call(
            "GET", f"public_api/v1/pages/{page_id}/users", {"page_access_token": token}
        )
        items = (payload or {}).get("users")
        return [u for u in items if isinstance(u, dict)] if isinstance(items, list) else []

    async def set_tag(
        self, page_id: str, conversation_id: str, token: str, tag_id: str, action: str
    ) -> list[str]:
        payload = await self._call(
            "POST",
            f"public_api/v1/pages/{page_id}/conversations/{conversation_id}/tags",
            {"page_access_token": token},
            body={"action": action, "tag_id": str(tag_id)},
        )
        data = (payload or {}).get("data")
        return [str(t) for t in data if t is not None] if isinstance(data, list) else []

    async def assign(
        self, page_id: str, conversation_id: str, token: str, user_ids: list[str]
    ) -> list[str]:
        payload = await self._call(
            "POST",
            f"public_api/v1/pages/{page_id}/conversations/{conversation_id}/assign",
            {"page_access_token": token},
            body={"assignee_ids": user_ids},
        )
        data = (payload or {}).get("assignee_ids")
        return [str(a) for a in data if a] if isinstance(data, list) else list(user_ids)

    async def ads(self, page_id: str, token: str, ad_id: str) -> list[dict[str, Any]]:
        # ``ad_ids=<one id>``: the ``ad_ids[]=`` form answers 400.
        payload = await self._call(
            "GET",
            f"public_api/v1/pages/{page_id}/ads",
            {"page_access_token": token, "ad_ids": ad_id},
        )
        data = (payload or {}).get("data")
        return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []

    async def download(self, url: str, limit: int) -> bytes:
        if not image_url_allowed(url):
            raise CskhUpstreamError(
                "download_invalid", "Địa chỉ ảnh không thuộc Pancake / Facebook."
            )
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client,
                client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as response,
            ):
                if response.status_code >= 400:
                    raise CskhUpstreamError(
                        "download_failed", f"Tải ảnh lỗi HTTP {response.status_code}."
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise CskhUpstreamError("download_too_large", "Ảnh quá lớn.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as error:
            raise CskhUpstreamError("download_failed", "Không tải được ảnh.") from error


class HttpPosClient:
    def __init__(self, base_url: str = POS_API_BASE, *, timeout: float = TIMEOUT_SECONDS) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def _call(
        self,
        method: str,
        path: str,
        api_key: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        query = {**(params or {}), "api_key": api_key}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{self._base}/{path}", params=query, json=body
                )
        except httpx.HTTPError as error:
            raise CskhUpstreamError("pos_unreachable", "Không kết nối được Pancake POS.") from error
        return _check(response, "pos")

    async def shops(self, api_key: str) -> list[dict[str, Any]]:
        payload = await self._call("GET", "shops", api_key)
        items = (payload or {}).get("shops")
        return [s for s in items if isinstance(s, dict)] if isinstance(items, list) else []

    async def orders(self, api_key: str, shop_id: str, search: str) -> list[dict[str, Any]]:
        payload = await self._call(
            "GET",
            f"shops/{shop_id}/orders",
            api_key,
            {"search": search, "page_size": 20, "option_sort": "inserted_at_desc"},
        )
        items = (payload or {}).get("data")
        return [o for o in items if isinstance(o, dict)] if isinstance(items, list) else []

    async def order(self, api_key: str, shop_id: str, order_id: str) -> dict[str, Any]:
        payload = await self._call("GET", f"shops/{shop_id}/orders/{order_id}", api_key)
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else (payload if isinstance(payload, dict) else {})

    async def update_order(
        self, api_key: str, shop_id: str, order_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        payload = await self._call("PUT", f"shops/{shop_id}/orders/{order_id}", api_key, body=body)
        return payload if isinstance(payload, dict) else {}

    async def create_order(
        self, api_key: str, shop_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        payload = await self._call("POST", f"shops/{shop_id}/orders", api_key, body=body)
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else (payload if isinstance(payload, dict) else {})


# --- fakes (tests, local work) -------------------------------------------------------------


@dataclass
class FakePancakeClient:
    """Pancake from a script: pages per user token, conversations / messages per page."""

    pages: dict[str, list[PancakePageInfo]] = field(default_factory=dict)
    page_tokens: dict[str, str] = field(default_factory=dict)
    convs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    threads: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    page_tags: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    page_users: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ad_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    images: dict[str, bytes] = field(default_factory=dict)
    sent: list[dict[str, str]] = field(default_factory=list)
    fail_send: str | None = None
    calls: list[str] = field(default_factory=list)

    async def list_pages(self, user_token: str) -> list[PancakePageInfo]:
        if user_token == "bad":  # noqa: S105 - the fake's refused token
            raise CskhUpstreamError("pancake_token_invalid", "Pancake: token sai.")
        return list(self.pages.get(user_token, []))

    async def generate_page_token(self, user_token: str, page_id: str) -> str:
        token = f"pt-{page_id}"
        self.page_tokens[page_id] = token
        return token

    async def conversations(self, page_id: str, token: str) -> list[dict[str, Any]]:
        self.calls.append(f"conversations:{page_id}")
        return list(self.convs.get(page_id, []))

    async def messages(
        self, page_id: str, conversation_id: str, token: str
    ) -> list[dict[str, Any]]:
        self.calls.append(f"messages:{conversation_id}")
        return list(self.threads.get(conversation_id, []))

    async def send(self, page_id: str, conversation_id: str, token: str, text: str) -> str:
        if self.fail_send:
            raise CskhUpstreamError("pancake_failed", self.fail_send)
        message_id = f"m_sent_{len(self.sent) + 1}"
        self.sent.append(
            {"page_id": page_id, "conversation_id": conversation_id, "text": text, "id": message_id}
        )
        return message_id

    async def tags(self, page_id: str, token: str) -> list[dict[str, Any]]:
        return list(self.page_tags.get(page_id, []))

    async def users(self, page_id: str, token: str) -> list[dict[str, Any]]:
        return list(self.page_users.get(page_id, []))

    async def set_tag(
        self, page_id: str, conversation_id: str, token: str, tag_id: str, action: str
    ) -> list[str]:
        conv = next(
            (c for c in self.convs.get(page_id, []) if c.get("id") == conversation_id), None
        )
        current = [str(t) for t in (conv or {}).get("tags") or []]
        if action == "add" and tag_id not in current:
            current.append(tag_id)
        if action == "remove":
            current = [t for t in current if t != tag_id]
        if conv is not None:
            conv["tags"] = current
        return current

    async def assign(
        self, page_id: str, conversation_id: str, token: str, user_ids: list[str]
    ) -> list[str]:
        return list(user_ids)

    async def ads(self, page_id: str, token: str, ad_id: str) -> list[dict[str, Any]]:
        info = self.ad_info.get(ad_id)
        return [info] if info else []

    async def download(self, url: str, limit: int) -> bytes:
        if url not in self.images:
            raise CskhUpstreamError("download_failed", "Không tải được ảnh.")
        return self.images[url]


@dataclass
class FakePosClient:
    shops_by_key: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: ``shop id -> orders``.
    store: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)

    async def shops(self, api_key: str) -> list[dict[str, Any]]:
        if api_key not in self.shops_by_key:
            raise CskhUpstreamError("pos_token_invalid", "Pancake POS: api_key sai.")
        return list(self.shops_by_key[api_key])

    async def orders(self, api_key: str, shop_id: str, search: str) -> list[dict[str, Any]]:
        digits = "".join(ch for ch in search if ch.isdigit())
        out = []
        for order in self.store.get(str(shop_id), []):
            phone = "".join(ch for ch in str(order.get("bill_phone_number") or "") if ch.isdigit())
            if (digits and digits[-9:] in phone) or search.lower() in str(
                order.get("bill_full_name") or ""
            ).lower():
                out.append(dict(order))
        return out

    async def order(self, api_key: str, shop_id: str, order_id: str) -> dict[str, Any]:
        for order in self.store.get(str(shop_id), []):
            if str(order.get("id")) == str(order_id):
                return dict(order)
        raise CskhUpstreamError("pos_failed", "Pancake POS: không có đơn.")

    async def update_order(
        self, api_key: str, shop_id: str, order_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self.updates.append({"shop_id": str(shop_id), "order_id": str(order_id), **body})
        for order in self.store.get(str(shop_id), []):
            if str(order.get("id")) == str(order_id):
                order.update(body)
        return {"success": True}

    async def create_order(
        self, api_key: str, shop_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        orders = self.store.setdefault(str(shop_id), [])
        order = {
            "id": 9000 + len(self.created) + 1,
            "system_id": 9000 + len(self.created) + 1,
            **body,
        }
        orders.append(order)
        self.created.append({"shop_id": str(shop_id), **body})
        return dict(order)


__all__ = [
    "FakePancakeClient",
    "FakePosClient",
    "HttpPancakeClient",
    "HttpPosClient",
    "PancakeClient",
    "PancakePageInfo",
    "PosClient",
    "image_url_allowed",
]
