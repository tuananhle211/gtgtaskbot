"""Step 1F.2.8: one reviewer, many items, one gate - and all of it or none of it.

The suite is built on Step 1F.2.7's ``World``, deliberately and not for
convenience: bulk approval must be the *same* rule as single approval, so it is
tested against the same fixture, the same grants and the same scopes. A test
that built its own world could pass while the two paths disagreed.

Three properties are asserted over and over, because they are the whole feature:

* **the same step.** Every item in a batch stands at the gate the batch names.
  Anything else - a different gate, a stage that is not a gate at all, an item
  that has moved since the checkbox was ticked - refuses the whole batch;
* **the same authorization.** Every item passes
  ``PrCapabilityService.require_approval`` for this actor, under the row lock,
  against the item as it is now. One failure refuses the whole batch;
* **nothing partial, ever.** After every refusal the suite counts the approval
  events and the stages, and both are exactly what they were before the call.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_bulk_approval_service import BulkApprovalOutcome, BulkApproveCommand
from meobot.application.pr_content_query import ContentQuery
from meobot.application.pr_services import build_pr_services
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrApprovalEvent, PrChannel, PrContentItem
from meobot.db.models.user import User
from meobot.domain.pr.errors import (
    PrBulkApprovalStaleError,
    PrBulkApprovalUnauthorizedError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrContentType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import BULK_APPROVAL_MAX_ITEMS, PrCapability

# The same world Step 1F.2.7's suite is argued against, not a lookalike of it -
# see ``tests/unit/pr_world.py``. The ``world`` fixture itself comes from
# ``tests/unit/conftest.py`` and is not imported.
from tests.unit.pr_world import TODAY, World, selected

pytestmark = pytest.mark.asyncio

EVERYTHING = GrantScope(content_type_scope=PrGrantScopeMode.ALL, channel_scope=PrGrantScopeMode.ALL)
LEAD_GATE = PrApprovalStage.TEAM_LEAD_REVIEW
HEAD_GATE = PrApprovalStage.HEAD_REVIEW


# --- Helpers ----------------------------------------------------------------


async def waiting(
    world: World,
    *,
    content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
    channels: tuple[PrChannel, ...] | None = None,
) -> uuid.UUID:
    """One item, walked to ``TEAM_LEAD_REVIEW`` the way the workflow intends.

    A channel is not optional here even though it is irrelevant to authorization
    in most of these tests: policy readiness refuses ``AI_REVIEW`` for an item
    with no planned channel, and this walks the real edges rather than writing a
    stage into the row. TikTok unless the test says otherwise.
    """
    content_id = await world.content_id(
        content_type=content_type,
        channels=(world.tiktok,) if channels is None else channels,
    )
    await world.to_team_lead_review(content_id)
    return content_id


async def at_head(world: World, content_id: uuid.UUID, user: User | None = None) -> uuid.UUID:
    """The same item, moved on to ``HEAD_REVIEW`` by a real team-lead approval.

    By whoever holds the team-lead grant in the test - the member, in every
    caller here. Walking the edge rather than writing the stage is what makes
    these "an item that has moved" tests rather than "a row somebody edited".
    """
    await world.approve(user or world.member, content_id, LEAD_GATE)
    return content_id


async def bulk(
    world: World,
    user: User,
    gate: PrApprovalStage,
    content_ids: list[uuid.UUID],
    *,
    comment: str | None = None,
) -> BulkApprovalOutcome:
    services = build_pr_services(world.session, world.settings)
    return await services.bulk_approvals.approve(
        actor=world.actor(user),
        request_id=world.request_id,
        command=BulkApproveCommand(
            gate=gate,
            content_ids=content_ids,
            reviewer_user_id=user.id,
            comment=comment,
        ),
    )


async def stage_of(world: World, content_id: uuid.UUID) -> PrWorkflowStage:
    row = await world.session.get(PrContentItem, content_id)
    assert row is not None
    await world.session.refresh(row)
    return row.workflow_stage


async def approval_count(world: World) -> int:
    return int(await world.session.scalar(select(func.count()).select_from(PrApprovalEvent)) or 0)


async def events_for(world: World, content_id: uuid.UUID) -> list[PrApprovalEvent]:
    result = await world.session.execute(
        select(PrApprovalEvent).where(PrApprovalEvent.content_id == content_id)
    )
    return list(result.scalars().all())


async def audit_rows(world: World, action: str) -> list[AuditLog]:
    result = await world.session.execute(select(AuditLog).where(AuditLog.action == action))
    return list(result.scalars().all())


# =============================================================================
# 1. The happy paths
# =============================================================================


async def test_1a_bulk_approves_one_item(world: World) -> None:
    """A batch of one is a batch. Same rule, same write, same event."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    content_id = await waiting(world)

    outcome = await bulk(world, world.member, LEAD_GATE, [content_id])

    assert len(outcome.approved) == 1
    assert await stage_of(world, content_id) is PrWorkflowStage.HEAD_REVIEW
    assert len(await events_for(world, content_id)) == 1


async def test_1b_bulk_approves_several_items(world: World) -> None:
    """Three items at one gate, one call, three separate decisions."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    ids = [await waiting(world) for _ in range(3)]

    outcome = await bulk(world, world.member, LEAD_GATE, ids, comment="Duyệt cả lô.")

    assert [item.content_id for item in outcome.approved] == sorted(ids, key=str)
    for content_id in ids:
        assert await stage_of(world, content_id) is PrWorkflowStage.HEAD_REVIEW
        events = await events_for(world, content_id)
        assert len(events) == 1
        assert events[0].decision is PrApprovalDecision.APPROVED
        assert events[0].comment == "Duyệt cả lô."
    assert await approval_count(world) == 3


async def test_1c_a_role_based_reviewer_still_works(world: World) -> None:
    """Requirement: the ordinary reviewer is unaffected.

    A ``TEAM_LEAD`` holding a legacy ``requires_role_baseline`` grant - the shape
    every pre-1F.2.7 row was migrated to - bulk approves exactly as they would
    approve one at a time.
    """
    await world.grant(
        world.lead, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING, requires_role_baseline=True
    )
    ids = [await waiting(world) for _ in range(2)]

    await bulk(world, world.lead, LEAD_GATE, ids)

    for content_id in ids:
        assert await stage_of(world, content_id) is PrWorkflowStage.HEAD_REVIEW


async def test_1d_an_explicitly_scoped_reviewer_works(world: World) -> None:
    """An ``EMPLOYEE`` with a narrow grant approves what the grant names."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    ids = [
        await waiting(
            world, content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.tiktok,)
        )
        for _ in range(2)
    ]

    await bulk(world, world.member, LEAD_GATE, ids)

    for content_id in ids:
        assert await stage_of(world, content_id) is PrWorkflowStage.HEAD_REVIEW


async def test_1e_a_multi_channel_item_is_approvable_when_fully_covered(world: World) -> None:
    """Both of an item's channels inside the grant: the subset test passes."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
    )
    content_id = await waiting(world, channels=(world.tiktok, world.facebook))

    await bulk(world, world.member, LEAD_GATE, [content_id])

    assert await stage_of(world, content_id) is PrWorkflowStage.HEAD_REVIEW


# =============================================================================
# 2. The same-step rule
# =============================================================================


async def test_2a_mixed_gates_refuse_the_whole_batch(world: World) -> None:
    """A team-lead item and a head item in one request approve neither."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    await world.grant(world.member, PrCapability.PR_HEAD_REVIEW, EVERYTHING)
    lead_item = await waiting(world)
    head_item = await at_head(world, await waiting(world))
    before = await approval_count(world)

    with pytest.raises(PrBulkApprovalStaleError) as refused:
        await bulk(world, world.member, LEAD_GATE, [lead_item, head_item])

    assert refused.value.details["approved"] == 0
    affected = refused.value.details["affected"]
    assert [entry["content_id"] for entry in affected] == [str(head_item)]
    assert affected[0]["reason"] == "moved"
    assert affected[0]["current_stage"] == PrWorkflowStage.HEAD_REVIEW.value
    assert await approval_count(world) == before
    assert await stage_of(world, lead_item) is PrWorkflowStage.TEAM_LEAD_REVIEW


async def test_2b_an_item_at_no_gate_at_all_refuses_the_batch(world: World) -> None:
    """``SCRIPTING`` is not a gate, so it is not "a different gate" either."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    good = await waiting(world)
    drafting = await world.content_id()
    before = await approval_count(world)

    with pytest.raises(PrBulkApprovalStaleError) as refused:
        await bulk(world, world.member, LEAD_GATE, [good, drafting])

    assert refused.value.details["affected"][0]["reason"] == "not_at_a_gate"
    assert await approval_count(world) == before


async def test_2c_a_missing_id_refuses_the_batch(world: World) -> None:
    """An id nothing stands behind is named, and nothing is approved."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    good = await waiting(world)
    ghost = uuid.uuid4()

    with pytest.raises(PrBulkApprovalStaleError) as refused:
        await bulk(world, world.member, LEAD_GATE, [good, ghost])

    assert refused.value.details["affected"][0] == {
        "content_id": str(ghost),
        "code": None,
        "current_stage": None,
        "reason": "missing",
    }
    assert await approval_count(world) == 0


# =============================================================================
# 3. Concurrency: somebody else got there first
# =============================================================================


async def test_3a_another_reviewer_moving_one_item_refuses_the_batch(world: World) -> None:
    """The exact race the step is about, played out in order.

    Two items are selected. Between the selection and the batch, another
    reviewer approves one of them - so it is at ``HEAD_REVIEW`` when the batch
    arrives. Neither is approved by the batch, and the item the other reviewer
    moved keeps *their* decision rather than acquiring a second one.
    """
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    await world.grant(world.lead, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    mine = await waiting(world)
    theirs = await waiting(world)

    await world.approve(world.lead, theirs, LEAD_GATE)  # the other reviewer

    with pytest.raises(PrBulkApprovalStaleError) as refused:
        await bulk(world, world.member, LEAD_GATE, [mine, theirs])

    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [str(theirs)]
    assert await stage_of(world, mine) is PrWorkflowStage.TEAM_LEAD_REVIEW
    assert len(await events_for(world, mine)) == 0
    # The other reviewer's decision stands, alone.
    theirs_events = await events_for(world, theirs)
    assert len(theirs_events) == 1
    assert theirs_events[0].reviewer_user_id == world.lead.id


async def test_3b_a_cancelled_item_refuses_the_batch(world: World) -> None:
    """A rejection is a move too, and it is caught by the same check."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    services = build_pr_services(world.session, world.settings)
    mine = await waiting(world)
    rejected = await waiting(world)
    await services.approvals.record_decision(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=rejected,
            reviewer_user_id=world.member.id,
            approval_stage=LEAD_GATE,
            decision=PrApprovalDecision.REJECTED,
            version_reviewed=1,
        ),
    )

    with pytest.raises(PrBulkApprovalStaleError):
        await bulk(world, world.member, LEAD_GATE, [mine, rejected])

    assert await stage_of(world, mine) is PrWorkflowStage.TEAM_LEAD_REVIEW


# =============================================================================
# 4. Authorization, item by item
# =============================================================================


async def test_4a_no_grant_at_all_is_refused_before_any_row_is_read(world: World) -> None:
    """The unscoped refusal, and it does not say whether the ids exist."""
    content_id = await waiting(world)

    with pytest.raises(PrPermissionDeniedError) as refused:
        await bulk(world, world.member, LEAD_GATE, [content_id, uuid.uuid4()])

    assert refused.value.details["reason"] == "missing_grant"
    assert await approval_count(world) == 0


async def test_4b_one_out_of_scope_content_type_refuses_the_batch(world: World) -> None:
    """A grant over Facebook posts does not decide a short-video script."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    covered = await waiting(
        world, content_type=PrContentType.FACEBOOK_POST, channels=(world.tiktok,)
    )
    outside = await waiting(
        world, content_type=PrContentType.SHORT_VIDEO_SCRIPT, channels=(world.tiktok,)
    )

    with pytest.raises(PrBulkApprovalUnauthorizedError) as refused:
        await bulk(world, world.member, LEAD_GATE, [covered, outside])

    assert refused.value.details["approved"] == 0
    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [str(outside)]
    assert refused.value.details["reason"] == "out_of_grant_scope"
    assert await approval_count(world) == 0
    assert await stage_of(world, covered) is PrWorkflowStage.TEAM_LEAD_REVIEW


async def test_4c_one_out_of_scope_channel_refuses_the_batch(world: World) -> None:
    """The channel axis, same shape as the classification axis."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    covered = await waiting(world, channels=(world.tiktok,))
    elsewhere = await waiting(world, channels=(world.youtube,))

    with pytest.raises(PrBulkApprovalUnauthorizedError) as refused:
        await bulk(world, world.member, LEAD_GATE, [covered, elsewhere])

    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [str(elsewhere)]
    assert await approval_count(world) == 0


async def test_4d_partial_coverage_of_a_multi_channel_item_refuses(world: World) -> None:
    """Approving a cross-posted item is what puts it on *both* channels.

    The subset rule, and the reason it is a subset rule. A grant naming only
    TikTok must not decide an item going to TikTok *and* YouTube - and in a
    batch that refusal takes the whole batch with it.
    """
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    single = await waiting(world, channels=(world.tiktok,))
    cross_posted = await waiting(world, channels=(world.tiktok, world.youtube))

    with pytest.raises(PrBulkApprovalUnauthorizedError) as refused:
        await bulk(world, world.member, LEAD_GATE, [single, cross_posted])

    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [
        str(cross_posted)
    ]
    assert await approval_count(world) == 0


async def test_4e_an_expired_grant_refuses_the_batch(world: World) -> None:
    """A grant that ended yesterday authorises nothing today."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        EVERYTHING,
        effective_to=TODAY - timedelta(days=1),
    )
    content_id = await waiting(world)

    with pytest.raises(PrPermissionDeniedError):
        await bulk(world, world.member, LEAD_GATE, [content_id])

    assert await approval_count(world) == 0


async def test_4f_a_revoked_grant_refuses_the_batch(world: World) -> None:
    """Revocation is immediate: the next request, not the end of the day."""
    grant_id = await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    content_id = await waiting(world)
    await world.capabilities.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, grant_id=grant_id
    )

    with pytest.raises(PrPermissionDeniedError):
        await bulk(world, world.member, LEAD_GATE, [content_id])

    assert await approval_count(world) == 0


async def test_4g_a_grant_revoked_mid_selection_refuses_a_partly_covered_batch(
    world: World,
) -> None:
    """Two grants, one revoked: the items it covered take the batch down.

    The actor still holds *a* grant at this gate, so the unscoped check passes
    and the refusal has to come from the per-item scope test - which is the
    check requirement 3 is about.
    """
    tiktok_grant = await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    on_tiktok = await waiting(world, channels=(world.tiktok,))
    on_facebook = await waiting(world, channels=(world.facebook,))
    await world.capabilities.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, grant_id=tiktok_grant
    )

    with pytest.raises(PrBulkApprovalUnauthorizedError) as refused:
        await bulk(world, world.member, LEAD_GATE, [on_tiktok, on_facebook])

    assert [entry["content_id"] for entry in refused.value.details["affected"]] == [str(on_tiktok)]
    assert await approval_count(world) == 0


# =============================================================================
# 5. The request itself: duplicates, emptiness, size
# =============================================================================


async def test_5a_duplicate_ids_are_deduplicated_not_double_approved(world: World) -> None:
    """The documented behaviour: keep the first, count what was dropped."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    content_id = await waiting(world)

    outcome = await bulk(world, world.member, LEAD_GATE, [content_id, content_id, content_id])

    assert outcome.duplicates_removed == 2
    assert outcome.requested == 3
    assert len(outcome.approved) == 1
    assert len(await events_for(world, content_id)) == 1


async def test_5b_an_empty_batch_is_refused(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    with pytest.raises(PrValidationError) as refused:
        await bulk(world, world.member, LEAD_GATE, [])
    assert refused.value.details["reason"] == "empty_batch"


async def test_5c_a_batch_over_the_limit_is_refused_before_any_lock(world: World) -> None:
    """The limit is a refusal, not a truncation. Approving 200 of 201 silently
    is the "reported 100, did 60" failure this whole step is built to prevent."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    with pytest.raises(PrValidationError) as refused:
        await bulk(
            world,
            world.member,
            LEAD_GATE,
            [uuid.uuid4() for _ in range(BULK_APPROVAL_MAX_ITEMS + 1)],
        )
    assert refused.value.details["reason"] == "batch_too_large"
    assert refused.value.details["max_items"] == BULK_APPROVAL_MAX_ITEMS
    assert await approval_count(world) == 0


async def test_5d_duplicates_are_counted_against_the_limit_after_removal(world: World) -> None:
    """201 ids of which 100 are repeats is 101 items, and it is accepted."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    content_id = await waiting(world)
    outcome = await bulk(
        world, world.member, LEAD_GATE, [content_id] * (BULK_APPROVAL_MAX_ITEMS + 1)
    )
    assert len(outcome.approved) == 1


# =============================================================================
# 6. History and correlation
# =============================================================================


async def test_6a_every_item_keeps_its_own_event_and_its_own_audit_row(world: World) -> None:
    """Requirement 9. A batch adds a row; it never replaces the per-item ones."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    ids = [await waiting(world) for _ in range(3)]

    outcome = await bulk(world, world.member, LEAD_GATE, ids)

    for content_id in ids:
        events = await events_for(world, content_id)
        assert len(events) == 1
        assert events[0].approval_stage is LEAD_GATE
        assert events[0].reviewer_user_id == world.member.id
        assert events[0].version_reviewed == 1

    per_item = await audit_rows(world, "pr.approval.recorded")
    assert len(per_item) == 3
    batch_id = str(outcome.batch_id)
    assert {(row.after_data or {}).get("batch_id") for row in per_item} == {batch_id}

    batch = await audit_rows(world, "pr.approval.batch_recorded")
    assert len(batch) == 1
    after = batch[0].after_data or {}
    assert after["approved_count"] == 3
    assert after["gate"] == LEAD_GATE.value
    assert sorted(after["content_ids"]) == sorted(str(value) for value in ids)


async def test_6b_a_single_approval_carries_no_batch_id(world: World) -> None:
    """Requirement 36: the one-item path is unchanged except for the key's
    presence, which is ``None`` - the same payload shape every reader had."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    content_id = await waiting(world)

    await world.approve(world.member, content_id, LEAD_GATE)

    rows = await audit_rows(world, "pr.approval.recorded")
    assert len(rows) == 1
    assert (rows[0].after_data or {})["batch_id"] is None
    assert await audit_rows(world, "pr.approval.batch_recorded") == []


async def test_6c_two_batches_get_two_ids(world: World) -> None:
    """The correlation id identifies *this* act, not the actor or the gate."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    first = await bulk(world, world.member, LEAD_GATE, [await waiting(world)])
    second = await bulk(world, world.member, LEAD_GATE, [await waiting(world)])
    assert first.batch_id != second.batch_id


# =============================================================================
# 7. The HTTP surface - a direct caller gets the same answers
# =============================================================================


async def test_7a_the_endpoint_approves_a_batch(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    ids = [str(await waiting(world)) for _ in range(2)]
    world.act_as(world.member)

    response = world.client.post(
        "/api/pr/reviews/bulk-approve",
        json={"gate": LEAD_GATE.value, "content_ids": ids},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["approved_count"] == 2
    assert body["gate"] == LEAD_GATE.value
    assert sorted(item["content_id"] for item in body["approved"]) == sorted(ids)
    assert all(item["new_stage"] == PrWorkflowStage.HEAD_REVIEW.value for item in body["approved"])


async def test_7b_the_endpoint_cannot_be_used_to_bypass_a_scope(world: World) -> None:
    """Requirement 25. The panel is not where any check happens."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    covered = str(await waiting(world, channels=(world.tiktok,)))
    outside = str(await waiting(world, channels=(world.youtube,)))
    world.act_as(world.member)

    response = world.client.post(
        "/api/pr/reviews/bulk-approve",
        json={"gate": LEAD_GATE.value, "content_ids": [covered, outside]},
    )

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "pr_bulk_approval_forbidden"
    assert error["details"]["approved"] == 0
    assert [entry["content_id"] for entry in error["details"]["affected"]] == [outside]
    assert await approval_count(world) == 0


async def test_7c_a_stale_batch_is_a_409_naming_the_items(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    await world.grant(world.member, PrCapability.PR_HEAD_REVIEW, EVERYTHING)
    fine = str(await waiting(world))
    moved = str(await at_head(world, await waiting(world)))
    world.act_as(world.member)

    response = world.client.post(
        "/api/pr/reviews/bulk-approve",
        json={"gate": LEAD_GATE.value, "content_ids": [fine, moved]},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "pr_bulk_approval_stale"
    assert error["details"]["approved"] == 0
    assert [entry["content_id"] for entry in error["details"]["affected"]] == [moved]


async def test_7d_the_endpoint_refuses_an_unknown_gate(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    world.act_as(world.member)
    response = world.client.post(
        "/api/pr/reviews/bulk-approve",
        json={"gate": "PUBLISHED", "content_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 422, response.text


async def test_7e_the_endpoint_enforces_the_limit_before_the_service_does(
    world: World,
) -> None:
    """The schema caps the list too, so an oversized body never allocates."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    world.act_as(world.member)
    response = world.client.post(
        "/api/pr/reviews/bulk-approve",
        json={
            "gate": LEAD_GATE.value,
            "content_ids": [str(uuid.uuid4()) for _ in range(BULK_APPROVAL_MAX_ITEMS + 1)],
        },
    )
    assert response.status_code == 422, response.text


# =============================================================================
# 8. "Select all at this step" - server-side eligibility
# =============================================================================


async def test_8a_the_selection_returns_only_what_the_grant_covers(world: World) -> None:
    """Eligibility is applied *before* the cut, so the count is the truth."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    mine = {await waiting(world, channels=(world.tiktok,)) for _ in range(3)}
    for _ in range(2):
        await waiting(world, channels=(world.youtube,))

    services = build_pr_services(world.session, world.settings)
    selection = await services.queries.approvable_selection(
        actor=world.actor(world.member), gate=LEAD_GATE, query=ContentQuery(limit=50)
    )

    assert selection.total == 3
    assert set(selection.content_ids) == mine
    assert selection.truncated is False


async def test_8b_the_selection_is_empty_without_a_grant_at_that_gate(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_HEAD_REVIEW, EVERYTHING)
    await waiting(world)
    services = build_pr_services(world.session, world.settings)
    selection = await services.queries.approvable_selection(
        actor=world.actor(world.member), gate=LEAD_GATE, query=ContentQuery()
    )
    assert selection.total == 0
    assert selection.content_ids == ()


async def test_8c_the_selection_truncates_honestly(world: World) -> None:
    """The ids are a bounded batch and the total is the whole queue."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    for _ in range(4):
        await waiting(world)

    services = build_pr_services(world.session, world.settings)
    selection = await services.queries.approvable_selection(
        actor=world.actor(world.member), gate=LEAD_GATE, query=ContentQuery(), limit=2
    )

    assert selection.total == 4
    assert len(selection.content_ids) == 2
    assert selection.truncated is True
    assert selection.limit == 2


async def test_8d_the_selection_honours_the_filters_it_is_given(world: World) -> None:
    """A select-all inside a filtered board means all of *what is on screen*."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    on_tiktok = await waiting(world, channels=(world.tiktok,))
    await waiting(world, channels=(world.youtube,))

    services = build_pr_services(world.session, world.settings)
    selection = await services.queries.approvable_selection(
        actor=world.actor(world.member),
        gate=LEAD_GATE,
        query=ContentQuery(channel_id=world.tiktok.id),
    )

    assert selection.content_ids == (on_tiktok,)


async def test_8e_the_selection_pins_the_stage_to_the_gate(world: World) -> None:
    """A caller's own ``stage`` cannot widen a select-all past its own step."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    await world.grant(world.member, PrCapability.PR_HEAD_REVIEW, EVERYTHING)
    at_lead = await waiting(world)
    await at_head(world, await waiting(world))

    services = build_pr_services(world.session, world.settings)
    selection = await services.queries.approvable_selection(
        actor=world.actor(world.member),
        gate=LEAD_GATE,
        query=ContentQuery(stage=PrWorkflowStage.HEAD_REVIEW),
    )

    assert selection.content_ids == (at_lead,)


async def test_8f_the_selection_endpoint_answers_the_same_thing(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    ids = {str(await waiting(world)) for _ in range(2)}
    world.act_as(world.member)

    response = world.client.get("/api/pr/reviews/approvable", params={"gate": LEAD_GATE.value})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert set(body["content_ids"]) == ids
    assert body["truncated"] is False
    assert body["limit"] == BULK_APPROVAL_MAX_ITEMS


async def test_8g_selected_ids_can_be_approved_as_a_batch(world: World) -> None:
    """The two halves join up: what the selection returns, the batch accepts."""
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    for _ in range(3):
        await waiting(world)
    world.act_as(world.member)

    selection = world.client.get(
        "/api/pr/reviews/approvable", params={"gate": LEAD_GATE.value}
    ).json()
    response = world.client.post(
        "/api/pr/reviews/bulk-approve",
        json={"gate": LEAD_GATE.value, "content_ids": selection["content_ids"]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["approved_count"] == 3


# =============================================================================
# 9. The board marks which cards may carry a checkbox
# =============================================================================


async def test_9a_the_board_flags_only_the_items_this_actor_may_decide(
    world: World,
) -> None:
    """``approvable_by_me`` is the server's answer, from the write's predicate."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    mine = str(await waiting(world, channels=(world.tiktok,)))
    theirs = str(await waiting(world, channels=(world.youtube,)))
    world.act_as(world.member)

    body = world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 50}).json()

    flags = {item["id"]: item["approvable_by_me"] for item in body["items"]}
    assert flags[mine] is True
    assert flags[theirs] is False


async def test_9b_an_actor_with_no_grant_sees_no_checkbox_anywhere(world: World) -> None:
    await waiting(world)
    world.act_as(world.member)
    body = world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 50}).json()
    assert all(item["approvable_by_me"] is False for item in body["items"])


async def test_9c_an_item_that_is_not_at_a_gate_is_never_flagged(world: World) -> None:
    await world.grant(world.member, PrCapability.PR_TEAM_LEAD_REVIEW, EVERYTHING)
    drafting = str(await world.content_id())
    world.act_as(world.member)
    body = world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 50}).json()
    flags = {item["id"]: item["approvable_by_me"] for item in body["items"]}
    assert flags[drafting] is False


# =============================================================================
# 10. One rule, one place - asserted by shape
# =============================================================================


async def test_10a_bulk_approval_reuses_the_single_approval_write() -> None:
    """No second implementation of the decision, the checks or the history.

    Asserted on the source because the failure it guards against is somebody
    "optimising" the loop into a bulk ``INSERT``: it would still pass every
    behavioural test above on the day it was written, and would silently stop
    writing transitions, notifications or audit rows the first time one of those
    changed.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "meobot"
        / "application"
        / "pr_bulk_approval_service.py"
    ).read_text(encoding="utf-8")

    assert "self._approvals.record_decision(" in source
    assert "require_approval(actor, content, gate)" in source
    # Nothing here writes an approval, a transition or a stage of its own.
    for forbidden in ("PrApprovalEvent(", "workflow_stage =", "session.add("):
        assert forbidden not in source, forbidden


async def test_10b_the_limit_is_declared_once(world: World) -> None:
    """The schema, the service and the selection all read one constant."""
    from meobot.api.schemas.pr import BulkApproveRequest

    field = BulkApproveRequest.model_fields["content_ids"]
    caps = [
        item.max_length for item in field.metadata if getattr(item, "max_length", None) is not None
    ]
    assert caps == [BULK_APPROVAL_MAX_ITEMS]
