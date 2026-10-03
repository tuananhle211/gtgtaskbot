"""The announcement somebody is still composing, and who it is going to.

This is where the two reported conversations went wrong, and the fix is
structural rather than conversational. In 0.6.0a2 a draft was one id in the FSM
store; a *set* of chosen recipients had nowhere to live, so:

* "Tất cả" had nothing to expand into, and the next turn asked what the owner
  wanted to do with the groups - which the draft, had there been one, already
  said;
* "Xác nhận" had nothing to confirm, and the next turn asked which recipients
  were meant - which the draft, had there been one, was holding.

So the draft is a table. It holds the content, every candidate, which of them
are ticked, which phrases did not resolve, who is composing, where, until when,
and a version. All of that survives a restart, which matters because a bot
deploy in the middle of somebody writing a department announcement should not
lose their words.

**One open draft per (bot, chat, person).** Opening a second closes the first,
so "Xác nhận" is never ambiguous about which draft it means. That is a stricter
rule than a unique index could express - "open" is two statuses and expiry moves
- so it is enforced here, in one place.

Nothing in this module sends anything, and nothing here decides permissions. It
records choices; :class:`~meobot.application.dispatch_service.DispatchService`
decides whether they may be acted on.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.recipient_resolver import RecipientCandidate
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.dispatch import MessageDispatchDraft, MessageDispatchDraftRecipient
from meobot.db.models.notifications import TelegramChat
from meobot.domain.dispatch.models import DraftStatus, SelectionSource
from meobot.domain.dispatch.privacy import classify_announcement
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

#: How long a draft stays continuable. Long enough to be interrupted by a
#: meeting; short enough that "Xác nhận" tomorrow cannot send words nobody has
#: looked at since yesterday.
DRAFT_TTL = timedelta(hours=6)


class DispatchDraftService:
    """Creates and edits the durable multi-destination draft.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Currently only the draft lifetime, via :data:`DRAFT_TTL`.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Reading ----------------------------------------------------------
    async def active(
        self, *, bot_identity: int, source_chat_id: int, telegram_user_id: int
    ) -> MessageDispatchDraft | None:
        """The draft that owns this turn, or ``None``.

        Bound to all three of bot, chat and person. A draft started privately
        is not continuable from a group, and one person's "Xác nhận" in a group
        can never confirm somebody else's - which is the same rule the signed
        callbacks enforce, applied to typed answers.

        An expired draft is marked expired here rather than left to be found by
        a sweep, so the very next turn says so plainly instead of acting on it.
        """
        result = await self._session.execute(
            select(MessageDispatchDraft)
            .where(
                MessageDispatchDraft.bot_identity == bot_identity,
                MessageDispatchDraft.source_chat_id == source_chat_id,
                MessageDispatchDraft.created_by_telegram_id == telegram_user_id,
                MessageDispatchDraft.status.in_([DraftStatus.CHOOSING, DraftStatus.PREVIEW]),
            )
            .order_by(MessageDispatchDraft.created_at.desc())
        )
        rows = list(result.scalars().all())
        if not rows:
            return None

        live: MessageDispatchDraft | None = None
        now = utcnow()
        for row in rows:
            if live is None and ensure_utc(row.expires_at) > now:
                live = row
            else:
                # Either expired, or an older draft the "one open draft" rule
                # should already have closed. Both are closed here.
                row.status = DraftStatus.EXPIRED
        await self._session.flush()
        return live

    async def by_id(self, draft_id: uuid.UUID) -> MessageDispatchDraft | None:
        return await self._session.get(MessageDispatchDraft, draft_id)

    async def recipients(self, draft_id: uuid.UUID) -> list[MessageDispatchDraftRecipient]:
        """Every candidate on one draft, in the order it was shown.

        Position order, not insertion order: "hai group đầu" counts off the
        card somebody is looking at, and a database ordering that changed
        between the card and the reply would silently select different groups.
        """
        result = await self._session.execute(
            select(MessageDispatchDraftRecipient)
            .where(MessageDispatchDraftRecipient.draft_id == draft_id)
            .order_by(MessageDispatchDraftRecipient.position.asc())
        )
        return list(result.scalars().all())

    async def selected(self, draft_id: uuid.UUID) -> list[MessageDispatchDraftRecipient]:
        """Only the ticked candidates that the sender may actually use."""
        return [row for row in await self.recipients(draft_id) if row.selected and row.permitted]

    async def recipient_row(self, recipient_id: uuid.UUID) -> MessageDispatchDraftRecipient | None:
        return await self._session.get(MessageDispatchDraftRecipient, recipient_id)

    # --- Creating ---------------------------------------------------------
    async def open(
        self,
        *,
        actor: Actor,
        bot_identity: int,
        source_chat_id: int,
        original_text: str,
        rendered_text: str,
        candidates: Sequence[RecipientCandidate],
        permitted_ids: set[uuid.UUID] | None = None,
        unresolved: Sequence[str] = (),
        preselect: bool = True,
    ) -> MessageDispatchDraft:
        """Start a draft, closing any the same person already had open.

        Args:
            candidates: What the request resolved to, in the order they will be
                shown. Every one gets a row, ticked or not, because the
                keyboard has to render both states and "bỏ Test" needs
                something to untick rather than something to delete.
            permitted_ids: Destinations this person may actually address.
                Anything outside it is recorded ``permitted=False`` and shown
                with a reason rather than quietly dropped - a list that is
                shorter than what somebody asked for, with no explanation, is
                how they end up believing a group received something.
            preselect: Tick everything. True when the request named or
                described the destinations; false when the person asked to
                choose from a list.
        """
        await self._close_open(
            bot_identity=bot_identity,
            source_chat_id=source_chat_id,
            telegram_user_id=actor.telegram_user_id,
        )

        verdict = classify_announcement(rendered_text)
        draft = MessageDispatchDraft(
            bot_identity=bot_identity,
            created_by_user_id=actor.user_id,
            created_by_telegram_id=actor.telegram_user_id,
            source_chat_id=source_chat_id,
            original_text=original_text,
            rendered_text=rendered_text,
            privacy_classification=verdict.classification,
            unresolved_phrases="\n".join(unresolved) or None,
            status=DraftStatus.CHOOSING,
            version=1,
            expires_at=utcnow() + DRAFT_TTL,
        )
        self._session.add(draft)
        await self._session.flush()

        allowed = permitted_ids if permitted_ids is not None else {c.chat.id for c in candidates}
        for position, candidate in enumerate(candidates, start=1):
            permitted = candidate.chat.id in allowed
            self._session.add(
                MessageDispatchDraftRecipient(
                    draft_id=draft.id,
                    recipient_chat_row_id=candidate.chat.id,
                    position=position,
                    display_name=candidate.chat.display_name[:200],
                    selected=preselect and permitted,
                    selection_source=candidate.source,
                    permitted=permitted,
                )
            )
        await self._session.flush()
        logger.info(
            "dispatch_draft_opened",
            extra={"draft_id": str(draft.id), "candidates": len(candidates)},
        )
        return draft

    async def _close_open(
        self, *, bot_identity: int, source_chat_id: int, telegram_user_id: int | None
    ) -> None:
        """One open draft per person per chat. Opening a second closes the first."""
        result = await self._session.execute(
            select(MessageDispatchDraft).where(
                MessageDispatchDraft.bot_identity == bot_identity,
                MessageDispatchDraft.source_chat_id == source_chat_id,
                MessageDispatchDraft.created_by_telegram_id == telegram_user_id,
                MessageDispatchDraft.status.in_([DraftStatus.CHOOSING, DraftStatus.PREVIEW]),
            )
        )
        for row in result.scalars().all():
            row.status = DraftStatus.CANCELLED
        await self._session.flush()

    # --- Editing the selection -------------------------------------------
    async def select_all(self, draft: MessageDispatchDraft) -> list[MessageDispatchDraftRecipient]:
        """ "Tất cả" - tick every candidate the sender may use."""
        rows = await self.recipients(draft.id)
        for row in rows:
            row.selected = row.permitted
            if row.permitted:
                row.selection_source = SelectionSource.ALL_REGISTERED
        await self._bump(draft)
        return rows

    async def select_none(self, draft: MessageDispatchDraft) -> None:
        rows = await self.recipients(draft.id)
        for row in rows:
            row.selected = False
        await self._bump(draft)

    async def select_positions(
        self, draft: MessageDispatchDraft, positions: Sequence[int]
    ) -> list[MessageDispatchDraftRecipient]:
        """ "Hai group đầu", "group 1 và 3" - tick exactly these positions."""
        wanted = set(positions)
        rows = await self.recipients(draft.id)
        for row in rows:
            row.selected = row.position in wanted and row.permitted
            if row.selected:
                row.selection_source = SelectionSource.LIST_REFERENCE
        await self._bump(draft)
        return [row for row in rows if row.selected]

    async def set_selected(
        self, draft: MessageDispatchDraft, *, recipient_id: uuid.UUID, selected: bool
    ) -> MessageDispatchDraftRecipient | None:
        """Tick or untick one candidate.

        Deliberately a *set* and not a toggle. The button rendered for an
        unticked group asks to tick it and the one for a ticked group asks to
        untick it, so pressing the same button twice is a no-op the second time
        rather than an undo somebody did not ask for.
        """
        row = await self.recipient_row(recipient_id)
        if row is None or row.draft_id != draft.id:
            return None
        if selected and not row.permitted:
            return row
        if row.selected != selected:
            row.selected = selected
            row.selection_source = SelectionSource.BUTTON
            await self._bump(draft)
        return row

    async def restrict_to(
        self, draft: MessageDispatchDraft, chat_ids: Sequence[uuid.UUID]
    ) -> list[MessageDispatchDraftRecipient]:
        """ "Chỉ Saykeng" - tick these and untick everything else."""
        wanted = set(chat_ids)
        rows = await self.recipients(draft.id)
        for row in rows:
            row.selected = row.recipient_chat_row_id in wanted and row.permitted
        await self._bump(draft)
        return [row for row in rows if row.selected]

    async def exclude(
        self, draft: MessageDispatchDraft, chat_ids: Sequence[uuid.UUID]
    ) -> list[MessageDispatchDraftRecipient]:
        """ "Bỏ Test", "không gửi Kết bạn" - untick these, leave the rest."""
        unwanted = set(chat_ids)
        rows = await self.recipients(draft.id)
        for row in rows:
            if row.recipient_chat_row_id in unwanted:
                row.selected = False
        await self._bump(draft)
        return [row for row in rows if row.selected]

    async def add(
        self,
        draft: MessageDispatchDraft,
        chats: Sequence[TelegramChat],
        *,
        permitted_ids: set[uuid.UUID] | None = None,
    ) -> list[MessageDispatchDraftRecipient]:
        """ "Thêm group Test" - put a destination on the draft and tick it."""
        existing = await self.recipients(draft.id)
        by_chat = {row.recipient_chat_row_id: row for row in existing}
        position = max((row.position for row in existing), default=0)
        for chat in chats:
            found = by_chat.get(chat.id)
            permitted = permitted_ids is None or chat.id in permitted_ids
            if found is not None:
                found.selected = permitted
                continue
            position += 1
            self._session.add(
                MessageDispatchDraftRecipient(
                    draft_id=draft.id,
                    recipient_chat_row_id=chat.id,
                    position=position,
                    display_name=chat.display_name[:200],
                    selected=permitted,
                    selection_source=SelectionSource.NAMED,
                    permitted=permitted,
                )
            )
        await self._bump(draft)
        return await self.recipients(draft.id)

    async def replace_candidates(
        self,
        draft: MessageDispatchDraft,
        candidates: Sequence[RecipientCandidate],
        *,
        permitted_ids: set[uuid.UUID] | None = None,
        preselect: bool = False,
    ) -> list[MessageDispatchDraftRecipient]:
        """Start the recipient list again, keeping the content.

        What "☑️ Chọn lại group" does. The words somebody wrote are the
        expensive part of a draft and are never discarded to change who reads
        them.
        """
        for row in await self.recipients(draft.id):
            await self._session.delete(row)
        await self._session.flush()

        allowed = permitted_ids if permitted_ids is not None else {c.chat.id for c in candidates}
        for position, candidate in enumerate(candidates, start=1):
            permitted = candidate.chat.id in allowed
            self._session.add(
                MessageDispatchDraftRecipient(
                    draft_id=draft.id,
                    recipient_chat_row_id=candidate.chat.id,
                    position=position,
                    display_name=candidate.chat.display_name[:200],
                    selected=preselect and permitted,
                    selection_source=candidate.source,
                    permitted=permitted,
                )
            )
        draft.status = DraftStatus.CHOOSING
        await self._bump(draft)
        return await self.recipients(draft.id)

    # --- Status transitions ----------------------------------------------
    async def mark_preview(self, draft: MessageDispatchDraft) -> MessageDispatchDraft:
        """The full card has been shown; the next "Xác nhận" means this."""
        if draft.status is DraftStatus.CHOOSING:
            draft.status = DraftStatus.PREVIEW
            await self._session.flush()
        return draft

    async def cancel(self, draft: MessageDispatchDraft) -> MessageDispatchDraft:
        draft.status = DraftStatus.CANCELLED
        draft.version += 1
        await self._session.flush()
        return draft

    async def mark_confirmed(self, draft: MessageDispatchDraft, *, dispatch_id: uuid.UUID) -> None:
        """Point the draft at the dispatch it became.

        Kept rather than deleted so a second "Xác nhận" - typed while the
        button press is still in flight - finds the dispatch that already
        exists instead of building a second one.
        """
        draft.status = DraftStatus.CONFIRMED
        draft.dispatch_id = dispatch_id
        await self._session.flush()

    async def _bump(self, draft: MessageDispatchDraft) -> None:
        """Every selection change invalidates every button drawn before it."""
        draft.version += 1
        await self._session.flush()
