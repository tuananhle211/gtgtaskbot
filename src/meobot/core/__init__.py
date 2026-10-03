"""Cross-cutting concerns: settings, logging, errors, time and request context.

This package must not import from ``meobot.api``, ``meobot.bot`` or
``meobot.integrations``. Everything else may import from here.
"""
