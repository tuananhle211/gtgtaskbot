"""The Zalo bridge (``deploy/zalo-bridge``, Node 22 + ``zca-js``, 0102).

A company Zalo nick is linked by scanning a QR code shown in APM; the bridge
keeps the session (encrypted on its volume), only LISTENS (never sends), and
pushes every message to ``POST /api/cskh/zalo/bridge`` with the same secret
header. APM asks it (``x-bridge-secret``) for: ``GET /health``,
``GET /accounts``, ``POST /qr/start``, ``GET /qr/status?session=``,
``POST /logout``. Reached only inside the compose network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from meobot.domain.cskh.models import CskhUpstreamError

TIMEOUT_SECONDS = 20.0


class ZaloBridge(Protocol):
    @property
    def configured(self) -> bool: ...

    async def alive(self) -> bool: ...

    async def accounts(self) -> list[dict[str, Any]]: ...

    async def qr_start(self) -> dict[str, Any]: ...

    async def qr_status(self, session: str) -> dict[str, Any]: ...

    async def logout(self, account_id: str) -> None: ...


class HttpZaloBridge:
    def __init__(self, url: str | None, secret: str | None, *, timeout: float = TIMEOUT_SECONDS):
        self._url = (url or "").rstrip("/")
        self._secret = secret or ""
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._url and self._secret)

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        wait: float | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise CskhUpstreamError(
                "zalo_bridge_not_configured", "Chưa cấu hình dịch vụ Zalo (ZALO_BRIDGE_URL)."
            )
        try:
            async with httpx.AsyncClient(timeout=wait or self._timeout) as client:
                response = await client.request(
                    method,
                    self._url + path,
                    json=body,
                    headers={"x-bridge-secret": self._secret},
                )
        except httpx.HTTPError as error:
            raise CskhUpstreamError(
                "zalo_bridge_down", "Dịch vụ quét QR Zalo chưa chạy."
            ) from error
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise CskhUpstreamError(
                "zalo_bridge_failed",
                str(message or f"Dịch vụ Zalo lỗi HTTP {response.status_code}"),
            )
        return payload if isinstance(payload, dict) else {}

    async def alive(self) -> bool:
        if not self.configured:
            return False
        try:
            return bool((await self._call("GET", "/health", wait=3)).get("ok"))
        except CskhUpstreamError:
            return False

    async def accounts(self) -> list[dict[str, Any]]:
        items = (await self._call("GET", "/accounts", wait=5)).get("accounts")
        return [a for a in items if isinstance(a, dict)] if isinstance(items, list) else []

    async def qr_start(self) -> dict[str, Any]:
        return await self._call("POST", "/qr/start", {})

    async def qr_status(self, session: str) -> dict[str, Any]:
        return await self._call(
            "GET", "/qr/status?session=" + httpx.QueryParams({"s": session})["s"]
        )

    async def logout(self, account_id: str) -> None:
        await self._call("POST", "/logout", {"id": account_id})


@dataclass
class FakeZaloBridge:
    up: bool = True
    live: list[dict[str, Any]] = field(default_factory=list)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    logged_out: list[str] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return True

    async def alive(self) -> bool:
        return self.up

    async def accounts(self) -> list[dict[str, Any]]:
        if not self.up:
            raise CskhUpstreamError("zalo_bridge_down", "Dịch vụ quét QR Zalo chưa chạy.")
        return list(self.live)

    async def qr_start(self) -> dict[str, Any]:
        if not self.up:
            raise CskhUpstreamError("zalo_bridge_down", "Dịch vụ quét QR Zalo chưa chạy.")
        session = f"s{len(self.sessions) + 1}"
        self.sessions[session] = {"state": "qr", "image": "iVBORw0KGgo="}
        return {"session": session, **self.sessions[session]}

    async def qr_status(self, session: str) -> dict[str, Any]:
        if session not in self.sessions:
            raise CskhUpstreamError("zalo_bridge_failed", "Phiên QR không còn, bấm quét lại")
        return dict(self.sessions[session])

    async def logout(self, account_id: str) -> None:
        self.logged_out.append(account_id)


__all__ = ["FakeZaloBridge", "HttpZaloBridge", "ZaloBridge"]
