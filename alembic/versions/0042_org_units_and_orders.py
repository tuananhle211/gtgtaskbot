"""Units (PR, Ads) and the Ads order engine's tables.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-07

Nine new tables and **no change to any existing one**. The PR content
workflow, its approvals, AI review, production, publication and the Work
ledger come through this revision byte for byte; an Ads order lives in its own
rows and meets PR only in the read layer that shows both on one board.

What is created
---------------

* ``org_units`` and ``org_unit_members`` - the two units and who is tagged
  into which. The tag is the wall: it decides what a person may see.
* ``orders``, ``order_nodes``, ``order_submissions``, ``order_events``,
  ``order_approvals`` - one Ads order, its four production nodes, every
  hand-in, the log and the gate decisions. The last three are append-only.
* ``order_code_counters`` - the number behind ``TUAN-D-261003-01``.
* ``order_work_rules`` - which Work type a node's approval counts as.

Why this revision seeds rows
----------------------------

``docs/handover/08`` keeps business taxonomy out of migrations and in the
CLIs. This revision makes a bounded exception, stated here so it is a decision
and not an oversight:

* the two ``org_units`` rows are not taxonomy but the module's registry; the
  gate that protects every ``/api/pr/*`` route reads them on the first request
  after deploy, and a deploy that left them to a CLI would lock everybody out
  until somebody ran it;
* **every existing user is tagged PR** so that nothing a PR member could do
  yesterday is refused today. This is the whole reason the rollout is safe;
* the three Ads work types and their default rules exist so the first approved
  node after go-live lands in the KPI ledger rather than being dropped.

Ids are ``uuid5`` from a fixed namespace, so the seed is idempotent across
environments and the downgrade can target exactly these rows.

Downgrade
---------

Drops the nine tables in reverse dependency order. The three seeded
``pr_work_types`` rows stay: they are ordinary taxonomy, work may already
count against them, and the next upgrade finds them by code.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_UNITS = "org_units"
ORG_UNIT_MEMBERS = "org_unit_members"
ORDERS = "orders"
ORDER_NODES = "order_nodes"
ORDER_SUBMISSIONS = "order_submissions"
ORDER_EVENTS = "order_events"
ORDER_APPROVALS = "order_approvals"
ORDER_CODE_COUNTERS = "order_code_counters"
ORDER_WORK_RULES = "order_work_rules"
USERS = "users"
WORK_TYPES = "pr_work_types"
RESTRICT = "RESTRICT"

UNIT_CODE_VALUES = ("PR", "ADS")
MEMBER_ROLE_VALUES = ("MEMBER", "ORDERER", "HEAD", "BIEN_TAP", "THIET_KE", "DUNG")
VIDEO_TYPE_VALUES = ("D", "TD", "BTD")
SCRIPT_SOURCE_VALUES = ("AI", "REAL")
STAGE_VALUES = (
    "ORDER_PENDING",
    "ORDER_RETURNED",
    "BIEN_TAP",
    "THIET_KE",
    "DUNG",
    "GAN_LINK",
    "DUYET_VIDEO_BT",
    "FINAL_REVIEW",
    "COMPLETED",
    "CANCELLED",
)
NODE_TYPE_VALUES = ("BIEN_TAP", "THIET_KE", "DUNG", "GAN_LINK")
NODE_STATUS_VALUES = (
    "CHUA_TOI",
    "BO_QUA",
    "CHUA_GIAO",
    "DANG_LAM",
    "AI_DANG_REVIEW",
    "CHO_DUYET",
    "DANG_SUA",
    "HOAN_THANH",
)
EVENT_KIND_VALUES = (
    "SUBMITTED",
    "RESUBMITTED",
    "ORDER_APPROVED",
    "ORDER_RETURNED",
    "NODE_ACTIVATED",
    "NODE_ASSIGNED",
    "NODE_ACCEPTED",
    "WORK_SUBMITTED",
    "NODE_APPROVED",
    "NODE_RETURNED",
    "LINK_ATTACHED",
    "VIDEO_APPROVED",
    "VIDEO_RETURNED",
    "FINAL_APPROVED",
    "FINAL_RETURNED",
    "PRIORITY_SET",
    "PRIORITY_CLEARED",
    "CANCELLED",
)
GATE_VALUES = ("ORDER", "VIDEO_BT", "FINAL")
DECISION_VALUES = ("APPROVED", "RETURNED")

ACTIVE_NODE = sa.text(
    "status IN ('CHUA_GIAO', 'DANG_LAM', 'AI_DANG_REVIEW', 'CHO_DUYET', 'DANG_SUA')"
)

#: Fixed namespace so the seeded ids are the same on every environment.
SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004200420042")
UNIT_SEED = (
    ("PR", "Phòng PR", "{}"),
    (
        "ADS",
        "Phòng Ads",
        '{"urgent_days": 7, "telegram_enabled": false, "btd_link_attacher": "DUNG"}',
    ),
)
#: (code, name, node type) - the three Ads work types and their default rule.
WORK_TYPE_SEED = (
    ("ADS_BIEN_TAP", "Biên tập kịch bản (Ads)", "BIEN_TAP"),
    ("ADS_THIET_KE", "Thiết kế (Ads)", "THIET_KE"),
    ("ADS_DUNG", "Dựng video (Ads)", "DUNG"),
)


def _seed_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{kind}:{key}")


def _enum(values: Sequence[str], name: str, length: int) -> sa.Enum:
    """A fresh enum object per column; sharing one across columns makes
    SQLAlchemy emit the same CHECK name twice on some backends."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{USERS}.id"], name=op.f(f"fk_{table}_{column}_{USERS}"), ondelete=RESTRICT
    )


def upgrade() -> None:
    _create_units()
    _create_orders()
    _seed()


def _create_units() -> None:
    op.create_table(
        ORG_UNITS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", _enum(UNIT_CODE_VALUES, "org_unit_code", 10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "settings",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_org_units_name_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_units")),
        sa.UniqueConstraint("code", name=op.f("uq_org_units_code")),
    )

    op.create_table(
        ORG_UNIT_MEMBERS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _enum(MEMBER_ROLE_VALUES, "org_unit_member_role", 20), nullable=False),
        sa.Column("is_lead", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("member_code", sa.String(length=20), nullable=True),
        sa.Column("personal_nas_url", sa.Text(), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "member_code IS NULL OR length(trim(member_code)) > 0",
            name=op.f("ck_org_unit_members_member_code_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_org_unit_members_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        _user_fk(ORG_UNIT_MEMBERS, "user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_unit_members")),
        sa.UniqueConstraint("unit_id", "user_id", name="uq_org_unit_members_unit_id_user_id"),
    )
    op.create_index("ix_org_unit_members_user_id", ORG_UNIT_MEMBERS, ["user_id"])
    # One open tag per member code within a unit. A closed tag frees the code.
    op.create_index(
        "uq_org_unit_members_unit_member_code",
        ORG_UNIT_MEMBERS,
        ["unit_id", "member_code"],
        unique=True,
        postgresql_where=sa.text("member_code IS NOT NULL AND left_at IS NULL"),
        sqlite_where=sa.text("member_code IS NOT NULL AND left_at IS NULL"),
    )


def _create_orders() -> None:
    op.create_table(
        ORDERS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("video_type", _enum(VIDEO_TYPE_VALUES, "order_video_type", 5), nullable=False),
        sa.Column("order_content", sa.Text(), nullable=False),
        sa.Column(
            "script_source", _enum(SCRIPT_SOURCE_VALUES, "order_script_source", 10), nullable=True
        ),
        sa.Column("design_link", sa.Text(), nullable=True),
        sa.Column("reference_link", sa.Text(), nullable=True),
        sa.Column("source_link", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "stage",
            _enum(STAGE_VALUES, "order_stage", 20),
            server_default="ORDER_PENDING",
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("returned_reason", sa.Text(), nullable=True),
        sa.Column("is_priority", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("product_link", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_orders_code_not_empty")),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_orders_title_not_empty")),
        sa.CheckConstraint(
            "video_type <> 'D' OR design_link IS NOT NULL",
            name=op.f("ck_orders_design_link_required_for_d"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_orders_version_positive")),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_orders_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        _user_fk(ORDERS, "owner_user_id"),
        _user_fk(ORDERS, "order_approved_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(op.f("ix_orders_code"), ORDERS, ["code"], unique=True)
    op.create_index("ix_orders_unit_submitted", ORDERS, ["unit_id", "submitted_at"])
    op.create_index(
        "ix_orders_unit_owner_submitted", ORDERS, ["unit_id", "owner_user_id", "submitted_at"]
    )
    op.create_index("ix_orders_unit_stage", ORDERS, ["unit_id", "stage"])

    op.create_table(
        ORDER_NODES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", _enum(NODE_TYPE_VALUES, "order_node_type", 10), nullable=False),
        sa.Column(
            "status",
            _enum(NODE_STATUS_VALUES, "order_node_status", 15),
            server_default="CHUA_TOI",
            nullable=False,
        ),
        sa.Column("preassigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("submission_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "revision_count >= 0", name=op.f("ck_order_nodes_revision_count_not_negative")
        ),
        sa.CheckConstraint(
            "submission_count >= 0", name=op.f("ck_order_nodes_submission_count_not_negative")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_order_nodes_version_positive")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{ORDERS}.id"],
            name=op.f("fk_order_nodes_order_id_orders"),
            ondelete=RESTRICT,
        ),
        _user_fk(ORDER_NODES, "preassigned_user_id"),
        _user_fk(ORDER_NODES, "assignee_user_id"),
        _user_fk(ORDER_NODES, "approved_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_nodes")),
        sa.UniqueConstraint("order_id", "node_type", name="uq_order_nodes_order_id_node_type"),
    )
    # The engine's invariant: one active node per order.
    op.create_index(
        "uq_order_nodes_active_per_order",
        ORDER_NODES,
        ["order_id"],
        unique=True,
        postgresql_where=ACTIVE_NODE,
        sqlite_where=ACTIVE_NODE,
    )
    op.create_index("ix_order_nodes_assignee_status", ORDER_NODES, ["assignee_user_id", "status"])
    op.create_index("ix_order_nodes_preassigned_user_id", ORDER_NODES, ["preassigned_user_id"])

    op.create_table(
        ORDER_SUBMISSIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("submission_no", sa.Integer(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("script_text", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "submission_no >= 1", name=op.f("ck_order_submissions_submission_no_positive")
        ),
        sa.CheckConstraint(
            "link IS NOT NULL OR script_text IS NOT NULL",
            name=op.f("ck_order_submissions_has_payload"),
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            [f"{ORDER_NODES}.id"],
            name=op.f("fk_order_submissions_node_id_order_nodes"),
            ondelete=RESTRICT,
        ),
        _user_fk(ORDER_SUBMISSIONS, "submitted_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_submissions")),
        sa.UniqueConstraint(
            "node_id", "submission_no", name="uq_order_submissions_node_id_submission_no"
        ),
    )

    op.create_table(
        ORDER_EVENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("kind", _enum(EVENT_KIND_VALUES, "order_event_kind", 30), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{ORDERS}.id"],
            name=op.f("fk_order_events_order_id_orders"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            [f"{ORDER_NODES}.id"],
            name=op.f("fk_order_events_node_id_order_nodes"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{ORDER_SUBMISSIONS}.id"],
            name=op.f("fk_order_events_submission_id_order_submissions"),
            ondelete=RESTRICT,
        ),
        _user_fk(ORDER_EVENTS, "actor_user_id"),
        _user_fk(ORDER_EVENTS, "assignee_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index("ix_order_events_order_created", ORDER_EVENTS, ["order_id", "created_at"])

    op.create_table(
        ORDER_APPROVALS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("gate", _enum(GATE_VALUES, "order_approval_gate", 10), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column(
            "decision", _enum(DECISION_VALUES, "order_approval_decision", 10), nullable=False
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("round_no >= 1", name=op.f("ck_order_approvals_round_no_positive")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{ORDERS}.id"],
            name=op.f("fk_order_approvals_order_id_orders"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{ORDER_SUBMISSIONS}.id"],
            name=op.f("fk_order_approvals_submission_id_order_submissions"),
            ondelete=RESTRICT,
        ),
        _user_fk(ORDER_APPROVALS, "actor_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_approvals")),
        sa.UniqueConstraint(
            "order_id", "gate", "round_no", name="uq_order_approvals_order_id_gate_round_no"
        ),
    )

    op.create_table(
        ORDER_CODE_COUNTERS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("member_code", sa.String(length=20), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("last_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(member_code)) > 0",
            name=op.f("ck_order_code_counters_member_code_not_empty"),
        ),
        sa.CheckConstraint(
            "last_no >= 0", name=op.f("ck_order_code_counters_last_no_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_order_code_counters_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_code_counters")),
        sa.UniqueConstraint(
            "unit_id",
            "member_code",
            "day",
            name="uq_order_code_counters_unit_id_member_code_day",
        ),
    )

    op.create_table(
        ORDER_WORK_RULES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column(
            "node_type", _enum(NODE_TYPE_VALUES, "order_work_rule_node_type", 10), nullable=False
        ),
        sa.Column(
            "video_type", _enum(VIDEO_TYPE_VALUES, "order_work_rule_video_type", 5), nullable=True
        ),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_order_work_rules_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_order_work_rules_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_work_rules")),
    )
    op.create_index(op.f("ix_order_work_rules_work_type_id"), ORDER_WORK_RULES, ["work_type_id"])
    op.create_index(
        "uq_order_work_rules_unit_node_type",
        ORDER_WORK_RULES,
        ["unit_id", "node_type", "video_type"],
        unique=True,
        postgresql_where=sa.text("video_type IS NOT NULL"),
        sqlite_where=sa.text("video_type IS NOT NULL"),
    )
    op.create_index(
        "uq_order_work_rules_unit_node_default",
        ORDER_WORK_RULES,
        ["unit_id", "node_type"],
        unique=True,
        postgresql_where=sa.text("video_type IS NULL"),
        sqlite_where=sa.text("video_type IS NULL"),
    )


def _seed() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    units = sa.table(
        ORG_UNITS,
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("settings", sa.JSON()),
        sa.column("active", sa.Boolean()),
    )
    for code, name, settings in UNIT_SEED:
        bind.execute(
            sa.insert(units).values(
                id=_seed_id("unit", code),
                code=code,
                name=name,
                settings=sa.text(f"'{settings}'"),
                active=True,
            )
        )

    # Every existing account is PR. Inactive and revoked users included: the
    # tag records what they were, and reactivation must not change that.
    members = sa.table(
        ORG_UNIT_MEMBERS,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("role", sa.String()),
        sa.column("is_lead", sa.Boolean()),
        sa.column("joined_at", sa.DateTime(timezone=True)),
    )
    pr_unit_id = _seed_id("unit", "PR")
    users = sa.table(USERS, sa.column("id", sa.Uuid()))
    user_ids = bind.execute(sa.select(users.c.id)).scalars().all()
    if user_ids:
        bind.execute(
            sa.insert(members),
            [
                {
                    "id": uuid.uuid4(),
                    "unit_id": pr_unit_id,
                    "user_id": user_id,
                    "role": "MEMBER",
                    "is_lead": False,
                    "joined_at": now,
                }
                for user_id in user_ids
            ],
        )

    work_types = sa.table(
        WORK_TYPES,
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("category", sa.String()),
        sa.column("default_unit", sa.String()),
    )
    rules = sa.table(
        ORDER_WORK_RULES,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("node_type", sa.String()),
        sa.column("video_type", sa.String()),
        sa.column("work_type_id", sa.Uuid()),
        sa.column("is_active", sa.Boolean()),
    )
    ads_unit_id = _seed_id("unit", "ADS")
    for code, name, node_type in WORK_TYPE_SEED:
        existing = bind.execute(
            sa.select(work_types.c.id).where(work_types.c.code == code)
        ).scalar_one_or_none()
        if existing is None:
            existing = _seed_id("work_type", code)
            bind.execute(
                sa.insert(work_types).values(
                    id=existing,
                    code=code,
                    name=name,
                    category="PRODUCTION",
                    # ITEM for all three: a unit is the department's choice to
                    # make later, and the existing suites expect new types to
                    # start there.
                    default_unit="ITEM",
                )
            )
        bind.execute(
            sa.insert(rules).values(
                id=_seed_id("rule", node_type),
                unit_id=ads_unit_id,
                node_type=node_type,
                video_type=None,
                work_type_id=existing,
                is_active=True,
            )
        )


def downgrade() -> None:
    for table in (
        ORDER_WORK_RULES,
        ORDER_APPROVALS,
        ORDER_EVENTS,
        ORDER_SUBMISSIONS,
        ORDER_NODES,
        ORDER_CODE_COUNTERS,
        ORDERS,
        ORG_UNIT_MEMBERS,
        ORG_UNITS,
    ):
        op.drop_table(table)
    # The three seeded ``pr_work_types`` stay. They are ordinary taxonomy rows,
    # work may already count against them, and the next upgrade finds them by
    # code rather than inserting twice.
