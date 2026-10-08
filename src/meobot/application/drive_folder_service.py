"""The allow-list of Drive folders MeoBot may create files in.

Without this table MeoBot would be a service account with write access to a
company Drive and an LLM deciding where to put things. With it, the set of
possible destinations is a list a human wrote, each entry verified against
Google before it can be used.

Validation asks Google four questions, in this order:

1. does the id resolve to something, and is it a folder;
2. can the service account read it;
3. can the service account add children to it (``canAddChildren``);
4. is it inside ``GOOGLE_DRIVE_ROOT_FOLDER_ID``, when that is configured.

A folder that fails any of them is stored with the failure recorded and is not
offered as a destination.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.config import Settings
from meobot.core.errors import (
    AuthorizationError,
    ConflictError,
    IntegrationError,
    NotFoundError,
    ValidationError,
)
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.drive import DriveFolder
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.drive.models import FolderValidationStatus
from meobot.domain.identity.models import Actor, Role
from meobot.integrations.google.drive import DriveClient, DriveFile

logger = get_logger(__name__)

#: How deep the "is this inside the root folder" walk goes before giving up.
#: Drive hierarchies MeoBot cares about are shallow; a deep walk would be a
#: fishing expedition through the company Drive.
MAX_ANCESTOR_DEPTH = 8


class FolderValidation:
    """Outcome of checking one folder against Google."""

    __slots__ = ("file", "message", "status")

    def __init__(
        self,
        status: FolderValidationStatus,
        *,
        file: DriveFile | None = None,
        message: str = "",
    ) -> None:
        self.status = status
        self.file = file
        self.message = message

    @property
    def ok(self) -> bool:
        return self.status is FolderValidationStatus.VALID


class DriveFolderService:
    """Register, validate and list allowed destination folders.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
        drive: Drive client used for validation.
        settings: Supplies the root-folder and Shared Drive constraints.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        drive: DriveClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._audit = audit
        self._drive = drive
        self._settings = settings

    # --- Queries ----------------------------------------------------------
    async def list_folders(self, *, active_only: bool = True) -> Sequence[DriveFolder]:
        """Registered folders, newest label first."""
        statement = select(DriveFolder).order_by(DriveFolder.name)
        if active_only:
            statement = statement.where(DriveFolder.active.is_(True))
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def list_usable(self) -> list[DriveFolder]:
        """Folders that may actually receive a file right now."""
        return [
            folder
            for folder in await self.list_folders(active_only=True)
            if folder.validation_status == FolderValidationStatus.VALID.value
        ]

    async def get(self, folder_id: uuid.UUID) -> DriveFolder:
        """Fetch one registered folder.

        Raises:
            NotFoundError: When the folder is not registered.
        """
        folder = await self._session.get(DriveFolder, folder_id)
        if folder is None:
            raise NotFoundError(f"Không tìm thấy thư mục Drive đã đăng ký: {folder_id}")
        return folder

    async def find_by_drive_id(self, drive_folder_id: str) -> DriveFolder | None:
        """Look a folder up by its Google id."""
        result = await self._session.execute(
            select(DriveFolder).where(DriveFolder.drive_folder_id == drive_folder_id)
        )
        return result.scalar_one_or_none()

    async def resolve_destination(self, reference: str) -> DriveFolder:
        """Resolve a user-typed folder reference to a usable registered folder.

        Accepts a registered folder's UUID prefix, its name, or its Google id.
        Refuses anything not registered - which is the point: an unregistered
        folder is not a destination, however well-formed its id looks.
        """
        candidates = await self.list_usable()
        needle = reference.strip().lower()
        if not needle:
            raise ValidationError("Bạn chưa chọn thư mục đích.")

        for folder in candidates:
            if (
                str(folder.id).lower().startswith(needle)
                or folder.name.lower() == needle
                or folder.drive_folder_id.lower() == needle
            ):
                return folder

        raise NotFoundError(
            "Thư mục này chưa được đăng ký với TasksBot. Dùng /drive_folders để "
            "xem danh sách, hoặc /add_drive_folder để đăng ký thêm."
        )

    def assert_usable_by(self, folder: DriveFolder, actor: Actor) -> None:
        """Check that ``actor`` may create files in ``folder``.

        Google's own permissions remain authoritative regardless of what this
        allows: a folder MeoBot thinks is fine will still fail at Drive if the
        service account was un-shared.

        Raises:
            AuthorizationError: When the folder is restricted to a team.
                MeoBot does not model which team a person belongs to, so a
                team-scoped folder is limited to ADMIN and OWNER rather than
                being silently open to every team lead.
            ValidationError: When the folder is inactive or unvalidated.
        """
        if not folder.active:
            raise ValidationError(f"Thư mục {folder.name!r} đang tắt, không dùng để tạo file.")
        if folder.validation_status != FolderValidationStatus.VALID.value:
            raise ValidationError(
                f"Thư mục {folder.name!r} chưa xác thực được với Google "
                f"(trạng thái: {folder.validation_status}). Hãy kiểm tra lại quyền chia sẻ."
            )
        if folder.team_scope and actor.role.rank < Role.ADMIN.rank:
            raise AuthorizationError(
                f"Thư mục {folder.name!r} giới hạn cho team {folder.team_scope!r}; "
                "chỉ ADMIN hoặc OWNER mới tạo file trong đó."
            )

    # --- Validation -------------------------------------------------------
    async def validate_folder_id(self, drive_folder_id: str) -> FolderValidation:
        """Check one Google folder id against every rule.

        Never raises for a *validation* failure - a bad folder is a result, not
        an exception. Only a transport failure propagates.
        """
        try:
            file = await self._drive.get_file(drive_folder_id)
        except NotFoundError:
            return FolderValidation(
                FolderValidationStatus.NOT_FOUND,
                message="Không tìm thấy thư mục. Kiểm tra lại link hoặc ID.",
            )
        except IntegrationError as exc:
            return FolderValidation(
                FolderValidationStatus.NO_ACCESS,
                message=exc.message,
            )

        if not file.is_folder:
            return FolderValidation(
                FolderValidationStatus.NOT_A_FOLDER,
                file=file,
                message="Đây là một file, không phải thư mục.",
            )
        if file.trashed:
            return FolderValidation(
                FolderValidationStatus.NOT_FOUND,
                file=file,
                message="Thư mục này đang ở thùng rác.",
            )
        if not file.can_add_children:
            return FolderValidation(
                FolderValidationStatus.CANNOT_CREATE,
                file=file,
                message=(
                    "TasksBot đọc được thư mục nhưng không có quyền tạo file trong đó. "
                    "Hãy cấp quyền Content manager (hoặc Editor) cho email service account."
                ),
            )

        inside = await self._is_inside_root(file)
        if not inside:
            root = self._settings.google_drive_root_folder_id
            return FolderValidation(
                FolderValidationStatus.OUTSIDE_ROOT,
                file=file,
                message=(
                    "Thư mục này nằm ngoài thư mục gốc đã cấu hình "
                    f"(GOOGLE_DRIVE_ROOT_FOLDER_ID={root}). TasksBot chỉ tạo file bên trong đó."
                ),
            )
        return FolderValidation(FolderValidationStatus.VALID, file=file)

    async def _is_inside_root(self, file: DriveFile) -> bool:
        """Walk parents up to the configured root.

        No root configured means no restriction. The walk is bounded so a
        pathological hierarchy cannot turn this into a Drive crawl.
        """
        root = (self._settings.google_drive_root_folder_id or "").strip()
        if not root:
            return True
        if file.file_id == root:
            return True

        seen: set[str] = set()
        frontier = list(file.parents)
        for _ in range(MAX_ANCESTOR_DEPTH):
            if not frontier:
                return False
            if root in frontier:
                return True
            next_frontier: list[str] = []
            for parent_id in frontier:
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                try:
                    parent = await self._drive.get_file(parent_id)
                except (NotFoundError, IntegrationError):
                    continue
                next_frontier.extend(parent.parents)
            frontier = next_frontier
        return False

    async def revalidate(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        folder_id: uuid.UUID,
    ) -> DriveFolder:
        """Re-run validation for an already registered folder."""
        folder = await self.get(folder_id)
        validation = await self.validate_folder_id(folder.drive_folder_id)
        folder.validation_status = validation.status.value
        folder.validation_error = validation.message or None
        folder.last_validated_at = utcnow()
        if validation.file is not None:
            folder.name = validation.file.name or folder.name
            folder.shared_drive_id = validation.file.drive_id or folder.shared_drive_id
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.TOOL_EXECUTED.value,
            result=AuditResult.SUCCESS if validation.ok else AuditResult.FAILED,
            entity_type="drive_folder",
            entity_id=str(folder.id),
            after_data={"validation_status": folder.validation_status},
            error_message=folder.validation_error,
        )
        return folder

    # --- Commands ---------------------------------------------------------
    async def register(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        drive_folder_id: str,
        path_label: str | None = None,
        purpose: str | None = None,
        team_scope: str | None = None,
    ) -> DriveFolder:
        """Register a folder as an allowed destination.

        Validation runs first: an unusable folder is never stored as usable.

        Raises:
            ValidationError: When Google says the folder cannot receive files.
            ConflictError: When the folder is already registered.
        """
        validation = await self.validate_folder_id(drive_folder_id)
        if not validation.ok:
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.TOOL_DENIED.value,
                result=AuditResult.FAILED,
                entity_type="drive_folder",
                entity_id=drive_folder_id,
                after_data={"validation_status": validation.status.value},
                error_message=validation.message,
            )
            raise ValidationError(validation.message, details={"status": validation.status.value})

        assert validation.file is not None
        existing = await self.find_by_drive_id(drive_folder_id)
        if existing is not None:
            raise ConflictError(
                f"Thư mục {existing.name!r} đã được đăng ký rồi.",
                details={"folder_id": str(existing.id)},
            )

        folder = DriveFolder(
            drive_folder_id=drive_folder_id,
            shared_drive_id=validation.file.drive_id,
            name=validation.file.name[:300] or drive_folder_id,
            path_label=(path_label or None) and path_label[:500],
            purpose=(purpose or None) and purpose[:300],
            team_scope=(team_scope or None) and team_scope[:100],
            active=True,
            validation_status=FolderValidationStatus.VALID.value,
            last_validated_at=utcnow(),
            created_by_user_id=actor.user_id,
            created_by_telegram_id=actor.telegram_user_id,
        )
        self._session.add(folder)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Thư mục này đã được đăng ký.", details={"drive_folder_id": drive_folder_id}
            ) from exc

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.TOOL_EXECUTED.value,
            result=AuditResult.SUCCESS,
            entity_type="drive_folder",
            entity_id=str(folder.id),
            after_data={
                "drive_folder_id": drive_folder_id,
                "name": folder.name,
                "shared_drive_id": folder.shared_drive_id,
                "team_scope": folder.team_scope,
            },
        )
        logger.info(
            "drive_folder_registered",
            extra={"folder_id": str(folder.id), "shared_drive": bool(folder.shared_drive_id)},
        )
        return folder

    async def set_active(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        folder_id: uuid.UUID,
        active: bool,
    ) -> DriveFolder:
        """Turn a destination folder on or off. Never deletes the row."""
        folder = await self.get(folder_id)
        folder.active = active
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.TOOL_EXECUTED.value,
            result=AuditResult.SUCCESS,
            entity_type="drive_folder",
            entity_id=str(folder.id),
            after_data={"active": active},
        )
        return folder
