"""Video production tracking between an approved script and a finished cut.

Owns assignment (who films, who edits), progress checkpoints and deadlines.
Entry is gated by ``meobot.domain.videos.workflow.assert_production_allowed``.

TODO(milestone-3): production task entity and per-assignee daily digest.
"""
