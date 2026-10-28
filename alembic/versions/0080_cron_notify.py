"""Per-job alert settings for "Tác vụ định kỳ" (chốt 28/10).

``cron_jobs.notify`` switches a job's failure alerts on or off and
``cron_jobs.notify_user_ids`` lists who gets them besides the OWNER (empty =
the OWNER alone). Only failures are sent. The default: Phòng Ads' two Meta
pulls on, told to the heads of the ADS department as seated now; every other
job off (its incidents stay on the screen).

Revision ID: 0080
Revises: 0079
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ADS_JOBS = ("ads.sync_spend", "ads.sync_charges")


def upgrade() -> None:
    op.add_column(
        "cron_jobs",
        sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "cron_jobs",
        sa.Column("notify_user_ids", _JSON, nullable=False, server_default=sa.text("'[]'")),
    )
    bind = op.get_bind()
    heads = [
        str(row[0])
        for row in bind.execute(
            sa.text(
                "SELECT DISTINCT u.id FROM users u"
                " JOIN department_members m ON m.user_id = u.id"
                " JOIN departments d ON d.id = m.department_id"
                " WHERE d.code = 'ADS' AND d.active AND m.position = 'HEAD'"
                " AND m.left_at IS NULL AND u.status = 'active' AND u.deleted_at IS NULL"
            )
        )
    ]
    cron_jobs = sa.table(
        "cron_jobs",
        sa.column("key", sa.String),
        sa.column("notify", sa.Boolean),
        sa.column("notify_user_ids", _JSON),
    )
    op.execute(
        cron_jobs.update()
        .where(cron_jobs.c.key.in_(ADS_JOBS))
        .values(notify=True, notify_user_ids=sorted(heads))
    )


def downgrade() -> None:
    op.drop_column("cron_jobs", "notify_user_ids")
    op.drop_column("cron_jobs", "notify")
