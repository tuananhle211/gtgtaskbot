"""Runtime configuration held in the ``system_settings`` table.

Values here are the ones a manager may change by chatting with MeoBot
(reminder times, review thresholds, working hours) - as opposed to ``.env``,
which holds infrastructure and secrets and is never editable at runtime.

Milestone 1 provides the table and versioning column only.
TODO(milestone-2): typed setting descriptors + conversational edit flow.
"""
