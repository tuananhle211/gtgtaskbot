"""Sending one announcement to several registered groups at once.

0.6.0a2 could address exactly one destination. That was not a missing feature so
much as a missing *aggregate*: an announcement carried a single
``destination_chat_id``, so "gửi cho ba group" had nowhere to be recorded, and
the conversation went round again asking which one was meant.

This package holds the vocabulary the multi-destination path needs and nothing
that talks to a database or to Telegram:

* :mod:`~meobot.domain.dispatch.models` - the statuses and their Vietnamese
  labels;
* :mod:`~meobot.domain.dispatch.phrases` - what "Tất cả", "hai group đầu" and
  "Ok gửi nhé" mean, decided by pattern and never by a model;
* :mod:`~meobot.domain.dispatch.splitting` - how a long announcement becomes
  ordered parts Telegram will accept, without losing a character;
* :mod:`~meobot.domain.dispatch.privacy` - whether text looks personal enough
  that it must not be posted into a group.
"""

from meobot.domain.dispatch.models import (
    DispatchPartStatus,
    DispatchRecipientStatus,
    DispatchStatus,
    DraftStatus,
    SelectionSource,
    dispatch_recipient_status_label,
)
from meobot.domain.dispatch.phrases import (
    Continuation,
    ContinuationKind,
    read_continuation,
)
from meobot.domain.dispatch.splitting import MessagePart, split_announcement

__all__ = [
    "Continuation",
    "ContinuationKind",
    "DispatchPartStatus",
    "DispatchRecipientStatus",
    "DispatchStatus",
    "DraftStatus",
    "MessagePart",
    "SelectionSource",
    "dispatch_recipient_status_label",
    "read_continuation",
    "split_announcement",
]
