"""Script queries and the two human decisions: approve, or send back.

Approving here means exactly one thing:
``ScriptStatus.APPROVED_FOR_PRODUCTION`` - permission to film. It creates no
video, no publish job, and no publishing permission of any kind. Publishing is
a separate approval on a separate entity (ADR-002), and no code path in this
module can reach it.

Before an approval is written, three things are verified: the actor's role and
permission (the policy engine already ran, this is the second check at the
point of the write), that the version being approved is still the current one,
and that the review on file belongs to that exact version.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, WorkflowStateError
from meobot.core.logging import get_logger
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.script_approval import ScriptApproval
from meobot.db.models.script_review import ScriptReview
from meobot.db.models.script_type import ScriptType
from meobot.db.models.sheet_profile import SheetProfile
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.scripts.models import ApprovalAction
from meobot.domain.scripts.workflow import (
    PENDING_REVIEW_STATUSES,
    ScriptStatus,
    assert_script_transition,
)

logger = get_logger(__name__)

#: Statuses from which a human may approve for production.
APPROVABLE_STATUSES: frozenset[ScriptStatus] = frozenset(
    {ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL, ScriptStatus.AI_REVIEWED}
)

#: Statuses from which a human may send a script back for revision.
REVISABLE_STATUSES: frozenset[ScriptStatus] = frozenset(
    {
        ScriptStatus.IMPORTED,
        ScriptStatus.SUBMITTED_FOR_REVIEW,
        ScriptStatus.REVIEWING,
        ScriptStatus.AI_REVIEWED,
        ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
        ScriptStatus.APPROVED_FOR_PRODUCTION,
    }
)


@dataclass(frozen=True, slots=True)
class ScriptDetail:
    """A script with everything the Telegram and API views need."""

    script: Script
    version: ScriptVersion | None
    review: ScriptReview | None
    profile: SheetProfile | None
    script_type: ScriptType | None

    @property
    def version_number(self) -> int:
        return self.version.version_number if self.version else 0

    @property
    def review_matches_version(self) -> bool:
        """True when the stored review judged the version now on screen."""
        return (
            self.review is not None
            and self.version is not None
            and self.review.script_version_id == self.version.id
        )


class ScriptService:
    """Reads scripts and records human decisions about them.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- Queries ----------------------------------------------------------
    async def get(self, script_id: uuid.UUID) -> Script:
        """Fetch a script.

        Raises:
            NotFoundError: When it does not exist.
        """
        script = await self._session.get(Script, script_id)
        if script is None:
            raise NotFoundError(f"Không tìm thấy kịch bản {script_id}")
        return script

    async def resolve(self, reference: str) -> Script:
        """Find a script by UUID, by UUID prefix, or by its sheet id.

        Owners type what they see in Telegram, which is a short id or the
        sheet's own code - not a full UUID.

        Raises:
            NotFoundError: When nothing matches.
            ConflictError: When a short reference matches several scripts.
        """
        text = reference.strip()
        if not text:
            raise NotFoundError("Thiếu mã kịch bản.")

        try:
            return await self.get(uuid.UUID(text))
        except ValueError:
            pass
        except NotFoundError:
            raise

        result = await self._session.execute(
            select(Script).where(Script.external_script_id == text).limit(2)
        )
        matches = list(result.scalars().all())
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ConflictError(
                f"Có nhiều kịch bản mang mã {text!r}. Hãy dùng ID đầy đủ.",
                details={"external_script_id": text},
            )

        prefix = text.replace("-", "").lower()
        if len(prefix) >= 6:
            candidates = await self._session.execute(select(Script).limit(500))
            hits = [
                script for script in candidates.scalars().all() if script.id.hex.startswith(prefix)
            ]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise ConflictError(f"Mã {text!r} chưa đủ để phân biệt kịch bản.")
        raise NotFoundError(f"Không tìm thấy kịch bản {text!r}.")

    async def detail(self, script_id: uuid.UUID) -> ScriptDetail:
        """Load a script together with its current version, review and source."""
        script = await self.get(script_id)
        version = (
            await self._session.get(ScriptVersion, script.current_version_id)
            if script.current_version_id
            else None
        )
        review: ScriptReview | None = None
        if version is not None:
            result = await self._session.execute(
                select(ScriptReview)
                .where(ScriptReview.script_version_id == version.id)
                .order_by(ScriptReview.created_at.desc())
                .limit(1)
            )
            review = result.scalars().first()
        if review is None:
            result = await self._session.execute(
                select(ScriptReview)
                .where(ScriptReview.script_id == script.id)
                .order_by(ScriptReview.created_at.desc())
                .limit(1)
            )
            review = result.scalars().first()

        profile = (
            await self._session.get(SheetProfile, script.sheet_profile_id)
            if script.sheet_profile_id
            else None
        )
        script_type = (
            await self._session.get(ScriptType, script.script_type_id)
            if script.script_type_id
            else None
        )
        return ScriptDetail(
            script=script,
            version=version,
            review=review,
            profile=profile,
            script_type=script_type,
        )

    async def list_pending(self, *, limit: int = 10, offset: int = 0) -> Sequence[Script]:
        """Scripts waiting for a review or for a human decision."""
        result = await self._session.execute(
            select(Script)
            .where(Script.status.in_(sorted(PENDING_REVIEW_STATUSES)))
            .order_by(Script.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def count_pending(self) -> int:
        """How many scripts are waiting overall."""
        result = await self._session.execute(
            select(func.count(Script.id)).where(Script.status.in_(sorted(PENDING_REVIEW_STATUSES)))
        )
        return int(result.scalar_one())

    async def list_awaiting_review(self, *, limit: int = 20) -> Sequence[Script]:
        """Scripts whose current version has not been reviewed yet."""
        result = await self._session.execute(
            select(Script)
            .where(
                Script.status.in_([ScriptStatus.IMPORTED, ScriptStatus.SUBMITTED_FOR_REVIEW]),
                Script.current_version_id.is_not(None),
            )
            .order_by(Script.updated_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_versions(self, script_id: uuid.UUID) -> list[ScriptVersion]:
        """Every version of a script, oldest first."""
        result = await self._session.execute(
            select(ScriptVersion)
            .where(ScriptVersion.script_id == script_id)
            .order_by(ScriptVersion.version_number)
        )
        return list(result.scalars().all())

    async def version_by_number(
        self,
        script_id: uuid.UUID,
        version_number: int,
    ) -> ScriptVersion | None:
        """Look up one version by its number.

        Inline buttons carry a version *number* (a UUID would not fit in
        Telegram's 64-byte callback payload), so this is how a button's claim
        is turned back into an exact version.
        """
        result = await self._session.execute(
            select(ScriptVersion).where(
                ScriptVersion.script_id == script_id,
                ScriptVersion.version_number == version_number,
            )
        )
        return result.scalars().first()

    async def list_approvals(self, script_id: uuid.UUID) -> list[ScriptApproval]:
        """Approval history, newest first."""
        result = await self._session.execute(
            select(ScriptApproval)
            .where(ScriptApproval.script_id == script_id)
            .order_by(ScriptApproval.created_at.desc())
        )
        return list(result.scalars().all())

    # --- Commands ---------------------------------------------------------
    async def approve_for_production(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        script_id: uuid.UUID,
        expected_version_id: uuid.UUID | None = None,
        comment: str | None = None,
        require_review: bool = True,
    ) -> ScriptApproval:
        """Approve the current version for production. Nothing more.

        Args:
            expected_version_id: The version the human was looking at. When it
                is no longer current, the approval is refused - a sheet edit
                between the button being drawn and being pressed must not be
                approved by accident.
            require_review: When True (the default), the current version must
                already have a review on file.

        Raises:
            AuthorizationError: When the actor lacks ``script.approve``.
            NotFoundError: When the script or its version is missing.
            ConflictError: When the version changed under the actor's feet.
            WorkflowStateError: When the status forbids approval, or the
                current version has never been reviewed.
        """
        self._require_permission(actor, Permission.SCRIPT_APPROVE)

        script = await self.get(script_id)
        version = await self._require_current_version(script, expected_version_id)

        if script.status not in APPROVABLE_STATUSES:
            raise WorkflowStateError(
                f"Kịch bản đang ở trạng thái {script.status.value!r}, chưa thể duyệt sản xuất.",
                details={"status": script.status.value},
            )

        if require_review:
            result = await self._session.execute(
                select(ScriptReview.id).where(ScriptReview.script_version_id == version.id).limit(1)
            )
            if result.scalars().first() is None:
                raise WorkflowStateError(
                    "Phiên bản hiện tại chưa được review. Hãy chạy /review_script trước.",
                    details={"script_version_id": str(version.id)},
                )

        status_before = script.status
        if status_before is ScriptStatus.AI_REVIEWED:
            assert_script_transition(status_before, ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL)
            script.status = ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL
        assert_script_transition(script.status, ScriptStatus.APPROVED_FOR_PRODUCTION)
        script.status = ScriptStatus.APPROVED_FOR_PRODUCTION

        approval = ScriptApproval(
            script_id=script.id,
            script_version_id=version.id,
            action=ApprovalAction.APPROVE_FOR_PRODUCTION,
            status_before=status_before,
            status_after=script.status,
            actor_user_id=actor.user_id,
            actor_telegram_id=actor.telegram_user_id,
            comment=comment,
        )
        self._session.add(approval)
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_APPROVED_FOR_PRODUCTION.value,
            result=AuditResult.SUCCESS,
            entity_type="script",
            entity_id=str(script.id),
            before_data={"status": status_before.value},
            after_data={
                "status": script.status.value,
                "script_version_id": str(version.id),
                "version_number": version.version_number,
                "approval_id": str(approval.id),
                # Spelled out so the audit trail itself records the boundary.
                "grants_publishing": False,
            },
        )
        logger.info(
            "script_approved_for_production",
            extra={
                "script_id": str(script.id),
                "version": version.version_number,
                "actor": actor.describe(),
            },
        )
        return approval

    async def request_revision(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        script_id: uuid.UUID,
        comment: str | None = None,
        expected_version_id: uuid.UUID | None = None,
    ) -> ScriptApproval:
        """Send a script back to its author with an optional comment.

        Raises:
            AuthorizationError: When the actor lacks ``script.approve``.
            NotFoundError: When the script or its version is missing.
            ConflictError: When the version changed under the actor's feet.
            WorkflowStateError: When the status forbids the transition.
        """
        self._require_permission(actor, Permission.SCRIPT_APPROVE)

        script = await self.get(script_id)
        version = await self._require_current_version(script, expected_version_id)

        if script.status not in REVISABLE_STATUSES:
            raise WorkflowStateError(
                f"Kịch bản đang ở trạng thái {script.status.value!r}, không thể yêu cầu sửa.",
                details={"status": script.status.value},
            )

        status_before = script.status
        assert_script_transition(status_before, ScriptStatus.REVISION_REQUIRED)
        script.status = ScriptStatus.REVISION_REQUIRED

        record = ScriptApproval(
            script_id=script.id,
            script_version_id=version.id,
            action=ApprovalAction.REQUEST_REVISION,
            status_before=status_before,
            status_after=script.status,
            actor_user_id=actor.user_id,
            actor_telegram_id=actor.telegram_user_id,
            comment=comment,
        )
        self._session.add(record)
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_REVISION_REQUESTED.value,
            result=AuditResult.SUCCESS,
            entity_type="script",
            entity_id=str(script.id),
            before_data={"status": status_before.value},
            after_data={
                "status": script.status.value,
                "script_version_id": str(version.id),
                "has_comment": bool(comment),
            },
        )
        return record

    async def queue_for_review(
        self,
        *,
        script_id: uuid.UUID,
    ) -> Script:
        """Move a script into the review queue, if it is not already there.

        Raises:
            NotFoundError: When the script does not exist.
        """
        script = await self.get(script_id)
        if script.status is ScriptStatus.IMPORTED:
            script.status = ScriptStatus.SUBMITTED_FOR_REVIEW
            await self._session.flush()
        return script

    # --- Guards -----------------------------------------------------------
    @staticmethod
    def _require_permission(actor: Actor, permission: Permission) -> None:
        """Second permission check, at the point of the write.

        The policy engine already ran for tool-originated calls, but this
        service is also reachable from the API and from Celery, and an
        approval must never depend on the caller having remembered to ask.
        """
        if not has_permission(actor.role, permission):
            raise AuthorizationError(
                # The message is read by a person, so it carries the label; the
                # details are read by the audit trail, so they carry the enum.
                f"Vai trò {role_label(actor.role)} không có quyền {permission.value}.",
                details={"role": actor.role.value, "permission": permission.value},
            )

    async def _require_current_version(
        self,
        script: Script,
        expected_version_id: uuid.UUID | None,
    ) -> ScriptVersion:
        """Return the current version, refusing a stale one.

        Raises:
            NotFoundError: When the script has no current version.
            ConflictError: When ``expected_version_id`` is not current.
        """
        if script.current_version_id is None:
            raise NotFoundError("Kịch bản chưa có nội dung.")
        if expected_version_id is not None and expected_version_id != script.current_version_id:
            raise ConflictError(
                "Nội dung kịch bản đã thay đổi sau khi bạn mở nó. "
                "Hãy xem lại bản mới nhất rồi quyết định.",
                details={
                    "expected_version_id": str(expected_version_id),
                    "current_version_id": str(script.current_version_id),
                },
            )
        version = await self._session.get(ScriptVersion, script.current_version_id)
        if version is None:
            raise NotFoundError("Không tìm thấy phiên bản hiện tại của kịch bản.")
        return version
