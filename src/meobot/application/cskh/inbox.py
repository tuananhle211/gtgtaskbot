"""Hộp chat page (0102): read, answer, tag, confirm phone / QR into POS Cake.

Ported from ``hop-chat-apexmed``: ``/api/state``, ``/api/messages``,
``/api/send``, ``/api/conv/pc_tag|assign``, ``/api/search`` (``search_convs``),
``/api/phone/check`` (``phone_check``), ``/api/pos/submit`` (``pos_confirm`` /
``pos_create``). Every read is narrowed to the caller's pages
(``page_scope``); another page's conversation is "not found".
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import CskhAccess, page_scope
from meobot.application.cskh.codes import codes_of, noted_codes
from meobot.application.cskh.common import (
    CskhClients,
    load_settings,
    names_of,
    page_token,
    pairs,
    pos_key,
    vn_now,
)
from meobot.application.cskh.sync import (
    CskhSync,
    load_pos_shops,
    same_session,
    send_text,
    set_phones,
    set_stage,
)
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cskh import (
    CskhAd,
    CskhConversation,
    CskhConversationCode,
    CskhLabel,
    CskhMessage,
    CskhOrder,
    CskhPage,
    CskhPageLabel,
    CskhPageMember,
    CskhPancakeTag,
    CskhPancakeUser,
    CskhPosKey,
    CskhPosShop,
    CskhQuickReply,
    CskhZaloThread,
)
from meobot.db.models.department import Department, DepartmentMember
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.cskh.errors import (
    CskhConflictError,
    CskhForbiddenError,
    CskhInvalidError,
    CskhNotFoundError,
    CskhUpstreamRefusedError,
)
from meobot.domain.cskh.models import (
    CONFIRMED_POS,
    DEFAULT_PLATFORM,
    MEMBER_ROLE_LABELS,
    PENDING_POS,
    PLATFORMS,
    POS_STATUS,
    STAGE_LABELS,
    ConfirmType,
    ConvStage,
    CskhUpstreamError,
    MemberRole,
    OrderInfo,
    clip,
    find_phones,
    fold,
    is_work_time,
    normalize_code,
    order_note_text,
    pancake_time,
    phone_key,
)

PAGE_SIZE = 60
#: "Xếp: Chờ lâu nhất" puts first the customers whose last message, this recent, has
#: no answer yet (older silences are no longer "waiting").
WAIT_WINDOW = timedelta(hours=24)
MAX_MESSAGES = 500
SEARCH_LIMIT = 200
#: The duplicate check's POS answers are kept this long per number.
POS_CACHE_SECONDS = 60
_POS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def iso(value: datetime | None) -> str | None:
    return ensure_utc(value).isoformat() if value else None


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: uuid.UUID | None
    name: str
    request_id: uuid.UUID


class CskhInbox:
    def __init__(self, session: AsyncSession, clients: CskhClients, audit: AuditService) -> None:
        self._session = session
        self._clients = clients
        self._audit = audit

    # --- scope ----------------------------------------------------------------------

    async def _pages(self, access: CskhAccess) -> set[str] | None:
        access.require_inbox()
        return await page_scope(self._session, access)

    async def conversation(
        self, access: CskhAccess, conversation_id: uuid.UUID
    ) -> CskhConversation:
        allowed = await self._pages(access)
        conv = await self._session.get(CskhConversation, conversation_id)
        if conv is None or (allowed is not None and conv.page_id not in allowed):
            raise CskhNotFoundError("cskh_conversation_not_found", "Không tìm thấy hội thoại.")
        return conv

    # --- the inbox's frame ------------------------------------------------------------

    async def state(self, access: CskhAccess) -> dict[str, Any]:
        allowed = await self._pages(access)
        pages_q = select(CskhPage).order_by(CskhPage.name)
        if allowed is not None:
            pages_q = pages_q.where(CskhPage.id.in_(allowed))
        pages = list((await self._session.scalars(pages_q)).all())
        ids = [p.id for p in pages]
        unread: dict[Any, Any] = pairs(
            (
                await self._session.execute(
                    select(
                        CskhConversation.page_id,
                        func.coalesce(func.sum(CskhConversation.unread), 0),
                    )
                    .where(CskhConversation.page_id.in_(ids))
                    .group_by(CskhConversation.page_id)
                )
            ).all()
        )
        page_labels: dict[str, list[str]] = {}
        for page_id, label_id in (
            await self._session.execute(
                select(CskhPageLabel.page_id, CskhPageLabel.label_id).where(
                    CskhPageLabel.page_id.in_(ids)
                )
            )
        ).all():
            page_labels.setdefault(page_id, []).append(str(label_id))
        members = await page_members(self._session, ids)
        marketers = await names_of(self._session, (p.marketer_user_id for p in pages))
        tags: dict[str, list[dict[str, str]]] = {}
        for row in (
            await self._session.scalars(
                select(CskhPancakeTag).where(CskhPancakeTag.page_id.in_(ids))
            )
        ).all():
            tags.setdefault(row.page_id, []).append(
                {"id": row.tag_id, "text": row.text, "color": row.color}
            )
        for items in tags.values():
            items.sort(key=lambda t: (len(t["id"]), t["id"]))
        users: dict[str, list[dict[str, str]]] = {}
        for user_row in (
            await self._session.scalars(
                select(CskhPancakeUser)
                .where(CskhPancakeUser.page_id.in_(ids))
                .order_by(CskhPancakeUser.name)
            )
        ).all():
            users.setdefault(user_row.page_id, []).append(
                {"id": user_row.user_id, "name": user_row.name}
            )
        labels = (await self._session.scalars(select(CskhLabel).order_by(CskhLabel.name))).all()
        replies_q = select(CskhQuickReply).order_by(CskhQuickReply.shortcut)
        if allowed is not None:
            replies_q = replies_q.where(
                or_(CskhQuickReply.page_id.is_(None), CskhQuickReply.page_id.in_(allowed))
            )
        replies = (await self._session.scalars(replies_q)).all()
        settings = await load_settings(self._session)
        campaigns = (
            await self._session.execute(
                select(CskhAd.campaign_id, CskhAd.campaign_name)
                .where(CskhAd.page_id.in_(ids), CskhAd.campaign_id != "")
                .distinct()
            )
        ).all()
        return {
            "pages": [
                {
                    "id": p.id,
                    "name": p.name,
                    "connected": bool(p.token_encrypted) and p.active,
                    "unread": int(unread.get(p.id, 0) or 0),
                    "label_ids": page_labels.get(p.id, []),
                    "members": members.get(p.id, []),
                    "marketer_user_id": str(p.marketer_user_id) if p.marketer_user_id else None,
                    "marketer_name": marketers.get(p.marketer_user_id)
                    if p.marketer_user_id
                    else None,
                    "pos_shop_id": p.pos_shop_id,
                    "pancake_tags": tags.get(p.id, []),
                    "pancake_users": users.get(p.id, []),
                    "short_name": p.short_name,
                    "color": p.color,
                }
                for p in pages
            ],
            "labels": [
                {"id": str(label.id), "name": label.name, "color": label.color} for label in labels
            ],
            "quick_replies": [
                {"id": str(r.id), "shortcut": r.shortcut, "text": r.text, "page_id": r.page_id}
                for r in replies
            ],
            "campaigns": sorted(
                [{"id": cid, "name": name} for cid, name in campaigns], key=lambda c: c["name"]
            ),
            "order_statuses": list(settings.order_statuses),
            "platforms": list(PLATFORMS),
            "work_now": is_work_time(settings, vn_now()),
            "auto_reply_enabled": settings.auto_reply_enabled,
            "wait_alert_minutes": settings.wait_alert_minutes,
            "stages": [{"id": stage.value, "label": STAGE_LABELS[stage]} for stage in ConvStage],
            "callback_total": int(
                await self._session.scalar(
                    select(func.count())
                    .select_from(CskhConversation)
                    .where(CskhConversation.page_id.in_(ids), CskhConversation.callback.is_(True))
                )
                or 0
            ),
            "unread_total": sum(int(v or 0) for v in unread.values()),
            "people": await self._people_list(),
        }

    async def _people_list(self) -> list[dict[str, Any]]:
        """Active accounts with their departments' codes: the confirmation's pickers
        (Marketing = ADS first, Telesale = TELE first, Người tạo)."""
        seats: dict[uuid.UUID, list[str]] = {}
        for user_id, code in (
            await self._session.execute(
                select(DepartmentMember.user_id, Department.code)
                .join(Department, Department.id == DepartmentMember.department_id)
                .where(DepartmentMember.left_at.is_(None), Department.active.is_(True))
            )
        ).all():
            seats.setdefault(user_id, []).append(code)
        rows = (
            await self._session.execute(
                select(User.id, User.full_name)
                .where(User.deleted_at.is_(None), User.active.is_(True))
                .order_by(User.full_name)
            )
        ).all()
        return [
            {"user_id": str(user_id), "name": name, "departments": sorted(seats.get(user_id, []))}
            for user_id, name in rows
        ]

    # --- the list ---------------------------------------------------------------------

    async def conversations(
        self,
        access: CskhAccess,
        *,
        page_id: str | None = None,
        label_id: uuid.UUID | None = None,
        pancake_tag: str | None = None,
        campaign_id: str | None = None,
        tab: str = "all",
        q: str | None = None,
        before: datetime | None = None,
        limit: int = PAGE_SIZE,
        stage: str | None = None,
        mine: bool = False,
        sort: str = "recent",
        offset: int = 0,
    ) -> dict[str, Any]:
        """``sort`` "recent" (newest first, paged by ``before``) or "wait" (the longest
        unanswered first, then the rest newest first; paged by ``offset``)."""
        allowed = await self._pages(access)
        query = select(CskhConversation).where(CskhConversation.last_at.is_not(None))
        if allowed is not None:
            query = query.where(CskhConversation.page_id.in_(allowed))
        if page_id:
            query = query.where(CskhConversation.page_id == page_id)
        if label_id:
            query = query.where(
                CskhConversation.page_id.in_(
                    select(CskhPageLabel.page_id).where(CskhPageLabel.label_id == label_id)
                )
            )
        if pancake_tag:
            query = query.where(_json_has(CskhConversation.pancake_tags, pancake_tag))
        if campaign_id:
            query = query.where(
                CskhConversation.ad_id.in_(
                    select(CskhAd.ad_id).where(CskhAd.campaign_id == campaign_id)
                )
            )
        if tab == "unread":
            query = query.where(CskhConversation.unread > 0)
        elif tab == "callback":
            query = query.where(CskhConversation.callback.is_(True))
        elif tab == "phone":
            query = query.where(CskhConversation.phone_digits != "")
        if stage:
            query = query.where(CskhConversation.stage == stage)
        if mine:
            query = query.where(await self._mine(access))
        hits: dict[uuid.UUID, dict[str, str]] = {}
        if q and q.strip():
            hits = await self._search(q, allowed)
            query = query.where(CskhConversation.id.in_(list(hits)))
        if sort == "wait":
            waiting = and_(
                CskhConversation.last_out.is_(False),
                CskhConversation.last_at >= utcnow() - WAIT_WINDOW,
            )
            query = query.order_by(
                case((waiting, 0), else_=1),
                case((waiting, CskhConversation.last_at), else_=None).asc(),
                CskhConversation.last_at.desc(),
                CskhConversation.id,
            ).offset(max(0, offset))
        else:
            if before is not None:
                query = query.where(CskhConversation.last_at < before)
            query = query.order_by(CskhConversation.last_at.desc())
        rows = list((await self._session.scalars(query.limit(limit + 1))).all())
        more = len(rows) > limit
        rows = rows[:limit]
        views = await self.rows(rows)
        for view in views:
            hit = hits.get(uuid.UUID(view["id"]))
            if hit:
                view["match"] = hit
        if sort == "wait":
            return {
                "conversations": views,
                "next_before": None,
                "next_offset": offset + len(rows) if more else None,
            }
        return {
            "conversations": views,
            "next_before": iso(rows[-1].last_at) if more and rows else None,
            "next_offset": None,
        }

    async def _mine(self, access: CskhAccess) -> Any:
        """ "Của tôi": conversations one answered from APM, or that Pancake assigns to a
        staff member of one's own name (Pancake's staff are not APM accounts)."""
        if access.user_id is None:
            return CskhConversation.id.is_(None)
        replied = CskhConversation.id.in_(
            select(CskhMessage.conversation_id).where(CskhMessage.sender_user_id == access.user_id)
        )
        me = await self._session.get(User, access.user_id)
        name = fold(me.full_name or "").strip() if me is not None else ""
        pancake_ids = (
            {
                row.user_id
                for row in (await self._session.scalars(select(CskhPancakeUser))).all()
                if fold(row.name or "").strip() == name
            }
            if name
            else set()
        )
        return or_(replied, *(_json_has(CskhConversation.assignee_ids, pid) for pid in pancake_ids))

    async def rows(self, rows: Sequence[CskhConversation]) -> list[dict[str, Any]]:
        looks = {
            page_id: (name, short, color)
            for page_id, name, short, color in (
                await self._session.execute(
                    select(CskhPage.id, CskhPage.name, CskhPage.short_name, CskhPage.color).where(
                        CskhPage.id.in_({r.page_id for r in rows})
                    )
                )
            ).all()
        }
        codes: dict[uuid.UUID, list[str]] = {}
        for conv_id, code in (
            await self._session.execute(
                select(CskhOrder.conversation_id, CskhOrder.customer_code)
                .where(
                    CskhOrder.conversation_id.in_([r.id for r in rows]),
                    CskhOrder.customer_code != "",
                )
                .order_by(CskhOrder.created_at.desc())
            )
        ).all():
            codes.setdefault(conv_id, []).append(normalize_code(code))
        for conv_id, found in (await noted_codes(self._session, [r.id for r in rows])).items():
            codes.setdefault(conv_id, []).extend(found)
        ad_names: dict[Any, Any] = pairs(
            (
                await self._session.execute(
                    select(CskhAd.ad_id, CskhAd.name).where(
                        CskhAd.ad_id.in_({r.ad_id for r in rows if r.ad_id})
                    )
                )
            ).all()
        )
        linked = set(
            (
                await self._session.scalars(
                    select(CskhZaloThread.conversation_id).where(
                        CskhZaloThread.conversation_id.in_([r.id for r in rows])
                    )
                )
            ).all()
        )
        return [
            {
                "id": str(r.id),
                "page_id": r.page_id,
                "page_name": looks.get(r.page_id, (r.page_id, "", ""))[0],
                "page_short": looks.get(r.page_id, (r.page_id, "", ""))[1],
                "page_color": looks.get(r.page_id, (r.page_id, "", ""))[2],
                "stage": r.stage,
                "codes": list(dict.fromkeys(codes.get(r.id, []))),
                "last_in_at": iso(r.last_in_at),
                "psid": r.psid,
                "customer_name": r.customer_name or f"Khách …{r.psid[-4:]}",
                "last_text": r.last_text,
                "last_at": iso(r.last_at),
                "last_out": r.last_out,
                "unread": r.unread,
                "callback": r.callback,
                "phones": list(r.phones or []),
                "has_qr": bool(r.qrs),
                "ad_id": r.ad_id,
                "ad_name": ad_names.get(r.ad_id) if r.ad_id else None,
                "pancake_tags": list(r.pancake_tags or []),
                "assignee_ids": list(r.assignee_ids or []),
                "zalo_linked": r.id in linked,
            }
            for r in rows
        ]

    async def _search(self, term: str, allowed: set[str] | None) -> dict[uuid.UUID, dict[str, str]]:
        """Customer code, phone, name (conversation / order), every message's words."""
        key = fold(term).strip()
        digits = re.sub(r"\D", "", term)
        hits: dict[uuid.UUID, dict[str, str]] = {}
        if len(key) < 2:
            return hits

        def add(conv_id: uuid.UUID, why: str, snippet: str) -> None:
            if conv_id not in hits and len(hits) < SEARCH_LIMIT:
                hits[conv_id] = {"why": why, "snippet": clip(snippet, 160)}

        scoped = CskhConversation.page_id.in_(allowed) if allowed is not None else None
        like = f"%{key}%"
        code = normalize_code(term)
        for conv_id, value in (
            await self._session.execute(
                select(CskhOrder.conversation_id, CskhOrder.customer_code)
                .where(CskhOrder.customer_code.like(f"%{code}%"), CskhOrder.customer_code != "")
                .order_by(CskhOrder.created_at.desc())
            )
        ).all():
            add(conv_id, "Mã KH", value)
        noted = "".join(ch for ch in code if ch.isalnum())
        if len(noted) >= 3:
            for conv_id, value in (
                await self._session.execute(
                    select(CskhConversationCode.conversation_id, CskhConversationCode.code)
                    .where(CskhConversationCode.code.like(f"%{noted}%"))
                    .order_by(CskhConversationCode.at.desc())
                )
            ).all():
                add(conv_id, "Mã KH (ghi chú)", value)
        if len(digits) >= 4:
            part = digits[-9:]
            query = select(CskhConversation.id, CskhConversation.phones).where(
                CskhConversation.phone_digits.like(f"%{part}%")
            )
            for conv_id, phones in (await self._session.execute(query)).all():
                hit = next((p for p in phones or [] if part in re.sub(r"\D", "", str(p))), "")
                add(conv_id, "SĐT", str(hit))
            for conv_id, phone in (
                await self._session.execute(
                    select(CskhOrder.conversation_id, CskhOrder.phone).where(
                        CskhOrder.phone_key.like(f"%{part}%")
                    )
                )
            ).all():
                add(conv_id, "SĐT", phone)
        for conv_id, name in (
            await self._session.execute(
                select(CskhConversation.id, CskhConversation.customer_name).where(
                    CskhConversation.customer_key.like(like)
                )
            )
        ).all():
            add(conv_id, "Tên khách", name)
        for conv_id, name in (
            await self._session.execute(
                select(CskhOrder.conversation_id, CskhOrder.customer_name).where(
                    func.lower(CskhOrder.customer_name).like(f"%{term.strip().lower()}%")
                )
            )
        ).all():
            add(conv_id, "Tên khách", name)
        for conv_id, name in (
            await self._session.execute(
                select(CskhConversation.id, CskhAd.name)
                .join(CskhAd, CskhAd.ad_id == CskhConversation.ad_id)
                .where(func.lower(CskhAd.name).like(f"%{term.strip().lower()}%"))
            )
        ).all():
            add(conv_id, "Quảng cáo", name)
        for conv_id, text in (
            await self._session.execute(
                select(CskhMessage.conversation_id, CskhMessage.text)
                .where(CskhMessage.text_key.like(like))
                .order_by(CskhMessage.at.desc())
                .limit(2000)
            )
        ).all():
            add(conv_id, "Tin nhắn", text)
        if scoped is not None and hits:
            visible = set(
                (
                    await self._session.scalars(
                        select(CskhConversation.id).where(
                            CskhConversation.id.in_(list(hits)), scoped
                        )
                    )
                ).all()
            )
            hits = {k: v for k, v in hits.items() if k in visible}
        return hits

    # --- one conversation -------------------------------------------------------------

    async def detail(
        self, access: CskhAccess, conversation_id: uuid.UUID, *, read: bool = True
    ) -> dict[str, Any]:
        conv = await self.conversation(access, conversation_id)
        if read and conv.unread:
            conv.unread = 0
            await self._session.flush()
        page = await self._session.get(CskhPage, conv.page_id)
        assert page is not None
        messages = list(
            (
                await self._session.scalars(
                    select(CskhMessage)
                    .where(CskhMessage.conversation_id == conv.id)
                    .order_by(CskhMessage.at.desc(), CskhMessage.created_at.desc())
                    .limit(MAX_MESSAGES)
                )
            ).all()
        )[::-1]
        ads = await self._ads(conv)
        members = (await page_members(self._session, [page.id])).get(page.id, [])
        marketer = (
            (await names_of(self._session, [page.marketer_user_id])).get(page.marketer_user_id)
            if page.marketer_user_id
            else None
        )
        shop = await self._session.get(CskhPosShop, page.pos_shop_id) if page.pos_shop_id else None
        zalo = await self._session.scalar(
            select(func.count())
            .select_from(CskhZaloThread)
            .where(CskhZaloThread.conversation_id == conv.id)
        )
        view = (await self.rows([conv]))[0]
        return {
            "conversation": {
                **view,
                "qrs": list(conv.qrs or []),
                "ads": ads,
                "notes": list(conv.notes or []),
                "codes": await codes_of(self._session, conv.id),
            },
            "page": {
                "id": page.id,
                "name": page.name,
                "connected": bool(page.token_encrypted) and page.active,
                "members": members,
                "marketer_user_id": str(page.marketer_user_id) if page.marketer_user_id else None,
                "marketer_name": marketer,
                "pos_shop_id": page.pos_shop_id,
                "pos_shop_name": shop.name if shop else None,
                "short_name": page.short_name,
                "color": page.color,
            },
            "messages": [message_view(m) for m in messages],
            "orders": await self.orders(conv),
            "zalo_threads": int(zalo or 0),
        }

    async def _ads(self, conv: CskhConversation) -> list[dict[str, Any]]:
        ids = [str(a.get("ad_id")) for a in conv.ads or [] if a.get("ad_id")]
        info = {
            a.ad_id: a
            for a in (
                await self._session.scalars(select(CskhAd).where(CskhAd.ad_id.in_(ids)))
            ).all()
        }
        out = []
        for item in conv.ads or []:
            ad = info.get(str(item.get("ad_id")))
            out.append(
                {
                    "ad_id": str(item.get("ad_id")),
                    "post_id": item.get("post_id") or "",
                    "at": item.get("at"),
                    "name": ad.name if ad else "",
                    "image_url": ad.image_url if ad else "",
                    "campaign_id": ad.campaign_id if ad else "",
                    "campaign_name": ad.campaign_name if ad else "",
                }
            )
        return out

    async def orders(self, conv: CskhConversation) -> list[dict[str, Any]]:
        rows = (
            await self._session.scalars(
                select(CskhOrder)
                .where(CskhOrder.conversation_id == conv.id)
                .order_by(CskhOrder.created_at.desc())
            )
        ).all()
        return [order_view(o) for o in rows]

    # --- actions ----------------------------------------------------------------------

    async def send(
        self,
        access: CskhAccess,
        conversation_id: uuid.UUID,
        text: str,
        actor: Actor,
        *,
        clear_callback: bool = False,
    ) -> dict[str, Any]:
        conv = await self.conversation(access, conversation_id)
        clean = (text or "").strip()
        if not clean or len(clean) > 2000:
            raise CskhInvalidError(
                "cskh_message_invalid", "Nhập nội dung tin (tối đa 2000 ký tự).", field="text"
            )
        page = await self._session.get(CskhPage, conv.page_id)
        assert page is not None
        message = await send_text(
            self._session,
            self._clients,
            conv,
            page,
            clean,
            sender_user_id=actor.user_id,
            sender_name=actor.name,
        )
        if clear_callback and conv.callback:
            set_stage(conv, ConvStage.CONSULTING)
        conv.unread = 0
        await self._session.flush()
        await self._record(actor, AuditAction.CSKH_MESSAGE_SENT, conv, {"page_id": conv.page_id})
        return message_view(message)

    async def set_callback(self, access: CskhAccess, conversation_id: uuid.UUID, on: bool) -> None:
        conv = await self.conversation(access, conversation_id)
        if on:
            set_stage(conv, ConvStage.CALLBACK)
        elif conv.callback:
            set_stage(conv, ConvStage.CONSULTING)
        await self._session.flush()

    async def set_stage(
        self, access: CskhAccess, conversation_id: uuid.UUID, stage: str, actor: Actor
    ) -> str:
        """ "Trạng thái": Mới / Đang tư vấn / Chờ gọi lại / Đã chốt."""
        conv = await self.conversation(access, conversation_id)
        try:
            value = ConvStage(stage)
        except ValueError as error:
            raise CskhInvalidError(
                "cskh_stage_invalid", "Trạng thái không hợp lệ.", field="stage"
            ) from error
        before = conv.stage
        set_stage(conv, value)
        await self._session.flush()
        if before != value.value:
            await self._record(
                actor, AuditAction.CSKH_STAGE_CHANGED, conv, {"from": before, "to": value.value}
            )
        return value.value

    async def mark_unread(self, access: CskhAccess, conversation_id: uuid.UUID) -> None:
        conv = await self.conversation(access, conversation_id)
        conv.unread = max(conv.unread or 0, 1)
        await self._session.flush()

    async def _token(self, conv: CskhConversation) -> tuple[CskhPage, str]:
        page = await self._session.get(CskhPage, conv.page_id)
        token = page_token(self._clients, page) if page is not None else None
        if page is None or not token:
            raise CskhInvalidError("cskh_page_not_connected", "Page này chưa nối Pancake.")
        return page, token

    async def pancake_tag(
        self, access: CskhAccess, conversation_id: uuid.UUID, tag_id: str, add: bool
    ) -> list[str]:
        conv = await self.conversation(access, conversation_id)
        page, token = await self._token(conv)
        conv.edited_at = utcnow()
        try:
            now = await self._clients.pancake.set_tag(
                page.id, f"{page.id}_{conv.psid}", token, str(tag_id), "add" if add else "remove"
            )
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        conv.pancake_tags = now
        await self._session.flush()
        return now

    async def assign(
        self, access: CskhAccess, conversation_id: uuid.UUID, user_id: str | None
    ) -> list[str]:
        conv = await self.conversation(access, conversation_id)
        page, token = await self._token(conv)
        conv.edited_at = utcnow()
        try:
            now = await self._clients.pancake.assign(
                page.id, f"{page.id}_{conv.psid}", token, [user_id] if user_id else []
            )
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        conv.assignee_ids = now
        await self._session.flush()
        return now

    async def rescan_qr(
        self, access: CskhAccess, conversation_id: uuid.UUID, sync: CskhSync
    ) -> dict[str, Any]:
        conv = await self.conversation(access, conversation_id)
        ids = list(
            (
                await self._session.scalars(
                    select(CskhMessage.id).where(
                        CskhMessage.conversation_id == conv.id, CskhMessage.direction == "in"
                    )
                )
            ).all()
        )
        found = await sync.scan_qr(ids, force=True)
        await self._session.refresh(conv)
        return {"scanned": len(ids), "found": found, "qrs": list(conv.qrs or [])}

    # --- the duplicate-number check -------------------------------------------------------

    async def phone_check(
        self,
        access: CskhAccess,
        conversation_id: uuid.UUID,
        extra_phone: str = "",
        *,
        with_pos: bool = True,
    ) -> dict[str, Any]:
        conv = await self.conversation(access, conversation_id)
        return await self._check(conv, extra_phone, with_pos=with_pos)

    async def _check(
        self, conv: CskhConversation, extra_phone: str, *, with_pos: bool
    ) -> dict[str, Any]:
        keys: dict[str, str] = {}
        for phone in [*(conv.phones or []), *([extra_phone] if extra_phone else [])]:
            key = phone_key(str(phone))
            if key and key not in keys:
                keys[key] = str(phone)
        page_names: dict[Any, Any] = pairs(
            (await self._session.execute(select(CskhPage.id, CskhPage.name))).all()
        )
        seen: list[dict[str, Any]] = []
        self_seen: list[dict[str, Any]] = []
        conv_ids = {conv.id}
        for key, phone in keys.items():
            first, times = await self._phone_seen_in(conv.id, key)
            self_seen.append({"phone": phone, "first_at": iso(first), "times": times})
            others = (
                await self._session.scalars(
                    select(CskhConversation).where(
                        CskhConversation.id != conv.id,
                        CskhConversation.phone_digits.like(f"%{key}%"),
                    )
                )
            ).all()
            for other in others:
                hit = next((p for p in other.phones or [] if phone_key(str(p)) == key), None)
                if not hit:
                    continue
                first_other, count = await self._phone_seen_in(other.id, key)
                conv_ids.add(other.id)
                seen.append(
                    {
                        "conversation_id": str(other.id),
                        "page_id": other.page_id,
                        "page_name": page_names.get(other.page_id, other.page_id),
                        "customer_name": other.customer_name,
                        "phone": str(hit),
                        "first_at": iso(first_other or other.last_in_at or other.last_at),
                        "times": count,
                        "ad_id": other.ad_id or "",
                    }
                )
        for text in dict.fromkeys(str(item.get("text") or "") for item in conv.qrs or []):
            if not text:
                continue
            others = (
                await self._session.scalars(
                    select(CskhConversation).where(
                        CskhConversation.id != conv.id, CskhConversation.qr_texts.contains(text)
                    )
                )
            ).all()
            for other in others:
                same = [x for x in other.qrs or [] if x.get("text") == text]
                if not same or any(s["conversation_id"] == str(other.id) for s in seen):
                    continue
                conv_ids.add(other.id)
                seen.append(
                    {
                        "conversation_id": str(other.id),
                        "page_id": other.page_id,
                        "page_name": page_names.get(other.page_id, other.page_id),
                        "customer_name": other.customer_name,
                        "phone": f"Mã QR {same[0].get('kind')}",
                        "qr": text,
                        "first_at": same[0].get("at") or iso(other.last_in_at),
                        "times": len(same),
                        "ad_id": other.ad_id or "",
                    }
                )
        orders: list[dict[str, Any]] = []
        pos_ids: set[str] = set()
        conditions = [CskhOrder.conversation_id.in_(list(conv_ids))]
        if keys:
            conditions.append(CskhOrder.phone_key.in_(list(keys)))
        for order in (
            await self._session.scalars(
                select(CskhOrder)
                .where(or_(*conditions))
                .order_by(CskhOrder.created_at.desc())
                .limit(500)
            )
        ).all():
            if order.pos_order_id:
                pos_ids.add(f"{order.pos_shop_id}:{order.pos_order_id}")
            orders.append(
                {
                    "source": "Hộp chat",
                    "page_id": order.page_id,
                    "page_name": page_names.get(order.page_id, order.page_id),
                    "phone": order.phone,
                    "customer_name": order.customer_name,
                    "content_name": order.content_name,
                    "at": iso(order.created_at),
                    "order_no": order.pos_order_no or order.pos_order_id or "",
                    "marketer": order.marketer_name,
                    "status_name": "Đã xác nhận",
                    "same_conversation": order.conversation_id == conv.id,
                }
            )
        pos_error = ""
        if with_pos and keys:
            try:
                for key, phone in keys.items():
                    for o in await self._pos_by_phone(key, phone):
                        tag = f"{o['shop_id']}:{o['id']}"
                        if tag in pos_ids:
                            continue
                        mine = o.get("conversation_id") == f"{conv.page_id}_{conv.psid}"
                        if mine and o.get("status") in PENDING_POS:
                            continue  # this customer's own order about to be confirmed
                        pos_ids.add(tag)
                        orders.append(
                            {
                                "source": "Pancake POS",
                                "page_id": o.get("page_id") or "",
                                "page_name": page_names.get(o.get("page_id") or "")
                                or o.get("page_name")
                                or "—",
                                "phone": o.get("bill_phone_number") or "",
                                "customer_name": o.get("bill_full_name") or "",
                                "content_name": ", ".join(
                                    i.get("name") or "" for i in o.get("items") or []
                                ),
                                "at": o.get("at"),
                                "order_no": str(o.get("system_id") or o.get("id") or ""),
                                "marketer": "",
                                "status_name": o.get("status_name"),
                                "same_conversation": mine,
                            }
                        )
            except (CskhUpstreamError, CskhUpstreamRefusedError) as error:
                pos_error = str(getattr(error, "message", error))
        orders.sort(key=lambda o: o.get("at") or "", reverse=True)
        own = [o for o in orders if o["same_conversation"]]
        is_new = not seen and len(own) == len(orders)
        own_first = [s["first_at"] for s in self_seen if s["first_at"]]
        self_record = (
            [
                {
                    "first_at": min(own_first),
                    "page_id": conv.page_id,
                    "page_name": page_names.get(conv.page_id, conv.page_id),
                }
            ]
            if own_first
            else []
        )
        old = await self._old_marketer([*self_record, *seen], orders)
        page = await self._session.get(CskhPage, conv.page_id)
        page_marketer = (
            (await names_of(self._session, [page.marketer_user_id])).get(page.marketer_user_id)
            if page and page.marketer_user_id
            else None
        )
        return {
            "phones": list(keys.values()),
            "is_new": is_new,
            "need_ack": (not is_new) or bool(own),
            "own_confirmed": own,
            "seen": seen,
            "orders": [o for o in orders if not o["same_conversation"]],
            "self": self_seen,
            "repeat_in_conversation": sum(s["times"] for s in self_seen) > 1,
            "pos_error": pos_error,
            "qrs": list(conv.qrs or []),
            "page_marketer": (
                {"user_id": str(page.marketer_user_id), "name": page_marketer}
                if page and page.marketer_user_id
                else None
            ),
            "old_marketer": old,
        }

    async def _phone_seen_in(self, conv_id: uuid.UUID, key: str) -> tuple[datetime | None, int]:
        first: datetime | None = None
        count = 0
        rows = (
            await self._session.execute(
                select(CskhMessage.text, CskhMessage.at)
                .where(
                    CskhMessage.conversation_id == conv_id,
                    CskhMessage.direction == "in",
                    CskhMessage.text.like(f"%{key[-3:]}%"),
                )
                .order_by(CskhMessage.at)
            )
        ).all()
        for text, at in rows:
            if any(phone_key(p) == key for p in find_phones(text)):
                count += 1
                first = first or at
        return first, count

    async def _old_marketer(
        self, seen: Sequence[dict[str, Any]], orders: Sequence[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """The oldest data's marketer (order's own, else that page's) - this
        conversation's own first sight of the number included; locked for N days."""
        records = [
            {
                "at": s.get("first_at") or "",
                "page_id": s["page_id"],
                "page_name": s["page_name"],
                "marketer": "",
            }
            for s in seen
        ] + [
            {
                "at": o.get("at") or "",
                "page_id": o.get("page_id"),
                "page_name": o.get("page_name"),
                "marketer": o.get("marketer") or "",
            }
            for o in orders
        ]
        settings = await load_settings(self._session)
        for record in sorted(records, key=lambda r: r["at"] or "9999"):
            name = record["marketer"]
            user_id: uuid.UUID | None = None
            if not name and record["page_id"]:
                page = await self._session.get(CskhPage, record["page_id"])
                if page and page.marketer_user_id:
                    user_id = page.marketer_user_id
                    name = (await names_of(self._session, [user_id])).get(user_id, "")
            if name:
                at = _parse(record["at"])
                days = settings.mkt_lock_days
                locked = bool(days and at and utcnow() - at < timedelta(days=days))
                return {
                    "name": name,
                    "user_id": str(user_id) if user_id else None,
                    "page_name": record["page_name"],
                    "at": record["at"] or None,
                    "locked": locked,
                    "lock_days": days,
                }
        return None

    # --- POS Cake -------------------------------------------------------------------------

    async def _shop_key(self, shop_id: str) -> str:
        shop = await self._session.get(CskhPosShop, shop_id)
        key = await self._session.get(CskhPosKey, shop.key_id) if shop else None
        if key is None:
            raise CskhInvalidError(
                "cskh_pos_missing", "Chưa có API key POS cho cửa hàng của page này."
            )
        return pos_key(self._clients, key)

    async def _page_shop(self, page: CskhPage) -> str:
        if not page.pos_shop_id:
            has_keys = await self._session.scalar(select(func.count()).select_from(CskhPosKey))
            if not has_keys:
                raise CskhInvalidError(
                    "cskh_pos_not_configured", "Chưa cấu hình Pancake POS (API key)."
                )
            await load_pos_shops(self._session, self._clients)
        if not page.pos_shop_id:
            raise CskhInvalidError(
                "cskh_pos_no_shop",
                "Page này chưa gắn cửa hàng nào trên Pancake POS "
                "(hoặc API key không có quyền với cửa hàng của page).",
            )
        return page.pos_shop_id

    async def _pos_by_phone(self, key: str, phone: str) -> list[dict[str, Any]]:
        """Orders holding this number in every shop (8 at once, kept 60 s)."""
        loop = asyncio.get_running_loop()
        cached = _POS_CACHE.get(key)
        if cached and loop.time() - cached[0] < POS_CACHE_SECONDS:
            return cached[1]
        settings = await load_settings(self._session)
        shops = [
            s
            for s in (await self._session.scalars(select(CskhPosShop))).all()
            if not settings.dup_skipped(s.name)
        ]
        keys = {k.id: k for k in (await self._session.scalars(select(CskhPosKey))).all()}
        gate = asyncio.Semaphore(8)

        async def one(shop: CskhPosShop) -> list[dict[str, Any]]:
            key_row = keys.get(shop.key_id)
            if key_row is None:
                return []
            async with gate:
                try:
                    found = await self._clients.pos.orders(
                        pos_key(self._clients, key_row), shop.id, phone
                    )
                except CskhUpstreamError:
                    return []
            names = {str(p.get("id")): p.get("name") or "" for p in shop.pages or []}
            return [
                {**pos_view(o, shop.id), "page_name": names.get(str(o.get("page_id"))) or shop.name}
                for o in found
                if phone_key(str(o.get("bill_phone_number") or "")) == key
            ]

        results = await asyncio.gather(*(one(s) for s in shops))
        out = [o for chunk in results for o in chunk]
        _POS_CACHE[key] = (loop.time(), out)
        return out

    async def pos_orders(self, conv: CskhConversation) -> list[dict[str, Any]]:
        """The customer's orders in the page's shop (by phone and by name)."""
        page = await self._session.get(CskhPage, conv.page_id)
        assert page is not None
        shop = await self._page_shop(page)
        api_key = await self._shop_key(shop)
        keys = {phone_key(str(p)) for p in conv.phones or []} - {""}
        terms = sorted(keys) + ([conv.customer_name] if conv.customer_name else [])
        found: dict[str, dict[str, Any]] = {}
        for term in terms:
            for order in await self._clients.pos.orders(api_key, shop, term):
                if (
                    order.get("conversation_id") == f"{page.id}_{conv.psid}"
                    or phone_key(str(order.get("bill_phone_number") or "")) in keys
                ):
                    found[str(order.get("id"))] = order
        views = [pos_view(o, shop) for o in found.values()]
        return sorted(views, key=lambda o: o.get("at") or "", reverse=True)[:10]

    async def confirm(
        self,
        access: CskhAccess,
        conversation_id: uuid.UUID,
        data: dict[str, Any],
        actor: Actor,
    ) -> dict[str, Any]:
        """ "✓ Xác nhận số điện thoại" / "Xác nhận QR": the conversation's pending POS
        order moved to "Đã xác nhận", else a new confirmed order. A repeated number /
        QR / an already confirmed customer needs ``dup_ack`` (409 ``cskh_duplicate``)."""
        conv = await self.conversation(access, conversation_id)
        page = await self._session.get(CskhPage, conv.page_id)
        assert page is not None
        phone = str(data.get("phone") or "").strip()[:30]
        check = await self._check(conv, phone, with_pos=True)
        if check["need_ack"] and not data.get("dup_ack"):
            raise CskhConflictError(
                "cskh_duplicate",
                "Khách đã có trên hệ thống (trùng số / mã QR / đã xác nhận) — "
                "xem cảnh báo trước khi xác nhận.",
                extra={"dup": check},
            )
        dup_note = dup_summary(check)
        confirm_type = ConfirmType.QR if data.get("confirm_type") == "qr" else ConfirmType.PHONE
        content = str(data.get("content_name") or "").strip()[:300]
        if confirm_type is ConfirmType.QR and not content:
            kinds = sorted({str(x.get("kind")) for x in check.get("qrs") or []})
            content = "Xác nhận QR" + (f" ({', '.join(kinds)})" if kinds else "")
        platform = str(data.get("platform") or DEFAULT_PLATFORM)
        if platform not in PLATFORMS:
            platform = DEFAULT_PLATFORM
        people = await self._people(data)
        marketer_id, marketer_name = people.get("marketer", (None, ""))
        old = check.get("old_marketer")
        if not marketer_name:
            if old:
                marketer_id = uuid.UUID(old["user_id"]) if old.get("user_id") else None
                marketer_name = old["name"]
            elif check.get("page_marketer"):
                marketer_id = uuid.UUID(check["page_marketer"]["user_id"])
                marketer_name = check["page_marketer"]["name"] or ""
        if old and marketer_name != old["name"]:
            if old["locked"] and not access.manage:
                raise CskhForbiddenError(
                    f"Số này thuộc marketing {old['name']} (dữ liệu cũ ở {old['page_name']}, "
                    f"chưa quá {old['lock_days']} ngày) — chỉ người quản trị hộp chat đổi được."
                )
            dup_note = (
                dup_note + "; " if dup_note else ""
            ) + f"Đổi marketing {old['name']} → {marketer_name}"
        creator_id, creator_name = people.get("creator", (actor.user_id, actor.name))
        telesale_id, telesale_name = people.get(
            "telesale", (None, str(data.get("telesale_name") or "").strip()[:200])
        )
        info = OrderInfo(
            customer_name=str(data.get("customer_name") or "").strip()[:300],
            address=str(data.get("address") or "").strip()[:500],
            content_name=content,
            order_status=str(data.get("order_status") or "").strip()[:120],
            platform=platform,
            marketer=marketer_name,
            creator=creator_name,
            telesale=telesale_name,
            customer_code=normalize_code(data.get("customer_code")),
            note=str(data.get("note") or "").strip()[:2000],
        )
        if phone:
            set_phones(conv, [phone])
        shop = await self._page_shop(page)
        api_key = await self._shop_key(shop)
        note = order_note_text(info)
        order_id = str(data.get("order_id") or "")
        try:
            if not order_id:
                pending = next(
                    (
                        o
                        for o in await self.pos_orders(conv)
                        if o["status"] in PENDING_POS
                        and o.get("conversation_id") == f"{page.id}_{conv.psid}"
                    ),
                    None,
                )
                order_id = pending["id"] if pending else ""
            if order_id:
                order = await self._pos_confirm(api_key, shop, page, order_id, note, info)
                action = "confirm"
            else:
                order = await self._pos_create(api_key, shop, page, conv, phone, note, info)
                action = "create"
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        _POS_CACHE.clear()
        row = CskhOrder(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            page_id=page.id,
            action=action,
            confirm_type=confirm_type.value,
            pos_shop_id=shop,
            pos_order_id=str(order.get("id") or "")[:40] or None,
            pos_order_no=str(order.get("system_id") or order.get("id") or "")[:40] or None,
            value=Decimal(str(int(order.get("total_price") or 0)))
            if order.get("total_price")
            else None,
            phone=phone or str(order.get("bill_phone_number") or "")[:30],
            phone_key=phone_key(phone or str(order.get("bill_phone_number") or "")),
            customer_name=info.customer_name or str(order.get("bill_full_name") or "")[:300],
            address=info.address,
            content_name=info.content_name,
            order_status=info.order_status,
            platform=info.platform,
            marketer_user_id=marketer_id,
            marketer_name=info.marketer[:200],
            creator_user_id=creator_id,
            creator_name=info.creator[:200],
            telesale_user_id=telesale_id,
            telesale_name=info.telesale[:200],
            customer_code=info.customer_code,
            note=info.note,
            dup_info=dup_note,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        set_stage(conv, ConvStage.CLOSED)
        await self._session.flush()
        await self._record(
            actor,
            AuditAction.CSKH_ORDER_CONFIRMED,
            conv,
            {
                "action": action,
                "confirm_type": confirm_type.value,
                "pos_order": row.pos_order_no,
                "duplicate": bool(dup_note),
            },
        )
        return {"order": order_view(row), "action": action, "pos": order}

    async def _people(self, data: dict[str, Any]) -> dict[str, tuple[uuid.UUID | None, str]]:
        out: dict[str, tuple[uuid.UUID | None, str]] = {}
        for key in ("marketer", "creator", "telesale"):
            raw = data.get(f"{key}_user_id")
            if not raw:
                continue
            try:
                user_id = uuid.UUID(str(raw))
            except ValueError as error:
                raise CskhInvalidError(
                    "cskh_person_invalid", "Người được chọn không hợp lệ.", field=key
                ) from error
            user = await self._session.get(User, user_id)
            if user is None or user.deleted_at is not None:
                raise CskhInvalidError(
                    "cskh_person_invalid", "Người được chọn không còn.", field=key
                )
            out[key] = (user.id, user.full_name)
        return out

    async def _pos_confirm(
        self, api_key: str, shop: str, page: CskhPage, order_id: str, note: str, info: OrderInfo
    ) -> dict[str, Any]:
        before = await self._clients.pos.order(api_key, shop, order_id)
        if str(before.get("page_id") or page.id) != page.id:
            raise CskhInvalidError("cskh_pos_wrong_page", "Đơn này không thuộc page của hội thoại.")
        if before.get("status") == CONFIRMED_POS:
            raise CskhInvalidError("cskh_pos_already", "Đơn này đã ở trạng thái Đã xác nhận.")
        body: dict[str, Any] = {"status": CONFIRMED_POS}
        if note:
            old = str(before.get("note") or "").strip()
            body["note"] = f"{old}\n{note}" if old else note
        if info.customer_name and info.customer_name != (before.get("bill_full_name") or ""):
            body["bill_full_name"] = info.customer_name
        address = (
            (before.get("shipping_address") or {}).get("address")
            if isinstance(before.get("shipping_address"), dict)
            else ""
        )
        if info.address and info.address != (address or ""):
            body["shipping_address"] = {
                "address": info.address,
                "full_name": info.customer_name or before.get("bill_full_name") or "",
                "phone_number": before.get("bill_phone_number") or "",
            }
        await self._clients.pos.update_order(api_key, shop, order_id, body)
        after = await self._clients.pos.order(api_key, shop, order_id)
        if after.get("status") != CONFIRMED_POS:
            label = POS_STATUS.get(int(after.get("status") or 0), str(after.get("status")))
            raise CskhInvalidError(
                "cskh_pos_not_confirmed",
                f"Pancake chưa chuyển đơn sang Đã xác nhận (trạng thái hiện tại: {label}).",
            )
        return pos_view(after, shop)

    async def _pos_create(
        self,
        api_key: str,
        shop: str,
        page: CskhPage,
        conv: CskhConversation,
        phone: str,
        note: str,
        info: OrderInfo,
    ) -> dict[str, Any]:
        name = info.customer_name or conv.customer_name
        body: dict[str, Any] = {
            "shop_id": int(shop) if shop.isdigit() else shop,
            "status": CONFIRMED_POS,
            "bill_full_name": name,
            "bill_phone_number": phone,
            "page_id": page.id,
            "conversation_id": f"{page.id}_{conv.psid}",
            "customer": {
                "fb_id": conv.psid,
                "name": conv.customer_name or name,
                "page_id": page.id,
            },
            "received_at_shop": False,
            "is_free_shipping": True,
            "note": note,
            "items": [],
        }
        if info.address:
            body["shipping_address"] = {
                "address": info.address,
                "full_name": name,
                "phone_number": phone,
            }
        if conv.ad_id:
            body["ad_id"] = conv.ad_id
        created = await self._clients.pos.create_order(api_key, shop, body)
        return pos_view(created, shop)

    async def _record(
        self, actor: Actor, action: AuditAction, conv: CskhConversation, data: dict[str, Any]
    ) -> None:
        await self._audit.record(
            AuditEntry(
                request_id=actor.request_id,
                action=action,
                result=AuditResult.SUCCESS,
                actor_user_id=actor.user_id,
                entity_type="cskh_conversation",
                entity_id=str(conv.id),
                after_data=data,
            )
        )


# --- views ------------------------------------------------------------------------------------


def message_view(m: CskhMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "mid": m.mid,
        "direction": m.direction,
        "kind": m.kind,
        "text": m.text,
        "at": iso(m.at),
        "status": m.status,
        "attachments": list(m.attachments or []),
        "qr": list(m.qr) if m.qr is not None else None,
        "sender_name": m.sender_name,
    }


def order_view(o: CskhOrder) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "action": o.action,
        "confirm_type": o.confirm_type,
        "pos_order_no": o.pos_order_no,
        "phone": o.phone,
        "customer_name": o.customer_name,
        "address": o.address,
        "content_name": o.content_name,
        "order_status": o.order_status,
        "platform": o.platform,
        "marketer_name": o.marketer_name,
        "creator_name": o.creator_name,
        "telesale_name": o.telesale_name,
        "customer_code": o.customer_code,
        "note": o.note,
        "dup_info": o.dup_info,
        "created_at": iso(o.created_at),
    }


def pos_view(o: dict[str, Any], shop_id: str) -> dict[str, Any]:
    status = o.get("status")
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    at = pancake_time(o.get("inserted_at"))
    return {
        "id": str(o.get("id") or ""),
        "system_id": o.get("system_id"),
        "status": code,
        "status_name": POS_STATUS.get(code, str(o.get("status_name") or status))
        if code is not None
        else "",
        "shop_id": shop_id,
        "total_price": o.get("total_price"),
        "bill_full_name": o.get("bill_full_name"),
        "bill_phone_number": o.get("bill_phone_number"),
        "conversation_id": o.get("conversation_id"),
        "page_id": str(o.get("page_id") or ""),
        "ad_id": o.get("ad_id"),
        "at": iso(at),
        "items": [
            {
                "name": (i.get("variation_info") or {}).get("name"),
                "quantity": i.get("quantity"),
            }
            for i in o.get("items") or []
            if isinstance(i, dict)
        ],
    }


def dup_summary(check: dict[str, Any]) -> str:
    if not check.get("need_ack"):
        return ""
    parts = (
        [f"khách đã có {len(check['own_confirmed'])} đơn xác nhận trước đó"]
        if check.get("own_confirmed")
        else []
    )
    if check["seen"]:
        parts.append("số đã có ở " + ", ".join(sorted({s["page_name"] for s in check["seen"]})))
    if check["orders"]:
        parts.append(f"{len(check['orders'])} đơn cũ")
    prefix = "Trùng số: " if check["seen"] or check["orders"] else "Xác nhận lại: "
    return prefix + "; ".join(parts)


async def page_members(
    session: AsyncSession, page_ids: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    rows = (
        await session.execute(
            select(
                CskhPageMember.page_id, CskhPageMember.user_id, CskhPageMember.role, User.full_name
            )
            .join(User, User.id == CskhPageMember.user_id)
            .where(CskhPageMember.page_id.in_(list(page_ids)), User.deleted_at.is_(None))
            .order_by(CskhPageMember.role, User.full_name)
        )
    ).all()
    out: dict[str, list[dict[str, Any]]] = {}
    for page_id, user_id, role, name in rows:
        try:
            label = MEMBER_ROLE_LABELS[MemberRole(role)]
        except ValueError:
            label = role
        out.setdefault(page_id, []).append(
            {"user_id": str(user_id), "name": name, "role": role, "role_label": label}
        )
    return out


def _json_has(column: Any, value: str) -> Any:
    """A JSON list of strings holding ``value`` (a text match on the JSON)."""
    clean = value.replace('"', "").replace("%", "").replace("_", "")
    return cast(column, Text).like(f'%"{clean}"%')


def _parse(value: object) -> datetime | None:
    try:
        return ensure_utc(datetime.fromisoformat(str(value))) if value else None
    except ValueError:
        return None


def inbox_sync(session: AsyncSession, clients: CskhClients) -> CskhSync:
    """A ``CskhSync`` working inside the request's session (QR re-scan, page refresh)."""
    return CskhSync(lambda: same_session(session), clients, pause=0)


__all__ = [
    "Actor",
    "CskhInbox",
    "dup_summary",
    "inbox_sync",
    "iso",
    "message_view",
    "order_view",
    "page_members",
    "pos_view",
]
