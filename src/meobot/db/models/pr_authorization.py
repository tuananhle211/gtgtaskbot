"""PR capability grants: who may review at which gate, and over what.

Step 1C.1 created ``pr_user_capabilities``. Step 1F.2.7 gives a grant a
**scope** and makes it **additive**, in three tables.

Why a table at all
------------------

Step 1C mapped every PR action onto an existing
:class:`~meobot.domain.permissions.matrix.Permission` and reported one thing it
could not express: **MeoBot's four roles cannot tell a Team Lead from a Head.**
``Role`` is ``OWNER``, ``ADMIN``, ``TEAM_LEAD``, ``EMPLOYEE``, and ``TEAM_LEAD``
already holds *both* ``script.review`` and ``script.approve``. Whatever pair of
permissions the two gates are mapped to, one person satisfies both - so
"the Head must be someone other than the Team Lead" was a rule the
authorization model had no way to state.

This table states it. It is deliberately **not** a second identity system: the
subject is a ``users.id``, and role permissions are untouched by anything here -
see :mod:`meobot.domain.pr.policy`.

Only three capabilities are grant-backed - the three review gates. The other
ten are decided by the permission matrix alone, because the matrix already
separates them adequately and adding rows nobody needs would make the model
harder to reason about for no gain.

What Step 1F.2.7 changed
------------------------

**Scope.** A grant now carries, per axis, a
:class:`~meobot.domain.pr.grants.PrGrantScopeMode` and - when that mode is
``SELECTED`` - the values it selected, in two association tables:
``pr_user_capability_content_types`` and ``pr_user_capability_channels``. Never
a comma-separated column: both axes are joined, filtered and audited, and a
string of ids is none of those things. The rules the pair implements are in
:mod:`meobot.domain.pr.grants`.

**Additive.** ``requires_role_baseline`` says whether the grant still needs the
holder's role to carry the capability's permission. New grants set it ``false``:
the point of an explicit grant is that a member with no reviewing role may
approve the six items they were actually given. Rows that predate this step are
migrated to ``true``, which is exactly the rule they were written under - see
revision 0031. Nothing about the permission matrix or anybody's role changes
either way.

**Immediate revocation.** ``revoked_at`` is a timestamp, not a date, and a grant
with one set is inactive from that instant. The previous revocation - setting
``effective_to`` to today - left the grant in force for the rest of the day,
because a day-scoped interval that *includes* its end date must. Both are
written on revoke: ``revoked_at`` is what authorization reads, ``effective_to``
is what a person reading the history sees.

Dated, like channel assignments
-------------------------------

``effective_from`` and ``effective_to`` are **closed** and either may be
``NULL``: ``NULL`` from means "since always", ``NULL`` to means "no planned
end" - the *Không hết hạn* the permissions screen offers. A grant is active on
day *D* when ``effective_from <= D <= effective_to`` under that reading, and when
``revoked_at`` is ``NULL``. Same date semantics as ``pr_channel_assignments`` -
see :mod:`meobot.domain.pr.assignments` - so "who was Head in March" stays
answerable after somebody hands the role over.

Nothing is deleted: an approval recorded last year was legitimate under the
grant that existed then, and erasing the grant would make the history
unreadable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meobot.db.base import Base, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.pr.grants import PrGrantScopeMode
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.policy import PrCapability

#: What makes a grant **open**: nobody has closed it. Written once and used by
#: both the model's partial index and the migration that creates it, in SQL both
#: PostgreSQL and SQLite accept - the offline suite builds this schema from the
#: models.
OPEN_GRANT = text("effective_to IS NULL AND revoked_at IS NULL")

#: The grants table, named once so the two child tables cannot drift from it.
GRANTS_TABLE = "pr_user_capabilities"


class PrUserCapability(Base, UUIDPrimaryKeyMixin):
    """One person's right to do one PR thing, over one period, in one scope.

    No ``updated_at``: the only mutations are closing the row and revoking it,
    both of which are themselves dated facts on the row. Correcting a scope is
    a new grant plus a revocation, not an edit - a grant that changed shape
    under an approval already recorded against it would make that approval
    inexplicable.

    ``granted_by_user_id`` is nullable because a grant may be seeded by a
    migration or an admin script with no acting person behind it, and naming
    somebody who did not decide would be a lie recorded as data.

    **No unique index on (user, capability).** Step 1C.1 had one, over open
    rows, and Step 1F.2.7 drops it: two scoped grants of the same capability to
    the same person - *Facebook posts on the two Facebook channels* and *short
    video scripts everywhere* - are two different rights, and a schema that
    called them a conflict would force one grant per combination, which is the
    thing the multi-select exists to avoid. What replaces it is a plain lookup
    index and an application-level refusal of an **identical** scope, in
    :meth:`~meobot.application.pr_capability_service.PrCapabilityService.grant`.
    """

    __tablename__ = GRANTS_TABLE
    __table_args__ = (
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
            name="effective_period_ordered",
        ),
        # "Who may do X right now" - the query the approval gate and the
        # reviewer pickers both run.
        Index("ix_pr_user_capabilities_capability_to", "capability", "effective_to"),
        # "Everything this person holds", which is what the scope resolution
        # for one approval decision reads.
        Index("ix_pr_user_capabilities_user_capability", "user_id", "capability"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    capability: Mapped[PrCapability] = mapped_column(
        value_enum(PrCapability, name="pr_capability", length=40), nullable=False
    )

    # --- Scope: where this right applies ---------------------------------
    #: ``ALL`` covers every classification including *Chưa phân loại*;
    #: ``SELECTED`` covers exactly ``content_types`` below, plus unclassified
    #: content only when ``include_unclassified_content`` says so.
    #:
    #: Fail-closed default. A row written without a scope covers nothing, which
    #: is the safe direction for the one column in this table that decides how
    #: much a grant is worth.
    content_type_scope: Mapped[PrGrantScopeMode] = mapped_column(
        value_enum(PrGrantScopeMode, name="pr_grant_scope_mode", length=20),
        nullable=False,
        default=PrGrantScopeMode.SELECTED,
        server_default=PrGrantScopeMode.SELECTED.value,
    )
    #: Whether a ``SELECTED`` classification scope reaches content nobody has
    #: classified. Default ``false``: absent is not a wildcard.
    include_unclassified_content: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: ``ALL`` covers every channel, including ones created after the grant, and
    #: including an item with no channel at all.
    channel_scope: Mapped[PrGrantScopeMode] = mapped_column(
        value_enum(PrGrantScopeMode, name="pr_grant_scope_mode", length=20),
        nullable=False,
        default=PrGrantScopeMode.SELECTED,
        server_default=PrGrantScopeMode.SELECTED.value,
    )
    #: Whether a ``SELECTED`` channel scope reaches content with no target.
    include_unassigned_channel: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Whether the holder's **role** must also carry the capability's baseline
    #: permission. ``false`` on everything created since Step 1F.2.7 - an
    #: explicit grant authorises on its own - and ``true`` on the rows that
    #: predate it, which is the rule those rows were written under. See
    #: :mod:`meobot.domain.pr.policy`.
    requires_role_baseline: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # --- Period and revocation -------------------------------------------
    #: First day in force. ``NULL`` means "since always".
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Last day in force. ``NULL`` means open-ended - *Không hết hạn*.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Set the instant somebody withdraws the grant. Authorization reads this
    #: before anything else, so a revocation takes effect on the next request
    #: rather than at midnight.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who withdrew it. Nullable for the same reason ``granted_by_user_id`` is.
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: Who decided, when anybody did.
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: Why, in a sentence. Prose for a person; nothing parses it.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: Loaded with the grant, in one extra statement per query rather than one
    #: per row: resolving an approval reads every active grant a person holds
    #: and needs both scopes for each of them, and a lazy attribute would be a
    #: blocking IO call inside an async request.
    content_types: Mapped[list[PrUserCapabilityContentType]] = relationship(
        back_populates="grant", lazy="selectin", cascade="all, delete-orphan"
    )
    channels: Mapped[list[PrUserCapabilityChannel]] = relationship(
        back_populates="grant", lazy="selectin", cascade="all, delete-orphan"
    )


class PrUserCapabilityContentType(Base, UUIDPrimaryKeyMixin):
    """One content classification a grant covers.

    A row per classification rather than a list column, because this is a set
    of canonical codes that gets joined, counted and audited. ``ON DELETE
    CASCADE`` on the grant: these rows are parts of it, not facts of their own.
    """

    __tablename__ = "pr_user_capability_content_types"
    __table_args__ = (
        Index(
            "uq_pr_user_capability_content_types_grant_type",
            "capability_grant_id",
            "content_type",
            unique=True,
        ),
    )

    capability_grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{GRANTS_TABLE}.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_type: Mapped[PrContentType] = mapped_column(
        value_enum(PrContentType, name="pr_content_type", length=30), nullable=False
    )

    grant: Mapped[PrUserCapability] = relationship(back_populates="content_types")


class PrUserCapabilityChannel(Base, UUIDPrimaryKeyMixin):
    """One channel a grant covers.

    ``RESTRICT`` towards ``pr_channels`` and ``CASCADE`` towards the grant, and
    the asymmetry is the point: a channel somebody has been given approval
    rights over must not be deletable out from under the grant, while the rows
    that spell out a grant's scope have no life without it.
    """

    __tablename__ = "pr_user_capability_channels"
    __table_args__ = (
        Index(
            "uq_pr_user_capability_channels_grant_channel",
            "capability_grant_id",
            "channel_id",
            unique=True,
        ),
    )

    capability_grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{GRANTS_TABLE}.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False, index=True
    )

    grant: Mapped[PrUserCapability] = relationship(back_populates="channels")


__all__: list[str] = [
    "GRANTS_TABLE",
    "OPEN_GRANT",
    "PrUserCapability",
    "PrUserCapabilityChannel",
    "PrUserCapabilityContentType",
]
