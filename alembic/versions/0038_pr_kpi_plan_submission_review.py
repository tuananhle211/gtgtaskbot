"""KPI self-service: when an employee sent their draft for review, and when it came back.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-08

**Five nullable columns on ``pr_work_plans``, two check constraints, and
nothing else.** No table, no status widened, no historical row touched.

Why the plan status is not widened
-----------------------------------

Employees now write their own KPI proposal and a manager reviews it. That
introduces one distinction M2's lifecycle did not have to make: a ``DRAFT``
the employee is still editing versus a ``DRAFT`` they have handed to their
manager and may no longer touch. It is a distinction about *who may edit
right now*, not about where the version is in its life - the plan is not in
force either way, decides nothing either way, and is superseded by nothing
either way. Everything that reads ``status`` today (the two partial unique
indexes, the evaluator's "only ``APPROVED`` decides", the history read model,
the check constraints that tie ``approved_at`` and ``superseded_at`` to their
statuses) stays exactly right, which is the argument for metadata over a
``SUBMITTED`` status: a fifth status would have had to be threaded through all
of it for no reader that needed it.

What one row now says
----------------------

============================  ==================================================
``DRAFT``, nothing set        the employee (or a manager) is writing it
``DRAFT``, ``submitted_at``   awaiting review; the employee is locked out, a
                              manager may still correct it
``DRAFT``, ``returned_at``    sent back; the employee may edit again, and
                              ``return_note`` says why
============================  ==================================================

``submitted_at`` and ``returned_at`` are never both set: resubmitting clears
the return, and returning clears the submission. The audit trail keeps every
one of those moves; the columns describe the present.

``submitted_by_user_id`` and ``returned_by_user_id`` are the provenance the
product asked for by name - subject, creator, submitter and approver are four
different people in the worst case, and none of them is inferred from a
session later. Each is tied to its timestamp by a check constraint, the way
``approved_by_user_id`` already is to ``approved_at``.

``return_note`` is the one prose field, and it is here rather than in a
comments subsystem because the migration was needed anyway and a manager's
reason for sending a plan back is read in exactly one place: the returned
draft's own card.

Downgrade
----------

Drops the five columns and their constraints. It loses who submitted or
returned each draft and when; no plan, quota, allocation or approval is
affected in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_PLANS = "pr_work_plans"
USERS = "users"
RESTRICT = "RESTRICT"


def upgrade() -> None:
    op.add_column(WORK_PLANS, sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(WORK_PLANS, sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True))
    op.add_column(WORK_PLANS, sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(WORK_PLANS, sa.Column("returned_by_user_id", sa.Uuid(), nullable=True))
    op.add_column(WORK_PLANS, sa.Column("return_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_pr_work_plans_submitted_by_user_id_users"),
        WORK_PLANS,
        USERS,
        ["submitted_by_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_foreign_key(
        op.f("fk_pr_work_plans_returned_by_user_id_users"),
        WORK_PLANS,
        USERS,
        ["returned_by_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_check_constraint(
        "submitted_by_matches_submitted_at",
        WORK_PLANS,
        "(submitted_at IS NULL) = (submitted_by_user_id IS NULL)",
    )
    op.create_check_constraint(
        "returned_by_matches_returned_at",
        WORK_PLANS,
        "(returned_at IS NULL) = (returned_by_user_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("returned_by_matches_returned_at", WORK_PLANS, type_="check")
    op.drop_constraint("submitted_by_matches_submitted_at", WORK_PLANS, type_="check")
    op.drop_constraint(
        op.f("fk_pr_work_plans_returned_by_user_id_users"), WORK_PLANS, type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_pr_work_plans_submitted_by_user_id_users"), WORK_PLANS, type_="foreignkey"
    )
    op.drop_column(WORK_PLANS, "return_note")
    op.drop_column(WORK_PLANS, "returned_by_user_id")
    op.drop_column(WORK_PLANS, "returned_at")
    op.drop_column(WORK_PLANS, "submitted_by_user_id")
    op.drop_column(WORK_PLANS, "submitted_at")
