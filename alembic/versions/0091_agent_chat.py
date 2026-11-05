"""Đội agent: ``agent_profiles``, ``agent_conversations``, ``agent_messages``.

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-05

People chat with the company's Hermes agents inside APM (plan
``docs/ai-assistant/AGENT_TEAM_CHAT_PLAN.md``):

* ``agent_profiles`` - the 15 agents (slug = the Hermes profile), their
  group, badge, on/off, and who may chat with each: department ids, people,
  or everyone. Seeded here; the departments are found by code (``BGD``,
  ``ADS``, ``BT``, ``TK``, ``D``, ``PR``, ``MEDIA``, ``TCKT``, ``SALE``) and a
  code the database does not have is simply skipped;
* ``agent_conversations`` - one person's thread with one agent, its Hermes
  transcript id ``apm-<id>``;
* ``agent_messages`` - the question and the answer (status, tools used,
  duration).

Downgrade
---------

Drops the three tables (every conversation is lost).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROFILES = "agent_profiles"
CONVERSATIONS = "agent_conversations"
MESSAGES = "agent_messages"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

#: (slug, name, title, badge, group, description, department codes, everyone)
_SEED: tuple[tuple[str, str, str, str, str, str, tuple[str, ...], bool], ...] = (
    (
        "chutich",
        "Chủ tịch",
        "Tầm nhìn, chiến lược, phản biện CEO",
        "CT",
        "BOARD",
        "Mục tiêu năm/quý, bản ghi nhớ chiến lược, họp tháng.",
        ("BGD",),
        False,
    ),
    (
        "ceo",
        "CEO",
        "Điều hành, họp giao ban, theo dõi quyết định",
        "CEO",
        "BOARD",
        "Chia mục tiêu cho các giám đốc, gom biên bản họp tuần.",
        ("BGD",),
        False,
    ),
    (
        "cfo",
        "CFO",
        "Tài chính, chi phí quảng cáo, ngân sách",
        "CFO",
        "BOARD",
        "Thực thu, Ads/DT, ngân sách so với thực chi, cảnh báo.",
        ("BGD", "TCKT"),
        False,
    ),
    (
        "cco",
        "CCO",
        "Kinh doanh & khách hàng",
        "CCO",
        "SALES",
        "Doanh thu thực thu, tỉ lệ chốt, Telesale, CSKH, Chat page.",
        ("BGD", "SALE"),
        False,
    ),
    (
        "cmo",
        "CMO",
        "Chiến lược marketing, điều phối 5 agent MKT",
        "CMO",
        "MARKETING",
        "Kế hoạch kênh, BM Hà Nội / Sài Gòn, soát nội dung.",
        ("BGD", "ADS", "BT", "TK", "D", "PR", "MEDIA"),
        False,
    ),
    (
        "mktads",
        "Marketing Ads",
        "Phân tích chiến dịch, đề xuất Tăng / Giữ / Tắt",
        "ADS",
        "MARKETING",
        "Báo cáo chiến dịch, cảnh báo chi bất thường.",
        ("BGD", "ADS"),
        False,
    ),
    (
        "mktnghiencuu",
        "Nghiên cứu thị trường",
        "Đối thủ, giá, xu hướng",
        "NC",
        "MARKETING",
        "Hồ sơ đối thủ có link nguồn, insight khách.",
        ("BGD", "ADS", "BT", "TK", "D", "PR", "MEDIA"),
        False,
    ),
    (
        "mktnoidung",
        "Nội dung",
        "Kịch bản video, bài Fanpage",
        "ND",
        "MARKETING",
        "Kịch bản đúng 8 mục cấm, lịch nội dung tuần.",
        ("BGD", "BT", "PR"),
        False,
    ),
    (
        "mktthietke",
        "Thiết kế",
        "Brief hình ảnh, banner, mã task T",
        "TK",
        "MARKETING",
        "Brief thiết kế, ý tưởng hình, mã task design.",
        ("BGD", "TK"),
        False,
    ),
    (
        "mktdung",
        "Dựng video",
        "Kịch bản dựng, shot list, mã task D",
        "DG",
        "MARKETING",
        "Shot list, định dạng theo kênh, hook 3 giây.",
        ("BGD", "D", "MEDIA"),
        False,
    ),
    (
        "telesale",
        "Telesale",
        "Kịch bản gọi, xử lý từ chối",
        "TS",
        "SALES",
        "Soạn kịch bản, phân loại lead - chỉ soạn nháp.",
        ("BGD", "SALE"),
        False,
    ),
    (
        "cskh",
        "Chăm sóc khách hàng",
        "Hỏi thăm sau dịch vụ, khiếu nại",
        "CS",
        "SALES",
        "Soạn tin hỏi thăm, ghi nhận khiếu nại - chỉ soạn nháp.",
        ("BGD", "SALE"),
        False,
    ),
    (
        "chatpage",
        "Chat page",
        "Trả lời inbox, bình luận Fanpage",
        "CP",
        "SALES",
        "Soạn trả lời inbox, xin SĐT - chỉ soạn nháp.",
        ("BGD", "SALE"),
        False,
    ),
    (
        "nhansu",
        "Nhân sự",
        "Tuyển dụng, onboarding, đào tạo",
        "NS",
        "SUPPORT",
        "JD, câu hỏi phỏng vấn, kế hoạch onboarding.",
        ("BGD",),
        False,
    ),
    (
        "phapche",
        "Pháp chế",
        "Soát quảng cáo, hợp đồng, dữ liệu cá nhân",
        "PC",
        "SUPPORT",
        "Soát nội dung và hợp đồng - không thay ý kiến luật sư.",
        (),
        True,
    ),
)


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


def upgrade() -> None:
    profiles = op.create_table(
        PROFILES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=False),
        sa.Column("badge", sa.String(length=6), nullable=False),
        sa.Column("group_code", sa.String(length=20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("everyone", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("allowed_department_ids", _JSON, nullable=False),
        sa.Column("allowed_user_ids", _JSON, nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(trim(slug)) > 0", name=op.f("ck_agent_profiles_slug_not_empty")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_agent_profiles_name_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_profiles")),
        sa.UniqueConstraint("slug", name=op.f("uq_agent_profiles_slug")),
    )
    op.create_table(
        CONVERSATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("hermes_session_id", sa.String(length=80), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            [f"{PROFILES}.id"],
            name=op.f("fk_agent_conversations_agent_id_agent_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_conversations_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_conversations")),
        sa.UniqueConstraint(
            "hermes_session_id", name=op.f("uq_agent_conversations_hermes_session_id")
        ),
    )
    op.create_index(
        "ix_agent_conversations_user_agent_last",
        CONVERSATIONS,
        ["user_id", "agent_id", "last_message_at"],
    )
    op.create_table(
        MESSAGES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("tools_used", _JSON, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{CONVERSATIONS}.id"],
            name=op.f("fk_agent_messages_conversation_id_agent_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_messages")),
    )
    op.create_index(
        "ix_agent_messages_conversation_created", MESSAGES, ["conversation_id", "created_at"]
    )

    departments = {
        code: str(dept_id)
        for dept_id, code in op.get_bind().execute(sa.text("SELECT id, code FROM departments"))
    }
    op.bulk_insert(
        profiles,
        [
            {
                "id": uuid.uuid4(),
                "slug": slug,
                "name": name,
                "title": title,
                "description": description,
                "badge": badge,
                "group_code": group,
                "sort_order": index,
                "active": True,
                "everyone": everyone,
                "allowed_department_ids": [departments[c] for c in codes if c in departments],
                "allowed_user_ids": [],
            }
            for index, (slug, name, title, badge, group, description, codes, everyone) in enumerate(
                _SEED
            )
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_messages_conversation_created", table_name=MESSAGES)
    op.drop_table(MESSAGES)
    op.drop_index("ix_agent_conversations_user_agent_last", table_name=CONVERSATIONS)
    op.drop_table(CONVERSATIONS)
    op.drop_table(PROFILES)
