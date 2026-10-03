"""Attaching, correcting and removing the material a reviewer needs.

Step 1F.2.3e. The write side of
:class:`~meobot.db.models.pr_content_resource.PrContentResource`, and nothing
else - reading them is
:meth:`~meobot.application.pr_query_service.PrQueryService.content_resources`,
because reading is gated on the read permission and writing is not.

The rule this service is built around
--------------------------------------

**Viewing is broad; editing is narrow.** Anybody who may read a content item may
read its resources - they are review context, and a reviewer who cannot see the
brief cannot do the job the resources exist to support. Changing them needs
:meth:`~meobot.application.pr_content_service.PrContentService.may_edit_metadata`:
management, or the person responsible for the item.

The case that decides the shape is a Head reviewer who is not responsible for the
piece. They must see every resource, and they must not be able to rewrite the
brief they are reviewing against. Holding a review capability is not holding an
edit capability, and this service is where that stays true.

What it deliberately does not do
---------------------------------

* **No notifications.** Attaching a moodboard is not news; twenty of them during
  a briefing session is noise. Every mutation is audited instead - Step 1F.2.3e,
  matching the priority decision in 1F.2.3d;
* **no workflow effect.** Adding, editing or deleting a resource moves no stage,
  satisfies no gate and blocks no approval. ``required_for_review`` is a
  reviewer-attention signal and is read by nothing except the ordering and a
  badge - see the model;
* **no fetching.** A location is validated for shape by
  :mod:`meobot.domain.pr.resources` and then stored. Nothing here opens a URL,
  checks a link is alive, makes a thumbnail or hands anything to a model.

Not the only way a resource is born
------------------------------------

Step 1F.2.3e.1 lets resources be supplied while the content item is being
created, which is a different **authorization** question - there is no content
row yet to be responsible for, so the create permission is the whole check - and
must not be a different **construction**. The row builder, the validator and the
audit payload live in
:mod:`meobot.application.pr_content_resource_support` and are called from both
places; what remains here is the after-the-fact half: who may change material
attached to an item that already exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_resource_support import (
    ContentResourceSpec,
    build_content_resource,
    record_resource_event,
    resource_snapshot,
)
from meobot.application.pr_content_service import PrContentService
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
)
from meobot.domain.pr.models import PrContentResourceType
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.resources import (
    normalize_label,
    normalize_note,
    normalize_resource_location,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AddContentResourceCommand:
    """One reference to attach to a content item that already exists.

    The fields other than ``content_id`` are exactly a
    :class:`~meobot.application.pr_content_resource_support.ContentResourceSpec`,
    and :attr:`spec` is how this hands them to the shared construction path -
    Step 1F.2.3e.1, so attaching a brief now and supplying one at creation build
    the same row from the same validator.
    """

    content_id: uuid.UUID
    resource_type: PrContentResourceType
    label: str
    location: str
    note: str | None = None
    required_for_review: bool = False

    @property
    def spec(self) -> ContentResourceSpec:
        """This command's resource, without the item it is being attached to."""
        return ContentResourceSpec(
            resource_type=self.resource_type,
            label=self.label,
            location=self.location,
            note=self.note,
            required_for_review=self.required_for_review,
        )


@dataclass(frozen=True, slots=True)
class UpdateContentResourceCommand:
    """Fields a resource may have changed. ``None`` means "leave alone".

    **There is no ``content_id``.** A resource belongs to the item it was
    attached to, and moving one between items is not an edit - it is a delete and
    an add, with different audit rows and a different story. Leaving the field out
    of the command is what makes that unrepresentable rather than merely refused.
    """

    resource_id: uuid.UUID
    resource_type: PrContentResourceType | None = None
    label: str | None = None
    location: str | None = None
    #: Explicitly ``None`` cannot clear the note - it means "unchanged", like
    #: every other field here. Clearing is sending an empty string, which
    #: :func:`~meobot.domain.pr.resources.normalize_note` turns back into
    #: ``None``.
    note: str | None = None
    required_for_review: bool | None = None


class PrContentResourceService:
    """Adds, edits and removes the references hanging off a content item.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
        content: Owns the metadata-edit predicate this service authorises
            against, so "who may attach a brief" and "who may retriage" have one
            answer rather than two that drift.
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

    # --- Writing ----------------------------------------------------------
    async def add_resource(
        self, *, actor: Actor, request_id: uuid.UUID, command: AddContentResourceCommand
    ) -> PrContentResource:
        """Attach one reference to a content item.

        Validated before anything is written, so a bad paste costs nothing and
        leaves no half-attached row behind - the order
        :meth:`~meobot.application.pr_production_service.PrProductionService.submit_production`
        uses, for the same reason.

        Raises:
            PrNotFoundError: No such content item.
            PrPermissionDeniedError: May not edit this item's metadata.
            PrValidationError: The label is blank, or the location is empty, too
                long, or carries a scheme this product refuses.
                ``details['reason']`` names which.
        """
        content = await self._authorized_content(actor, command.content_id)

        # Step 1F.2.3e.1: the shared builder, which is also what a create-time
        # initial resource goes through. Two copies of "what a resource row is"
        # is how one of them stops validating the location.
        resource = build_content_resource(
            command.spec, content_id=content.id, author_user_id=self._author(actor)
        )
        self._session.add(resource)
        await self._session.flush()

        await self._record(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_RESOURCE_ADDED,
            content=content,
            resource=resource,
        )
        logger.info(
            "pr_content_resource_added",
            extra={
                "pr_content_id": str(content.id),
                "resource_type": resource.resource_type.value,
                "required_for_review": resource.required_for_review,
            },
        )
        return resource

    async def update_resource(
        self, *, actor: Actor, request_id: uuid.UUID, command: UpdateContentResourceCommand
    ) -> PrContentResource:
        """Correct a reference in place.

        A resource is a pointer to material that lives elsewhere, so fixing a
        label or a link is a correction rather than a new fact - there is no
        version row, and the previous values live in the audit trail. See the
        model for why this table is mutable while the ones beside it are not.

        The location is re-validated against the resource's type **after** any
        type change in the same command, so switching a row to ``DRIVE_FILE``
        without also changing the location is refused rather than stored.

        Raises:
            PrNotFoundError: No such resource.
            PrPermissionDeniedError: May not edit the owning item's metadata.
            PrValidationError: A new label or location is unacceptable.
        """
        resource = await self._require_resource(command.resource_id)
        content = await self._authorized_content(actor, resource.content_id)

        before = resource_snapshot(resource)

        if command.resource_type is not None:
            resource.resource_type = command.resource_type
        if command.label is not None:
            resource.label = normalize_label(command.label)
        if command.location is not None:
            resource.location = normalize_resource_location(
                resource.resource_type, command.location
            )
        elif command.resource_type is not None:
            # The type changed and the location did not. ``DRIVE_FILE`` now
            # promises a Drive host the stored location may not have, so the
            # existing value is re-checked against the new type rather than left
            # to contradict it.
            resource.location = normalize_resource_location(
                resource.resource_type, resource.location
            )
        if command.note is not None:
            resource.note = normalize_note(command.note)
        if command.required_for_review is not None:
            resource.required_for_review = command.required_for_review

        await self._session.flush()
        await self._session.refresh(resource)

        after = resource_snapshot(resource)
        if before == after:
            # Nothing actually changed - a form submitted unedited. No audit row,
            # for the reason a no-op priority change writes none.
            return resource

        await self._record(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_RESOURCE_UPDATED,
            content=content,
            resource=resource,
            # Only the fields that moved, so a reader sees the change rather than
            # a copy of the row.
            before={key: value for key, value in before.items() if after[key] != value},
        )
        logger.info(
            "pr_content_resource_updated",
            extra={"pr_content_id": str(content.id), "pr_resource_id": str(resource.id)},
        )
        return resource

    async def delete_resource(
        self, *, actor: Actor, request_id: uuid.UUID, resource_id: uuid.UUID
    ) -> None:
        """Remove one reference. Nothing else changes.

        Not a soft delete and not a lifecycle event: the row is a pointer, the
        material it points at is untouched, and the workflow stage does not move.
        The audit row is written **before** the delete so it names a resource that
        still exists at the moment it is described.

        Raises:
            PrNotFoundError: No such resource.
            PrPermissionDeniedError: May not edit the owning item's metadata.
        """
        resource = await self._require_resource(resource_id)
        content = await self._authorized_content(actor, resource.content_id)

        await self._record(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_RESOURCE_DELETED,
            content=content,
            resource=resource,
        )
        await self._session.execute(
            delete(PrContentResource).where(PrContentResource.id == resource_id)
        )
        await self._session.flush()
        logger.info(
            "pr_content_resource_deleted",
            extra={"pr_content_id": str(content.id), "pr_resource_id": str(resource_id)},
        )

    # --- Internals --------------------------------------------------------
    async def _authorized_content(self, actor: Actor, content_id: uuid.UUID) -> PrContentItem:
        """The content item, if this actor may edit its metadata.

        The capability is required first so somebody with no PR write rights at
        all gets the plain refusal, and the responsibility check second so the
        message names the real reason.
        """
        await self._capabilities.require(actor, PrCapability.PR_CONTENT_EDIT)
        content = await self._content.require_content(content_id)
        if not await self._content.may_edit_metadata(actor, content):
            raise PrPermissionDeniedError(
                "Bạn chỉ sửa được tài nguyên của nội dung mình phụ trách.",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "capability": PrCapability.PR_CONTENT_EDIT.value,
                    "reason": "not_responsible",
                },
            )
        return content

    async def _require_resource(self, resource_id: uuid.UUID) -> PrContentResource:
        found = await self._session.execute(
            select(PrContentResource).where(PrContentResource.id == resource_id)
        )
        resource = found.scalars().first()
        if resource is None:
            raise PrNotFoundError(
                "No PR content resource with that id",
                details={"resource_id": str(resource_id)},
            )
        return resource

    async def _record(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        action: AuditAction,
        content: PrContentItem,
        resource: PrContentResource,
        before: dict[str, object] | None = None,
    ) -> None:
        """One line, so this service and ``create_content`` write the same event.

        Step 1F.2.3e.1 moved the payload into
        :func:`~meobot.application.pr_content_resource_support.record_resource_event`;
        what stays here is only the binding to this service's own audit writer.
        """
        await record_resource_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=action,
            content=content,
            resource=resource,
            before=before,
        )

    @staticmethod
    def _author(actor: Actor) -> uuid.UUID:
        """Who attached it. A resource with no author has nobody to ask about it."""
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "Tài khoản này chưa được liên kết với một người dùng, không thể thực hiện.",
                details={"reason": "no_user_record"},
            )
        return actor.user_id


__all__: list[str] = [
    "AddContentResourceCommand",
    "PrContentResourceService",
    "UpdateContentResourceCommand",
]
