"""Script Type Registry service: versioning rules and conflict handling."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from meobot.application.audit_service import AuditService
from meobot.application.script_type_service import ScriptTypeService
from meobot.core.errors import ConflictError, NotFoundError, ValidationError
from meobot.db.models.script_type import ScriptType, ScriptTypeVersion
from meobot.domain.identity.models import Actor
from tests.fakes import FakeResult, FakeSession

VALID_RUBRIC: dict[str, Any] = {
    "passing_score": 70,
    "criteria": [
        {"code": "hook_strength", "name": "Sức hút", "weight": 60},
        {"code": "clarity", "name": "Rõ ràng", "weight": 40},
    ],
}


def make_service(session: FakeSession) -> ScriptTypeService:
    return ScriptTypeService(session, AuditService(session))


def make_script_type(*, version: int = 1) -> ScriptType:
    script_type = ScriptType(
        code="doctor_education",
        name="Bác sĩ chia sẻ",
        description=None,
        active=True,
        current_version=version,
    )
    script_type.id = uuid.uuid4()
    return script_type


async def test_create_script_type_writes_type_version_and_audit(
    owner_actor: Actor, request_id: uuid.UUID
) -> None:
    session = FakeSession(results=[FakeResult()])  # no existing code
    service = make_service(session)

    script_type = await service.create_script_type(
        actor=owner_actor,
        request_id=request_id,
        code="doctor_education",
        name="Bác sĩ chia sẻ",
        review_rubric=VALID_RUBRIC,
    )

    assert script_type.current_version == 1
    versions = session.added_of(ScriptTypeVersion)
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].review_rubric["criteria"][0]["code"] == "hook_strength"


async def test_create_rejects_invalid_rubric(owner_actor: Actor, request_id: uuid.UUID) -> None:
    """An invalid rubric must be refused before anything is written."""
    session = FakeSession()
    service = make_service(session)

    bad_rubric = {"criteria": [{"code": "clarity", "name": "Rõ ràng", "weight": 55}]}
    with pytest.raises(ValidationError):
        await service.create_script_type(
            actor=owner_actor,
            request_id=request_id,
            code="x_type",
            name="X",
            review_rubric=bad_rubric,
        )
    assert session.added == []


async def test_create_rejects_duplicate_code(owner_actor: Actor, request_id: uuid.UUID) -> None:
    existing = make_script_type()
    session = FakeSession(results=[FakeResult([existing])])
    service = make_service(session)

    with pytest.raises(ConflictError, match="đã tồn tại"):
        await service.create_script_type(
            actor=owner_actor,
            request_id=request_id,
            code="doctor_education",
            name="Trùng",
            review_rubric=VALID_RUBRIC,
        )


async def test_add_version_appends_and_advances_current(
    owner_actor: Actor, request_id: uuid.UUID
) -> None:
    """A rubric change creates version N+1; version N is never mutated."""
    script_type = make_script_type(version=3)
    session = FakeSession(rows={script_type.id: script_type})
    service = make_service(session)

    version = await service.add_version(
        actor=owner_actor,
        request_id=request_id,
        script_type_id=script_type.id,
        review_rubric=VALID_RUBRIC,
    )

    assert version.version == 4
    assert script_type.current_version == 4


async def test_add_version_rejects_stale_expected_version(
    owner_actor: Actor, request_id: uuid.UUID
) -> None:
    """Optimistic concurrency: a stale writer must not overwrite a newer rubric."""
    script_type = make_script_type(version=5)
    session = FakeSession(rows={script_type.id: script_type})
    service = make_service(session)

    with pytest.raises(ConflictError, match="Version hiện tại là 5"):
        await service.add_version(
            actor=owner_actor,
            request_id=request_id,
            script_type_id=script_type.id,
            review_rubric=VALID_RUBRIC,
            expected_version=3,
        )
    assert script_type.current_version == 5


async def test_duplicate_version_integrity_error_becomes_conflict(
    owner_actor: Actor, request_id: uuid.UUID
) -> None:
    """The (script_type_id, version) unique constraint surfaces as ConflictError."""
    script_type = make_script_type(version=1)
    session = FakeSession(rows={script_type.id: script_type})
    session.flush_error = IntegrityError("INSERT", {}, Exception("duplicate key"))
    service = make_service(session)

    with pytest.raises(ConflictError, match="version 2"):
        await service.add_version(
            actor=owner_actor,
            request_id=request_id,
            script_type_id=script_type.id,
            review_rubric=VALID_RUBRIC,
        )


async def test_add_version_requires_existing_script_type(
    owner_actor: Actor, request_id: uuid.UUID
) -> None:
    session = FakeSession()
    service = make_service(session)

    with pytest.raises(NotFoundError):
        await service.add_version(
            actor=owner_actor,
            request_id=request_id,
            script_type_id=uuid.uuid4(),
            review_rubric=VALID_RUBRIC,
        )


async def test_deactivate_keeps_history(owner_actor: Actor, request_id: uuid.UUID) -> None:
    """Deactivating never deletes versions - past reviews stay explainable."""
    script_type = make_script_type(version=2)
    session = FakeSession(rows={script_type.id: script_type})
    service = make_service(session)

    result = await service.deactivate(
        actor=owner_actor, request_id=request_id, script_type_id=script_type.id
    )

    assert result.active is False
    assert result.current_version == 2
