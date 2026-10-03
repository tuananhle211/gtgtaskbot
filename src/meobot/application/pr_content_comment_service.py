"""Talking about a piece of content, without changing it.

Step 1F.2.3g. The write and read side of
:class:`~meobot.db.models.pr_content_comment.PrContentComment`.

The one thing this service must never do
-----------------------------------------

**Move anything.** No stage changes, no version is written, no approval is
satisfied or invalidated, no priority moves, no owner or producer changes, no
notification is queued and no task appears. A comment is discussion, and a
discussion feature that quietly nudged the workflow would be the worst kind of
surprise - somebody types *"ok duyệt nhé"* and a gate opens.

That is why this service takes the content service and the audit service and
**nothing else**: there is no workflow service here to call, which makes the
promise structural rather than a matter of discipline.

Who may say something
----------------------

Anybody who may **view** the content, and nothing narrower. That is
:meth:`~meobot.application.pr_content_service.PrContentService.require_viewable_content`
- the module's one authoritative "can this actor see this piece" - rather than a
capability of its own, because inventing ``PR_CONTENT_COMMENT`` would have
created a right somebody has to be granted before they can point out that a hook
runs three seconds long. Reading the comments takes exactly the same check, so
there is no state in which a person can see a conversation and not join it, or
join one they cannot see.

Contribution is not management
-------------------------------

Being allowed to speak is not being allowed to edit what somebody else said:

* **edit** is the author's, and only theirs. Not management's either - a lead
  rewording a member's comment produces a sentence in that member's name that
  they never wrote, which is worse than anything it could fix;
* **delete** is the author's or a moderator's, where "moderator" is the existing
  ``PR_CONTENT_CANCEL`` - the capability that already means *"may end this piece
  of work"* and is already the management half of content deletion. No new
  capability, no role string, no ``OWNER`` bypass.

Deleting is a tombstone
------------------------

The row survives with ``deleted_at`` set, the body and the name stop being sent,
and the replies underneath stay readable. The alternative - removing the row -
either orphans somebody else's answers or deletes them along with the question,
and both of those destroy other people's words to honour one person's decision
about their own.

**One audit row, on delete only.** Creating and editing write none: the row
already carries who wrote it, when, and whether it was reworded, it is shown back
to its author, and an audit entry per typed sentence would bury the events
somebody actually searches the trail for. Removal is different in kind - the row
stops showing what it said, and *"who took whose comment down"* is asked
directly.

A fixed number of queries for a thread, never one per root
-----------------------------------------------------------

:meth:`PrContentCommentService.list_comments` costs the same number of statements
for a thread of two hundred as for a thread of two: the content item, the page of
roots, their count, **all** their replies in one ``IN``, the authors of both in a
second, and one capability lookup for the moderator flag. Not one of them is per
row.

Fetching replies per root is the N+1 shape this module avoids everywhere, and it
is the shape a comment thread invites more than anything else in the schema - so
a test counts the statements rather than trusting this paragraph.

Nothing here is stage-gated
----------------------------

A comment may be added, edited and removed at every workflow stage, ``ARCHIVED``
included. That is the existing archive policy rather than a widening of it:
``ARCHIVED`` in this repository is a
:data:`~meobot.domain.pr.workflow.TERMINAL_STAGES` member - **no transition may
leave it** - and a member of
:data:`~meobot.domain.pr.lifecycle.PUBLISHED_ONWARD_STAGES` - it may not be
deleted. Neither is a rule about saying something. Priority, content type,
review resources, derivatives and destination links have all been stage-
independent since the steps that introduced them, and a discussion that had to
stop the moment a piece was put away would go silent at exactly the point
somebody asks *"why did we archive this one?"*.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_content_comment import PrContentComment
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.comments import (
    DEFAULT_COMMENT_PAGE,
    MAX_COMMENT_PAGE,
    normalize_comment_body,
)
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.policy import PrCapability

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AddCommentCommand:
    """One thing to say about a content item.

    ``parent_comment_id`` is the root being answered, and is ``None`` for a new
    thread. It may never name a *reply*: threading is one level deep, and a
    client that tries is refused rather than silently re-parented - see
    :meth:`PrContentCommentService.add_comment`.
    """

    content_id: uuid.UUID
    body: str
    parent_comment_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CommentView:
    """One comment, as a client should see it.

    A read model rather than the row, because three of its five facts are not on
    the row: the author's **name**, and whether *this* session may edit or delete
    it. A client comparing ``author_user_id`` against its session id would be
    re-deriving an authorization rule the server owns, and would get the
    moderator half wrong.

    ``author_name`` and ``body`` are both ``None`` once the comment is
    tombstoned. That is not a data-hiding accident: what a deleted comment says
    is *"there was a comment here"*, and sending the words with a flag beside
    them would leave every client one bug away from rendering them.
    """

    id: uuid.UUID
    content_id: uuid.UUID
    parent_comment_id: uuid.UUID | None
    #: ``None`` for a tombstone.
    author_user_id: uuid.UUID | None
    #: ``None`` for a tombstone, and ``None`` for an author whose ``users`` row
    #: has since gone - which a client renders as an absence rather than as an
    #: id.
    author_name: str | None
    #: ``None`` for a tombstone.
    body: str | None
    created_at: datetime
    #: When the author last reworded it, if they ever did.
    edited_at: datetime | None
    is_deleted: bool
    can_edit: bool
    can_delete: bool
    #: Empty for a reply. Threading is one level deep.
    replies: tuple[CommentView, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentCommentPage:
    """One page of root threads, and how many there are in total.

    ``total`` counts **roots**, not comments: it is what a *"còn 12 chủ đề
    nữa"* is drawn from, and counting replies into it would make the number
    disagree with what paging through actually produces.
    """

    threads: tuple[CommentView, ...]
    total: int
    limit: int
    offset: int


class PrContentCommentService:
    """The comment thread on a content item.

    Args:
        session: Unit of work. The caller owns the transaction boundary - every
            method here flushes and none commits.
        audit: Event writer sharing that session. Used by exactly one method.
        capabilities: Resolves PR capabilities. Asked one question only:
            whether this actor holds ``PR_CONTENT_CANCEL``, which is what makes
            them a moderator here.
        content: Owns
            :meth:`~meobot.application.pr_content_service.PrContentService.require_viewable_content`,
            the module's one answer to "may this actor see this piece" - which
            is the whole of the authorization for reading and for writing a
            comment.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        content: PrContentService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._content = content

    # --- Reading ----------------------------------------------------------
    async def list_comments(
        self,
        *,
        actor: Actor,
        content_id: uuid.UUID,
        limit: int = DEFAULT_COMMENT_PAGE,
        offset: int = 0,
    ) -> ContentCommentPage:
        """One page of root threads with their replies, oldest first.

        Ordered ``created_at`` ascending with ``id`` as the tiebreaker, so a
        conversation reads top to bottom the way it happened and two comments
        written in the same transaction still come back in a fixed order. The
        replies under each root use the same key, for the same two reasons.

        **Tombstones are included**, roots and replies alike. A deleted root has
        to be, or its answers would appear under nothing; a deleted reply is kept
        for consistency, so that what deletion does to a comment does not depend
        on where in the thread it sat.

        The statement count does not depend on how many comments there are -
        the roots, their count, all their replies in one ``IN``, the authors in
        a second, and the two lookups the authorization needs. See the module
        docstring.

        Raises:
            PrPermissionDeniedError: May not view this content.
            PrNotFoundError: No such content item.
        """
        await self._content.require_viewable_content(actor, content_id)
        bounded = max(1, min(limit, MAX_COMMENT_PAGE))
        start = max(0, offset)

        roots = (
            (
                await self._session.execute(
                    select(PrContentComment)
                    .where(
                        PrContentComment.content_id == content_id,
                        PrContentComment.parent_comment_id.is_(None),
                    )
                    .order_by(PrContentComment.created_at.asc(), PrContentComment.id.asc())
                    .limit(bounded)
                    .offset(start)
                )
            )
            .scalars()
            .all()
        )
        total = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(PrContentComment)
                    .where(
                        PrContentComment.content_id == content_id,
                        PrContentComment.parent_comment_id.is_(None),
                    )
                )
            ).scalar()
            or 0
        )

        replies = await self._replies_for([root.id for root in roots])
        names = await self._author_names(
            [*roots, *(reply for group in replies.values() for reply in group)]
        )
        moderator = await self._is_moderator(actor)

        threads = tuple(
            self._view(
                root,
                actor=actor,
                moderator=moderator,
                names=names,
                replies=tuple(
                    self._view(reply, actor=actor, moderator=moderator, names=names)
                    for reply in replies.get(root.id, ())
                ),
            )
            for root in roots
        )
        return ContentCommentPage(threads=threads, total=total, limit=bounded, offset=start)

    async def may_comment(self, actor: Actor, content: PrContentItem) -> bool:
        """May this actor add a comment to this item?

        The write's own condition, read without raising, so
        :class:`~meobot.application.pr_action_service.PrAvailableActionService`
        can offer the composer without keeping a second copy of the rule.

        Takes the content it is asked about even though the current rule does
        not read it: the signature is the one every other ``may_*`` predicate in
        this module has, and a future per-item visibility rule would land here
        rather than changing every caller.
        """
        del content
        return self._content.may_view(actor)

    # --- Writing ----------------------------------------------------------
    async def add_comment(
        self, *, actor: Actor, request_id: uuid.UUID, command: AddCommentCommand
    ) -> PrContentComment:
        """Say one thing about this content item.

        Validated before anything is written, so a rejected reply leaves no
        half-attached row - the order every write in this module uses.

        Moves no stage, writes no version, satisfies no gate and notifies
        nobody. See the module docstring.

        Args:
            actor: Anybody who may view the item.
            request_id: Correlation id. Unused by the happy path - creating a
                comment writes no audit row - and taken anyway so the signature
                matches every other command here and so an audited variant would
                not change the call sites.
            command: The content item, the body, and the root being answered.

        Raises:
            PrPermissionDeniedError: May not view this content, or has no
                ``users`` row to be the author.
            PrNotFoundError: No such content item, or no such parent comment.
            PrValidationError: Empty or over-long body, a parent belonging to
                another content item, a parent that is itself a reply, or a
                parent that has been deleted.
        """
        del request_id
        content = await self._content.require_viewable_content(actor, command.content_id)
        author = self._author(actor)
        body = normalize_comment_body(command.body)
        parent = await self._parent_for(content, command.parent_comment_id)

        comment = PrContentComment(
            content_id=content.id,
            author_user_id=author,
            parent_comment_id=parent.id if parent is not None else None,
            body=body,
        )
        self._session.add(comment)
        await self._session.flush()
        logger.info(
            "pr_content_comment_added",
            extra={
                "pr_content_id": str(content.id),
                "pr_comment_id": str(comment.id),
                "is_reply": parent is not None,
            },
        )
        return comment

    async def update_comment(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        comment_id: uuid.UUID,
        body: str,
    ) -> PrContentComment:
        """Reword one's own comment.

        The author's, and nobody else's - not a lead's and not an owner's. See
        the module docstring on why moderation stops at removal.

        ``content_id`` comes from the path so the URL names the aggregate, and is
        **checked** against the comment's own rather than trusted: without it,
        a comment id from one content item could be edited through another
        item's URL by anybody who may view that other item.

        Args:
            actor: The comment's author.
            request_id: Correlation id. Unused - an edit writes no audit row.
            content_id: The item the URL named.
            comment_id: The comment to reword.
            body: The new text.

        Raises:
            PrPermissionDeniedError: May not view this content, or is not the
                author.
            PrNotFoundError: No such comment, or it belongs to another item.
            PrValidationError: Empty or over-long body.
            PrConflictError: Never. A deleted comment is a
                :class:`~meobot.domain.pr.errors.PrValidationError`, because
                there is nothing to conflict with - the text is gone.
        """
        del request_id
        await self._content.require_viewable_content(actor, content_id)
        comment = await self._require_comment(content_id, comment_id, lock=True)
        if comment.is_deleted:
            raise PrValidationError(
                "A deleted comment cannot be edited",
                details={
                    "comment_id": str(comment.id),
                    "reason": "comment_deleted",
                },
            )
        if actor.user_id is None or comment.author_user_id != actor.user_id:
            raise PrPermissionDeniedError(
                "Bạn chỉ sửa được bình luận của mình.",
                details={
                    "content_id": str(content_id),
                    "comment_id": str(comment.id),
                    "reason": "not_the_author",
                },
            )

        cleaned = normalize_comment_body(body)
        if cleaned == comment.body:
            # A form submitted unedited. Nothing is written, so nothing claims
            # the comment was reworded - the same no-op rule a priority change
            # and a derivative correction both follow.
            return comment

        comment.body = cleaned
        comment.edited_at = utcnow()
        await self._session.flush()
        await self._session.refresh(comment)
        logger.info(
            "pr_content_comment_updated",
            extra={"pr_content_id": str(content_id), "pr_comment_id": str(comment.id)},
        )
        return comment

    async def delete_comment(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        comment_id: uuid.UUID,
    ) -> PrContentComment:
        """Take one comment down, leaving the thread standing.

        A tombstone rather than a delete: ``deleted_at`` and
        ``deleted_by_user_id`` are set, the row stays, and the replies underneath
        remain readable. See the module docstring.

        The author, or a moderator holding ``PR_CONTENT_CANCEL``. Deleting twice
        is idempotent and writes nothing the second time, so a double-click does
        not produce two audit rows claiming two removals.

        Raises:
            PrPermissionDeniedError: May not view this content, or is neither
                the author nor a moderator.
            PrNotFoundError: No such comment, or it belongs to another item.
        """
        await self._content.require_viewable_content(actor, content_id)
        comment = await self._require_comment(content_id, comment_id, lock=True)
        if comment.is_deleted:
            return comment

        author = actor.user_id is not None and comment.author_user_id == actor.user_id
        moderator = await self._is_moderator(actor)
        if not (author or moderator):
            raise PrPermissionDeniedError(
                "Bạn chỉ xoá được bình luận của mình.",
                details={
                    "content_id": str(content_id),
                    "comment_id": str(comment.id),
                    "capability": PrCapability.PR_CONTENT_CANCEL.value,
                    "reason": "not_the_author",
                },
            )

        comment.deleted_at = utcnow()
        comment.deleted_by_user_id = actor.user_id
        await self._session.flush()
        await self._session.refresh(comment)

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_COMMENT_DELETED,
            entity_type="pr_content_comment",
            entity_id=comment.id,
            after={
                "content_id": str(content_id),
                "comment_id": str(comment.id),
                "author_user_id": str(comment.author_user_id),
                "is_reply": comment.parent_comment_id is not None,
                # Who removed it, in the vocabulary the rule is written in.
                # Never the body: the audit trail is not a second copy of a
                # conversation somebody chose to take down.
                "removed_by": "author" if author else "moderator",
            },
        )
        logger.info(
            "pr_content_comment_deleted",
            extra={
                "pr_content_id": str(content_id),
                "pr_comment_id": str(comment.id),
                "by_author": author,
            },
        )
        return comment

    # --- Internals --------------------------------------------------------
    async def _require_comment(
        self, content_id: uuid.UUID, comment_id: uuid.UUID, *, lock: bool
    ) -> PrContentComment:
        """One comment of **this** content item, held against concurrent writers.

        The ``content_id`` check is the security boundary of these routes and
        not a tidiness one: the path says which aggregate is being addressed,
        and a comment that belongs to a different item is a *not found* here
        rather than a permission error - the caller is not entitled to learn it
        exists.
        """
        found = (
            await lock_row(self._session, PrContentComment, comment_id)
            if lock
            else await self._session.get(PrContentComment, comment_id)
        )
        if found is None or found.content_id != content_id:
            raise PrNotFoundError(
                "No PR content comment with that id on this content item",
                details={"content_id": str(content_id), "comment_id": str(comment_id)},
            )
        return found

    async def _parent_for(
        self, content: PrContentItem, parent_id: uuid.UUID | None
    ) -> PrContentComment | None:
        """The root being answered, checked three ways.

        Same content item, actually a root, and not a tombstone. All three are
        refused rather than repaired: attaching a reply to the root of the reply
        somebody aimed at would move their answer under a different question,
        and answering a comment that has been taken down is answering nothing.
        """
        if parent_id is None:
            return None
        parent = await self._session.get(PrContentComment, parent_id)
        if parent is None:
            raise PrNotFoundError(
                "No PR content comment with that id",
                details={"parent_comment_id": str(parent_id)},
            )
        if parent.content_id != content.id:
            raise PrValidationError(
                "That comment belongs to another content item",
                details={
                    "field": "parent_comment_id",
                    "reason": "foreign_comment",
                    "parent_comment_id": str(parent_id),
                    "content_id": str(content.id),
                },
            )
        if parent.parent_comment_id is not None:
            raise PrValidationError(
                "Replies may not be replied to",
                details={
                    "field": "parent_comment_id",
                    "reason": "nested_reply",
                    "parent_comment_id": str(parent_id),
                    # The root the client should aim at instead, so a panel can
                    # retry rather than only apologise.
                    "root_comment_id": str(parent.parent_comment_id),
                },
            )
        if parent.is_deleted:
            raise PrValidationError(
                "That comment has been deleted",
                details={
                    "field": "parent_comment_id",
                    "reason": "comment_deleted",
                    "parent_comment_id": str(parent_id),
                },
            )
        return parent

    async def _replies_for(
        self, root_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[PrContentComment]]:
        """Every reply to a page of roots, in **one** query.

        Grouped in Python afterwards, which is the cheap half: the expensive
        half would be a query per root, and this exists so there is not one.
        """
        if not root_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(PrContentComment)
                    .where(PrContentComment.parent_comment_id.in_(root_ids))
                    .order_by(PrContentComment.created_at.asc(), PrContentComment.id.asc())
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[uuid.UUID, list[PrContentComment]] = {}
        for row in rows:
            assert row.parent_comment_id is not None  # the WHERE guarantees it
            grouped.setdefault(row.parent_comment_id, []).append(row)
        return grouped

    async def _author_names(self, comments: Sequence[PrContentComment]) -> Mapping[uuid.UUID, str]:
        """The display names for a page of comments, in one query.

        Joined here rather than resolved by the client against ``/people``,
        which lists **active** users only: a comment written by somebody who has
        since left is still part of the conversation, and rendering it with no
        name - or worse, with a UUID - would be a hole in the record for the
        most ordinary reason a person leaves a team.

        Tombstoned comments are skipped, so a name that will not be sent is not
        fetched either.
        """
        wanted = {comment.author_user_id for comment in comments if not comment.is_deleted}
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(User.id, User.full_name).where(User.id.in_(wanted))
        )
        return {row[0]: row[1] for row in rows.all()}

    async def _is_moderator(self, actor: Actor) -> bool:
        """May this actor remove somebody else's comment?

        ``PR_CONTENT_CANCEL`` - the capability that already means "may end this
        piece of work" and is already the management half of content deletion.
        Asked once per request rather than per row.
        """
        return await self._capabilities.allows(actor, PrCapability.PR_CONTENT_CANCEL)

    def _view(
        self,
        comment: PrContentComment,
        *,
        actor: Actor,
        moderator: bool,
        names: Mapping[uuid.UUID, str],
        replies: tuple[CommentView, ...] = (),
    ) -> CommentView:
        """One row as a client should see it, tombstones included."""
        deleted = comment.is_deleted
        mine = actor.user_id is not None and comment.author_user_id == actor.user_id
        return CommentView(
            id=comment.id,
            content_id=comment.content_id,
            parent_comment_id=comment.parent_comment_id,
            author_user_id=None if deleted else comment.author_user_id,
            author_name=None if deleted else names.get(comment.author_user_id),
            body=None if deleted else comment.body,
            created_at=comment.created_at,
            edited_at=None if deleted else comment.edited_at,
            is_deleted=deleted,
            can_edit=not deleted and mine,
            can_delete=not deleted and (mine or moderator),
            replies=replies,
        )

    @staticmethod
    def _author(actor: Actor) -> uuid.UUID:
        """Who said it. A comment with no author has nobody to attribute it to."""
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "Tài khoản này chưa được liên kết với một người dùng, không thể bình luận.",
                details={"reason": "no_user_record"},
            )
        return actor.user_id


__all__: list[str] = [
    "AddCommentCommand",
    "CommentView",
    "ContentCommentPage",
    "PrContentCommentService",
]
