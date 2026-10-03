"""Deciding whether somebody may do a PR thing, and recording who said so.

Every PR write goes through :meth:`PrCapabilityService.require`, and every
approval decision goes through it **with the item in hand**. It applies the rule
written down in :mod:`meobot.domain.pr.policy`:

* for the ten capabilities that are not grant-backed, the actor's role must
  carry the capability's baseline permission - unchanged since Step 1C;
* for the three review gates, at least one **active, in-scope grant** must
  admit them, per :func:`~meobot.domain.pr.policy.grant_admits`.

One rule, one place
-------------------

There is no second implementation of scope matching anywhere. The approval
write, the undo, the "what can I do with this" read model and the Telegram
review tools all call :meth:`require` or :meth:`allows` on this service, and the
scope arithmetic itself is :class:`~meobot.domain.pr.grants.GrantScope`, which
has no session and can be argued with in isolation.

Scoped and unscoped questions
-----------------------------

:meth:`require` takes an optional ``content``. With it, the full rule is
applied. Without it, the weaker question is answered - *could any grant this
person holds ever authorise them at this gate* - which is what a queue picking
lanes and a dashboard counting badges need, and which is deliberately **not**
what any write asks. Every approval path in the module passes ``content``; the
regression suite asserts that by walking the call sites.

Nothing is cached. Effective grants are read from the session on every call, so
a revocation takes effect on the next request rather than when some TTL
expires - which is the behaviour requirement 10 of Step 1F.2.7 asks for, and the
reason there is no memoisation here to be tempted by.

Identity
--------

A grant is keyed on ``users.id`` and nothing else. There is no Telegram id, no
username and no chat anywhere in this module: the same decision has to be
reachable from the bot, the web admin UI and a CLI script, and a rule that
consulted a Telegram handle would work in exactly one of them.

An actor with no ``users`` row - the bootstrap owner before first sync - can
never hold a grant, and :meth:`require` says so explicitly rather than failing
an empty query. That is the honest answer: a grant is given to a person, and
until they have a row there is nobody to give it to.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_support import record_pr_event
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrChannel, PrContentItem, PrContentTarget
from meobot.db.models.pr_authorization import (
    PrUserCapability,
    PrUserCapabilityChannel,
    PrUserCapabilityContentType,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.grants import ContentScopeKey, GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import PrApprovalStage, PrContentType
from meobot.domain.pr.policy import (
    APPROVAL_CAPABILITIES,
    GRANT_BACKED,
    PR_CAPABILITY_ADMIN_PERMISSION,
    PrCapability,
    baseline_permission,
    grant_admits,
    meets_baseline,
    require_baseline,
    require_permission,
    requires_grant,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PrCapabilityGrant:
    """One person's active right, flattened for a client to render.

    A dataclass rather than the ORM row because this crosses the service
    boundary: a web UI listing "who may approve" should not be holding a
    mapped instance attached to a session it does not own. It carries the whole
    scope, because a permissions screen that showed *who* and *which gate* and
    not *over what* would be describing a grant this module no longer issues.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    capability: PrCapability
    scope: GrantScope
    effective_from: date | None
    effective_to: date | None
    revoked_at: datetime | None
    requires_role_baseline: bool
    granted_by_user_id: uuid.UUID | None

    @classmethod
    def from_row(cls, row: PrUserCapability) -> PrCapabilityGrant:
        return cls(
            id=row.id,
            user_id=row.user_id,
            capability=row.capability,
            scope=scope_of(row),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            revoked_at=row.revoked_at,
            requires_role_baseline=row.requires_role_baseline,
            granted_by_user_id=row.granted_by_user_id,
        )


def scope_of(row: PrUserCapability) -> GrantScope:
    """Read a stored grant's scope into the domain object that decides with it.

    The one translation between the three tables and
    :class:`~meobot.domain.pr.grants.GrantScope`. Kept a module function rather
    than a property on the ORM class so the domain object stays reachable from
    tests that never touch a session.
    """
    return GrantScope(
        content_type_scope=row.content_type_scope,
        content_types=frozenset(entry.content_type for entry in row.content_types),
        include_unclassified_content=row.include_unclassified_content,
        channel_scope=row.channel_scope,
        channel_ids=frozenset(entry.channel_id for entry in row.channels),
        include_unassigned_channel=row.include_unassigned_channel,
    )


class PrCapabilityService:
    """Resolves PR capabilities, and grants or revokes the ones that persist.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- The check every PR write makes -----------------------------------
    async def require(
        self,
        actor: Actor,
        capability: PrCapability,
        *,
        content: PrContentItem | None = None,
        on: date | None = None,
    ) -> None:
        """Raise :class:`PrPermissionDeniedError` unless the actor may do this.

        Args:
            actor: The authenticated principal. Only :attr:`Actor.role` and
                :attr:`Actor.user_id` are consulted.
            capability: What they are trying to do.
            content: The item the action is about, when there is one. Supplying
                it applies the grant's scope; omitting it asks only whether the
                actor could ever be authorised - see the module docstring.
            on: The day to judge a dated grant against. Defaults to today in
                UTC - grants are day-scoped and the difference between UTC and
                the display timezone cannot change who may approve without
                somebody having deliberately set a boundary date on the day of
                the handover. Revocation is not day-scoped and ignores this.
        """
        if not requires_grant(capability):
            require_baseline(actor, capability)
            return

        if actor.user_id is None:
            raise PrPermissionDeniedError(
                f"{capability.value} is granted to a user, and this actor has no user row",
                details={
                    "capability": capability.value,
                    "role": actor.role.value,
                    "reason": "actor_has_no_user_row",
                },
            )

        key = await self.content_scope_key(content) if content is not None else None
        grants = await self._active_grants(actor.user_id, capability, on=on)
        for row in grants:
            if grant_admits(
                actor,
                capability,
                requires_role_baseline=row.requires_role_baseline,
                scope=scope_of(row),
                key=key,
            ):
                return

        # Two refusals, two reasons, because they need different next steps: ask
        # for a grant, or ask for this item to be added to the one you have.
        reason = "out_of_grant_scope" if grants and key is not None else "missing_grant"
        details: dict[str, object] = {
            "capability": capability.value,
            "role": actor.role.value,
            "user_id": str(actor.user_id),
            "permission": baseline_permission(capability).value,
            "reason": reason,
        }
        if content is not None and key is not None:
            details["content_id"] = str(content.id)
            details["content_type"] = key.content_type.value if key.content_type else None
            details["channel_ids"] = sorted(str(value) for value in key.channel_ids)
        raise PrPermissionDeniedError(
            f"{capability.value} has not been granted to this user for this content"
            if reason == "out_of_grant_scope"
            else f"{capability.value} has not been granted to this user",
            details=details,
        )

    async def allows(
        self,
        actor: Actor,
        capability: PrCapability,
        *,
        content: PrContentItem | None = None,
        on: date | None = None,
    ) -> bool:
        """The same decision as :meth:`require`, as a boolean.

        For a client deciding which buttons to draw. Never used instead of
        :meth:`require` at a write: a screen drawn a minute ago is not
        authorization.
        """
        try:
            await self.require(actor, capability, content=content, on=on)
        except PrPermissionDeniedError:
            return False
        return True

    async def can_approve(
        self,
        actor: Actor,
        content: PrContentItem,
        approval_stage: PrApprovalStage,
        *,
        on: date | None = None,
    ) -> bool:
        """**The** approval question: may this person decide this item at this gate?

        The named entry point Step 1F.2.7 requires, and a thin one on purpose -
        it is :meth:`allows` with the gate translated to its capability through
        :data:`~meobot.domain.pr.policy.APPROVAL_CAPABILITIES`. Anything that
        wants to know whether an approval is permitted asks this or
        :meth:`require_approval`, and nothing anywhere reimplements either.
        """
        return await self.allows(
            actor, APPROVAL_CAPABILITIES[approval_stage], content=content, on=on
        )

    async def require_approval(
        self,
        actor: Actor,
        content: PrContentItem,
        approval_stage: PrApprovalStage,
        *,
        on: date | None = None,
    ) -> None:
        """:meth:`can_approve`, refusing instead of returning ``False``.

        What every write that records an approval calls, immediately before it
        writes one.
        """
        await self.require(actor, APPROVAL_CAPABILITIES[approval_stage], content=content, on=on)

    # --- Scope of one item -------------------------------------------------
    async def content_scope_key(self, content: PrContentItem) -> ContentScopeKey:
        """The two canonical facts a scope decision is taken against.

        The classification is the item's own column; the channels are every
        ``pr_content_targets`` row it has. Both are read here, once, rather than
        being passed in by callers who might each assemble them differently -
        and neither is derived from a label, a title or a platform name.
        """
        result = await self._session.execute(
            select(PrContentTarget.channel_id).where(PrContentTarget.content_id == content.id)
        )
        return ContentScopeKey(
            content_type=content.content_type,
            channel_ids=frozenset(result.scalars().all()),
        )

    # --- Reading ----------------------------------------------------------
    async def capabilities_for_actor(
        self, actor: Actor, *, on: date | None = None
    ) -> frozenset[PrCapability]:
        """Everything this actor may currently do **somewhere** in the PR module.

        Unscoped by construction: it answers a question about a person, not
        about an item, and a screen uses it to decide which sections exist at
        all. A review capability appears here as soon as one active grant would
        admit the actor for *something*; whether it admits them for the item
        they then open is :meth:`can_approve`, asked per item.
        """
        held = {
            capability
            for capability in PrCapability
            if not requires_grant(capability) and meets_baseline(actor, capability)
        }
        # One query for all three gates rather than one each - and the *same*
        # enumeration the queue is built from, so "do I review at all" and "what
        # is waiting for me" can never be answered from two different readings
        # of the grant table.
        held.update(grant.capability for grant in await self.approval_grants_for(actor, on=on))
        return frozenset(held)

    async def approval_grants_for(
        self, actor: Actor, *, on: date | None = None
    ) -> Sequence[PrCapabilityGrant]:
        """The grants that currently authorise this actor at a review gate.

        Step 1F.2.7a, and the hinge of it. Every grant that is **active** - not
        revoked, inside its dates - and whose ``requires_role_baseline`` test
        this actor passes. Exactly the set :meth:`require` loops over, minus the
        per-item scope test it cannot do without an item.

        That is what makes a read model built on this incapable of drifting from
        the write: the queue asks for the grants, translates each one's scope
        into SQL, and gets the same answer the write would give item by item.
        Nothing else may enumerate grants for that purpose.

        Empty for a principal with no ``users`` row - they can hold no grant, so
        their queue is empty, which is the honest answer rather than a wide one.
        """
        if actor.user_id is None:
            return []
        result = await self._session.execute(
            self._active_query(on=on).where(
                PrUserCapability.user_id == actor.user_id,
                PrUserCapability.capability.in_(sorted(GRANT_BACKED, key=lambda c: c.value)),
            )
        )
        return [
            PrCapabilityGrant.from_row(row)
            for row in result.scalars().all()
            if grant_admits(
                actor,
                row.capability,
                requires_role_baseline=row.requires_role_baseline,
                scope=scope_of(row),
                key=None,
            )
        ]

    async def granted_capabilities(
        self, user_id: uuid.UUID, *, on: date | None = None
    ) -> frozenset[PrCapability]:
        """The grant-backed capabilities this person holds, ignoring their role.

        Deliberately not the same question as
        :meth:`capabilities_for_actor`: this is what the grant table says, and
        an administrator reviewing grants needs that even when the person's
        role currently makes a ``requires_role_baseline`` grant inoperative.
        """
        result = await self._session.execute(
            self._active_query(on=on).where(PrUserCapability.user_id == user_id)
        )
        return frozenset(row.capability for row in result.scalars().all())

    async def users_with(
        self, capability: PrCapability, *, on: date | None = None
    ) -> Sequence[PrCapabilityGrant]:
        """Who currently holds one grant-backed capability, and over what.

        Answers "who may perform TEAM_LEAD_REVIEW". Returns the grants, not the
        users: whoever asks wants the scope and the dates too, and a second
        query for them would be one more chance to read a stale answer.

        Returns an empty sequence for a capability nobody has to be granted -
        the honest answer, since "who may create content" is a question about
        roles rather than about this table.
        """
        if not requires_grant(capability):
            return []
        result = await self._session.execute(
            self._active_query(on=on)
            .where(PrUserCapability.capability == capability)
            .order_by(PrUserCapability.created_at.asc())
        )
        return [PrCapabilityGrant.from_row(row) for row in result.scalars().all()]

    async def active_grants(
        self,
        *,
        user_id: uuid.UUID | None = None,
        capability: PrCapability | None = None,
        on: date | None = None,
    ) -> Sequence[PrCapabilityGrant]:
        """Every active grant, optionally narrowed. What the admin screen lists.

        One query for the whole page rather than one per gate: the permissions
        screen shows all three gates together, and three round trips could
        return three different instants' worth of truth.
        """
        statement = self._active_query(on=on)
        if user_id is not None:
            statement = statement.where(PrUserCapability.user_id == user_id)
        if capability is not None:
            statement = statement.where(PrUserCapability.capability == capability)
        result = await self._session.execute(statement.order_by(PrUserCapability.created_at.asc()))
        return [PrCapabilityGrant.from_row(row) for row in result.scalars().all()]

    # --- Administering grants ---------------------------------------------
    async def grant(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        capability: PrCapability,
        scope: GrantScope | None = None,
        requires_role_baseline: bool = False,
        effective_from: date | None = None,
        effective_to: date | None = None,
        note: str | None = None,
    ) -> PrUserCapability:
        """Give one person one review right, over one scope.

        Gated on ``user.role.manage`` - the existing owner-only permission for
        decisions about who may do what. No new permission was invented for
        this, and **no capability guards itself**, so nobody can grant their way
        into being able to grant, and nobody can widen a grant they hold.

        Args:
            scope: Where the right applies. Defaults to
                :meth:`~meobot.domain.pr.grants.GrantScope.everything` - both
                axes ``ALL`` - which is what a caller that predates scoping
                means and what the existing tests assert. The web UI always
                sends an explicit scope.
            requires_role_baseline: Whether the holder's role must also carry
                the permission. ``False`` - additive - for everything created
                through the API and the panel.

        Raises:
            PrPermissionDeniedError: The actor may not administer grants.
            PrValidationError: The capability is not grant-backed, the dates
                are the wrong way round, the scope covers nothing, or it names
                a channel that does not exist.
            PrNotFoundError: No such user.
            PrConflictError: That person already holds an active grant of this
                capability with exactly this scope.
        """
        require_permission(actor, PR_CAPABILITY_ADMIN_PERMISSION)
        if not requires_grant(capability):
            raise PrValidationError(
                f"{capability.value} is decided by role and cannot be granted",
                details={
                    "capability": capability.value,
                    "permission": baseline_permission(capability).value,
                },
            )
        if (
            effective_from is not None
            and effective_to is not None
            and effective_to < effective_from
        ):
            raise PrValidationError(
                "A grant cannot end before it starts",
                details={
                    "effective_from": effective_from.isoformat(),
                    "effective_to": effective_to.isoformat(),
                },
            )
        if await self._session.get(User, user_id) is None:
            raise PrNotFoundError("No user with that id", details={"user_id": str(user_id)})

        wanted = scope if scope is not None else GrantScope.everything()
        if wanted.is_empty:
            raise PrValidationError(
                "A grant must cover at least one content classification and one channel",
                details={
                    "content_type_scope": wanted.content_type_scope.value,
                    "channel_scope": wanted.channel_scope.value,
                },
            )
        await self._require_channels_exist(wanted.channel_ids)

        for existing in await self.active_grants(user_id=user_id, capability=capability):
            if existing.scope == wanted:
                raise PrConflictError(
                    "That person already holds an active grant of this capability "
                    "over exactly this scope",
                    details={
                        "user_id": str(user_id),
                        "capability": capability.value,
                        "grant_id": str(existing.id),
                    },
                )

        row = PrUserCapability(
            user_id=user_id,
            capability=capability,
            content_type_scope=wanted.content_type_scope,
            include_unclassified_content=wanted.include_unclassified_content,
            channel_scope=wanted.channel_scope,
            include_unassigned_channel=wanted.include_unassigned_channel,
            requires_role_baseline=requires_role_baseline,
            effective_from=effective_from,
            effective_to=effective_to,
            granted_by_user_id=actor.user_id,
            note=note,
        )
        row.content_types = [
            PrUserCapabilityContentType(content_type=value)
            for value in sorted(wanted.content_types, key=lambda entry: entry.value)
        ]
        row.channels = [
            PrUserCapabilityChannel(channel_id=value)
            for value in sorted(wanted.channel_ids, key=str)
        ]
        self._session.add(row)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CAPABILITY_GRANTED,
            entity_type="pr_user_capability",
            entity_id=row.id,
            after=_scope_audit(
                user_id=user_id,
                capability=capability,
                scope=wanted,
                requires_role_baseline=requires_role_baseline,
                effective_from=effective_from,
                effective_to=effective_to,
            ),
        )
        logger.info(
            "pr_capability_granted",
            extra={
                "pr_user_id": str(user_id),
                "capability": capability.value,
                "content_type_scope": wanted.content_type_scope.value,
                "channel_scope": wanted.channel_scope.value,
            },
        )
        return row

    async def revoke(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        grant_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        capability: PrCapability | None = None,
        effective_to: date | None = None,
    ) -> PrUserCapability:
        """Withdraw a grant, effective immediately. Never deletes a row.

        ``revoked_at`` is stamped with the current instant, and that is what
        authorization reads - so the person loses the right on their next
        request rather than at the end of the day. ``effective_to`` is dated
        alongside it for the benefit of anybody reading the history.

        An approval recorded last year was legitimate under the grant that
        existed then; erasing the grant would make that history unreadable.

        Name the grant with ``grant_id``. ``(user_id, capability)`` is accepted
        for callers that predate scoped grants and resolves only when the
        person holds exactly one active grant of that capability - with two, it
        refuses rather than guessing which right to take away.

        Raises:
            PrNotFoundError: There is no active grant to withdraw.
            PrValidationError: Neither form of identification was given, the
                pair is ambiguous, or the end date precedes the start.
        """
        require_permission(actor, PR_CAPABILITY_ADMIN_PERMISSION)
        row = await self._grant_to_revoke(grant_id=grant_id, user_id=user_id, capability=capability)

        closing = effective_to or utcnow().date()
        if row.effective_from is not None and closing < row.effective_from:
            raise PrValidationError(
                "A grant cannot end before it starts",
                details={
                    "effective_from": row.effective_from.isoformat(),
                    "effective_to": closing.isoformat(),
                },
            )
        row.revoked_at = utcnow()
        row.revoked_by_user_id = actor.user_id
        row.effective_to = closing
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CAPABILITY_REVOKED,
            entity_type="pr_user_capability",
            entity_id=row.id,
            after={
                "user_id": str(row.user_id),
                "capability": row.capability.value,
                "effective_to": closing.isoformat(),
                "revoked_at": row.revoked_at.isoformat(),
            },
        )
        logger.info(
            "pr_capability_revoked",
            extra={"pr_user_id": str(row.user_id), "capability": row.capability.value},
        )
        return row

    async def revoke_all(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        capability: PrCapability,
    ) -> Sequence[PrUserCapability]:
        """Withdraw **every** active grant of one capability from one person.

        The unambiguous reading of "take Head Review away from Hảo", and the one
        a conversational client needs: since Step 1F.2.7 she may hold several
        grants of that gate over different scopes, and a chat message has no way
        to name one of them. Revoking is the safe direction - it can only ever
        remove authority - so answering the plain sentence plainly is right here,
        where refusing on ambiguity would leave somebody unable to take a right
        back at all.

        Raises:
            PrNotFoundError: There is no active grant to withdraw.
        """
        require_permission(actor, PR_CAPABILITY_ADMIN_PERMISSION)
        result = await self._session.execute(
            self._active_query(on=None).where(
                PrUserCapability.user_id == user_id,
                PrUserCapability.capability == capability,
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            raise PrNotFoundError(
                "That person holds no active grant of this capability",
                details={"user_id": str(user_id), "capability": capability.value},
            )
        return [
            await self.revoke(actor=actor, request_id=request_id, grant_id=row.id) for row in rows
        ]

    # --- Internals --------------------------------------------------------
    async def _grant_to_revoke(
        self,
        *,
        grant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        capability: PrCapability | None,
    ) -> PrUserCapability:
        """Resolve which row a revocation is about. See :meth:`revoke`."""
        if grant_id is not None:
            row = await self._session.get(PrUserCapability, grant_id)
            if row is None or row.revoked_at is not None:
                raise PrNotFoundError(
                    "There is no active grant with that id",
                    details={"grant_id": str(grant_id)},
                )
            return row
        if user_id is None or capability is None:
            raise PrValidationError(
                "Name the grant to withdraw, by id or by person and capability",
                details={"reason": "grant_not_identified"},
            )
        result = await self._session.execute(
            self._active_query(on=None).where(
                PrUserCapability.user_id == user_id,
                PrUserCapability.capability == capability,
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            raise PrNotFoundError(
                "That person holds no active grant of this capability",
                details={"user_id": str(user_id), "capability": capability.value},
            )
        if len(rows) > 1:
            raise PrValidationError(
                "That person holds several grants of this capability - name the one to withdraw",
                details={
                    "user_id": str(user_id),
                    "capability": capability.value,
                    "grant_ids": sorted(str(row.id) for row in rows),
                },
            )
        return rows[0]

    async def _require_channels_exist(self, channel_ids: Iterable[uuid.UUID]) -> None:
        """Refuse a scope naming a channel that is not there.

        A grant over a channel id nobody can produce content for is a grant
        that silently does nothing, and the person who wrote it would have no
        way to tell. Checked here rather than left to the foreign key so the
        refusal names the ids.
        """
        wanted = set(channel_ids)
        if not wanted:
            return
        found = await self._session.execute(select(PrChannel.id).where(PrChannel.id.in_(wanted)))
        missing = wanted - set(found.scalars().all())
        if missing:
            raise PrNotFoundError(
                "No channel with that id",
                details={"channel_ids": sorted(str(value) for value in missing)},
            )

    def _active_query(self, *, on: date | None) -> Select[tuple[PrUserCapability]]:
        """Grants in force on one day, and not withdrawn.

        Closed date intervals with ``NULL`` unbounded, plus the revocation test
        - which is not day-scoped, because the whole point of ``revoked_at`` is
        that it does not wait for a day to end.
        """
        day = on or utcnow().date()
        return select(PrUserCapability).where(
            PrUserCapability.revoked_at.is_(None),
            or_(
                PrUserCapability.effective_from.is_(None),
                PrUserCapability.effective_from <= day,
            ),
            or_(PrUserCapability.effective_to.is_(None), PrUserCapability.effective_to >= day),
        )

    async def _active_grants(
        self, user_id: uuid.UUID, capability: PrCapability, *, on: date | None
    ) -> Sequence[PrUserCapability]:
        result = await self._session.execute(
            self._active_query(on=on).where(
                PrUserCapability.user_id == user_id,
                PrUserCapability.capability == capability,
            )
        )
        return list(result.scalars().all())


def _scope_audit(
    *,
    user_id: uuid.UUID,
    capability: PrCapability,
    scope: GrantScope,
    requires_role_baseline: bool,
    effective_from: date | None,
    effective_to: date | None,
) -> dict[str, object]:
    """What a grant looks like in the audit trail.

    The whole scope, spelled out: "somebody was given Head Review" is not a
    reviewable record of a decision when the interesting half is *over what*.
    """
    return {
        "user_id": str(user_id),
        "capability": capability.value,
        "content_type_scope": scope.content_type_scope.value,
        "content_types": sorted(value.value for value in scope.content_types),
        "include_unclassified_content": scope.include_unclassified_content,
        "channel_scope": scope.channel_scope.value,
        "channel_ids": sorted(str(value) for value in scope.channel_ids),
        "include_unassigned_channel": scope.include_unassigned_channel,
        "requires_role_baseline": requires_role_baseline,
        "effective_from": effective_from.isoformat() if effective_from else None,
        "effective_to": effective_to.isoformat() if effective_to else None,
    }


__all__: list[str] = [
    "ContentScopeKey",
    "GrantScope",
    "PrCapabilityGrant",
    "PrCapabilityService",
    "PrContentType",
    "PrGrantScopeMode",
    "scope_of",
]
