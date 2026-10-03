"""Audit writing, including redaction of anything secret-shaped."""

from __future__ import annotations

import uuid

from meobot.application.audit_service import MAX_FIELD_LENGTH, AuditService
from meobot.core.logging import REDACTED, redact_text
from meobot.db.models.audit_log import AuditLog
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.identity.models import Actor
from tests.fakes import FakeSession


async def test_record_action_writes_a_row(owner_actor: Actor, request_id: uuid.UUID) -> None:
    session = FakeSession()
    service = AuditService(session)

    row = await service.record_action(
        request_id=request_id,
        actor=owner_actor,
        action=AuditAction.SCRIPT_TYPE_CREATED.value,
        result=AuditResult.SUCCESS,
        entity_type="script_type",
        entity_id="TT-01",
        after_data={"code": "doctor_education"},
    )

    assert session.added_of(AuditLog) == [row]
    assert session.flush_count == 1
    assert row.request_id == request_id
    assert row.actor_telegram_id == owner_actor.telegram_user_id
    assert row.actor_user_id is None  # bootstrap owner has no users row yet
    assert row.result is AuditResult.SUCCESS
    assert row.after_data == {"code": "doctor_education"}


async def test_denials_are_audited_too(employee_actor: Actor, request_id: uuid.UUID) -> None:
    session = FakeSession()
    service = AuditService(session)

    row = await service.record_action(
        request_id=request_id,
        actor=employee_actor,
        action=AuditAction.TOOL_DENIED.value,
        result=AuditResult.DENIED,
        entity_type="tool",
        entity_id="script.approve",
        error_message="Vai trò EMPLOYEE không có quyền script.approve.",
    )

    assert row.result is AuditResult.DENIED
    assert row.error_message is not None
    assert row.actor_user_id == employee_actor.user_id


async def test_secrets_are_redacted_before_storage(
    owner_actor: Actor, request_id: uuid.UUID
) -> None:
    """Tokens must never be persisted, even if a caller passes them in."""
    session = FakeSession()
    service = AuditService(session)

    row = await service.record_action(
        request_id=request_id,
        actor=owner_actor,
        action="integration.configured",
        result=AuditResult.SUCCESS,
        after_data={
            "telegram_bot_token": "123456789:AAHreal_looking_token_value_here_x",
            "refresh_token": "1//0eXampleRefreshToken",
            "note": "connected page",
        },
    )

    assert row.after_data is not None
    assert row.after_data["telegram_bot_token"] == REDACTED
    assert row.after_data["refresh_token"] == REDACTED
    assert row.after_data["note"] == "connected page"


async def test_token_shaped_text_inside_a_normal_field_is_masked() -> None:
    """Redaction also catches secrets pasted into free-form text."""
    masked = redact_text("token là 123456789:AAHreal_looking_token_value_here_x nhé")
    assert "AAHreal_looking_token_value_here_x" not in masked
    assert REDACTED in masked


async def test_dsn_password_is_masked() -> None:
    masked = redact_text("postgresql+asyncpg://meobot:sup3rs3cret@postgres:5432/meobot")
    assert "sup3rs3cret" not in masked


async def test_long_values_are_truncated(owner_actor: Actor, request_id: uuid.UUID) -> None:
    """An oversized payload must not be able to bloat the audit table."""
    session = FakeSession()
    service = AuditService(session)

    row = await service.record_action(
        request_id=request_id,
        actor=owner_actor,
        action="script.submitted",
        result=AuditResult.SUCCESS,
        after_data={"script_body": "x" * (MAX_FIELD_LENGTH * 2)},
    )

    assert row.after_data is not None
    assert len(row.after_data["script_body"]) == MAX_FIELD_LENGTH


async def test_entry_can_be_recorded_directly(request_id: uuid.UUID) -> None:
    session = FakeSession()
    service = AuditService(session)

    row = await service.record(
        AuditEntry(
            request_id=request_id,
            action="system.health_check",
            result=AuditResult.SUCCESS,
        )
    )

    assert row.action == "system.health_check"
    assert row.before_data is None
    assert row.after_data is None
