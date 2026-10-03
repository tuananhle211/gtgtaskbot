"""Platforms - the registry every channel sits on.

Step 1F.2.1. Platforms were seeded by hand until now, which was survivable
while nobody needed one and stopped being survivable the moment Step 1F.2 made
a target channel mandatory: no platform means no channel means no content.

The code is business identity, not a label
------------------------------------------

``pr_platforms.code`` is the only thing that connects a channel to the Step 1F.1
policy system. :data:`POLICY_GROUNDED_PLATFORM_CODES` is compared against it
directly, so a platform named "Facebook Việt Nam" with the code ``FB_VN`` is not
Facebook as far as policy grounding is concerned - and it should not be, because
guessing that it was would mean inferring a legal obligation from a display
name somebody typed.

So the code is asked for explicitly, never derived from the name. Normalization
goes exactly as far as case and surrounding space and no further: ``facebook``
becomes ``FACEBOOK`` because those are the same identifier written differently,
while ``Facebook Việt Nam`` is rejected rather than mangled into something that
happens to match.

Why ``PR_CHANNEL_MANAGE``
-------------------------

No new capability was invented. A platform exists to hold channels, the people
who register channels are the people who register platforms, and
``PR_CHANNEL_MANAGE`` already means "may change the publishing registry". A
separate ``PR_PLATFORM_MANAGE`` would be a grant nobody would ever hold
independently, and every capability that cannot be held independently is a row
in a table that quietly means nothing.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import record_pr_event
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrPlatform
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrConflictError, PrValidationError
from meobot.domain.pr.models import PrEntityStatus
from meobot.domain.pr.policy import PR_READ_PERMISSION, PrCapability, require_permission

logger = get_logger(__name__)

MAX_PLATFORM_CODE_LENGTH = 64
MAX_PLATFORM_NAME_LENGTH = 200

#: A canonical platform code: uppercase letters, digits and underscores, opening
#: with a letter. Deliberately narrow - this is the token Step 1F.1 matches on,
#: so it must survive being written down, pasted into a manifest and compared
#: byte for byte. Spaces, punctuation and diacritics all belong in the *name*.
CANONICAL_PLATFORM_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True, slots=True)
class CreatePlatformCommand:
    """A place content can be published.

    ``code`` is canonical identity and is normalized to upper case; ``name`` is
    whatever a person should read on screen and is stored as typed.
    """

    code: str
    name: str
    api_available: bool = False
    api_note: str | None = None


class PrPlatformService:
    """Platform master data. Read for everyone with PR access, write gated.

    Args:
        session: Unit of work; the caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
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

    async def list_platforms(
        self, *, actor: Actor, status: PrEntityStatus | None = PrEntityStatus.ACTIVE
    ) -> Sequence[PrPlatform]:
        """Platforms, by name, for a picker.

        Defaults to ACTIVE because the caller that matters is a form: offering a
        retired platform would let somebody register new channels onto something
        the department stopped using. Pass ``status=None`` for an admin listing.
        """
        require_permission(actor, PR_READ_PERMISSION)
        statement = select(PrPlatform)
        if status is not None:
            statement = statement.where(PrPlatform.status == status)
        result = await self._session.execute(statement.order_by(PrPlatform.name.asc()))
        return result.scalars().all()

    async def create_platform(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreatePlatformCommand
    ) -> PrPlatform:
        """Register a platform. The code is checked, never inferred."""
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        code = self._require_code(command.code)
        name = self._require_name(command.name)
        await self._require_code_unused(code)

        platform = PrPlatform(
            code=code,
            name=name,
            api_available=command.api_available,
            api_note=(command.api_note or None),
        )
        self._session.add(platform)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PLATFORM_CREATED,
            entity_type="pr_platform",
            entity_id=platform.id,
            after={"code": platform.code, "name": platform.name},
        )
        logger.info("pr_platform_created", extra={"platform_code": platform.code})
        return platform

    # --- Validation -------------------------------------------------------
    @staticmethod
    def _require_code(value: str) -> str:
        code = (value or "").strip().upper()
        if not code:
            raise PrValidationError(
                "Mã nền tảng không được để trống.",
                details={"field": "code", "reason": "blank"},
            )
        if len(code) > MAX_PLATFORM_CODE_LENGTH:
            raise PrValidationError(
                f"Mã nền tảng dài quá {MAX_PLATFORM_CODE_LENGTH} ký tự.",
                details={"field": "code", "max_length": MAX_PLATFORM_CODE_LENGTH},
            )
        if not CANONICAL_PLATFORM_CODE.fullmatch(code):
            raise PrValidationError(
                "Mã nền tảng chỉ gồm chữ cái không dấu, số và dấu gạch dưới, "
                "bắt đầu bằng chữ cái. Ví dụ: FACEBOOK, TIKTOK.",
                details={"field": "code", "reason": "not_canonical"},
            )
        return code

    @staticmethod
    def _require_name(value: str) -> str:
        name = (value or "").strip()
        if not name:
            raise PrValidationError(
                "Tên nền tảng không được để trống.",
                details={"field": "name", "reason": "blank"},
            )
        if len(name) > MAX_PLATFORM_NAME_LENGTH:
            raise PrValidationError(
                f"Tên nền tảng dài quá {MAX_PLATFORM_NAME_LENGTH} ký tự.",
                details={"field": "name", "max_length": MAX_PLATFORM_NAME_LENGTH},
            )
        return name

    async def _require_code_unused(self, code: str) -> None:
        """Refuse a duplicate here rather than at the unique index.

        The index would also catch it, as an ``IntegrityError`` raised on flush -
        which aborts the whole transaction and reaches the user as a 500. A
        checked conflict is the same guarantee with an answer somebody can act
        on; the index stays as the thing that is actually authoritative.
        """
        existing = await self._session.execute(
            select(PrPlatform.code).where(func.upper(PrPlatform.code) == code).limit(1)
        )
        if existing.scalars().first() is not None:
            raise PrConflictError(
                f"Đã có nền tảng với mã {code}.",
                details={"field": "code", "code": code, "reason": "duplicate_code"},
            )


__all__ = [
    "CANONICAL_PLATFORM_CODE",
    "MAX_PLATFORM_CODE_LENGTH",
    "MAX_PLATFORM_NAME_LENGTH",
    "CreatePlatformCommand",
    "PrPlatformService",
]
