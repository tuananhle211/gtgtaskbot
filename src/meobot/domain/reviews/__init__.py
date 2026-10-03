"""AI review of scripts against a versioned rubric.

A review always records the ``script_type_version_id`` it was produced
with, so historical scores stay explainable after a rubric changes.

TODO(milestone-2): ``ReviewResult`` (per-criterion score + rationale),
the reviewer service and the ``ai_reviewed`` transition.
"""
