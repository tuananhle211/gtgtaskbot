"""Recording that a piece of content actually went out.

This service records **facts, not actions**. It calls no platform API, opens no
network connection and schedules nothing. "Register a publication" means
somebody has published something and is telling MeoBot about it; automatic
publishing to TikTok, YouTube or Facebook is out of scope for Step 1C and has
no code path here.

What one call does, atomically:

* inserts a ``pr_publications`` row - the *occurrence*, naming **which produced
  file** went out;
* marks the matching ``pr_content_targets`` row ``PUBLISHED`` when there is one -
  the *plan* is now fulfilled;
* on the first publication, moves the content ``READY_TO_PUBLISH -> PUBLISHED``.

Subsequent publications happen while the content is already ``PUBLISHED``, and no
further stage change happens - a piece that goes to three channels over three
days becomes ``PUBLISHED`` once, on the first, which is what "published" means
about the idea rather than about one cut of it.

``url`` is stored for a person to click and is never a key, exactly as
``pr_publications`` was designed: ``platform_post_id`` is what a future
collector matches on, and nothing here looks a publication up by URL.

Step 1F.2.3f.2 / 1F.2.3f.3: who may record one
-----------------------------------------------

Recording a publication used to need ``PR_PUBLICATION_REGISTER``, which is
``publish.social`` and therefore ``ADMIN`` and ``OWNER`` only - so the person who
had actually just posted the video could not write down that they had. That is
backwards for what is a daily contributor task, and widening ``publish.social``
would have handed contributors everything else it guards.

Step 1F.2.3f.2 split the two jobs and gave creation its own capability, then
narrowed it again with a *channel* rule: a contributor could record a posting
only on a channel they were assigned to, or on a planned target of a piece that
was theirs. That second half is what Step 1F.2.3f.3 removes. It refused the
ordinary case it was written for - somebody posts the video on a colleague's
channel, or on a channel created last week that nobody has been assigned to yet -
with *"Bạn chưa được phép ghi nhận bài đăng trên kênh này."*, and the only ways
around it were an assignment nobody meant or a back-dated plan.

**Creating a publication is now the module's view rule and nothing narrower**:
whoever may read a piece of content may write down that it went out. That is the
same rule Step 1F.2.3g gave derivatives and comments, asked of the same
:meth:`~meobot.application.pr_content_service.PrContentService.require_viewable_content`
so there is one answer to "may this person see this piece" and not three.
``PR_PUBLICATION_REGISTER`` keeps its meaning as *administering publication
history* - correcting anybody's row, taking one back.

Widening **creation** widened nothing else, and the tests say so one by one.
Correcting is per row and follows ``publisher_user_id``; reversing stays
management-only; the channel, the output and the stage are checked exactly as
before, because none of them was ever a permission - they are what makes the row
true. See :meth:`PrPublicationService.may_record_publication`.

Step 1F.2.3f: which file, and which channel
--------------------------------------------

**Every new publication names exactly one output** - an original
``pr_production_submissions`` row or a ``pr_content_derivatives`` row, never
both and never neither. Before this, a publication said "this content was on
TikTok in October" and the useful question was *which cut* was on TikTok: a
piece has a 60-second master, a 25-second cutdown and a captioned variant, and
"we posted the content" does not identify any of them.

Both references are nullable columns, and only the "not both" half is a database
``CHECK``. Rows written before the revision reference no output and there is no
honest way to give them one - inventing a submission to satisfy an XOR would be
fabricating production lineage - so *"and not neither"* is enforced here, on new
writes, and legacy rows keep both ``NULL`` legitimately and permanently. See
``0025``.

**The channel no longer has to be a planned target.** That requirement was right
for a piece being published once, on the plan somebody wrote in August, and
wrong for the thing this content model is actually for: in October a TikTok
channel exists that did not exist when the targets were chosen, somebody makes a
cutdown, and posts it. Requiring a target there would leave exactly two
workarounds - clone the content, or back-date a plan nobody made - and both
corrupt the record worse than an unplanned publication ever could.

So the target is now **linkage, not permission**: when one exists it is marked
``PUBLISHED``, because that is a plan being fulfilled and every channel-level
report joins through it; when none exists the publication is recorded anyway,
against the canonical ``channel_id`` it already carries. What is published is
authoritative by channel, and the plan is a plan.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_content_work_projector import request_content_work_projection
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.db.models.pr import (
    PrChannel,
    PrContentItem,
    PrContentTarget,
)
from meobot.db.models.pr_content_asset import PrContentDerivative
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPostMetricSnapshot, PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.assets import normalize_publication_url
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import PrContentTargetStatus, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import (
    ACTIVE_PUBLICATION_STATUSES,
    PrPublicationStatus,
    is_active_publication,
)
from meobot.domain.pr.resources import normalize_note
from meobot.domain.pr.workflow import PUBLISHABLE_STAGES, PrTransitionTrigger

logger = get_logger(__name__)


# ``PUBLISHABLE_STAGES`` is imported above rather than defined here, and stays in
# ``__all__``: the rule is a domain fact - :mod:`meobot.domain.pr.workflow` owns
# it beside ``TERMINAL_STAGES`` and ``EDITABLE_STAGES`` - while this module is
# where it is enforced, and is what several callers import it from. One
# definition, two readers, no chance of two answers.


@dataclass(frozen=True, slots=True)
class RegisterPublicationCommand:
    """One occurrence of one content item appearing on one channel.

    **There is no ``code`` field**: Step 1C.1 allocates ``PUB-YYYY-nnnnnn``
    server-side, in the year ``published_at`` falls in locally - the explicit
    business instant, rather than whenever somebody got round to recording it.
    """

    content_id: uuid.UUID
    channel_id: uuid.UUID
    published_at: datetime
    #: Step 1F.2.3f. The original production submission that went out. Mutually
    #: exclusive with :attr:`derivative_id`, and **exactly one of the two is
    #: required** - see
    #: :meth:`PrPublicationService.register_publication`. Both left ``None`` is
    #: refused rather than stored: a publication that cannot say which file it
    #: was is the record this step exists to stop producing.
    production_submission_id: uuid.UUID | None = None
    #: Step 1F.2.3f. The derivative that went out - a cutdown, a caption variant.
    derivative_id: uuid.UUID | None = None
    platform_post_id: str | None = None
    url: str | None = None
    #: Step 1F.2.3f. The circumstance - *"đăng lại dịp khai trương"*. Optional
    #: prose; nothing parses it.
    note: str | None = None
    publisher_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class UpdatePublicationCommand:
    """A correction to a publication already on record. Step 1F.2.3f.1.

    ``None`` means "leave alone" on every field, as everywhere else in this
    module's siblings.

    **There is no ``channel_id`` and no output reference**, and their absence is
    the design rather than an omission. Those three columns are what the row
    *means* - "the 25-second cut went out on the Apexmed TikTok channel" - and
    rewriting one silently turns a record of one event into a record of a
    different event that nobody witnessed. A row entered against the wrong
    channel is reversed and re-entered, which leaves both facts visible.

    What is left is everything that is a *transcription* of the same event: the
    link somebody pasted, the time they typed, and the note beside it.
    """

    publication_id: uuid.UUID
    url: str | None = None
    published_at: datetime | None = None
    #: An empty string clears it; ``None`` leaves it alone, which is the rule
    #: :func:`~meobot.domain.pr.resources.normalize_note` already implements for
    #: every other note field in this module's neighbours.
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ReversalOutcome:
    """What one publication reversal did, and what it deliberately did not.

    ``stage_reverted`` is the interesting field. A reversal always marks the
    publication; it moves the content back only when the five conditions in
    :meth:`PrPublicationService.reverse_publication` hold, and when it does not,
    :attr:`reason` says which one stopped it - so a client can explain the
    outcome instead of showing a button that appeared to do nothing.
    """

    publication: PrPublication
    #: ``True`` when ``PUBLISHED -> READY_TO_PUBLISH`` was written.
    stage_reverted: bool
    #: Machine-readable, and set **only** when the stage did not move:
    #: ``other_active_publications``, ``has_metrics``, ``no_publication_transition``.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationOutcome:
    """What was recorded, and whether it was the one that made this public."""

    publication: PrPublication
    #: The planned target this fulfilled, when the channel was one that had been
    #: planned for. ``None`` since Step 1F.2.3f for a publication to a channel
    #: nobody planned - a channel created after the plan was written, which is
    #: the reuse case the step is built around. Absence here is a fact about the
    #: plan and never a refusal.
    target: PrContentTarget | None
    #: ``PUBLISHED`` when this call moved the content, ``None`` when it was
    #: already published and this was an additional channel.
    new_stage: PrWorkflowStage | None

    @property
    def was_first(self) -> bool:
        return self.new_stage is not None


class PrPublicationService:
    """Registers publications against planned targets.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        workflow: The only writer of ``workflow_stage``.
        capabilities: Resolves PR capabilities against roles and grants.
        codes: Allocates ``PUB-YYYY-nnnnnn`` on the same session.
        content: Owns the module's one answer to "may this actor see this
            piece" - Step 1F.2.3f.3's create rule, borrowed rather than
            restated. The same dependency
            :class:`~meobot.application.pr_content_asset_service.PrContentAssetService`
            takes for the same reason.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        workflow: PrContentWorkflowService,
        capabilities: PrCapabilityService,
        codes: PrCodeService,
        content: PrContentService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._workflow = workflow
        self._capabilities = capabilities
        self._codes = codes
        self._content = content

    async def register_publication(
        self, *, actor: Actor, request_id: uuid.UUID, command: RegisterPublicationCommand
    ) -> PublicationOutcome:
        """Record one publication, fulfil its target if it had one, publish on the first.

        The order is the rule, and it is validate-everything-then-write: the
        output reference, the channel, the URL and the publisher are all settled
        before a single row is added, so a refused request leaves no publication,
        no stage change and no audit row. One commit, taken by the caller.

        Raises:
            PrPermissionDeniedError: The actor may not **view** this content -
                Step 1F.2.3f.3. There is no channel condition any more: a viewer
                records a posting on any real channel, and the refusals below are
                what keeps that honest.
            PrNotFoundError: No such content, publisher, channel or output.
            PrWorkflowTransitionError: The content is not at a stage that may be
                published from.
            PrValidationError: Neither or both output references were given, the
                named output belongs to another content item, or the URL is
                unusable.
        """
        # Step 1F.2.3f.3. The view rule, asked before anything is loaded so a
        # non-viewer learns nothing about whether the id exists - and asked
        # through the module's one implementation of it rather than a copy. It
        # raises on both halves: the permission, then the item.
        await self._content.require_viewable_content(actor, command.content_id)
        content = await self._workflow.lock(command.content_id)

        code = await self._codes.allocate_publication_code(at=command.published_at)

        if content.workflow_stage not in PUBLISHABLE_STAGES:
            raise PrWorkflowTransitionError(
                "PR content is not ready to be published",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "allowed": sorted(stage.value for stage in PUBLISHABLE_STAGES),
                },
            )

        # Step 1F.2.3f. Which file went out, checked to belong to this content
        # item - never trusted from the request. See ``_require_output``.
        submission_id, derivative_id = await self._require_output(content, command)
        # Step 1F.2.3f. The channel is now checked here rather than implied by
        # the target lookup below: with the plan no longer a gate, a mistyped
        # ``channel_id`` would otherwise reach the insert and come back as a
        # foreign-key violation instead of a sentence naming the field.
        await self._require_channel(command.channel_id)
        # Linkage rather than permission: a channel nobody planned for is the
        # reuse case, not a mistake. See the module docstring.
        target = await self._planned_target(content.id, command.channel_id)
        url = normalize_publication_url(command.url) if command.url else None
        if (
            command.publisher_user_id is not None
            and await self._session.get(User, command.publisher_user_id) is None
        ):
            raise PrNotFoundError(
                "No user with that id to record as publisher",
                details={"publisher_user_id": str(command.publisher_user_id)},
            )

        publication = PrPublication(
            code=code,
            content_id=content.id,
            channel_id=command.channel_id,
            production_submission_id=submission_id,
            derivative_id=derivative_id,
            platform_post_id=command.platform_post_id,
            url=url,
            note=normalize_note(command.note),
            published_at=command.published_at,
            publisher_user_id=command.publisher_user_id or actor.user_id,
            status=PrPublicationStatus.PUBLISHED,
        )
        self._session.add(publication)

        if target is not None:
            target.status = PrContentTargetStatus.PUBLISHED
        await self._session.flush()

        new_stage: PrWorkflowStage | None = None
        if content.workflow_stage is PrWorkflowStage.READY_TO_PUBLISH:
            new_stage = PrWorkflowStage.PUBLISHED
            await self._workflow.apply(
                actor=actor,
                request_id=request_id,
                content=content,
                target=new_stage,
                trigger=PrTransitionTrigger.PUBLICATION,
                reason=f"publication:{code}",
            )

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PUBLICATION_REGISTERED,
            entity_type="pr_publication",
            entity_id=publication.id,
            after={
                "code": publication.code,
                "content_id": str(content.id),
                "content_code": content.code,
                "channel_id": str(command.channel_id),
                "published_at": command.published_at.isoformat(),
                "platform_post_id": command.platform_post_id,
                "first_publication": new_stage is not None,
                # Step 1F.2.3f. Which file went out, and where the reader can
                # find it. The URL is the public post; the output's own location
                # is not copied here - it is on the row this points at.
                "production_submission_id": str(submission_id) if submission_id else None,
                "derivative_id": str(derivative_id) if derivative_id else None,
                "url": url,
                "planned_target": target is not None,
            },
        )
        logger.info(
            "pr_publication_registered",
            extra={
                "pr_content_id": str(content.id),
                "pr_publication_id": str(publication.id),
                "first_publication": new_stage is not None,
            },
        )
        # M3. A publication is a work milestone of its own, and the second and
        # third channels of a fan-out change **no stage at all** - so the
        # workflow's own trigger would never fire for them and the postings
        # would go unprojected. Same transaction, same total operation, same
        # inability to fail a publication - see
        # ``request_content_work_projection``.
        await request_content_work_projection(self._session, publication.content_id)
        return PublicationOutcome(publication=publication, target=target, new_stage=new_stage)

    # --- Correcting and taking back, Step 1F.2.3f.1 ------------------------
    async def update_publication(
        self, *, actor: Actor, request_id: uuid.UUID, command: UpdatePublicationCommand
    ) -> PrPublication:
        """Correct the transcription of a publication already on record.

        The URL, the instant and the note - and nothing that defines what the row
        means. See :class:`UpdatePublicationCommand` on why the channel and the
        output are not here.

        The URL goes through
        :func:`~meobot.domain.pr.assets.normalize_publication_url`, the **same**
        validator :meth:`register_publication` uses: a scheme this product
        refuses on the way in must not become storable on the way through a
        correction.

        Raises:
            PrPermissionDeniedError: Not the publisher, and not management -
                Step 1F.2.3f.2.
            PrNotFoundError: No such publication.
            PrConflictError: The publication has been reversed, or its content is
                archived - both read-only.
            PrValidationError: The new URL is unusable.
        """
        publication = await self._require_publication(command.publication_id)
        content = await self._workflow.lock(publication.content_id)
        await self._require_may_edit(actor, publication)
        self._require_correctable(content, publication)

        before = _publication_snapshot(publication)
        if command.url is not None:
            publication.url = normalize_publication_url(command.url)
        if command.published_at is not None:
            publication.published_at = command.published_at
        if command.note is not None:
            publication.note = normalize_note(command.note)

        await self._session.flush()
        await self._session.refresh(publication)

        after = _publication_snapshot(publication)
        if before == after:
            # A form submitted unedited. No audit row, for the reason a no-op
            # priority change writes none.
            return publication

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PUBLICATION_UPDATED,
            entity_type="pr_publication",
            entity_id=publication.id,
            # Only what moved, so a reader sees the change rather than the row.
            before={key: value for key, value in before.items() if after[key] != value},
            after={
                "code": publication.code,
                "content_id": str(content.id),
                "content_code": content.code,
                **{key: value for key, value in after.items() if before[key] != value},
            },
        )
        logger.info(
            "pr_publication_updated",
            extra={
                "pr_content_id": str(content.id),
                "pr_publication_id": str(publication.id),
                "changed": sorted(key for key in after if after[key] != before[key]),
            },
        )
        return publication

    async def reverse_publication(
        self, *, actor: Actor, request_id: uuid.UUID, publication_id: uuid.UUID
    ) -> ReversalOutcome:
        """Take back a publication recorded in error, without erasing it.

        **The row stays.** Publication history is operational evidence and a
        correction is not a deletion, so this marks the row
        :attr:`~meobot.domain.pr.reporting.PrPublicationStatus.REVERSED` and
        leaves everything else about it exactly where it was - the channel, the
        output, the URL, the instant, the person who recorded it. What changes is
        that it stops counting as something that happened.

        Whether the content's stage follows is a **separate** decision, taken
        here rather than by the caller, and it needs all five of:

        1. the content is at ``PUBLISHED`` - not ``MEASURED``, not ``ARCHIVED``.
           Enforced before anything is written, because at ``MEASURED`` the
           reversal itself is refused;
        2. **no other active publication remains.** A piece on Facebook and
           TikTok that loses its TikTok row is still published;
        3. **no metric snapshot exists against any of this content's
           publications.** Numbers were read off a real post; un-publishing the
           content underneath them would leave a report describing a piece the
           record says never went out;
        4. a ``PUBLICATION`` transition to ``PUBLISHED`` is **still in force** -
           un-reversed, in the structured history Step 1F.2.3b added. This is
           what "do not rely on the count alone" means: the count says nothing
           is out there now, and the history says which event put it there and
           that nobody has taken it back already;
        5. that event is genuinely reversible, which
           :meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.apply`
           re-checks against the matrix.

        When 2, 3 or 4 fails the publication is still reversed and the stage is
        left alone - :attr:`ReversalOutcome.reason` says which, so a client can
        explain rather than look inert.

        This is **not** the generic undo. Step 1F.2.3b's
        :class:`~meobot.application.pr_undo_service.PrWorkflowUndoService` reverses
        *decisions* and classifies only ``HUMAN_APPROVAL`` edges, so it has never
        offered to un-publish anything and still does not. Reversing a publication
        is a business action tied to one publication row, and the server decides
        whether a stage change follows.

        Raises:
            PrPermissionDeniedError: The actor may not manage publications.
            PrNotFoundError: No such publication.
            PrConflictError: Already reversed, the content is not at
                ``PUBLISHED``, or measurements hang off this very publication.
        """
        if not await self.may_reverse_publication(actor):
            # Management only, and unchanged by Step 1F.2.3f.2's contributor
            # capability - see :meth:`may_reverse_publication`.
            await self._capabilities.require(actor, PrCapability.PR_PUBLICATION_REGISTER)
        publication = await self._require_publication(publication_id)
        content = await self._workflow.lock(publication.content_id)

        if not is_active_publication(publication.status):
            raise self._refusal(content, publication, "already_reversed")
        if content.workflow_stage is not PrWorkflowStage.PUBLISHED:
            # ``MEASURED`` and ``ARCHIVED`` land here, and deliberately: numbers
            # have been read off this piece, or it has been put away. Reversing a
            # publication underneath either would make the later state describe
            # something the record no longer says happened, and the honest fix is
            # a correction to the numbers or a new publication - not a rewrite.
            raise self._refusal(content, publication, "stage_not_publishable_back")
        if await self._has_metrics(publication_id=publication.id):
            # Its *own* measurements. Reversing would leave observations claiming
            # numbers for a posting the record says never took place.
            raise self._refusal(content, publication, "has_metrics")

        publication.status = PrPublicationStatus.REVERSED
        await self._session.flush()

        reason = await self._blocks_stage_reversal(content)
        reverted = False
        if reason is None:
            event = await self._publication_transition(content.id)
            if event is None:
                reason = "no_publication_transition"
            else:
                await self._workflow.apply(
                    actor=actor,
                    request_id=request_id,
                    content=content,
                    target=PrWorkflowStage.READY_TO_PUBLISH,
                    trigger=PrTransitionTrigger.UNDO,
                    reason=f"publication_reversed:{publication.code}",
                    reverses=event,
                )
                reverted = True

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PUBLICATION_REVERSED,
            entity_type="pr_publication",
            entity_id=publication.id,
            before={"status": PrPublicationStatus.PUBLISHED.value},
            after={
                "status": publication.status.value,
                "code": publication.code,
                "content_id": str(content.id),
                "content_code": content.code,
                "channel_id": str(publication.channel_id),
                "stage_reverted": reverted,
                "workflow_stage": content.workflow_stage.value,
                "reason": reason,
            },
        )
        logger.info(
            "pr_publication_reversed",
            extra={
                "pr_content_id": str(content.id),
                "pr_publication_id": str(publication.id),
                "stage_reverted": reverted,
            },
        )
        # M3. The reversal may leave the stage where it is - one channel of
        # three - so the work that came from this posting has to be reconciled
        # out on the strength of the row's status alone.
        await request_content_work_projection(self._session, publication.content_id)
        return ReversalOutcome(publication=publication, stage_reverted=reverted, reason=reason)

    # --- Reading ----------------------------------------------------------
    async def list_publications(
        self, content_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[PrPublication]:
        """Every occurrence of one content item, newest first.

        ``code`` is the second sort key and it is not decoration: two postings
        recorded with the same ``published_at`` - a fan-out registered in one
        sitting, a back-filled campaign - would otherwise come back in whatever
        order the database chose, and a history that reorders itself between two
        opens of the same page is a history nobody trusts. The code is unique and
        allocated in sequence, so the order is total.
        """
        result = await self._session.execute(
            select(PrPublication)
            .where(PrPublication.content_id == content_id)
            .order_by(PrPublication.published_at.desc(), PrPublication.code.desc())
            .limit(limit)
        )
        return result.scalars().all()

    # --- Internals --------------------------------------------------------
    async def _require_output(
        self, content: PrContentItem, command: RegisterPublicationCommand
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        """Which produced file went out: exactly one, and this item's own.

        Step 1F.2.3f, and three refusals rather than one, because they are three
        different mistakes:

        * **neither** - a publication that cannot say which cut it was. Legacy
          rows are like this and cannot be helped; a new one is a record being
          written incomplete, and the database cannot refuse it without
          fabricating lineage for the old ones, so it is refused here;
        * **both** - meaningless under any reading. Refused here *and* by
          ``ck_pr_publications_output_not_both``, because unlike "neither" it is
          safe to assert about every row that has ever existed;
        * **somebody else's output.** The commonest way a client gets this wrong
          is a stale form, and the result would be a publication claiming a file
          that came out of a different piece of content. The ids are looked up
          and their ``content_id`` compared - never trusted from the request.
        """
        submission_id = command.production_submission_id
        derivative_id = command.derivative_id
        if submission_id is not None and derivative_id is not None:
            raise PrValidationError(
                "A publication names one produced output, not two",
                details={
                    "field": "production_submission_id",
                    "reason": "output_not_both",
                    "production_submission_id": str(submission_id),
                    "derivative_id": str(derivative_id),
                },
            )
        if submission_id is None and derivative_id is None:
            raise PrValidationError(
                "A publication must name the produced output that was published",
                details={
                    "field": "production_submission_id",
                    "reason": "output_required",
                    "content_id": str(content.id),
                },
            )

        if submission_id is not None:
            submission = await self._session.get(PrProductionSubmission, submission_id)
            if submission is None:
                raise PrNotFoundError(
                    "No production submission with that id",
                    details={"production_submission_id": str(submission_id)},
                )
            self._require_own(
                submission.content_id,
                content,
                field="production_submission_id",
                value=submission_id,
            )
            return submission_id, None

        derivative = await self._session.get(PrContentDerivative, derivative_id)
        if derivative is None:
            raise PrNotFoundError(
                "No content derivative with that id",
                details={"derivative_id": str(derivative_id)},
            )
        self._require_own(
            derivative.content_id, content, field="derivative_id", value=derivative_id
        )
        return None, derivative_id

    @staticmethod
    def _require_own(
        owner_id: uuid.UUID, content: PrContentItem, *, field: str, value: uuid.UUID | None
    ) -> None:
        """The output belongs to the content being published."""
        if owner_id != content.id:
            raise PrValidationError(
                "That produced output belongs to another content item",
                details={
                    "field": field,
                    "reason": "foreign_output",
                    field: str(value),
                    "content_id": str(content.id),
                    "content_code": content.code,
                },
            )

    # --- Who may do what, Step 1F.2.3f.2 / 1F.2.3f.3 -----------------------
    async def may_record_publication(self, actor: Actor, content: PrContentItem) -> bool:
        """May this actor record a publication against this item? Step 1F.2.3f.3.

        **The view rule, and nothing narrower.** Whoever may read the piece may
        write down that it went out - the same answer
        :class:`~meobot.application.pr_query_service.PrQueryService` gives before
        it will show the piece at all, read here through
        :meth:`~meobot.application.pr_content_service.PrContentService.may_view`
        rather than restated, so a future narrowing of "who may view" narrows
        recording with it.

        What this replaces is a **channel** condition: an in-force
        ``pr_channel_assignments`` row, or a planned target of a piece the actor
        was responsible for. It refused the ordinary case - the person who
        actually posted the video is routinely neither the channel's assignee nor
        the content's owner - and the two ways around it, an assignment nobody
        meant and a back-dated plan, both put a worse fact in the database than
        the publication they were working around.

        The channel is still checked, and so are the output, the stage and the
        URL: see :meth:`register_publication`. None of them is a permission.
        ``channel_id`` is deliberately **not** a parameter any more, because no
        answer depends on it any longer - an action list and a write now ask
        exactly the same question.

        Read-only and never raises.
        """
        del content
        return self._content.may_view(actor)

    async def may_edit_publication(self, actor: Actor, publication: PrPublication) -> bool:
        """May this actor correct **this** publication?

        Its own answer per row, because ownership is a property of the row:

        * ``PR_PUBLICATION_REGISTER`` - management, any publication, as in Step
          1F.2.3f.1;
        * otherwise **the person recorded as having published it**. Read off
          ``publisher_user_id``, which is the canonical attribution and is what
          answers "ai add link đăng bài" - deliberately not the content's owner
          or the responsible user, who may never have touched this posting.

        Unchanged by Step 1F.2.3f.3, which widened **creating** only: a viewer
        who recorded a posting corrects their own typo and nobody else's link,
        and holds nothing over the row beside it.
        """
        if await self._capabilities.allows(actor, PrCapability.PR_PUBLICATION_REGISTER):
            return True
        if actor.user_id is None or publication.publisher_user_id is None:
            return False
        if publication.publisher_user_id != actor.user_id:
            return False
        return await self._capabilities.allows(actor, PrCapability.PR_PUBLICATION_CREATE)

    async def may_reverse_publication(self, actor: Actor) -> bool:
        """May this actor take a publication back?

        **Management only**, unchanged from Step 1F.2.3f.1 and deliberately not
        widened here. Recording a posting is a statement about your own work;
        reversing one edits the distribution history and can move the content's
        stage back, which is a decision about the record rather than a
        contribution to it. A contributor who recorded the wrong thing corrects
        the link, or asks somebody who administers publications.
        """
        return await self._capabilities.allows(actor, PrCapability.PR_PUBLICATION_REGISTER)

    async def _require_may_edit(self, actor: Actor, publication: PrPublication) -> None:
        """The edit rule, as a refusal naming whose publication it is."""
        if await self.may_edit_publication(actor, publication):
            return
        raise PrPermissionDeniedError(
            "Bạn chỉ sửa được bài đăng do mình ghi nhận.",
            details={
                "publication_id": str(publication.id),
                "publication_code": publication.code,
                "capability": PrCapability.PR_PUBLICATION_REGISTER.value,
                "reason": "not_the_publisher",
            },
        )

    async def _require_publication(self, publication_id: uuid.UUID) -> PrPublication:
        found = await self._session.get(PrPublication, publication_id)
        if found is None:
            raise PrNotFoundError(
                "No publication with that id",
                details={"publication_id": str(publication_id)},
            )
        return found

    @staticmethod
    def _require_correctable(content: PrContentItem, publication: PrPublication) -> None:
        """A publication may be corrected unless it is history or the piece is filed.

        Two refusals, both `409`:

        * **already reversed.** The row says the posting never happened; editing
          the URL on it would be maintaining a link for an event nobody claims;
        * **archived content.** ``ARCHIVED`` is where a piece's life ends -
          nothing transitions out of it and nothing deletes it - and its
          publication history is part of what was put away. Read-only, matching
          the archive rules this repository already has rather than inventing a
          softer one for this table.

        ``MEASURED`` is deliberately **not** here: correcting a mistyped link on
        a piece whose numbers have been read is exactly the correction somebody
        needs to make, and it changes nothing the measurement depends on.
        """
        if not is_active_publication(publication.status):
            raise PrConflictError(
                "This publication has been reversed and is part of the record now",
                details={
                    "publication_id": str(publication.id),
                    "publication_code": publication.code,
                    "status": publication.status.value,
                    "reason": "already_reversed",
                },
            )
        if content.workflow_stage is PrWorkflowStage.ARCHIVED:
            raise PrConflictError(
                "Archived content's publication history is read-only",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "workflow_stage": content.workflow_stage.value,
                    "reason": "archived",
                },
            )

    @staticmethod
    def _refusal(
        content: PrContentItem, publication: PrPublication, reason: str
    ) -> PrConflictError:
        """One shape for every reversal refusal, so a client branches on ``reason``."""
        return PrConflictError(
            "This publication cannot be reversed",
            details={
                "content_id": str(content.id),
                "content_code": content.code,
                "publication_id": str(publication.id),
                "publication_code": publication.code,
                "workflow_stage": content.workflow_stage.value,
                "status": publication.status.value,
                "reason": reason,
            },
        )

    async def _blocks_stage_reversal(self, content: PrContentItem) -> str | None:
        """What, if anything, stops the content going back to *Sẵn sàng đăng*.

        ``None`` means nothing does. Called **after** the publication has been
        marked reversed, so "another active publication" is genuinely another
        one - see :func:`~meobot.domain.pr.reporting.is_active_publication` for
        what active means and why ``REMOVED`` counts as one.
        """
        others = await self._session.scalar(
            select(
                exists().where(
                    PrPublication.content_id == content.id,
                    PrPublication.status.in_(sorted(ACTIVE_PUBLICATION_STATUSES)),
                )
            )
        )
        if bool(others):
            return "other_active_publications"
        if await self._has_metrics(content_id=content.id):
            return "has_metrics"
        return None

    async def _has_metrics(
        self, *, publication_id: uuid.UUID | None = None, content_id: uuid.UUID | None = None
    ) -> bool:
        """Has anybody read numbers off this publication, or off this content's?

        One query for both questions because they are the same join with a
        different anchor, and both are asked for the same reason: a metric
        snapshot is an observation of a real post, and un-publishing what it
        describes would leave a report about a piece the record says never went
        out.
        """
        condition = (
            PrPublication.id == publication_id
            if publication_id is not None
            else PrPublication.content_id == content_id
        )
        found = await self._session.scalar(
            select(
                exists().where(
                    PrPostMetricSnapshot.publication_id == PrPublication.id,
                    condition,
                )
            )
        )
        return bool(found)

    async def _publication_transition(
        self, content_id: uuid.UUID
    ) -> PrContentTransitionEvent | None:
        """The ``READY_TO_PUBLISH -> PUBLISHED`` move that is still in force.

        Step 1F.2.3b's structured history, asked the one question a reversal
        needs: *which event put this content here, and has anybody taken it back
        already?* ``reversed_by_event_id IS NULL`` is that second half, and it is
        why two reversals cannot both claim to have un-published the same piece.

        Newest first, because a piece may legitimately have been published,
        reversed and published again - and the one to take back is the last one
        standing.
        """
        result = await self._session.execute(
            select(PrContentTransitionEvent)
            .where(
                PrContentTransitionEvent.content_id == content_id,
                PrContentTransitionEvent.to_stage == PrWorkflowStage.PUBLISHED,
                PrContentTransitionEvent.trigger == PrTransitionTrigger.PUBLICATION,
                PrContentTransitionEvent.reversed_by_event_id.is_(None),
            )
            .order_by(PrContentTransitionEvent.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _require_channel(self, channel_id: uuid.UUID) -> PrChannel:
        """The channel this went out on. It must exist; it need not be active.

        No status check, deliberately. A publication is a record of something
        that already happened, and a channel the team has since retired is
        exactly the kind of channel a back-filled posting belongs to - refusing
        it would make the history unrecordable to protect a rule about *planning*
        new work. Choosing a channel to publish *to* is a different act on a
        different screen, and that picker offers active channels only.
        """
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No channel with that id",
                details={"field": "channel_id", "channel_id": str(channel_id)},
            )
        return channel

    async def _planned_target(
        self, content_id: uuid.UUID, channel_id: uuid.UUID
    ) -> PrContentTarget | None:
        """The planned target for this channel, when there is one.

        ``None`` is an ordinary answer since Step 1F.2.3f and never a refusal -
        see the module docstring on why publishing to a channel nobody planned
        for is the reuse case rather than a mistake.
        """
        result = await self._session.execute(
            select(PrContentTarget).where(
                PrContentTarget.content_id == content_id,
                PrContentTarget.channel_id == channel_id,
            )
        )
        return result.scalars().one_or_none()

    @staticmethod
    def _require_text(value: str, field_name: str, max_length: int) -> str:
        text = (value or "").strip()
        if not text:
            raise PrValidationError(
                f"Publication {field_name} must not be blank", details={"field": field_name}
            )
        if len(text) > max_length:
            raise PrValidationError(
                f"Publication {field_name} is longer than {max_length} characters",
                details={"field": field_name, "max_length": max_length},
            )
        return text


def _publication_snapshot(publication: PrPublication) -> dict[str, object]:
    """The correctable fields, as the audit trail records them.

    Three, and no more - the channel and the output are not correctable, so they
    never appear on either side of a change. Nothing here carries content.

    The instant is normalised to UTC before it is rendered. The column is
    ``DateTime(timezone=True)`` and everything written to it is UTC, but SQLite
    has no timezone type and hands the value back untagged - so a snapshot taken
    before a flush and one taken after would render the *same* instant as
    ``...+00:00`` and ``...``, and the "only what moved" filter below would
    report a change nobody made.
    """
    published_at = publication.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    return {
        "url": publication.url,
        "published_at": published_at.astimezone(UTC).isoformat(),
        "note": publication.note,
    }


__all__: list[str] = [
    "PUBLISHABLE_STAGES",
    "PrPublicationService",
    "PublicationOutcome",
    "RegisterPublicationCommand",
    "ReversalOutcome",
    "UpdatePublicationCommand",
]
