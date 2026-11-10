"""Hồ sơ nhân sự (0096): attached documents and the staff's change requests.

Documents ("Tài liệu đính kèm", H2): PDF / JPEG / PNG / WebP by their magic
bytes, at most 10 MB, kept private in the database with their SHA-256. HCNS
(``EDIT``) uploads and deletes; whoever sees the profile's sensitive part
(the person, ``SENSITIVE`` holders) downloads. Every download is audited.

Change requests (Q13): a person asks to change their phone, personal email or
addresses; HCNS approves (the profile changes) or refuses with a reason.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from meobot.application.audit_service import AuditService
from meobot.application.hr.access import HrAccess, HrForbiddenError, HrNotFoundError
from meobot.application.hr.profiles import HrValidationError
from meobot.db.models.hr_profile import HrChangeRequest, HrDocument, HrProfile, HrScanLink
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.hr.profiles import (
    DOCUMENT_KINDS,
    MAX_DOCUMENT_BYTES,
    MAX_SCAN_LINKS,
    REQUEST_FIELDS,
    REQUEST_STATUS_LABELS,
    SCAN_LINK,
    RequestStatus,
)
from meobot.domain.identity.models import Actor

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff(data: bytes) -> str | None:
    for magic, content_type in _MAGIC:
        if data.startswith(magic):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class HrFileService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- documents -------------------------------------------------------------------

    async def upload(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: str,
        name: str,
        data: bytes,
    ) -> HrDocument:
        access.require_edit()
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise HrNotFoundError()
        if kind not in DOCUMENT_KINDS:
            raise HrValidationError(
                "hr_document_invalid", "Loại tài liệu không hợp lệ.", field="kind"
            )
        if not data:
            raise HrValidationError("hr_document_invalid", "Tệp trống.")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise HrValidationError(
                "hr_document_too_large", f"Tệp lớn quá {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB."
            )
        content_type = sniff(data)
        if content_type is None:
            raise HrValidationError(
                "hr_document_invalid", "Chỉ nhận PDF hoặc ảnh (JPG, PNG, WebP)."
            )
        cleaned = " ".join(name.replace("/", "_").replace("\\", "_").split())[:255] or "tai-lieu"
        row = HrDocument(
            user_id=user_id,
            kind=kind,
            name=cleaned,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            data=data,
            uploaded_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_DOCUMENT_UPLOADED,
            user_id,
            {"document_id": str(row.id), "kind": kind, "size": len(data), "sha256": row.sha256},
        )
        return row

    async def download(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, document_id: uuid.UUID
    ) -> HrDocument:
        row = await self._session.scalar(
            select(HrDocument).where(HrDocument.id == document_id).options(undefer(HrDocument.data))
        )
        if row is None or not access.sees_sensitive(row.user_id):
            raise HrNotFoundError("Không tìm thấy tài liệu.")
        await self._record(
            actor,
            request_id,
            AuditAction.HR_DOCUMENT_DOWNLOADED,
            row.user_id,
            {"document_id": str(row.id), "kind": row.kind},
        )
        return row

    async def delete(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        access.require_edit()
        row = await self._session.get(HrDocument, document_id)
        if row is None:
            raise HrNotFoundError("Không tìm thấy tài liệu.")
        await self._session.delete(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_DOCUMENT_DELETED,
            row.user_id,
            {"document_id": str(row.id), "kind": row.kind, "name": row.name},
        )

    # --- scan links (0099) ------------------------------------------------------------

    @staticmethod
    def _link_fields(label: str | None, url: str | None) -> tuple[str | None, str]:
        clean_url = (url or "").strip()
        if not SCAN_LINK.fullmatch(clean_url) or len(clean_url) > 2000:
            raise HrValidationError(
                "hr_scan_link_invalid", "Link phải bắt đầu bằng http:// hoặc https://.", field="url"
            )
        clean_label = " ".join((label or "").split()) or None
        if clean_label is not None and len(clean_label) > 200:
            raise HrValidationError(
                "hr_scan_link_invalid", "Tên link tối đa 200 ký tự.", field="label"
            )
        return clean_label, clean_url

    def _may_edit_links(self, access: HrAccess, user_id: uuid.UUID) -> None:
        access.require_edit()
        if not access.sees_sensitive(user_id):
            raise HrForbiddenError("Chỉ người xem được hồ sơ giấy mới sửa được link scan.")

    async def add_scan_link(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        label: str | None,
        url: str,
    ) -> HrScanLink:
        self._may_edit_links(access, user_id)
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise HrNotFoundError()
        clean_label, clean_url = self._link_fields(label, url)
        existing = (
            await self._session.scalars(select(HrScanLink).where(HrScanLink.user_id == user_id))
        ).all()
        if len(existing) >= MAX_SCAN_LINKS:
            raise HrValidationError(
                "hr_scan_link_invalid", f"Tối đa {MAX_SCAN_LINKS} link mỗi người.", field="url"
            )
        if any(row.url == clean_url for row in existing):
            raise HrValidationError(
                "hr_scan_link_duplicate", "Link này đã có trong hồ sơ.", field="url"
            )
        row = HrScanLink(
            user_id=user_id,
            label=clean_label,
            url=clean_url,
            sort_order=max((item.sort_order for item in existing), default=-1) + 1,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_SCAN_LINK_ADDED,
            user_id,
            {"link_id": str(row.id), "label": clean_label},
        )
        return row

    async def update_scan_link(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        link_id: uuid.UUID,
        label: str | None,
        url: str,
    ) -> HrScanLink:
        row = await self._session.get(HrScanLink, link_id)
        if row is None or not access.sees_sensitive(row.user_id):
            raise HrNotFoundError("Không tìm thấy link.")
        self._may_edit_links(access, row.user_id)
        clean_label, clean_url = self._link_fields(label, url)
        clash = await self._session.scalar(
            select(HrScanLink.id).where(
                HrScanLink.user_id == row.user_id,
                HrScanLink.url == clean_url,
                HrScanLink.id != row.id,
            )
        )
        if clash is not None:
            raise HrValidationError(
                "hr_scan_link_duplicate", "Link này đã có trong hồ sơ.", field="url"
            )
        row.label = clean_label
        row.url = clean_url
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_SCAN_LINK_UPDATED,
            row.user_id,
            {"link_id": str(row.id), "label": clean_label},
        )
        return row

    async def delete_scan_link(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, link_id: uuid.UUID
    ) -> None:
        row = await self._session.get(HrScanLink, link_id)
        if row is None or not access.sees_sensitive(row.user_id):
            raise HrNotFoundError("Không tìm thấy link.")
        self._may_edit_links(access, row.user_id)
        await self._session.delete(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_SCAN_LINK_DELETED,
            row.user_id,
            {"link_id": str(row.id), "label": row.label},
        )

    # --- change requests -------------------------------------------------------------

    async def submit(
        self, *, actor: Actor, request_id: uuid.UUID, field: str, value: str | None
    ) -> HrChangeRequest:
        """One's own request; a pending one on the same field is replaced."""
        if actor.user_id is None:
            raise HrForbiddenError()
        if field not in REQUEST_FIELDS:
            raise HrValidationError(
                "hr_request_invalid", "Trường này không tự sửa được.", field="field"
            )
        cleaned = " ".join((value or "").split())
        if len(cleaned) > 500:
            raise HrValidationError("hr_request_invalid", "Tối đa 500 ký tự.", field="value")
        profile = await self._session.get(HrProfile, actor.user_id)
        old = None if profile is None else getattr(profile, field)
        if (old or "") == cleaned:
            raise HrValidationError("hr_request_unchanged", "Giá trị mới giống giá trị hiện tại.")
        pending = (
            await self._session.scalars(
                select(HrChangeRequest).where(
                    HrChangeRequest.user_id == actor.user_id,
                    HrChangeRequest.field == field,
                    HrChangeRequest.status == RequestStatus.PENDING.value,
                )
            )
        ).all()
        for row in pending:
            row.status = RequestStatus.CANCELLED.value
        made = HrChangeRequest(
            user_id=actor.user_id,
            field=field,
            old_value=old,
            new_value=cleaned or None,
            status=RequestStatus.PENDING.value,
        )
        self._session.add(made)
        await self._session.flush()
        await self._record(
            actor, request_id, AuditAction.HR_CHANGE_SUBMITTED, actor.user_id, {"field": field}
        )
        return made

    async def requests(
        self, *, access: HrAccess, status: str | None, mine: bool
    ) -> list[dict[str, Any]]:
        statement = select(HrChangeRequest, User).join(User, User.id == HrChangeRequest.user_id)
        if mine or not access.edit:
            statement = statement.where(HrChangeRequest.user_id == access.user_id)
        if status:
            statement = statement.where(HrChangeRequest.status == status)
        rows = (
            await self._session.execute(
                statement.order_by(HrChangeRequest.created_at.desc()).limit(500)
            )
        ).all()
        return [
            {
                "id": str(request.id),
                "user_id": str(user.id),
                "member_code": user.member_code,
                "full_name": user.full_name,
                "field": request.field,
                "field_label": REQUEST_FIELDS.get(request.field, request.field),
                "old_value": request.old_value,
                "new_value": request.new_value,
                "status": request.status,
                "status_label": REQUEST_STATUS_LABELS[RequestStatus(request.status)],
                "note": request.note,
                "created_at": request.created_at,
                "decided_at": request.decided_at,
            }
            for request, user in rows
        ]

    async def pending_count(self) -> int:
        rows = (
            await self._session.scalars(
                select(HrChangeRequest.id).where(
                    HrChangeRequest.status == RequestStatus.PENDING.value
                )
            )
        ).all()
        return len(rows)

    async def decide(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        change_id: uuid.UUID,
        approve: bool,
        note: str | None,
    ) -> HrChangeRequest:
        access.require_edit()
        row = await self._session.get(HrChangeRequest, change_id)
        if row is None:
            raise HrNotFoundError("Không tìm thấy yêu cầu.")
        if row.status != RequestStatus.PENDING.value:
            raise HrValidationError("hr_request_decided", "Yêu cầu này đã được xử lý.")
        reason = " ".join((note or "").split()) or None
        if not approve and not reason:
            raise HrValidationError(
                "hr_request_note_missing", "Cần ghi lý do từ chối.", field="note"
            )
        if approve:
            profile = await self._session.get(HrProfile, row.user_id)
            if profile is None:
                profile = HrProfile(user_id=row.user_id)
                self._session.add(profile)
            setattr(profile, row.field, row.new_value)
            profile.updated_by_user_id = actor.user_id
        row.status = (RequestStatus.APPROVED if approve else RequestStatus.REJECTED).value
        row.note = reason
        row.decided_by_user_id = actor.user_id
        row.decided_at = datetime.now(UTC)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_CHANGE_DECIDED,
            row.user_id,
            {"request_id": str(row.id), "field": row.field, "approved": approve},
        )
        return row

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        user_id: uuid.UUID | None,
        after: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_profile",
            entity_id=None if user_id is None else str(user_id),
            after_data=after,
        )


__all__ = ["HrFileService", "sniff"]
