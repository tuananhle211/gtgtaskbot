"""ORD: the design link is optional on every order.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-14

Drops ``ck_orders_design_link_required_without_design`` (0044): an order whose
process edits without a design step (``D``, ``BD``) no longer has to arrive
with a design link. The API stopped asking for it in the same release.

Downgrade
---------

Puts the check back ``NOT VALID``: rows placed without a link meanwhile stay
as they are, new ones must carry it again.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "ck_orders_design_link_required_without_design"
RULE = "video_type NOT IN ('D', 'BD') OR design_link IS NOT NULL"


def upgrade() -> None:
    # ``op.f``: the name as it stands, past the naming convention's prefix.
    op.drop_constraint(op.f(CHECK), "orders", type_="check")


def downgrade() -> None:
    op.execute(f"ALTER TABLE orders ADD CONSTRAINT {CHECK} CHECK ({RULE}) NOT VALID")
