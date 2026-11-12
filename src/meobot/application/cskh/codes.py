"""A page conversation's customer codes ("Mã KH", 0104).

Two sources: the confirmed orders (``cskh_orders.customer_code``, they win) and the
customer notes the care team writes on Pancake ("KH1426035 - hẹn gọi lại 20h"),
kept in ``cskh_conversation_codes``. Ported from the prototype's 12/11 update
(``pancake_save_notes``, ``codes_of_conv``, ``zalo_link_by_new_code``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cskh import (
    CskhConversation,
    CskhConversationCode,
    CskhOrder,
    CskhZaloMessage,
    CskhZaloThread,
)
from meobot.domain.cskh.models import (
    MIN_CODE_LENGTH,
    LinkKind,
    clip,
    code_in_text,
    normalize_code,
    note_codes,
    parse_notes,
)

NOTE_SOURCE = "pancake_note"


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


async def codes_of(session: AsyncSession, conversation_id: uuid.UUID) -> list[str]:
    """Every code of one conversation: its orders' (newest first), then its notes'."""
    ordered = (
        await session.scalars(
            select(CskhOrder.customer_code)
            .where(CskhOrder.conversation_id == conversation_id, CskhOrder.customer_code != "")
            .order_by(CskhOrder.created_at.desc())
        )
    ).all()
    noted = (
        await session.scalars(
            select(CskhConversationCode.code)
            .where(CskhConversationCode.conversation_id == conversation_id)
            .order_by(CskhConversationCode.at.desc())
        )
    ).all()
    return list(dict.fromkeys(normalize_code(c) for c in [*ordered, *noted] if c))


async def noted_codes(
    session: AsyncSession, conversation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """The notes' codes of several conversations (the list's rows)."""
    out: dict[uuid.UUID, list[str]] = {}
    if not conversation_ids:
        return out
    for conv_id, code in (
        await session.execute(
            select(CskhConversationCode.conversation_id, CskhConversationCode.code)
            .where(CskhConversationCode.conversation_id.in_(conversation_ids))
            .order_by(CskhConversationCode.at.desc())
        )
    ).all():
        out.setdefault(conv_id, []).append(code)
    return out


async def all_codes(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Every code ≥ 4 characters -> its conversation; an order's code beats a note's."""
    out: dict[str, uuid.UUID] = {}
    for code, conv_id in (
        await session.execute(
            select(CskhConversationCode.code, CskhConversationCode.conversation_id).order_by(
                CskhConversationCode.at
            )
        )
    ).all():
        if len(code) >= MIN_CODE_LENGTH:
            out[code.upper()] = conv_id
    for code, conv_id in (
        await session.execute(
            select(CskhOrder.customer_code, CskhOrder.conversation_id)
            .where(
                CskhOrder.customer_code != "",
                func.length(CskhOrder.customer_code) >= MIN_CODE_LENGTH,
            )
            .order_by(CskhOrder.created_at)
        )
    ).all():
        out[code.upper()] = conv_id
    return out


async def conversation_by_code(
    session: AsyncSession, term: str
) -> tuple[uuid.UUID, str, str] | None:
    """``(conversation, code, where)`` for a typed code: an order's, else a note's."""
    typed = normalize_code(term)
    # A note's code is kept as KH + digits: "KH-50081" / "kh 50081" typed find it.
    code = "".join(ch for ch in typed if ch.isalnum())
    if not code:
        return None
    row = (
        await session.execute(
            select(CskhOrder.conversation_id, CskhOrder.customer_code)
            .where(func.upper(CskhOrder.customer_code) == typed, CskhOrder.customer_code != "")
            .order_by(CskhOrder.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is not None:
        return row[0], row[1], "order"
    noted = (
        await session.execute(
            select(CskhConversationCode.conversation_id, CskhConversationCode.code)
            .where(CskhConversationCode.code == code)
            .order_by(CskhConversationCode.at.desc())
            .limit(1)
        )
    ).first()
    if noted is not None:
        return noted[0], noted[1], "note"
    return None


async def save_notes(session: AsyncSession, conv: CskhConversation, raw: object) -> list[str]:
    """Pancake's notes of one customer: kept, their codes kept (a code no note holds
    any more dropped). Returns the codes new to this conversation."""
    items = parse_notes(raw)
    if items == list(conv.notes or []):
        return []
    conv.notes = items
    found: dict[str, datetime] = {}
    for item in items:
        at = _parse(item.get("at")) or utcnow()
        for code in note_codes(str(item.get("text") or "")):
            found[code] = at
    have = set(
        (
            await session.scalars(
                select(CskhConversationCode.code).where(
                    CskhConversationCode.conversation_id == conv.id,
                    CskhConversationCode.source == NOTE_SOURCE,
                )
            )
        ).all()
    )
    gone = have - set(found)
    if gone:
        await session.execute(
            delete(CskhConversationCode).where(
                CskhConversationCode.conversation_id == conv.id,
                CskhConversationCode.code.in_(list(gone)),
            )
        )
    new = [code for code in found if code not in have]
    for code in new:
        session.add(
            CskhConversationCode(
                conversation_id=conv.id, code=code, source=NOTE_SOURCE, at=found[code]
            )
        )
    await session.flush()
    return new


async def link_threads_by_code(session: AsyncSession, conversation_id: uuid.UUID, code: str) -> int:
    """A code just written on the page: an unmatched Zalo thread whose messages hold it
    is matched by code (the page side's numbers are then masked for the telesale)."""
    if len(code) < MIN_CODE_LENGTH:
        return 0
    rows = (
        await session.execute(
            select(CskhZaloThread, CskhZaloMessage.text)
            .join(CskhZaloMessage, CskhZaloMessage.thread_id == CskhZaloThread.id)
            .where(
                CskhZaloThread.conversation_id.is_(None),
                func.upper(CskhZaloMessage.text).like(f"%{code}%"),
            )
        )
    ).all()
    linked: set[uuid.UUID] = set()
    for thread, text in rows:
        if thread.id in linked or not code_in_text(code, text or ""):
            continue
        link(
            thread,
            conversation_id,
            code,
            f"Mã KH {code} trong tin Zalo (ghi chú Pancake)",
            LinkKind.CODE,
        )
        linked.add(thread.id)
    if linked:
        await session.flush()
    return len(linked)


def _parse(value: object) -> datetime | None:
    try:
        return ensure_utc(datetime.fromisoformat(str(value))) if value else None
    except ValueError:
        return None


__all__ = [
    "NOTE_SOURCE",
    "all_codes",
    "codes_of",
    "conversation_by_code",
    "link",
    "link_threads_by_code",
    "noted_codes",
    "save_notes",
]
