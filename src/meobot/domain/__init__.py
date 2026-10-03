"""Domain layer: business vocabulary, invariants and workflow rules.

Dependency rules (enforced by review, checked by ``make lint``):

* ``domain`` may import from ``meobot.core`` and the standard library only.
* ``domain`` must NEVER import aiogram, FastAPI, SQLAlchemy models, or any
  vendor SDK. Persistence and transport live outside this package.
* ``application`` orchestrates ``domain`` + ``db`` + ``integrations``.
"""
