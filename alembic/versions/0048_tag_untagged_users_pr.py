"""Streams: every account still untagged today is tagged PR.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-08

Data only. No table, column or index changes.

Why
---

Until now a user with **no** ``org_unit_members`` row at all was treated as a
PR ``MEMBER`` (the "legacy rule"). The rule is gone: an untagged account now
sees no stream until a team lead, an ADMIN or the OWNER tags it, and joining by
invite creates no tag. So that nobody who works in PR today silently loses
access, this revision writes the tag the rule used to imply:

* every **active** user (``users.active``) with **no** ``org_unit_members``
  row at all - open or closed; a closed row is a decision somebody made and is
  left alone - gets one open PR ``MEMBER`` row;
* users created after this revision runs are untagged.

Ids are ``uuid5`` of a fixed namespace and the user id, so the rows this
revision wrote can be told apart from every tag written by a person.

Downgrade
---------

Deletes exactly the rows this revision inserted (the PR rows whose id is the
deterministic id of their user), whatever happened to them since. Everybody
untagged by that is back on the legacy rule the downgraded code applies.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_UNIT_MEMBERS = "org_unit_members"
USERS = "users"

#: 0042's namespace: the PR unit's seeded id.
UNIT_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004200420042")
#: This revision's own namespace: the ids of the tags it writes.
TAG_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004800480048")


def pr_unit_id() -> uuid.UUID:
    return uuid.uuid5(UNIT_SEED_NAMESPACE, "unit:PR")


def seeded_tag_id(user_id: uuid.UUID) -> uuid.UUID:
    """The id 0048 gives the PR tag of ``user_id``."""
    return uuid.uuid5(TAG_SEED_NAMESPACE, f"pr-member:{user_id}")


def _members() -> sa.TableClause:
    return sa.table(
        ORG_UNIT_MEMBERS,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("role", sa.String()),
        sa.column("is_lead", sa.Boolean()),
        sa.column("joined_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    bind = op.get_bind()
    units = sa.table("org_units", sa.column("id", sa.Uuid()))
    unit_id = pr_unit_id()
    if bind.execute(sa.select(units.c.id).where(units.c.id == unit_id)).first() is None:
        # A database whose PR row is missing never ran 0042's seed; nothing to tag.
        return
    members = _members()
    users = sa.table(USERS, sa.column("id", sa.Uuid()), sa.column("active", sa.Boolean()))
    untagged = (
        bind.execute(
            sa.select(users.c.id)
            .where(
                users.c.active.is_(True),
                ~sa.exists(sa.select(members.c.id).where(members.c.user_id == users.c.id)),
            )
            .order_by(users.c.id)
        )
        .scalars()
        .all()
    )
    if not untagged:
        return
    now = bind.execute(sa.select(sa.func.now())).scalar_one()
    bind.execute(
        sa.insert(members),
        [
            {
                "id": seeded_tag_id(user_id),
                "unit_id": unit_id,
                "user_id": user_id,
                "role": "MEMBER",
                "is_lead": False,
                "joined_at": now,
            }
            for user_id in untagged
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    members = _members()
    rows = bind.execute(
        sa.select(members.c.id, members.c.user_id).where(members.c.unit_id == pr_unit_id())
    ).all()
    ours = [row_id for row_id, user_id in rows if row_id == seeded_tag_id(user_id)]
    if ours:
        bind.execute(sa.delete(members).where(members.c.id.in_(ours)))
