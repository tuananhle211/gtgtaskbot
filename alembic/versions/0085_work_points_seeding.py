"""Points outside the order flow: PR task codes, publications, seeding (0085).

Revision ID: 0085
Revises: 0084
Create Date: 2026-10-30

* ``work_points`` - the points ledger the reports read beside
  ``order_node_points``: PR task codes once approved, +10 per publication
  with a link, 200 per approved seeding phone, approved "việc khác";
* ``pr_point_claims`` - one person's task codes for one stage (Nội dung /
  Sản xuất) of one PR piece, waiting for a Trưởng phòng PR;
* ``seeding_members`` / ``seeding_targets`` / ``seeding_phones`` /
  ``seeding_other_reports`` - the PR seeding group, its team KPI per week /
  month, the numbers handed in, and the other work reported.

The PR workflow tables are not touched. Nothing is backfilled.

Downgrade
---------
Drops the six tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
RESTRICT = "RESTRICT"
DECISIONS = "status IN ('PENDING', 'APPROVED', 'REJECTED')"


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=RESTRICT
    )


def _now() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "work_points",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("items", _JSON, nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ref_code", sa.String(length=64), nullable=True),
        sa.Column("ref_title", sa.Text(), nullable=True),
        sa.Column("ref_link", sa.String(length=300), nullable=True),
        _now(),
        _fk("work_points", "user_id", "users"),
        _fk("work_points", "department_id", "departments"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_points")),
        sa.UniqueConstraint("source_key", name=op.f("uq_work_points_source_key")),
    )
    op.create_index("ix_work_points_user_earned", "work_points", ["user_id", "earned_at"])
    op.create_index(
        "ix_work_points_department_earned", "work_points", ["department_id", "earned_at"]
    )

    op.create_table(
        "pr_point_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=12), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("items", _JSON, nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_reason", sa.String(length=20), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "stage IN ('CONTENT', 'PRODUCTION')", name=op.f("ck_pr_point_claims_stage_known")
        ),
        sa.CheckConstraint(DECISIONS, name=op.f("ck_pr_point_claims_status_known")),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name=op.f("fk_pr_point_claims_content_id_pr_content_items"),
            ondelete="CASCADE",
        ),
        _fk("pr_point_claims", "user_id", "users"),
        _fk("pr_point_claims", "decided_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_point_claims")),
        sa.UniqueConstraint("content_id", "stage", "user_id", name="uq_pr_point_claims_person"),
    )
    op.create_index("ix_pr_point_claims_status", "pr_point_claims", ["status"])

    op.create_table(
        "seeding_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by_user_id", sa.Uuid(), nullable=True),
        _fk("seeding_members", "user_id", "users"),
        _fk("seeding_members", "added_by_user_id", "users"),
        _fk("seeding_members", "removed_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seeding_members")),
    )
    op.create_index(
        "uq_seeding_members_open",
        "seeding_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )

    op.create_table(
        "seeding_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=6), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("set_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('WEEK', 'MONTH')", name=op.f("ck_seeding_targets_kind_known")),
        sa.CheckConstraint("target >= 0", name=op.f("ck_seeding_targets_target_not_negative")),
        _fk("seeding_targets", "set_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seeding_targets")),
        sa.UniqueConstraint("kind", "period_start", name="uq_seeding_targets_period"),
    )

    op.create_table(
        "seeding_phones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.CheckConstraint(DECISIONS, name=op.f("ck_seeding_phones_status_known")),
        _fk("seeding_phones", "submitted_by_user_id", "users"),
        _fk("seeding_phones", "decided_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seeding_phones")),
    )
    op.create_index(
        "uq_seeding_phones_live",
        "seeding_phones",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("status <> 'REJECTED'"),
    )
    op.create_index(
        "ix_seeding_phones_person", "seeding_phones", ["submitted_by_user_id", "submitted_at"]
    )
    op.create_index("ix_seeding_phones_status", "seeding_phones", ["status"])

    op.create_table(
        "seeding_other_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.CheckConstraint("points > 0", name=op.f("ck_seeding_other_reports_points_positive")),
        sa.CheckConstraint(DECISIONS, name=op.f("ck_seeding_other_reports_status_known")),
        _fk("seeding_other_reports", "user_id", "users"),
        _fk("seeding_other_reports", "decided_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seeding_other_reports")),
    )
    op.create_index(
        "ix_seeding_other_reports_person", "seeding_other_reports", ["user_id", "submitted_at"]
    )


def downgrade() -> None:
    op.drop_table("seeding_other_reports")
    op.drop_table("seeding_phones")
    op.drop_table("seeding_targets")
    op.drop_table("seeding_members")
    op.drop_table("pr_point_claims")
    op.drop_table("work_points")
