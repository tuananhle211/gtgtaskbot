"""The unified task: one row, one page and one action endpoint for PR and Ads.

Import the concrete module you need; this package exports nothing, so that
:mod:`meobot.db.models` can install the flush hook in :mod:`.sync` without
pulling the read and write services in with it.
"""
