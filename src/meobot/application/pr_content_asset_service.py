"""Re-cuts of a finished piece, and the pages it sends people to.

Step 1F.2.3f. The write side of
:class:`~meobot.db.models.pr_content_asset.PrContentDerivative` and
:class:`~meobot.db.models.pr_content_asset.PrContentDestination`. Reading them is
:meth:`~meobot.application.pr_query_service.PrQueryService.content_derivatives`
and its neighbour, because reading is gated on the read permission and writing is
not.

Two collections in one service, and why
----------------------------------------

They are not the same thing and are deliberately not authorised the same way -
see below. What they share is everything else: both are lists of labelled
pointers hanging off a content item, both are mutable in place, both are audited
and neither touches the workflow. Splitting them into two services would have
duplicated the audit binding, the "belongs to this item" check and the author
resolution three times over for two tables of four columns.

**Neither is a workflow event.** Recording a cutdown moves no stage, satisfies no
gate, reopens no production and requires no new approval. A piece at ``PUBLISHED``
that gains its fifth derivative is still at ``PUBLISHED``, and the board does not
notice. That is the point of the feature: reuse happens on the same content
record, and the alternative - cloning the content, or sending it back through
production - is what this step exists to make unnecessary.

**Neither notifies anybody.** Attaching a link is not news, and five of them
during one campaign afternoon is noise. Every mutation is audited instead, which
is the decision Step 1F.2.3d took for priority and 1F.2.3e for resources.

Three authorisation rules, on purpose
--------------------------------------

**Adding a derivative is open to anybody who may view the item.** Step 1F.2.3g,
and a deliberate reversal of what 1F.2.3f shipped. That step authorised a
derivative as produced work - ``PR_PRODUCTION_EXECUTE`` plus being the producer
or holding ``PR_PRODUCTION_ASSIGN`` - by reasoning from the *master*, the file an
internal reviewer judges and a named producer hands over. A derivative is none of
that: nothing reviews it, no stage moves, nobody is waiting on it, and it is
typically recorded months after the work finished by whoever happens to have made
the cut - a channel owner, a designer, somebody who reformatted it for a new
placement. Requiring them to be the producer of record meant the honest answer
was "ask the producer to add it for you", which is how a record of reuse stops
being kept.

So the rule is the module's one **view** rule and nothing narrower:
:meth:`~meobot.application.pr_content_service.PrContentService.require_viewable_content`.
If you may read the piece, you may write down that a cut of it exists.

**Correcting or removing one is narrower**: its recorder, or production
management under the pre-existing rule -
:meth:`~meobot.application.pr_production_service.PrProductionService.may_manage_production_output`,
the same predicate that decides who may submit a cut. Contribution is not
management: adding a derivative gets nobody the right to edit somebody else's,
delete it, approve anything, move a stage, assign a producer or touch the
original submission. See :meth:`PrContentAssetService.may_change_derivative`.

A **destination is content metadata** - the landing page this piece points at is
a fact about the campaign, not about a file - so it is authorised exactly as
priority, content type and resources are:
:meth:`~meobot.application.pr_content_service.PrContentService.may_edit_metadata`.
The person responsible for the piece is who knows which booking page it should
send people to, and no production capability is involved. **Unchanged by
1F.2.3g**: a landing page is a commercial claim about the campaign, and widening
who may attach one is a different decision from widening who may record a file.

None of the three reads a role string or admits an ``OWNER`` bypass, and each
reuses an existing predicate rather than inventing one.

What the open rule does **not** weaken
---------------------------------------

Every historical-safety rule below survives 1F.2.3g exactly as written. A
derivative a publication points at - including one whose publication was
*reversed* - still has ``location``, ``derivative_type`` and
``source_submission_id`` frozen, and still cannot be deleted by anybody, its
recorder included. Opening who may *add* a row says nothing about who may rewrite
history, and the two questions are answered in different places on purpose.

What a publication makes immutable
-----------------------------------

Once a publication points at a derivative, that row is part of the answer to
*"what did we actually post on TikTok in October"*. So:

* **delete is refused outright.** Not a soft delete, not a cascade: the
  publication would be left naming a file that no longer exists, and the
  ``RESTRICT`` foreign key would refuse the statement anyway. Refusing in the
  service turns a database error into a sentence somebody can act on;
* **the identity-bearing fields are frozen** - ``location``, ``derivative_type``
  and ``source_submission_id``. Changing where a published file lives, or what
  kind of cut it was, silently rewrites history: a reader of the publication row
  would be told the October post used a file that had never been posted;
* **the human fields stay editable** - ``label`` and ``note``. Fixing a typo in
  *"TikTok cut 25s"* corrects how the row reads and changes nothing about what
  happened, and the audit trail carries the previous value.

An unreferenced derivative is fully mutable and fully deletable, because nothing
depends on it being what it was.

Nothing here is stage-gated
----------------------------

A derivative or a destination may be added at any workflow stage, including
``PUBLISHED``, ``MEASURED`` and ``ARCHIVED``. That is not a new permission: this
repository already treats operational metadata as stage-independent - priority,
content type and resources have never consulted ``workflow_stage`` - and
``ARCHIVED``'s existing meaning is precise and narrower than "frozen". It is a
:data:`~meobot.domain.pr.workflow.TERMINAL_STAGES` member, which means **no
transition may leave it**, and it is one of
:data:`~meobot.domain.pr.lifecycle.PUBLISHED_ONWARD_STAGES`, which means it may
not be deleted. Neither is a rule about attaching a link.

Recording, six months later, the cutdown that a colleague actually made is
correcting the record of an archived piece rather than reopening it - and
refusing it would leave the team with the one workaround this step exists to
remove: cloning the content.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_production_service import PrProductionService
from meobot.application.pr_support import record_pr_event
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_content_asset import PrContentDerivative, PrContentDestination
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.assets import (
    normalize_derivative_location,
    normalize_destination_url,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrContentDerivativeType
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.resources import normalize_label, normalize_note

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AddDerivativeCommand:
    """One produced file to record against a content item.

    ``source_submission_id`` is the master this was cut from, and is optional
    because the lineage is honestly sometimes unknown - see the model. When it is
    given it must belong to **this** content item, which is checked rather than
    trusted.
    """

    content_id: uuid.UUID
    derivative_type: PrContentDerivativeType
    label: str
    location: str
    source_submission_id: uuid.UUID | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateDerivativeCommand:
    """Fields a derivative may have changed. ``None`` means "leave alone".

    **There is no ``content_id``.** A derivative belongs to the item it was cut
    from, and moving one between items is not an edit - it is a delete and an
    add, with different audit rows and a different story. Leaving the field out
    is what makes that unrepresentable rather than merely refused, exactly as
    :class:`~meobot.application.pr_content_resource_service.UpdateContentResourceCommand`
    does it.
    """

    derivative_id: uuid.UUID
    derivative_type: PrContentDerivativeType | None = None
    label: str | None = None
    location: str | None = None
    #: Explicitly ``None`` cannot clear the lineage - it means "unchanged", like
    #: every other field here. There is deliberately no way to *unset* a source
    #: submission: the link is a record of where a file came from, and forgetting
    #: it is not a correction.
    source_submission_id: uuid.UUID | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DerivativeView:
    """One derivative, as a client should see it. Step 1F.2.3g.

    The row plus the three facts that are not on it: who recorded it *by name*,
    and whether **this** session may correct or remove it. All three used to be
    the client's problem - a panel matched ``created_by_user_id`` against the
    ``/people`` list it happened to have loaded, and drew edit controls from a
    content-level flag that could only be right for half the list.

    ``can_edit`` means "the ``PATCH`` will not refuse you for *who you are*". It
    does not promise every field is writable: a published output has its
    location, type and lineage frozen for everybody, which is what
    ``is_published_output`` is for.
    """

    derivative: PrContentDerivative
    #: ``None`` when the recorder's ``users`` row has gone. A client renders
    #: that as an absence, never as the id.
    created_by_name: str | None
    #: Whether any publication - a reversed one included - names this file.
    is_published_output: bool
    can_edit: bool
    can_delete: bool


@dataclass(frozen=True, slots=True)
class AddDestinationCommand:
    """One commercial page this content sends people to."""

    content_id: uuid.UUID
    label: str
    url: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateDestinationCommand:
    """Fields a destination may have changed. ``None`` means "leave alone"."""

    destination_id: uuid.UUID
    label: str | None = None
    url: str | None = None
    note: str | None = None


class PrContentAssetService:
    """Records the re-cuts of a piece and the pages it points at.

    Args:
        session: Unit of work. The caller owns the transaction boundary - every
            method here flushes and none commits.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
        content: Owns ``may_edit_metadata``, which destinations authorise
            against, and the content lookup both halves share.
        production: Owns ``may_manage_production_output``, which derivatives
            authorise against - so "who may hand in a cut" and "who may record a
            re-cut of it" have one answer.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        content: PrContentService,
        production: PrProductionService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._content = content
        self._production = production

    # --- Derivatives ------------------------------------------------------
    async def add_derivative(
        self, *, actor: Actor, request_id: uuid.UUID, command: AddDerivativeCommand
    ) -> PrContentDerivative:
        """Record one produced re-cut of this content item.

        Validated before anything is written, so a bad paste costs nothing and
        leaves no half-attached row behind - the order ``submit_production`` and
        ``add_resource`` both use.

        Moves no stage. See the module docstring: this is produced work being
        recorded, not a workflow event.

        Raises:
            PrNotFoundError: No such content item, or no such source submission.
            PrPermissionDeniedError: May not record production output here.
            PrValidationError: Blank label, or a location that is empty, too
                long, or carries a refused scheme. ``details['reason']`` names
                which. Also raised when the named source submission belongs to
                another content item.
        """
        content = await self._content.require_viewable_content(actor, command.content_id)
        source = await self._source_submission(content, command.source_submission_id)

        derivative = PrContentDerivative(
            content_id=content.id,
            source_submission_id=source.id if source is not None else None,
            derivative_type=command.derivative_type,
            label=normalize_label(command.label),
            location=normalize_derivative_location(command.location),
            note=normalize_note(command.note),
            created_by_user_id=self._author(actor),
        )
        self._session.add(derivative)
        await self._session.flush()

        await self._record_derivative(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DERIVATIVE_ADDED,
            content=content,
            derivative=derivative,
        )
        logger.info(
            "pr_content_derivative_added",
            extra={
                "pr_content_id": str(content.id),
                "pr_derivative_id": str(derivative.id),
                "derivative_type": derivative.derivative_type.value,
                "from_submission": source is not None,
            },
        )
        return derivative

    async def update_derivative(
        self, *, actor: Actor, request_id: uuid.UUID, command: UpdateDerivativeCommand
    ) -> PrContentDerivative:
        """Correct a derivative in place, within what publication history allows.

        Once a publication points at this row, ``location``, ``derivative_type``
        and ``source_submission_id`` are frozen and ``label`` and ``note`` are
        not - see the module docstring on why that line is where it is. The
        refusal is raised **before** anything is assigned, so a request changing
        one frozen field and two free ones changes nothing at all rather than
        half of what was asked.

        Raises:
            PrNotFoundError: No such derivative, or no such source submission.
            PrPermissionDeniedError: May not record production output here.
            PrConflictError: A frozen field was changed on a published output.
            PrValidationError: A new label or location is unacceptable.
        """
        derivative = await self._require_derivative(command.derivative_id)
        content = await self._authorized_for_derivative(actor, derivative)

        frozen = {
            "location": command.location is not None
            and normalize_derivative_location(command.location) != derivative.location,
            "derivative_type": command.derivative_type is not None
            and command.derivative_type is not derivative.derivative_type,
            "source_submission_id": command.source_submission_id is not None
            and command.source_submission_id != derivative.source_submission_id,
        }
        changing = sorted(field for field, moved in frozen.items() if moved)
        if changing and await self._is_published_output(derivative.id):
            raise PrConflictError(
                "This output has already been published, so where it points may not be changed",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "derivative_id": str(derivative.id),
                    "fields": changing,
                    "reason": "published_output_is_immutable",
                    # What *is* still editable, so a client can say so rather
                    # than disabling the whole form.
                    "editable": ["label", "note"],
                },
            )

        before = _derivative_snapshot(derivative)
        source = await self._source_submission(content, command.source_submission_id)

        if command.derivative_type is not None:
            derivative.derivative_type = command.derivative_type
        if command.label is not None:
            derivative.label = normalize_label(command.label)
        if command.location is not None:
            derivative.location = normalize_derivative_location(command.location)
        if source is not None:
            derivative.source_submission_id = source.id
        if command.note is not None:
            derivative.note = normalize_note(command.note)

        await self._session.flush()
        await self._session.refresh(derivative)

        after = _derivative_snapshot(derivative)
        if before == after:
            # A form submitted unedited. No audit row, for the reason a no-op
            # priority change writes none.
            return derivative

        await self._record_derivative(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DERIVATIVE_UPDATED,
            content=content,
            derivative=derivative,
            before={key: value for key, value in before.items() if after[key] != value},
        )
        logger.info(
            "pr_content_derivative_updated",
            extra={
                "pr_content_id": str(content.id),
                "pr_derivative_id": str(derivative.id),
            },
        )
        return derivative

    async def delete_derivative(
        self, *, actor: Actor, request_id: uuid.UUID, derivative_id: uuid.UUID
    ) -> None:
        """Remove one derivative, unless something was published from it.

        A published derivative is refused rather than soft-deleted or cascaded:
        the publication row would be left naming a file that no longer exists,
        and the ``RESTRICT`` foreign key would refuse the statement anyway. The
        refusal here turns a database error into a sentence.

        The audit row is written **before** the delete, so it describes a row
        that still exists at the moment it is described.

        Raises:
            PrNotFoundError: No such derivative.
            PrPermissionDeniedError: May not record production output here.
            PrConflictError: A publication points at this output.
        """
        derivative = await self._require_derivative(derivative_id)
        content = await self._authorized_for_derivative(actor, derivative)

        if await self._is_published_output(derivative.id):
            raise PrConflictError(
                "This output has already been published, so it may not be deleted",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "derivative_id": str(derivative.id),
                    "reason": "published_output_is_immutable",
                },
            )

        await self._record_derivative(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DERIVATIVE_DELETED,
            content=content,
            derivative=derivative,
        )
        await self._session.execute(
            delete(PrContentDerivative).where(PrContentDerivative.id == derivative_id)
        )
        await self._session.flush()
        logger.info(
            "pr_content_derivative_deleted",
            extra={
                "pr_content_id": str(content.id),
                "pr_derivative_id": str(derivative_id),
            },
        )

    # --- Destinations -----------------------------------------------------
    async def add_destination(
        self, *, actor: Actor, request_id: uuid.UUID, command: AddDestinationCommand
    ) -> PrContentDestination:
        """Attach one product or landing page to this content item.

        Raises:
            PrNotFoundError: No such content item.
            PrPermissionDeniedError: May not edit this item's metadata.
            PrValidationError: Blank label, or a URL that is empty, too long,
                pathlike, or carries a refused scheme.
        """
        content = await self._authorized_for_metadata(actor, command.content_id)

        destination = PrContentDestination(
            content_id=content.id,
            label=normalize_label(command.label),
            url=normalize_destination_url(command.url),
            note=normalize_note(command.note),
            added_by_user_id=self._author(actor),
        )
        self._session.add(destination)
        await self._session.flush()

        await self._record_destination(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DESTINATION_ADDED,
            content=content,
            destination=destination,
        )
        logger.info(
            "pr_content_destination_added",
            extra={
                "pr_content_id": str(content.id),
                "pr_destination_id": str(destination.id),
            },
        )
        return destination

    async def update_destination(
        self, *, actor: Actor, request_id: uuid.UUID, command: UpdateDestinationCommand
    ) -> PrContentDestination:
        """Correct a destination link in place.

        Nothing freezes here, unlike a published derivative: a destination is
        where a customer is sent *now*, so a moved landing page is a correction
        somebody must be able to make. What the old URL was lives in the audit
        trail.

        Raises:
            PrNotFoundError: No such destination.
            PrPermissionDeniedError: May not edit the owning item's metadata.
            PrValidationError: A new label or URL is unacceptable.
        """
        destination = await self._require_destination(command.destination_id)
        content = await self._authorized_for_metadata(actor, destination.content_id)

        before = _destination_snapshot(destination)
        if command.label is not None:
            destination.label = normalize_label(command.label)
        if command.url is not None:
            destination.url = normalize_destination_url(command.url)
        if command.note is not None:
            destination.note = normalize_note(command.note)

        await self._session.flush()
        await self._session.refresh(destination)

        after = _destination_snapshot(destination)
        if before == after:
            return destination

        await self._record_destination(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DESTINATION_UPDATED,
            content=content,
            destination=destination,
            before={key: value for key, value in before.items() if after[key] != value},
        )
        logger.info(
            "pr_content_destination_updated",
            extra={
                "pr_content_id": str(content.id),
                "pr_destination_id": str(destination.id),
            },
        )
        return destination

    async def delete_destination(
        self, *, actor: Actor, request_id: uuid.UUID, destination_id: uuid.UUID
    ) -> None:
        """Remove one destination link. Nothing else changes.

        Raises:
            PrNotFoundError: No such destination.
            PrPermissionDeniedError: May not edit the owning item's metadata.
        """
        destination = await self._require_destination(destination_id)
        content = await self._authorized_for_metadata(actor, destination.content_id)

        await self._record_destination(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DESTINATION_DELETED,
            content=content,
            destination=destination,
        )
        await self._session.execute(
            delete(PrContentDestination).where(PrContentDestination.id == destination_id)
        )
        await self._session.flush()
        logger.info(
            "pr_content_destination_deleted",
            extra={
                "pr_content_id": str(content.id),
                "pr_destination_id": str(destination_id),
            },
        )

    # --- Derivatives, read ------------------------------------------------
    async def describe_derivatives(
        self, *, actor: Actor, content_id: uuid.UUID
    ) -> tuple[DerivativeView, ...]:
        """This item's derivatives, each with what *this* session may do to it.

        Step 1F.2.3g. The list itself is unchanged - oldest first, gated on the
        read permission - and what is new is the three booleans beside each row.
        They are here rather than on the client because two of the three are
        genuinely per row since this step: a contributor may correct the cut they
        recorded and not the one beside it, and a derivative something was
        published from may not be deleted by anybody.

        **Three queries, whatever the list holds**: the derivatives, the set of
        them a publication points at, and the names of the people who recorded
        them. Asking "has this been published" per row is the N+1 this method
        exists to avoid, and it is the shape a per-row permission invites.

        Raises:
            PrPermissionDeniedError: May not view this content.
            PrNotFoundError: No such content item.
        """
        content = await self._content.require_viewable_content(actor, content_id)
        rows = (
            (
                await self._session.execute(
                    select(PrContentDerivative)
                    .where(PrContentDerivative.content_id == content.id)
                    .order_by(PrContentDerivative.created_at.asc(), PrContentDerivative.id.asc())
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return ()

        published = await self._published_derivative_ids([row.id for row in rows])
        names = await self._author_names([row.created_by_user_id for row in rows])
        # One capability lookup and one relationship query for the whole list,
        # rather than one of each per row.
        manages = await self._may_manage_output(actor, content)
        return tuple(
            DerivativeView(
                derivative=row,
                created_by_name=names.get(row.created_by_user_id),
                is_published_output=row.id in published,
                can_edit=manages or self._is_creator(actor, row),
                can_delete=(manages or self._is_creator(actor, row)) and row.id not in published,
            )
            for row in rows
        )

    async def may_add_derivative(self, actor: Actor, content: PrContentItem) -> bool:
        """May this actor record a derivative against this item? Step 1F.2.3g.

        The write's own condition read without raising, for
        :class:`~meobot.application.pr_action_service.PrAvailableActionService`.

        It is the **view** rule and nothing narrower, which is the change this
        step makes. Before it, recording a cutdown took ``PR_PRODUCTION_EXECUTE``
        plus being the producer or holding ``PR_PRODUCTION_ASSIGN`` - a rule
        written for the *master* an internal reviewer judges, and wrong for the
        thing it was applied to. A derivative is not a handover: nothing is
        reviewed, no stage moves, nobody is waiting on it. It is somebody
        writing down that a cut of this piece exists and where it lives, months
        after the work finished, and the person who knows that is whoever made
        the cut - a channel owner, a designer, an intern with the Drive link -
        none of whom is the producer of record.

        Contribution is still not management: recording one does not let
        somebody edit another person's, delete it, approve anything, move a
        stage or touch production. See :meth:`may_change_derivative`.
        """
        del content
        return self._content.may_view(actor)

    async def may_change_derivative(self, actor: Actor, derivative: PrContentDerivative) -> bool:
        """May this actor correct or remove **this** derivative? Step 1F.2.3g.

        Two ways in, and an unrelated viewer has neither:

        * they **recorded it** - ``created_by_user_id`` is theirs. Fixing a
          mistyped label on the row you added an hour ago is the other half of
          being allowed to add one, and requiring a producer for it would make
          the open contribution useless;
        * they **manage production output** for the item - the pre-existing
          rule, unchanged: ``PR_PRODUCTION_EXECUTE`` plus being the producer or
          holding ``PR_PRODUCTION_ASSIGN``.

        Says nothing about *what* may be changed. A derivative a publication
        points at has its identity-bearing fields frozen for everybody, manager
        included - see :meth:`update_derivative` - and may be deleted by nobody.
        """
        if self._is_creator(actor, derivative):
            return True
        content = await self._session.get(PrContentItem, derivative.content_id)
        if content is None:
            return False
        return await self._may_manage_output(actor, content)

    # --- Internals --------------------------------------------------------
    @staticmethod
    def _is_creator(actor: Actor, derivative: PrContentDerivative) -> bool:
        """Did this actor record this row? Never true for an actor with no user."""
        return actor.user_id is not None and derivative.created_by_user_id == actor.user_id

    async def _may_manage_output(self, actor: Actor, content: PrContentItem) -> bool:
        """The production-authority half, unchanged from Step 1F.2.3f.

        The capability *and* the relationship, in the order the write checked
        them, asked of the service that owns the second half.
        """
        if not await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_EXECUTE):
            return False
        return await self._production.may_manage_production_output(actor, content)

    async def _authorized_for_derivative(
        self, actor: Actor, derivative: PrContentDerivative
    ) -> PrContentItem:
        """The content item, if this actor may change **this** derivative.

        The view check runs first, so somebody who may not read the item at all
        is refused for that reason rather than for not owning a row they cannot
        see. Then :meth:`may_change_derivative`, which is the rule.
        """
        content = await self._content.require_viewable_content(actor, derivative.content_id)
        if not await self.may_change_derivative(actor, derivative):
            raise PrPermissionDeniedError(
                "Bạn chỉ sửa hoặc xoá được sản phẩm phái sinh do mình thêm.",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "derivative_id": str(derivative.id),
                    "capability": PrCapability.PR_PRODUCTION_EXECUTE.value,
                    "reason": "not_the_contributor",
                },
            )
        return content

    async def _published_derivative_ids(
        self, derivative_ids: Sequence[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """Which of these derivatives a publication points at, in one query.

        The batched form of :meth:`_is_published_output` and the same rule:
        **every** publication counts, a reversed one included. Used only by the
        read model - the writes ask the single-row question, because between the
        list and the press somebody may have published one.
        """
        if not derivative_ids:
            return frozenset()
        rows = await self._session.execute(
            select(PrPublication.derivative_id)
            .where(PrPublication.derivative_id.in_(derivative_ids))
            .distinct()
        )
        return frozenset(row[0] for row in rows.all() if row[0] is not None)

    async def _author_names(self, user_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        """The display names for a list of derivatives, in one query.

        Joined here rather than resolved by the client against ``/people``,
        which lists **active** users only: *"Thêm bởi"* on a cut recorded by
        somebody who has since left is exactly the case a client-side lookup
        renders as a blank.
        """
        wanted = set(user_ids)
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(User.id, User.full_name).where(User.id.in_(wanted))
        )
        return {row[0]: row[1] for row in rows.all()}

    async def _authorized_for_metadata(self, actor: Actor, content_id: uuid.UUID) -> PrContentItem:
        """The content item, if this actor may edit its metadata.

        The same rule priority, content type and review resources already use -
        one predicate for the whole class of "facts *about* the work".
        """
        await self._capabilities.require(actor, PrCapability.PR_CONTENT_EDIT)
        content = await self._content.require_content(content_id)
        if not await self._content.may_edit_metadata(actor, content):
            raise PrPermissionDeniedError(
                "Bạn chỉ sửa được link sản phẩm của nội dung mình phụ trách.",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "capability": PrCapability.PR_CONTENT_EDIT.value,
                    "reason": "not_responsible",
                },
            )
        return content

    async def _source_submission(
        self, content: PrContentItem, submission_id: uuid.UUID | None
    ) -> PrProductionSubmission | None:
        """The named master cut, checked to belong to **this** content item.

        Not trusted from the request. A derivative pointing at another item's
        submission would claim a lineage that never happened, and would make
        "which files came out of this piece" answer with somebody else's.
        """
        if submission_id is None:
            return None
        found = await self._session.get(PrProductionSubmission, submission_id)
        if found is None:
            raise PrNotFoundError(
                "No production submission with that id",
                details={"source_submission_id": str(submission_id)},
            )
        if found.content_id != content.id:
            raise PrValidationError(
                "That production submission belongs to another content item",
                details={
                    "field": "source_submission_id",
                    "reason": "foreign_submission",
                    "source_submission_id": str(submission_id),
                    "content_id": str(content.id),
                },
            )
        return found

    async def _is_published_output(self, derivative_id: uuid.UUID) -> bool:
        """Does any publication name this derivative as what went out?

        **Every** publication row counts, including a reversed one - Step
        1F.2.3f.1, and the same reasoning the permanent-delete floor uses. A
        reversed publication is still the record of a posting that named this
        file; deleting the file underneath it would leave that record pointing at
        nothing. Deliberately not the *active* predicate.
        """
        found = await self._session.execute(
            select(exists().where(PrPublication.derivative_id == derivative_id))
        )
        return bool(found.scalar())

    async def _require_derivative(self, derivative_id: uuid.UUID) -> PrContentDerivative:
        found = await self._session.get(PrContentDerivative, derivative_id)
        if found is None:
            raise PrNotFoundError(
                "No PR content derivative with that id",
                details={"derivative_id": str(derivative_id)},
            )
        return found

    async def _require_destination(self, destination_id: uuid.UUID) -> PrContentDestination:
        found = await self._session.get(PrContentDestination, destination_id)
        if found is None:
            raise PrNotFoundError(
                "No PR content destination with that id",
                details={"destination_id": str(destination_id)},
            )
        return found

    async def _record_derivative(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        action: AuditAction,
        content: PrContentItem,
        derivative: PrContentDerivative,
        before: dict[str, object] | None = None,
    ) -> None:
        """One audit row naming and locating the derivative.

        The payload carries the identity, the lineage and the location - what
        somebody reconstructing "which file was this" needs - and never the note
        or anything the location points at.
        """
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=action,
            entity_type="pr_content_derivative",
            entity_id=derivative.id,
            before=before,
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "derivative_id": str(derivative.id),
                **_derivative_snapshot(derivative),
            },
        )

    async def _record_destination(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        action: AuditAction,
        content: PrContentItem,
        destination: PrContentDestination,
        before: dict[str, object] | None = None,
    ) -> None:
        """One audit row naming the link. Concise: a label and a URL is the row."""
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=action,
            entity_type="pr_content_destination",
            entity_id=destination.id,
            before=before,
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "destination_id": str(destination.id),
                **_destination_snapshot(destination),
            },
        )

    @staticmethod
    def _author(actor: Actor) -> uuid.UUID:
        """Who recorded it. A row with no author has nobody to ask about it."""
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "Tài khoản này chưa được liên kết với một người dùng, không thể thực hiện.",
                details={"reason": "no_user_record"},
            )
        return actor.user_id


def _derivative_snapshot(derivative: PrContentDerivative) -> dict[str, object]:
    """The audited fields of a derivative. No note, no fetched content."""
    return {
        "derivative_type": derivative.derivative_type.value,
        "label": derivative.label,
        "location": derivative.location,
        "source_submission_id": (
            str(derivative.source_submission_id) if derivative.source_submission_id else None
        ),
    }


def _destination_snapshot(destination: PrContentDestination) -> dict[str, object]:
    """The audited fields of a destination link."""
    return {"label": destination.label, "url": destination.url}


__all__: list[str] = [
    "AddDerivativeCommand",
    "AddDestinationCommand",
    "DerivativeView",
    "PrContentAssetService",
    "UpdateDerivativeCommand",
    "UpdateDestinationCommand",
]
