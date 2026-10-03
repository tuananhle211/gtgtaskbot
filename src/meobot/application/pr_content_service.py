"""Creating content and writing new drafts of it.

The one rule this service exists to keep: **the current projection on
``pr_content_items`` and the newest row in ``pr_content_versions`` always
describe the same draft.** They are written in one transaction, under one lock,
or neither is written. A projection that disagreed with the version history
would be worse than having no projection, because every list view would show a
title no reviewer ever saw.

Why revision is so narrow
-------------------------

:meth:`PrContentService.revise_content` works while the content is at ``IDEA``,
``BRIEFING`` or ``SCRIPTING`` and nowhere else. From ``AI_REVIEW`` onwards
somebody - or something - is forming a judgement about a specific draft, and a
draft that changes underneath a reviewer makes their verdict a statement about
text that no longer exists. Content that needs changing after review gets sent
back by a reviewer, which returns it to ``SCRIPTING`` and *then* lets it be
rewritten. That is a longer path on purpose: it leaves a record of why the
rewrite happened.

``expected_version`` is required, not optional
-----------------------------------------------

A caller revising a draft is holding a screen. If somebody else has written a
version since that screen was drawn, the edit is being applied to text the
author never read, and merging it silently would lose one of the two edits with
nothing to show for it. So the version the caller believes is current is part
of the command, and a mismatch is
:class:`~meobot.domain.pr.errors.PrStaleVersionError` rather than a write.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_query import responsible_for
from meobot.application.pr_content_resource_support import (
    ContentResourceSpec,
    build_content_resource,
    record_resource_event,
    resource_refusal_index,
    validated_resource_spec,
)
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.base import Base
from meobot.db.models.pr import (
    PrBrand,
    PrChannel,
    PrContentItem,
    PrContentTarget,
    PrPlatform,
)
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrStaleVersionError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import (
    POLICY_GROUNDED_PLATFORM_CODES,
    PrChannelStatus,
    PrContentType,
    PrDistributionMode,
    PrPriority,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import (
    PR_READ_PERMISSION,
    PrCapability,
    may_read,
    require_permission,
)
from meobot.domain.pr.workflow import EDITABLE_STAGES

logger = get_logger(__name__)

#: The longest a title may be, matching ``pr_content_items.title`` and
#: ``pr_content_versions.title``. Checked here so the failure is a typed
#: domain error rather than a database truncation or a driver exception.
MAX_TITLE_LENGTH = 300


@dataclass(frozen=True, slots=True)
class ContentTargetSpec:
    """One channel a piece of content is planned for.

    ``distribution_mode`` is required for a policy-grounded platform and
    rejected for none: which official policy applies depends on it, and Step
    1F.1 blocks AI review until it is known. Supplying it at creation is what
    stops a piece existing in the ambiguous state.
    """

    channel_id: uuid.UUID
    target_publish_at: object | None = None
    adaptation_note: str | None = None
    distribution_mode: PrDistributionMode = PrDistributionMode.UNSPECIFIED


@dataclass(frozen=True, slots=True)
class CreateContentCommand:
    """Everything needed to bring a content item into existence.

    **There is no ``code`` field.** Step 1C required one because no safe
    allocator existed; Step 1C.1 added
    :class:`~meobot.application.pr_code_service.PrCodeService`, and the field
    was removed rather than made optional so there is exactly one way a code
    comes into being. A caller cannot supply ``CNT-2026-000001`` and cannot
    collide with one.
    """

    title: str
    brand_id: uuid.UUID
    owner_user_id: uuid.UUID
    format_id: uuid.UUID | None = None
    pillar_id: uuid.UUID | None = None
    topic: str | None = None
    hook: str | None = None
    brief: str | None = None
    script_text: str | None = None
    priority: PrPriority = PrPriority.NORMAL
    #: Step 1F.2.3e. Which of the six formats this is.
    #:
    #: Optional *here* and required at both human-facing entry points, through
    #: ``require_content_type`` below - the same shape ``require_targets`` uses,
    #: and for a related reason. Making it mandatory on the dataclass would force
    #: every internal caller and every fixture to name a format, including the
    #: tests whose whole subject is content that has none: rows created before
    #: the column existed are ``NULL``, and a suite that could not construct that
    #: state could not test it.
    content_type: PrContentType | None = None
    planned_publish_at: object | None = None
    targets: tuple[ContentTargetSpec, ...] = field(default_factory=tuple)
    #: Step 1F.2.3e.1. Review material to attach as part of this creation.
    #:
    #: Zero to many, and **zero is the ordinary case** - a piece created with no
    #: brief yet is not incomplete, and nothing here refuses one. What this field
    #: exists for is the opposite case: somebody who already has the brief, the
    #: packshot and two reference videos in front of them should not have to
    #: create the item, wait, and then attach four things to it one at a time.
    #:
    #: They are part of the same aggregate and the same transaction, so an
    #: unacceptable location in the third of four takes the content item with it
    #: rather than leaving a piece created from a form somebody thought had
    #: failed.
    initial_resources: tuple[ContentResourceSpec, ...] = field(default_factory=tuple)
    #: Refuse a content item with no planned channel. Set by the web create
    #: route; left off for the Telegram tool and any internal caller that
    #: legitimately drafts before the channels are decided. Targetless content
    #: is not forbidden here - it is just not what the web flow produces.
    require_targets: bool = False
    #: Refuse a content item with no format. Set by **both** human-facing create
    #: paths - the web form and the Telegram tool - because Step 1F.2.3e's rule
    #: is that everything created from now on has a type. Left off for internal
    #: callers and fixtures, which is what keeps legacy ``NULL`` constructible.
    require_content_type: bool = False


@dataclass(frozen=True, slots=True)
class ReviseContentCommand:
    """A new draft of an existing content item.

    Every field is the *new* value for that draft. Fields left ``None`` are
    carried forward from the current version rather than cleared - a caller
    editing only the hook should not have to resend the brief, and a version
    row with holes in it would not be a draft anybody could review.
    """

    content_id: uuid.UUID
    expected_version: int
    title: str | None = None
    topic: str | None = None
    hook: str | None = None
    brief: str | None = None
    script_text: str | None = None
    change_note: str | None = None
    priority: PrPriority | None = None


@dataclass(frozen=True, slots=True)
class ContentSnapshot:
    """A content item together with the draft that is currently true of it."""

    content: PrContentItem
    version: PrContentVersion

    @property
    def version_no(self) -> int:
        return self.version.version_no


class PrContentService:
    """Content items and their immutable draft history.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
        codes: Allocates the human-readable code, on the same session, so the
            number and the row it names commit together.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        codes: PrCodeService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._codes = codes

    # --- Creation ---------------------------------------------------------
    async def create_content(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreateContentCommand
    ) -> ContentSnapshot:
        """Create the item, version 1 and the planned targets, atomically.

        The code is allocated here, in this transaction, so a rolled-back
        creation cannot leave a code pointing at nothing - and, because the
        counter is a table row rather than a sequence, hands the number back
        for the next caller to use.

        The stage is :attr:`~meobot.domain.pr.models.PrWorkflowStage.IDEA`,
        which is the column default and is set explicitly anyway so that the
        starting point is a decision in this file rather than a fact about a
        migration.

        Initial resources - Step 1F.2.3e.1 - are part of that "atomically". They
        are validated before anything is written, built by the same code path
        ``add_resource`` uses, and audited with the same
        ``pr.content.resource_added`` event; a refusal on any of them leaves no
        content row, no resource row and no audit row behind.

        Raises:
            PrPermissionDeniedError: The actor may not create content.
            PrValidationError: A required field is blank or too long, or an
                initial resource has a blank label or an unacceptable location.
                ``details['initial_resource_index']`` says which one.
            PrNotFoundError: The brand, owner or a target channel is unknown.
            PrConflictError: The same channel was requested twice as a target.
        """
        await self._capabilities.require(actor, PrCapability.PR_CONTENT_CREATE)

        if command.require_content_type and command.content_type is None:
            # Refused before the code is allocated, so a form submitted without a
            # format does not burn a ``CNT-…`` number on an item that never
            # exists. Step 1F.2.3e.
            raise PrValidationError(
                "New content must have a content type",
                details={"field": "content_type", "reason": "required"},
            )

        title = self._require_text(command.title, "title", MAX_TITLE_LENGTH)
        # Step 1F.2.3e.1: every draft resource is checked *before* a code is
        # allocated and before a row exists, for the reason the content-type
        # refusal above is where it is - a form refused for a bad paste should
        # cost nothing. The rows themselves cannot be built until the content id
        # exists, which is why this is validation now and construction later.
        resources = [
            self._validated_resource(spec, index)
            for index, spec in enumerate(command.initial_resources)
        ]
        code = await self._codes.allocate_content_code(at=utcnow())
        await self._require_exists(PrBrand, command.brand_id, "brand")
        await self._require_exists(User, command.owner_user_id, "owner_user")

        content = PrContentItem(
            code=code,
            title=title,
            brand_id=command.brand_id,
            format_id=command.format_id,
            pillar_id=command.pillar_id,
            topic=command.topic,
            hook=command.hook,
            brief=command.brief,
            priority=command.priority,
            content_type=command.content_type,
            workflow_stage=PrWorkflowStage.IDEA,
            owner_user_id=command.owner_user_id,
            planned_publish_at=command.planned_publish_at,
            created_by_user_id=self._author(actor, command.owner_user_id),
        )
        self._session.add(content)
        await self._session.flush()

        version = await self._append_version(
            content=content,
            version_no=1,
            title=title,
            topic=command.topic,
            hook=command.hook,
            brief=command.brief,
            script_text=command.script_text,
            change_note=None,
            author_user_id=self._author(actor, command.owner_user_id),
        )

        if command.require_targets and not command.targets:
            raise PrValidationError(
                "Vui lòng chọn ít nhất một kênh dự kiến.",
                details={"reason": "targets_required"},
            )

        seen: set[uuid.UUID] = set()
        for spec in command.targets:
            if spec.channel_id in seen:
                raise PrConflictError(
                    "Một kênh chỉ được chọn một lần.",
                    details={"channel_id": str(spec.channel_id)},
                )
            seen.add(spec.channel_id)
            await self._require_target_channel(spec)
            self._session.add(
                PrContentTarget(
                    content_id=content.id,
                    channel_id=spec.channel_id,
                    target_publish_at=spec.target_publish_at,
                    adaptation_note=spec.adaptation_note,
                    distribution_mode=spec.distribution_mode,
                )
            )
        # Step 1F.2.3e.1. Attached here rather than by a second request from the
        # client: N requests after a committed create is how a piece ends up
        # existing with two of its four references, and the person who filled the
        # form seeing an error that mentions neither.
        attached = [
            build_content_resource(
                spec,
                content_id=content.id,
                # The same author the content row gets, including the bootstrap
                # fallback - see ``_author``. The alternative is a create that
                # succeeds for the item and refuses its resources, which is the
                # split this whole step exists to avoid.
                author_user_id=self._author(actor, command.owner_user_id),
            )
            for spec in resources
        ]
        self._session.add_all(attached)
        # One transaction: the content row, every target, every initial resource
        # and the audit events commit together or not at all. A target that fails
        # validation takes the content item with it rather than leaving an orphan
        # whose policy context nobody set.
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_CREATED,
            entity_type="pr_content_item",
            entity_id=content.id,
            after={
                "code": content.code,
                "brand_id": str(content.brand_id),
                "owner_user_id": str(content.owner_user_id),
                "workflow_stage": content.workflow_stage.value,
                "version_no": version.version_no,
                "target_channel_ids": sorted(str(channel_id) for channel_id in seen),
            },
        )
        await self._record_version_event(
            request_id=request_id, actor=actor, content=content, version=version
        )
        for resource in attached:
            # ``pr.content.resource_added``, the same action and the same payload
            # a resource attached tomorrow gets. A separate
            # ``initial_resource_added`` vocabulary would mean every reader of
            # the trail had to know both to answer one question.
            await record_resource_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_CONTENT_RESOURCE_ADDED,
                content=content,
                resource=resource,
            )
        logger.info(
            "pr_content_created",
            extra={
                "pr_content_id": str(content.id),
                "code": content.code,
                "initial_resources": len(attached),
            },
        )
        return ContentSnapshot(content=content, version=version)

    # --- Revision ---------------------------------------------------------
    async def revise_content(
        self, *, actor: Actor, request_id: uuid.UUID, command: ReviseContentCommand
    ) -> ContentSnapshot:
        """Write draft N+1 and move the projection onto it.

        The content row is locked first, so the version this reads as latest is
        still the latest when the new one is written. Without that, two callers
        both revising version 4 would both compute 5 and one insert would fail
        on the unique index - correct, but as a database error rather than a
        typed conflict, and only by luck of timing.

        Raises:
            PrPermissionDeniedError: The actor may not write content.
            PrNotFoundError: No such content, or it has no versions.
            PrWorkflowTransitionError: The content is past the point where the
                draft may still change.
            PrStaleVersionError: ``expected_version`` is not the latest.
        """
        await self._capabilities.require(actor, PrCapability.PR_CONTENT_EDIT)

        content = await self._lock_content(command.content_id)
        if content.workflow_stage not in EDITABLE_STAGES:
            raise PrWorkflowTransitionError(
                "PR content may only be revised before it is under review",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "editable": sorted(stage.value for stage in EDITABLE_STAGES),
                },
            )

        current = await self.current_version(content.id)
        if current is None:
            raise PrNotFoundError(
                "PR content has no versions to revise",
                details={"content_id": str(content.id)},
            )
        if command.expected_version != current.version_no:
            raise PrStaleVersionError(
                "PR content has been revised since it was read",
                details={
                    "content_id": str(content.id),
                    "expected_version": command.expected_version,
                    "current_version": current.version_no,
                },
            )

        title = (
            self._require_text(command.title, "title", MAX_TITLE_LENGTH)
            if command.title is not None
            else current.title
        )
        version = await self._append_version(
            content=content,
            version_no=current.version_no + 1,
            title=title,
            topic=self._carry(command.topic, current.topic),
            hook=self._carry(command.hook, current.hook),
            brief=self._carry(command.brief, current.brief),
            script_text=self._carry(command.script_text, current.script_text),
            change_note=command.change_note,
            author_user_id=self._author(actor, content.owner_user_id),
        )

        # The projection follows the draft, in the same transaction. Nothing
        # else in the module writes these four columns.
        content.title = version.title
        content.topic = version.topic
        content.hook = version.hook
        content.brief = version.brief
        if command.priority is not None:
            content.priority = command.priority
        await self._session.flush()

        await self._record_version_event(
            request_id=request_id, actor=actor, content=content, version=version
        )
        logger.info(
            "pr_content_version_created",
            extra={"pr_content_id": str(content.id), "version_no": version.version_no},
        )
        return ContentSnapshot(content=content, version=version)

    # --- Priority ---------------------------------------------------------
    async def set_content_priority(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        priority: PrPriority,
    ) -> PrContentItem:
        """Retriage one content item. **No new version.**

        Step 1F.2.3d. Until this existed, the only way to change a priority was
        :meth:`revise_content`, which writes an immutable
        :class:`~meobot.db.models.pr_content_version.PrContentVersion` and
        refuses outside :data:`~meobot.domain.pr.workflow.EDITABLE_STAGES`. Both
        halves of that were wrong for this:

        * a **version** is a draft of the script - what the piece *says*. Priority
          is what the queue should do with it *next*, which is not a property of
          any draft, and recording "v4" because a manager marked something urgent
          would put a row in the review history that no reviewer wrote;
        * the **stage restriction** made the field unchangeable exactly when it
          matters. A piece at ``HEAD_REVIEW`` or in production is precisely the
          thing somebody needs to escalate; a triage attribute that freezes once
          the work starts is a triage attribute nobody can use.

        So this writes one column and one audit row, and touches nothing else -
        not the stage, not the version, not the approval history. Priority is
        operational metadata in the strict sense: it changes what the queue
        *shows first*, and no workflow, gate or authorization rule reads it.

        Who may
        -------

        The same two-tier shape
        :class:`~meobot.application.pr_lifecycle_service.PrLifecycleService`
        uses, and deliberately not a new capability:

        * everybody needs ``PR_CONTENT_EDIT`` - this is a change to what the
          content *is*, on the same footing as
          ``set_target_distribution_mode``, and Step 1C.1's vocabulary is ten
          write capabilities rather than one per field;
        * **management** - ``PR_CONTENT_CANCEL``, whoever may already end this
          piece of work - retriages anything. Somebody trusted to cancel a piece
          is trusted to mark it urgent;
        * a **member** retriages what they are responsible for, via
          :func:`~meobot.application.pr_content_query.responsible_for` - the
          same predicate behind *Của tôi* and the "Người phụ trách" filter, so
          "what I may reprioritise" and "what the panel calls mine" cannot drift
          apart.

        There is no super-admin bypass, and an unrelated member holding
        ``PR_CONTENT_EDIT`` is refused: the capability says they may edit content,
        and the responsibility check says *which*.

        Notifications
        -------------

        **None**, and that is a decision rather than an omission. A manager
        sweeping a backlog changes twenty priorities in a minute, and twenty
        notifications is how people learn to ignore the bell. The change is
        audited, it is visible on the card and on the detail page, and
        ``MY_ACTIONS`` already puts the work in front of the person who has to
        do it. See ``docs/pr/STEP_1F23D_NOTIFICATIONS_AND_PRIORITY.md``.

        Args:
            actor: Gated as above.
            request_id: Correlation id for the audit row.
            content_id: The item to retriage. Permanently deleted content has no
                row, so a deleted id is a :class:`PrNotFoundError` here rather
                than a separate check - Step 1F.2.3a made deletion permanent.
            priority: The new level. Already a
                :class:`~meobot.domain.pr.models.PrPriority`, so an arbitrary
                string was refused by the caller's parser before reaching here.

        Returns:
            The updated row, flushed but **not committed** - the caller owns the
            transaction, as everywhere else in this service.

        Raises:
            PrNotFoundError: No such content item.
            PrPermissionDeniedError: Missing ``PR_CONTENT_EDIT``, or neither
                management nor responsible for this item.
        """
        await self._capabilities.require(actor, PrCapability.PR_CONTENT_EDIT)
        content = await self._lock_content(content_id)

        if not await self._may_edit_metadata(actor, content):
            raise PrPermissionDeniedError(
                "Bạn chỉ đổi được mức độ ưu tiên của nội dung mình phụ trách.",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "capability": PrCapability.PR_CONTENT_EDIT.value,
                    "reason": "not_responsible",
                },
            )

        before = content.priority
        if before is priority:
            # Nothing changed, so nothing is recorded. An audit trail that logs
            # "URGENT -> URGENT" every time somebody reopens a select teaches
            # whoever reads it to skip priority rows.
            return content

        content.priority = priority
        await self._session.flush()
        # ``updated_at`` carries ``onupdate=func.now()``, so the flush above
        # expires it: its new value is whatever the database computed, and
        # SQLAlchemy will not guess. Reloading it **here**, inside an awaited
        # call, is what stops the first plain attribute read from attempting IO
        # on its own - which in async code is a ``MissingGreenlet`` rather than
        # a lazy load, and would strike in the route that serialises this row
        # rather than anywhere near the write.
        await self._session.refresh(content)

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_PRIORITY_CHANGED,
            entity_type="pr_content_item",
            entity_id=content.id,
            before={"priority": before.value},
            after={
                "content_code": content.code,
                "priority": content.priority.value,
            },
        )
        logger.info(
            "pr_content_priority_changed",
            extra={
                "pr_content_id": str(content.id),
                "from": before.value,
                "to": content.priority.value,
            },
        )
        return content

    async def set_content_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        content_type: PrContentType,
    ) -> PrContentItem:
        """Classify or reclassify one content item. **No new version.**

        Step 1F.2.3e, and the same shape as :meth:`set_content_priority` for the
        same reason: the format a piece is written in is a fact *about* the work,
        not a draft *of* it. Recording "v4" because somebody corrected a
        mislabelled Facebook post would put a row in the review history that no
        reviewer wrote.

        No stage condition either. The two things this is for are classifying a
        historical item that predates the column, and fixing a wrong choice - and
        both are most likely to happen long after the piece left the editable
        stages.

        Who may: :meth:`may_edit_metadata` - management, or the person
        responsible for the item, on top of ``PR_CONTENT_EDIT``.

        Args:
            actor: Gated as above.
            request_id: Correlation id for the audit row.
            content_id: The item to classify. Permanently deleted content has no
                row, so a deleted id is a :class:`PrNotFoundError` here.
            content_type: The new format. Already a
                :class:`~meobot.domain.pr.models.PrContentType`, so an arbitrary
                string was refused by the caller's parser before reaching here.

                There is deliberately no way to set it *back* to ``None``:
                "unclassified" is where a row starts, not somewhere to return it
                to, and a control that could blank the field would make the
                required-on-create rule pointless.

        Returns:
            The updated row, flushed but **not committed** - the caller owns the
            transaction.

        Raises:
            PrNotFoundError: No such content item.
            PrPermissionDeniedError: Missing ``PR_CONTENT_EDIT``, or neither
                management nor responsible for this item.
        """
        await self._capabilities.require(actor, PrCapability.PR_CONTENT_EDIT)
        content = await self._lock_content(content_id)

        if not await self._may_edit_metadata(actor, content):
            raise PrPermissionDeniedError(
                "Bạn chỉ đổi được loại nội dung của nội dung mình phụ trách.",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "capability": PrCapability.PR_CONTENT_EDIT.value,
                    "reason": "not_responsible",
                },
            )

        before = content.content_type
        if before is content_type:
            # Nothing changed, so nothing is recorded - the rule
            # :meth:`set_content_priority` follows, for the same reason.
            return content

        content.content_type = content_type
        await self._session.flush()
        # ``updated_at`` carries ``onupdate=func.now()``, so the flush expires it.
        # Reloading inside an awaited call is what stops the first plain attribute
        # read - in the route's serialiser - from attempting IO on its own.
        await self._session.refresh(content)

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_TYPE_CHANGED,
            entity_type="pr_content_item",
            entity_id=content.id,
            # ``None`` is a real previous value here - the historical case - and
            # is recorded as such rather than omitted.
            before={"content_type": before.value if before else None},
            after={
                "content_code": content.code,
                "content_type": content_type.value,
            },
        )
        logger.info(
            "pr_content_type_changed",
            extra={
                "pr_content_id": str(content.id),
                "from": before.value if before else None,
                "to": content_type.value,
            },
        )
        return content

    async def may_edit_metadata(self, actor: Actor, content: PrContentItem) -> bool:
        """May this actor change this item's **operational metadata**?

        One predicate for the whole class of change, and the class has grown:
        priority (Step 1F.2.3d), content type and review resources (Step
        1F.2.3e). All three are *about* the work rather than *in* it - none
        writes a version, none moves a stage, and none is read by a workflow
        rule - so they share one rule rather than three that would drift.

        Named for what it decides rather than for its first caller: it began as
        ``may_set_priority``, and a second and third use made that name a lie
        about its scope.

        The write's **own** predicate, exported so
        :class:`~meobot.application.pr_action_service.PrActionService` can offer
        these controls without keeping a second copy of the rule. A panel that
        rendered a control the ``PATCH`` would refuse with a 403 is how somebody
        learns the screen is guessing.

        Read-only and never raises: an actor without ``PR_CONTENT_EDIT`` is
        ``False`` here and a refusal there, rather than an exception a caller
        building a list of offers would have to catch.
        """
        if not await self._capabilities.allows(actor, PrCapability.PR_CONTENT_EDIT):
            return False
        return await self._may_edit_metadata(actor, content)

    async def _may_edit_metadata(self, actor: Actor, content: PrContentItem) -> bool:
        """Management, or responsible for this row. Assumes ``PR_CONTENT_EDIT``.

        The responsibility query is skipped for a manager, because the answer
        cannot change theirs - the same short-circuit
        :meth:`~meobot.application.pr_lifecycle_service.PrLifecycleService._require_deletable`
        makes, for the same reason.

        Note what holding a **review** capability does not do: a Head who is not
        responsible for a piece and does not manage content may read everything
        about it and change none of it. Being able to approve something is not
        being able to edit it, and Step 1F.2.3e's resources follow that line
        exactly - reviewers see them, and do not rewrite them.
        """
        if await self._capabilities.allows(actor, PrCapability.PR_CONTENT_CANCEL):
            return True
        return await self._responsible(actor, content)

    async def _responsible(self, actor: Actor, content: PrContentItem) -> bool:
        """Is this actor the owner, or on an unfinished task of this item?

        The same one-row question
        :meth:`~meobot.application.pr_lifecycle_service.PrLifecycleService._responsible`
        asks, against the same shared predicate, so the two answers cannot
        disagree about who a piece belongs to.
        """
        if actor.user_id is None:
            return False
        found = await self._session.execute(
            select(PrContentItem.id).where(
                PrContentItem.id == content.id, responsible_for(actor.user_id)
            )
        )
        return found.scalars().first() is not None

    # --- Reading ----------------------------------------------------------
    async def require_viewable_content(self, actor: Actor, content_id: uuid.UUID) -> PrContentItem:
        """The content item, if this actor may **see** it. Step 1F.2.3g.

        The authoritative answer to *"can this actor view this content"*, and
        deliberately not a new rule: it is
        ``require_permission(actor, PR_READ_PERMISSION)`` - the same call
        :class:`~meobot.application.pr_query_service.PrQueryService` makes
        before every list, every detail read and every child collection -
        followed by the item having to exist.

        There is no per-row visibility in this module and this does not invent
        one. Somebody who may read PR content may read *this* content, which is
        why the two things Step 1F.2.3g opened to any viewer - contributing a
        derivative, and commenting - can be authorised by the read itself.

        Written once and called from both, so a future narrowing of "who may
        view" narrows contribution with it rather than leaving a second copy of
        the rule behind.

        Raises:
            PrPermissionDeniedError: The actor's role lacks
                :data:`~meobot.domain.pr.policy.PR_READ_PERMISSION`.
            PrNotFoundError: No such content item.
        """
        require_permission(actor, PR_READ_PERMISSION)
        return await self.require_content(content_id)

    @staticmethod
    def may_view(actor: Actor) -> bool:
        """The boolean form of :meth:`require_viewable_content`'s first half.

        For the read models that build a list of offers and must not raise -
        see :class:`~meobot.application.pr_action_service.PrAvailableActionService`.
        The item's existence is not in question there: the caller is holding it.
        """
        return may_read(actor)

    async def require_content(self, content_id: uuid.UUID) -> PrContentItem:
        """Load a content item or raise :class:`PrNotFoundError`."""
        content = await self._session.get(PrContentItem, content_id)
        if content is None:
            raise PrNotFoundError(
                "No PR content item with that id", details={"content_id": str(content_id)}
            )
        return content

    async def current_version(self, content_id: uuid.UUID) -> PrContentVersion | None:
        """The newest immutable draft, or ``None`` before version 1 exists.

        Ordered by ``version_no`` rather than ``created_at``: the version
        number is the thing reviews point at, and two rows written in the same
        millisecond would otherwise order arbitrarily.
        """
        result = await self._session.execute(
            select(PrContentVersion)
            .where(PrContentVersion.content_id == content_id)
            .order_by(PrContentVersion.version_no.desc())
            .limit(1)
        )
        return result.scalars().one_or_none()

    async def require_current_version(self, content_id: uuid.UUID) -> PrContentVersion:
        """The newest draft, refusing content that has none."""
        version = await self.current_version(content_id)
        if version is None:
            raise PrNotFoundError(
                "PR content has no versions", details={"content_id": str(content_id)}
            )
        return version

    async def list_versions(
        self, content_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[PrContentVersion]:
        """Every draft of one item, newest first."""
        result = await self._session.execute(
            select(PrContentVersion)
            .where(PrContentVersion.content_id == content_id)
            .order_by(PrContentVersion.version_no.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def snapshot(self, content_id: uuid.UUID) -> ContentSnapshot:
        """The item and its current draft together."""
        content = await self.require_content(content_id)
        return ContentSnapshot(
            content=content, version=await self.require_current_version(content_id)
        )

    # --- Internals --------------------------------------------------------
    async def _append_version(
        self,
        *,
        content: PrContentItem,
        version_no: int,
        title: str,
        topic: str | None,
        hook: str | None,
        brief: str | None,
        script_text: str | None,
        change_note: str | None,
        author_user_id: uuid.UUID,
    ) -> PrContentVersion:
        version = PrContentVersion(
            content_id=content.id,
            version_no=version_no,
            title=title,
            topic=topic,
            hook=hook,
            brief=brief,
            script_text=script_text,
            change_note=change_note,
            created_by_user_id=author_user_id,
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def _lock_content(self, content_id: uuid.UUID) -> PrContentItem:
        content = await lock_row(self._session, PrContentItem, content_id)
        if content is None:
            raise PrNotFoundError(
                "No PR content item with that id", details={"content_id": str(content_id)}
            )
        return content

    async def _record_version_event(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        content: PrContentItem,
        version: PrContentVersion,
    ) -> None:
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_VERSION_CREATED,
            entity_type="pr_content_version",
            entity_id=version.id,
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "version_no": version.version_no,
                "created_by_user_id": str(version.created_by_user_id),
                "has_script_text": version.script_text is not None,
            },
        )

    @staticmethod
    def _validated_resource(spec: ContentResourceSpec, index: int) -> ContentResourceSpec:
        """One initial resource, checked by the resource validator itself.

        Step 1F.2.3e.1. **No second set of rules for the create path**: the same
        :func:`~meobot.application.pr_content_resource_support.validated_resource_spec`
        that guards ``add_resource`` guards this, so ``javascript:`` is refused
        in the create form for the same reason and with the same
        ``details['reason']`` it is refused on the detail page.

        The only thing added is *which* of them failed, so a form holding five
        drafts can put the sentence under the right one.
        """
        with resource_refusal_index(index):
            return validated_resource_spec(spec)

    async def _require_target_channel(self, spec: ContentTargetSpec) -> None:
        """Validate one planned channel, and the mode its platform demands.

        The platform is read through ``channel.platform_id``, never taken from
        the caller and never guessed from a channel's name: a channel called
        "FB Apexmed" on a YouTube platform row is a YouTube target, and reading
        the name would decide which official rulebook somebody's advertising is
        judged against.
        """
        channel = await self._session.get(PrChannel, spec.channel_id)
        if channel is None:
            raise PrNotFoundError(
                "Không tìm thấy kênh này.", details={"channel_id": str(spec.channel_id)}
            )
        if channel.status is not PrChannelStatus.ACTIVE:
            raise PrValidationError(
                "Kênh này đã ngừng hoạt động, không chọn được.",
                details={"reason": "channel_inactive", "channel_id": str(channel.id)},
            )

        platform = await self._session.get(PrPlatform, channel.platform_id)
        code = platform.code if platform else ""
        if code in POLICY_GROUNDED_PLATFORM_CODES and (
            spec.distribution_mode is PrDistributionMode.UNSPECIFIED
        ):
            # Not a default and not a guess: the two modes are judged against
            # different official policy, so somebody has to say which.
            raise PrValidationError(
                "Kênh Facebook/TikTok cần chọn Organic hoặc Quảng cáo trả phí.",
                details={
                    "reason": "distribution_mode_required",
                    "channel_id": str(channel.id),
                    "platform_code": code,
                },
            )

    async def _require_exists[ModelT: Base](
        self, model: type[ModelT], entity_id: uuid.UUID, label: str
    ) -> None:
        if await self._session.get(model, entity_id) is None:
            raise PrNotFoundError(
                f"No {label} with that id", details={f"{label}_id": str(entity_id)}
            )

    @staticmethod
    def _author(actor: Actor, fallback_user_id: uuid.UUID) -> uuid.UUID:
        """Whose ``users.id`` goes on the row.

        The acting person, when there is one. The bootstrap owner has no
        ``users`` row until first sync - see
        :attr:`~meobot.domain.identity.models.Actor.is_bootstrap_owner` - and
        the columns here are ``NOT NULL`` foreign keys, so that one case falls
        back to the content owner. It is recorded rather than hidden: the audit
        event carries the actor, so "the owner created it" and "the bootstrap
        owner created it on the owner's behalf" stay distinguishable.
        """
        return actor.user_id if actor.user_id is not None else fallback_user_id

    @staticmethod
    def _carry(supplied: str | None, current: str | None) -> str | None:
        """New value when one was given, otherwise the previous draft's."""
        return current if supplied is None else supplied

    @staticmethod
    def _require_text(value: str, field_name: str, max_length: int) -> str:
        text = (value or "").strip()
        if not text:
            raise PrValidationError(
                f"PR content {field_name} must not be blank", details={"field": field_name}
            )
        if len(text) > max_length:
            raise PrValidationError(
                f"PR content {field_name} is longer than {max_length} characters",
                details={"field": field_name, "max_length": max_length, "length": len(text)},
            )
        return text


__all__: list[str] = [
    "ContentSnapshot",
    "ContentTargetSpec",
    "CreateContentCommand",
    "PrContentService",
    "ReviseContentCommand",
]
