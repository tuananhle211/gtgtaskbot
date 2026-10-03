"""Supporting material a reviewer consults while judging a piece of content.

Step 1F.2.3e. One table, ``pr_content_resources``.

What this is, and what it is emphatically not
----------------------------------------------

A resource is **input**: the client brief, the moodboard, the reference video,
the study a health claim rests on, the brand guideline. It is what somebody reads
*in order to* write the script, and what a reviewer needs open in another tab in
order to say whether the script is right.

``pr_production_submissions`` is **output**: the finished cut, the exported
artwork, the thing internal review is judging. The two are never the same row,
never the same table and never the same section of the screen, and this docstring
is where that boundary is written down:

===================================  =============================
Content resource                     Production submission
===================================  =============================
Brief khách hàng                     Video hoàn thiện v1
Moodboard chiến dịch                 File thiết kế đã xuất
Video tham khảo                      Bản dựng gửi duyệt nội bộ
Nguồn cho một khẳng định y khoa      —
===================================  =============================

Reusing the submissions table for references was considered and is the mistake
this file exists to prevent: a submission carries a ``submission_no`` allocated
under a lock, a ``content_version_id`` saying which draft it was cut from, and a
producer - none of which a moodboard has, and all of which would have to become
nullable to accommodate one. What survives that is a table where half the columns
are meaningless for half the rows, and an internal-review screen that cannot tell
which rows it is meant to be judging.

Mutable, and not versioned
--------------------------

Unlike ``pr_content_versions`` and ``pr_production_submissions``, a resource is
edited in place and deleted outright. It is a pointer to material that lives
elsewhere, not a record of something that happened - fixing a typo in a label is
a correction, not a new fact, and keeping the old label would be keeping a
mistake. What somebody *did* to a resource is in the audit trail, which is where
history belongs when the row itself is not history.

Hence ``TimestampMixin``: ``updated_at`` means something here, and does not on
the append-only tables beside it.

Nothing is fetched
------------------

MeoBot never opens a resource. Not to make a thumbnail, not to check a link is
alive, not to feed it to a model. ``location`` is validated for **shape** by
:func:`~meobot.domain.pr.resources.normalize_resource_location` and then stored
verbatim; every read of it is a person clicking or copying it. That is what keeps
this feature away from SSRF, from prompt injection, and from quietly downloading
a client's private Drive document.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.domain.pr.models import PrContentResourceType


class PrContentResource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One reference attached to one content item."""

    __tablename__ = "pr_content_resources"
    __table_args__ = (
        CheckConstraint(_not_empty("label"), name="label_not_empty"),
        CheckConstraint(_not_empty("location"), name="location_not_empty"),
        # The only query this table serves: "the resources of this item", asked
        # by the detail page and by nothing else. ``required_for_review`` is in
        # the index because it is the first sort key and it is two values wide,
        # so the ordering comes off the index rather than out of a sort - and
        # because at zero extra cost it means the common read touches one
        # structure. No index on ``resource_type``: nothing filters by it.
        Index(
            "ix_pr_content_resources_content_required",
            "content_id",
            "required_for_review",
        ),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    resource_type: Mapped[PrContentResourceType] = mapped_column(
        value_enum(PrContentResourceType, name="pr_content_resource_type", length=20),
        nullable=False,
    )
    #: What a person calls it - *"Ảnh packshot sản phẩm"*. Required, because a
    #: list of eight rows titled with their own URLs is a list nobody can scan.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The URL or the NAS path, exactly as it was validated - never rewritten.
    #: ``Text`` rather than a bounded column for the reason the submissions
    #: table uses one: a signed Drive URL is long, and the length limit that
    #: matters is the validator's, which returns an error somebody can read.
    location: Mapped[str] = mapped_column(Text, nullable=False)
    #: *"dùng packshot số 3"*. Optional, plain text, never rendered as markup.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Step 1F.2.3e. **A reviewer-attention signal, and nothing else.** It moves
    #: the row to the top of the list and gives it a badge. It gates no
    #: transition, blocks no approval and is read by no workflow rule - see
    #: ``docs/pr/STEP_1F23E_CONTENT_TYPES_AND_RESOURCES.md``. Making it a
    #: precondition for approving would be new approval-gating state, which this
    #: step deliberately does not add.
    required_for_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Who attached it. ``RESTRICT`` like every other PR reference to a person:
    #: a user row is not deleted out from under the history of what they did.
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )


__all__: list[str] = ["PrContentResource"]
