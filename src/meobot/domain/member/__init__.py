"""The Vietnamese-first interaction layer a Member actually sees.

A Member should be able to use MeoBot the way they talk to a colleague: in
Vietnamese, with or without accents, without remembering a single command and
without ever meeting an enum, an id or an English status word.

Three modules make that possible and they are deliberately separate:

* :mod:`~meobot.domain.member.copy` is the only place Member-facing wording
  lives, so a phrase cannot drift between a button, a card and an error;
* :mod:`~meobot.domain.member.normalization` folds the shapes people really
  type ("viec hnay cua toi") onto something matchable, without touching URLs or
  names;
* :mod:`~meobot.domain.member.intents` decides what a message *means*, using
  patterns rather than a model - so the decision is deterministic, free, and
  auditable, and a provider outage cannot stop somebody accepting a task.
"""
