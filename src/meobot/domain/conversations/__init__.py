"""Conversation state: per-chat sessions, pending clarifications and
multi-turn workflow context.

Milestone 1 keeps conversations stateless - each Telegram message is planned
and executed independently. The pending-confirmation flow in
``meobot.application.confirmation_service`` is the only cross-message state.

TODO(milestone-2): persist a ``conversations`` table with the active topic,
the last referenced entity and short-term memory for follow-up questions.
"""
