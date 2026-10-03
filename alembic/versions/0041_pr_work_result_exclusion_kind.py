"""Why an excluded work result is out: ``pr_work_results.exclusion_kind``.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-14

One nullable column and one CHECK. Additive; no row is rewritten.

Why
---

``0039`` gave a result ``PENDING / COUNTED / EXCLUDED`` and a free-text
``excluded_reason``. Three different acts wrote ``EXCLUDED``: an administrator's
*Xóa kết quả*, a validator's *Từ chối / Không ghi nhận*, and the projector
reversing a result whose source approval was withdrawn. The projector could not
tell them apart, and treated every ``EXCLUDED`` row as restorable - so a
validator's reviewed rejection came back on the next content projection.

``exclusion_kind`` records **which** act it was, as a small vocabulary
(:class:`~meobot.domain.pr.work_results.PrWorkExclusionKind`):
``ADMIN_REMOVED`` (a projection may re-evaluate it), ``VALIDATOR_REJECTED`` (it
may not; only *Xem xét lại* releases it) and ``SOURCE_REVERSED`` (current source
truth decides). The CHECK says a row that is not ``EXCLUDED`` carries no kind.

Existing rows
-------------

**No backfill.** A row excluded before this revision keeps ``NULL``. Its
free-text reason has a recognisable prefix in most cases (*"Quản trị viên gỡ
kết quả"*, *"…: nguồn không còn xác nhận công việc này"*), but a validator's
text is whatever they typed and could coincide with either, and the audit trail
would have to be matched by timestamp to tell a restored-then-rejected row from
a removed one. Guessing would fabricate a decision. ``NULL`` therefore means
**legacy, author not recorded**, and the projector treats it as it treats a
validator's rejection - it leaves the row alone - so nothing a person decided
is silently reversed. A validator releases such a row with *Xem xét lại*.

At the time of writing ``0039`` and ``0040`` have not been deployed, so the
population this applies to is expected to be empty.

Downgrade
----------

Drops the column and the CHECK. The distinction is lost; every excluded row
becomes restorable again, which is the behaviour ``0040`` had.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_RESULTS = "pr_work_results"
EXCLUSION_KIND_VALUES = ("ADMIN_REMOVED", "VALIDATOR_REJECTED", "SOURCE_REVERSED")


def upgrade() -> None:
    op.add_column(
        WORK_RESULTS,
        sa.Column(
            "exclusion_kind",
            sa.Enum(
                *EXCLUSION_KIND_VALUES,
                name="pr_work_result_exclusion_kind",
                native_enum=False,
                length=20,
            ),
            nullable=True,
        ),
    )
    # Bare name: the metadata naming convention prefixes ``ck_<table>_`` on
    # both the create and the drop, exactly as ``0039`` relies on.
    op.create_check_constraint(
        "exclusion_kind_matches_status",
        WORK_RESULTS,
        "status = 'EXCLUDED' OR exclusion_kind IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("exclusion_kind_matches_status", WORK_RESULTS, type_="check")
    op.drop_column(WORK_RESULTS, "exclusion_kind")
