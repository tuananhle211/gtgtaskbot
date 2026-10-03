"""Two durable things hanging off a content item: its re-cuts, and its links.

Step 1F.2.3f. Both tables exist because a content item is a **reusable asset**
rather than a job that ends when it goes out once. The master cut goes to
Facebook in August; in October a TikTok channel that did not exist then wants the
same piece, so somebody makes a 25-second cut of it and posts that. Before this
step there were exactly two ways to record that, and both were wrong:

* **clone the content.** Two rows, two codes, two scripts to keep in step, and
  every report that counts "how many pieces did we make" answers 2 for one idea;
* **overwrite the production submission.** Destroys the file the internal
  reviewer actually approved, which is the failure
  :mod:`meobot.db.models.pr_production` was written to prevent.

So a third row: a derivative. Same content, new file, no workflow.

``pr_content_derivatives`` - output, and not the master
--------------------------------------------------------

Deliberately **not** ``pr_production_submissions``. That table is the append-only
record of *what an internal reviewer judged*: every column on it -
``submission_no``, ``content_version_id``, ``producer_user_id``,
``submitted_by_user_id`` - exists to answer "which file was this decision
about". A cutdown made three months after approval is judged by nobody, belongs
to no submission sequence, and has no reviewer to be the *n*-th handover to.
Making those four columns nullable to fit it would have left the internal-review
screen unable to tell which of its rows it was meant to be showing.

The lineage link points the other way instead. ``source_submission_id`` is
optional and names the master this was cut from, so *"TikTok cut 25s ← Video
final 60s"* is a stored fact rather than a guess from timestamps. Optional
because it is honestly sometimes unknown: a file re-cut from raw footage on
somebody's laptop did not come from a tracked submission, and requiring a lie
there would be worse than an absent link.

**It points only at a submission, never at another derivative.** A derivative of
a derivative is a real thing and this step does not model it: recursive lineage
needs cycle prevention, depth limits and a display that can draw a tree, and
none of that is worth carrying for a link nobody has asked to follow yet. The
column's type is what enforces the decision - it is a foreign key to
``pr_production_submissions`` and could not name a derivative if somebody tried.

Mutable, unlike the submissions beside it
------------------------------------------

There is an ``updated_at``, and there is an ``UPDATE`` path. A derivative is a
**pointer to a file**, like :class:`~meobot.db.models.pr_content_resource.PrContentResource`
and unlike a submission: fixing a mistyped label or a moved Drive link is a
correction, not a new fact, and the previous values live in the audit trail.
What the service refuses to rewrite is a derivative a publication already points
at - see :class:`~meobot.application.pr_content_asset_service.PrContentAssetService`
- because the row is then part of the answer to "what did we actually post".

``pr_content_destinations`` - where the customer is sent
----------------------------------------------------------

The landing page, the booking page, the product listing. Durable commercial
metadata about the piece, and its own table because it is none of the three
things it sits near:

* not a **resource** - that is material somebody reads *in order to* write and
  review, and a reviewer's brief is not a page a customer visits;
* not a **production output** - nobody produced it, and it is not a thing that
  can be published;
* not a **publication URL** - that is where *this piece* ended up, one row per
  posting. A destination is where the piece **sends people**, and it is the same
  link across every channel and every repost.

Two columns of substance, ``label`` and ``url``, and that is the entire model.
This is not a product catalogue: no SKU, no price, no inventory, no status. If
the team ever needs those, they need a product table that content links to,
which is a different design and a different step.

Foreign keys
------------

``RESTRICT`` throughout, like every other reference in this module. The aggregate
is deleted explicitly and in order by
:class:`~meobot.application.pr_lifecycle_service.PrContentLifecycleService`, and
``RESTRICT`` is the backstop that turns a table somebody forgot to add to that
plan into a failed transaction rather than a half-deleted content item. Step
1F.2.3f adds both tables to that plan, and a test walks the metadata and fails if
it had not been.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.domain.pr.models import PrContentDerivativeType


class PrContentDerivative(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One re-cut, remix or reformat produced from one content item."""

    __tablename__ = "pr_content_derivatives"
    __table_args__ = (
        CheckConstraint(_not_empty("label"), name="label_not_empty"),
        CheckConstraint(_not_empty("location"), name="location_not_empty"),
        # The only query this table serves - "the derivatives of this item,
        # oldest first" - asked by the detail page and by the publication form's
        # output picker. ``created_at`` is in it because it is the sort key, so
        # the ordering comes off the index rather than out of a sort, which is
        # the reasoning ``ix_pr_publications_channel_published`` is built on.
        #
        # **No index on ``derivative_type``.** "Which content has CUTDOWN
        # derivatives" is a question the *column* makes answerable and nothing
        # asks yet; an index for it now would be speculative on a table holding
        # a handful of rows per item.
        Index("ix_pr_content_derivatives_content_created", "content_id", "created_at"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: The master this was cut from, when it was cut from a tracked one.
    #: Nullable on purpose - see the module docstring - and a foreign key to
    #: ``pr_production_submissions`` and nothing else, which is what makes
    #: derivative-of-derivative unrepresentable rather than merely refused.
    source_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_production_submissions.id", ondelete=RESTRICT), nullable=True
    )
    derivative_type: Mapped[PrContentDerivativeType] = mapped_column(
        value_enum(PrContentDerivativeType, name="pr_content_derivative_type", length=20),
        nullable=False,
    )
    #: What a person calls it - *"TikTok cut 25s"*, *"Facebook Reel 30s"*.
    #: Required, because a list of five outputs titled with their own Drive URLs
    #: is a list nobody can pick from - and this list is what the publication
    #: form's "Sản phẩm đã đăng" dropdown is made of.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The URL or the NAS path, exactly as it was validated - never rewritten.
    #: ``Text`` for the reason the submissions table uses one: a signed Drive URL
    #: is long, and the limit that matters is the validator's, which returns a
    #: sentence rather than a database error.
    location: Mapped[str] = mapped_column(Text, nullable=False)
    #: *"dùng bản có logo mới"*. Optional, plain text, never rendered as markup.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Who recorded it. ``RESTRICT`` like every other PR reference to a person.
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )


class PrContentDestination(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One commercial page this content sends people to."""

    __tablename__ = "pr_content_destinations"
    __table_args__ = (
        CheckConstraint(_not_empty("label"), name="label_not_empty"),
        CheckConstraint(_not_empty("url"), name="url_not_empty"),
        # "The destination links of this item", and nothing else.
        Index("ix_pr_content_destinations_content", "content_id"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: *"Landing page dịch vụ"*, *"Trang đặt lịch"*. Required for the same reason
    #: a derivative's is: a column of bare URLs is unreadable.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    #: An ``http(s)`` URL with a host - see
    #: :func:`~meobot.domain.pr.assets.normalize_destination_url`. Never a path:
    #: this field's whole job is to be clickable.
    url: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )


__all__: list[str] = ["PrContentDerivative", "PrContentDestination"]
