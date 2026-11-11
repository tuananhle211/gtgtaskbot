"""Zalo telesale (0102): nicks read through the bridge, matched to page conversations.

Ported from ``hop-chat-apexmed`` (``zalo_process``, ``zalo_save_msg``,
``zalo_find_conv``, ``zalo_search``, ``zalo_mask_page_msg``, the ``/api/zalo/*``
routes):

* the bridge pushes ``{type, zaloAccountId, source:"zca", data:{msgs|groupMsgs|status|info}}``;
* a new thread is matched to a page conversation by a customer code (Mã KH,
  ≥ 4 characters, whole word) in a message, else a phone number (orders first,
  then the conversations' numbers);
* a thread matched BY CODE shows the page side with every phone number's last
  4 digits hidden (``LinkKind.CODE``) - on the server;
* ``ZALO OWN`` = the nicks handed to the caller; reading / sending a page
  message from a Zalo thread needs the thread to be the caller's, not the page.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import CskhAccess, page_scope, zalo_scope
from meobot.application.cskh.common import CskhClients, names_of, pairs
from meobot.application.cskh.inbox import Actor, iso, message_view
from meobot.application.cskh.sync import aware, send_text
from meobot.core.time import utcnow
from meobot.db.models.cskh import (
    CskhConversation,
    CskhMessage,
    CskhOrder,
    CskhPage,
    CskhZaloAccount,
    CskhZaloMessage,
    CskhZaloThread,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.cskh.errors import (
    CskhForbiddenError,
    CskhInvalidError,
    CskhNotFoundError,
    CskhUpstreamRefusedError,
)
from meobot.domain.cskh.models import (
    MIN_CODE_LENGTH,
    ZALO_STATUS_LABELS,
    CskhUpstreamError,
    LinkKind,
    ZaloMessage,
    ZaloStatus,
    clip,
    code_in_text,
    find_phones,
    fold,
    looks_like_phone,
    mask_deep,
    mask_phone,
    mask_phones_in,
    normalize_code,
    parse_zalo_push,
    phone_key,
)
from meobot.integrations.zalo_bridge.client import ZaloBridge

THREADS_LIMIT = 500
MESSAGES_LIMIT = 500
PAGE_MESSAGES_LIMIT = 300
SEARCH_LIMIT = 30


# --- the bridge's push -----------------------------------------------------------------------


async def take_push(session: AsyncSession, payload: object) -> int:
    """Save what the bridge pushed; returns the number of new messages."""
    now = utcnow()
    saved = 0
    for item in parse_zalo_push(payload, now):
        account = await session.get(CskhZaloAccount, item.account_id)
        if account is None:
            account = CskhZaloAccount(id=item.account_id, status=ZaloStatus.CONNECTED.value)
            session.add(account)
            await session.flush()
        if item.status:
            account.status = item.status
            account.status_note = item.reason
            account.status_at = now
        if item.name:
            account.name = item.name
        if item.phone:
            account.phone = item.phone
        if item.avatar:
            account.avatar = item.avatar
        for message in item.messages:
            if await save_zalo_message(session, account, message, history=item.history):
                saved += 1
    await session.flush()
    return saved


async def save_zalo_message(
    session: AsyncSession, account: CskhZaloAccount, message: ZaloMessage, *, history: bool
) -> bool:
    thread: CskhZaloThread | None = await session.scalar(
        select(CskhZaloThread).where(
            CskhZaloThread.account_id == account.id, CskhZaloThread.peer_uid == message.peer_uid
        )
    )
    if thread is None:
        thread = CskhZaloThread(
            id=uuid.uuid4(),
            account_id=account.id,
            peer_uid=message.peer_uid,
            is_group=message.group,
            peer_name="",
            peer_avatar="",
            last_text="",
            unread=0,
            customer_code="",
            link_how="",
        )
        session.add(thread)
        await session.flush()
    if message.peer_name:
        thread.peer_name = message.peer_name
    if message.peer_avatar:
        thread.peer_avatar = message.peer_avatar
    account_last = aware(account.last_at)
    if account_last is None or message.at > account_last:
        account.last_at = message.at
    thread_last = aware(thread.last_at)
    if thread_last is None or message.at >= thread_last:
        thread.last_at = message.at
        thread.last_text = clip(message.text, 500)
        thread.last_out = message.outgoing
    known = await session.scalar(
        select(CskhZaloMessage.id).where(CskhZaloMessage.msg_id == message.msg_id)
    )
    if known is not None:
        return False
    session.add(
        CskhZaloMessage(
            id=uuid.uuid4(),
            thread_id=thread.id,
            msg_id=message.msg_id,
            direction="out" if message.outgoing else "in",
            sender_name=message.sender_name,
            text=message.text,
            text_digits=re.sub(r"\D", "", message.text),
            text_key=fold(message.text),
            kind=message.kind,
            at=message.at,
        )
    )
    if not history:
        thread.unread = 0 if message.outgoing else (thread.unread or 0) + 1
    if thread.conversation_id is None and message.text and not message.group:
        match = await find_conversation(session, message.text)
        if match is not None:
            conv_id, code, how = match
            link(
                thread,
                conv_id,
                code,
                how,
                LinkKind.CODE if code and how.startswith("Mã KH") else LinkKind.PHONE,
            )
    await session.flush()
    return True


async def _codes(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Every customer code ≥ 4 characters -> its page conversation (the latest)."""
    rows = (
        await session.execute(
            select(CskhOrder.customer_code, CskhOrder.conversation_id)
            .where(
                CskhOrder.customer_code != "",
                func.length(CskhOrder.customer_code) >= MIN_CODE_LENGTH,
            )
            .order_by(CskhOrder.created_at)
        )
    ).all()
    return {code.upper(): conv_id for code, conv_id in rows}


async def find_conversation(session: AsyncSession, text: str) -> tuple[uuid.UUID, str, str] | None:
    """A customer code first, then a phone number (orders, then conversations)."""
    for code, conv_id in (await _codes(session)).items():
        if code_in_text(code, text):
            return conv_id, code, f"Mã KH {code} trong tin Zalo"
    for phone in find_phones(text):
        key = phone_key(phone)
        if not key:
            continue
        order = (
            await session.execute(
                select(CskhOrder.conversation_id, CskhOrder.customer_code)
                .where(CskhOrder.phone_key == key)
                .order_by(CskhOrder.created_at.desc())
                .limit(1)
            )
        ).first()
        if order is not None:
            return order[0], order[1] or "", f"SĐT {phone} trong tin Zalo"
        for conv in (
            await session.scalars(
                select(CskhConversation).where(CskhConversation.phone_digits.like(f"%{key}%"))
            )
        ).all():
            if any(phone_key(str(p)) == key for p in conv.phones or []):
                return conv.id, "", f"SĐT {phone} trong tin Zalo"
    return None


def link(
    thread: CskhZaloThread,
    conversation_id: uuid.UUID,
    code: str,
    how: str,
    kind: LinkKind,
    *,
    user_id: uuid.UUID | None = None,
) -> None:
    thread.conversation_id = conversation_id
    thread.customer_code = code[:40]
    thread.link_how = clip(how, 300)
    thread.link_kind = kind.value
    thread.linked_at = utcnow()
    thread.linked_by_user_id = user_id


# --- the Zalo inbox --------------------------------------------------------------------------


class CskhZalo:
    def __init__(
        self, session: AsyncSession, clients: CskhClients, bridge: ZaloBridge, audit: AuditService
    ) -> None:
        self._session = session
        self._clients = clients
        self._bridge = bridge
        self._audit = audit

    async def _scope(self, access: CskhAccess) -> set[str] | None:
        access.require_zalo()
        return await zalo_scope(self._session, access)

    async def thread(self, access: CskhAccess, thread_id: uuid.UUID) -> CskhZaloThread:
        scope = await self._scope(access)
        thread = await self._session.get(CskhZaloThread, thread_id)
        if thread is None or (scope is not None and thread.account_id not in scope):
            raise CskhNotFoundError("cskh_zalo_thread_not_found", "Không tìm thấy hội thoại Zalo.")
        return thread

    async def threads(
        self,
        access: CskhAccess,
        *,
        account_id: str | None = None,
        user_id: uuid.UUID | None = None,
        tab: str = "all",
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        scope = await self._scope(access)
        query = select(CskhZaloThread)
        if scope is not None:
            query = query.where(CskhZaloThread.account_id.in_(scope))
        if account_id:
            query = query.where(CskhZaloThread.account_id == account_id)
        if user_id:
            query = query.where(
                CskhZaloThread.account_id.in_(
                    select(CskhZaloAccount.id).where(CskhZaloAccount.user_id == user_id)
                )
            )
        if tab == "unread":
            query = query.where(CskhZaloThread.unread > 0)
        elif tab == "unlinked":
            query = query.where(
                CskhZaloThread.conversation_id.is_(None), CskhZaloThread.is_group.is_(False)
            )
        if q and q.strip():
            key = fold(q).strip()
            digits = re.sub(r"\D", "", q)
            matching = select(CskhZaloMessage.thread_id).where(
                or_(
                    CskhZaloMessage.text_key.like(f"%{key}%"),
                    *(
                        [CskhZaloMessage.text_digits.like(f"%{digits[-9:]}%")]
                        if len(digits) >= 4
                        else []
                    ),
                )
            )
            query = query.where(
                or_(
                    func.lower(CskhZaloThread.peer_name).like(f"%{q.strip().lower()}%"),
                    CskhZaloThread.customer_code.like(f"%{normalize_code(q)}%"),
                    CskhZaloThread.id.in_(matching),
                )
            )
        rows = list(
            (
                await self._session.scalars(
                    query.order_by(CskhZaloThread.last_at.desc().nulls_last()).limit(THREADS_LIMIT)
                )
            ).all()
        )
        return await self.views(rows)

    async def views(self, rows: Sequence[CskhZaloThread]) -> list[dict[str, Any]]:
        accounts = {
            a.id: a
            for a in (
                await self._session.scalars(
                    select(CskhZaloAccount).where(
                        CskhZaloAccount.id.in_({r.account_id for r in rows})
                    )
                )
            ).all()
        }
        sales = await names_of(self._session, (a.user_id for a in accounts.values()))
        convs = {
            c.id: c
            for c in (
                await self._session.scalars(
                    select(CskhConversation).where(
                        CskhConversation.id.in_(
                            {r.conversation_id for r in rows if r.conversation_id}
                        )
                    )
                )
            ).all()
        }
        pages: dict[Any, Any] = pairs(
            (
                await self._session.execute(
                    select(CskhPage.id, CskhPage.name).where(
                        CskhPage.id.in_({c.page_id for c in convs.values()})
                    )
                )
            ).all()
        )
        out = []
        for row in rows:
            account = accounts.get(row.account_id)
            conv = convs.get(row.conversation_id) if row.conversation_id else None
            masked = row.link_kind == LinkKind.CODE.value
            phones = [str(p) for p in (conv.phones if conv else []) or []]
            out.append(
                {
                    "id": str(row.id),
                    "account_id": row.account_id,
                    "account_name": account.name if account else "",
                    "account_status": account.status if account else "",
                    "sale_user_id": str(account.user_id) if account and account.user_id else None,
                    "sale_name": sales.get(account.user_id)
                    if account and account.user_id
                    else None,
                    "peer_uid": row.peer_uid,
                    "peer_name": row.peer_name
                    or ("Nhóm" if row.is_group else f"Zalo …{row.peer_uid[-4:]}"),
                    "peer_avatar": row.peer_avatar,
                    "is_group": row.is_group,
                    "last_text": row.last_text,
                    "last_out": row.last_out,
                    "last_at": iso(row.last_at),
                    "unread": row.unread,
                    "conversation_id": str(row.conversation_id) if row.conversation_id else None,
                    "customer_name": conv.customer_name if conv else None,
                    "page_id": conv.page_id if conv else None,
                    "page_name": pages.get(conv.page_id) if conv else None,
                    "conversation_phones": [mask_phone(p) for p in phones] if masked else phones,
                    "customer_code": row.customer_code,
                    "link_how": row.link_how,
                    "link_kind": row.link_kind,
                    "masked": masked,
                }
            )
        return out

    async def messages(
        self, access: CskhAccess, thread_id: uuid.UUID, *, read: bool = True
    ) -> dict[str, Any]:
        thread = await self.thread(access, thread_id)
        if read and thread.unread:
            thread.unread = 0
            await self._session.flush()
        rows = list(
            (
                await self._session.scalars(
                    select(CskhZaloMessage)
                    .where(CskhZaloMessage.thread_id == thread.id)
                    .order_by(CskhZaloMessage.at.desc())
                    .limit(MESSAGES_LIMIT)
                )
            ).all()
        )[::-1]
        return {"thread": (await self.views([thread]))[0], "messages": [zalo_view(m) for m in rows]}

    async def page_messages(self, access: CskhAccess, thread_id: uuid.UUID) -> dict[str, Any]:
        """The page conversation of a matched thread (phones masked when matched by code)."""
        thread = await self.thread(access, thread_id)
        if thread.conversation_id is None:
            return {"conversation": None, "messages": [], "masked": False}
        conv = await self._session.get(CskhConversation, thread.conversation_id)
        if conv is None:
            return {"conversation": None, "messages": [], "masked": False}
        page = await self._session.get(CskhPage, conv.page_id)
        rows = list(
            (
                await self._session.scalars(
                    select(CskhMessage)
                    .where(CskhMessage.conversation_id == conv.id)
                    .order_by(CskhMessage.at.desc())
                    .limit(PAGE_MESSAGES_LIMIT)
                )
            ).all()
        )[::-1]
        masked = thread.link_kind == LinkKind.CODE.value
        views = [message_view(m) for m in rows]
        phones = [str(p) for p in conv.phones or []]
        if masked:
            views = [mask_message(v) for v in views]
            phones = [mask_phone(p) for p in phones]
        return {
            "conversation": {
                "id": str(conv.id),
                "page_id": conv.page_id,
                "page_name": page.name if page else conv.page_id,
                "customer_name": conv.customer_name,
                "phones": phones,
                "connected": bool(page and page.token_encrypted and page.active),
            },
            "messages": views,
            "masked": masked,
        }

    async def page_send(
        self, access: CskhAccess, thread_id: uuid.UUID, text: str, actor: Actor
    ) -> dict[str, Any]:
        """A telesale answers the customer on the PAGE from their Zalo thread."""
        thread = await self.thread(access, thread_id)
        if thread.conversation_id is None:
            raise CskhInvalidError(
                "cskh_zalo_unlinked", "Hội thoại Zalo này chưa ghép với khách trên page."
            )
        clean = (text or "").strip()
        if not clean or len(clean) > 2000:
            raise CskhInvalidError(
                "cskh_message_invalid", "Nhập nội dung tin (tối đa 2000 ký tự).", field="text"
            )
        conv = await self._session.get(CskhConversation, thread.conversation_id)
        page = await self._session.get(CskhPage, conv.page_id) if conv else None
        if conv is None or page is None:
            raise CskhNotFoundError("cskh_conversation_not_found", "Không tìm thấy hội thoại page.")
        message = await send_text(
            self._session,
            self._clients,
            conv,
            page,
            clean,
            sender_user_id=actor.user_id,
            sender_name=f"{actor.name} (từ hộp chat Zalo)",
        )
        await self._audit.record(
            AuditEntry(
                request_id=actor.request_id,
                action=AuditAction.CSKH_MESSAGE_SENT,
                result=AuditResult.SUCCESS,
                actor_user_id=actor.user_id,
                entity_type="cskh_conversation",
                entity_id=str(conv.id),
                after_data={"page_id": conv.page_id, "from_zalo_thread": str(thread.id)},
            )
        )
        view = message_view(message)
        return mask_message(view) if thread.link_kind == LinkKind.CODE.value else view

    # --- matching --------------------------------------------------------------------------

    async def resolve(self, term: str) -> tuple[uuid.UUID, str, LinkKind] | None:
        clean = (term or "").strip()
        if not clean:
            return None
        code = normalize_code(clean)
        row = (
            await self._session.execute(
                select(CskhOrder.conversation_id, CskhOrder.customer_code)
                .where(CskhOrder.customer_code == code, CskhOrder.customer_code != "")
                .order_by(CskhOrder.created_at.desc())
                .limit(1)
            )
        ).first()
        if row is not None:
            return row[0], row[1], LinkKind.CODE
        match = await find_conversation(self._session, clean)
        if match is None:
            return None
        return match[0], match[1], LinkKind.CODE if match[2].startswith("Mã KH") else LinkKind.PHONE

    async def link_by_term(
        self, access: CskhAccess, thread_id: uuid.UUID, term: str, actor: Actor
    ) -> dict[str, Any]:
        """From the Zalo inbox: a customer code or a phone number typed in."""
        thread = await self.thread(access, thread_id)
        found = await self.resolve(term)
        if found is None:
            raise CskhNotFoundError(
                "cskh_zalo_no_match", "Không tìm thấy khách có mã KH / SĐT này trên hệ thống page."
            )
        conv_id, code, kind = found
        how = f"Gắn tay theo mã KH: {code}" if kind is LinkKind.CODE else f"Gắn tay: {term.strip()}"
        link(thread, conv_id, code, how, kind, user_id=actor.user_id)
        await self._session.flush()
        await self._linked(actor, thread)
        return (await self.views([thread]))[0]

    async def link_to_conversation(
        self,
        access: CskhAccess,
        thread_id: uuid.UUID,
        conversation_id: uuid.UUID,
        actor: Actor,
        *,
        term: str = "",
        match: str = "",
    ) -> dict[str, Any]:
        """From the page inbox's "Zalo telesale" tab: this conversation <-> a found thread."""
        thread = await self.thread(access, thread_id)
        conv = await self._visible_conversation(access, conversation_id)
        codes = [
            c
            for c in (
                await self._session.scalars(
                    select(CskhOrder.customer_code)
                    .where(CskhOrder.conversation_id == conv.id, CskhOrder.customer_code != "")
                    .order_by(CskhOrder.created_at.desc())
                )
            ).all()
            if c
        ]
        wanted = normalize_code(term)
        by_code = next((c for c in codes if wanted and c == wanted), None)
        if by_code is None and match.startswith("Mã KH"):
            picked = normalize_code(match[len("Mã KH") :])
            by_code = next((c for c in codes if c == picked), None)
        how = (
            f"Gắn tay theo mã KH: {by_code} (từ hộp chat page)"
            if by_code
            else f"Gắn tay từ hộp chat page ({actor.name})"
            + (f": {term.strip()}" if term.strip() else "")
        )
        link(
            thread,
            conv.id,
            by_code or (codes[0] if codes else ""),
            how,
            LinkKind.CODE if by_code else LinkKind.PHONE,
            user_id=actor.user_id,
        )
        await self._session.flush()
        await self._linked(actor, thread)
        return (await self.views([thread]))[0]

    async def unlink(
        self, access: CskhAccess, thread_id: uuid.UUID, actor: Actor
    ) -> dict[str, Any]:
        thread = await self.thread(access, thread_id)
        thread.conversation_id = None
        thread.customer_code = ""
        thread.link_how = ""
        thread.link_kind = None
        thread.linked_at = None
        thread.linked_by_user_id = None
        await self._session.flush()
        await self._linked(actor, thread)
        return (await self.views([thread]))[0]

    async def _visible_conversation(
        self, access: CskhAccess, conversation_id: uuid.UUID
    ) -> CskhConversation:
        conv = await self._session.get(CskhConversation, conversation_id)
        pages = await page_scope(self._session, access) if access.sees_inbox else set()
        if conv is None or (pages is not None and conv.page_id not in pages):
            raise CskhNotFoundError("cskh_conversation_not_found", "Không tìm thấy hội thoại.")
        return conv

    async def _linked(self, actor: Actor, thread: CskhZaloThread) -> None:
        await self._audit.record(
            AuditEntry(
                request_id=actor.request_id,
                action=AuditAction.CSKH_ZALO_LINKED,
                result=AuditResult.SUCCESS,
                actor_user_id=actor.user_id,
                entity_type="cskh_zalo_thread",
                entity_id=str(thread.id),
                after_data={
                    "conversation_id": str(thread.conversation_id)
                    if thread.conversation_id
                    else None,
                    "link_kind": thread.link_kind,
                },
            )
        )

    async def for_conversation(
        self, access: CskhAccess, conversation_id: uuid.UUID
    ) -> dict[str, Any]:
        """The page inbox's "Zalo telesale" tab: matched threads (+ messages), else suggestions."""
        conv = await self._visible_conversation(access, conversation_id)
        scope = await zalo_scope(self._session, access) if access.sees_zalo else set()
        query = select(CskhZaloThread).where(CskhZaloThread.conversation_id == conv.id)
        if scope is not None:
            query = query.where(CskhZaloThread.account_id.in_(scope))
        rows = list((await self._session.scalars(query)).all())
        threads = await self.views(rows)
        for view, row in zip(threads, rows, strict=True):
            messages = list(
                (
                    await self._session.scalars(
                        select(CskhZaloMessage)
                        .where(CskhZaloMessage.thread_id == row.id)
                        .order_by(CskhZaloMessage.at.desc())
                        .limit(MESSAGES_LIMIT)
                    )
                ).all()
            )[::-1]
            view["messages"] = [zalo_view(m) for m in messages]
        codes = list(
            dict.fromkeys(
                (
                    await self._session.scalars(
                        select(CskhOrder.customer_code).where(
                            CskhOrder.conversation_id == conv.id, CskhOrder.customer_code != ""
                        )
                    )
                ).all()
            )
        )
        suggest = [] if threads or scope == set() else await self._search("", conv, scope)
        return {
            "threads": threads,
            "codes": codes,
            "suggest": suggest,
            "may_link": access.sees_zalo,
        }

    async def search(
        self, access: CskhAccess, term: str, conversation_id: uuid.UUID | None
    ) -> list[dict[str, Any]]:
        scope = await self._scope(access)
        conv = (
            await self._visible_conversation(access, conversation_id) if conversation_id else None
        )
        return await self._search(term, conv, scope)

    async def _search(
        self, term: str, conv: CskhConversation | None, scope: set[str] | None
    ) -> list[dict[str, Any]]:
        """Threads by code / phone / Zalo name / words; no term + a conversation =
        suggestions by that customer's codes and numbers."""
        terms: list[tuple[str, str]] = []
        if term.strip():
            terms = [("Tìm", term.strip())]
        elif conv is not None:
            for code in dict.fromkeys(
                (
                    await self._session.scalars(
                        select(CskhOrder.customer_code).where(
                            CskhOrder.conversation_id == conv.id, CskhOrder.customer_code != ""
                        )
                    )
                ).all()
            ):
                if len(code) >= MIN_CODE_LENGTH:
                    terms.append(("Mã KH", code))
            phones = {str(p) for p in conv.phones or []}
            phones |= {
                p
                for p in (
                    await self._session.scalars(
                        select(CskhOrder.phone).where(
                            CskhOrder.conversation_id == conv.id, CskhOrder.phone != ""
                        )
                    )
                ).all()
                if p
            }
            terms += [("SĐT", p) for p in phones]
        if scope is not None and not scope:
            return []
        hits: dict[uuid.UUID, tuple[str, str]] = {}
        for why, value in terms:
            if looks_like_phone(value):
                key = re.sub(r"\D", "", value)[-9:]
                condition = CskhZaloMessage.text_digits.like(f"%{key}%")
                label = f"{why} {value}" if why != "Tìm" else f"SĐT {value}"
                rows = (
                    await self._session.execute(
                        select(CskhZaloMessage.thread_id, CskhZaloMessage.text)
                        .where(condition)
                        .order_by(CskhZaloMessage.at.desc())
                        .limit(400)
                    )
                ).all()
            else:
                if len(value) < 2:
                    continue
                key = fold(value)
                label = f"{why} {value}" if why != "Tìm" else f"“{value}”"
                rows = list(
                    (
                        await self._session.execute(
                            select(CskhZaloMessage.thread_id, CskhZaloMessage.text)
                            .where(CskhZaloMessage.text_key.like(f"%{key}%"))
                            .order_by(CskhZaloMessage.at.desc())
                            .limit(400)
                        )
                    ).all()
                )
                rows += list(
                    (
                        await self._session.execute(
                            select(CskhZaloThread.id, CskhZaloThread.last_text).where(
                                or_(
                                    func.lower(CskhZaloThread.peer_name).like(f"%{value.lower()}%"),
                                    CskhZaloThread.customer_code == normalize_code(value),
                                )
                            )
                        )
                    ).all()
                )
            for thread_id, text in rows:
                if thread_id not in hits:
                    hits[thread_id] = (label, clip(text or "", 160))
        if not hits:
            return []
        query = select(CskhZaloThread).where(CskhZaloThread.id.in_(list(hits)))
        if scope is not None:
            query = query.where(CskhZaloThread.account_id.in_(scope))
        rows_found = list((await self._session.scalars(query)).all())
        order = list(hits)
        rows_found.sort(key=lambda t: order.index(t.id))
        out = []
        for view in await self.views(rows_found[:SEARCH_LIMIT]):
            if conv is not None and view["conversation_id"] == str(conv.id):
                continue
            view["match"], view["snippet"] = hits[uuid.UUID(view["id"])]
            out.append(view)
        return out

    # --- nicks -------------------------------------------------------------------------------

    async def accounts(self, access: CskhAccess) -> dict[str, Any]:
        scope = await self._scope(access)
        query = select(CskhZaloAccount).order_by(CskhZaloAccount.last_at.desc().nulls_last())
        if scope is not None:
            query = query.where(CskhZaloAccount.id.in_(scope))
        rows = list((await self._session.scalars(query)).all())
        alive = await self._bridge.alive() if self._bridge.configured else False
        live: dict[str, dict[str, Any]] = {}
        if alive:
            try:
                live = {str(a.get("id")): a for a in await self._bridge.accounts()}
            except CskhUpstreamError:
                live = {}
        counts = {
            account_id: (int(total or 0), int(linked or 0), int(unread or 0))
            for account_id, total, linked, unread in (
                await self._session.execute(
                    select(
                        CskhZaloThread.account_id,
                        func.count(),
                        func.count(CskhZaloThread.conversation_id),
                        func.coalesce(func.sum(CskhZaloThread.unread), 0),
                    ).group_by(CskhZaloThread.account_id)
                )
            ).all()
        }
        sales = await names_of(self._session, (a.user_id for a in rows))
        accounts = []
        for row in rows:
            state = live.get(row.id)
            status, note = row.status, row.status_note
            if state is not None:
                status = (
                    ZaloStatus.CONNECTED.value
                    if state.get("connected")
                    else ZaloStatus.CLOSED.value
                )
                note = str(state.get("reason") or "")
            elif alive and status == ZaloStatus.CONNECTED.value:
                status, note = ZaloStatus.CLOSED.value, "Dịch vụ Zalo không giữ phiên của nick này"
            total, linked, unread = counts.get(row.id, (0, 0, 0))
            accounts.append(
                {
                    "id": row.id,
                    "name": row.name or row.id,
                    "phone": row.phone,
                    "avatar": row.avatar,
                    "user_id": str(row.user_id) if row.user_id else None,
                    "sale_name": sales.get(row.user_id) if row.user_id else None,
                    "status": status,
                    "status_label": ZALO_STATUS_LABELS.get(status, status),
                    "status_note": note,
                    "status_at": iso(row.status_at),
                    "last_at": iso(row.last_at),
                    "threads": total,
                    "linked": linked,
                    "unlinked": total - linked,
                    "unread": unread,
                }
            )
        return {
            "accounts": accounts,
            "bridge": {"configured": self._bridge.configured, "alive": alive},
            "may_manage": access.manage,
            "may_scan": access.sees_zalo,
            "sales": await self._sales() if access.manage or access.zalo == "ALL" else [],
        }

    async def _sales(self) -> list[dict[str, str]]:
        rows = (
            await self._session.execute(
                select(User.id, User.full_name)
                .where(User.deleted_at.is_(None), User.active.is_(True))
                .order_by(User.full_name)
            )
        ).all()
        return [{"user_id": str(user_id), "name": name} for user_id, name in rows]

    async def assign(
        self,
        access: CskhAccess,
        account_ids: Iterable[str],
        user_id: uuid.UUID | None,
        actor: Actor,
    ) -> int:
        access.require_manage()
        if user_id is not None:
            user = await self._session.get(User, user_id)
            if user is None or user.deleted_at is not None:
                raise CskhInvalidError(
                    "cskh_person_invalid", "Người được chọn không còn.", field="user_id"
                )
        changed = 0
        for account_id in account_ids:
            account = await self._session.get(CskhZaloAccount, str(account_id))
            if account is None:
                continue
            account.user_id = user_id
            changed += 1
            await self._audit.record(
                AuditEntry(
                    request_id=actor.request_id,
                    action=AuditAction.CSKH_ZALO_ACCOUNT_UPDATED,
                    result=AuditResult.SUCCESS,
                    actor_user_id=actor.user_id,
                    entity_type="cskh_zalo_account",
                    entity_id=account.id,
                    after_data={"user_id": str(user_id) if user_id else None},
                )
            )
        await self._session.flush()
        return changed

    async def logout(self, access: CskhAccess, account_id: str, actor: Actor) -> None:
        access.require_manage()
        account = await self._session.get(CskhZaloAccount, account_id)
        if account is None:
            raise CskhNotFoundError("cskh_zalo_account_not_found", "Không tìm thấy nick Zalo.")
        try:
            await self._bridge.logout(account_id)
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        account.status = ZaloStatus.LOGOUT.value
        account.status_note = "Đã ngắt kết nối trên hệ thống"
        account.status_at = utcnow()
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=actor.request_id,
                action=AuditAction.CSKH_ZALO_ACCOUNT_LOGOUT,
                result=AuditResult.SUCCESS,
                actor_user_id=actor.user_id,
                entity_type="cskh_zalo_account",
                entity_id=account.id,
            )
        )

    # --- QR login ----------------------------------------------------------------------

    async def qr_start(
        self, access: CskhAccess, owner_id: uuid.UUID | None, owners: dict[str, uuid.UUID | None]
    ) -> dict[str, Any]:
        """A telesale's own scan hands the nick to them; a manager picks the telesale."""
        access.require_zalo()
        if access.zalo == "OWN" and not access.manage:
            owner_id = access.user_id
        try:
            started = await self._bridge.qr_start()
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        session_id = str(started.get("session") or "")
        if session_id:
            owners[session_id] = owner_id
        return {
            "session": session_id,
            "state": started.get("state"),
            "image": started.get("image"),
            "error": started.get("error"),
        }

    async def qr_status(
        self, access: CskhAccess, session_id: str, owners: dict[str, uuid.UUID | None]
    ) -> dict[str, Any]:
        access.require_zalo()
        if session_id not in owners:
            raise CskhForbiddenError("Phiên quét QR này không phải của bạn.")
        try:
            state = await self._bridge.qr_status(session_id)
        except CskhUpstreamError as error:
            raise CskhUpstreamRefusedError(error.code, error.message) from error
        if state.get("state") == "ok" and state.get("accountId"):
            owner = owners.pop(session_id, None)
            account_id = str(state["accountId"])[:40]
            account = await self._session.get(CskhZaloAccount, account_id)
            if account is None:
                account = CskhZaloAccount(id=account_id)
                self._session.add(account)
            account.status = ZaloStatus.CONNECTED.value
            account.status_note = ""
            account.status_at = utcnow()
            if state.get("name"):
                account.name = str(state["name"])[:200]
            if state.get("phone"):
                account.phone = str(state["phone"])[:30]
            if state.get("avatar"):
                account.avatar = str(state["avatar"])[:1000]
            if owner is not None and account.user_id is None:
                account.user_id = owner
            await self._session.flush()
        return {
            key: state.get(key)
            for key in ("state", "image", "name", "phone", "avatar", "accountId", "error")
        }


def zalo_view(m: CskhZaloMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "direction": m.direction,
        "sender_name": m.sender_name,
        "text": m.text,
        "kind": m.kind,
        "at": iso(m.at),
    }


def mask_message(view: dict[str, Any]) -> dict[str, Any]:
    out = dict(view)
    out["text"] = mask_phones_in(out.get("text"))
    out["attachments"] = mask_deep(out.get("attachments") or [])
    if out.get("qr") is not None:
        out["qr"] = mask_deep(out["qr"])
    return out


__all__ = [
    "CskhZalo",
    "find_conversation",
    "link",
    "mask_message",
    "save_zalo_message",
    "take_push",
    "zalo_view",
]
