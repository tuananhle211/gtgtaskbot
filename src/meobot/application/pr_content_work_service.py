"""Which work type a content milestone counts as. M3.

A small service, and small on purpose: it owns the **one** administratively
configurable thing in M3 and nothing else. The milestones, the contributor
rules, the independent-validation boundary and the source-key contract are
domain rules in :mod:`meobot.domain.pr.content_work`, because those are the
department's anti-gaming boundary rather than its taxonomy - a settings screen
that let somebody redefine *"content creation is complete when it is
submitted"* would be a settings screen that turns the boundary off.

What is configurable is the sentence *"a short-video script that gets approved
is one ``SHORT_VIDEO_SCRIPT`` of work"*, and it has to be, because it is exactly
what changes without a deploy.

Resolution
----------

A rule naming a ``content_type`` beats the kind's default. Both are loaded once
per projector run and matched in memory - see :meth:`PrContentWorkRuleService.resolver` -
because the projector asks this question once per candidate and a query per
candidate is the N+1 a batch reconcile would pay a hundred times over.

**No mapping means no work - so a missing mapping is provisioned, never
guessed.** There is no fallback to whichever work type sorts first, and no
inference from the content's title, channel or a display name that happens to
match. What the projector does instead, since ``0040``, is bind an unmapped
content type to a work type it creates for exactly that type and kind - see
:meth:`PrContentWorkRuleService.ensure_auto_rule` - so the first accepted
deliverable of a new content type is counted rather than reported as
``NO_MAPPING``. An explicit rule, exact or default, always wins over that; a
rule an administrator has deactivated is an explicit decision and is honoured.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import record_pr_event
from meobot.db.models.pr_content_work import PrContentWorkRule
from meobot.db.models.pr_work import PrWorkType
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_work import PrContentWorkKind
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.policy import PrCapability

#: The longest note a rule may carry.
MAX_RULE_NOTE = 2000


@dataclass(frozen=True, slots=True)
class ContentWorkResolver:
    """The active mapping, loaded once and matched in memory.

    A value object rather than a service call per candidate: the projector asks
    *"what work type is this"* once for every content item in a batch, and a
    hundred-item reconcile would otherwise be a hundred queries against a table
    with a handful of rows in it.

    Immutable, so a batch cannot half-see a mapping change somebody made while
    it was running - every candidate in one run is resolved against one picture
    of the configuration, which is what makes the run's report mean something.
    """

    #: ``(kind, content_type)`` -> work type. The specific rules.
    exact: Mapping[tuple[PrContentWorkKind, PrContentType], uuid.UUID]
    #: ``kind`` -> work type. The kind's default, when one is configured.
    default: Mapping[PrContentWorkKind, uuid.UUID]

    def resolve(
        self, kind: PrContentWorkKind, content_type: PrContentType | None
    ) -> uuid.UUID | None:
        """The work type for this milestone, or ``None`` when nothing maps it.

        **Exact beats default**, and ``None`` really means *no answer*: the
        caller reports
        :attr:`~meobot.domain.pr.content_work.PrContentWorkOutcome.NO_MAPPING`
        and writes nothing rather than guessing.

        Content whose own ``content_type`` is ``NULL`` falls to the default, and
        the two meanings of ``NULL`` coincide usefully rather than by accident:
        neither the piece nor the configuration has anything more specific to
        say.
        """
        if content_type is not None:
            found = self.exact.get((kind, content_type))
            if found is not None:
                return found
        return self.default.get(kind)

    def bind(
        self, kind: PrContentWorkKind, content_type: PrContentType, work_type_id: uuid.UUID
    ) -> None:
        """Make a binding this run **itself** just provisioned visible to the rest of it.

        The one write this value object accepts, and it does not weaken the
        one-picture-per-run rule above: a batch that provisions a type for the
        first piece of a new content type must see that same type for the
        second piece in the same batch, or it would ask the database again for
        an answer it already has.
        """
        exact = self.exact
        assert isinstance(exact, dict)
        exact[(kind, content_type)] = work_type_id


class PrContentWorkRuleService:
    """Reads and writes the Content → Work mapping. ``PR_WORK_CONFIGURE``.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Gates every write on ``PR_WORK_CONFIGURE`` - the same
            capability that configures the work taxonomy and approves a KPI
            plan, because this is the same kind of decision and inventing a
            fourth capability for it would be paperwork with no invariant
            behind it.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities

    # =====================================================================
    # Reading
    # =====================================================================
    async def resolver(self) -> ContentWorkResolver:
        """Load the active mapping. **No capability check.**

        Called by the projector, which acts as the worker and holds no
        capabilities - and reads no personal data here: the table says which
        work type a kind of content maps to, which is department configuration
        rather than anybody's record.
        """
        rows = await self._active_rules()
        exact: dict[tuple[PrContentWorkKind, PrContentType], uuid.UUID] = {}
        default: dict[PrContentWorkKind, uuid.UUID] = {}
        for row in rows:
            if row.content_type is None:
                default[row.contribution_kind] = row.work_type_id
            else:
                exact[(row.contribution_kind, row.content_type)] = row.work_type_id
        return ContentWorkResolver(exact=exact, default=default)

    async def list_rules(self, *, actor: Actor) -> Sequence[PrContentWorkRule]:
        """Every rule, active or not, for a configuration screen.

        ``PR_WORK_CONFIGURE``: this is the screen that decides what future work
        is filed as, and a deactivated rule is part of the picture somebody
        editing it needs.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        statement = select(PrContentWorkRule).order_by(
            PrContentWorkRule.contribution_kind.asc(),
            PrContentWorkRule.content_type.asc().nulls_first(),
        )
        return (await self._session.execute(statement)).scalars().all()

    async def rule_for(
        self, kind: PrContentWorkKind, content_type: PrContentType | None
    ) -> PrContentWorkRule | None:
        """The one rule row for this case, **active or not**. No capability check.

        What the projector asks on the missing-binding path only: a resolver
        holds the active rules, and the difference between *no rule* and *a
        rule somebody turned off* is the difference between provisioning a
        binding and honouring a decision not to have one.
        """
        return await self._rule_for(kind, content_type)

    async def work_types_for(
        self, rules: Sequence[PrContentWorkRule]
    ) -> dict[uuid.UUID, PrWorkType]:
        """The work types those rules name, in one query rather than per row."""
        wanted = {row.work_type_id for row in rules}
        if not wanted:
            return {}
        statement = select(PrWorkType).where(PrWorkType.id.in_(wanted))
        return {row.id: row for row in (await self._session.execute(statement)).scalars()}

    # =====================================================================
    # Writing
    # =====================================================================
    async def upsert_rule(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        contribution_kind: PrContentWorkKind,
        content_type: PrContentType | None,
        work_type_id: uuid.UUID,
        is_active: bool = True,
        note: str | None = None,
    ) -> PrContentWorkRule:
        """Set the mapping for one case. ``PR_WORK_CONFIGURE``. **Idempotent.**

        Upsert rather than create-or-fail, because *"short-video scripts count as
        ``SHORT_VIDEO_SCRIPT``"* is a statement about the world rather than a row
        somebody owns: saying it twice should leave one rule, and saying
        something different should replace the first answer rather than produce
        two rules the projector has to choose between. The two partial unique
        indexes say the same thing from underneath.

        The work type is **validated, not trusted**: an inactive type is refused
        here rather than discovered later as work filed under a heading the
        department retired.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        work_type = await self._session.get(PrWorkType, work_type_id)
        if work_type is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.",
                details={"entity": "pr_work_type", "id": str(work_type_id)},
            )
        if not work_type.is_active:
            raise PrValidationError(
                "Loại công việc này đã ngừng sử dụng nên không dùng để ánh xạ được.",
                details={
                    "field": "work_type_id",
                    "reason": "work_type_inactive",
                    "work_type_code": work_type.code,
                },
            )

        existing = await self._rule_for(contribution_kind, content_type)
        before = (
            {
                "work_type_id": str(existing.work_type_id),
                "is_active": existing.is_active,
            }
            if existing is not None
            else None
        )
        if existing is None:
            existing = PrContentWorkRule(
                contribution_kind=contribution_kind,
                content_type=content_type,
                work_type_id=work_type.id,
                is_active=is_active,
                note=_optional_text(note, "note", MAX_RULE_NOTE),
                created_by_user_id=_require_user_id(actor),
            )
            self._session.add(existing)
        else:
            existing.work_type_id = work_type.id
            existing.is_active = is_active
            if note is not None:
                existing.note = _optional_text(note, "note", MAX_RULE_NOTE)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=(
                AuditAction.PR_CONTENT_WORK_RULE_CREATED
                if before is None
                else AuditAction.PR_CONTENT_WORK_RULE_UPDATED
            ),
            entity_type="pr_content_work_rule",
            entity_id=existing.id,
            before=before,
            after={
                "contribution_kind": contribution_kind.value,
                "content_type": content_type.value if content_type else None,
                "work_type_id": str(work_type.id),
                "work_type_code": work_type.code,
                "is_active": is_active,
            },
        )
        return existing

    async def ensure_auto_rule(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        contribution_kind: PrContentWorkKind,
        content_type: PrContentType,
        work_type: PrWorkType,
    ) -> PrContentWorkRule:
        """Bind a content type the system just provisioned a work type for. Internal.

        **Reachable from no route, and checks no capability.** The projector is
        the caller, acting as the worker, and the trust is the content milestone
        that triggered it. The row it writes is an ordinary exact rule - it
        appears on the mapping screen, an administrator may remap or deactivate
        it through :meth:`upsert_rule` like any other - with one difference:
        ``created_by_user_id`` is ``NULL``, because nobody decided it. That is
        the provenance marker, and it is never rewritten by a later edit.

        Idempotent on the ``(kind, content_type)`` unique index. A rule already
        there, **whatever its state**, is returned as it is: an active one is
        the binding, and an inactive one is a decision the caller must honour
        rather than a gap to fill. Two workers binding the same type at once
        both insert; one loses on the index and re-reads the winner's row
        inside the savepoint rather than failing the projection.
        """
        if not work_type.is_active:
            raise PrValidationError(
                "Loại công việc này đã ngừng sử dụng nên không dùng để ánh xạ được.",
                details={
                    "field": "work_type_id",
                    "reason": "work_type_inactive",
                    "work_type_code": work_type.code,
                },
            )
        existing = await self._rule_for(contribution_kind, content_type)
        if existing is not None:
            return existing
        row = PrContentWorkRule(
            contribution_kind=contribution_kind,
            content_type=content_type,
            work_type_id=work_type.id,
            is_active=True,
            note="Hệ thống tự tạo khi nội dung đầu tiên của loại này được duyệt.",
            created_by_user_id=None,
        )
        # Added inside the savepoint - see ``ensure_source_work_type`` on why.
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # Another worker bound it a moment ago. Theirs is the rule.
            if row in self._session:
                self._session.expunge(row)
            winner = await self._rule_for(contribution_kind, content_type)
            if winner is None:  # pragma: no cover - the index refused for another reason
                raise
            return winner
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_WORK_RULE_CREATED,
            entity_type="pr_content_work_rule",
            entity_id=row.id,
            before=None,
            after={
                "contribution_kind": contribution_kind.value,
                "content_type": content_type.value,
                "work_type_id": str(work_type.id),
                "work_type_code": work_type.code,
                "is_active": True,
                "reason": "content_auto_provision",
            },
        )
        return row

    # =====================================================================
    # Internals
    # =====================================================================
    async def _active_rules(self) -> Sequence[PrContentWorkRule]:
        statement = select(PrContentWorkRule).where(PrContentWorkRule.is_active.is_(True))
        return (await self._session.execute(statement)).scalars().all()

    async def _rule_for(
        self, kind: PrContentWorkKind, content_type: PrContentType | None
    ) -> PrContentWorkRule | None:
        statement = select(PrContentWorkRule).where(
            PrContentWorkRule.contribution_kind == kind,
            PrContentWorkRule.content_type.is_(None)
            if content_type is None
            else PrContentWorkRule.content_type == content_type,
        )
        return (await self._session.execute(statement)).scalars().one_or_none()


def _require_user_id(actor: Actor) -> uuid.UUID:
    if actor.user_id is None:
        raise PrPermissionDeniedError(
            "This actor has no user row, so it cannot configure a mapping",
            details={"reason": "actor_has_no_user_row"},
        )
    return actor.user_id


def _optional_text(value: str | None, field: str, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > limit:
        raise PrValidationError(
            f"{field} is longer than {limit} characters",
            details={"field": field, "reason": "too_long", "maximum": limit},
        )
    return trimmed


__all__: list[str] = [
    "MAX_RULE_NOTE",
    "ContentWorkResolver",
    "PrContentWorkRuleService",
]
