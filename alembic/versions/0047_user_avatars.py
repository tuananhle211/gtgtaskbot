"""Profile pictures: ``user_avatars``.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-08

Additive. Nothing PR or Ads reads or writes changes.

``user_avatars``
----------------

At most one row per person (``user_id`` is the primary key, ``ON DELETE
CASCADE`` from ``users``):

* ``content_type`` - ``image/webp``, ``image/jpeg`` or ``image/png`` (CHECK);
* ``data`` - the image bytes, already cropped and resized by the browser;
* ``size_bytes`` - ``1..307200`` (300 KB, CHECK);
* ``version`` - ``>= 1``, bumped on each upload; the ``v`` of the image URL;
* ``updated_at`` - when the picture last changed.

Downgrade
---------

Drops the table: every uploaded picture is lost and everybody shows initials.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_AVATARS = "user_avatars"


def upgrade() -> None:
    op.create_table(
        USER_AVATARS,
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_type IN ('image/webp', 'image/jpeg', 'image/png')",
            name=op.f("ck_user_avatars_content_type_known"),
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 307200", name=op.f("ck_user_avatars_size_range")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_user_avatars_version_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_avatars_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_avatars")),
    )


def downgrade() -> None:
    op.drop_table(USER_AVATARS)
