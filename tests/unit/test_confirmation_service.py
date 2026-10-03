"""Confirmation lifecycle: creation, redemption, expiry and ownership."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from meobot.application.audit_service import AuditService
from meobot.application.confirmation_service import ConfirmationService
from meobot.core.config import Settings
from meobot.core.errors import ConfirmationExpiredError, NotFoundError
from meobot.core.time import utc_in, utcnow
from meobot.db.models.confirmation_request import ConfirmationRequestRow
from meobot.domain.identity.models import Actor
from meobot.domain.policy.models import ActionPlan, ConfirmationState, RiskLevel
from tests.fakes import FakeResult, FakeSession

APPROVE_PLAN = ActionPlan(
    intent="approve_script",
    risk_level=RiskLevel.HIGH,
    requires_confirmation=True,
    tool_name="script.approve",
    arguments={"script_id": "TT-0312", "version": 3},
)


def make_service(session: FakeSession, settings: Settings) -> ConfirmationService:
    return ConfirmationService(session, settings, AuditService(session))


def make_row(
    actor: Actor,
    *,
    expires_in: int = 300,
    status: ConfirmationState = ConfirmationState.PENDING,
    token: str = "abc123",  # noqa: S107 - fixture value, not a credential
) -> ConfirmationRequestRow:
    row = ConfirmationRequestRow(
        user_id=actor.user_id,
        telegram_user_id=actor.telegram_user_id,
        action_plan=APPROVE_PLAN.model_dump(mode="json"),
        confirmation_token=token,
        idempotency_key=None,
        status=status,
        expires_at=utc_in(expires_in),
    )
    row.id = uuid.uuid4()
    return row


async def test_create_stores_plan_and_issues_token(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    session = FakeSession()
    service = make_service(session, settings)

    row = await service.create(actor=team_lead_actor, request_id=request_id, plan=APPROVE_PLAN)

    assert row.confirmation_token
    assert row.status is ConfirmationState.PENDING
    assert row.action_plan["tool_name"] == "script.approve"
    assert row.expires_at > utcnow()


async def test_create_is_idempotent_for_the_same_key(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    """Asking twice must not produce two ways to execute the same action."""
    existing = make_row(team_lead_actor, token="already-pending")
    session = FakeSession(results=[FakeResult([existing])])
    service = make_service(session, settings)

    plan = APPROVE_PLAN.model_copy(update={"idempotency_key": "approve:TT-0312:v3"})
    row = await service.create(actor=team_lead_actor, request_id=request_id, plan=plan)

    assert row.confirmation_token == "already-pending"
    assert session.added == []


async def test_redeem_returns_the_stored_plan(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    """The plan comes from storage, so the confirming message cannot alter it."""
    row = make_row(team_lead_actor)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    redeemed, plan = await service.redeem(
        actor=team_lead_actor, request_id=request_id, token="abc123"
    )

    assert redeemed.status is ConfirmationState.CONFIRMED
    assert redeemed.confirmed_at is not None
    assert plan.tool_name == "script.approve"
    assert plan.arguments == {"script_id": "TT-0312", "version": 3}


async def test_expired_token_is_rejected_and_marked(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    row = make_row(team_lead_actor, expires_in=-1)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    with pytest.raises(ConfirmationExpiredError, match="hết hạn"):
        await service.redeem(actor=team_lead_actor, request_id=request_id, token="abc123")

    assert row.status is ConfirmationState.EXPIRED


async def test_token_expires_exactly_at_the_deadline(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    """The boundary is closed: at ``expires_at`` the token is already dead."""
    row = make_row(team_lead_actor)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    with pytest.raises(ConfirmationExpiredError):
        await service.redeem(
            actor=team_lead_actor,
            request_id=request_id,
            token="abc123",
            now=row.expires_at,
        )


async def test_token_valid_just_before_the_deadline(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    row = make_row(team_lead_actor)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    _, plan = await service.redeem(
        actor=team_lead_actor,
        request_id=request_id,
        token="abc123",
        now=row.expires_at - timedelta(seconds=1),
    )
    assert plan.tool_name == "script.approve"


async def test_token_cannot_be_reused(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    row = make_row(team_lead_actor, status=ConfirmationState.CONFIRMED)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    with pytest.raises(ConfirmationExpiredError, match="đã được sử dụng"):
        await service.redeem(actor=team_lead_actor, request_id=request_id, token="abc123")


async def test_another_user_cannot_redeem_the_token(
    settings: Settings, team_lead_actor: Actor, employee_actor: Actor, request_id: uuid.UUID
) -> None:
    """A stranger gets 'invalid token', never a hint that it exists."""
    row = make_row(team_lead_actor)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    with pytest.raises(NotFoundError, match="không hợp lệ"):
        await service.redeem(actor=employee_actor, request_id=request_id, token="abc123")
    assert row.status is ConfirmationState.PENDING


async def test_unknown_token_is_rejected(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    session = FakeSession(results=[FakeResult()])
    service = make_service(session, settings)

    with pytest.raises(NotFoundError):
        await service.redeem(actor=team_lead_actor, request_id=request_id, token="nope")


async def test_reject_marks_the_request(
    settings: Settings, team_lead_actor: Actor, request_id: uuid.UUID
) -> None:
    row = make_row(team_lead_actor)
    session = FakeSession(results=[FakeResult([row])])
    service = make_service(session, settings)

    await service.reject(actor=team_lead_actor, request_id=request_id, token="abc123")

    assert row.status is ConfirmationState.REJECTED


async def test_expire_stale_marks_overdue_requests(
    settings: Settings, team_lead_actor: Actor
) -> None:
    overdue = [make_row(team_lead_actor, expires_in=-10), make_row(team_lead_actor, expires_in=-5)]
    session = FakeSession(results=[FakeResult(overdue)])
    service = make_service(session, settings)

    count = await service.expire_stale()

    assert count == 2
    assert all(row.status is ConfirmationState.EXPIRED for row in overdue)
