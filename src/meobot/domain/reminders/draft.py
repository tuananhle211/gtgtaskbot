"""A reminder being built across several turns.

The bug this replaces: every follow-up message was fed back through
:func:`~meobot.domain.reminders.parsing.parse_reminder` as though it were a
complete new sentence. So "22h40", sent to fill in a missing time, was parsed as
a whole reminder - producing no content, discarding "đi ngủ", and answering with
the same generic error again. The user could not get out of the loop by
answering the question they had been asked.

A draft therefore keeps its fields **separately** and merges into them. Knowing
*which* field is missing is the whole point: when only the time is absent, a
bare "22h40" fills the time and nothing else; when only the content is absent,
"Đi ngủ" fills the content and leaves the time alone.

It is a plain frozen dataclass with a dict round-trip because it lives in the
PostgreSQL-backed aiogram FSM store, which holds JSON. Being durable is what
lets a draft survive ``/start`` - and surviving ``/start`` is what makes the
onboarding fix seamless rather than an apology.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from meobot.core.time import ensure_utc, utcnow
from meobot.domain.reminders.models import ScheduleKind
from meobot.domain.reminders.schedule import ReminderSchedule

#: The FSM key this is stored under.
DRAFT_KEY = "reminder_draft"

#: What a draft can still be waiting for. Used to pick the right question and,
#: more importantly, to decide what a bare reply is allowed to overwrite.
MISSING_CONTENT = "content"
MISSING_TIME = "time"


@dataclass(frozen=True, slots=True)
class ReminderDraftState:
    """One half-built reminder. **Never** a reminder.

    Kept structurally distinct from
    :class:`~meobot.db.models.reminder.Reminder` so no code path can describe
    one as the other - a draft existing is not a reason to say "đã lưu".
    """

    content: str = ""
    #: Set once a time is known. ``None`` means the draft is still waiting.
    local_time: time | None = None
    schedule_kind: ScheduleKind = ScheduleKind.ONE_TIME
    weekday: int | None = None
    run_date: date | None = None
    #: Minutes from "now" for a relative reminder. Kept alongside the resolved
    #: clock so the preview can say "Sau 5 phút" rather than only "22:40" -
    #: which is what the person actually asked for.
    relative_minutes: int | None = None
    timezone_name: str = "Asia/Ho_Chi_Minh"
    #: Exactly what the person typed, after redaction. Audited, never re-parsed.
    original_text: str = ""
    source_chat_id: int | None = None
    initiator_telegram_id: int | None = None
    version: int = 1
    created_at: datetime | None = None
    #: True while the draft is parked waiting for the account to be completed.
    awaiting_onboarding: bool = False

    # --- State ------------------------------------------------------------
    @property
    def missing(self) -> tuple[str, ...]:
        """Which fields still have to be supplied, in the order they are asked."""
        gaps: list[str] = []
        if not self.content.strip():
            gaps.append(MISSING_CONTENT)
        if self.local_time is None:
            gaps.append(MISSING_TIME)
        return tuple(gaps)

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def is_expired(self, ttl_seconds: int, *, now: datetime | None = None) -> bool:
        """True once a half-finished draft is too old to still mean anything.

        A forgotten draft that silently captures tomorrow's message is worse
        than one that expires and says so.
        """
        if self.created_at is None:
            return False
        moment = now or utcnow()
        return ensure_utc(self.created_at) + timedelta(seconds=ttl_seconds) < moment

    # --- Merging ----------------------------------------------------------
    def with_content(self, content: str) -> ReminderDraftState:
        """Fill only the content."""
        return replace(self, content=content.strip(), version=self.version + 1)

    def with_schedule(
        self,
        schedule: ReminderSchedule,
        *,
        relative_minutes: int | None = None,
    ) -> ReminderDraftState:
        """Fill only the timing, leaving the content untouched."""
        return replace(
            self,
            local_time=schedule.local_time,
            schedule_kind=schedule.kind,
            weekday=schedule.weekday,
            run_date=schedule.run_date,
            relative_minutes=relative_minutes,
            version=self.version + 1,
        )

    def cleared_of_onboarding(self) -> ReminderDraftState:
        return replace(self, awaiting_onboarding=False, version=self.version + 1)

    # --- Output -----------------------------------------------------------
    def to_schedule(self, *, now: datetime, tz: ZoneInfo) -> ReminderSchedule:
        """The schedule this draft describes.

        Raises:
            ValueError: The draft is not complete. Callers check
                :attr:`is_complete` first; this is the backstop that keeps an
                incomplete draft from becoming a reminder at some wrong hour.
        """
        if self.local_time is None:
            raise ValueError("draft has no time yet")

        if self.schedule_kind is ScheduleKind.WEEKLY:
            return ReminderSchedule(
                kind=ScheduleKind.WEEKLY, local_time=self.local_time, weekday=self.weekday
            )
        if self.schedule_kind is ScheduleKind.DAILY:
            return ReminderSchedule(kind=ScheduleKind.DAILY, local_time=self.local_time)

        day = self.run_date
        if day is None:
            # A bare clock with no date: today if it is still ahead, else
            # tomorrow. Never a time that has already passed.
            local_now = now.astimezone(tz)
            day = local_now.date()
            if self.local_time <= local_now.time():
                day = day + timedelta(days=1)
        return ReminderSchedule(
            kind=ScheduleKind.ONE_TIME, local_time=self.local_time, run_date=day
        )

    def describe_timing(self) -> str:
        """The timing in the words the person used.

        Somebody who said "sau 5 phút" is shown "Sau 5 phút", not "22:40 ngày
        30/07/2026" - a preview they cannot recognise is a preview they cannot
        check.
        """
        from meobot.domain.reminders.schedule import describe

        if self.relative_minutes is not None:
            if self.relative_minutes % 60 == 0 and self.relative_minutes >= 60:
                return f"Sau {self.relative_minutes // 60} tiếng"
            return f"Sau {self.relative_minutes} phút"
        if self.local_time is None:
            return "chưa rõ"
        # A one-time schedule needs a date to be constructible; when the draft
        # has only a clock so far, "today" is the placeholder the preview will
        # replace once ``to_schedule`` resolves the real day.
        placeholder = self.run_date or utcnow().astimezone(ZoneInfo(self.timezone_name)).date()
        return describe(
            ReminderSchedule(
                kind=self.schedule_kind,
                local_time=self.local_time,
                weekday=self.weekday,
                run_date=placeholder,
            )
        )

    # --- Persistence ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, for the FSM store."""
        return {
            "content": self.content,
            "local_time": self.local_time.isoformat() if self.local_time else None,
            "schedule_kind": self.schedule_kind.value,
            "weekday": self.weekday,
            "run_date": self.run_date.isoformat() if self.run_date else None,
            "relative_minutes": self.relative_minutes,
            "timezone_name": self.timezone_name,
            "original_text": self.original_text,
            "source_chat_id": self.source_chat_id,
            "initiator_telegram_id": self.initiator_telegram_id,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "awaiting_onboarding": self.awaiting_onboarding,
        }

    @classmethod
    def from_dict(cls, stored: dict[str, Any]) -> ReminderDraftState | None:
        """Rebuild from FSM data. ``None`` for anything unreadable.

        A corrupt draft is discarded rather than guessed at: continuing from
        half-understood state is how a reminder ends up at the wrong hour.
        """
        try:
            clock = stored.get("local_time")
            day = stored.get("run_date")
            created = stored.get("created_at")
            return cls(
                content=str(stored.get("content") or ""),
                local_time=time.fromisoformat(str(clock)) if clock else None,
                schedule_kind=ScheduleKind(str(stored.get("schedule_kind") or "ONE_TIME")),
                weekday=stored.get("weekday"),
                run_date=date.fromisoformat(str(day)) if day else None,
                relative_minutes=stored.get("relative_minutes"),
                timezone_name=str(stored.get("timezone_name") or "Asia/Ho_Chi_Minh"),
                original_text=str(stored.get("original_text") or ""),
                source_chat_id=stored.get("source_chat_id"),
                initiator_telegram_id=stored.get("initiator_telegram_id"),
                version=int(stored.get("version") or 1),
                created_at=datetime.fromisoformat(str(created)) if created else None,
                awaiting_onboarding=bool(stored.get("awaiting_onboarding")),
            )
        except (TypeError, ValueError):
            return None
