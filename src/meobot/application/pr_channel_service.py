"""Channels, and who is responsible for them over which dates.

Two rules live here that the database deliberately does not enforce, both
recorded as deferred in ``docs/pr/STEP_1A_PR_CORE_FOUNDATION.md``:

**Assignment periods must not overlap.** The partial unique index refuses a
second *open* row for one ``(channel, person, role)`` and nothing more, so two
closed rows covering the same months, or a closed row overlapping an open one,
are accepted by the schema. Both are data-entry mistakes and both are refused
here, using the closed-interval rule in :mod:`meobot.domain.pr.assignments` -
``effective_to`` is the last day in force, so a handover cannot share a day.

**A brand's code never changes.** ``PrBrand`` already refuses it in the mapper,
but a mapper validator only sees writes that go through a mapped instance.
:meth:`PrChannelService.update_brand` refuses it again at the service boundary,
with a typed error rather than a bare ``ValueError``, so a client gets
something it can act on.

Channel URLs are stored and never used as keys, exactly as Step 1A specified:
nothing in this service looks a channel up by URL, and ``external_id`` remains
the platform's identifier.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.core.logging import get_logger
from meobot.db.models.pr import (
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrPlatform,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.assignments import first_overlapping
from meobot.domain.pr.errors import (
    PrAssignmentOverlapError,
    PrConflictError,
    PrImmutableFieldError,
    PrNotFoundError,
    PrValidationError,
)
from meobot.domain.pr.models import (
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrChannelStatus,
    PrEntityStatus,
)
from meobot.domain.pr.policy import PR_READ_PERMISSION, PrCapability, require_permission

logger = get_logger(__name__)

MAX_CHANNEL_NAME_LENGTH = 200
#: Step 1F.2.4a. Matches ``pr_channels.handle``. Generous - a YouTube handle,
#: an ``@`` and a long brand name all fit - because the column is display text
#: rather than an identifier anything looks up.
MAX_CHANNEL_HANDLE_LENGTH = 200

#: The tiers ``ck_pr_channels_tier_in_range`` permits.
VALID_TIERS: frozenset[int] = frozenset({1, 2, 3})


@dataclass(frozen=True, slots=True)
class CreateChannelCommand:
    """One account or property the team publishes to.

    **There is no ``code`` field**: Step 1C.1 allocates ``CH-nnnn``
    server-side, and channel numbering does not reset by year.
    """

    name: str
    platform_id: uuid.UUID
    category: PrChannelCategory
    brand_id: uuid.UUID | None = None
    tier: int | None = None
    external_id: str | None = None
    url: str | None = None
    #: Step 1F.2.4a. ``@drtien``, ``apexmed``, ``UCxxxxxxxx`` - optional, stored
    #: as written. Blank or whitespace becomes ``None`` rather than an empty
    #: handle, which is a different and less honest thing.
    handle: str | None = None
    started_at: date | None = None


@dataclass(frozen=True, slots=True)
class UpdateChannelCommand:
    """Fields a channel may have changed. ``None`` means "leave alone".

    ``code`` is absent by design: a channel code is printed on reports and
    pasted into spreadsheets that outlive a release, and this service offers no
    way to change one.
    """

    channel_id: uuid.UUID
    name: str | None = None
    category: PrChannelCategory | None = None
    brand_id: uuid.UUID | None = None
    tier: int | None = None
    external_id: str | None = None
    url: str | None = None
    handle: str | None = None
    #: Step 1F.2.4a. A channel registered on the wrong platform used to be
    #: unfixable, which mattered little while a platform was a label and matters
    #: a lot now that it decides how a channel's numbers are read. Changing it
    #: **moves no history**: the readings taken under the old platform stay
    #: exactly where they are, attached to the same channel identity, and the
    #: panel warns before the change rather than silently reinterpreting them.
    platform_id: uuid.UUID | None = None
    status: PrChannelStatus | None = None


class PrChannelService:
    """Channel master data and the dated assignments hanging off it.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
        codes: Allocates ``CH-nnnn`` on the same session.
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

    # --- Reading ----------------------------------------------------------
    async def get_channel(self, *, actor: Actor, channel_id: uuid.UUID) -> PrChannel:
        require_permission(actor, PR_READ_PERMISSION)
        return await self._require_channel(channel_id)

    async def list_channels(
        self,
        *,
        actor: Actor,
        brand_id: uuid.UUID | None = None,
        platform_id: uuid.UUID | None = None,
        status: PrChannelStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[PrChannel]:
        require_permission(actor, PR_READ_PERMISSION)
        statement = select(PrChannel)
        if brand_id is not None:
            statement = statement.where(PrChannel.brand_id == brand_id)
        if platform_id is not None:
            statement = statement.where(PrChannel.platform_id == platform_id)
        if status is not None:
            statement = statement.where(PrChannel.status == status)
        statement = statement.order_by(PrChannel.code.asc()).limit(limit).offset(offset)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def list_channel_assignments(
        self,
        *,
        actor: Actor,
        channel_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        assignment_role: PrChannelAssignmentRole | None = None,
        open_only: bool = False,
    ) -> Sequence[PrChannelAssignment]:
        """Who has held which role here, in date order."""
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            self._assignment_query(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=assignment_role,
                open_only=open_only,
            ).order_by(PrChannelAssignment.effective_from.asc())
        )
        return result.scalars().all()

    # --- Master data ------------------------------------------------------
    async def create_channel(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreateChannelCommand
    ) -> PrChannel:
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        name = self._require_text(command.name, "name", MAX_CHANNEL_NAME_LENGTH)
        self._require_valid_tier(command.tier)
        handle = self._optional_text(command.handle, "handle", MAX_CHANNEL_HANDLE_LENGTH)
        code = await self._codes.allocate_channel_code()
        platform = await self._require_active_platform(command.platform_id)
        if (
            command.brand_id is not None
            and await self._session.get(PrBrand, command.brand_id) is None
        ):
            raise PrNotFoundError(
                "No brand with that id", details={"brand_id": str(command.brand_id)}
            )

        channel = PrChannel(
            code=code,
            name=name,
            platform_id=command.platform_id,
            brand_id=command.brand_id,
            tier=command.tier,
            category=command.category,
            external_id=command.external_id,
            url=command.url,
            handle=handle,
            started_at=command.started_at,
        )
        self._session.add(channel)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_CREATED,
            entity_type="pr_channel",
            entity_id=channel.id,
            after={
                "code": channel.code,
                "platform_id": str(channel.platform_id),
                "platform_code": platform.code,
                "category": channel.category.value,
            },
        )
        return channel

    async def update_channel(
        self, *, actor: Actor, request_id: uuid.UUID, command: UpdateChannelCommand
    ) -> PrChannel:
        """Change a channel's mutable fields. Its code is not one of them."""
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        channel = await self._require_channel(command.channel_id)
        before = {
            "name": channel.name,
            "category": channel.category.value,
            "status": channel.status.value,
            "tier": channel.tier,
            "platform_id": str(channel.platform_id),
            "handle": channel.handle,
        }

        if command.platform_id is not None and command.platform_id != channel.platform_id:
            # Re-validated exactly as a create does: a channel may not be moved
            # onto a platform that has been retired, or onto one that is not
            # there. Metric history is untouched - see ``UpdateChannelCommand``.
            await self._require_active_platform(command.platform_id)
            channel.platform_id = command.platform_id
        if command.handle is not None:
            channel.handle = self._optional_text(
                command.handle, "handle", MAX_CHANNEL_HANDLE_LENGTH
            )
        if command.name is not None:
            channel.name = self._require_text(command.name, "name", MAX_CHANNEL_NAME_LENGTH)
        if command.category is not None:
            channel.category = command.category
        if command.brand_id is not None:
            if await self._session.get(PrBrand, command.brand_id) is None:
                raise PrNotFoundError(
                    "No brand with that id", details={"brand_id": str(command.brand_id)}
                )
            channel.brand_id = command.brand_id
        if command.tier is not None:
            self._require_valid_tier(command.tier)
            channel.tier = command.tier
        if command.external_id is not None:
            channel.external_id = command.external_id
        if command.url is not None:
            channel.url = command.url
        if command.status is not None:
            channel.status = command.status
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_UPDATED,
            entity_type="pr_channel",
            entity_id=channel.id,
            before=before,
            after={
                "name": channel.name,
                "category": channel.category.value,
                "status": channel.status.value,
                "tier": channel.tier,
                "platform_id": str(channel.platform_id),
                "handle": channel.handle,
            },
        )
        return channel

    async def update_brand(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        brand_id: uuid.UUID,
        name: str | None = None,
        code: str | None = None,
    ) -> PrBrand:
        """Rename a brand. Refuses a code change.

        Raises:
            PrImmutableFieldError: ``code`` differs from the stored one.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        brand = await self._session.get(PrBrand, brand_id)
        if brand is None:
            raise PrNotFoundError("No brand with that id", details={"brand_id": str(brand_id)})
        if code is not None and code.strip() != brand.code:
            raise PrImmutableFieldError(
                "A brand code cannot be changed once set",
                details={"brand_id": str(brand_id), "current": brand.code, "submitted": code},
            )
        if name is not None:
            brand.name = self._require_text(name, "name", MAX_CHANNEL_NAME_LENGTH)
        await self._session.flush()
        return brand

    # --- Assignments ------------------------------------------------------
    async def assign_user(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        channel_id: uuid.UUID,
        user_id: uuid.UUID,
        assignment_role: PrChannelAssignmentRole,
        effective_from: date,
        effective_to: date | None = None,
        is_primary: bool = False,
        allocation_percent: Decimal = Decimal("100"),
    ) -> PrChannelAssignment:
        """Open a dated assignment, refusing any overlap for the same triple.

        Raises:
            PrValidationError: The range ends before it starts, or the
                allocation is outside 0-100.
            PrAssignmentOverlapError: Another row for this channel, person and
                role already covers one of these days.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        channel = await self._lock_channel(channel_id)
        if await self._session.get(User, user_id) is None:
            raise PrNotFoundError("No user with that id", details={"user_id": str(user_id)})
        if effective_to is not None and effective_to < effective_from:
            raise PrValidationError(
                "An assignment cannot end before it starts",
                details={
                    "effective_from": effective_from.isoformat(),
                    "effective_to": effective_to.isoformat(),
                },
            )
        if allocation_percent < Decimal(0) or allocation_percent > Decimal(100):
            raise PrValidationError(
                "Allocation must be between 0 and 100",
                details={"allocation_percent": str(allocation_percent)},
            )

        existing = await self._session.execute(
            self._assignment_query(
                channel_id=channel_id, user_id=user_id, assignment_role=assignment_role
            )
        )
        clash = first_overlapping(
            existing.scalars().all(), effective_from=effective_from, effective_to=effective_to
        )
        if clash is not None:
            raise PrAssignmentOverlapError(
                "This person already holds that role on this channel over part of that period",
                details={
                    "channel_id": str(channel_id),
                    "user_id": str(user_id),
                    "assignment_role": assignment_role.value,
                    "requested_from": effective_from.isoformat(),
                    "requested_to": effective_to.isoformat() if effective_to else None,
                    "existing_from": clash.effective_from.isoformat(),
                    "existing_to": clash.effective_to.isoformat() if clash.effective_to else None,
                },
            )

        assignment = PrChannelAssignment(
            channel_id=channel_id,
            user_id=user_id,
            assignment_role=assignment_role,
            is_primary=is_primary,
            allocation_percent=allocation_percent,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        self._session.add(assignment)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_ASSIGNED,
            entity_type="pr_channel_assignment",
            entity_id=assignment.id,
            after={
                "channel_id": str(channel_id),
                "channel_code": channel.code,
                "user_id": str(user_id),
                "assignment_role": assignment_role.value,
                "effective_from": effective_from.isoformat(),
                "effective_to": effective_to.isoformat() if effective_to else None,
            },
        )
        return assignment

    async def close_assignment(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        assignment_id: uuid.UUID,
        effective_to: date,
    ) -> PrChannelAssignment:
        """Set an end date on an open assignment. Never deletes a row.

        Raises:
            PrConflictError: It is already closed.
            PrValidationError: The end date precedes the start.
            PrAssignmentOverlapError: Closing here would leave this row
                overlapping another for the same triple.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        assignment = await lock_row(self._session, PrChannelAssignment, assignment_id)
        if assignment is None:
            raise PrNotFoundError(
                "No channel assignment with that id",
                details={"assignment_id": str(assignment_id)},
            )
        if assignment.effective_to is not None:
            raise PrConflictError(
                "That channel assignment is already closed",
                details={
                    "assignment_id": str(assignment_id),
                    "effective_to": assignment.effective_to.isoformat(),
                },
            )
        if effective_to < assignment.effective_from:
            raise PrValidationError(
                "An assignment cannot end before it starts",
                details={
                    "effective_from": assignment.effective_from.isoformat(),
                    "effective_to": effective_to.isoformat(),
                },
            )

        # Shortening an open row can only remove days, so it cannot create a
        # new clash - but the row may have been opened before this rule
        # existed, so the check runs anyway rather than assuming history is
        # clean.
        others = await self._session.execute(
            self._assignment_query(
                channel_id=assignment.channel_id,
                user_id=assignment.user_id,
                assignment_role=assignment.assignment_role,
            ).where(PrChannelAssignment.id != assignment.id)
        )
        clash = first_overlapping(
            others.scalars().all(),
            effective_from=assignment.effective_from,
            effective_to=effective_to,
        )
        if clash is not None:
            raise PrAssignmentOverlapError(
                "Closing this assignment there would overlap another for the same role",
                details={
                    "assignment_id": str(assignment_id),
                    "existing_from": clash.effective_from.isoformat(),
                    "existing_to": clash.effective_to.isoformat() if clash.effective_to else None,
                },
            )

        assignment.effective_to = effective_to
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_ASSIGNMENT_CLOSED,
            entity_type="pr_channel_assignment",
            entity_id=assignment.id,
            after={
                "channel_id": str(assignment.channel_id),
                "user_id": str(assignment.user_id),
                "assignment_role": assignment.assignment_role.value,
                "effective_to": effective_to.isoformat(),
            },
        )
        return assignment

    # --- Internals --------------------------------------------------------
    @staticmethod
    def _assignment_query(
        *,
        channel_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        assignment_role: PrChannelAssignmentRole | None = None,
        open_only: bool = False,
    ) -> Select[tuple[PrChannelAssignment]]:
        statement = select(PrChannelAssignment).where(PrChannelAssignment.channel_id == channel_id)
        if user_id is not None:
            statement = statement.where(PrChannelAssignment.user_id == user_id)
        if assignment_role is not None:
            statement = statement.where(PrChannelAssignment.assignment_role == assignment_role)
        if open_only:
            statement = statement.where(PrChannelAssignment.effective_to.is_(None))
        return statement

    async def _require_channel(self, channel_id: uuid.UUID) -> PrChannel:
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        return channel

    async def _lock_channel(self, channel_id: uuid.UUID) -> PrChannel:
        channel = await lock_row(self._session, PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        return channel

    async def _require_active_platform(self, platform_id: uuid.UUID) -> PrPlatform:
        """The platform a channel may sit on, or a refusal naming why not.

        Step 1F.2.1: a retired platform stops accepting new channels - retiring
        one and then finding channels still being registered onto it would make
        the status a note rather than a decision. Step 1F.2.4a applies the same
        rule to *moving* a channel onto one, through this one method, so the two
        paths cannot come to disagree about what an active platform is.
        """
        platform = await self._session.get(PrPlatform, platform_id)
        if platform is None:
            raise PrNotFoundError(
                "No platform with that id", details={"platform_id": str(platform_id)}
            )
        if platform.status is not PrEntityStatus.ACTIVE:
            raise PrValidationError(
                "Nền tảng này đã ngừng hoạt động, không tạo kênh mới được.",
                details={
                    "field": "platform_id",
                    "reason": "platform_inactive",
                    "platform_code": platform.code,
                },
            )
        return platform

    @staticmethod
    def _require_valid_tier(tier: int | None) -> None:
        if tier is not None and tier not in VALID_TIERS:
            raise PrValidationError(
                "Channel tier must be 1, 2 or 3",
                details={"tier": tier, "allowed": sorted(VALID_TIERS)},
            )

    @staticmethod
    def _optional_text(value: str | None, field_name: str, max_length: int) -> str | None:
        """Trimmed text, or ``None`` for a field somebody left blank.

        ``""`` and ``"   "`` are what a form submits when a person tabs past an
        optional box, and storing either would be recording that the channel has
        a handle which is nothing. The column's ``CHECK`` refuses them anyway;
        this turns the refusal into a normalization.
        """
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if len(text) > max_length:
            raise PrValidationError(
                f"PR channel {field_name} is longer than {max_length} characters",
                details={"field": field_name, "max_length": max_length, "length": len(text)},
            )
        return text

    @staticmethod
    def _require_text(value: str, field_name: str, max_length: int) -> str:
        text = (value or "").strip()
        if not text:
            raise PrValidationError(
                f"PR channel {field_name} must not be blank", details={"field": field_name}
            )
        if len(text) > max_length:
            raise PrValidationError(
                f"PR channel {field_name} is longer than {max_length} characters",
                details={"field": field_name, "max_length": max_length, "length": len(text)},
            )
        return text


__all__: list[str] = ["CreateChannelCommand", "PrChannelService", "UpdateChannelCommand"]
