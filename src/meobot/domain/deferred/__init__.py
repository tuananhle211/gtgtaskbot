"""The first question a stranger asked, kept safely until somebody decides.

Telegram's Bot API cannot fetch an arbitrary past message by id, so "answer the
question they asked before you approved them" is only possible if MeoBot kept
the question at the time. This package holds the vocabulary for doing that
without treating an unapproved stranger as a user.
"""

from meobot.domain.deferred.models import (
    AuthorizationMode,
    DeferredMessageStatus,
    is_replayable,
)

__all__ = ["AuthorizationMode", "DeferredMessageStatus", "is_replayable"]
