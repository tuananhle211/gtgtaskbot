"""Persistence layer: SQLAlchemy declarative models and session management.

Only ``meobot.application`` and the Alembic environment import from here.
Domain code stays free of SQLAlchemy.
"""

from meobot.db.base import Base
from meobot.db.session import Database, get_database

__all__ = ["Base", "Database", "get_database"]
