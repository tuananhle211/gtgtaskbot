"""One announcement, several groups: the draft, the decision and the outcomes.

**Why a draft is a table and not FSM state.** 0.6.0a2 kept the announcement
being composed in the FSM store, keyed by (bot, chat, user), holding one id.
That was enough for one destination and nothing else: a *set* of chosen
recipients, the phrases that had not resolved yet, the ones excluded, and the
content itself had nowhere to live. So "Tất cả" had nothing to expand into and
"Xác nhận" had nothing to confirm - which is exactly what the two reported
conversations look like from the inside.

``message_dispatch_drafts`` is that missing home. It survives a restart, it is
versioned so a stale button cannot act on a changed selection, and it expires
so a card from last week cannot send today.

**Why the dispatch is a separate aggregate.** A confirmation is one decision -
one audit event, one thing to be idempotent about - and it produces N
independent outcomes. Collapsing the two would mean either N confirmations or
one status for twelve groups, and neither is true. So ``message_dispatches``
holds the decision, ``message_dispatch_recipients`` holds one row per group,
and a group that refuses the bot fails on its own row while the other eleven
succeed on theirs.

**Why parts exist.** A long announcement becomes several Telegram messages, and
"delivered" for a destination means *all* of them arrived. A recipient that got
part 1 and not part 2 is ``PARTIAL_FAILURE``, and retrying it must send part 2
only - which needs a row per (recipient, part) to be answerable at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.dispatch.models import (
    DispatchPartStatus,
    DispatchRecipientStatus,
    DispatchStatus,
    DraftStatus,
    SelectionSource,
)
from meobot.domain.notifications.models import FailureCategory, PrivacyClassification


class MessageDispatchDraft(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An announcement somebody is still choosing recipients for.

    At most one is open per (bot, source chat, person) - enforced by the
    application rather than by a partial unique index, because "open" is two
    statuses and expiry makes it a moving target. Opening a second one closes
    the first, so "Xác nhận" is never ambiguous about which draft it means.
    """

    __tablename__ = "message_dispatch_drafts"
    __table_args__ = (
        Index(
            "ix_message_dispatch_drafts_open",
            "bot_identity",
            "source_chat_id",
            "created_by_telegram_id",
            "status",
        ),
    )

    bot_identity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: The bootstrap owner has no ``users`` row, and still has to be able to
    #: continue their own draft. This is what identifies them.
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Where it is being written. A draft opened in one chat is never
    #: continuable from another, which is what stops one person's "Xác nhận" in
    #: a group confirming something they started privately.
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Exactly what the person typed, kept so nothing is ever re-derived.
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: The announcement body, with the addressing stripped. This is what the
    #: groups will read, and what the preview shows.
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    privacy_classification: Mapped[PrivacyClassification] = mapped_column(
        value_enum(PrivacyClassification, name="privacy_classification", length=30),
        nullable=False,
        default=PrivacyClassification.PUBLIC_OPERATIONAL,
    )
    #: Recipient phrases that matched nothing. Kept rather than discarded so the
    #: preview can say which words MeoBot could not place instead of quietly
    #: sending to a shorter list.
    unresolved_phrases: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[DraftStatus] = mapped_column(
        value_enum(DraftStatus, name="dispatch_draft_status", length=20),
        nullable=False,
        default=DraftStatus.CHOOSING,
        index=True,
    )
    #: Bumped on every selection change, and signed into the confirm button. A
    #: preview drawn for three groups cannot confirm a draft that now has five.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set when a confirmation turned this into a dispatch, so a second
    #: confirmation returns the same one instead of creating a second.
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class MessageDispatchDraftRecipient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One candidate destination on one draft, selected or not.

    Every candidate gets a row, including the ones currently unticked: the
    multi-select keyboard has to render both states, and "bỏ Test" has to have
    something to untick rather than something to delete.
    """

    __tablename__ = "message_dispatch_draft_recipients"
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "recipient_chat_row_id",
            name="uq_dispatch_draft_recipients_draft_chat",
        ),
        Index("ix_dispatch_draft_recipients_draft", "draft_id", "position"),
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_dispatch_drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_chat_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="CASCADE"), nullable=False
    )
    #: Where this group sat on the card the person was shown. "Hai group đầu"
    #: counts off this, never off a database ordering that could change.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Resolved once, at candidate time, so the preview and the delivery report
    #: call the group the same thing even if it is renamed in between.
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    selection_source: Mapped[SelectionSource] = mapped_column(
        value_enum(SelectionSource, name="dispatch_selection_source", length=20),
        nullable=False,
        default=SelectionSource.NAMED,
    )
    #: False when the sender may not use this destination. Kept visible rather
    #: than hidden, so the preview can say why a named group is not on the list.
    permitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MessageDispatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One confirmed announcement, addressed to one or more groups.

    The counters are maintained by the worker as destinations settle, so
    "2/3 đã gửi" is a read rather than an aggregate query over every recipient
    row - and so the summary can be sent the moment the last one settles rather
    than by a sweep that polls.
    """

    __tablename__ = "message_dispatches"
    # Distinct from the single-column index ``status`` declares below: naming
    # both the same is a collision SQLite reports and PostgreSQL would too.
    __table_args__ = (Index("ix_message_dispatches_status_created", "status", "created_at"),)

    bot_identity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    privacy_classification: Mapped[PrivacyClassification] = mapped_column(
        value_enum(PrivacyClassification, name="privacy_classification", length=30),
        nullable=False,
        default=PrivacyClassification.PUBLIC_OPERATIONAL,
    )

    status: Mapped[DispatchStatus] = mapped_column(
        value_enum(DispatchStatus, name="dispatch_status", length=20),
        nullable=False,
        default=DispatchStatus.QUEUED,
        index=True,
    )
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retrying_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_parts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Incremented by a retry, and part of every idempotency key, so retrying a
    #: failed group produces a new outbox row rather than colliding with the
    #: one that failed.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set once the sender has been told how it went, so a worker that settles
    #: the last destination twice does not report twice.
    summary_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageDispatchPart(Base, UUIDPrimaryKeyMixin):
    """One ordered piece of one long announcement.

    Stored rather than re-split at delivery time. Re-splitting would mean a
    worker running a later version of the splitter could send different text
    from the one the sender confirmed, and "what was confirmed is what is sent"
    is the whole reason there is a preview.
    """

    __tablename__ = "message_dispatch_parts"
    __table_args__ = (
        UniqueConstraint("dispatch_id", "part_number", name="uq_dispatch_parts_dispatch_number"),
    )

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_dispatches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    total_parts: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Written explicitly by the confirming transaction, so every part of one
    #: announcement carries the same instant. The server default is belt and
    #: braces: a ``NOT NULL`` column whose value is *usually* supplied is the
    #: exact shape of the 0.6.0a3 defect, where the model expected a default
    #: the migrated schema did not have. See migration ``0011``.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MessageDispatchRecipient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One group's own outcome for one dispatch.

    Snapshotted at confirmation time: ``destination_display_name`` and
    ``telegram_chat_id`` are copied here rather than read through the
    registration, so a group renamed or de-registered afterwards still reports
    under the name the sender confirmed.
    """

    __tablename__ = "message_dispatch_recipients"
    __table_args__ = (
        UniqueConstraint(
            "dispatch_id",
            "telegram_chat_row_id",
            name="uq_dispatch_recipients_dispatch_chat",
        ),
        Index("ix_dispatch_recipients_status", "dispatch_id", "status"),
    )

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_dispatches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_chat_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="CASCADE"), nullable=False
    )
    #: The numeric destination, resolved once. A destination that moves later
    #: does not silently redirect something somebody already confirmed.
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[DispatchRecipientStatus] = mapped_column(
        value_enum(DispatchRecipientStatus, name="dispatch_recipient_status", length=20),
        nullable=False,
        default=DispatchRecipientStatus.QUEUED,
    )
    delivered_parts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_parts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_category: Mapped[FailureCategory] = mapped_column(
        value_enum(FailureCategory, name="failure_category", length=40),
        nullable=False,
        default=FailureCategory.NONE,
    )
    #: Bumped by a retry of this destination alone.
    attempt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MessageDispatchRecipientPart(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One part of one announcement at one destination.

    This is the row that makes "gửi lại phần còn thiếu" possible: a retry looks
    for the parts of this recipient that are not ``DELIVERED`` and queues those,
    so a group that received two of three messages gets the third and not all
    three again.
    """

    __tablename__ = "message_dispatch_recipient_parts"
    __table_args__ = (
        UniqueConstraint(
            "recipient_id",
            "dispatch_part_id",
            name="uq_dispatch_recipient_parts_recipient_part",
        ),
        Index("ix_dispatch_recipient_parts_outbound", "outbound_message_id"),
    )

    recipient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_dispatch_recipients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dispatch_part_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_dispatch_parts.id", ondelete="CASCADE"), nullable=False
    )
    part_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: The outbox row carrying this part. The link is what lets the worker
    #: settle a business outcome from a delivery result without guessing.
    outbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[DispatchPartStatus] = mapped_column(
        value_enum(DispatchPartStatus, name="dispatch_part_status", length=20),
        nullable=False,
        default=DispatchPartStatus.QUEUED,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
