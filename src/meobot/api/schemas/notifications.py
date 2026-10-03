"""Request and response bodies for the web notification centre.

Step 1F.2.3d. Hand-written Pydantic models, like every other schema module here,
and for the same reason: **no ORM row is ever serialised directly.**

What a row deliberately does not carry
---------------------------------------

There is no ``recipient_user_id`` on the way out. Every notification in a
response belongs to the person who asked for it - the service put that in the
``WHERE`` clause - so echoing the id back would be a field that is either
redundant or a bug, and no client has a use for it.

``target_id`` **is** a uuid on the wire, because it is a target rather than
text: the panel turns it into ``/pr/content/<id>`` and never renders it. The
words a person reads are ``title`` and ``body``, and neither has ever contained
an identifier - see :mod:`meobot.domain.notifications.web`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meobot.application.user_notification_service import NotificationPage
from meobot.db.models.user_notification import UserNotification


class NotificationResponse(BaseModel):
    """One notification, as a panel row."""

    id: uuid.UUID
    #: A ``NotificationEvent`` value. Sent so a client may group or icon by
    #: category; it is never what a person reads.
    event_type: str
    title: str
    body: str
    #: ``"pr_content"``, or ``null`` for a notification about nothing linkable.
    target_kind: str | None
    target_id: uuid.UUID | None
    #: ``null`` while unread. A timestamp rather than a boolean, for the reason
    #: the column is one: when somebody saw a thing is worth keeping.
    read_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: UserNotification) -> NotificationResponse:
        return cls(
            id=row.id,
            event_type=row.event_type,
            title=row.title,
            body=row.body,
            target_kind=row.target_kind,
            target_id=row.target_id,
            read_at=row.read_at,
            created_at=row.created_at,
        )


class NotificationListResponse(BaseModel):
    """One page of the inbox, and the badge count.

    Both in one response because the panel needs both the moment the bell opens,
    and two requests for one screen is a round trip nobody asked for.
    """

    items: list[NotificationResponse]
    #: Unread across the whole inbox, not just this page.
    unread_count: int
    has_more: bool

    @classmethod
    def from_page(cls, page: NotificationPage) -> NotificationListResponse:
        return cls(
            items=[NotificationResponse.from_row(row) for row in page.items],
            unread_count=page.unread_count,
            has_more=page.has_more,
        )


class UnreadCountResponse(BaseModel):
    """Just the badge.

    Its own endpoint so the shell can keep the count fresh without fetching
    twenty rows to count them - the thing
    :class:`~meobot.application.user_notification_service.UserNotificationService`
    exists to avoid.
    """

    unread_count: int = Field(ge=0)


class MarkAllReadResponse(BaseModel):
    """What ``mark all read`` did.

    Returns the resulting count rather than nothing, so the client's badge comes
    from the server's answer instead of from an assumption that the call worked.
    """

    marked: int
    unread_count: int = Field(ge=0)


__all__: list[str] = [
    "MarkAllReadResponse",
    "NotificationListResponse",
    "NotificationResponse",
    "UnreadCountResponse",
]
