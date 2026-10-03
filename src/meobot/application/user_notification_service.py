"""One person's inbox: writing to it, reading it, and marking it read.

Step 1F.2.3d. The whole of what the web notification centre talks to, and the
only module that touches
:class:`~meobot.db.models.user_notification.UserNotification`.

Ownership is the invariant
---------------------------

Every method here takes the acting user's id and puts it in the ``WHERE``
clause. Not as a check after loading a row - **in the query**, so a notification
belonging to somebody else is not "found and refused", it is simply not found.

That distinction is the security property. A service that loaded by id and then
compared owners would leak existence through timing and through the difference
between 403 and 404, and would be one refactor away from leaking the row. Here,
``mark_read`` with a stranger's uuid updates zero rows and reports that the
notification does not exist, which is both true from the caller's point of view
and the only thing they are entitled to learn.

``mark_all_read`` is an ``UPDATE`` scoped the same way. There is no "all users"
path, no role that widens it and no administrative override: a bell is personal,
and nothing about it should be able to touch another person's state.

Transactions
------------

Like every service in this layer: writes ``flush``, never ``commit``. The inbox
row for a workflow event is written in the **same transaction as the workflow
change**, so a Head approval and the notice about it either both exist or
neither does - the guarantee ``outbound_messages`` already gives Telegram,
extended to the panel.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.user_notification import UserNotification
from meobot.domain.notifications.models import NotificationEvent

logger = get_logger(__name__)

#: What the panel loads behind the bell. Small on purpose: a dropdown is for
#: "what happened recently", and somebody looking for a decision from three
#: weeks ago is looking for the content, not for the notification about it.
DEFAULT_LIMIT = 20

#: The ceiling on ``limit``. Stops a caller asking for the whole history in one
#: request, which is the shape that turns a bell into a table scan.
MAX_LIMIT = 50


@dataclass(frozen=True, slots=True)
class NotificationPage:
    """One page of somebody's inbox, and the count the badge needs.

    The two travel together because the panel needs both on every open and
    asking twice would be two round trips for one screen.
    """

    items: tuple[UserNotification, ...]
    #: Unread across the person's **whole** inbox, not just this page. A badge
    #: reading "3" when there are nine unread further down would be worse than
    #: no badge.
    unread_count: int
    #: Whether older notifications exist beyond this page.
    has_more: bool


class UserNotificationService:
    """Reads and writes one user's notifications, and nobody else's.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Writing ----------------------------------------------------------
    async def record(
        self,
        *,
        recipient_user_id: uuid.UUID,
        event: NotificationEvent,
        title: str,
        body: str,
        idempotency_key: str,
        target_kind: str | None = None,
        target_id: uuid.UUID | None = None,
    ) -> UserNotification | None:
        """Put one notification in one person's inbox.

        Idempotent on ``idempotency_key``, using the same
        ``begin_nested``/``IntegrityError`` shape
        :class:`~meobot.application.outbox_service.OutboxService` uses: a
        collision must not roll back the workflow change this is running inside,
        so the insert gets its own ``SAVEPOINT`` and a duplicate is read back
        rather than raised.

        Returns:
            The row, or the existing one if this event was already recorded.
            ``None`` never happens through this path and is not returned; the
            optional type is there because
            :meth:`~UserNotificationService.record` is called from notification
            code that tolerates "nothing was written".
        """
        row = UserNotification(
            recipient_user_id=recipient_user_id,
            event_type=event.value,
            title=title,
            body=body,
            target_kind=target_kind,
            target_id=target_id,
            idempotency_key=idempotency_key,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.execute(
                select(UserNotification).where(UserNotification.idempotency_key == idempotency_key)
            )
            return existing.scalars().first()
        return row

    # --- Reading ----------------------------------------------------------
    async def page(
        self, *, user_id: uuid.UUID, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> NotificationPage:
        """This person's notifications, newest first, with the unread count.

        ``limit`` is clamped to :data:`MAX_LIMIT` rather than rejected: a client
        asking for too many wants as many as it can have, and a 422 for a number
        the server is willing to reduce is a refusal that helps nobody.

        One extra row is fetched to answer ``has_more`` without a second
        ``COUNT`` over the whole inbox - the count that matters for the badge is
        the unread one, and it has an index of its own.
        """
        capped = max(1, min(limit, MAX_LIMIT))
        rows = await self._session.execute(
            select(UserNotification)
            .where(UserNotification.recipient_user_id == user_id)
            # ``id`` last so two notifications written in the same transaction -
            # the two recipients of one internal-review approval - come back in
            # a stable order rather than whichever the database happens to pick.
            .order_by(UserNotification.created_at.desc(), UserNotification.id.desc())
            .limit(capped + 1)
            .offset(max(0, offset))
        )
        found = list(rows.scalars().all())
        return NotificationPage(
            items=tuple(found[:capped]),
            unread_count=await self.unread_count(user_id=user_id),
            has_more=len(found) > capped,
        )

    async def get(
        self, *, user_id: uuid.UUID, notification_id: uuid.UUID
    ) -> UserNotification | None:
        """One notification, **if it is this person's**.

        The owner is a condition of the lookup, not a check after it, so a
        stranger's id and an id that never existed both return ``None`` - which
        is what lets the route answer 404 to each without deciding which it was.
        """
        found = await self._session.execute(
            select(UserNotification).where(
                UserNotification.id == notification_id,
                UserNotification.recipient_user_id == user_id,
            )
        )
        return found.scalars().first()

    async def unread_count(self, *, user_id: uuid.UUID) -> int:
        """How many of this person's notifications are unread.

        A ``COUNT`` against ``ix_user_notifications_recipient_unread``, not a
        fetch-and-len: the badge is rendered on every page load, and loading
        rows to measure them is the shape that stops working first.
        """
        total = await self._session.scalar(
            select(func.count())
            .select_from(UserNotification)
            .where(
                UserNotification.recipient_user_id == user_id,
                UserNotification.read_at.is_(None),
            )
        )
        return total or 0

    # --- Read state -------------------------------------------------------
    async def mark_read(self, *, user_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
        """Mark one notification read. Returns whether it was this person's.

        The owner is in the ``WHERE``, so somebody else's id updates nothing and
        returns ``False`` - indistinguishable, from the caller's side, from an id
        that does not exist. That is the intended answer to both.

        ``read_at IS NULL`` is also in the ``WHERE``, so opening an already-read
        notification does not move its timestamp: *when did I first see this* is
        the useful fact, and re-stamping it on every click would destroy it. A
        second call therefore returns ``False``, which callers treat as "nothing
        to do" rather than as failure - see the route.
        """
        result = await self._session.execute(
            update(UserNotification)
            .where(
                UserNotification.id == notification_id,
                UserNotification.recipient_user_id == user_id,
                UserNotification.read_at.is_(None),
            )
            .values(read_at=utcnow())
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def mark_all_read(self, *, user_id: uuid.UUID) -> int:
        """Mark every unread notification of **this** user read.

        One ``UPDATE``, scoped to the caller. There is deliberately no variant
        that takes another user's id: nothing in the product needs to clear
        somebody else's bell, and a method that could would be one authorization
        bug away from doing it.
        """
        result = await self._session.execute(
            update(UserNotification)
            .where(
                UserNotification.recipient_user_id == user_id,
                UserNotification.read_at.is_(None),
            )
            .values(read_at=utcnow())
        )
        marked = int(result.rowcount or 0)  # type: ignore[attr-defined]
        logger.info("user_notifications_marked_read", extra={"count": marked})
        return marked


__all__: list[str] = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "NotificationPage",
    "UserNotificationService",
]
