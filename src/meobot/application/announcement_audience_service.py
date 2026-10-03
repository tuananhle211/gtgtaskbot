"""Who was expected to read an announcement, and who has.

"Ai chưa đọc?" needs a denominator, and getting the denominator wrong is worse
than not having one. Two ways to get it wrong, both avoided here:

**Computing it at question time.** The set of active users changes. Somebody
hired after Tuesday's announcement has not failed to read it, and somebody who
left should not appear forever on a list of people to chase. So the audience is
**snapshotted when the announcement is published** and never recomputed.

**Inventing it.** Telegram will not enumerate a group's members for a bot.
There is no API for it, and there is no way to derive it. For a group with no
configured audience MeoBot therefore reports the count of confirmations and
says plainly that it cannot name who is missing - which is the true answer, and
a great deal more useful than a confident wrong list.

The three audience rules:

* **department announcement** - every active registered user;
* **team announcement** - the users explicitly assigned to that destination
  (see :mod:`~meobot.application.chat_assignment_service`);
* **anything else** - no snapshot, and MeoBot says so.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.notifications import (
    Announcement,
    AnnouncementAcknowledgement,
    AnnouncementRecipient,
    TelegramChat,
)
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.notifications.models import ChatPurpose

logger = get_logger(__name__)

#: Said verbatim when a group has no configured expected audience. Naming the
#: reason matters: "MeoBot cannot tell" and "everybody has read it" look the
#: same to somebody who is not told which one they are looking at.
UNCONFIGURED_AUDIENCE = (
    "Thông báo này đã có {count} người xác nhận đã đọc.\n"
    "Group chưa được cấu hình danh sách thành viên kỳ vọng nên MeoBot chưa thể "
    "xác định ai chưa đọc."
)


@dataclass(slots=True)
class ReadReport:
    """Who has and has not confirmed one announcement."""

    acknowledged: list[str] = field(default_factory=list)
    outstanding: list[str] = field(default_factory=list)
    #: False when no audience was snapshotted, so ``outstanding`` means nothing.
    audience_known: bool = True

    @property
    def acknowledged_count(self) -> int:
        return len(self.acknowledged)

    def render(self) -> str:
        """The Vietnamese answer to "ai chưa đọc?"."""
        if not self.audience_known:
            return UNCONFIGURED_AUDIENCE.format(count=self.acknowledged_count)
        total = self.acknowledged_count + len(self.outstanding)
        lines = [f"Đã đọc: {self.acknowledged_count}/{total} người."]
        if self.outstanding:
            lines.append("")
            lines.append("Chưa xác nhận:")
            lines.extend(f"• {name}" for name in self.outstanding)
        else:
            lines.append("Tất cả mọi người đã xác nhận.")
        return "\n".join(lines)


class AnnouncementAudienceService:
    """Snapshots expected recipients and answers read-receipt questions.

    Args:
        session: Unit of work. :meth:`snapshot` must share the publishing
            transaction, so the audience and the announcement commit together.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._assignments = ChatAssignmentService(session)

    async def snapshot(
        self,
        *,
        announcement: Announcement,
        destination: TelegramChat,
        now: datetime | None = None,
    ) -> int:
        """Record who was expected to read this, at publication time.

        Idempotent: publishing is already idempotent, and re-snapshotting would
        quietly change a denominator somebody has already been shown.

        Returns:
            How many people were recorded. Zero means MeoBot has no configured
            audience for this destination and will say so rather than guess.
        """
        existing = await self.expected(announcement.id)
        if existing:
            return len(existing)

        moment = now or utcnow()
        audience = await self._audience_for(destination)
        for user in audience:
            self._session.add(
                AnnouncementRecipient(
                    announcement_id=announcement.id,
                    user_id=user.id,
                    telegram_user_id=user.telegram_user_id,
                    display_name=user.full_name[:300] if user.full_name else None,
                    expected_at_publish_time=moment,
                    created_at=moment,
                )
            )
        await self._session.flush()
        logger.info(
            "announcement_audience_snapshotted",
            extra={"announcement_id": str(announcement.id), "expected": len(audience)},
        )
        return len(audience)

    async def _audience_for(self, destination: TelegramChat) -> Sequence[User]:
        """The expected readers of one destination, by its configured purpose."""
        if destination.purpose is ChatPurpose.DEPARTMENT_ANNOUNCEMENTS:
            result = await self._session.execute(
                select(User)
                .where(User.status == UserStatus.ACTIVE, User.active.is_(True))
                .order_by(User.full_name.asc())
            )
            return result.scalars().all()
        # A team group: only the people explicitly assigned to it. An empty
        # result is a real answer - it means nobody configured the audience.
        return await self._assignments.audience_of(chat_row_id=destination.id)

    async def expected(self, announcement_id: uuid.UUID) -> Sequence[AnnouncementRecipient]:
        """The snapshotted audience for one announcement."""
        result = await self._session.execute(
            select(AnnouncementRecipient)
            .where(AnnouncementRecipient.announcement_id == announcement_id)
            .order_by(AnnouncementRecipient.display_name.asc())
        )
        return result.scalars().all()

    async def mark_acknowledged(
        self, *, announcement_id: uuid.UUID, telegram_user_id: int, now: datetime | None = None
    ) -> None:
        """Stamp the snapshot row for whoever just pressed "Đã đọc".

        Separate from the acknowledgement row itself: the acknowledgement is
        the fact, this is the snapshot's view of it, and somebody who was not
        in the expected audience can still confirm - they simply do not change
        a denominator they were never part of.
        """
        result = await self._session.execute(
            select(AnnouncementRecipient).where(
                AnnouncementRecipient.announcement_id == announcement_id,
                AnnouncementRecipient.telegram_user_id == telegram_user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None and row.acknowledged_at is None:
            row.acknowledged_at = now or utcnow()
            await self._session.flush()

    async def report(self, announcement_id: uuid.UUID) -> ReadReport:
        """Who has confirmed, and who has not."""
        acknowledgements = await self._session.execute(
            select(AnnouncementAcknowledgement).where(
                AnnouncementAcknowledgement.announcement_id == announcement_id
            )
        )
        confirmed = list(acknowledgements.scalars().all())
        expected = await self.expected(announcement_id)

        if not expected:
            return ReadReport(
                acknowledged=[row.display_name or "Một thành viên" for row in confirmed],
                audience_known=False,
            )

        confirmed_ids = {row.telegram_user_id for row in confirmed}
        report = ReadReport()
        for row in expected:
            name = row.display_name or "Một thành viên"
            if row.telegram_user_id in confirmed_ids or row.acknowledged_at is not None:
                report.acknowledged.append(name)
            else:
                report.outstanding.append(name)
        return report

    async def unread_recipients(
        self, announcement_id: uuid.UUID, *, limit: int
    ) -> Sequence[AnnouncementRecipient]:
        """People to remind, bounded.

        Bounded because a reminder that goes to eighty people by accident
        cannot be recalled. The caller reports what was left out.
        """
        expected = await self.expected(announcement_id)
        outstanding = [row for row in expected if row.acknowledged_at is None]
        return outstanding[:limit]
