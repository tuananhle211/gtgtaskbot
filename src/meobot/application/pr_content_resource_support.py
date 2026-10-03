"""One way a review resource comes into existence, wherever it is attached.

Step 1F.2.3e.1. A resource can now arrive by two routes - attached to a content
item that already exists (Step 1F.2.3e's
:class:`~meobot.application.pr_content_resource_service.PrContentResourceService`)
or supplied as part of the item's own creation
(:meth:`~meobot.application.pr_content_service.PrContentService.create_content`)
- and this module exists so that is **two callers of one construction path**
rather than two paths that happen to agree today.

What would go wrong without it
------------------------------

The three things a resource write has to get right are the validation
(:mod:`meobot.domain.pr.resources`), the row it builds, and the audit payload
that describes it. A second copy of any of them drifts in a way nobody notices:
a create form that accepted ``javascript:`` because it validated the label and
forgot the location; an audit row for an initial resource shaped differently
from the one written half an hour later when somebody adds another, so a reader
of the trail cannot tell they are the same kind of event.

So the spec, the builder and the audit payload live here, and neither service
constructs a :class:`~meobot.db.models.pr_content_resource.PrContentResource`
by hand.

Why here and not in the domain
------------------------------

:mod:`meobot.domain.pr.resources` holds the *decisions* - what a location may
be, how long a label may run - and imports nothing from :mod:`meobot.db`, which
is the boundary the domain package keeps. Building an ORM row is not a decision,
so it lives one layer out.

Why a module rather than a shared base class
--------------------------------------------

:class:`~meobot.application.pr_content_resource_service.PrContentResourceService`
already depends on
:class:`~meobot.application.pr_content_service.PrContentService` - it authorises
against ``may_edit_metadata``, deliberately, so "who may attach a brief" and
"who may retriage" have one answer. Putting these helpers on either service
would make that dependency circular. Free functions have no such problem and
need no instance to call.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from meobot.application.audit_service import AuditService
from meobot.application.pr_support import record_pr_event
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.models import PrContentResourceType
from meobot.domain.pr.resources import (
    normalize_label,
    normalize_note,
    normalize_resource_location,
)


@dataclass(frozen=True, slots=True)
class ContentResourceSpec:
    """One reference, as asked for. Not yet validated and not yet a row.

    Deliberately has **no ``content_id``**: it is the part of a resource that is
    the same whether the content item already exists or is being created in the
    same transaction. Which item it belongs to is the caller's to say, and for
    an initial resource there is no id to name until the content row has been
    flushed.
    """

    resource_type: PrContentResourceType
    label: str
    location: str
    note: str | None = None
    required_for_review: bool = False


def validated_resource_spec(spec: ContentResourceSpec) -> ContentResourceSpec:
    """The same spec with every field checked and normalised.

    Separated from :func:`build_content_resource` for one reason:
    ``create_content`` validates every initial resource **before** it allocates
    a content code or writes a row, so a bad paste in the third of five costs
    nothing and burns no ``CNT-…`` number - the order Step 1F.2.3e's
    ``add_resource`` already uses within a single write.

    Idempotent, so calling it and then building from the result is safe:
    stripping stripped text changes nothing, and a location that passed the
    validator passes it again.

    Raises:
        PrValidationError: The label is blank or too long, the note is too long,
            or the location is empty, too long, the wrong shape, or carries a
            scheme this product refuses. ``details['reason']`` names which.
    """
    return ContentResourceSpec(
        resource_type=spec.resource_type,
        label=normalize_label(spec.label),
        location=normalize_resource_location(spec.resource_type, spec.location),
        note=normalize_note(spec.note),
        required_for_review=spec.required_for_review,
    )


def build_content_resource(
    spec: ContentResourceSpec, *, content_id: uuid.UUID, author_user_id: uuid.UUID
) -> PrContentResource:
    """A validated row, ready to add to a session. Nothing is written here.

    The caller owns the session and the transaction, as everywhere else in this
    package.
    """
    validated = validated_resource_spec(spec)
    return PrContentResource(
        content_id=content_id,
        resource_type=validated.resource_type,
        label=validated.label,
        location=validated.location,
        note=validated.note,
        required_for_review=validated.required_for_review,
        added_by_user_id=author_user_id,
    )


@contextmanager
def resource_refusal_index(index: int) -> Iterator[None]:
    """Tag a refusal with *which* of several resources it was about.

    Step 1F.2.3e.1. A create form can carry five drafts, and "Liên kết chưa có
    tên miền" against the whole form makes somebody re-read all five. The
    ``details`` mapping keeps every key it had - the ``reason`` vocabulary is
    shared with the production validator and clients already render it - and
    gains ``initial_resource_index``, which is what lets the browser put the
    sentence under the draft that caused it.

    A no-op for anything that is not a validation refusal.
    """
    try:
        yield
    except PrValidationError as error:
        raise PrValidationError(
            error.message,
            details={**error.details, "initial_resource_index": index},
        ) from error


def resource_snapshot(resource: PrContentResource) -> dict[str, object]:
    """The auditable fields of a resource.

    The **note is deliberately absent**, and so is anything the location points
    at. A note can be a paragraph of review instructions, and an audit trail is
    not the place to accumulate copies of prose - see
    ``docs/pr/STEP_1F23E_CONTENT_TYPES_AND_RESOURCES.md``. That the note changed
    is visible from the ``updated_at`` on the row.
    """
    return {
        "resource_type": resource.resource_type.value,
        "label": resource.label,
        "location": resource.location[:200],
        "required_for_review": resource.required_for_review,
    }


async def record_resource_event(
    audit: AuditService,
    *,
    request_id: uuid.UUID,
    actor: Actor,
    action: AuditAction,
    content: PrContentItem,
    resource: PrContentResource,
    before: dict[str, object] | None = None,
) -> None:
    """Append one resource event, in the caller's transaction.

    The same payload shape whether the resource was attached to an existing item
    or supplied at creation - Step 1F.2.3e.1 deliberately writes
    ``pr.content.resource_added`` for an initial resource rather than inventing
    a second action for it. A reader of the trail is asking "what material was
    attached to this piece, and by whom", and that question should not have two
    answers depending on how early somebody thought of the brief.
    """
    await record_pr_event(
        audit,
        request_id=request_id,
        actor=actor,
        action=action,
        entity_type="pr_content_resource",
        entity_id=resource.id,
        before=before,
        after={
            "content_id": str(content.id),
            "content_code": content.code,
            **resource_snapshot(resource),
        },
    )


__all__: list[str] = [
    "ContentResourceSpec",
    "build_content_resource",
    "record_resource_event",
    "resource_refusal_index",
    "resource_snapshot",
    "validated_resource_spec",
]
