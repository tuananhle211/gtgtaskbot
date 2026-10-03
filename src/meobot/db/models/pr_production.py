"""One handover of a finished production file. Append-only.

Step 1F.2.3. ``pr_production_submissions`` is to a cut what
``pr_content_versions`` is to a script, and the reasoning is the same one Step
1D2 wrote down for drafts: the thing a reviewer looked at must still exist,
unchanged, after they have decided about it.

Why a table rather than a column on the content item
----------------------------------------------------

A ``production_url`` column on ``pr_content_items`` would have been one
migration and one form field, and it would have destroyed its own history on the
second submission. The ordinary sequence in this workflow is:

```
submit v1 -> internal review: yêu cầu sửa -> back to PRODUCTION
submit v2 -> internal review: duyệt      -> READY_TO_PUBLISH
```

With a column, ``UPDATE`` on the second submit erases the file the first
decision was about, and the "yêu cầu sửa" event in ``pr_approval_events`` starts
pointing at a cut that was never rejected. Both submissions are true, each about
its own file, and both stay - which is what makes
``pr_approval_events.production_submission_id`` meaningful.

So: no ``updated_at``, no ``UPDATE`` anywhere in the module, and a new row per
handover. ``submission_no`` numbers them per content item, allocated under the
content row's lock exactly as ``version_no`` is, so "the latest submission" is a
question with one answer and not a race between two timestamps.

Two people, both recorded
-------------------------

``producer_user_id`` is whose work this is; ``submitted_by_user_id`` is who
pressed the button. They are almost always the same person and the exception is
the point: when a manager submits on a producer's behalf, a record that kept only
one of the two would either lose the author or lose the actor. ``pr_approval_events``
made the same choice with ``reviewer_user_id`` against the audit trail's actor.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.domain.pr.models import PrProductionArtifactType


class PrProductionSubmission(Base, UUIDPrimaryKeyMixin):
    """One production file handed over for internal review."""

    __tablename__ = "pr_production_submissions"
    __table_args__ = (
        CheckConstraint("submission_no >= 1", name="submission_no_positive"),
        CheckConstraint(_not_empty("location"), name="location_not_empty"),
        # Unique, and therefore also *the* index for "the submissions of this
        # item, newest first" and "does submission 2 exist" - the same reasoning
        # ``uq_pr_content_versions_content_version`` is built on. Two concurrent
        # submits racing for number 2 do not both win; the loser's transaction
        # fails rather than silently becoming a second "latest".
        Index(
            "uq_pr_production_submissions_content_no",
            "content_id",
            "submission_no",
            unique=True,
        ),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: Which script draft this cut was made from. Required: a video is a version
    #: of *something*, and an internal reviewer who spots a line that should not
    #: be there needs to know which script it came out of.
    content_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_versions.id", ondelete=RESTRICT), nullable=False
    )
    #: 1, 2, 3 … per content item. Allocated under the content row's lock.
    submission_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Whose production work this is - ``pr_content_items.producer_user_id`` at
    #: the moment of submission, copied so a later reassignment does not rewrite
    #: who made this file.
    producer_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: Who actually pressed submit. See the module docstring.
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    artifact_type: Mapped[PrProductionArtifactType] = mapped_column(
        value_enum(PrProductionArtifactType, name="pr_production_artifact_type", length=20),
        nullable=False,
    )
    #: The URL or the path, exactly as it was validated - never rewritten. Text
    #: rather than ``String(n)``: a signed Drive URL is long, the length rule is
    #: :data:`~meobot.domain.pr.production.MAX_LOCATION_LENGTH`, and a column
    #: limit that disagreed with it would fail as a database error instead of a
    #: sentence.
    location: Mapped[str] = mapped_column(Text, nullable=False)
    #: What to call this file on screen - "bản dựng 30s", "có phụ đề".
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Anything the reviewer should know before watching. Prose; nothing parses it.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: No ``updated_at`` beside it, by design. This table is append-only.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


__all__: list[str] = ["PrProductionSubmission"]
