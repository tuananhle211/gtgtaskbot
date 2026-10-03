"""PR and Communications: the vocabulary of brands, channels, content and tasks.

Step 1A is the persistence foundation only. This package holds the enumerations
the PR tables store, and nothing else: no service, no workflow rule, no
transition table. Those arrive with Step 1B, and they will import the names from
here rather than redeclaring them.

The same dependency rule as every other domain package applies - standard
library and :mod:`meobot.core` only. No SQLAlchemy, no aiogram, no vendor SDK.
"""
