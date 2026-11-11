"""Hộp chat CSKH (Pancake + POS Cake + Zalo telesale) replaces the 0094 agent replies.

Revision ID: 0102
Revises: 0101
Create Date: 2026-11-11

Ported from the prototype ``hop-chat-apexmed``: the pages of Pancake read into
one inbox, POS Cake confirmations, QR codes, quick replies, labels, and the
telesales' Zalo nicks matched to the page conversations. See
:mod:`meobot.db.models.cskh`.

* drops ``pancake_replies`` / ``pancake_conversations`` / ``pancake_pages``
  (the 0094 agent suggestions - no customer data was kept there);
* creates the 17 ``cskh_*`` tables;
* permissions (module ``CSKH``): an old ``REPLY`` grant becomes ``INBOX ALL``
  (department cells and personal rows alike, scope kept when NONE); ``MANAGE``
  stays; seeds Phòng Tư vấn online (``TVOL``: HEAD ``INBOX ALL`` + ``ZALO ALL``,
  MEMBER ``INBOX OWN``) and Telesales (``TELE``: HEAD ``ZALO ALL`` + ``INBOX ALL``,
  MEMBER ``ZALO OWN``) where they have no CSKH cell yet.

Downgrade
---------

Drops the ``cskh_*`` tables (every conversation, message, confirmation and
Zalo thread is lost), turns ``INBOX`` back into ``REPLY`` and removes ``ZALO``,
then recreates the empty 0094 tables.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_TABLES = (
    "cskh_zalo_messages",
    "cskh_zalo_threads",
    "cskh_zalo_accounts",
    "cskh_orders",
    "cskh_ads",
    "cskh_messages",
    "cskh_conversations",
    "cskh_quick_replies",
    "cskh_pancake_users",
    "cskh_pancake_tags",
    "cskh_page_labels",
    "cskh_labels",
    "cskh_page_members",
    "cskh_pages",
    "cskh_pos_shops",
    "cskh_pos_keys",
    "cskh_pancake_accounts",
)

#: ``department code -> position -> {permission: scope}`` seeded when the
#: department has no CSKH cell yet.
SEED: dict[str, dict[str, dict[str, str]]] = {
    "TVOL": {"HEAD": {"INBOX": "ALL", "ZALO": "ALL"}, "MEMBER": {"INBOX": "OWN"}},
    "TELE": {"HEAD": {"ZALO": "ALL", "INBOX": "ALL"}, "MEMBER": {"ZALO": "OWN"}},
}


def _times() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _fk(
    table: str, column: str, target: str, ondelete: str, target_column: str = "id"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.{target_column}"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def _false() -> sa.TextClause:
    return sa.text("false")


def upgrade() -> None:
    op.drop_table("pancake_replies")
    op.drop_table("pancake_conversations")
    op.drop_table("pancake_pages")

    op.create_table(
        "cskh_pancake_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(12), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("expires_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _fk("cskh_pancake_accounts", "created_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_pancake_accounts")),
    )
    op.create_table(
        "cskh_pos_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("key_encrypted", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(12), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("shop_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _fk("cskh_pos_keys", "created_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_pos_keys")),
    )
    op.create_table(
        "cskh_pos_shops",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("pages", _JSON, nullable=False),
        *_times(),
        _fk("cskh_pos_shops", "key_id", "cskh_pos_keys", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_pos_shops")),
    )
    op.create_table(
        "cskh_pages",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("pancake_account_id", sa.Uuid(), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_hint", sa.String(12), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("marketer_user_id", sa.Uuid(), nullable=True),
        sa.Column("pos_shop_id", sa.String(40), nullable=True),
        sa.Column("polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_error", sa.String(300), nullable=True),
        sa.Column("meta_at", sa.DateTime(timezone=True), nullable=True),
        *_times(),
        _fk("cskh_pages", "pancake_account_id", "cskh_pancake_accounts", "SET NULL"),
        _fk("cskh_pages", "marketer_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_pages")),
    )
    op.create_table(
        "cskh_page_members",
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        *_times(),
        _fk("cskh_page_members", "page_id", "cskh_pages", "CASCADE"),
        _fk("cskh_page_members", "user_id", "users", "CASCADE"),
        sa.PrimaryKeyConstraint("page_id", "user_id", name=op.f("pk_cskh_page_members")),
    )
    op.create_table(
        "cskh_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("color", sa.String(12), nullable=False),
        *_times(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_labels")),
        sa.UniqueConstraint("name", name=op.f("uq_cskh_labels_name")),
    )
    op.create_table(
        "cskh_page_labels",
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=False),
        _fk("cskh_page_labels", "page_id", "cskh_pages", "CASCADE"),
        _fk("cskh_page_labels", "label_id", "cskh_labels", "CASCADE"),
        sa.PrimaryKeyConstraint("page_id", "label_id", name=op.f("pk_cskh_page_labels")),
    )
    op.create_table(
        "cskh_pancake_tags",
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("tag_id", sa.String(60), nullable=False),
        sa.Column("text", sa.String(200), nullable=False),
        sa.Column("color", sa.String(20), nullable=False),
        _fk("cskh_pancake_tags", "page_id", "cskh_pages", "CASCADE"),
        sa.PrimaryKeyConstraint("page_id", "tag_id", name=op.f("pk_cskh_pancake_tags")),
    )
    op.create_table(
        "cskh_pancake_users",
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _fk("cskh_pancake_users", "page_id", "cskh_pages", "CASCADE"),
        sa.PrimaryKeyConstraint("page_id", "user_id", name=op.f("pk_cskh_pancake_users")),
    )
    op.create_table(
        "cskh_quick_replies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("shortcut", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_id", sa.String(40), nullable=True),
        *_times(),
        _fk("cskh_quick_replies", "page_id", "cskh_pages", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_quick_replies")),
    )
    op.create_index(
        "uq_cskh_quick_replies_shared",
        "cskh_quick_replies",
        ["shortcut"],
        unique=True,
        postgresql_where=sa.text("page_id IS NULL"),
        sqlite_where=sa.text("page_id IS NULL"),
    )
    op.create_index(
        "uq_cskh_quick_replies_page",
        "cskh_quick_replies",
        ["shortcut", "page_id"],
        unique=True,
        postgresql_where=sa.text("page_id IS NOT NULL"),
        sqlite_where=sa.text("page_id IS NOT NULL"),
    )
    op.create_table(
        "cskh_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("psid", sa.String(80), nullable=False),
        sa.Column("customer_name", sa.String(300), nullable=False),
        sa.Column("customer_key", sa.String(300), nullable=False),
        sa.Column("last_text", sa.String(500), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_out", sa.Boolean(), server_default=_false(), nullable=False),
        sa.Column("last_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pancake_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread", sa.Integer(), nullable=False),
        sa.Column("callback", sa.Boolean(), server_default=_false(), nullable=False),
        sa.Column("last_auto_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pancake_tags", _JSON, nullable=False),
        sa.Column("assignee_ids", _JSON, nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phones", _JSON, nullable=False),
        sa.Column("phone_digits", sa.Text(), nullable=False),
        sa.Column("ad_id", sa.String(60), nullable=True),
        sa.Column("ads", _JSON, nullable=False),
        sa.Column("qrs", _JSON, nullable=False),
        sa.Column("qr_texts", sa.Text(), nullable=False),
        *_times(),
        _fk("cskh_conversations", "page_id", "cskh_pages", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_conversations")),
        sa.UniqueConstraint("page_id", "psid", name=op.f("uq_cskh_conversations_page_id_psid")),
    )
    op.create_index("ix_cskh_conversations_last_at", "cskh_conversations", ["last_at"])
    op.create_index(
        "ix_cskh_conversations_page_last_at", "cskh_conversations", ["page_id", "last_at"]
    )
    op.create_table(
        "cskh_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("mid", sa.String(200), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_key", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(300), nullable=False),
        sa.Column("attachments", _JSON, nullable=False),
        sa.Column("qr", _JSON, nullable=True),
        sa.Column("sender_user_id", sa.Uuid(), nullable=True),
        sa.Column("sender_name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("cskh_messages", "conversation_id", "cskh_conversations", "CASCADE"),
        _fk("cskh_messages", "sender_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_messages")),
        sa.UniqueConstraint("mid", name=op.f("uq_cskh_messages_mid")),
    )
    op.create_index("ix_cskh_messages_conversation_at", "cskh_messages", ["conversation_id", "at"])
    op.create_table(
        "cskh_ads",
        sa.Column("ad_id", sa.String(60), nullable=False),
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.String(60), nullable=False),
        sa.Column("campaign_name", sa.String(500), nullable=False),
        sa.Column("account_id", sa.String(60), nullable=False),
        sa.Column("found", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ad_id", name=op.f("pk_cskh_ads")),
    )
    op.create_table(
        "cskh_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.String(40), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("confirm_type", sa.String(6), nullable=False),
        sa.Column("pos_shop_id", sa.String(40), nullable=True),
        sa.Column("pos_order_id", sa.String(40), nullable=True),
        sa.Column("pos_order_no", sa.String(40), nullable=True),
        sa.Column("value", sa.Numeric(14, 0), nullable=True),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("phone_key", sa.String(12), nullable=False),
        sa.Column("customer_name", sa.String(300), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column("content_name", sa.String(300), nullable=False),
        sa.Column("order_status", sa.String(120), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("marketer_user_id", sa.Uuid(), nullable=True),
        sa.Column("marketer_name", sa.String(200), nullable=False),
        sa.Column("creator_user_id", sa.Uuid(), nullable=True),
        sa.Column("creator_name", sa.String(200), nullable=False),
        sa.Column("telesale_user_id", sa.Uuid(), nullable=True),
        sa.Column("telesale_name", sa.String(200), nullable=False),
        sa.Column("customer_code", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("dup_info", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _fk("cskh_orders", "conversation_id", "cskh_conversations", "RESTRICT"),
        _fk("cskh_orders", "marketer_user_id", "users", "SET NULL"),
        _fk("cskh_orders", "creator_user_id", "users", "SET NULL"),
        _fk("cskh_orders", "telesale_user_id", "users", "SET NULL"),
        _fk("cskh_orders", "created_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_orders")),
    )
    op.create_index("ix_cskh_orders_conversation", "cskh_orders", ["conversation_id", "created_at"])
    op.create_index("ix_cskh_orders_phone_key", "cskh_orders", ["phone_key"])
    op.create_index("ix_cskh_orders_customer_code", "cskh_orders", ["customer_code"])
    op.create_table(
        "cskh_zalo_accounts",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("avatar", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("status_note", sa.String(300), nullable=False),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        *_times(),
        _fk("cskh_zalo_accounts", "user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_zalo_accounts")),
    )
    op.create_table(
        "cskh_zalo_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(40), nullable=False),
        sa.Column("peer_uid", sa.String(60), nullable=False),
        sa.Column("is_group", sa.Boolean(), server_default=_false(), nullable=False),
        sa.Column("peer_name", sa.String(200), nullable=False),
        sa.Column("peer_avatar", sa.Text(), nullable=False),
        sa.Column("last_text", sa.String(500), nullable=False),
        sa.Column("last_out", sa.Boolean(), server_default=_false(), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("customer_code", sa.String(40), nullable=False),
        sa.Column("link_how", sa.String(300), nullable=False),
        sa.Column("link_kind", sa.String(6), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _fk("cskh_zalo_threads", "account_id", "cskh_zalo_accounts", "CASCADE"),
        _fk("cskh_zalo_threads", "conversation_id", "cskh_conversations", "SET NULL"),
        _fk("cskh_zalo_threads", "linked_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_zalo_threads")),
        sa.UniqueConstraint(
            "account_id", "peer_uid", name=op.f("uq_cskh_zalo_threads_account_id_peer_uid")
        ),
    )
    op.create_index("ix_cskh_zalo_threads_last_at", "cskh_zalo_threads", ["last_at"])
    op.create_index("ix_cskh_zalo_threads_conversation", "cskh_zalo_threads", ["conversation_id"])
    op.create_table(
        "cskh_zalo_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("msg_id", sa.String(120), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("sender_name", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_digits", sa.Text(), nullable=False),
        sa.Column("text_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        _fk("cskh_zalo_messages", "thread_id", "cskh_zalo_threads", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cskh_zalo_messages")),
        sa.UniqueConstraint("msg_id", name=op.f("uq_cskh_zalo_messages_msg_id")),
    )
    op.create_index("ix_cskh_zalo_messages_thread_at", "cskh_zalo_messages", ["thread_id", "at"])

    # --- permissions ---------------------------------------------------------------------
    op.execute(
        sa.text(
            "UPDATE department_permissions SET permission = 'INBOX' "
            "WHERE module = 'CSKH' AND permission = 'REPLY'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE user_permissions SET permission = 'INBOX' "
            "WHERE module = 'CSKH' AND permission = 'REPLY'"
        )
    )
    bind = op.get_bind()
    now = datetime.now(UTC)
    departments = bind.execute(
        sa.text("SELECT id, code FROM departments WHERE code IN ('TVOL', 'TELE')")
    ).all()
    grants = sa.table(
        "department_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows: list[dict[str, object]] = []
    for department_id, code in departments:
        taken = bind.execute(
            sa.text(
                "SELECT count(*) FROM department_permissions "
                "WHERE department_id = :d AND module = 'CSKH'"
            ),
            {"d": department_id},
        ).scalar_one()
        if taken:
            continue
        for position, cells in SEED[code].items():
            rows += [
                {
                    "id": uuid.uuid4(),
                    "department_id": department_id,
                    "position": position,
                    "module": "CSKH",
                    "permission": permission,
                    "scope": scope,
                    "created_at": now,
                    "updated_at": now,
                }
                for permission, scope in cells.items()
            ]
    if rows:
        op.bulk_insert(grants, rows)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM department_permissions WHERE module = 'CSKH' AND permission = 'ZALO'")
    )
    op.execute(
        sa.text("DELETE FROM user_permissions WHERE module = 'CSKH' AND permission = 'ZALO'")
    )
    op.execute(
        sa.text(
            "UPDATE department_permissions SET permission = 'REPLY', scope = 'ALL' "
            "WHERE module = 'CSKH' AND permission = 'INBOX'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE user_permissions SET permission = 'REPLY' "
            "WHERE module = 'CSKH' AND permission = 'INBOX'"
        )
    )
    for table in _TABLES:
        op.drop_table(table)

    # The 0094 tables, empty.
    op.create_table(
        "pancake_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("agent_slug", sa.String(40), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_hint", sa.String(12), nullable=False),
        sa.Column("webhook_key", sa.String(64), nullable=False),
        sa.Column("handoff_tag_ids", _JSON, nullable=False),
        sa.Column(
            "stop_when_assigned", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_ok", sa.Boolean(), nullable=True),
        sa.Column("last_check_message", sa.String(300), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        sa.CheckConstraint(
            "length(trim(page_id)) > 0", name=op.f("ck_pancake_pages_page_id_not_empty")
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_pancake_pages_name_not_empty")),
        _fk("pancake_pages", "created_by_user_id", "users", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pancake_pages")),
        sa.UniqueConstraint("page_id", name=op.f("uq_pancake_pages_page_id")),
        sa.UniqueConstraint("webhook_key", name=op.f("uq_pancake_pages_webhook_key")),
    )
    op.create_table(
        "pancake_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_row_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.String(160), nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(40), nullable=True),
        sa.Column("tag_ids", _JSON, nullable=False),
        sa.Column("assignee_ids", _JSON, nullable=False),
        sa.Column("seen_message_id", sa.String(160), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_ids", _JSON, nullable=False),
        *_times(),
        _fk("pancake_conversations", "page_row_id", "pancake_pages", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pancake_conversations")),
        sa.UniqueConstraint(
            "page_row_id",
            "conversation_id",
            name=op.f("uq_pancake_conversations_page_row_id_conversation_id"),
        ),
    )
    op.create_table(
        "pancake_replies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_row_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_row_id", sa.Uuid(), nullable=False),
        sa.Column("customer_message_id", sa.String(160), nullable=False),
        sa.Column("customer_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("suggested_text", sa.Text(), nullable=True),
        sa.Column("sent_text", sa.Text(), nullable=True),
        sa.Column("handoff", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("handoff_reason", sa.String(200), nullable=True),
        sa.Column("violations", _JSON, nullable=False),
        sa.Column("review_passed", sa.Boolean(), nullable=True),
        sa.Column("review_note", sa.String(300), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_id", sa.String(160), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _fk("pancake_replies", "page_row_id", "pancake_pages", "CASCADE"),
        _fk("pancake_replies", "conversation_row_id", "pancake_conversations", "CASCADE"),
        _fk("pancake_replies", "decided_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pancake_replies")),
        sa.UniqueConstraint(
            "page_row_id",
            "customer_message_id",
            name=op.f("uq_pancake_replies_page_row_id_customer_message_id"),
        ),
    )
    op.create_index(
        "ix_pancake_replies_status_created", "pancake_replies", ["status", "created_at"]
    )
    op.create_index(
        "ix_pancake_replies_conversation",
        "pancake_replies",
        ["conversation_row_id", "created_at"],
    )
