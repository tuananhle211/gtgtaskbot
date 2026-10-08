"""Creating, running and managing reminders.

The rule that shapes this whole module: **nothing is called created until it is
committed.** MeoBot previously answered "em đã ghi nhận lịch nhắc" to a sentence
it had merely understood, and the user found out it had not by the reminder not
arriving. So the flow is parse → preview → confirm → commit, and only the last
step produces a sentence containing "đã tạo".

Firing is deliberately split from delivery:

    sweep → claim a due reminder
          → create occurrence + outbox row in ONE transaction
          → advance next_run_at in the same transaction
          → the q_notifications worker sends it later

The occurrence and the outbox row commit together, which is why a reminder
cannot fire without producing a message and cannot produce a message without
recording that it fired. Telegram is never called inside that transaction: a
slow provider would otherwise hold a lock on the reminder for the length of an
HTTPS request.

Duplicate protection is one database constraint,
``uq_reminder_occurrences_reminder_moment``. Two Beat processes, an overlapping
sweep and a retried task all try to insert the same ``(reminder, instant)``
pair, and exactly one wins.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    reminder_occurrence_key,
)
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConflictError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.notifications import TelegramChat
from meobot.db.models.reminder import Reminder, ReminderOccurrence
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.reminders.models import (
    MissedOccurrencePolicy,
    OccurrenceStatus,
    ReminderDestinationType,
    ReminderStatus,
    ScheduleKind,
)
from meobot.domain.reminders.parsing import ReminderDraft, parse_reminder
from meobot.domain.reminders.schedule import (
    ReminderSchedule,
    describe,
    describe_instant,
    parse_recurrence_rule,
)

logger = get_logger(__name__)

#: Said when there is no durable user to own the reminder. Deliberately about
#: the *account*, not about a profile: see :meth:`ReminderService.create`.
ACCOUNT_NOT_READY = (
    "TasksBot cần hoàn tất tài khoản sử dụng trước khi tạo lịch nhắc.\nBạn nhấn /start nhé."
)

MEMBER_CANNOT_TARGET_OTHERS = (
    "TasksBot chỉ đặt lịch nhắc cho chính bạn. Nếu cần nhắc cả nhóm, bạn nhờ "
    "Trưởng phòng hoặc Trưởng nhóm phụ trách group đó giúp nhé."
)
NOT_YOUR_REMINDER = "Lịch nhắc này không phải của bạn."
NO_SUCH_REMINDER = "TasksBot không tìm thấy lịch nhắc nào như vậy."
REMINDERS_DISABLED = "Tính năng lịch nhắc hiện đang tắt."
NEEDS_CONTENT = "Bạn muốn TasksBot nhắc nội dung gì ạ?"


@dataclass(frozen=True, slots=True)
class ReminderPreview:
    """What the user is about to create. **Not** a reminder.

    Deliberately a separate type from :class:`Reminder` so no code path can
    accidentally describe one as the other - the whole bug this release fixed
    was a preview being reported as a committed record.
    """

    content: str
    schedule: ReminderSchedule
    first_run_at: datetime
    destination_label: str
    destination_type: ReminderDestinationType
    destination_chat: TelegramChat | None = None
    timezone_name: str = "Asia/Ho_Chi_Minh"

    def render(self, tz: ZoneInfo, *, timing: str | None = None) -> str:
        """The Vietnamese preview card, in the wording the spec fixed.

        Args:
            timing: How to describe the schedule, when the person's own words
                are clearer than the computed one. Somebody who said "sau 5
                phút" should see "Sau 5 phút" rather than a date they have to
                decode - a preview they cannot recognise is one they cannot
                check.
        """
        return "\n".join(
            [
                "⏰ LỊCH NHẮC",
                "",
                "Nội dung:",
                self.content,
                "",
                "Thời gian:",
                timing or describe(self.schedule),
                "",
                "Nơi nhận:",
                self.destination_label,
                "",
                "Lần nhắc đầu tiên:",
                describe_instant(self.first_run_at, tz=tz),
            ]
        )


class ReminderService:
    """The reminder module's application layer.

    Args:
        session: Unit of work. :meth:`create` and :meth:`fire` both need the
            caller's transaction, so the durable record and the message it
            produces commit together.
        settings: Sweep budget, grace window and default timezone.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._assignments = ChatAssignmentService(session)

    @property
    def _tz(self) -> ZoneInfo:
        return self._settings.reminder_timezone

    # --- Creating ---------------------------------------------------------
    async def preview(
        self,
        *,
        actor: Actor,
        text: str,
        destination_chat: TelegramChat | None = None,
        now: datetime | None = None,
    ) -> ReminderPreview:
        """Parse a sentence into something the user can check before committing.

        Raises:
            ValidationError: The sentence cannot become a reminder, with a
                Vietnamese explanation of why - an unsupported monthly rule, a
                missing time, or a missing subject.
            AuthorizationError: The destination is not this actor's to address.
        """
        if not self._settings.reminder_enabled:
            raise ValidationError(REMINDERS_DISABLED)

        moment = now or utcnow()
        result = parse_reminder(text, now=moment, tz=self._tz)
        if result.problem:
            raise ValidationError(result.problem)
        if result.draft is None:  # pragma: no cover - guarded by ``problem``
            raise ValidationError(NEEDS_CONTENT)
        if result.draft.needs_content:
            raise ValidationError(NEEDS_CONTENT)

        return await self.preview_for_draft(
            actor=actor, draft=result.draft, destination_chat=destination_chat, now=moment
        )

    async def preview_for_draft(
        self,
        *,
        actor: Actor,
        draft: ReminderDraft,
        destination_chat: TelegramChat | None = None,
        now: datetime | None = None,
    ) -> ReminderPreview:
        """Build a preview from an already-parsed draft.

        Separate entry point so the "4 giờ" clarification can re-use the draft
        it already produced rather than re-parsing a sentence the user has
        since amended by pressing a button.
        """
        moment = now or utcnow()
        if destination_chat is not None:
            await self._require_may_target(actor, destination_chat)
            label = f"Group {destination_chat.display_name}"
            destination_type = ReminderDestinationType.REGISTERED_CHAT
        else:
            label = "Chat riêng của bạn"
            destination_type = ReminderDestinationType.USER_PRIVATE

        return ReminderPreview(
            content=draft.content,
            schedule=draft.schedule,
            first_run_at=draft.schedule.next_after(moment, tz=self._tz),
            destination_label=label,
            destination_type=destination_type,
            destination_chat=destination_chat,
            timezone_name=str(self._tz),
        )

    async def create(
        self,
        *,
        actor: Actor,
        preview: ReminderPreview,
        source_chat_id: int | None = None,
        now: datetime | None = None,
    ) -> Reminder:
        """Commit a previewed reminder. **The first point at which one exists.**

        Raises:
            AuthorizationError: The destination is not this actor's to address.
            ValidationError: The actor has no user row, so nothing can own it.
        """
        if actor.user_id is None:
            # "Hồ sơ" in this codebase means the optional descriptive
            # ActorProfile, which a reminder has never needed. Saying it here
            # sent people looking for a setting that was not the problem: what
            # is actually missing is a durable account, and /start creates one.
            raise ValidationError(ACCOUNT_NOT_READY)
        if preview.destination_chat is not None:
            await self._require_may_target(actor, preview.destination_chat)

        moment = now or utcnow()
        row = Reminder(
            owner_user_id=actor.user_id,
            created_by_user_id=actor.user_id,
            source_chat_id=source_chat_id,
            destination_type=preview.destination_type,
            destination_user_id=(
                actor.user_id
                if preview.destination_type is ReminderDestinationType.USER_PRIVATE
                else None
            ),
            destination_chat_id=(
                preview.destination_chat.id if preview.destination_chat is not None else None
            ),
            content=preview.content,
            timezone=str(self._tz),
            schedule_kind=preview.schedule.kind,
            recurrence_rule=preview.schedule.recurrence_rule,
            local_time=preview.schedule.local_time,
            next_run_at=preview.schedule.next_after(moment, tz=self._tz),
            status=ReminderStatus.ACTIVE,
            missed_occurrence_policy=MissedOccurrencePolicy.DELIVER_LATEST,
            version=1,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "reminder_created",
            extra={"reminder_id": str(row.id), "kind": row.schedule_kind.value},
        )
        return row

    # --- Managing ---------------------------------------------------------
    async def list_for(
        self, *, user_id: uuid.UUID, include_inactive: bool = False
    ) -> Sequence[Reminder]:
        """This person's reminders, soonest first."""
        statement = select(Reminder).where(Reminder.owner_user_id == user_id)
        if not include_inactive:
            statement = statement.where(
                Reminder.status.in_([ReminderStatus.ACTIVE, ReminderStatus.PAUSED])
            )
        result = await self._session.execute(
            statement.order_by(Reminder.next_run_at.asc().nulls_last())
        )
        return result.scalars().all()

    async def find(self, *, user_id: uuid.UUID, phrase: str) -> Sequence[Reminder]:
        """Reminders whose content matches a phrase.

        Returns every match rather than the best one. "Tạm dừng lịch nhắc đi
        dạy" when two reminders mention teaching is a question, not a guess -
        pausing the wrong one is silent and only discovered by the right one
        not firing.
        """
        from meobot.domain.member.normalization import strip_accents

        needle = strip_accents(phrase).strip()
        if not needle:
            return ()
        candidates = await self.list_for(user_id=user_id)
        return [row for row in candidates if needle in strip_accents(row.content)]

    async def pause(self, *, actor: Actor, reminder: Reminder) -> Reminder:
        """Stop firing without losing the reminder. Reversible."""
        self._require_owner(actor, reminder)
        reminder.status = ReminderStatus.PAUSED
        reminder.version += 1
        await self._session.flush()
        return reminder

    async def resume(
        self, *, actor: Actor, reminder: Reminder, now: datetime | None = None
    ) -> Reminder:
        """Start firing again, from the next future occurrence.

        The paused period is not replayed: a reminder that was off for a week
        should not deliver seven messages when it comes back.
        """
        self._require_owner(actor, reminder)
        moment = now or utcnow()
        schedule = self._schedule_of(reminder)
        reminder.status = ReminderStatus.ACTIVE
        reminder.next_run_at = schedule.next_after(moment, tz=self._zone_of(reminder))
        reminder.version += 1
        await self._session.flush()
        return reminder

    async def cancel(
        self, *, actor: Actor, reminder: Reminder, now: datetime | None = None
    ) -> Reminder:
        """Stop for good. Never a hard delete - the history stays answerable."""
        self._require_owner(actor, reminder)
        reminder.status = ReminderStatus.CANCELLED
        reminder.cancelled_at = now or utcnow()
        reminder.next_run_at = None
        reminder.version += 1
        await self._session.flush()
        return reminder

    async def reschedule(
        self,
        *,
        actor: Actor,
        reminder: Reminder,
        local_time: time,
        now: datetime | None = None,
    ) -> Reminder:
        """Move a reminder to a different time of day, keeping its recurrence."""
        self._require_owner(actor, reminder)
        moment = now or utcnow()
        current = self._schedule_of(reminder)
        updated = ReminderSchedule(
            kind=current.kind,
            local_time=local_time,
            weekday=current.weekday,
            run_date=current.run_date,
        )
        reminder.local_time = local_time
        reminder.recurrence_rule = updated.recurrence_rule
        reminder.next_run_at = updated.next_after(moment, tz=self._zone_of(reminder))
        reminder.version += 1
        await self._session.flush()
        return reminder

    # --- Firing -----------------------------------------------------------
    async def due_batch(
        self, *, now: datetime | None = None, limit: int | None = None
    ) -> Sequence[Reminder]:
        """Active reminders whose moment has arrived.

        ``FOR UPDATE SKIP LOCKED`` on PostgreSQL so two sweeps do not fight
        over the same rows. SQLite - which the offline tests use - has no such
        clause and serialises writes anyway, so the same code is correct on
        both, and the unique constraint on occurrences is the real guarantee
        either way.
        """
        moment = now or utcnow()
        statement = (
            select(Reminder)
            .where(
                Reminder.status == ReminderStatus.ACTIVE,
                Reminder.next_run_at.is_not(None),
                Reminder.next_run_at <= moment,
            )
            .order_by(Reminder.next_run_at.asc())
            .limit(limit or self._settings.reminder_batch_size)
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def fire(
        self,
        reminder: Reminder,
        *,
        router: NotificationRouter,
        now: datetime | None = None,
    ) -> ReminderOccurrence | None:
        """Produce one occurrence and one outbound message, or skip.

        Both writes happen in the caller's transaction alongside the advance of
        ``next_run_at``, so a reminder cannot fire without producing a message,
        produce a message without recording the firing, or fire twice for the
        same instant.

        Returns:
            The occurrence, or ``None`` when this instant was already handled
            by another sweep.
        """
        moment = now or utcnow()
        scheduled_for = ensure_utc(reminder.next_run_at) if reminder.next_run_at else moment
        zone = self._zone_of(reminder)
        schedule = self._schedule_of(reminder)

        occurrence = await self._claim_occurrence(reminder, scheduled_for, now=moment)
        if occurrence is None:
            # Another sweep got there first. Advancing is still safe and
            # idempotent, and leaving it un-advanced would re-fire forever.
            self._advance(reminder, schedule, zone, after=moment)
            await self._session.flush()
            return None

        # A firing that is too old is recorded and skipped rather than sent:
        # after an outage, "you had a meeting two days ago" is noise, and
        # delivering every missed occurrence turns a restart into a flood.
        grace = timedelta(seconds=self._settings.reminder_missed_grace_seconds)
        if moment - scheduled_for > grace:
            occurrence.status = OccurrenceStatus.SKIPPED
            occurrence.skip_reason = "outside_grace_window"
            logger.info(
                "reminder_occurrence_skipped",
                extra={"reminder_id": str(reminder.id), "scheduled_for": scheduled_for.isoformat()},
            )
        else:
            queued = await self._queue_message(reminder, occurrence, router=router)
            occurrence.status = OccurrenceStatus.QUEUED if queued else OccurrenceStatus.FAILED

        reminder.last_run_at = moment
        self._advance(reminder, schedule, zone, after=moment)
        await self._session.flush()
        return occurrence

    async def _claim_occurrence(
        self, reminder: Reminder, scheduled_for: datetime, *, now: datetime
    ) -> ReminderOccurrence | None:
        """Insert the ``(reminder, instant)`` row, or lose the race gracefully.

        The insert runs inside a **savepoint**. Rolling the whole transaction
        back on a duplicate would be a much worse bug than the duplicate: one
        sweep fires many reminders, and losing a race on the tenth would
        discard the nine that had already been queued.
        """
        existing = await self._session.execute(
            select(ReminderOccurrence).where(
                ReminderOccurrence.reminder_id == reminder.id,
                ReminderOccurrence.scheduled_for == scheduled_for,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        occurrence = ReminderOccurrence(
            reminder_id=reminder.id,
            scheduled_for=scheduled_for,
            status=OccurrenceStatus.SCHEDULED,
            created_at=now,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(occurrence)
                await self._session.flush()
        except IntegrityError:
            # Lost the race against a concurrent sweep. The savepoint undid
            # this insert and nothing else.
            return None
        return occurrence

    async def _queue_message(
        self,
        reminder: Reminder,
        occurrence: ReminderOccurrence,
        *,
        router: NotificationRouter,
    ) -> bool:
        """Write the outbound message for one firing. Never sends it."""
        destination: TelegramChat | None = None
        private_chat_id: int | None = None

        if reminder.destination_type is ReminderDestinationType.REGISTERED_CHAT:
            if reminder.destination_chat_id is None:  # pragma: no cover - defensive
                return False
            destination = await self._session.get(TelegramChat, reminder.destination_chat_id)
            if destination is None:
                return False
            template_key = "reminder.group"
            label = destination.display_name
        else:
            from meobot.application.recipient_resolver import RecipientResolver

            resolution = await RecipientResolver(self._session, self._settings).private_destination(
                user_id=reminder.destination_user_id or reminder.owner_user_id
            )
            if not resolution.is_resolved:
                return False
            private_chat_id = resolution.telegram_chat_id
            template_key = "reminder.personal"
            label = "Chat riêng"

        result = await router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.REMINDER_DUE,
                    template_key=template_key,
                    payload={"content": reminder.content},
                    idempotency_key=reminder_occurrence_key(
                        reminder.id, ensure_utc(occurrence.scheduled_for).isoformat()
                    ),
                    aggregate_type="reminder",
                    aggregate_id=reminder.id,
                    recipient_user_id=reminder.destination_user_id,
                    private_chat_id=private_chat_id,
                    destination=destination,
                    source_chat_id=reminder.source_chat_id,
                    created_by_user_id=reminder.created_by_user_id,
                    destination_label=label,
                    business_summary=f"Lịch nhắc: {reminder.content}"[:300],
                )
            ]
        )
        messages = result.queued or result.duplicates
        if not messages:
            return False
        occurrence.outbox_message_id = messages[0].id
        return True

    def _advance(
        self,
        reminder: Reminder,
        schedule: ReminderSchedule,
        zone: ZoneInfo,
        *,
        after: datetime,
    ) -> None:
        """Move a reminder to its next firing, or retire a one-time one."""
        if schedule.kind is ScheduleKind.ONE_TIME:
            reminder.status = ReminderStatus.COMPLETED
            reminder.next_run_at = None
            return
        reminder.next_run_at = schedule.next_after(after, tz=zone)

    # --- Helpers ----------------------------------------------------------
    def _schedule_of(self, reminder: Reminder) -> ReminderSchedule:
        """Rebuild the stored schedule."""
        return parse_recurrence_rule(reminder.recurrence_rule, local_time=reminder.local_time)

    def _zone_of(self, reminder: Reminder) -> ZoneInfo:
        """The reminder's own timezone, falling back to the configured one."""
        try:
            return ZoneInfo(reminder.timezone)
        except Exception:  # pragma: no cover - a corrupt stored zone
            return self._tz

    @staticmethod
    def _require_owner(actor: Actor, reminder: Reminder) -> None:
        """Only the owner manages their reminder. Not even the Trưởng phòng."""
        if actor.user_id is None or reminder.owner_user_id != actor.user_id:
            raise AuthorizationError(NOT_YOUR_REMINDER)

    async def _require_may_target(self, actor: Actor, chat: TelegramChat) -> None:
        """Whether this actor may aim a reminder at this group.

        A Member may not target a group at all. A Trưởng nhóm may target the
        groups explicitly assigned to them and no others. OWNER and ADMIN may
        target any active registered group.

        Raises:
            AuthorizationError: The actor may not address this destination.
        """
        if actor.role in {Role.OWNER, Role.ADMIN}:
            if not chat.is_active:
                raise ConflictError("Group này hiện không nhận thông báo tự động.")
            return
        if actor.role is Role.TEAM_LEAD:
            if await self._assignments.may_broadcast_to(user_id=actor.user_id, chat=chat):
                return
            raise AuthorizationError(
                f"Bạn chưa được giao quản lý group {chat.display_name}, "
                "nên TasksBot chưa đặt lịch nhắc vào đó được."
            )
        raise AuthorizationError(MEMBER_CANNOT_TARGET_OTHERS)


def not_found_error() -> ConflictError:
    """The error raised when a named reminder does not exist."""
    return ConflictError(NO_SUCH_REMINDER)
