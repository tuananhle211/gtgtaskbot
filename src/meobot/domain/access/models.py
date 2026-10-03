"""Access vocabulary: user lifecycle, group policy, and the Guest principal.

Three ideas live here and they are deliberately separate.

**Lifecycle** (:class:`UserStatus`) is global and is about the *account*: an
active member, one temporarily suspended, one whose access was revoked. It is
stored on ``users`` and it outranks everything else - no per-group setting can
put a suspended account back on the air.

**Group policy** (:class:`GroupPolicyMode`) is local and is about *this chat*:
answer them here, ignore them here, stay quiet here until a timestamp. It can
only ever narrow what the lifecycle already allows.

**Guest** (:class:`GuestPrincipal`) is neither. A Guest has no ``users`` row and
no :class:`~meobot.domain.identity.models.Role`, so there is no permission set
to accidentally grant. It is a chat-only principal, bounded by a question count
and a wall-clock deadline, and scoped to exactly one group. Modelling it as a
separate type - rather than as a synthetic ``EMPLOYEE`` - is what makes "a Guest
cannot run a tool" a fact about the type system instead of a rule somebody has
to remember to check.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: A Guest's default allowance, and the maximum this release will grant.
GUEST_DEFAULT_QUESTION_LIMIT = 10
GUEST_DEFAULT_DURATION = timedelta(hours=24)

#: How long an unanswered access request stays open before it must be re-made.
PENDING_REQUEST_TTL = timedelta(minutes=30)

#: After an explicit refusal, how long before the owner is bothered again.
REJECTION_NOTIFY_COOLDOWN = timedelta(hours=24)

#: How long an approval button stays pressable.
CALLBACK_TTL = timedelta(hours=24)


class UserStatus(StrEnum):
    """Global lifecycle of a registered ``users`` row.

    Stored as a string, like every other enum in this codebase, so adding a
    state later is an ``UPDATE``-free migration.
    """

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"

    @property
    def may_use_meobot(self) -> bool:
        """True only for a fully activated account."""
        return self is UserStatus.ACTIVE


class GroupPolicyMode(StrEnum):
    """What MeoBot does with one person's messages in one group."""

    INHERIT = "inherit"
    ALLOW = "allow"
    GUEST = "guest"
    IGNORE = "ignore"
    MUTE_UNTIL = "mute_until"


class AccessAction(StrEnum):
    """What an owner may decide about an unknown person who tagged MeoBot.

    Values are two characters because they travel inside Telegram's 64-byte
    ``callback_data`` budget - see :mod:`meobot.domain.access.callbacks`.
    """

    ANSWER_ONCE = "ao"
    GRANT_GUEST = "gg"
    ADD_AS_MEMBER = "am"
    IGNORE_ONCE = "io"
    IGNORE_IN_GROUP = "ig"
    #: The second press of the two-step "add as Member" flow. Creating an
    #: account is the highest-risk action on this menu, so the first press only
    #: renders a preview and this one commits it.
    CONFIRM_MEMBER = "cm"


class QuotaAction(StrEnum):
    """What an owner may decide about a member who ran out of chat quota."""

    ADD_10_TODAY = "q1"
    RESET_TODAY = "qr"
    SET_CUSTOM_LIMIT = "qs"
    DENY = "qd"


class PendingRequestStatus(StrEnum):
    """Lifecycle of one access request."""

    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AccessOutcome(StrEnum):
    """The verdict of the access gate for one incoming Telegram update.

    Every value is a *reason*, not just a yes/no, because the transport has to
    behave differently for each: some are answered, some are silently dropped,
    and exactly one opens an approval request.
    """

    #: A registered, active user; the normal pipeline runs.
    REGISTERED = "registered"
    #: An active Guest in this group; the chat-only pipeline runs.
    GUEST = "guest"
    #: One approved reply to one already-known message.
    ANSWER_ONCE = "answer_once"
    #: Not addressed to MeoBot at all - not a refusal, just not our turn.
    NOT_ADDRESSED = "not_addressed"
    #: The gate has no opinion; the pre-existing registered-actor rules decide.
    #: Used for private chats with an unknown account, where "you are not
    #: registered" is the right answer and was already being given correctly.
    DEFER = "defer"
    #: Silently dropped: ignore/mute policy, a bot, or an anonymous sender.
    SILENT = "silent"
    #: Blocked with a visible message: suspended, revoked, quota exhausted.
    BLOCKED = "blocked"
    #: Unknown person who tagged MeoBot: an owner approval request is opened.
    PENDING_APPROVAL = "pending_approval"


class GuestPrincipal(BaseModel):
    """A temporary, chat-only participant. Not a system user.

    Deliberately *not* an :class:`~meobot.domain.identity.models.Actor`: it has
    no ``user_id``, no ``Role`` and therefore no permission set. Every handler
    that touches business data asks for an ``Actor``, so a Guest cannot reach
    one - the tool pipeline is unreachable by construction rather than by a
    check that could be forgotten.
    """

    model_config = ConfigDict(frozen=True)

    policy_id: uuid.UUID
    telegram_user_id: int
    telegram_chat_id: int
    display_name: str = "Khách"
    telegram_username: str | None = None
    granted_at: datetime
    expires_at: datetime
    question_limit: int = Field(default=GUEST_DEFAULT_QUESTION_LIMIT, ge=1)
    questions_used: int = Field(default=0, ge=0)
    reserved_count: int = Field(default=0, ge=0)

    @property
    def questions_remaining(self) -> int:
        """Slots left, never negative."""
        return max(0, self.question_limit - self.questions_used - self.reserved_count)

    def is_expired(self, now: datetime) -> bool:
        """True once the wall-clock deadline has passed."""
        return now >= self.expires_at

    def is_exhausted(self) -> bool:
        """True once every granted question has been used or reserved."""
        return self.questions_remaining <= 0

    def may_ask(self, now: datetime) -> bool:
        """Both limits must still hold; whichever runs out first ends access."""
        return not self.is_expired(now) and not self.is_exhausted()

    def scoped_to(self, chat_id: int) -> bool:
        """A Guest exists in exactly one group and nowhere else."""
        return self.telegram_chat_id == chat_id


class AccessDecision(BaseModel):
    """What the gate concluded, and what the transport should do about it."""

    model_config = ConfigDict(frozen=True)

    outcome: AccessOutcome
    #: Present only for :attr:`AccessOutcome.REGISTERED`.
    actor_user_id: uuid.UUID | None = None
    #: Present only for :attr:`AccessOutcome.GUEST`.
    guest: GuestPrincipal | None = None
    #: Shown to the user when the outcome is ``BLOCKED``; never a private
    #: administrative note, and never a suspension reason.
    message: str | None = None
    #: Whether :attr:`message` may be sent in a group. A quota notice is the
    #: member's own business and is fine anywhere; "your account is suspended"
    #: is not something to announce to their colleagues, so it is private-only.
    notify_in_group: bool = True
    #: Set when the gate opened or reused an approval request.
    pending_request_id: uuid.UUID | None = None
    #: Structured reason for logs and audit. Never shown in a group.
    reason: str = ""

    @property
    def may_run_handler(self) -> bool:
        """True when an aiogram handler should be reached at all."""
        return self.outcome in {
            AccessOutcome.REGISTERED,
            AccessOutcome.GUEST,
            AccessOutcome.ANSWER_ONCE,
        }

    @property
    def is_chat_only(self) -> bool:
        """True when only the plain chat-generation path may run."""
        return self.outcome in {AccessOutcome.GUEST, AccessOutcome.ANSWER_ONCE}


def guest_deadline(granted_at: datetime, duration: timedelta = GUEST_DEFAULT_DURATION) -> datetime:
    """When a Guest granted at ``granted_at`` stops being able to ask.

    Both Guest limits start at the moment the owner confirms, not at the moment
    the stranger first spoke - the grant is what is being measured.
    """
    return granted_at + duration
