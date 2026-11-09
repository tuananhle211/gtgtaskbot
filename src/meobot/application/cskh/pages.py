"""The pages "Chăm sóc khách hàng" answers (0094): wiring, tokens, webhook address.

Only a ``MANAGE`` holder (the OWNER by role) reads and writes them. A page's
token is stored encrypted (``SecretBox``, AAD ``pancake_page:<id>``); only
``••••abcd`` leaves the server. ``webhook_key`` is the secret part of the
address pasted into Pancake ("Đổi địa chỉ" makes a new one, the old stops).
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import CskhAccess
from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.core.time import utcnow
from meobot.db.models.agent_chat import AgentProfile
from meobot.db.models.pancake import PancakePage
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.cskh.errors import CskhInvalidError, CskhPageNotFoundError
from meobot.domain.pancake.models import DEFAULT_AGENT_SLUG, PancakeError, ReplyMode
from meobot.integrations.pancake.client import PancakeClient

_PAGE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_TAGS = 30


def token_aad(page_row_id: uuid.UUID) -> str:
    return f"pancake_page:{page_row_id}"


def token_hint(token: str) -> str:
    return "••••" + token[-4:] if len(token) >= 8 else "••••"


def new_webhook_key() -> str:
    return secrets.token_urlsafe(24)


def webhook_url(settings: Settings, key: str) -> str:
    """The address pasted into Pancake (the web's origin proxies ``/api/cskh``)."""
    base = (settings.web_base_url or "").rstrip("/")
    return f"{base}/api/cskh/pancake/webhook/{key}"


def read_token(page: PancakePage, box: SecretBox) -> str | None:
    if not page.access_token_encrypted:
        return None
    return box.decrypt(page.access_token_encrypted, aad=token_aad(page.id))


class CskhPageService:
    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        settings: Settings,
        *,
        secret_box: Callable[[], SecretBox] | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._settings = settings
        self._secret_box = secret_box or (lambda: build_secret_box(settings))

    # --- reading ----------------------------------------------------------------

    def secret_ready(self) -> bool:
        try:
            self._secret_box()
        except ConfigurationError:
            return False
        return True

    async def pages(self, access: CskhAccess) -> list[PancakePage]:
        access.require_manage()
        return list(
            (
                await self._session.scalars(
                    select(PancakePage).order_by(PancakePage.created_at, PancakePage.name)
                )
            ).all()
        )

    async def page(self, access: CskhAccess, page_row_id: uuid.UUID) -> PancakePage:
        access.require_manage()
        row = await self._session.get(PancakePage, page_row_id)
        if row is None:
            raise CskhPageNotFoundError()
        return row

    async def agents(self) -> list[AgentProfile]:
        """The agents a page may be answered by (every active one)."""
        return list(
            (
                await self._session.scalars(
                    select(AgentProfile)
                    .where(AgentProfile.active.is_(True))
                    .order_by(AgentProfile.sort_order, AgentProfile.name)
                )
            ).all()
        )

    # --- checks -----------------------------------------------------------------

    @staticmethod
    def _page_id(value: Any) -> str:
        text = str(value or "").strip()
        if not _PAGE_ID.match(text):
            raise CskhInvalidError(
                "cskh_page_id_invalid",
                "Nhập Page ID đúng như Pancake ghi (chữ, số, dấu _ . : -).",
                field="page_id",
            )
        return text

    @staticmethod
    def _name(value: Any) -> str:
        text = " ".join(str(value or "").split())
        if not text or len(text) > 120:
            raise CskhInvalidError(
                "cskh_name_invalid", "Nhập tên page (tối đa 120 ký tự).", field="name"
            )
        return text

    @staticmethod
    def _token(value: Any) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 1000 or any(ch.isspace() for ch in text):
            raise CskhInvalidError(
                "cskh_token_invalid",
                "Dán nguyên Page Access Token lấy ở Pancake > Cài đặt > Công cụ.",
                field="access_token",
            )
        return text

    @staticmethod
    def _tags(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > MAX_TAGS:
            raise CskhInvalidError(
                "cskh_tags_invalid", "Danh sách nhãn không hợp lệ.", field="handoff_tag_ids"
            )
        tags: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text or len(text) > 80:
                raise CskhInvalidError(
                    "cskh_tags_invalid",
                    "Mỗi nhãn là ID nhãn trên Pancake.",
                    field="handoff_tag_ids",
                )
            if text not in tags:
                tags.append(text)
        return tags

    @staticmethod
    def _mode(value: Any) -> ReplyMode:
        try:
            return ReplyMode(str(value))
        except ValueError as error:
            raise CskhInvalidError(
                "cskh_mode_invalid", "Chọn cách trả lời.", field="mode"
            ) from error

    async def _agent_slug(self, value: Any) -> str:
        slug = str(value or DEFAULT_AGENT_SLUG).strip()
        agent = await self._session.scalar(
            select(AgentProfile).where(AgentProfile.slug == slug, AgentProfile.active.is_(True))
        )
        if agent is None:
            raise CskhInvalidError(
                "cskh_agent_invalid", "Chọn một agent đang bật.", field="agent_slug"
            )
        return slug

    def _encrypt(self, page_row_id: uuid.UUID, token: str) -> str:
        try:
            box = self._secret_box()
        except ConfigurationError as error:
            raise CskhInvalidError(
                "cskh_secret_not_configured",
                "Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được token.",
                field="access_token",
            ) from error
        return box.encrypt(token, aad=token_aad(page_row_id))

    # --- writing ----------------------------------------------------------------

    async def _record(
        self,
        access: CskhAccess,
        action: AuditAction,
        page: PancakePage,
        request_id: uuid.UUID,
        changed: list[str] | None = None,
    ) -> None:
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=action,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="pancake_page",
                entity_id=str(page.id),
                after_data={
                    "page_id": page.page_id,
                    "name": page.name,
                    "mode": page.mode.value,
                    "agent_slug": page.agent_slug,
                    "active": page.active,
                    **({"changed": changed} if changed is not None else {}),
                },
            )
        )

    async def create(
        self, access: CskhAccess, data: dict[str, Any], *, request_id: uuid.UUID
    ) -> PancakePage:
        access.require_manage()
        page_id = self._page_id(data.get("page_id"))
        if await self._session.scalar(select(PancakePage.id).where(PancakePage.page_id == page_id)):
            raise CskhInvalidError("cskh_page_exists", "Page này đã được kết nối.", field="page_id")
        page = PancakePage(
            id=uuid.uuid4(),
            page_id=page_id,
            name=self._name(data.get("name")),
            mode=self._mode(data.get("mode") or ReplyMode.SUGGEST.value),
            agent_slug=await self._agent_slug(data.get("agent_slug")),
            webhook_key=new_webhook_key(),
            handoff_tag_ids=self._tags(data.get("handoff_tag_ids")),
            stop_when_assigned=bool(data.get("stop_when_assigned", False)),
            active=False,
            created_by_user_id=access.user_id,
        )
        token = data.get("access_token")
        if token:
            clean = self._token(token)
            page.access_token_encrypted = self._encrypt(page.id, clean)
            page.access_token_hint = token_hint(clean)
        page.active = self._may_be_active(page, bool(data.get("active", False)))
        self._session.add(page)
        await self._session.flush()
        await self._record(access, AuditAction.CSKH_PAGE_CREATED, page, request_id)
        return page

    @staticmethod
    def _may_be_active(page: PancakePage, wanted: bool) -> bool:
        if wanted and not page.access_token_encrypted:
            raise CskhInvalidError(
                "cskh_token_missing", "Nhập token của page trước khi bật.", field="access_token"
            )
        return wanted

    async def update(
        self,
        access: CskhAccess,
        page_row_id: uuid.UUID,
        data: dict[str, Any],
        *,
        request_id: uuid.UUID,
    ) -> PancakePage:
        """Only the keys sent change; a blank / missing token keeps the stored one."""
        page = await self.page(access, page_row_id)
        changed: list[str] = []
        if "name" in data:
            page.name = self._name(data["name"])
            changed.append("name")
        if "mode" in data:
            page.mode = self._mode(data["mode"])
            changed.append("mode")
        if "agent_slug" in data:
            page.agent_slug = await self._agent_slug(data["agent_slug"])
            changed.append("agent_slug")
        if "handoff_tag_ids" in data:
            page.handoff_tag_ids = self._tags(data["handoff_tag_ids"])
            changed.append("handoff_tag_ids")
        if "stop_when_assigned" in data:
            page.stop_when_assigned = bool(data["stop_when_assigned"])
            changed.append("stop_when_assigned")
        if data.get("access_token"):
            clean = self._token(data["access_token"])
            page.access_token_encrypted = self._encrypt(page.id, clean)
            page.access_token_hint = token_hint(clean)
            page.last_check_ok, page.last_check_message, page.last_checked_at = None, None, None
            changed.append("access_token")
        if "active" in data:
            page.active = self._may_be_active(page, bool(data["active"]))
            changed.append("active")
        if data.get("rotate_webhook_key"):
            page.webhook_key = new_webhook_key()
            changed.append("webhook_key")
        await self._session.flush()
        await self._record(access, AuditAction.CSKH_PAGE_UPDATED, page, request_id, changed)
        return page

    async def delete(
        self, access: CskhAccess, page_row_id: uuid.UUID, *, request_id: uuid.UUID
    ) -> None:
        page = await self.page(access, page_row_id)
        await self._record(access, AuditAction.CSKH_PAGE_DELETED, page, request_id)
        await self._session.delete(page)
        await self._session.flush()

    async def check(
        self, access: CskhAccess, page_row_id: uuid.UUID, client: PancakeClient
    ) -> PancakePage:
        """ "Kiểm tra token": one small read on Pancake, the result kept on the page."""
        page = await self.page(access, page_row_id)
        try:
            token = read_token(page, self._secret_box())
        except ConfigurationError:
            token = None
        if token is None:
            ok, message = False, "Chưa có token (hoặc server chưa có khoá mã hoá)."
        else:
            try:
                count = await client.check(page.page_id, token)
            except PancakeError as error:
                ok, message = False, error.message
            else:
                ok, message = True, f"Đọc được Pancake ({count} hội thoại gần nhất)."
        page.last_checked_at, page.last_check_ok, page.last_check_message = utcnow(), ok, message
        await self._session.flush()
        return page


__all__ = [
    "CskhPageService",
    "new_webhook_key",
    "read_token",
    "token_aad",
    "token_hint",
    "webhook_url",
]
