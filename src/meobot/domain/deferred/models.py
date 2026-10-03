"""Status vocabulary for a Guest's held-back first question.

The state machine is deliberately explicit rather than a pair of booleans,
because four different things can happen to a stored question and three of them
must never produce an answer:

    PENDING_APPROVAL ──▶ AUTHORIZED_ONCE ──┐
                    ├──▶ AUTHORIZED_GUEST ─┼──▶ PROCESSING ──▶ RESPONSE_QUEUED ──▶ ANSWERED
                    ├──▶ REJECTED          │                        └──▶ FAILED
                    └──▶ EXPIRED           │

``PROCESSING`` exists so the transition out of "authorized" is a single atomic
``UPDATE ... WHERE status = 'AUTHORIZED_*'``. That one statement is what makes
a double-tapped owner button, a redelivered Telegram update, a Celery retry and
a worker restart all produce exactly one answer: whoever loses the race sees
zero rows updated and does nothing.

None of these names is ever shown to anybody. Users see Vietnamese sentences.
"""

from __future__ import annotations

from enum import StrEnum


class DeferredMessageStatus(StrEnum):
    """Where one held question is in its life."""

    PENDING_APPROVAL = "PENDING_APPROVAL"
    AUTHORIZED_ONCE = "AUTHORIZED_ONCE"
    AUTHORIZED_GUEST = "AUTHORIZED_GUEST"
    PROCESSING = "PROCESSING"
    RESPONSE_QUEUED = "RESPONSE_QUEUED"
    ANSWERED = "ANSWERED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"

    @property
    def is_authorized(self) -> bool:
        """True when an owner has said this question may be answered."""
        return self in {
            DeferredMessageStatus.AUTHORIZED_ONCE,
            DeferredMessageStatus.AUTHORIZED_GUEST,
        }

    @property
    def is_terminal(self) -> bool:
        """True when nothing further will happen to this question."""
        return self in {
            DeferredMessageStatus.ANSWERED,
            DeferredMessageStatus.REJECTED,
            DeferredMessageStatus.EXPIRED,
            DeferredMessageStatus.FAILED,
        }

    @property
    def text_should_be_purged(self) -> bool:
        """True when the stored words are no longer needed for anything.

        A stranger who was refused, ignored or left to expire never became a
        user, and keeping what they wrote would mean the least-trusted people
        in the system have the longest-lived data in it.
        """
        return self in {
            DeferredMessageStatus.REJECTED,
            DeferredMessageStatus.EXPIRED,
        }


class AuthorizationMode(StrEnum):
    """What the owner granted when they approved the question.

    The distinction is load-bearing for quota: an ``ANSWER_ONCE`` reply creates
    no Guest window and therefore consumes nothing, while ``GUEST_WINDOW``
    spends the first of the Guest's ten answers - but only once the reply has
    actually been delivered.
    """

    #: "💬 Trả lời một lần" - one chat-only answer, no ongoing access.
    ANSWER_ONCE = "ANSWER_ONCE"
    #: "⏱ Guest: 10 lượt / 24 giờ" - the first of the granted answers.
    GUEST_WINDOW = "GUEST_WINDOW"
    #: "Thêm làm Member" - replayed through the member-safe path.
    MEMBER = "MEMBER"


#: Which authorization each status came from, for the replay worker.
_STATUS_FOR_MODE: dict[AuthorizationMode, DeferredMessageStatus] = {
    AuthorizationMode.ANSWER_ONCE: DeferredMessageStatus.AUTHORIZED_ONCE,
    AuthorizationMode.GUEST_WINDOW: DeferredMessageStatus.AUTHORIZED_GUEST,
    AuthorizationMode.MEMBER: DeferredMessageStatus.AUTHORIZED_GUEST,
}


def status_for_mode(mode: AuthorizationMode) -> DeferredMessageStatus:
    """The status an authorization of ``mode`` moves a held question into."""
    return _STATUS_FOR_MODE[mode]


def is_replayable(status: DeferredMessageStatus) -> bool:
    """True when this question may still be turned into an answer."""
    return status.is_authorized
