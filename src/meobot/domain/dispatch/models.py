"""Statuses for a multi-destination announcement, and what a person reads.

Three levels, and they are deliberately separate:

* the **draft** is what somebody is still composing and choosing recipients for;
* the **dispatch** is the confirmed decision - one row, one confirmation, one
  audit event;
* the **recipient** is one group's own outcome, which succeeds or fails on its
  own. One group refusing the bot must not hold up the other eleven, and the
  only way to express that is to give every destination its own status.

A fourth level exists only for long announcements: a recipient that received
part 1 but not part 2 is ``PARTIAL_FAILURE``, which is neither "đã gửi" nor
"chưa gửi được", and reporting it as either would be a lie.
"""

from __future__ import annotations

from enum import StrEnum


class DraftStatus(StrEnum):
    """Lifecycle of a message somebody is still composing."""

    #: Recipients are still being chosen.
    CHOOSING = "CHOOSING"
    #: The full preview has been shown and is waiting for a confirmation.
    PREVIEW = "PREVIEW"
    #: Confirmed. A :class:`DispatchStatus` row now owns the outcome.
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    #: Left too long. Confirming it would send words nobody has looked at today.
    EXPIRED = "EXPIRED"

    @property
    def is_open(self) -> bool:
        """True while this draft still owns the conversation."""
        return self in {DraftStatus.CHOOSING, DraftStatus.PREVIEW}


class DispatchStatus(StrEnum):
    """Lifecycle of one confirmed announcement across all its destinations."""

    QUEUED = "QUEUED"
    #: At least one destination has settled, and at least one has not.
    IN_PROGRESS = "IN_PROGRESS"
    #: Every destination succeeded.
    COMPLETED = "COMPLETED"
    #: Every destination settled, and at least one did not arrive.
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    #: Every destination settled, and none arrived.
    FAILED = "FAILED"

    @property
    def is_settled(self) -> bool:
        """True once no destination is still being attempted."""
        return self in {
            DispatchStatus.COMPLETED,
            DispatchStatus.PARTIALLY_FAILED,
            DispatchStatus.FAILED,
        }


class DispatchRecipientStatus(StrEnum):
    """What happened at one destination."""

    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    #: Some parts of a long announcement arrived and some did not.
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    #: Never attempted: the sender turned out not to be allowed to use it.
    SKIPPED = "SKIPPED"

    @property
    def is_settled(self) -> bool:
        return self is not DispatchRecipientStatus.QUEUED

    @property
    def needs_retry(self) -> bool:
        """True for the destinations "thử lại nơi lỗi" should touch.

        Deliberately excludes :attr:`DELIVERED`. Retrying a group that already
        received the announcement would post it twice, and the person pressing
        the button is trying to fix the ones that did *not* arrive.
        """
        return self in {
            DispatchRecipientStatus.FAILED,
            DispatchRecipientStatus.PARTIAL_FAILURE,
        }


class DispatchPartStatus(StrEnum):
    """What happened to one part of one long announcement at one destination."""

    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class SelectionSource(StrEnum):
    """How one destination came to be on the list.

    Kept per recipient because the preview has to be honest about it: a group
    the person named is different from one MeoBot inferred from a tag, and only
    the second one needs "MeoBot hiểu ... là" in front of it.
    """

    #: The person named this group.
    NAMED = "NAMED"
    #: Matched a tag, purpose, team, brand or department filter.
    INFERRED = "INFERRED"
    #: "tất cả group" - everything the sender may use.
    ALL_REGISTERED = "ALL_REGISTERED"
    #: Pressed on the multi-select keyboard.
    BUTTON = "BUTTON"
    #: "ba group trên", "hai group đầu" - counted off a list just shown.
    LIST_REFERENCE = "LIST_REFERENCE"


#: What a person reads instead of a status name. No English, no enum value.
DISPATCH_RECIPIENT_STATUS_LABELS: dict[DispatchRecipientStatus, str] = {
    DispatchRecipientStatus.QUEUED: "Đang chờ gửi",
    DispatchRecipientStatus.DELIVERED: "Đã gửi",
    DispatchRecipientStatus.PARTIAL_FAILURE: "Mới gửi được một phần",
    DispatchRecipientStatus.FAILED: "Chưa gửi được",
    DispatchRecipientStatus.SKIPPED: "Không được phép gửi",
}

DISPATCH_STATUS_LABELS: dict[DispatchStatus, str] = {
    DispatchStatus.QUEUED: "Đã xếp hàng gửi",
    DispatchStatus.IN_PROGRESS: "Đang gửi",
    DispatchStatus.COMPLETED: "Đã gửi xong",
    DispatchStatus.PARTIALLY_FAILED: "Có nơi chưa gửi được",
    DispatchStatus.FAILED: "Chưa gửi được nơi nào",
}


def dispatch_recipient_status_label(status: DispatchRecipientStatus) -> str:
    """Vietnamese wording for one destination's outcome."""
    return DISPATCH_RECIPIENT_STATUS_LABELS[status]


def dispatch_status_label(status: DispatchStatus) -> str:
    """Vietnamese wording for a whole dispatch."""
    return DISPATCH_STATUS_LABELS[status]
