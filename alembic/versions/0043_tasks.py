"""One ``tasks`` row per PR content item and per Ads order.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-07

One new table and **no change to any existing one**. ``tasks`` is the
unit-neutral picture of a piece of work - code, title, kind, owner, priority,
the source's own stage and the board phase it maps to - so one page
(``/tasks/{code}``) and one action endpoint can serve both units. Each row is
a 1-1 extension of exactly one source: ``pr_content_items`` or ``orders``.

The rows are written by the application's flush hook
(``meobot.application.tasks.sync``) on every write of the source; this
revision only backfills the rows that already exist.

Backfill
--------

* the unit is the ``PR`` row of ``org_units`` for content, ``orders.unit_id``
  for an order;
* the phase is the board's mapping (``meobot.domain.board.models``), copied
  here as literals so a later change to the mapping cannot rewrite history;
* ``stage_since`` is the newest transition into the current stage for content
  (its ``created_at`` when there is none), and for an order the activation of
  its active node, else its approval, else its submission - the same reading
  the board makes;
* ``finished_at`` is set for the finished phases: the completion for an
  order, the stage clock otherwise.

Downgrade
---------

Drops the table. Nothing is lost that the sources do not still hold.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS = "tasks"
RESTRICT = "RESTRICT"
CASCADE = "CASCADE"

#: Copied from ``PR_STAGE_PHASE`` at the time of writing.
PR_STAGE_PHASE = (
    ("IDEA", "ORDER"),
    ("BRIEFING", "ORDER"),
    ("SCRIPTING", "ORDER"),
    ("AI_REVIEW", "REVIEW"),
    ("TEAM_LEAD_REVIEW", "REVIEW"),
    ("HEAD_REVIEW", "REVIEW"),
    ("APPROVED", "REVIEW"),
    ("PRODUCTION", "PRODUCTION"),
    ("INTERNAL_REVIEW", "FINAL_REVIEW"),
    ("READY_TO_PUBLISH", "DONE"),
    ("PUBLISHED", "DONE"),
    ("MEASURED", "DONE"),
    ("ARCHIVED", "DONE"),
    ("CANCELLED", "CANCELLED"),
)
#: Copied from ``ADS_STAGE_PHASE`` at the time of writing.
ADS_STAGE_PHASE = (
    ("ORDER_PENDING", "REVIEW"),
    ("ORDER_RETURNED", "ORDER"),
    ("BIEN_TAP", "PRODUCTION"),
    ("THIET_KE", "PRODUCTION"),
    ("DUNG", "PRODUCTION"),
    ("GAN_LINK", "PRODUCTION"),
    ("DUYET_VIDEO_BT", "FINAL_REVIEW"),
    ("FINAL_REVIEW", "FINAL_REVIEW"),
    ("COMPLETED", "DONE"),
    ("CANCELLED", "CANCELLED"),
)
PRIORITY_LEVELS = ("HIGH", "URGENT", "CRITICAL")
ACTIVE_NODE_STATUSES = ("CHUA_GIAO", "DANG_LAM", "AI_DANG_REVIEW", "CHO_DUYET", "DANG_SUA")

ONE_SOURCE = (
    "(source_type = 'PR_CONTENT' AND pr_content_id IS NOT NULL AND order_id IS NULL) OR "
    "(source_type = 'ORDER' AND order_id IS NOT NULL AND pr_content_id IS NULL)"
)
PHASE_VALUES = "phase IN ('ORDER', 'REVIEW', 'PRODUCTION', 'FINAL_REVIEW', 'DONE', 'CANCELLED')"


def _case(column: str, pairs: Sequence[tuple[str, str]]) -> str:
    whens = " ".join(f"WHEN '{stage}' THEN '{phase}'" for stage, phase in pairs)
    return f"CASE {column} {whens} END"


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TASKS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("pr_content_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=30), server_default=sa.text("''"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("is_priority", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("stage_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(ONE_SOURCE, name=op.f("ck_tasks_one_source")),
        sa.CheckConstraint(PHASE_VALUES, name=op.f("ck_tasks_phase_valid")),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_tasks_code_not_empty")),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["org_units.id"], name=op.f("fk_tasks_unit_id_org_units"), ondelete=RESTRICT
        ),
        sa.ForeignKeyConstraint(
            ["pr_content_id"],
            ["pr_content_items.id"],
            name=op.f("fk_tasks_pr_content_id_pr_content_items"),
            ondelete=CASCADE,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_tasks_order_id_orders"), ondelete=CASCADE
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_tasks_owner_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("code", name=op.f("uq_tasks_code")),
        sa.UniqueConstraint("pr_content_id", name=op.f("uq_tasks_pr_content_id")),
        sa.UniqueConstraint("order_id", name=op.f("uq_tasks_order_id")),
    )
    op.create_index(op.f("ix_tasks_unit_id"), TASKS, ["unit_id"])
    op.create_index(op.f("ix_tasks_phase"), TASKS, ["phase"])
    op.create_index(op.f("ix_tasks_owner_user_id"), TASKS, ["owner_user_id"])
    op.create_index(op.f("ix_tasks_created_at"), TASKS, ["created_at"])
    _backfill()


def _backfill() -> None:
    pr_phase = _case("c.workflow_stage", PR_STAGE_PHASE)
    pr_since = (
        "COALESCE((SELECT max(e.created_at) FROM pr_content_transition_events e "
        "WHERE e.content_id = c.id AND e.to_stage = c.workflow_stage), c.created_at)"
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO tasks (
                id, unit_id, source_type, pr_content_id, order_id, code, title, kind,
                owner_user_id, is_priority, stage, phase, stage_since, finished_at,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(), u.id, 'PR_CONTENT', c.id, NULL, c.code, c.title,
                COALESCE(c.content_type, ''), c.owner_user_id,
                c.priority IN ({_quoted(PRIORITY_LEVELS)}),
                c.workflow_stage, {pr_phase}, {pr_since},
                CASE WHEN {pr_phase} IN ('DONE', 'CANCELLED') THEN {pr_since} END,
                c.created_at, c.updated_at
            FROM pr_content_items c
            JOIN org_units u ON u.code = 'PR'
            """
        )
    )
    ads_phase = _case("o.stage", ADS_STAGE_PHASE)
    ads_since = (
        "CASE WHEN o.stage = 'COMPLETED' AND o.completed_at IS NOT NULL THEN o.completed_at "
        "ELSE COALESCE((SELECT max(n.activated_at) FROM order_nodes n WHERE n.order_id = o.id "
        f"AND n.status IN ({_quoted(ACTIVE_NODE_STATUSES)})), o.order_approved_at, o.submitted_at) "
        "END"
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO tasks (
                id, unit_id, source_type, pr_content_id, order_id, code, title, kind,
                owner_user_id, is_priority, stage, phase, stage_since, finished_at,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(), o.unit_id, 'ORDER', NULL, o.id, o.code, o.title,
                o.video_type, o.owner_user_id, o.is_priority,
                o.stage, {ads_phase}, {ads_since},
                CASE
                    WHEN o.stage = 'COMPLETED' THEN COALESCE(o.completed_at, o.updated_at)
                    WHEN o.stage = 'CANCELLED' THEN o.updated_at
                END,
                o.submitted_at, o.updated_at
            FROM orders o
            """
        )
    )


def downgrade() -> None:
    op.drop_table(TASKS)
