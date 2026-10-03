"""Owner-authored announcements: draft, confirm, publish.

**Two-step by design.** Anything a person *wrote* that is about to appear in a
group they are not in gets a preview and an explicit confirmation. The draft
row exists first, the outbound message only after the press - so a
half-composed thought is never one network hiccup away from the whole
department.

That is the line this release draws: **user-authored content is confirmed,
system-generated notifications are not.** An HR approval card is not confirmed
a second time, because the owner already confirmed the decision that produced
it; an announcement is, because nobody has agreed to those words yet.

Publishing writes the announcement transition and the outbound message in one
transaction. Telegram is not involved here at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.announcement_audience_service import AnnouncementAudienceService
from meobot.application.audit_service import AuditService
from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    RouteResult,
    announcement_key,
)
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConflictError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.notifications import (
    Announcement,
    AnnouncementAcknowledgement,
    TelegramChat,
)
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import (
    AnnouncementStatus,
    ChatPurpose,
    NotificationEvent,
)
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

MAX_CONTENT = 3000

CANNOT_BROADCAST = (
    f"Bạn chưa được phép gửi thông báo tới group. Việc này thuộc quyền {role_label(Role.OWNER)}."
)
NOT_THIS_DESTINATION = (
    "Bạn chưa được phép gửi vào nơi nhận này. Bạn chỉ gửi được tới group của team mình phụ trách."
)
ALREADY_PUBLISHED = "Thông báo này đã được gửi rồi."


class AnnouncementService:
    """Drafts, confirms and publishes announcements.

    Args:
        session: Unit of work. Publishing writes the transition and the
            outbound message here, together.
        settings: Broadcast limits.
        audit: Audit writer sharing the session.
    """

    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._router = NotificationRouter(session, settings)
        self._assignments = ChatAssignmentService(session)

    # --- Authorization ----------------------------------------------------
    @staticmethod
    def may_broadcast(actor: Actor) -> bool:
        """Whether this person may send to any group at all.

        Deliberately narrow. ``SETTINGS_WRITE`` is held by OWNER and ADMIN, but
        broadcasting is not a settings change - it is speaking to the whole
        department in MeoBot's voice, so it needs its own permission and the
        owner is the only role that has it.
        """
        return has_permission(actor.role, Permission.ANNOUNCEMENT_BROADCAST)

    async def may_use(self, actor: Actor, destination: TelegramChat) -> bool:
        """Whether this person may send to *this* destination.

        Since 0.6.0a2 a Trưởng nhóm can genuinely be allowed here, but only
        through an explicit assignment to this exact group - see
        :class:`~meobot.application.chat_assignment_service.ChatAssignmentService`.
        Never by purpose: a team lead who manages Content has no authority over
        Seeding, and purpose-based reasoning would hand them both.

        A department-wide destination is never reachable this way. Assignments
        grant authority over one group; speaking for the whole department is a
        different thing, and it stays with the owner.
        """
        if self.may_broadcast(actor):
            return True
        if destination.purpose is ChatPurpose.DEPARTMENT_ANNOUNCEMENTS:
            return False
        return await self._assignments.may_broadcast_to(user_id=actor.user_id, chat=destination)

    # --- Drafting ---------------------------------------------------------
    async def draft(
        self,
        *,
        actor: Actor,
        content: str,
        source_chat_id: int,
        destination: TelegramChat | None,
        request_read_receipt: bool = False,
    ) -> Announcement:
        """Create an unsent draft.

        Raises:
            AuthorizationError: The actor may not broadcast at all, or may not
                use this destination.
        """
        if not self.may_broadcast(actor) and destination is None:
            raise AuthorizationError(CANNOT_BROADCAST)
        if destination is not None and not await self.may_use(actor, destination):
            raise AuthorizationError(NOT_THIS_DESTINATION)

        row = Announcement(
            created_by_user_id=actor.user_id,
            created_by_telegram_id=actor.telegram_user_id,
            source_chat_id=source_chat_id,
            content=content.strip()[:MAX_CONTENT],
            destination_chat_id=destination.id if destination is not None else None,
            status=AnnouncementStatus.DRAFT,
            request_read_receipt=request_read_receipt,
            version=1,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_destination(
        self, *, actor: Actor, announcement_id: uuid.UUID, destination: TelegramChat
    ) -> Announcement:
        """Point a draft at a different group."""
        row = await self.require(announcement_id)
        if row.status is not AnnouncementStatus.DRAFT:
            raise ConflictError(ALREADY_PUBLISHED)
        if not await self.may_use(actor, destination):
            raise AuthorizationError(NOT_THIS_DESTINATION)
        row.destination_chat_id = destination.id
        row.version += 1
        await self._session.flush()
        return row

    # --- Publishing -------------------------------------------------------
    async def publish(
        self, *, actor: Actor, request_id: uuid.UUID, announcement_id: uuid.UUID
    ) -> tuple[Announcement, RouteResult]:
        """Confirm a draft and queue it for delivery. Idempotent.

        The transition and the outbound message are written in the caller's
        transaction, so a crash between them is impossible. Publishing an
        already-published announcement returns it unchanged rather than
        queueing a second copy.
        """
        row = await self.require(announcement_id)
        if not self.may_broadcast(actor) and row.destination_chat_id is None:
            raise AuthorizationError(CANNOT_BROADCAST)

        destination = (
            await self._session.get(TelegramChat, row.destination_chat_id)
            if row.destination_chat_id is not None
            else None
        )
        if destination is None:
            raise ConflictError(
                "Thông báo này chưa có nơi nhận. Bạn chọn group rồi gửi lại giúp mình nhé."
            )
        if not await self.may_use(actor, destination):
            raise AuthorizationError(NOT_THIS_DESTINATION)

        if row.status is AnnouncementStatus.PUBLISHED:
            # Already confirmed. Return the previous successful result rather
            # than sending the department a second copy.
            return row, RouteResult()

        row.status = AnnouncementStatus.PUBLISHED
        row.confirmed_at = utcnow()
        row.version += 1
        await self._session.flush()

        # Snapshot who was expected to read this, now, in the publishing
        # transaction. Computing it later would answer a different question
        # every time somebody joins or leaves - a person hired next week has
        # not failed to read today's announcement.
        expected = await AnnouncementAudienceService(self._session).snapshot(
            announcement=row, destination=destination
        )

        result = await self._router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.ANNOUNCEMENT_PUBLISHED,
                    template_key="announcement.group",
                    payload={
                        "content": row.content,
                        # A flag for the delivery worker, never rendered.
                        "request_read_receipt": bool(row.request_read_receipt),
                    },
                    idempotency_key=announcement_key(
                        row.id, destination.telegram_chat_id, row.version
                    ),
                    aggregate_type="announcement",
                    aggregate_id=row.id,
                    destination=destination,
                    source_chat_id=row.source_chat_id,
                    created_by_user_id=actor.user_id,
                )
            ]
        )

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ANNOUNCEMENT_PUBLISHED.value,
            result=AuditResult.SUCCESS if result.any_queued else AuditResult.DENIED,
            entity_type="announcement",
            entity_id=str(row.id),
            after_data={
                # Source *and* destination, which is what makes a cross-chat
                # action reconstructable afterwards.
                "source_chat_id": row.source_chat_id,
                "destination_chat_id": destination.telegram_chat_id,
                "destination_purpose": destination.purpose.value,
                "privacy_classification": "PUBLIC_OPERATIONAL",
                "outbound_message_ids": [str(item.id) for item in result.queued],
                "expected_recipients": expected,
                "request_read_receipt": bool(row.request_read_receipt),
                "actor_role": actor.role.value,
                "actor_role_label": role_label(actor.role),
            },
        )
        logger.info(
            "announcement_published",
            extra={"announcement_id": str(row.id), "queued": len(result.queued)},
        )
        return row, result

    async def cancel(self, *, announcement_id: uuid.UUID) -> Announcement:
        """Abandon a draft. Nothing is deleted."""
        row = await self.require(announcement_id)
        if row.status is AnnouncementStatus.PUBLISHED:
            raise ConflictError(ALREADY_PUBLISHED)
        row.status = AnnouncementStatus.CANCELLED
        row.version += 1
        await self._session.flush()
        return row

    # --- Acknowledgements -------------------------------------------------
    async def acknowledge(
        self,
        *,
        announcement_id: uuid.UUID,
        telegram_user_id: int,
        user_id: uuid.UUID | None,
        display_name: str | None,
        needs_followup: bool = False,
    ) -> tuple[AnnouncementAcknowledgement, bool]:
        """Record that somebody read it. Returns ``(row, created)``.

        Unique per (announcement, person), so pressing twice records once and
        nobody can acknowledge on somebody else's behalf.
        """
        existing = await self._session.execute(
            select(AnnouncementAcknowledgement).where(
                AnnouncementAcknowledgement.announcement_id == announcement_id,
                AnnouncementAcknowledgement.telegram_user_id == telegram_user_id,
            )
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            if needs_followup and not found.needs_followup:
                found.needs_followup = True
                await self._session.flush()
            return found, False

        row = AnnouncementAcknowledgement(
            announcement_id=announcement_id,
            telegram_user_id=telegram_user_id,
            user_id=user_id,
            display_name=(display_name or None) and display_name[:300],
            needs_followup=needs_followup,
            created_at=utcnow(),
        )
        self._session.add(row)
        await self._session.flush()
        return row, True

    async def acknowledgements(
        self, announcement_id: uuid.UUID
    ) -> Sequence[AnnouncementAcknowledgement]:
        """Everybody who confirmed one announcement."""
        result = await self._session.execute(
            select(AnnouncementAcknowledgement)
            .where(AnnouncementAcknowledgement.announcement_id == announcement_id)
            .order_by(AnnouncementAcknowledgement.created_at.asc())
        )
        return result.scalars().all()

    # --- Reading ----------------------------------------------------------
    async def require(self, announcement_id: uuid.UUID) -> Announcement:
        row = await self._session.get(Announcement, announcement_id)
        if row is None:
            raise ConflictError("Mình không còn tìm thấy thông báo này.")
        return row

    async def recent(self, *, actor: Actor, limit: int = 10) -> Sequence[Announcement]:
        """This person's own recent announcements."""
        result = await self._session.execute(
            select(Announcement)
            .where(Announcement.created_by_user_id == actor.user_id)
            .order_by(Announcement.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


def default_purpose_for(text: str) -> ChatPurpose | None:
    """Read a purpose out of a registration sentence, or ``None``.

    Deterministic phrase matching. Nothing here asks a model what a group is
    for - and when nothing matches, MeoBot asks rather than picking.
    """
    from meobot.application.recipient_resolver import PURPOSE_PHRASES
    from meobot.domain.member.normalization import strip_accents

    folded = " ".join(strip_accents(text or "").split())
    for phrase, purpose in PURPOSE_PHRASES.items():
        if phrase in folded:
            return purpose
    return None
