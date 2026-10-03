"""Content → Work auto-provisioning: a mapping the system wrote.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-10

One change, and it is a **relaxation**: ``pr_content_work_rules.created_by_user_id``
becomes nullable. No table is added, no row is written or rewritten, and no
index changes.

Why
---

The projector now provisions the missing work type and its Content → Work
binding itself the first time a content type's deliverable is accepted, so a
new content type never loses its first result to a mapping nobody had
configured yet. That binding is an ordinary ``pr_content_work_rules`` row - it
appears on the mapping screen and an administrator may remap it - but it was
not a person's decision, and the column that named the person who decided it
had nothing honest to hold.

``NULL`` therefore means exactly one thing: **provisioned by the system from
the content workflow**. It is the provenance marker, and it costs no second
column: every rule an administrator writes still names them, and every rule the
projector writes names nobody. The audit row beside it carries the actor and
the reason.

Downgrade
---------

Restores ``NOT NULL`` and therefore **refuses while auto-provisioned rules
exist**: there is no person to attribute them to, and inventing one would be
worse than stopping. An operator who genuinely needs to go back assigns an owner
to those rows first - ``UPDATE pr_content_work_rules SET created_by_user_id =
... WHERE created_by_user_id IS NULL`` - which is a decision, and is why it is
not made here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTENT_WORK_RULES = "pr_content_work_rules"


def upgrade() -> None:
    op.alter_column(
        CONTENT_WORK_RULES,
        "created_by_user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    """Back to ``NOT NULL``. Refuses rather than guessing an owner."""
    remaining = (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM pr_content_work_rules WHERE created_by_user_id IS NULL")
        )
        .scalar_one()
    )
    if remaining:
        raise RuntimeError(
            f"{remaining} auto-provisioned content work rule(s) have no creator; assign "
            "created_by_user_id to them before downgrading past 0040"
        )
    op.alter_column(
        CONTENT_WORK_RULES,
        "created_by_user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
