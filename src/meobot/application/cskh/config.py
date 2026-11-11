"""Kết nối page, nhãn, câu mẫu, cài đặt (0102): ``CSKH MANAGE`` (quick replies read by all).

Ported from ``hop-chat-apexmed``'s admin tabs ("Kết nối page", "Thẻ & câu mẫu",
"Cài đặt hộp chat"): the Pancake user tokens and POS keys live encrypted in
the database instead of ``config.json``; "Chủ page" / "Nhân viên trực" are APM
accounts (``cskh_page_members``), which also decides who sees the page.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import CskhAccess
from meobot.application.cskh.common import (
    JOB_KEY,
    SETTINGS_KEY,
    STATE_KEY,
    VN,
    CskhClients,
    account_aad,
    account_token,
    load_settings,
    names_of,
    pairs,
    pos_aad,
    pos_key,
    read_state,
    write_state,
)
from meobot.application.cskh.inbox import Actor, iso, page_members
from meobot.application.cskh.sync import (
    QR_READY,
    job_running,
    load_pos_shops,
    pancake_pages,
    start_job,
)
from meobot.core.time import utcnow
from meobot.db.models.cskh import (
    CskhConversation,
    CskhLabel,
    CskhPage,
    CskhPageLabel,
    CskhPageMember,
    CskhPancakeAccount,
    CskhPosKey,
    CskhPosShop,
    CskhQuickReply,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.cskh.errors import (
    CskhConflictError,
    CskhInvalidError,
    CskhNotFoundError,
    CskhUpstreamRefusedError,
)
from meobot.domain.cskh.models import (
    LABEL_COLORS,
    MEMBER_ROLE_LABELS,
    CskhUpstreamError,
    InboxSettings,
    MemberRole,
    clean_shortcut,
    parse_quick_replies,
    settings_from,
    token_hint,
    valid_hhmm,
)

MAX_KEYS = 20
MAX_LABELS = 200


class CskhConfig:
    def __init__(self, session: AsyncSession, clients: CskhClients, audit: AuditService) -> None:
        self._session = session
        self._clients = clients
        self._audit = audit

    async def _audit_it(
        self,
        actor: Actor,
        action: AuditAction,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            AuditEntry(
                request_id=actor.request_id,
                action=action,
                result=AuditResult.SUCCESS,
                actor_user_id=actor.user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                after_data=data,
            )
        )

    # --- the overview -----------------------------------------------------------------

    async def overview(self, access: CskhAccess) -> dict[str, Any]:
        access.require_manage()
        accounts = (
            await self._session.scalars(
                select(CskhPancakeAccount).order_by(
                    CskhPancakeAccount.sort_order, CskhPancakeAccount.created_at
                )
            )
        ).all()
        keys = (
            await self._session.scalars(
                select(CskhPosKey).order_by(CskhPosKey.sort_order, CskhPosKey.created_at)
            )
        ).all()
        shops = (await self._session.scalars(select(CskhPosShop).order_by(CskhPosShop.name))).all()
        settings = await load_settings(self._session)
        state = await read_state(self._session, STATE_KEY)
        job = await read_state(self._session, JOB_KEY)
        if job.get("running") and not await job_running(self._session):
            job["running"] = False
            job["stale"] = True
        return {
            "pancake_accounts": [
                {
                    "id": str(a.id),
                    "label": a.label,
                    "token_hint": a.token_hint,
                    "expires_on": iso(a.expires_on),
                    "page_count": a.page_count,
                    "active_count": a.active_count,
                    "last_error": a.last_error,
                    "checked_at": iso(a.checked_at),
                }
                for a in accounts
            ],
            "pos_keys": [
                {
                    "id": str(k.id),
                    "label": k.label,
                    "key_hint": k.key_hint,
                    "shop_count": k.shop_count,
                    "last_error": k.last_error,
                    "checked_at": iso(k.checked_at),
                }
                for k in keys
            ],
            "pos_shops": [
                {
                    "id": s.id,
                    "name": s.name,
                    "pages": len(s.pages or []),
                    "dup_check": not settings.dup_skipped(s.name),
                }
                for s in shops
            ],
            "pages": await self.pages(),
            "labels": await self.labels(),
            "people": await self.people(),
            "new_pages": int(state.get("new_pages") or 0),
            "new_pages_checked_at": state.get("checked_at"),
            "job": job or None,
            "settings": settings.as_dict(),
            "secret_ready": self._clients.secret_ready(),
            "qr_ready": QR_READY,
            "member_roles": [
                {"value": r.value, "label": MEMBER_ROLE_LABELS[r]} for r in MemberRole
            ],
            "label_colors": list(LABEL_COLORS),
        }

    async def pages(self) -> list[dict[str, Any]]:
        pages = list((await self._session.scalars(select(CskhPage).order_by(CskhPage.name))).all())
        ids = [p.id for p in pages]
        stats = {
            page_id: (int(count or 0), int(unread or 0), last)
            for page_id, count, unread, last in (
                await self._session.execute(
                    select(
                        CskhConversation.page_id,
                        func.count(),
                        func.coalesce(func.sum(CskhConversation.unread), 0),
                        func.max(CskhConversation.last_at),
                    ).group_by(CskhConversation.page_id)
                )
            ).all()
        }
        labels: dict[str, list[str]] = {}
        for page_id, label_id in (
            await self._session.execute(select(CskhPageLabel.page_id, CskhPageLabel.label_id))
        ).all():
            labels.setdefault(page_id, []).append(str(label_id))
        members = await page_members(self._session, ids)
        marketers = await names_of(self._session, (p.marketer_user_id for p in pages))
        shops: dict[Any, Any] = pairs(
            (await self._session.execute(select(CskhPosShop.id, CskhPosShop.name))).all()
        )
        settings = await load_settings(self._session)
        out = []
        for page in pages:
            count, unread, last = stats.get(page.id, (0, 0, None))
            out.append(
                {
                    "id": page.id,
                    "name": page.name,
                    "username": page.username,
                    "active": page.active,
                    "connected": bool(page.token_encrypted) and page.active,
                    "token_hint": page.token_hint,
                    "skipped": settings.skipped(page.name),
                    "marketer_user_id": str(page.marketer_user_id)
                    if page.marketer_user_id
                    else None,
                    "marketer_name": marketers.get(page.marketer_user_id)
                    if page.marketer_user_id
                    else None,
                    "members": members.get(page.id, []),
                    "label_ids": labels.get(page.id, []),
                    "pos_shop_id": page.pos_shop_id,
                    "pos_shop_name": shops.get(page.pos_shop_id) if page.pos_shop_id else None,
                    "conversations": count,
                    "unread": unread,
                    "last_at": iso(last),
                    "polled_at": iso(page.polled_at),
                    "poll_error": page.poll_error,
                    "meta_at": iso(page.meta_at),
                }
            )
        return out

    async def people(self) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                select(User.id, User.full_name, User.member_code)
                .where(User.deleted_at.is_(None), User.active.is_(True))
                .order_by(User.full_name)
            )
        ).all()
        return [{"user_id": str(i), "name": name, "member_code": code} for i, name, code in rows]

    # --- Pancake accounts ---------------------------------------------------------------

    async def add_pancake_account(
        self, access: CskhAccess, data: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        access.require_manage()
        token = _secret(data.get("token"), "token", "Dán API Access Token của tài khoản Pancake.")
        count = await self._session.scalar(select(func.count()).select_from(CskhPancakeAccount))
        if int(count or 0) >= MAX_KEYS:
            raise CskhInvalidError("cskh_too_many", "Tối đa 20 tài khoản Pancake.")
        try:
            listed = await self._clients.pancake.list_pages(token)
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        row = CskhPancakeAccount(
            id=uuid.uuid4(),
            label=_label(data.get("label"), f"Tài khoản Pancake {int(count or 0) + 1}"),
            token_hint=token_hint(token),
            token_encrypted="",
            sort_order=int(count or 0),
            expires_on=_date(data.get("expires_on")),
            page_count=len(listed),
            active_count=sum(1 for p in listed if p.status == "activated"),
            checked_at=utcnow(),
            created_by_user_id=actor.user_id,
        )
        row.token_encrypted = self._clients.box().encrypt(token, aad=account_aad(row.id))
        for existing in (await self._session.scalars(select(CskhPancakeAccount))).all():
            # Same last characters: compare the real tokens before refusing.
            if (
                existing.token_hint == row.token_hint
                and account_token(self._clients, existing) == token
            ):
                raise CskhInvalidError(
                    "cskh_duplicate_key", "Token này đã được khai báo.", field="token"
                )
        self._session.add(row)
        await self._session.flush()
        await self._audit_it(
            actor,
            AuditAction.CSKH_PANCAKE_ACCOUNT_SAVED,
            "cskh_pancake_account",
            str(row.id),
            {"label": row.label},
        )
        return {"id": str(row.id), "page_count": row.page_count, "active_count": row.active_count}

    async def update_pancake_account(
        self, access: CskhAccess, account_id: uuid.UUID, data: dict[str, Any], actor: Actor
    ) -> None:
        access.require_manage()
        row = await self._session.get(CskhPancakeAccount, account_id)
        if row is None:
            raise CskhNotFoundError()
        if "label" in data:
            row.label = _label(data.get("label"), row.label)
        if "expires_on" in data:
            row.expires_on = _date(data.get("expires_on"))
        if "sort_order" in data:
            row.sort_order = int(data.get("sort_order") or 0)
        if data.get("token"):
            token = _secret(
                data.get("token"), "token", "Dán API Access Token của tài khoản Pancake."
            )
            try:
                await self._clients.pancake.list_pages(token)
            except CskhUpstreamError as error:
                raise CskhUpstreamRefusedError(error.code, error.message) from error
            row.token_encrypted = self._clients.box().encrypt(token, aad=account_aad(row.id))
            row.token_hint = token_hint(token)
            row.last_error = None
        await self._session.flush()
        await self._audit_it(
            actor,
            AuditAction.CSKH_PANCAKE_ACCOUNT_SAVED,
            "cskh_pancake_account",
            str(row.id),
            {"label": row.label, "token_changed": bool(data.get("token"))},
        )

    async def remove_pancake_account(
        self, access: CskhAccess, account_id: uuid.UUID, actor: Actor
    ) -> None:
        access.require_manage()
        row = await self._session.get(CskhPancakeAccount, account_id)
        if row is None:
            raise CskhNotFoundError()
        await self._session.delete(row)
        await self._session.flush()
        await self._audit_it(
            actor, AuditAction.CSKH_PANCAKE_ACCOUNT_REMOVED, "cskh_pancake_account", str(account_id)
        )

    async def pancake_listing(self, access: CskhAccess) -> list[dict[str, Any]]:
        """Every page on the Pancake accounts, with whether it is connected / skipped."""
        access.require_manage()
        listed = await pancake_pages(self._session, self._clients)
        connected = {p.id: p for p in (await self._session.scalars(select(CskhPage))).all()}
        settings = await load_settings(self._session)
        return [
            {
                "id": info.id,
                "name": info.name,
                "username": info.username,
                "platform": info.platform,
                "status": info.status,
                "account_label": account.label,
                "connected": info.id in connected
                and bool(connected[info.id].token_encrypted)
                and connected[info.id].active,
                "known": info.id in connected,
                "skipped": settings.skipped(info.name),
            }
            for info, account in listed
        ]

    # --- POS keys --------------------------------------------------------------------------

    async def add_pos_key(
        self, access: CskhAccess, data: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        access.require_manage()
        key = _secret(
            data.get("key"), "key", "Dán API key POS Cake (Cài đặt > Ứng dụng > Webhook/API)."
        )
        existing = list((await self._session.scalars(select(CskhPosKey))).all())
        if len(existing) >= MAX_KEYS:
            raise CskhInvalidError("cskh_too_many", "Tối đa 20 API key POS.")
        if any(
            row.key_hint == token_hint(key) and pos_key(self._clients, row) == key
            for row in existing
        ):
            raise CskhInvalidError(
                "cskh_duplicate_key", "API key này đã được khai báo.", field="key"
            )
        try:
            shops = await self._clients.pos.shops(key)
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        row = CskhPosKey(
            id=uuid.uuid4(),
            label=_label(data.get("label"), f"Tài khoản POS {len(existing) + 1}"),
            key_encrypted="",
            key_hint=token_hint(key),
            sort_order=len(existing),
            shop_count=len(shops),
            created_by_user_id=actor.user_id,
        )
        row.key_encrypted = self._clients.box().encrypt(key, aad=pos_aad(row.id))
        self._session.add(row)
        await self._session.flush()
        shop_count = await load_pos_shops(self._session, self._clients)
        await self._audit_it(
            actor, AuditAction.CSKH_POS_KEY_SAVED, "cskh_pos_key", str(row.id), {"label": row.label}
        )
        return {"id": str(row.id), "shops": shop_count}

    async def remove_pos_key(self, access: CskhAccess, key_id: uuid.UUID, actor: Actor) -> None:
        access.require_manage()
        row = await self._session.get(CskhPosKey, key_id)
        if row is None:
            raise CskhNotFoundError()
        await self._session.delete(row)
        await self._session.flush()
        if await self._session.scalar(select(func.count()).select_from(CskhPosKey)):
            with contextlib.suppress(CskhUpstreamRefusedError):
                await load_pos_shops(self._session, self._clients)
        else:
            for page in (await self._session.scalars(select(CskhPage))).all():
                page.pos_shop_id = None
        await self._session.flush()
        await self._audit_it(actor, AuditAction.CSKH_POS_KEY_REMOVED, "cskh_pos_key", str(key_id))

    async def reload_pos(self, access: CskhAccess) -> int:
        access.require_manage()
        return await load_pos_shops(self._session, self._clients)

    # --- pages -------------------------------------------------------------------------------

    async def begin_connect(
        self, access: CskhAccess, page_ids: Sequence[str] | None, actor: Actor
    ) -> list[str]:
        """The pages to connect (all activated, not skipped, when none named); job state set."""
        access.require_manage()
        if not await self._session.scalar(select(func.count()).select_from(CskhPancakeAccount)):
            raise CskhInvalidError("cskh_pancake_missing", "Chưa có token tài khoản Pancake.")
        if await job_running(self._session):
            raise CskhConflictError(
                "cskh_job_running", "Đang có một lượt kết nối chạy, chờ xong rồi thử lại."
            )
        if page_ids is None:
            settings = await load_settings(self._session)
            listed = await pancake_pages(self._session, self._clients)
            ids = [
                info.id
                for info, _ in listed
                if info.status == "activated" and not settings.skipped(info.name)
            ]
            label = "Đồng bộ toàn bộ page"
        else:
            ids = [str(p).strip()[:40] for p in page_ids if str(p).strip()]
            label = "Tạo kết nối page"
        if not ids:
            raise CskhInvalidError("cskh_no_pages", "Chưa chọn page nào.")
        await start_job(self._session, label=label, total=len(ids), user_id=actor.user_id)
        await self._audit_it(
            actor,
            AuditAction.CSKH_PAGE_CREATED,
            "cskh_page",
            ",".join(ids)[:200],
            {"pages": len(ids), "label": label},
        )
        return ids

    async def update_pages(
        self, access: CskhAccess, page_ids: Sequence[str], data: dict[str, Any], actor: Actor
    ) -> int:
        """One page or several: on / off, marketer, members (set / add / remove), labels."""
        access.require_manage()
        pages = list(
            (
                await self._session.scalars(select(CskhPage).where(CskhPage.id.in_(list(page_ids))))
            ).all()
        )
        if not pages:
            raise CskhNotFoundError("cskh_page_not_found", "Không tìm thấy page.")
        changed: list[str] = []
        if "active" in data:
            for page in pages:
                if data["active"] and not page.token_encrypted:
                    raise CskhInvalidError(
                        "cskh_token_missing", f"Page {page.name} chưa có token: bấm Kết nối trước."
                    )
                page.active = bool(data["active"])
            changed.append("active")
        if "marketer_user_id" in data:
            marketer = await self._person(data.get("marketer_user_id"), "marketer_user_id")
            for page in pages:
                page.marketer_user_id = marketer
            changed.append("marketer")
        if "members" in data:
            members = await self._members(data.get("members"))
            mode = str(data.get("member_mode") or "set")
            for page in pages:
                if mode == "set":
                    await self._session.execute(
                        delete(CskhPageMember).where(CskhPageMember.page_id == page.id)
                    )
                    await self._session.flush()
                for user_id, role in members:
                    current = await self._session.get(CskhPageMember, (page.id, user_id))
                    if mode == "remove":
                        if current is not None:
                            await self._session.delete(current)
                    elif current is None:
                        self._session.add(
                            CskhPageMember(page_id=page.id, user_id=user_id, role=role.value)
                        )
                    else:
                        current.role = role.value
            changed.append("members")
        if "label_ids" in data:
            label_ids = await self._labels(data.get("label_ids"))
            mode = str(data.get("label_mode") or "set")
            for page in pages:
                if mode == "set":
                    await self._session.execute(
                        delete(CskhPageLabel).where(CskhPageLabel.page_id == page.id)
                    )
                    await self._session.flush()
                for label_id in label_ids:
                    current_label = await self._session.get(CskhPageLabel, (page.id, label_id))
                    if mode == "remove":
                        if current_label is not None:
                            await self._session.delete(current_label)
                    elif current_label is None:
                        self._session.add(CskhPageLabel(page_id=page.id, label_id=label_id))
            changed.append("labels")
        await self._session.flush()
        await self._audit_it(
            actor,
            AuditAction.CSKH_PAGE_UPDATED,
            "cskh_page",
            ",".join(p.id for p in pages)[:200],
            {"pages": len(pages), "changed": changed},
        )
        return len(pages)

    async def disconnect(self, access: CskhAccess, page_id: str, actor: Actor) -> None:
        """Forget the page token and stop reading (conversations stay)."""
        access.require_manage()
        page = await self._session.get(CskhPage, page_id)
        if page is None:
            raise CskhNotFoundError("cskh_page_not_found", "Không tìm thấy page.")
        page.token_encrypted = None
        page.token_hint = ""
        page.active = False
        await self._session.flush()
        await self._audit_it(
            actor, AuditAction.CSKH_PAGE_DELETED, "cskh_page", page.id, {"name": page.name}
        )

    async def _person(self, raw: object, field: str) -> uuid.UUID | None:
        if not raw:
            return None
        try:
            user_id = uuid.UUID(str(raw))
        except ValueError as error:
            raise CskhInvalidError(
                "cskh_person_invalid", "Người được chọn không hợp lệ.", field=field
            ) from error
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise CskhInvalidError("cskh_person_invalid", "Người được chọn không còn.", field=field)
        return user_id

    async def _members(self, raw: object) -> list[tuple[uuid.UUID, MemberRole]]:
        out: list[tuple[uuid.UUID, MemberRole]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            user_id = await self._person(item.get("user_id"), "members")
            try:
                role = MemberRole(str(item.get("role") or "STAFF"))
            except ValueError as error:
                raise CskhInvalidError(
                    "cskh_role_invalid", "Vai trò không hợp lệ.", field="members"
                ) from error
            if user_id is not None:
                out.append((user_id, role))
        return out

    async def _labels(self, raw: object) -> list[uuid.UUID]:
        out: list[uuid.UUID] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                label_id = uuid.UUID(str(item))
            except ValueError as error:
                raise CskhInvalidError(
                    "cskh_label_invalid", "Nhãn không hợp lệ.", field="label_ids"
                ) from error
            if await self._session.get(CskhLabel, label_id) is None:
                raise CskhInvalidError("cskh_label_invalid", "Nhãn không còn.", field="label_ids")
            out.append(label_id)
        return out

    # --- labels -----------------------------------------------------------------------------

    async def labels(self) -> list[dict[str, Any]]:
        counts: dict[Any, Any] = pairs(
            (
                await self._session.execute(
                    select(CskhPageLabel.label_id, func.count()).group_by(CskhPageLabel.label_id)
                )
            ).all()
        )
        rows = (await self._session.scalars(select(CskhLabel).order_by(CskhLabel.name))).all()
        return [
            {"id": str(r.id), "name": r.name, "color": r.color, "pages": int(counts.get(r.id, 0))}
            for r in rows
        ]

    async def save_label(
        self, access: CskhAccess, label_id: uuid.UUID | None, data: dict[str, Any]
    ) -> dict[str, Any]:
        access.require_manage()
        name = " ".join(str(data.get("name") or "").split())[:80]
        if not name:
            raise CskhInvalidError("cskh_label_name", "Nhập tên nhãn.", field="name")
        color = str(data.get("color") or "blue")
        if color not in LABEL_COLORS:
            color = "blue"
        clash = await self._session.scalar(
            select(CskhLabel).where(func.lower(CskhLabel.name) == name.lower())
        )
        if clash is not None and clash.id != label_id:
            raise CskhInvalidError("cskh_label_exists", "Đã có nhãn tên này.", field="name")
        if label_id is None:
            if (
                int(await self._session.scalar(select(func.count()).select_from(CskhLabel)) or 0)
                >= MAX_LABELS
            ):
                raise CskhInvalidError("cskh_too_many", "Tối đa 200 nhãn.")
            row = CskhLabel(id=uuid.uuid4(), name=name, color=color)
            self._session.add(row)
        else:
            found = await self._session.get(CskhLabel, label_id)
            if found is None:
                raise CskhNotFoundError()
            row = found
            row.name, row.color = name, color
        await self._session.flush()
        return {"id": str(row.id), "name": row.name, "color": row.color}

    async def delete_label(self, access: CskhAccess, label_id: uuid.UUID) -> None:
        access.require_manage()
        row = await self._session.get(CskhLabel, label_id)
        if row is None:
            raise CskhNotFoundError()
        await self._session.delete(row)
        await self._session.flush()

    # --- quick replies ----------------------------------------------------------------------

    async def quick_replies(self, access: CskhAccess) -> list[dict[str, Any]]:
        access.require_manage()
        names: dict[Any, Any] = pairs(
            (await self._session.execute(select(CskhPage.id, CskhPage.name))).all()
        )
        rows = (
            await self._session.scalars(select(CskhQuickReply).order_by(CskhQuickReply.shortcut))
        ).all()
        return [
            {
                "id": str(r.id),
                "shortcut": r.shortcut,
                "text": r.text,
                "page_id": r.page_id,
                "page_name": names.get(r.page_id) if r.page_id else None,
            }
            for r in rows
        ]

    async def _clash(
        self, shortcut: str, page_id: str | None, own: uuid.UUID | None
    ) -> CskhQuickReply | None:
        query = select(CskhQuickReply).where(CskhQuickReply.shortcut == shortcut)
        query = query.where(
            CskhQuickReply.page_id.is_(None)
            if page_id is None
            else CskhQuickReply.page_id == page_id
        )
        row: CskhQuickReply | None = await self._session.scalar(query)
        return row if row is not None and row.id != own else None

    async def _page_or_none(self, raw: object) -> str | None:
        page_id = str(raw or "").strip() or None
        if page_id and await self._session.get(CskhPage, page_id) is None:
            raise CskhInvalidError("cskh_page_not_found", "Không tìm thấy page.", field="page_id")
        return page_id

    async def save_quick_reply(
        self, access: CskhAccess, reply_id: uuid.UUID | None, data: dict[str, Any]
    ) -> dict[str, Any]:
        access.require_manage()
        shortcut = clean_shortcut(data.get("shortcut"))
        text = str(data.get("text") or "").strip()[:4000]
        if not shortcut or not text:
            raise CskhInvalidError("cskh_quick_reply_invalid", "Nhập ký hiệu và nội dung.")
        page_id = await self._page_or_none(data.get("page_id"))
        if await self._clash(shortcut, page_id, reply_id):
            raise CskhInvalidError(
                "cskh_quick_reply_exists",
                f"Ký hiệu /{shortcut} đã có trong phạm vi này.",
                field="shortcut",
            )
        if reply_id is None:
            row = CskhQuickReply(id=uuid.uuid4(), shortcut=shortcut, text=text, page_id=page_id)
            self._session.add(row)
        else:
            found = await self._session.get(CskhQuickReply, reply_id)
            if found is None:
                raise CskhNotFoundError()
            row = found
            row.shortcut, row.text, row.page_id = shortcut, text, page_id
        await self._session.flush()
        return {
            "id": str(row.id),
            "shortcut": row.shortcut,
            "text": row.text,
            "page_id": row.page_id,
        }

    async def delete_quick_reply(self, access: CskhAccess, reply_id: uuid.UUID) -> None:
        access.require_manage()
        row = await self._session.get(CskhQuickReply, reply_id)
        if row is None:
            raise CskhNotFoundError()
        await self._session.delete(row)
        await self._session.flush()

    async def import_quick_replies(
        self, access: CskhAccess, text: str, page_id: object
    ) -> dict[str, Any]:
        access.require_manage()
        scope = await self._page_or_none(page_id)
        added, skipped = 0, []
        for shortcut, body in parse_quick_replies(text):
            if await self._clash(shortcut, scope, None):
                skipped.append(shortcut)
                continue
            self._session.add(
                CskhQuickReply(id=uuid.uuid4(), shortcut=shortcut, text=body[:4000], page_id=scope)
            )
            await self._session.flush()
            added += 1
        return {"added": added, "skipped": skipped}

    async def share_quick_replies(
        self, access: CskhAccess, ids: Iterable[uuid.UUID] | None, overwrite: bool
    ) -> dict[str, Any]:
        """A page's own replies become shared by every page (asks before overwriting)."""
        access.require_manage()
        query = select(CskhQuickReply).where(CskhQuickReply.page_id.is_not(None))
        wanted = list(ids or [])
        if wanted:
            query = query.where(CskhQuickReply.id.in_(wanted))
        moved, conflict = 0, []
        for row in (await self._session.scalars(query)).all():
            shared = await self._clash(row.shortcut, None, None)
            if shared is not None and shared.text != row.text and not overwrite:
                conflict.append(row.shortcut)
                continue
            if shared is not None:
                shared.text = row.text
                await self._session.delete(row)
            else:
                row.page_id = None
            await self._session.flush()
            moved += 1
        return {"moved": moved, "conflict": conflict}

    # --- settings ----------------------------------------------------------------------------

    async def save_settings(
        self, access: CskhAccess, data: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        access.require_manage()
        current = (await load_settings(self._session)).as_dict()
        merged = {**current, **{k: v for k, v in data.items() if k in current}}
        for key in ("work_start", "work_end"):
            if not valid_hhmm(str(merged[key])):
                raise CskhInvalidError(
                    "cskh_settings_invalid", "Giờ dạng HH:MM (24 giờ).", field=key
                )
        if str(merged["work_start"]) >= str(merged["work_end"]):
            raise CskhInvalidError(
                "cskh_settings_invalid", "Giờ bắt đầu phải trước giờ kết thúc.", field="work_end"
            )
        days = merged.get("work_days")
        if not isinstance(days, list) or any(
            not isinstance(d, int) or not 0 <= d <= 6 for d in days
        ):
            raise CskhInvalidError(
                "cskh_settings_invalid", "Chọn ngày làm việc.", field="work_days"
            )
        text = str(merged.get("auto_reply_text") or "").strip()
        if not text or len(text) > 1000:
            raise CskhInvalidError(
                "cskh_settings_invalid",
                "Nhập tin tự động (tối đa 1000 ký tự).",
                field="auto_reply_text",
            )
        lock = merged.get("mkt_lock_days")
        if not isinstance(lock, int) or not 0 <= lock <= 3650:
            raise CskhInvalidError(
                "cskh_settings_invalid", "Số ngày giữ marketing 0-3650.", field="mkt_lock_days"
            )
        for key in ("order_statuses", "skip_keywords", "pos_dup_skip_keywords"):
            value = merged.get(key)
            if not isinstance(value, list) or len(value) > 100:
                raise CskhInvalidError(
                    "cskh_settings_invalid", "Danh sách không hợp lệ.", field=key
                )
            merged[key] = [str(v).strip()[:120] for v in value if str(v).strip()]
        merged["auto_reply_text"] = text
        merged["auto_reply_enabled"] = bool(merged.get("auto_reply_enabled"))
        settings: InboxSettings = settings_from(merged)
        await write_state(
            self._session,
            SETTINGS_KEY,
            settings.as_dict(),
            user_id=actor.user_id,
            description="Hộp chat CSKH: giờ làm, tin tự động, trạng thái đơn, giữ marketing.",
        )
        await self._audit_it(
            actor,
            AuditAction.CSKH_SETTINGS_UPDATED,
            "system_setting",
            SETTINGS_KEY,
            {"keys": sorted(k for k in data if k in current)},
        )
        return settings.as_dict()


def _secret(raw: object, field: str, message: str) -> str:
    text = str(raw or "").strip()
    if not text or len(text) > 2000 or any(ch.isspace() for ch in text):
        raise CskhInvalidError("cskh_token_invalid", message, field=field)
    return text


def _label(raw: object, default: str) -> str:
    text = " ".join(str(raw or "").split())[:120]
    return text or default


def _date(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError as error:
        raise CskhInvalidError(
            "cskh_date_invalid", "Ngày không hợp lệ.", field="expires_on"
        ) from error
    return value if value.tzinfo else value.replace(tzinfo=VN)


__all__ = ["CskhConfig"]
