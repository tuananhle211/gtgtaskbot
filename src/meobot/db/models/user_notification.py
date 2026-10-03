"""One person's notification inbox, as read by the web panel.

Step 1F.2.3d. One table, ``user_notifications``.

Why this is not ``outbound_messages``
-------------------------------------

The obvious place to read a notification centre from is the outbox, and it was
the first thing tried. It does not work, for two reasons that are both about
what that table *is*:

* ``outbound_messages.telegram_chat_id`` is ``NOT NULL``, and
  :class:`~meobot.application.pr_notifications.PrNotificationService` writes **no
  row at all** when the recipient has no reachable private chat. So an outbox-backed
  bell would be permanently empty for exactly the people the web panel exists to
  serve - somebody who has never opened the bot. "Users should not need Telegram
  to discover workflow events" cannot be built on a table that only records the
  events Telegram could carry;
* it is a **delivery** table. Its rows are claimed, retried, backed off and
  settled by a worker; they carry attempt counts and failure categories, and they
  include rows addressed to *groups*. A ``read_at`` column on it would be a UI
  concern bolted to a transport queue, and "unread" would have quietly meant
  "unread, if we managed to send it".

So this is a second **table**, not a second notification *system*. It is written
by the same service, in the same transaction, from the same event - see
:meth:`~meobot.application.pr_notifications.PrNotificationService._deliver`. The
outbox keeps owning delivery and the router keeps owning routing; this owns *what
one person has been told and whether they have looked at it*. Neither suppresses
the other: marking a notification read here does not touch a queued Telegram
message, and a Telegram message that fails to send does not mark anything unread.

Why the text is stored rather than rendered on read
----------------------------------------------------

``outbound_messages`` stores a ``template_key`` and a payload, and renders at
send time. This stores ``title`` and ``body`` as finished Vietnamese.

That is deliberate duplication, and the reason is that the two channels are not
saying the same thing in the same shape. The Telegram templates are built for a
chat window - an emoji heading, several lines, a bare URL at the end - and a bell
dropdown needs a short title and one line of body, with the link as a target
rather than as text. Rendering the Telegram string into a dropdown would put
``https://…/pr/content/8f3e…`` in front of somebody as *content*, which is the
one thing a deep link should never be.

Storing the words also makes the record honest about history: a notification says
what it said on the day it was written, even after somebody edits a template or
renames a stage.

Read state, and who owns it
----------------------------

``read_at`` is nullable and set once. Unread is ``read_at IS NULL`` rather than a
boolean, because "when did they see this" is worth keeping and costs the same
column.

Ownership is ``recipient_user_id`` and it is enforced in **every** query, never
inferred from the id in a URL - see
:class:`~meobot.application.user_notification_service.UserNotificationService`.
``ON DELETE CASCADE``: a deleted user's inbox has no other owner and nothing else
points at these rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserNotification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One thing one person has been told, and whether they have read it."""

    __tablename__ = "user_notifications"
    __table_args__ = (
        # One business event produces one inbox row, however many times the
        # workflow that raised it is retried. The same key the outbox uses, so a
        # duplicate is a duplicate in both places or in neither.
        UniqueConstraint("idempotency_key", name="uq_user_notifications_idempotency_key"),
        # The panel's list: this person's notifications, newest first.
        Index("ix_user_notifications_recipient_created", "recipient_user_id", "created_at"),
        # The badge: ``count(*) WHERE recipient_user_id = ? AND read_at IS NULL``.
        # Its own index rather than leaning on the one above, because the count
        # runs on **every page load** and this makes it an index-only scan
        # instead of a walk over the person's whole history.
        Index("ix_user_notifications_recipient_unread", "recipient_user_id", "read_at"),
    )

    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: A :class:`~meobot.domain.notifications.models.NotificationEvent` value.
    #: A plain string for the reason ``outbound_messages.event_type`` is one: the
    #: vocabulary belongs to the domain enum, and a column that constrained it
    #: would need a migration every time an event is added.
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    #: The bold line. Short enough to read at a glance in a dropdown.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: One or two sentences under it. ``Text`` rather than a bounded string: a
    #: content title is user-supplied and already 300 characters wide, and
    #: truncating for display is the panel's decision rather than the schema's.
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: What this is about - ``"pr_content"`` today. With ``target_id``, this is
    #: the deep link, kept as two structured columns rather than a stored URL so
    #: the panel decides its own routes and an old row still resolves after a
    #: path changes.
    target_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    #: ``NULL`` until the person opens it. Set once; nothing marks a row unread
    #: again, because "I want to look at this later" is not what a bell is for.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)


__all__: list[str] = ["UserNotification"]
