"""Building one turn's grounded context: static domain, live actor, real records.

Step 1F.2.3h. This is the service the whole step exists to add, and its shape is
its argument:

    canonical domain context   (static, no query, same for everybody)
  + this actor's evaluated rights
  + the one content item this turn is about, if any and if allowed
  + only the child collections this question needs, bounded
  = a context a model can answer from without guessing.

The order that matters
----------------------

**Authorize, then fetch, then build.** Never fetch-then-filter, and never
fetch-then-instruct. A private row that reaches the prompt with *"do not reveal
this"* attached has already left the building - the model is not a security
boundary, and a prompt is not an ACL. So every read here goes through the same
service the panel's own routes go through, with the same actor, and an
unauthorized item produces **no object context at all** rather than a redacted
one. There is no code path in this module that reads a PR table directly.

The one read rule
-----------------

The content is loaded with
:meth:`~meobot.application.pr_query_service.PrQueryService.get_content` - the
method the panel's own detail route calls - so the gate is
:data:`~meobot.domain.pr.policy.PR_READ_PERMISSION`, which is exactly what Step
1F.2.3g's
:meth:`~meobot.application.pr_content_service.PrContentService.require_viewable_content`
wraps. Same permission, same refusal, one rule.

``get_content`` rather than the bare predicate because it also returns the
brand, the channels and the current draft in bounded queries, which the context
needs anyway - and because a second entry point into "may this actor see this
content" is precisely the thing that eventually disagrees with the first. A test
removes the permission and asserts the read and the context build refuse
together.

What the panel may show you, the assistant may ground on; nothing more.

Why the row flags are computed here rather than described
----------------------------------------------------------

``can_edit``, ``can_delete``, ``can_reverse``, ``can_correct`` are asked of the
same predicates the ``PATCH`` and ``DELETE`` routes ask - per row, before the
prompt exists. The alternative was to hand the model a capability list and an
owner id and let it work them out, which fails in the one direction that
matters: it produces a confident *"bạn bấm Sửa được"* for a row the server will
refuse. See Step 1F.2.3g, which moved these onto the row for the panel for the
same reason.

Cost
----

One turn with a content item costs a fixed number of statements: the item, its
actions, whichever collections were selected, and **one** query resolving every
person named across all of them. Names are the N+1 this module is most exposed
to - a creator per derivative, a publisher per publication, an author per
comment - so they are collected and looked up once at the end. Nothing here is
per row.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_services import PrServices
from meobot.core.logging import get_logger
from meobot.db.models.user import User
from meobot.domain.assistant.domain_context import (
    MEOBOT_DOMAIN_CONTEXT,
    MeoBotDomainContext,
)
from meobot.domain.assistant.intent import ContextSection, sections_for
from meobot.domain.assistant.work_context import (
    ActorAssistantContext,
    AssistantAvailableActions,
    AssistantCollection,
    AssistantRecordContext,
    AssistantSection,
    ContentAssistantContext,
    MeoBotAssistantContext,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrNotFoundError, PrPermissionDeniedError
from meobot.domain.pr.labels import (
    content_type_label,
    derivative_type_label,
    priority_label,
    resource_type_label,
    stage_label,
)
from meobot.domain.pr.reporting import is_active_publication

logger = get_logger(__name__)

#: How many rows of any one collection reach the model. Deliberately small and
#: the same for every collection: the question is almost always about the recent
#: end, and a number a reader can hold in their head is worth more than a number
#: tuned per table. Anything beyond it is reported as truncated, never dropped
#: silently - see :class:`~meobot.domain.assistant.work_context.AssistantCollection`.
COLLECTION_LIMIT = 20

#: The tighter cap for the transition history, which is the one collection that
#: grows without anybody deciding to add to it.
HISTORY_LIMIT = 15

#: What the model is told when a content id was named and could not be used.
#: One sentence for *"no such item"* and *"not yours to see"* alike: telling the
#: difference apart in the prompt would let somebody probe for existence by
#: reading MeoBot's wording back.
OBJECT_UNAVAILABLE = (
    "Không có bản ghi nội dung nào trong bối cảnh lượt này. Nếu người dùng hỏi về "
    "'nội dung này' / 'bản này' / 'link này', hãy nói là mình chưa xác định được bản "
    "ghi họ đang nói tới và hỏi lại mã nội dung. Đừng đoán."
)


@dataclass(frozen=True, slots=True)
class ContextRef:
    """A pointer to the object a turn is about. **An id and a kind, nothing else.**

    This is the whole of what a client may say about the current object. It may
    not send a stage, a permission, a creator or a title: those are resolved
    here from the database, under this actor's authorization, precisely so that
    a browser (or a compromised one) cannot dictate what the assistant believes
    about a record.

    ``kind`` exists so that a later object type - a task, a channel - is a new
    value rather than a new parameter, and so an unknown one is ignored rather
    than mis-resolved.
    """

    kind: str
    id: str

    #: The only kind this step resolves.
    PR_CONTENT = "pr_content"

    @property
    def is_content(self) -> bool:
        return self.kind == self.PR_CONTENT

    def content_id(self) -> uuid.UUID | None:
        """The id as a UUID, or ``None`` if it is not one.

        A malformed id is not an error here. The reference arrives from a client
        or from a conversation pointer, and a stale or mangled one must degrade
        to "no object context" rather than break an otherwise ordinary chat
        turn.
        """
        if not self.is_content:
            return None
        try:
            return uuid.UUID(self.id)
        except (ValueError, AttributeError, TypeError):
            return None


class MeoBotAssistantContextService:
    """Assembles the grounded context for one assistant turn.

    Args:
        services: The PR service bundle, on the caller's session. Every read
            goes through it - this service owns no queries against PR tables of
            its own, which is what keeps "what the assistant may see" and "what
            the panel may show" one answer instead of two.
        session: The same session, used for exactly one thing: resolving the
            display names of the people the selected records name, in a single
            ``IN``. It is passed explicitly rather than taken off ``services``
            so that this one direct query is visible in the constructor.
        domain: The canonical context. Injectable so a test can pin a version;
            defaults to the one instance.
    """

    def __init__(
        self,
        services: PrServices,
        session: AsyncSession,
        *,
        domain: MeoBotDomainContext = MEOBOT_DOMAIN_CONTEXT,
    ) -> None:
        self._services = services
        self._session = session
        self._domain = domain

    async def build(
        self,
        *,
        actor: Actor,
        message: str,
        context_ref: ContextRef | None = None,
    ) -> MeoBotAssistantContext:
        """The context for one turn.

        Never raises for a context reason. A stale pointer, an item that was
        deleted between two messages, or an actor who may not read it all
        produce the same thing: a context with no object and a sentence saying
        so. A chat turn must not fail because an optional page hint went out of
        date - somebody asking an ordinary question is not helped by an error
        raised over a pointer they never saw.

        Args:
            actor: The authenticated principal. Every read is made as them.
            message: What they typed, used only to select which collections are
                worth loading.
            context_ref: Which object this turn is about, if the client or the
                conversation knows. An id and a kind; see :class:`ContextRef`.
        """
        base = MeoBotAssistantContext(
            domain_version=self._domain.version,
            domain_block=self._domain.render(),
            actor=await self._actor_context(actor),
        )

        content_id = context_ref.content_id() if context_ref is not None else None
        if content_id is None:
            # No pointer at all is the ordinary case and says nothing - only a
            # pointer that *failed* is worth explaining to the model.
            return base if context_ref is None else _with_unavailable(base, OBJECT_UNAVAILABLE)

        try:
            detail = await self._services.queries.get_content(actor=actor, content_id=content_id)
        except (PrNotFoundError, PrPermissionDeniedError):
            # Not found and not permitted, deliberately indistinguishable. See
            # ``OBJECT_UNAVAILABLE``.
            logger.info(
                "assistant_object_context_unavailable",
                extra={"context_ref_kind": context_ref.kind if context_ref else None},
            )
            return _with_unavailable(base, OBJECT_UNAVAILABLE)

        content = detail.content
        pending: set[uuid.UUID] = {content.owner_user_id}
        if content.producer_user_id is not None:
            pending.add(content.producer_user_id)

        sections, truncated, more_people = await self._record_sections(
            actor=actor, message=message, content_id=content.id, detail_content=content
        )
        pending.update(more_people)
        names = await self._display_names(pending)

        actions = await self._services.actions.for_content(actor=actor, content=content)
        return MeoBotAssistantContext(
            domain_version=base.domain_version,
            domain_block=base.domain_block,
            actor=base.actor,
            content=ContentAssistantContext(
                content_id=str(content.id),
                code=content.code,
                title=content.title,
                stage=content.workflow_stage.value,
                stage_label=stage_label(content.workflow_stage),
                content_type_label=content_type_label(content.content_type),
                priority_label=priority_label(content.priority),
                responsible=names.get(content.owner_user_id),
                producer=(
                    names.get(content.producer_user_id)
                    if content.producer_user_id is not None
                    else None
                ),
                brand=detail.brand.name if detail.brand else None,
                channels=tuple(channel.name for channel in detail.target_channels),
                current_version_no=(
                    detail.current_version.version_no if detail.current_version else None
                ),
            ),
            actions=AssistantAvailableActions(
                kinds=tuple(sorted({action.kind.value for action in actions}))
            ),
            records=AssistantRecordContext(
                sections=tuple(
                    AssistantSection(name, _resolve_people(payload, names))
                    for name, payload in sections
                ),
                truncated=truncated,
            ),
        )

    # --- The actor --------------------------------------------------------
    async def _actor_context(self, actor: Actor) -> ActorAssistantContext:
        """Who is asking, with **evaluated** rights rather than a role to reason from.

        The capability set is the live one - role baseline *and* active grants,
        as of today - because that is the only version of "what may this person
        do" that agrees with what the writes will accept.
        """
        capabilities = await self._services.capabilities.capabilities_for_actor(actor)
        return ActorAssistantContext(
            user_id=str(actor.user_id) if actor.user_id else None,
            display_name=actor.full_name,
            role_label=role_label(actor.role),
            capabilities=tuple(sorted(capability.value for capability in capabilities)),
        )

    # --- The records ------------------------------------------------------
    async def _record_sections(
        self,
        *,
        actor: Actor,
        message: str,
        content_id: uuid.UUID,
        detail_content: object,
    ) -> tuple[list[tuple[str, dict[str, Any]]], tuple[str, ...], set[uuid.UUID]]:
        """Load exactly the collections this question selected, and nothing else.

        Returns the rendered payloads, the names of the ones that were cut
        short, and every ``users.id`` mentioned across them - so the caller can
        resolve all of them in one query rather than one per row.
        """
        wanted = sections_for(message, has_object=True)
        payloads: list[tuple[str, dict[str, Any]]] = []
        truncated: list[str] = []
        people: set[uuid.UUID] = set()

        for section in wanted:
            collection, mentioned = await self._load(
                section, actor=actor, content_id=content_id, content=detail_content
            )
            payloads.append((section.value, collection.as_payload()))
            people.update(mentioned)
            if collection.truncated:
                truncated.append(section.value)
        return payloads, tuple(truncated), people

    async def _load(
        self,
        section: ContextSection,
        *,
        actor: Actor,
        content_id: uuid.UUID,
        content: object,
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """One collection, read through the service that owns it.

        The ``match`` is exhaustive over :class:`ContextSection` and there is no
        fallback arm, on purpose: adding a section without teaching this method
        to load it should be a type error here rather than a silently missing
        block in a prompt.
        """
        match section:
            case ContextSection.DERIVATIVES:
                return await self._derivatives(actor, content_id)
            case ContextSection.PUBLICATIONS:
                return await self._publications(actor, content_id)
            case ContextSection.PRODUCTION_SUBMISSIONS:
                return await self._submissions(actor, content_id, content)
            case ContextSection.COMMENTS:
                return await self._comments(actor, content_id)
            case ContextSection.RESOURCES:
                return await self._resources(actor, content_id)
            case ContextSection.DESTINATIONS:
                return await self._destinations(actor, content_id)
            case ContextSection.HISTORY:
                return await self._history(actor, content_id)

    async def _derivatives(
        self, actor: Actor, content_id: uuid.UUID
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """Re-cuts, with the per-row answers Step 1F.2.3g put on them.

        ``describe_derivatives`` already batches the publication check and the
        recorder names, so this costs the same three statements for twenty rows
        as for one.
        """
        views = await self._services.content_assets.describe_derivatives(
            actor=actor, content_id=content_id
        )
        rows = _tail(views, COLLECTION_LIMIT)
        items = tuple(
            {
                "id": str(view.derivative.id),
                "label": view.derivative.label,
                "derivative_type": derivative_type_label(view.derivative.derivative_type),
                "location": view.derivative.location,
                "location_is": "đường dẫn file sản phẩm, không phải link bài đăng",
                "source_submission_id": (
                    str(view.derivative.source_submission_id)
                    if view.derivative.source_submission_id
                    else None
                ),
                "note": view.derivative.note,
                "created_by": view.created_by_name,
                "created_at": view.derivative.created_at,
                "is_published_output": view.is_published_output,
                "can_edit": view.can_edit,
                "can_delete": view.can_delete,
            }
            for view in rows
        )
        return AssistantCollection(items=items, total=len(views)), set()

    async def _publications(
        self, actor: Actor, content_id: uuid.UUID
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """Postings, with the same two flags the panel's rows carry.

        ``may_reverse_publication`` is asked once for the actor and combined per
        row with the row's own status, which is exactly what the list route
        does - so the assistant and the panel offer the same thing.
        """
        rows = await self._services.publications.list_publications(content_id)
        # The read gate for this collection is the content read, already passed.
        recent = _tail(rows, COLLECTION_LIMIT)
        may_reverse = await self._services.publications.may_reverse_publication(actor)
        people: set[uuid.UUID] = set()
        items: list[dict[str, Any]] = []
        for row in recent:
            if row.publisher_user_id is not None:
                people.add(row.publisher_user_id)
            items.append(
                {
                    "id": str(row.id),
                    "code": row.code,
                    "channel_id": str(row.channel_id),
                    "published_output": (
                        {"kind": "san_pham_goc", "id": str(row.production_submission_id)}
                        if row.production_submission_id
                        else (
                            {"kind": "san_pham_phai_sinh", "id": str(row.derivative_id)}
                            if row.derivative_id
                            else {"kind": "khong_ro", "id": None}
                        )
                    ),
                    "link_dang": row.url,
                    "published_at": row.published_at,
                    "note": row.note,
                    "status": row.status.value,
                    "is_active": is_active_publication(row.status),
                    "publisher_user_id": (
                        str(row.publisher_user_id) if row.publisher_user_id else None
                    ),
                    "can_edit": await self._services.publications.may_edit_publication(actor, row),
                    "can_reverse": may_reverse and is_active_publication(row.status),
                }
            )
        return AssistantCollection(items=tuple(items), total=len(rows)), people

    async def _submissions(
        self, actor: Actor, content_id: uuid.UUID, content: object
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """Master cuts, with ``can_correct`` computed the way the route does."""
        rows = await self._services.queries.content_submissions(actor=actor, content_id=content_id)
        published = {
            publication.production_submission_id
            for publication in await self._services.publications.list_publications(content_id)
        }
        recent = _tail(rows, COLLECTION_LIMIT)
        people: set[uuid.UUID] = set()
        items: list[dict[str, Any]] = []
        for row in recent:
            people.update({row.producer_user_id, row.submitted_by_user_id})
            items.append(
                {
                    "id": str(row.id),
                    "submission_no": row.submission_no,
                    "artifact_type": row.artifact_type.value,
                    "location": row.location,
                    "location_is": "đường dẫn file sản phẩm, không phải link bài đăng",
                    "label": row.label,
                    "note": row.note,
                    "producer_user_id": str(row.producer_user_id),
                    "submitted_by_user_id": str(row.submitted_by_user_id),
                    "is_published_output": row.id in published,
                    "can_correct": (
                        row.id not in published
                        and await self._services.production.may_correct_submission(
                            actor,
                            content,  # type: ignore[arg-type]
                            row,
                        )
                    ),
                }
            )
        return AssistantCollection(items=tuple(items), total=len(rows)), people

    async def _comments(
        self, actor: Actor, content_id: uuid.UUID
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """The discussion, tombstones preserved as tombstones.

        A deleted comment arrives here with no body and no name, exactly as it
        reaches the panel - so there is nothing for the assistant to quote back
        from a comment somebody took down.
        """
        page = await self._services.content_comments.list_comments(
            actor=actor, content_id=content_id, limit=COLLECTION_LIMIT
        )
        items = tuple(
            {
                "id": str(thread.id),
                "author": thread.author_name,
                "created_at": thread.created_at,
                "edited": thread.edited_at is not None,
                "is_deleted": thread.is_deleted,
                "body": thread.body if not thread.is_deleted else "[đã xoá]",
                "can_edit": thread.can_edit,
                "can_delete": thread.can_delete,
                "replies": [
                    {
                        "id": str(reply.id),
                        "author": reply.author_name,
                        "created_at": reply.created_at,
                        "is_deleted": reply.is_deleted,
                        "body": reply.body if not reply.is_deleted else "[đã xoá]",
                        "can_edit": reply.can_edit,
                        "can_delete": reply.can_delete,
                    }
                    for reply in thread.replies
                ],
            }
            for thread in page.threads
        )
        return AssistantCollection(items=items, total=page.total), set()

    async def _resources(
        self, actor: Actor, content_id: uuid.UUID
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """Review material - the *input* side, never confused with output."""
        rows = await self._services.queries.content_resources(actor=actor, content_id=content_id)
        recent = _tail(rows, COLLECTION_LIMIT)
        items = tuple(
            {
                "id": str(row.id),
                "label": row.label,
                "resource_type": resource_type_label(row.resource_type),
                "location": row.location,
                "note": row.note,
                "required_for_review": row.required_for_review,
            }
            for row in recent
        )
        return AssistantCollection(items=items, total=len(rows)), set()

    async def _destinations(
        self, actor: Actor, content_id: uuid.UUID
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """Where the piece sends a customer. Not proof it was published."""
        rows = await self._services.queries.content_destinations(actor=actor, content_id=content_id)
        recent = _tail(rows, COLLECTION_LIMIT)
        items = tuple(
            {
                "id": str(row.id),
                "label": row.label,
                "url": row.url,
                "url_is": "trang đích thương mại, không phải bằng chứng đã đăng",
                "note": row.note,
            }
            for row in recent
        )
        return AssistantCollection(items=items, total=len(rows)), set()

    async def _history(
        self, actor: Actor, content_id: uuid.UUID
    ) -> tuple[AssistantCollection, set[uuid.UUID]]:
        """Bounded transition history - who moved it, when, and what was undone."""
        del actor  # the content read already gated this
        rows = await self._services.undo.history(content_id, limit=HISTORY_LIMIT * 4)
        recent = _tail(rows, HISTORY_LIMIT)
        people: set[uuid.UUID] = set()
        items: list[dict[str, Any]] = []
        for row in recent:
            if row.actor_user_id is not None:
                people.add(row.actor_user_id)
            items.append(
                {
                    "id": str(row.id),
                    "from_stage": stage_label(row.from_stage),
                    "to_stage": stage_label(row.to_stage),
                    "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                    "created_at": row.created_at,
                    "is_undo": row.reverses_event_id is not None,
                    "was_undone": row.reversed_by_event_id is not None,
                }
            )
        return AssistantCollection(items=tuple(items), total=len(rows)), people

    # --- People -----------------------------------------------------------
    async def _display_names(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Every person named anywhere in this context, in **one** query.

        Deliberately not filtered to active users. Historical attribution is the
        whole point of asking "who added this", and a colleague who has left is
        the ordinary case - blanking them would turn a real answer into a hole.
        """
        wanted = {user_id for user_id in user_ids if user_id is not None}
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(User.id, User.full_name).where(User.id.in_(wanted))
        )
        return {row[0]: row[1] for row in rows.all()}


def _with_unavailable(base: MeoBotAssistantContext, reason: str) -> MeoBotAssistantContext:
    """The same context, saying plainly that the named object could not be used."""
    return MeoBotAssistantContext(
        domain_version=base.domain_version,
        domain_block=base.domain_block,
        actor=base.actor,
        object_unavailable_reason=reason,
    )


def _tail[T](rows: Sequence[T], limit: int) -> Sequence[T]:
    """The most recent ``limit`` rows. Every collection here is oldest-first."""
    return rows[-limit:] if len(rows) > limit else rows


def _resolve_people(payload: dict[str, Any], names: Mapping[uuid.UUID, str]) -> dict[str, Any]:
    """Swap the ``*_user_id`` fields for display names, in place of the ids.

    Done after all collections are loaded, so the lookup behind it is one query
    for the whole turn rather than one per row - see the module docstring.

    The id is dropped rather than kept beside the name: a UUID in a prompt is
    tokens the model cannot use and might read back to a person.
    """

    def swap(node: object) -> object:
        if isinstance(node, dict):
            result: dict[str, Any] = {}
            for key, value in node.items():
                if key.endswith("_user_id") and isinstance(value, str):
                    try:
                        resolved = names.get(uuid.UUID(value))
                    except ValueError:
                        resolved = None
                    result[key.removesuffix("_user_id")] = resolved
                    continue
                result[key] = swap(value)
            return result
        if isinstance(node, list):
            return [swap(item) for item in node]
        return node

    return swap(payload)  # type: ignore[return-value]


__all__: list[str] = [
    "COLLECTION_LIMIT",
    "HISTORY_LIMIT",
    "OBJECT_UNAVAILABLE",
    "ContextRef",
    "MeoBotAssistantContextService",
]
